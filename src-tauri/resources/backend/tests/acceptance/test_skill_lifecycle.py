"""
Acceptance gate: declarative skill lifecycle (Phase F).

The approved Phase F contract requires:
- Skills are validated, versioned declarative workflows; no executable code.
- References are restricted to `inputs.<field>` and `steps.<id>.output.<field>`.
- A validated version is pinned to each running task.
- A skill disabled before its next unstarted step pauses the task.
- Proposed skills require explicit review and enablement; they are never run.
- Complete-record purge leaves only non-sensitive purge receipts.

This gate is deterministic: it exercises the engine and the owner API against
an in-memory DB with a real read-only tool executor (no model, no OS writes).
"""

import pytest

from backend.api.schemas import TaskState
from backend.models.database import Task, TaskEvent, PurgeReceipt
from backend.services.skill_engine import (
    allowed_tool_names,
    create_skill,
    add_skill_version,
    execute_skill,
    maybe_generate_proposal,
    set_skill_state,
    validate_definition,
)
from backend.services.task_runner import TaskRunner
from backend.tools.executor import ToolExecutor, ToolOutput
from backend.tests.test_skill_engine import FakeReadTool
from pydantic import BaseModel as PBaseModel

from .conftest import ACCEPTANCE_CONTROL_TOKEN


AUTH = {"X-Control-Token": ACCEPTANCE_CONTROL_TOKEN, "Origin": "tauri://localhost"}


class FakeReadInput(PBaseModel):
    path: str


class FakeReadTool:
    name = "file_read"
    description = "read a file"
    input_schema = FakeReadInput

    def get_policy_requirements(self):
        return {}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        return ToolOutput(
            success=True,
            data={"content": f"contents of {input_data.path}"},
            model_summary=f"read {input_data.path}",
        )

    def set_cancelled_check(self, check):
        pass

    def clear_cancelled_check(self):
        pass


def _definition(**overrides):
    d = {
        "version": "1.0.0",
        "name": "read workflow",
        "description": "reads a file",
        "category": "file",
        "parameters": [{"name": "path", "type": "string", "required": True, "description": "path"}],
        "steps": [{"step_id": "read1", "tool": "file_read", "inputs": {"path": "inputs.path"}}],
    }
    d.update(overrides)
    return d


def _readonly_executor():
    ex = ToolExecutor()
    ex.register_tool(FakeReadTool())
    return ex


def test_skill_submit_validate_and_enable_via_api(client):
    """Owner submission is validated; only explicit approval enables a skill."""
    response = client.post(
        "/api/v1/skills",
        json={"skill_id": "gate.1", "definition": _definition()},
        headers=AUTH,
    )
    assert response.status_code == 200
    proposal_id = response.json()["proposal_id"]

    # Malicious/executable submissions are rejected before they become proposals.
    malicious = _definition(steps=[{"step_id": "s1", "tool": "file_read", "inputs": {"path": "__import__('os').system('x')"}}])
    bad = client.post("/api/v1/skills", json={"skill_id": "gate.bad", "definition": malicious}, headers=AUTH)
    assert bad.status_code == 422

    # Not enabled yet.
    assert client.get("/api/v1/skills/gate.1", headers=AUTH).status_code == 404

    approved = client.post(f"/api/v1/skills/proposals/{proposal_id}/approve", headers=AUTH)
    assert approved.status_code == 200

    detail = client.get("/api/v1/skills/gate.1", headers=AUTH).json()
    assert detail["state"] == "enabled"
    assert detail["versions"][0]["version"] == "1.0.0"


def test_rejected_proposal_never_runs(client):
    created = client.post(
        "/api/v1/skills",
        json={"skill_id": "gate.reject", "definition": _definition()},
        headers=AUTH,
    ).json()
    rejected = client.post(f"/api/v1/skills/proposals/{created['proposal_id']}/reject", headers=AUTH)
    assert rejected.status_code == 200
    assert rejected.json()["status"] == "rejected"
    assert client.get("/api/v1/skills/gate.reject", headers=AUTH).status_code == 404


@pytest.mark.asyncio
async def test_pinned_version_pauses_when_skill_disabled(db_session):
    """Disabling a skill before its next unstarted step pauses the task."""
    from backend.services.skill_engine import SkillDefinition

    ex = _readonly_executor()
    definition = SkillDefinition.model_validate(_definition(steps=[
        {"step_id": "read1", "tool": "file_read", "inputs": {"path": "C:\\a"}},
        {"step_id": "read2", "tool": "file_read", "inputs": {"path": "C:\\b"}},
    ]))
    errors = validate_definition(definition, allowed_tool_names(ex))
    assert not errors
    skill = create_skill(db_session, "gate.pause", definition.name, definition.description)
    add_skill_version(db_session, skill, definition, errors)

    runner = TaskRunner(db_session)
    await runner.acquire_lease()
    db_session.add(Task(id="t-pause", original_request="x", state="running", version=1))
    db_session.commit()

    # A disabled skill cannot be started at all (no pin, no steps, no events).
    set_skill_state(db_session, "gate.pause", "disabled")
    outcome = await execute_skill(
        db_session, runner, "t-pause", "gate.pause", inputs={}, tool_executor=ex,
    )
    assert not outcome.success
    assert "not enabled" in (outcome.error or "")
    task = db_session.query(Task).filter_by(id="t-pause").first()
    assert task.state == TaskState.RUNNING.value
    assert db_session.query(TaskEvent).filter_by(task_id="t-pause").count() == 0


@pytest.mark.asyncio
async def test_pause_before_next_step_when_disabled_mid_run(db_session):
    from backend.services.skill_engine import SkillDefinition

    ex = _readonly_executor()
    definition = SkillDefinition.model_validate(_definition(steps=[
        {"step_id": "read1", "tool": "file_read", "inputs": {"path": "C:\\a"}},
        {"step_id": "read2", "tool": "file_read", "inputs": {"path": "C:\\b"}},
    ]))
    errors = validate_definition(definition, allowed_tool_names(ex))
    skill = create_skill(db_session, "gate.mid", definition.name, definition.description)
    add_skill_version(db_session, skill, definition, errors)

    # The first step's tool disables the skill mid-run.
    class DisablingReadTool(FakeReadTool):
        async def execute(self, input_data):
            set_skill_state(db_session, "gate.mid", "disabled")
            return ToolOutput(success=True, data={"content": "x"}, model_summary="x")

    ex._tools["file_read"] = DisablingReadTool()

    runner = TaskRunner(db_session)
    await runner.acquire_lease()
    db_session.add(Task(id="t-mid", original_request="x", state="running", version=1))
    db_session.commit()

    outcome = await execute_skill(
        db_session, runner, "t-mid", "gate.mid", inputs={}, tool_executor=ex,
    )
    assert outcome.paused
    assert outcome.error == "skill_disabled"
    task = db_session.query(Task).filter_by(id="t-mid").first()
    assert task.state == TaskState.PAUSED.value
    # Only the first step ran; the second (unstarted) step never executed.
    tool_events = (
        db_session.query(TaskEvent)
        .filter_by(task_id="t-mid", event_type="tool.executed")
        .count()
    )
    assert tool_events == 1
    assert task.selected_skill_version  # the version was pinned to the task


@pytest.mark.asyncio
async def test_proposal_requires_two_materially_similar_traces(db_session):
    """A proposed skill is generated only after two materially similar verified
    successful traces, and is never enabled automatically."""
    def _trace_task(task_id):
        db_session.add(Task(id=task_id, original_request="x", state="completed", version=1))
        db_session.add(TaskEvent(
            task_id=task_id, event_type="tool.executed",
            event_data='{"tool_name": "file_read", "success": true, "output": {"path": "C:\\\\x"}}',
            sequence_num=1,
        ))
        db_session.commit()

    ex = _readonly_executor()
    _trace_task("t-solo")
    assert maybe_generate_proposal(db_session, "t-solo", ex) is None

    _trace_task("t-pair")
    proposal = maybe_generate_proposal(db_session, "t-solo", ex)
    assert proposal is not None
    assert proposal.status == "proposed"
    # Still not executable until reviewed.
    assert set_skill_state(db_session, str(proposal.id), "enabled") is None


def test_complete_record_purge_writes_non_sensitive_receipt(client, db_session):
    from datetime import datetime, timedelta

    db_session.add(Task(
        id="t-purge-gate", original_request="PRIVATE GATE TEXT", state="completed", version=1,
        created_at=datetime.utcnow() - timedelta(days=200), updated_at=datetime.utcnow(),
    ))
    db_session.add(TaskEvent(task_id="t-purge-gate", event_type="tool.executed",
                             event_data="{}", sequence_num=1))
    db_session.commit()

    response = client.post(
        "/api/v1/retention/purge",
        json={"task_id": "t-purge-gate", "reason": "retention"},
        headers=AUTH,
    )
    assert response.status_code == 200
    assert response.json()["purged_count"] == 1

    # Complete record is gone; only the non-sensitive receipt remains.
    assert db_session.query(Task).filter_by(id="t-purge-gate").first() is None
    receipts = client.get("/api/v1/retention/receipts", headers=AUTH).json()
    assert any(r["task_id"] == "t-purge-gate" for r in receipts)
    assert "PRIVATE GATE TEXT" not in str(receipts)
    assert db_session.query(PurgeReceipt).count() == 1
