"""
Acceptance gate: per-action approval flow (Phase C+ enablement).

A confirmation-required tool call is never executed on the first pass. The loop
stages it on the task (`pending_action`), parks the task in WAITING_FOR_USER,
and only executes once the `/reply` endpoint marks it approved. A denial skips
execution and tells the model the action was refused.
"""

import json

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import DecisionType
from backend.tools.executor import ToolExecutor
from backend.tools.file_tools import create_file_tools
from backend.tools.system_tools import create_system_tools
from backend.models.database import TaskEvent


@pytest.mark.asyncio
async def test_confirmation_tool_staged_and_internally_approved_remains_blocked(db_session, task_runner, tmp_path, monkeypatch):
    """A confirmation-required tool is staged as pending permission and is
    not dispatched merely because its internal pending status is approved."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    await task_runner.acquire_lease()
    task = await task_runner.create_task("Create a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}
    target = tmp_path / "approval_test.txt"

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First turn: propose a confirmation-required mutating tool.
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_create",
                "params": {"path": str(target), "content": "hello"},
            }
        if calls["n"] == 2:
            # The approved action executes deterministically before the model
            # is asked again; the model then observes the result and completes.
            return DecisionType.COMPLETE_TASK, {"result": "File created"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    # The task must be parked, not executed.
    parked = await task_runner.get_task(task.id)
    assert parked.state == TaskState.WAITING_FOR_USER.value
    assert parked.pending_question is not None
    pending = json.loads(parked.pending_action)
    assert pending["tool_name"] == "file_create"
    assert pending["status"] == "awaiting"
    # No mutating action was started while awaiting approval.
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert not any(e.event_type == "action.started" for e in events)
    assert not target.exists()

    # User approves via the reply path: set status approved and resume.
    # The reply transition is an internal state change, not user authorization
    # to execute; durable pending permission remains required.
    from backend.models.database import Task
    pending_row = db_session.query(Task).filter_by(id=task.id).first()
    pending_payload = json.loads(pending_row.pending_action)
    pending_payload["status"] = "approved"
    pending_row.pending_action = json.dumps(pending_payload)
    db_session.commit()

    await task_runner.acquire_lease()
    try:
        await task_runner.transition_state(
            task.id, TaskState.RUNNING, "action.approved",
            {"answer": "approve", "decision": "approved"}, pending_question=None,
        )
    finally:
        await task_runner.release_lease()

    await task_runner.acquire_lease()
    await loop.execute_task(task.id)

    done = await task_runner.get_task(task.id)
    # An internally approved pending action is evidence, not consent: dispatch is
    # intentionally refused. The staged permission and approval evidence remain
    # durable for audit and external (non-internal) review.
    assert done.state == TaskState.RUNNING.value
    pending = json.loads(done.pending_action)
    assert pending["tool_name"] == "file_create"
    assert pending["status"] == "approved"
    assert not target.exists()
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert not any(e.event_type == "action.started" for e in events)
    assert not any(
        e.event_type == "tool.executed"
        and json.loads(e.event_data).get("tool_name") == "file_create"
        for e in events
    )
    assert any(e.event_type == "action.approved" for e in events)
    assert effect_calls["n"] == 0
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_confirmation_tool_denied_skips_execution(db_session, task_runner, tmp_path, monkeypatch):
    """A denied action never runs; the loop reports the refusal and moves on."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    await task_runner.acquire_lease()
    task = await task_runner.create_task("Create a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}
    target = tmp_path / "denied_test.txt"

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_create",
                "params": {"path": str(target), "content": "hello"},
            }
        if calls["n"] == 2:
            # After denial, the model re-proposes (it cannot know yet): the loop
            # must recognize the denied status and skip without executing.
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_create",
                "params": {"path": str(target), "content": "hello"},
            }
        return DecisionType.COMPLETE_TASK, {"result": "Nothing was created"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    parked = await task_runner.get_task(task.id)
    assert parked.state == TaskState.WAITING_FOR_USER.value
    pending = json.loads(parked.pending_action)
    assert pending["status"] == "awaiting"

    # User denies.
    # The denial transition is internal state handling, not authorization to
    # execute any external action.
    await task_runner.acquire_lease()
    try:
        await task_runner.set_pending_action_status(task.id, "denied")
        await task_runner.transition_state(
            task.id, TaskState.RUNNING, "action.denied",
            {"answer": "no", "decision": "denied"}, pending_question=None,
        )
    finally:
        await task_runner.release_lease()

    await task_runner.acquire_lease()
    await loop.execute_task(task.id)

    done = await task_runner.get_task(task.id)
    assert done.state == TaskState.COMPLETED.value
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    # The file_create tool never executed.
    assert not any(
        e.event_type == "tool.executed"
        and json.loads(e.event_data).get("tool_name") == "file_create"
        for e in events
    )
    assert not any(e.event_type == "action.started" for e in events)
    assert not target.exists()
    assert effect_calls["n"] == 0
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_internally_approved_action_is_not_dispatched_despite_model_flapping(db_session, task_runner, tmp_path, monkeypatch):
    """Internal approval status does not dispatch the blocked action, even if
    the model proposes another tool during an attempted resume; no file or
    tool effect is produced.

    Regression guard: previously an internally approved action could be treated
    as dispatchable; it must remain blocked for external consent.
    """
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    await task_runner.acquire_lease()
    task = await task_runner.create_task("Create a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}
    target = tmp_path / "flap_test.txt"

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First turn: propose a confirmation-required mutating tool.
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_create",
                "params": {"path": str(target), "content": "hello"},
            }
        # After approval the model flaps to an unrelated read-only tool first.
        return DecisionType.EXECUTE_TOOL, {
            "tool_name": "system_active_window",
            "params": {},
        }

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)
    parked = await task_runner.get_task(task.id)
    assert parked.state == TaskState.WAITING_FOR_USER.value

    # User approves; resume execution.
    # The approval transition is internal runner state, not user consent by itself.
    from backend.models.database import Task
    pending_row = db_session.query(Task).filter_by(id=task.id).first()
    pending_payload = json.loads(pending_row.pending_action)
    pending_payload["status"] = "approved"
    pending_row.pending_action = json.dumps(pending_payload)
    db_session.commit()

    await task_runner.acquire_lease()
    try:
        await task_runner.transition_state(
            task.id, TaskState.RUNNING, "action.approved",
            {"answer": "approve", "decision": "approved"}, pending_question=None,
        )
    finally:
        await task_runner.release_lease()

    await task_runner.acquire_lease()
    await loop.execute_task(task.id)

    # An internal approval status is not authenticated human consent, so the
    # approved action is intentionally not dispatched even if the model proposes
    # another tool. No file is created and no tool is executed.
    assert not target.exists()
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert not any(
        e.event_type == "tool.executed"
        and json.loads(e.event_data).get("tool_name") == "file_create"
        for e in events
    )
    assert effect_calls["n"] == 0
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_non_confirmation_tool_runs_without_approval(db_session, task_runner):
    """Low-risk tools (no confirmation) execute immediately, no staging."""
    ex = ToolExecutor()
    for tool in create_system_tools():
        ex.register_tool(tool)

    await task_runner.acquire_lease()
    task = await task_runner.create_task("What time is it?")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "system_time",
                "params": {},
            }
        return DecisionType.COMPLETE_TASK, {"result": "Time read"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    done = await task_runner.get_task(task.id)
    assert done.state == TaskState.COMPLETED.value
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert any(e.event_type == "tool.executed" for e in events)
    assert not any(e.event_type == "action.pending_approval" for e in events)
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_autonomous_mode_does_not_self_approve_destructive_action(db_session, task_runner, tmp_path, monkeypatch):
    """Autonomous mode does not self-approve destructive tools; they are staged
    for explicit approval and not dispatched."""
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    target = tmp_path / "doomed.txt"
    target.write_text("data")

    await task_runner.acquire_lease()
    task = await task_runner.create_task("Delete a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_delete",
                "params": {"path": str(target)},
            }
        return DecisionType.COMPLETE_TASK, {"result": "Deleted"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    done = await task_runner.get_task(task.id)
    # Autonomy/config flags are not authenticated human consent. A consequential
    # destructive action is staged for explicit approval rather than executed.
    assert done.state == TaskState.WAITING_FOR_USER.value
    assert done.pending_question is not None
    pending = json.loads(done.pending_action)
    assert pending["tool_name"] == "file_delete"
    assert pending["status"] == "awaiting"
    assert target.exists()
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert not any(e.event_type == "tool.executed" for e in events)
    assert any(e.event_type == "action.pending_approval" for e in events)
    assert effect_calls["n"] == 0
    await task_runner.release_lease()
