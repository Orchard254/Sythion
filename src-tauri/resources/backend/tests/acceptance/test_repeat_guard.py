"""
Acceptance gate: repeat guards.

A model that proposes the same (tool, params) call over and over is signalling
no-progress. The loop refuses to re-run an identical call that already failed
(repeat guard), and also refuses an identical call that keeps succeeding with no
state change (spin guard). In both cases the model is told to change approach
and the task fails quickly if it keeps up -- instead of burning model tokens or
the whole action budget.
"""

import json

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import DecisionType
from backend.models.database import TaskEvent


def _failed_code(db_session, task_id):
    events = db_session.query(TaskEvent).filter_by(task_id=task_id).all()
    for e in reversed(events):
        if e.event_type == "task.failed":
            return json.loads(e.event_data).get("code")
    return None


def _event_types(db_session, task_id):
    events = db_session.query(TaskEvent).filter_by(task_id=task_id).all()
    return [e.event_type for e in events]


@pytest.mark.asyncio
async def test_repeating_failing_call_is_blocked_then_task_fails(db_session, task_runner):
    """An identical failing call runs once, then identical proposals are
    blocked and the task fails instead of retrying forever."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executions = {"n": 0}
    decisions = {"n": 0}

    async def _decision(*args, **kwargs):
        decisions["n"] += 1
        return DecisionType.EXECUTE_TOOL, {"tool_name": "fixture_tool", "params": {"arg": "same"}}

    async def _tool(*args, **kwargs):
        executions["n"] += 1
        return False

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "REPEATED_ACTION_FAILURE"
    # The identical failing call executed exactly once; every later identical
    # proposal was refused without execution, and the task failed quickly.
    assert executions["n"] == 1
    assert decisions["n"] <= 4
    assert "action.repeat_blocked" in _event_types(db_session, task.id)
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_repeated_successful_call_is_not_blocked(db_session, task_runner):
    """A successful call may legitimately be used again; only failures count."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executions = {"n": 0}

    async def _decision(*args, **kwargs):
        if executions["n"] < 2:
            return DecisionType.EXECUTE_TOOL, {"tool_name": "fixture_tool", "params": {"arg": "same"}}
        return DecisionType.COMPLETE_TASK, {"result": "done"}

    async def _tool(*args, **kwargs):
        executions["n"] += 1
        return True

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value
    assert executions["n"] == 2
    assert "action.repeat_blocked" not in _event_types(db_session, task.id)
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_repeated_successful_call_with_no_change_is_spin_blocked_then_fails(db_session, task_runner):
    """A call that keeps succeeding identically with no state change is a
    spin. After the block limit identical proposals are refused; if the model
    keeps spinning the task fails instead of burning the whole action budget."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executions = {"n": 0}
    decisions = {"n": 0}

    async def _decision(*args, **kwargs):
        decisions["n"] += 1
        return DecisionType.EXECUTE_TOOL, {"tool_name": "fixture_tool", "params": {"arg": "same"}}

    async def _tool(*args, **kwargs):
        executions["n"] += 1
        return True

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "SPINNING_ACTION"
    # With REPEAT_SPIN_BLOCK_LIMIT=3 the identical success runs twice, is then
    # refused, and the task fails at REPEAT_SPIN_FAIL_LIMIT=5 -- not after 50.
    assert executions["n"] == 2
    assert decisions["n"] == 5
    assert "action.spin_blocked" in _event_types(db_session, task.id)
    assert "action.repeat_blocked" not in _event_types(db_session, task.id)
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_spin_guard_needs_consecutive_identical_proposals(db_session, task_runner):
    """Alternating between different successful calls is not a spin; each
    identical run resets when a different call is proposed."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executions = {"n": 0}
    targets = iter([{"arg": "a"}, {"arg": "b"}, {"arg": "a"}, {"arg": "b"}])

    async def _decision(*args, **kwargs):
        try:
            params = {"tool_name": "fixture_tool", "params": next(targets)}
        except StopIteration:
            return DecisionType.COMPLETE_TASK, {"result": "done"}
        return DecisionType.EXECUTE_TOOL, params

    async def _tool(*args, **kwargs):
        executions["n"] += 1
        return True

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value
    assert executions["n"] == 4
    assert "action.spin_blocked" not in _event_types(db_session, task.id)
    assert "action.repeat_blocked" not in _event_types(db_session, task.id)
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_different_arguments_are_not_blocked(db_session, task_runner):
    """Only an identical (tool, params) call is grouped as a repeat."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executions = {"n": 0}
    targets = iter([{"arg": "a"}, {"arg": "b"}])

    async def _decision(*args, **kwargs):
        try:
            params = {"tool_name": "fixture_tool", "params": next(targets)}
        except StopIteration:
            return DecisionType.COMPLETE_TASK, {"result": "done"}
        return DecisionType.EXECUTE_TOOL, params

    async def _tool(*args, **kwargs):
        executions["n"] += 1
        return False

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value
    assert executions["n"] == 2
    assert "action.repeat_blocked" not in _event_types(db_session, task.id)
    await task_runner.release_lease()
