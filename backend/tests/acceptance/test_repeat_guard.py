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
from backend.services.model_engine import DecisionType, ModelDecisionError
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


def test_spin_recovery_context_carries_verified_result_and_completion_directive():
    """The deterministic termination directive feeds the already-obtained tool
    result back to the model, tells it to stop re-querying, and to advance with
    the answer tool then complete_task -- without fabricating a completion."""
    params = {"tool_name": "system_file_roots", "params": {}}
    events = [
        {
            "event_type": "tool.executed",
            "event_data": {
                "tool_name": "system_file_roots",
                "success": True,
                "model_summary": "Approved file roots: C:\\Sythion\\sythion",
                "output": {"approved_roots": ["C:\\Sythion\\sythion"]},
            },
        }
    ]
    directive = TaskExecutionLoop._build_spin_recovery_context(params, events)

    assert "system_file_roots" in directive
    assert "Do NOT call `system_file_roots` again" in directive
    assert "C:\\Sythion\\sythion" in directive          # verified result carried
    assert "complete_task" in directive                  # explicit completion
    assert "file_read" in directive                      # advance to answer tool
    assert "Never repeat an identical read-only query" in directive


def test_spin_recovery_context_is_safe_without_a_prior_result():
    """No verified result => no fabricated answer; only the directive is sent."""
    directive = TaskExecutionLoop._build_spin_recovery_context(
        {"tool_name": "system_time", "params": {}}, []
    )
    assert "system_time" in directive
    assert "complete_task" in directive
    assert "You already obtained" not in directive


@pytest.mark.asyncio
async def test_model_failure_completes_from_verified_read_evidence(db_session, task_runner):
    """When the model fails to terminate after a successful read, the task
    completes from the recorded tool output (never a fabricated answer)."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Read README.md and report its first line")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    state = {"tool_done": False}

    async def _decision(*args, **kwargs):
        if not state["tool_done"]:
            return DecisionType.EXECUTE_TOOL, {
                "tool_name": "file_read",
                "params": {"path": "README.md"},
            }
        # Model fails to issue complete_task.
        raise ModelDecisionError(code="CLOUD_ERROR", message="no tool call")

    async def _tool(*args, **kwargs):
        state["tool_done"] = True
        # Emulate a successful read whose output is recorded by the runner.
        event_data = {
            "tool_name": "file_read",
            "params": {"path": "README.md"},
            "success": True,
            "mutating": False,
            "model_summary": "README first line",
            "output": {"content": "# Sythion heading\nrest of file"},
        }
        await task_runner.record_event(task.id, "tool.executed", event_data)
        return True

    loop.model_engine.make_decision = _decision
    loop._execute_tool = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value
    assert "# Sythion heading" in (task.final_result or "")
    assert "verified `file_read` tool output" in (task.final_result or "")
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_model_failure_does_not_fabricate_completion_without_read_evidence(db_session, task_runner):
    """No successful read result => the task still fails honestly."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Read README.md and report its first line")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)

    async def _decision(*args, **kwargs):
        raise ModelDecisionError(code="CLOUD_ERROR", message="no tool call")

    loop.model_engine.make_decision = _decision
    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
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
