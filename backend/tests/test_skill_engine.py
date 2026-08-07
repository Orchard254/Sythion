"""
Phase F skill engine tests.

The declarative skill contract: skills are validated, versioned workflows of
read-only tool calls with references limited to `inputs.<field>` and
`steps.<step_id>.output.<field>`. Executable code, templates, expressions,
loops, branches, coordinates, and secret values are rejected. A validated
version is pinned to each running task; a skill disabled before its next
unstarted step pauses the task.
"""

import json
import pytest
from pydantic import BaseModel as PBaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task, TaskEvent
from backend.api.schemas import TaskState, ToolOutput
from backend.services.task_runner import TaskRunner
from backend.services import skill_engine
from backend.services.skill_engine import (
    SkillDefinition,
    SkillParameter,
    SkillStep,
    SkillRunOutcome,
    allowed_tool_names,
    approve_proposal,
    create_skill,
    add_skill_version,
    execute_skill,
    get_enabled_skill_version,
    get_proposal,
    list_skill_summaries,
    maybe_generate_proposal,
    parse_definition,
    reject_proposal,
    set_skill_state,
    validate_definition,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class FakeReadInput(PBaseModel):
    path: str
    limit: int = 1000


class FakeWriteInput(PBaseModel):
    path: str
    content: str


class FakeReadTool:
    name = "file_read"
    description = "read a file"
    input_schema = FakeReadInput

    def __init__(self, side_effect=None):
        self._side_effect = side_effect

    def get_policy_requirements(self):
        return {}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        if self._side_effect:
            await self._side_effect(input_data)
        data = {
            "content": f"contents of {input_data.path}",
            "length": len(input_data.path),
        }
        return ToolOutput(
            success=True,
            data=data,
            model_summary=f"read {input_data.path}",
        )


class FakeMutatingTool:
    name = "file_write"
    description = "write a file"
    input_schema = FakeWriteInput

    def get_policy_requirements(self):
        return {"mutating": True}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        return ToolOutput(success=True, data={})


@pytest.fixture
def executor():
    from backend.tools.executor import ToolExecutor

    ex = ToolExecutor()
    ex.register_tool(FakeReadTool())
    ex.register_tool(FakeMutatingTool())
    return ex


def make_definition(**overrides):
    base = {
        "version": "1.0.0",
        "name": "read workflow",
        "description": "reads a file",
        "category": "custom",
        "parameters": [{"name": "path", "type": "string", "required": True, "description": "file path"}],
        "steps": [
            {
                "step_id": "read1",
                "tool": "file_read",
                "inputs": {"path": "inputs.path"},
            },
        ],
    }
    base.update(overrides)
    return SkillDefinition.model_validate(base)


def _enabled_skill(db, executor, definition=None, skill_id="skill.1"):
    definition = definition or make_definition()
    errors = validate_definition(definition, allowed_tool_names(executor))
    assert not errors, errors
    skill = create_skill(db, skill_id=skill_id, name=definition.name, description=definition.description)
    add_skill_version(db, skill, definition, errors)
    return skill


def _completed_trace_task(db, task_id, tool_name="file_read", params=None, success=True):
    task = Task(
        id=task_id,
        original_request="sample task",
        state="completed",
        version=1,
    )
    db.add(task)
    event = TaskEvent(
        task_id=task_id,
        event_type="tool.executed",
        event_data=json.dumps({
            "tool_name": tool_name,
            "success": success,
            "output": params or {"path": "C:\\x", "limit": 100},
            "model_summary": "ok",
        }),
        sequence_num=1,
    )
    db.add(event)
    db.commit()
    return task


class TestValidation:
    """Declarative validation: references restricted, dangerous content rejected."""

    def test_valid_definition(self, executor):
        assert validate_definition(make_definition(), allowed_tool_names(executor)) == []

    def test_rejects_unknown_tool(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "os.system", "inputs": {}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("unknown or disallowed tool" in e for e in errors)

    def test_rejects_mutating_tool(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_write", "inputs": {}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("unknown or disallowed tool" in e for e in errors)

    def test_rejects_undeclared_input_reference(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"path": "inputs.not_declared"}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("undeclared input parameter" in e for e in errors)

    def test_rejects_unknown_step_reference(self, executor):
        d = make_definition(steps=[
            {"step_id": "s1", "tool": "file_read", "inputs": {"path": "steps.nope.output.foo"}},
        ])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("unknown step" in e for e in errors)

    def test_rejects_forward_reference(self, executor):
        d = make_definition(steps=[
            {"step_id": "s1", "tool": "file_read", "inputs": {"path": "steps.s2.output.path"}},
            {"step_id": "s2", "tool": "file_read", "inputs": {"path": "C:\\x"}},
        ])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("forward refs" in e for e in errors)

    def test_rejects_input_not_a_tool_parameter(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"bogus_key": "1"}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("not a parameter of tool" in e for e in errors)

    @pytest.mark.parametrize("dangerous", [
        {"path": "{{template}}"},
        {"path": "eval(open('/x').read())"},
        {"path": "__import__('os').system('x')"},
        {"path": "import subprocess"},
        {"path": "os.system('x')"},
        {"path": "lambda x: x"},
        {"path": "`rm -rf`"},
    ])
    def test_rejects_executable_and_template_content(self, executor, dangerous):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": dangerous}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert errors, f"expected rejection for {dangerous}"

    def test_rejects_coordinate_parameter_name(self, executor):
        d = make_definition(parameters=[{"name": "x", "type": "integer", "description": "screen x"}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("coordinate" in e for e in errors)

    def test_rejects_coordinate_literal(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"path": "1200,800"}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("coordinate pair" in e for e in errors)

    def test_rejects_secret_hint_keys(self, executor):
        d = make_definition(
            parameters=[{"name": "api_key", "type": "string", "description": "key"}],
            steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"path": "inputs.api_key"}}],
        )
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("secret" in e for e in errors)

    def test_rejects_nested_dict_literals(self, executor):
        d = make_definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"path": {"nested": True}}}])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("literal scalar" in e for e in errors)

    def test_rejects_duplicate_step_ids(self, executor):
        d = make_definition(steps=[
            {"step_id": "s1", "tool": "file_read", "inputs": {"path": "a"}},
            {"step_id": "s1", "tool": "file_read", "inputs": {"path": "b"}},
        ])
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("duplicate step_id" in e for e in errors)

    def test_rejects_bad_version(self, executor):
        d = make_definition(version="latest")
        errors = validate_definition(d, allowed_tool_names(executor))
        assert any("version" in e for e in errors)


class TestExecution:
    """Version pinning and step execution with restrictive reference resolution."""

    async def _run_with_lease(self, db_session, task_id):
        runner = TaskRunner(db_session)
        await runner.acquire_lease()
        task = Task(id=task_id, original_request="run skill", state="running", version=1)
        db_session.add(task)
        db_session.commit()
        return runner

    @pytest.mark.asyncio
    async def test_pins_version_and_records_events(self, db_session, executor):
        skill = _enabled_skill(db_session, executor, skill_id="skill.1")
        runner = await self._run_with_lease(db_session, "t1")
        version = skill.versions[0]

        outcome = await execute_skill(
            db_session, runner, "t1", "skill.1",
            inputs={"path": "C:\\test.txt"}, tool_executor=executor,
        )

        assert outcome.success
        assert outcome.version == version.version
        task = db_session.query(Task).filter_by(id="t1").first()
        assert task.selected_skill_version.startswith("skill.1@1.0.0@")
        events = db_session.query(TaskEvent).filter_by(task_id="t1").all()
        event_types = [e.event_type for e in events]
        assert "tool.executed" in event_types
        assert "skill.executed" in event_types

    @pytest.mark.asyncio
    async def test_step_output_reference(self, db_session, executor):
        def two_steps():
            return make_definition(steps=[
                {"step_id": "read1", "tool": "file_read", "inputs": {"path": "inputs.path"}},
                {"step_id": "read2", "tool": "file_read", "inputs": {"path": "steps.read1.output.content"}},
            ])
        _enabled_skill(db_session, executor, definition=two_steps(), skill_id="skill.chain")
        runner = await self._run_with_lease(db_session, "t-chain")

        outcome = await execute_skill(
            db_session, runner, "t-chain", "skill.chain",
            inputs={"path": "C:\\x"}, tool_executor=executor,
        )

        assert outcome.success
        # read2 read the content produced by read1 ("contents of C:\x"), so its
        # own content is "contents of " + that.
        content = outcome.outputs["read2"]["content"]
        assert content == "contents of contents of C:\\x"

    @pytest.mark.asyncio
    async def test_missing_required_input_reference_fails(self, db_session, executor):
        _enabled_skill(db_session, executor, skill_id="skill.1")
        runner = await self._run_with_lease(db_session, "t1")

        outcome = await execute_skill(
            db_session, runner, "t1", "skill.1", inputs={}, tool_executor=executor,
        )
        assert not outcome.success
        assert "missing input parameter" in (outcome.error or "")

    @pytest.mark.asyncio
    async def test_skill_must_be_enabled(self, db_session, executor):
        _enabled_skill(db_session, executor, skill_id="skill.1")
        set_skill_state(db_session, "skill.1", "disabled")
        runner = await self._run_with_lease(db_session, "t1")

        outcome = await execute_skill(
            db_session, runner, "t1", "skill.1",
            inputs={"path": "C:\\x"}, tool_executor=executor,
        )
        assert not outcome.success
        assert "not enabled" in (outcome.error or "")

    @pytest.mark.asyncio
    async def test_pauses_when_skill_disabled_before_next_step(self, db_session, executor):
        def two_steps():
            return make_definition(steps=[
                {"step_id": "read1", "tool": "file_read", "inputs": {"path": "C:\\x"}},
                {"step_id": "read2", "tool": "file_read", "inputs": {"path": "C:\\y"}},
            ])
        _enabled_skill(db_session, executor, definition=two_steps(), skill_id="skill.disable")

        # The first step disables the skill mid-run.
        async def disable_skill(_input_data):
            set_skill_state(db_session, "skill.disable", "disabled")

        executor._tools["file_read"] = FakeReadTool(side_effect=disable_skill)

        runner = await self._run_with_lease(db_session, "t-disable")
        outcome = await execute_skill(
            db_session, runner, "t-disable", "skill.disable",
            inputs={}, tool_executor=executor,
        )

        assert outcome.paused
        assert outcome.error == "skill_disabled"
        task = db_session.query(Task).filter_by(id="t-disable").first()
        assert task.state == TaskState.PAUSED.value
        # The second step must NOT have run.
        events = db_session.query(TaskEvent).filter_by(task_id="t-disable").all()
        assert [e.event_type for e in events].count("tool.executed") == 1

    @pytest.mark.asyncio
    async def test_pause_is_resumable_after_reenable(self, db_session, executor):
        def two_steps():
            return make_definition(steps=[
                {"step_id": "read1", "tool": "file_read", "inputs": {"path": "C:\\x"}},
                {"step_id": "read2", "tool": "file_read", "inputs": {"path": "C:\\y"}},
            ])
        _enabled_skill(db_session, executor, definition=two_steps(), skill_id="skill.resume")

        async def disable_skill(_input_data):
            set_skill_state(db_session, "skill.resume", "disabled")

        executor._tools["file_read"] = FakeReadTool(side_effect=disable_skill)
        runner = await self._run_with_lease(db_session, "t-resume")
        outcome = await execute_skill(
            db_session, runner, "t-resume", "skill.resume", inputs={}, tool_executor=executor,
        )
        assert outcome.paused

        # Owner re-enables; a fresh run resumes and completes the remaining steps.
        set_skill_state(db_session, "skill.resume", "enabled")
        executor._tools["file_read"] = FakeReadTool()
        outcome2 = await execute_skill(
            db_session, runner, "t-resume", "skill.resume", inputs={}, tool_executor=executor,
        )
        assert outcome2.success
        events = db_session.query(TaskEvent).filter_by(task_id="t-resume").all()
        assert [e.event_type for e in events].count("tool.executed") == 3  # 1 + 2 remaining

    @pytest.mark.asyncio
    async def test_rejects_conflicting_pin(self, db_session, executor):
        _enabled_skill(db_session, executor, skill_id="skill.1")
        _enabled_skill(db_session, executor, definition=make_definition(name="other"), skill_id="skill.2")
        runner = await self._run_with_lease(db_session, "t1")

        first = await execute_skill(
            db_session, runner, "t1", "skill.1", inputs={"path": "C:\\x"}, tool_executor=executor,
        )
        assert first.success
        second = await execute_skill(
            db_session, runner, "t1", "skill.2", inputs={"path": "C:\\x"}, tool_executor=executor,
        )
        assert not second.success
        assert "already pinned" in (second.error or "")


class TestProposals:
    """Proposed skills: only after >=2 materially similar verified traces."""

    def test_no_proposal_for_single_trace(self, db_session, executor):
        _completed_trace_task(db_session, "t-solo")
        assert maybe_generate_proposal(db_session, "t-solo", executor) is None

    def test_proposal_after_two_similar_traces(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal is not None
        assert proposal.status == "proposed"
        trace_ids = json.loads(proposal.trace_ids)
        assert "t1" in trace_ids and "t2" in trace_ids

    def test_no_proposal_for_dissimilar_traces(self, db_session, executor):
        # Different parameter-key signature => different trace => no proposal.
        _completed_trace_task(db_session, "t1", params={"path": "C:\\x", "limit": 100})
        _completed_trace_task(db_session, "t2", params={"path": "C:\\y"})
        assert maybe_generate_proposal(db_session, "t1", executor) is None

    def test_no_proposal_for_failed_trace(self, db_session, executor):
        _completed_trace_task(db_session, "t1", success=True)
        _completed_trace_task(db_session, "t2", success=False)
        assert maybe_generate_proposal(db_session, "t1", executor) is None

    def test_proposal_is_not_auto_enabled(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

    def test_approve_enables_versioned_skill(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)

        skill = approve_proposal(db_session, proposal, executor)
        assert skill.state == "enabled"
        assert len(skill.versions) == 1
        refreshed = get_proposal(db_session, proposal.id)
        assert refreshed.status == "enabled"
        # Enabled skill is now executable.
        assert get_enabled_skill_version(db_session, str(skill.id)) is not None

    def test_reject_marks_proposal(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        updated = reject_proposal(db_session, proposal)
        assert updated.status == "rejected"

    def test_approve_rejects_non_proposed_twice(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        approve_proposal(db_session, proposal, executor)
        with pytest.raises(ValueError):
            approve_proposal(db_session, proposal, executor)

    def test_revisited_task_returns_none(self, db_session, executor):
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal is not None
        # The same task contributes its evidence once; nothing new to add.
        assert maybe_generate_proposal(db_session, "t1", executor) is None

    def test_proposal_accumulates_traces_below_threshold(self, db_session, executor, monkeypatch):
        from backend.core.config import settings
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 5)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"
        assert len(json.loads(proposal.trace_ids)) == 2

        _completed_trace_task(db_session, "t3")
        again = maybe_generate_proposal(db_session, "t3", executor)
        assert again.id == proposal.id  # same proposal accumulates, no duplicate
        assert len(json.loads(proposal.trace_ids)) == 3
        assert proposal.status == "proposed"
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

    def test_proposal_auto_enables_at_threshold(self, db_session, executor, monkeypatch):
        from backend.core.config import settings
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 3)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"

        _completed_trace_task(db_session, "t3")
        proposal = maybe_generate_proposal(db_session, "t3", executor)
        assert proposal.status == "enabled"
        enabled = get_enabled_skill_version(db_session, str(proposal.id))
        assert enabled is not None
        skill, version = enabled
        assert len(skill.versions) == 1
        assert version.definition_hash  # a pinned, versioned live skill

    def test_auto_enable_revalidates_against_current_toolset(self, db_session, executor, monkeypatch):
        """A proposal whose tool is no longer allowed is never auto-enabled."""
        from backend.core.config import settings
        from backend.tools.executor import ToolExecutor
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 2)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        # file_read is gone from the executor, so the definition can no longer validate.
        proposal = maybe_generate_proposal(db_session, "t1", ToolExecutor())
        assert proposal is not None
        assert proposal.status == "proposed"
        assert json.loads(proposal.validation_errors)  # re-validation failed
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

    def test_rejected_signature_is_never_reproposed_or_auto_enabled(self, db_session, executor, monkeypatch):
        from backend.core.config import settings
        from backend.models.database import ProposedSkill
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 3)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"  # below threshold, awaiting review
        reject_proposal(db_session, proposal)

        _completed_trace_task(db_session, "t3")
        _completed_trace_task(db_session, "t4")
        _completed_trace_task(db_session, "t5")
        assert maybe_generate_proposal(db_session, "t3", executor) is None
        assert maybe_generate_proposal(db_session, "t4", executor) is None
        assert maybe_generate_proposal(db_session, "t5", executor) is None

        rows = db_session.query(ProposedSkill).all()
        assert len(rows) == 1
        assert rows[0].status == "rejected"
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

    def test_auto_enable_disabled_at_zero_threshold(self, db_session, executor, monkeypatch):
        from backend.core.config import settings
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 0)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        for tid in ["t3", "t4", "t5", "t6"]:
            _completed_trace_task(db_session, tid)
            maybe_generate_proposal(db_session, tid, executor)
        assert proposal.status == "proposed"
        assert len(json.loads(proposal.trace_ids)) == 6
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

    def test_owner_submitted_proposal_never_auto_enables(self, db_session, executor, monkeypatch):
        """Owner-submitted proposals carry a skill_id and always need review."""
        from backend.core.config import settings
        from backend.models.database import ProposedSkill
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 2)
        proposal = ProposedSkill(
            skill_id="owner.skill",
            definition=skill_engine.definition_to_json(make_definition()),
            trace_ids=json.dumps(["t1", "t2"]),  # even with enough evidence
            status="proposed",
            validation_errors="[]",
        )
        db_session.add(proposal)
        db_session.commit()
        db_session.refresh(proposal)

        assert skill_engine._auto_enable_if_ready(db_session, proposal, executor) is None
        db_session.refresh(proposal)
        assert proposal.status == "proposed"
        assert get_enabled_skill_version(db_session, "owner.skill") is None


class TestSummaries:
    def test_list_skill_summaries(self, db_session, executor):
        _enabled_skill(db_session, executor, skill_id="skill.1")
        summaries = list_skill_summaries(db_session)
        assert summaries
        assert summaries[0]["id"] == "skill.1"
        assert summaries[0]["state"] == "enabled"
        assert summaries[0]["current_version"] == "1.0.0"
        assert summaries[0]["current_definition_hash"]

    def test_definition_hash_is_deterministic(self):
        d1 = make_definition()
        d2 = make_definition()
        h1 = skill_engine.definition_hash(skill_engine.definition_to_json(d1))
        h2 = skill_engine.definition_hash(skill_engine.definition_to_json(d2))
        assert h1 == h2
        assert len(h1) == 64

    def test_definition_round_trip(self):
        d = make_definition()
        assert parse_definition(skill_engine.definition_to_json(d)) == d
