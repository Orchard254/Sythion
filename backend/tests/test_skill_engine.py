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
from backend.tools.file_tools import FileReadTool


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
    """A reviewed FileReadTool instance with controlled in-memory read behavior.

    `__class__` is normalized onto :class:`FileReadTool` so the containment
    reviewed built-in identity check recognizes the tool without weakening policy.
    """

    def __new__(cls, side_effect=None):
        tool = FileReadTool()
        tool._side_effect = side_effect

        async def execute(input_data):
            if tool._side_effect:
                await tool._side_effect(input_data)
            data = {
                "content": f"contents of {input_data.path}",
                "length": len(input_data.path),
            }
            return ToolOutput(
                success=True,
                data=data,
                model_summary=f"read {input_data.path}",
            )

        tool.execute = execute
        tool.validate_targets = lambda input_data: None
        tool.__class__ = FileReadTool
        return tool


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

    def set_cancelled_check(self, check):
        pass

    def clear_cancelled_check(self):
        pass


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
            "params": params if params is not None else {"path": "C:\\x", "limit": 100},
            "output": {"content": "ok"},
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
        _completed_trace_task(db_session, "t1", params={"path": "C:\\x", "max_bytes": 100})
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

    def test_proposal_never_auto_enabled_by_trace_count(
        self, db_session, executor, monkeypatch
    ):
        """Verified traces grow a proposal but never enable it.

        Enablement is an explicit owner decision with a durable receipt. A
        trace count — even far above any legacy threshold — must not create a
        live skill. The explicit approval path must still enable it.
        """
        from backend.core.config import settings

        # Even a permissive legacy threshold must not authorize auto-enable.
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 3)
        for tid in ("t1", "t2"):
            _completed_trace_task(db_session, tid)
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"

        for tid in ("t3", "t4", "t5", "t6"):
            _completed_trace_task(db_session, tid)
            maybe_generate_proposal(db_session, tid, executor)

        db_session.refresh(proposal)
        assert proposal.status == "proposed"
        assert len(json.loads(proposal.trace_ids)) == 6
        assert proposal.skill_id is None
        assert proposal.decided_at is None  # no owner decision / receipt
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

        # Explicit owner approval still enables it and records the receipt.
        skill = approve_proposal(db_session, proposal, executor)
        assert skill.state == "enabled"
        enabled = get_enabled_skill_version(db_session, str(skill.id))
        assert enabled is not None
        enabled_skill, version = enabled
        assert len(enabled_skill.versions) == 1
        assert version.definition_hash  # a pinned, versioned live skill
        db_session.refresh(proposal)
        assert proposal.status == "enabled"
        assert proposal.decided_at is not None

    def test_auto_enable_helper_cannot_enable_by_itself(
        self, db_session, executor, monkeypatch
    ):
        """The legacy auto-enable helper is a no-op even with a low threshold."""
        from backend.core.config import settings

        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 2)
        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal.status == "proposed"
        assert skill_engine._auto_enable_if_ready(db_session, proposal, executor) is None
        db_session.refresh(proposal)
        assert proposal.status == "proposed"
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

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


class TestProposalSchemaValidation:
    """Regression tests verifying proposal generation strictly separates input schemas from output fields."""

    @pytest.fixture
    def full_executor(self):
        from backend.tools.executor import ToolExecutor
        from backend.tools.registry import register_all_tools

        ex = ToolExecutor()
        register_all_tools(ex)
        return ex

    def test_proposal_no_input_tool_system_battery(self, db_session, full_executor):
        """A tool with no inputs (like system_battery) produces empty step inputs even if trace had output fields."""
        def _battery_task(task_id, percent):
            task = Task(id=task_id, original_request="check battery", state="completed", version=1)
            db_session.add(task)
            event = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "system_battery",
                    "success": True,
                    "params": {
                        "available": True,
                        "percent": percent,
                        "plugged_in": True,
                        "time_left": 3600,
                    },
                    "output": {
                        "available": True,
                        "percent": percent,
                        "plugged_in": True,
                        "time_left": 3600,
                    },
                }),
                sequence_num=1,
            )
            db_session.add(event)
            db_session.commit()

        _battery_task("tb-1", 95)
        _battery_task("tb-2", 90)

        proposal = maybe_generate_proposal(db_session, "tb-1", full_executor)
        assert proposal is not None
        definition = parse_definition(proposal.definition)
        assert len(definition.steps) == 1
        assert definition.steps[0].tool == "system_battery"
        assert definition.steps[0].inputs == {}
        assert definition.parameters == []

        errors = validate_definition(definition, allowed_tool_names(full_executor))
        assert errors == []
        assert json.loads(proposal.validation_errors) == []

    def test_proposal_required_input_tool_file_read(self, db_session, full_executor):
        """A tool with required inputs filters out extraneous output fields and abstracts varied inputs."""
        def _read_task(task_id, path):
            task = Task(id=task_id, original_request="read file", state="completed", version=1)
            db_session.add(task)
            event = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "file_read",
                    "success": True,
                    "params": {
                        "path": path,
                        "max_bytes": 1000,
                        "content": "hello file",
                        "bytes_read": 10,
                    },
                    "output": {
                        "content": "hello file",
                        "bytes_read": 10,
                    },
                }),
                sequence_num=1,
            )
            db_session.add(event)
            db_session.commit()

        _read_task("tr-1", "C:\\data\\file1.txt")
        _read_task("tr-2", "C:\\data\\file2.txt")

        proposal = maybe_generate_proposal(db_session, "tr-1", full_executor)
        assert proposal is not None
        definition = parse_definition(proposal.definition)
        assert len(definition.steps) == 1
        assert definition.steps[0].tool == "file_read"
        assert "path" in definition.steps[0].inputs
        assert definition.steps[0].inputs["max_bytes"] == 1000
        # Output fields content and bytes_read were filtered out
        assert "content" not in definition.steps[0].inputs
        assert "bytes_read" not in definition.steps[0].inputs

        errors = validate_definition(definition, allowed_tool_names(full_executor))
        assert errors == []
        assert json.loads(proposal.validation_errors) == []

    def test_proposal_multi_step_trace(self, db_session, full_executor):
        """Multi-step traces combine no-input and input tools cleanly and validate."""
        def _multi_task(task_id, filename):
            task = Task(id=task_id, original_request="battery to file", state="completed", version=1)
            db_session.add(task)
            event1 = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "system_battery",
                    "success": True,
                    "params": {"available": True, "percent": 90},
                    "output": {"available": True, "percent": 90},
                }),
                sequence_num=1,
            )
            event2 = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "file_read",
                    "success": True,
                    "params": {"path": f"C:\\logs\\{filename}"},
                    "output": {"content": "ok"},
                }),
                sequence_num=2,
            )
            db_session.add(event1)
            db_session.add(event2)
            db_session.commit()

        _multi_task("tm-1", "battery_log.txt")
        _multi_task("tm-2", "battery_log.txt")

        proposal = maybe_generate_proposal(db_session, "tm-1", full_executor)
        assert proposal is not None
        definition = parse_definition(proposal.definition)
        assert len(definition.steps) == 2
        assert definition.steps[0].tool == "system_battery"
        assert definition.steps[0].inputs == {}
        assert definition.steps[1].tool == "file_read"
        assert definition.steps[1].inputs == {"path": "C:\\logs\\battery_log.txt"}

        errors = validate_definition(definition, allowed_tool_names(full_executor))
        assert errors == []
        assert json.loads(proposal.validation_errors) == []

    def test_valid_no_input_proposal_waits_for_owner_approval(
        self, db_session, full_executor, monkeypatch
    ):
        """A valid no-input proposal stays proposed until the owner approves it."""
        from backend.core.config import settings
        monkeypatch.setattr(settings, "skill_auto_enable_threshold", 2)

        def _battery_task(task_id):
            task = Task(id=task_id, original_request="check battery", state="completed", version=1)
            db_session.add(task)
            event = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "system_battery",
                    "success": True,
                    "params": {"available": True, "percent": 100},
                    "output": {"available": True, "percent": 100},
                }),
                sequence_num=1,
            )
            db_session.add(event)
            db_session.commit()

        _battery_task("tae-1")
        _battery_task("tae-2")

        proposal = maybe_generate_proposal(db_session, "tae-1", full_executor)
        assert proposal is not None
        assert proposal.status == "proposed"
        assert json.loads(proposal.validation_errors) == []
        assert get_enabled_skill_version(db_session, str(proposal.id)) is None

        skill = approve_proposal(db_session, proposal, full_executor)
        assert skill.state == "enabled"
        enabled = get_enabled_skill_version(db_session, str(skill.id))
        assert enabled is not None


class TestSkillProposalsVerification:
    """Tests for the read-back verification added to skill_proposals.py to prevent silent commit failures."""

    def test_maybe_generate_proposal_trace_ids_verification_failure_returns_none(self, db_session, executor, monkeypatch):
        """_auto_enable_if_ready trace_ids update returns None when verification fails (fix behavior).

        This test FAILS on pre-fix code because:
        1. Pre-fix code lacks the sessionmaker import (AttributeError when patching)
        2. Pre-fix code has no verification - it always returns the proposal on trace_ids update
        """
        from unittest.mock import patch, MagicMock

        # Create two similar completed tasks
        def _completed_trace_task(db, task_id):
            task = Task(id=task_id, original_request="test", state="completed", version=1)
            db.add(task)
            event = TaskEvent(
                task_id=task_id,
                event_type="tool.executed",
                event_data=json.dumps({
                    "tool_name": "file_read",
                    "success": True,
                    "params": {"path": f"C:\\test\\{task_id}.txt"},
                    "output": {"content": "ok"},
                }),
                sequence_num=1,
            )
            db.add(event)
            db.commit()

        _completed_trace_task(db_session, "t1")
        _completed_trace_task(db_session, "t2")

        # Create a proposal
        proposal = maybe_generate_proposal(db_session, "t1", executor)
        assert proposal is not None

        # Patch the module's sessionmaker to simulate read-back failure for trace_ids update
        mock_session_instance = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = None
        mock_session_instance.query.return_value = mock_query
        mock_session_instance.__enter__ = lambda self: mock_session_instance
        mock_session_instance.__exit__ = lambda *args: None

        mock_sessionmaker_class = MagicMock()
        mock_sessionmaker_class.return_value = mock_session_instance

        with patch('backend.services.skill_proposals.sessionmaker', mock_sessionmaker_class):
            # Call maybe_generate_proposal again with t2 - should update trace_ids
            result = maybe_generate_proposal(db_session, "t2", executor)

            # Fix behavior: returns None when trace_ids verification fails
            # Pre-fix behavior: returns proposal (no verification)
            assert result is None, "Expected None when verification fails, got proposal"


class TestSkillRuntimeVerification:
    """Tests for the read-back verification added to skill_runtime.py to prevent silent commit failures."""

    @pytest.mark.asyncio
    async def test_execute_skill_pin_verification_failure_returns_error(self, db_session, executor, monkeypatch):
        """execute_skill returns error when task.selected_skill_version pin verification fails (fix behavior).

        This test FAILS on pre-fix code because:
        1. Pre-fix code lacks the sessionmaker import (AttributeError when patching)
        2. Pre-fix code has no verification - it commits the pin but doesn't verify it persisted
        """
        from unittest.mock import patch, MagicMock

        # Create a skill with a version using system_battery (no input tool)
        def _enabled_skill(db, exec_, skill_id="skill.pin", definition=None):
            from backend.services.skill_runtime import (
                SkillDefinition, SkillParameter, SkillStep,
                create_skill, add_skill_version, parse_definition, validate_definition
            )
            if definition is None:
                definition = SkillDefinition(
                    version="1.0.0",
                    name="Pin Test Skill",
                    steps=[SkillStep(step_id="s1", tool="system_battery", inputs={})],
                )
            skill = create_skill(db, skill_id=skill_id, name=definition.name, description=definition.description)
            errors = validate_definition(definition, allowed_tool_names(exec_))
            add_skill_version(db, skill, definition, errors)
            db.commit()
            return skill

        _enabled_skill(db_session, executor, skill_id="skill.pin")

        # Create a task
        from backend.models.database import Task
        task = Task(id="task-pin-verify", original_request="test pin", state="running", normalized_goal="test")
        db_session.add(task)
        db_session.commit()

        # Mock runner
        class MockRunner:
            async def get_task(self, task_id):
                return None
            async def record_event(self, task_id, event_type, event_data):
                pass

        runner = MockRunner()

        # Patch the module's sessionmaker to simulate read-back failure for pin verification
        mock_session_instance = MagicMock()
        mock_query = MagicMock()
        mock_query.filter_by.return_value.first.return_value = None  # Verification fails - pin not found
        mock_session_instance.query.return_value = mock_query
        mock_session_instance.__enter__ = lambda self: mock_session_instance
        mock_session_instance.__exit__ = lambda *args: None

        mock_sessionmaker_class = MagicMock()
        mock_sessionmaker_class.return_value = mock_session_instance

        with patch('backend.services.skill_runtime.sessionmaker', mock_sessionmaker_class):
            # Execute skill - should fail verification and return error
            outcome = await execute_skill(
                db=db_session,
                runner=runner,
                task_id="task-pin-verify",
                skill_id="skill.pin",
                inputs={},
                tool_executor=executor,
            )

            # Fix behavior: returns SkillRunOutcome(success=False, error="Failed to pin skill version")
            # Pre-fix behavior: would commit pin without verification (or raise on commit failure)
            assert not outcome.success
            assert "Failed to pin skill version" in (outcome.error or ""), f"Expected pin verification error, got: {outcome.error}"

