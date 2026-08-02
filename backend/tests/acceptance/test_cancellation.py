"""
Acceptance gate: Cancellation (Phase B).

Cancellation is durable (survives restart via the immutable event stream),
checked before OS calls, and prevents the next action from executing.
"""

import pytest
from datetime import datetime, timedelta

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.models.database import TaskEvent, RunnerLease


@pytest.mark.asyncio
async def test_cancel_is_durable_and_terminal(db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Do something", idempotency_key="c-1")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    cancelled = await task_runner.cancel_task(task.id)

    assert cancelled.state == TaskState.CANCELLED.value
    assert cancelled.terminal_reason == "cancelled_by_user"

    # A cancelled task is terminal - it cannot return to running.
    with pytest.raises(RuntimeError, match="Invalid task state transition"):
        await task_runner.transition_state(task.id, TaskState.RUNNING, "task.restarted", {})

    # The cancellation is recorded durably in the immutable event stream.
    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    assert events[-1].event_type == "task.cancelled"
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_cancel_prevents_next_action_after_model_call(db_session, task_runner):
    """A cancel arriving during the model call must stop execution BEFORE an OS action."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Delete the temp files")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)

    calls = {"executed": False}

    async def _decision(*args, **kwargs):
        # Simulate the cancel arriving while the model is "thinking".
        await task_runner.cancel_task(task.id)
        from backend.services.model_engine import DecisionType
        return DecisionType.EXECUTE_TOOL, {"tool_name": "file_delete", "params": {"path": "C:\\tmp"}}

    loop.model_engine.make_decision = _decision

    async def _tool(*args, **kwargs):
        calls["executed"] = True
        return None

    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    assert calls["executed"] is False
    assert (await task_runner.get_task(task.id)).state == TaskState.CANCELLED.value
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_stale_lease_cancel_survives_restart(db_session):
    """A cancelled task stays cancelled even if a new runner takes over."""
    owner = TaskRunner(db_session)
    await owner.acquire_lease()
    task = await owner.create_task("Cancelled task", idempotency_key="c-2")
    await owner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})
    await owner.cancel_task(task.id)

    # Simulate a crash: lease expires, a replacement runner takes over.
    lease = db_session.query(RunnerLease).filter_by(id=1).first()
    lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
    db_session.commit()

    replacement = TaskRunner(db_session)
    assert await replacement.acquire_lease() is True
    recovered = await replacement.get_task(task.id)

    assert recovered.state == TaskState.CANCELLED.value
    await replacement.release_lease()
