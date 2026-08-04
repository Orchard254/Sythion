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
async def test_confirmation_tool_staged_and_approved(db_session, task_runner, tmp_path, monkeypatch):
    """A confirmation-required tool is staged for approval, then runs only
    after the pending action is marked approved."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)

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
    await task_runner.set_pending_action_status(task.id, "approved")
    await task_runner.transition_state(
        task.id, TaskState.RUNNING, "action.approved",
        {"answer": "approve", "decision": "approved"}, pending_question=None,
    )

    await loop.execute_task(task.id)

    done = await task_runner.get_task(task.id)
    assert done.state == TaskState.COMPLETED.value
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert any(e.event_type == "tool.executed" for e in events)
    assert any(e.event_type == "action.approved" for e in events)
    assert target.exists()
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_confirmation_tool_denied_skips_execution(db_session, task_runner, tmp_path, monkeypatch):
    """A denied action never runs; the loop reports the refusal and moves on."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)

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
    await task_runner.set_pending_action_status(task.id, "denied")
    await task_runner.transition_state(
        task.id, TaskState.RUNNING, "action.denied",
        {"answer": "no", "decision": "denied"}, pending_question=None,
    )

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
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_approved_action_executes_despite_model_flapping(db_session, task_runner, tmp_path, monkeypatch):
    """An approved action runs deterministically even if the model changes its
    mind on resume and proposes something else first.

    Regression for a live failure: after the user approved `app_launch`, the
    model re-decided (web_search / a different app id) and the approved action
    never executed.
    """
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)

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
    await task_runner.set_pending_action_status(task.id, "approved")
    await task_runner.transition_state(
        task.id, TaskState.RUNNING, "action.approved",
        {"answer": "approve", "decision": "approved"}, pending_question=None,
    )

    await loop.execute_task(task.id)

    # The approved file_create MUST have executed despite the model proposing
    # system_active_window (which is not even registered in this executor).
    assert target.exists(), "approved action was lost to model flapping"
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert any(
        e.event_type == "tool.executed"
        and json.loads(e.event_data).get("tool_name") == "file_create"
        for e in events
    )
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
