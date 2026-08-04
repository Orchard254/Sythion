"""
Acceptance gate: Model failure (Phase B).

Invalid, parallel, malformed, unavailable, rate-limited, stale-model, and
exhausted-budget decisions cause no repeated mutation and no local fallback:
the task fails cleanly after bounded repair attempts.
"""

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionError, DecisionType
from backend.models.database import TaskEvent


def _failed_code(db_session, task_id):
    events = db_session.query(TaskEvent).filter_by(task_id=task_id).all()
    for e in reversed(events):
        if e.event_type == "task.failed":
            import json
            return json.loads(e.event_data).get("code")
    return None


@pytest.mark.asyncio
async def test_invalid_decision_fails_task_without_mutation(db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do the thing")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _bad_decision(*args, **kwargs):
        raise ModelDecisionError("MISSING_CONTROL_CALL", "model returned text without a tool call")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _bad_decision
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "MISSING_CONTROL_CALL"
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_parallel_calls_rejected_no_mutation(db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do two things")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _parallel_decision(*args, **kwargs):
        raise ModelDecisionError("PARALLEL_CALLS", "model made multiple parallel calls")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _parallel_decision
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "PARALLEL_CALLS"
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_rate_limited_no_local_fallback(db_session, task_runner):
    """Rate-limited / unavailable provider decisions never trigger a local fallback."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Fetch data")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _rate_limited(*args, **kwargs):
        raise ModelDecisionError("RATE_LIMITED", "Rate limit exceeded")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _rate_limited
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_rate_limited_fails_on_first_attempt_no_repair(db_session, task_runner):
    """A rate-limited provider fails the task immediately, never amplifying
    the outage with repeated repair calls."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Fetch data")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    decision_calls = {"n": 0}

    async def _rate_limited(*args, **kwargs):
        decision_calls["n"] += 1
        raise ModelDecisionError("CLOUD_ERROR", "Ollama Cloud request failed: Rate limit exceeded")

    loop.model_engine.make_decision = _rate_limited

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "CLOUD_ERROR"
    # One call only - no repair retries against a rate-limited provider.
    assert decision_calls["n"] == 1
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_budget_exhaustion_fails_without_further_actions(db_session, task_runner):
    """Exceeding the per-task action budget stops the loop."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Endless task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    loop.MAX_ACTIONS_PER_TASK = 3

    decision_calls = {"n": 0}

    async def _always_tool(*args, **kwargs):
        decision_calls["n"] += 1
        return DecisionType.EXECUTE_TOOL, {"tool_name": "system_time", "params": {"n": decision_calls["n"]}}

    async def _tool(*args, **kwargs):
        return None

    loop.model_engine.make_decision = _always_tool
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "ACTION_LIMIT_EXCEEDED"
    # The loop stopped making decisions after hitting the cap.
    assert decision_calls["n"] <= 3
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_cloud_outage_fails_honestly(db_session, task_runner):
    """A persistent Cloud outage fails the task after bounded repairs, no mutation."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Fetch something")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _cloud_out(*args, **kwargs):
        raise ModelDecisionError("CLOUD_ERROR", "Ollama Cloud request failed: connection refused")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _cloud_out
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "CLOUD_ERROR"
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_retired_model_fails_honestly(db_session, task_runner):
    """A retired/unavailable model fails the task, never a local fallback."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Use the model")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _retired(*args, **kwargs):
        raise ModelDecisionError("MODEL_UNAVAILABLE", "Selected model is unavailable")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _retired
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "MODEL_UNAVAILABLE"
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_malformed_decision_fails_honestly(db_session, task_runner):
    """A persistently malformed decision fails the task after bounded repairs."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Malformed task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _malformed(*args, **kwargs):
        raise ModelDecisionError("INVALID_JSON", "Failed to parse arguments")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _malformed
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "INVALID_JSON"
    assert executed["ran"] is False
    await task_runner.release_lease()

