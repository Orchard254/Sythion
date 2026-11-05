"""Typed permission metadata for staged action approvals.

These tests establish that the consequential-action wait is explicitly typed as
permission and that an approved replay carries a stable action ID. They do not
establish immutability of tool parameters, approval of a task version, or
exactly-once external effects; concurrent/replay ownership safety relies on
backend/tests/test_task_ownership.py and backend/tests/test_narrow_safety_blocker_worker.py.
"""

import json

import pytest

from backend.api.schemas import TaskState
from backend.models.database import TaskEvent
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import DecisionType
from backend.tools.executor import ToolExecutor
from backend.tools.file_tools import create_file_tools


@pytest.mark.asyncio
async def test_consequential_action_stages_typed_permission_metadata(db_session, task_runner, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    await task_runner.acquire_lease()
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    task = await task_runner.create_task("Create a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)

    target = tmp_path / "permission_metadata.txt"

    async def _decision(self, *args, **kwargs):
        return (
            DecisionType.EXECUTE_TOOL,
            {"tool_name": "file_create", "params": {"path": str(target), "content": "hello"}},
        )

    loop.model_engine.make_decision = _decision.__get__(loop, type(loop))

    await loop.execute_task(task.id)

    parked = await task_runner.get_task(task.id)
    assert parked.state == TaskState.WAITING_FOR_USER.value
    pending = json.loads(parked.pending_action)
    assert pending["kind"] == "permission"
    assert pending["tool_name"] == "file_create"
    assert pending["status"] == "awaiting"
    # The staged action ID is execution linkage only. It is reused if the same
    # approval is replayed, but it does not make params/task-version immutable or
    # provide a cryptographic consent binding.
    assert isinstance(pending.get("action_id"), str) and pending["action_id"]
    assert not target.exists()
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).all()
    assert not any(e.event_type == "action.started" for e in events)
    assert effect_calls["n"] == 0
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_approved_permission_status_remains_blocked_and_staged_action_preserved(db_session, task_runner, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    await task_runner.acquire_lease()
    ex = ToolExecutor()
    for tool in create_file_tools():
        ex.register_tool(tool)
    effect_calls = {"n": 0}
    original_execute = ex.execute_tool

    async def _counting_execute(*args, **kwargs):
        effect_calls["n"] += 1
        return await original_execute(*args, **kwargs)

    ex.execute_tool = _counting_execute

    task = await task_runner.create_task("Create a file")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)

    target = tmp_path / "bound_permission.txt"
    staged_binding = {}

    async def _decision(self, *args, **kwargs):
        if not staged_binding:
            return (
                DecisionType.EXECUTE_TOOL,
                {"tool_name": "file_create", "params": {"path": str(target), "content": "hello"}},
            )
        return DecisionType.COMPLETE_TASK, {"result": "File created"}

    loop.model_engine.make_decision = _decision.__get__(loop, type(loop))

    await loop.execute_task(task.id)
    parked = await task_runner.get_task(task.id)
    pending = json.loads(parked.pending_action)
    staged_binding["id"] = pending["action_id"]

    # Set only the synthetic pending status field directly in the test so the
    # approved branch can be exercised without manufacturing authenticated
    # human consent. The helper refuses this transition by design.
    from backend.models.database import Task
    pending_task = db_session.query(Task).filter_by(id=task.id).first()
    pending_data = json.loads(pending_task.pending_action)
    pending_data["status"] = "approved"
    pending_task.pending_action = json.dumps(pending_data)
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
    # Internally stored approval status is durable evidence only, not
    # independently authenticated human consent. The approved pending action
    # is therefore intentionally not dispatched or replayed.
    assert done.state == TaskState.RUNNING.value
    pending = json.loads(done.pending_action)
    assert pending["action_id"] == staged_binding["id"]
    assert pending["status"] == "approved"
    assert not target.exists()
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).all()
    assert not any(e.event_type == "action.started" for e in events)
    assert not any(
        e.event_type == "tool.executed"
        and json.loads(e.event_data).get("tool_name") == "file_create"
        for e in events
    )
    assert any(e.event_type == "action.approved" for e in events)
    assert effect_calls["n"] == 0
    await task_runner.release_lease()