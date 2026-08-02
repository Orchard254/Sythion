"""
Acceptance gate: per-task budgets (Phase B).

Every task tracks its Cloud request count, input/output tokens, executed
actions, repair attempts, and elapsed time. Exceeding any per-task budget
fails the task honestly -- never retries, never mutates, never falls back
locally -- and the loop records an evidence-safe budget snapshot event.
"""

import json

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionError, DecisionType
from backend.core.budgets import TaskBudget, BudgetExceededError
from backend.models.database import TaskEvent


def _event_codes(db_session, task_id):
    events = db_session.query(TaskEvent).filter_by(task_id=task_id).all()
    codes = []
    for e in events:
        if e.event_type == "task.failed":
            codes.append(json.loads(e.event_data).get("code"))
    return codes


def _budget_snapshot(db_session, task_id):
    events = db_session.query(TaskEvent).filter_by(task_id=task_id).all()
    for e in reversed(events):
        if e.event_type == "task.budget":
            return json.loads(e.event_data)
    return None


# --- Unit: budget accounting -------------------------------------------------


def test_budget_tracks_all_counters():
    b = TaskBudget(max_actions=3, max_cloud_requests=2, max_input_tokens=100,
                   max_output_tokens=50, max_elapsed_seconds=3600, max_repairs=2)
    b.record_cloud_request(input_tokens=40, output_tokens=10)
    b.record_cloud_request(input_tokens=20, output_tokens=5)
    b.record_action()
    b.record_repair()

    snap = b.snapshot()
    assert snap["cloud_requests"] == 2
    assert snap["input_tokens"] == 60
    assert snap["output_tokens"] == 15
    assert snap["actions"] == 1
    assert snap["repairs"] == 1
    assert "limits" in snap


@pytest.mark.parametrize("limit,setter,expected_code", [
    ({"max_actions": 0}, lambda b: b.record_action(), "ACTION_LIMIT_EXCEEDED"),
    ({"max_cloud_requests": 0}, lambda b: b.record_cloud_request(), "CLOUD_REQUEST_LIMIT_EXCEEDED"),
    ({"max_input_tokens": 10}, lambda b: b.record_cloud_request(input_tokens=50), "INPUT_TOKEN_LIMIT_EXCEEDED"),
    ({"max_output_tokens": 10}, lambda b: b.record_cloud_request(output_tokens=50), "OUTPUT_TOKEN_LIMIT_EXCEEDED"),
])
def test_budget_raises_specific_code(limit, setter, expected_code):
    b = TaskBudget(**limit)
    with pytest.raises(BudgetExceededError) as exc_info:
        setter(b)
    assert exc_info.value.code == expected_code


def test_budget_elapsed_check():
    b = TaskBudget(max_elapsed_seconds=1)
    import time
    b._started_at = time.monotonic() - 10  # Pretend the task started 10s ago.
    with pytest.raises(BudgetExceededError) as exc_info:
        b.check_elapsed()
    assert exc_info.value.code == "ELAPSED_TIME_EXCEEDED"


# --- Loop: budget exhaustion is terminal and honest --------------------------


@pytest.mark.asyncio
async def test_cloud_request_budget_fails_honestly(db_session, task_runner):
    """Cloud request budget exhaustion fails the task with the matching code."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Heavy task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    executed = {"ran": False}

    async def _decision(*args, **kwargs):
        budget = kwargs.get("budget")
        assert budget is not None
        budget.record_cloud_request(input_tokens=100, output_tokens=10)
        raise BudgetExceededError("CLOUD_REQUEST_LIMIT_EXCEEDED", "request budget exceeded")

    async def _tool(*args, **kwargs):
        executed["ran"] = True
        return None

    loop.model_engine.make_decision = _decision
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert "CLOUD_REQUEST_LIMIT_EXCEEDED" in _event_codes(db_session, task.id)
    assert executed["ran"] is False
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_elapsed_budget_fails_honestly(db_session, task_runner):
    """Elapsed-time budget exhaustion fails the task without further decisions."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Slow task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    decision_calls = {"n": 0}

    async def _decision(*args, **kwargs):
        decision_calls["n"] += 1
        return DecisionType.EXECUTE_TOOL, {"tool_name": "system_time", "params": {}}

    async def _tool(*args, **kwargs):
        return None

    loop.model_engine.make_decision = _decision
    loop._execute_decision = _tool
    loop.MAX_ACTIONS_PER_TASK = 1000  # Never trip the action budget here.

    with pytest.MonkeyPatch.context() as mp:
        import backend.services.task_execution_loop as loop_module
        mp.setattr(loop_module, "TaskBudget", _ExpiredBudget)

        await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.FAILED.value
    assert "ELAPSED_TIME_EXCEEDED" in _event_codes(db_session, task.id)
    await task_runner.release_lease()


class _ExpiredBudget(TaskBudget):
    def __init__(self):
        super().__init__(max_actions=1000)

    def check_elapsed(self):
        raise BudgetExceededError("ELAPSED_TIME_EXCEEDED", "elapsed budget exceeded")


# --- Loop: budget snapshot is recorded as durable evidence -------------------


@pytest.mark.asyncio
async def test_budget_snapshot_recorded_on_terminal_state(db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Snapshot task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)

    async def _decision(*args, **kwargs):
        budget = kwargs.get("budget")
        if budget is not None:
            budget.record_cloud_request(input_tokens=30, output_tokens=5)
        return DecisionType.COMPLETE_TASK, {"summary": "done"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value

    snap = _budget_snapshot(db_session, task.id)
    assert snap is not None
    assert snap["cloud_requests"] == 1
    assert snap["input_tokens"] == 30
    assert snap["output_tokens"] == 5
    # The snapshot is evidence-safe: no secret or raw content.
    assert "api_key" not in json.dumps(snap).lower()
    await task_runner.release_lease()
