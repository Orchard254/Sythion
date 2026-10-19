"""
Acceptance gate: Event stream (Phase B).

Replay from `after_sequence` reconstructs exactly the committed timeline.
Slow/disconnected clients can poll from the last sequence they saw and never
miss or duplicate an event; the ledger is append-only and sequence-strict.
"""

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.models.database import TaskEvent


@pytest.mark.asyncio
async def test_events_are_append_only_with_strict_sequence(db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Ledger task", idempotency_key="e-1")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.started", {})
    await task_runner.cancel_task(task.id)
    await task_runner.release_lease()

    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()

    assert [e.event_type for e in events] == ["task.created", "task.started", "task.cancelled"]
    assert [e.sequence_num for e in events] == [1, 2, 3]


@pytest.mark.asyncio
async def test_replay_from_after_sequence_is_exact(client, db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Replay task", idempotency_key="e-2")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.started", {})
    await task_runner.cancel_task(task.id)
    await task_runner.release_lease()

    headers = {"X-Control-Token": "acceptance-control-token"}

    full = client.get(f"/api/v1/tasks/{task.id}/events", headers=headers).json()["events"]
    assert len(full) == 3

    # Replaying from after_sequence=1 returns exactly the committed tail.
    tail = client.get(
        f"/api/v1/tasks/{task.id}/events?after_sequence=1", headers=headers
    ).json()["events"]
    assert [e["sequence_num"] for e in tail] == [2, 3]
    assert tail == full[1:]

    # Replaying from the last sequence returns nothing new (idempotent catch-up).
    nothing = client.get(
        f"/api/v1/tasks/{task.id}/events?after_sequence=3", headers=headers
    ).json()["events"]
    assert nothing == []


@pytest.mark.asyncio
async def test_disconnected_client_catches_up_without_duplicates(client, db_session, task_runner):
    await task_runner.acquire_lease()
    task = await task_runner.create_task("Catch-up task", idempotency_key="e-3")
    await task_runner.release_lease()

    headers = {"X-Control-Token": "acceptance-control-token"}

    # Client saw only the creation event (seq 1), then "disconnects".
    first = client.get(f"/api/v1/tasks/{task.id}/events", headers=headers).json()["events"]
    assert [e["sequence_num"] for e in first] == [1]

    # More events happen while the client is away.
    runner = TaskRunner(db_session)
    await runner.acquire_lease()
    await runner.transition_state(task.id, TaskState.RUNNING, "task.started", {})
    await runner.cancel_task(task.id)
    await runner.release_lease()

    # On reconnect, replay from the last seen sequence - exactly the new events.
    delta = client.get(
        f"/api/v1/tasks/{task.id}/events?after_sequence=1", headers=headers
    ).json()["events"]
    assert [e["sequence_num"] for e in delta] == [2, 3]
    assert [e["event_type"] for e in delta] == ["task.started", "task.cancelled"]
