"""
Acceptance gate: Intent routing (Phase B).

A clarification reply must continue the SAME task (same task ID) and be
recorded durably. A correction requires cancelling the active task first and
starting a replacement - the system never silently forks or re-routes an
active task.
"""

import pytest
from fastapi import HTTPException

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionEngine, DecisionType
from backend.tools.executor import ToolExecutor
from backend.models.database import TaskEvent


def _events_for(db_session, task_id):
    return db_session.query(TaskEvent).filter_by(task_id=task_id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()


async def _drive_to_waiting(db_session, task_runner, tool_executor, task_id):
    """Run the loop with a stubbed model that asks a clarifying question."""
    loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
    async def _ask(*args, **kwargs):
        return DecisionType.REQUEST_USER_INPUT, {"question": "Which file?"}
    loop.model_engine.make_decision = _ask
    await loop.execute_task(task_id)


@pytest.mark.asyncio
async def test_reply_continues_same_task_and_records_event(db_session, task_runner, tool_executor):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Create a report about Q3 sales")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    await _drive_to_waiting(db_session, task_runner, tool_executor, task.id)
    assert (await task_runner.get_task(task.id)).state == TaskState.WAITING_FOR_USER.value

    # Simulate the reply endpoint's durable transition on the SAME task.
    updated = await task_runner.transition_state(
        task_id=task.id,
        new_state=TaskState.RUNNING,
        event_type="user.replied",
        event_data={"answer": "use the quarterly file", "replied_at": "2026-01-01T00:00:00"},
        pending_question=None,
    )

    assert updated.id == task.id
    assert updated.state == TaskState.RUNNING.value
    assert updated.pending_question is None

    events = _events_for(db_session, task.id)
    assert events[-1].event_type == "user.replied"
    assert "quarterly file" in events[-1].event_data
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_reply_rejected_for_non_waiting_task(client, db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Just get the time")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})
    await task_runner.release_lease()

    # The HTTP layer must reject a reply to a task that is not waiting_for_user.
    response = client.post(
        f"/api/v1/tasks/{task.id}/reply",
        json={"answer": "nope"},
        headers={"X-Control-Token": "acceptance-control-token"},
    )
    assert response.status_code == 400


@pytest.mark.asyncio
async def test_correction_requires_cancel_then_replacement(db_session, task_runner):
    await task_runner.acquire_lease()
    original = await task_runner.create_task("Schedule meeting at 10", idempotency_key="m-1")
    await task_runner.transition_state(original.id, TaskState.RUNNING, "task.execution_started", {})

    # A second request while the first is active cannot silently become work.
    duplicate = await task_runner.create_task("Schedule meeting at 10", idempotency_key="m-1")
    assert duplicate.id == original.id

    # The user must cancel, then submit the correction as a replacement.
    cancelled = await task_runner.cancel_task(original.id)
    assert cancelled.state == TaskState.CANCELLED.value

    replacement = await task_runner.create_task("Schedule meeting at 11 instead", idempotency_key="m-2")
    assert replacement.id != original.id
    assert replacement.state == TaskState.CREATED.value

    events = _events_for(db_session, original.id)
    assert [e.event_type for e in events] == ["task.created", "task.execution_started", "task.cancelled"]
    await task_runner.release_lease()


@pytest.mark.asyncio
async def test_task_ownership_second_runner_cannot_create(db_session, task_runner):
    """A second request while one task runs must not queue or duplicate work."""
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Busy task")

    second = TaskRunner(db_session)
    with pytest.raises(RuntimeError, match="lease not held"):
        await second.create_task("Another task")

    await task_runner.release_lease()


def test_idempotent_replay_of_terminal_task_is_not_rerun(client, db_session, task_runner):
    """Replaying an idempotency key for an already-completed task must not 500 or re-execute."""
    import asyncio
    asyncio.run(task_runner.acquire_lease())
    task = asyncio.run(task_runner.create_task("done task", idempotency_key="done-1"))
    asyncio.run(task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {}))
    asyncio.run(task_runner.transition_state(
        task.id, TaskState.COMPLETED, "task.completed", {"summary": "done"}, final_result="done"
    ))
    asyncio.run(task_runner.release_lease())

    response = client.post(
        "/api/v1/tasks",
        json={"original_request": "done task", "idempotency_key": "done-1"},
        headers={"X-Control-Token": "acceptance-control-token"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["id"] == task.id
    assert body["state"] == "completed"
    assert body["final_result"] == "done"
    # The replay did not append a second execution_started event or re-run the task.
    events = _events_for(db_session, task.id)
    assert [e.event_type for e in events] == ["task.created", "task.execution_started", "task.completed"]

