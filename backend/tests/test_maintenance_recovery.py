"""Periodic autonomous maintenance/recovery sub-sweeps.

No real waits and no model/provider calls: all state is synthetic, sessions are
in-memory SQLite, time is injected explicitly, and the only profile access is
isolated under a temporary LOCALAPPDATA.
"""

import asyncio
from datetime import datetime, timedelta

import pytest

from backend.api.schemas import TaskState
from backend.models.database import (
    AgentWake,
    RunnerLease,
    Task,
    TaskEvent,
)
from backend.runtime import maintenance
from backend.runtime.events import decode_event_data
from backend.runtime.task_ownership import (
    ACTIVE,
    EXPIRED,
    TaskOwnershipRow,
)
from backend.services.main_agent import _scope

NOW = datetime(2026, 1, 1, 12, 0, 0)
STALE = NOW - timedelta(hours=2)
FRESH = NOW - timedelta(seconds=30)
RECLAIM_SECONDS = 600


@pytest.fixture(autouse=True)
def _isolated_localappdata(tmp_path, monkeypatch):
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path))
    yield


class _SharedSessionProxy:
    """Delegates DB work to the test session but ignores close()."""

    def __init__(self, db):
        self._db = db

    def close(self):
        pass

    def __getattr__(self, name):
        return getattr(self._db, name)


class _SharedSessionManager:
    def __init__(self, db):
        self._proxy = _SharedSessionProxy(db)

    def new_session(self):
        return self._proxy


class _FakeWorker:
    """Liveness + requeue surface used by the reclaim tests."""

    def __init__(self, processing=()):
        self.processing = set(processing)
        self.requeued = []

    def is_processing(self, key):
        return key in self.processing

    def requeue_reclaimed(self, key):
        self.requeued.append(key)


def _wake(db, key, *, status="RUNNING", claimed_at=STALE, attempts=0,
          agent_id="agent-1", trigger_type="goal.wake"):
    row = AgentWake(
        agent_id=agent_id,
        wake_key=key,
        trigger_type=trigger_type,
        coalesce_key=trigger_type,
        status=status,
        claimed_at=claimed_at,
        attempts=attempts,
        started_at=claimed_at or NOW,
    )
    db.add(row)
    db.commit()
    return row


def _task(db, task_id, state=TaskState.RUNNING, active_action_id=None, version=1):
    row = Task(
        id=task_id,
        original_request="Synthetic task",
        state=state.value if hasattr(state, "value") else state,
        active_action_id=active_action_id,
        version=version,
        created_at=NOW - timedelta(hours=3),
        updated_at=NOW - timedelta(hours=3),
    )
    db.add(row)
    db.commit()
    return row


def _ownership(db, task_id, *, status=ACTIVE, expires_at=FRESH):
    row = TaskOwnershipRow(
        task_id=task_id,
        owner_nonce="nonce-" + task_id,
        worker_id="worker-1",
        status=status,
        acquired_at=NOW - timedelta(minutes=1),
        heartbeat_at=NOW,
        expires_at=expires_at,
        fencing_token=1,
        version=1,
    )
    db.add(row)
    db.commit()
    return row


def _events(db, task_id, event_type=None):
    """Ordered ``(event_type, payload)`` pairs from the immutable ledger."""
    query = db.query(TaskEvent).filter(TaskEvent.task_id == task_id)
    if event_type is not None:
        query = query.filter(TaskEvent.event_type == event_type)
    return [
        (e.event_type, decode_event_data(e.event_data))
        for e in query.order_by(TaskEvent.sequence_num.asc()).all()
    ]


# --- wake reclaim ------------------------------------------------------------


def test_stale_running_wake_is_reclaimed_pending_and_requeued(test_db):
    key = "goal.wake:stale"
    _wake(test_db, key, attempts=2)
    worker = _FakeWorker()

    report = maintenance.reclaim_stale_wakes_once(
        test_db, worker, now=NOW, reclaim_seconds=RECLAIM_SECONDS,
    )

    assert report["reclaimed"] == 1
    assert report["failed"] == 0
    assert report["skipped_live"] == 0
    assert report["reclaimed_keys"] == [key]
    test_db.expire_all()
    row = test_db.query(AgentWake).filter_by(wake_key=key).one()
    assert row.status == "PENDING"  # MainAgentService.WAKE_PENDING
    assert _events(test_db, _scope("agent-1"), "agent.wake.reclaimed") == [
        ("agent.wake.reclaimed", {"wake_key": key, "attempts": 2}),
    ]


def test_stale_running_wake_live_in_worker_is_untouched(test_db):
    """Central double-execution guard: is_processing wins over staleness."""
    key = "goal.wake:live"
    _wake(test_db, key, attempts=1)
    worker = _FakeWorker(processing={key})

    report = maintenance.reclaim_stale_wakes_once(
        test_db, worker, now=NOW, reclaim_seconds=RECLAIM_SECONDS,
    )

    assert report["reclaimed"] == 0
    assert report["skipped_live"] == 1
    assert worker.requeued == []
    test_db.expire_all()
    assert test_db.query(AgentWake).filter_by(wake_key=key).one().status == "RUNNING"
    assert _events(test_db, _scope("agent-1")) == []


def test_fresh_running_wake_is_untouched(test_db):
    key = "goal.wake:fresh"
    _wake(test_db, key, claimed_at=FRESH)
    worker = _FakeWorker()

    report = maintenance.reclaim_stale_wakes_once(
        test_db, worker, now=NOW, reclaim_seconds=RECLAIM_SECONDS,
    )

    assert report["reclaimed"] == 0
    assert report["reclaimed_keys"] == []
    test_db.expire_all()
    assert test_db.query(AgentWake).filter_by(wake_key=key).one().status == "RUNNING"


def test_attempt_cap_marks_wake_failed_reclaim_limit(test_db):
    key = "goal.wake:capped"
    _wake(test_db, key, attempts=5)
    worker = _FakeWorker()

    report = maintenance.reclaim_stale_wakes_once(
        test_db, worker, now=NOW, reclaim_seconds=RECLAIM_SECONDS,
    )

    assert report["failed"] == 1
    assert report["reclaimed"] == 0
    assert report["reclaimed_keys"] == []
    assert worker.requeued == []
    test_db.expire_all()
    row = test_db.query(AgentWake).filter_by(wake_key=key).one()
    assert row.status == "FAILED"  # MainAgentService.WAKE_FAILED
    assert row.outcome == "reclaim_limit"
    assert _events(test_db, _scope("agent-1"), "agent.wake.failed") == [
        ("agent.wake.failed", {"wake_key": key, "attempts": 5}),
    ]


@pytest.mark.asyncio
async def test_second_sweep_is_idempotent(test_db):
    """The async sub-sweep re-queues exactly once; a second cycle is a no-op."""
    key = "goal.wake:once"
    _wake(test_db, key, attempts=1)
    worker = _FakeWorker()
    manager = _SharedSessionManager(test_db)
    kwargs = dict(now=NOW, reclaim_seconds=RECLAIM_SECONDS)

    first = await maintenance._wake_reclaim_sub_sweep(manager, worker, **kwargs)
    second = await maintenance._wake_reclaim_sub_sweep(manager, worker, **kwargs)

    assert first["reclaimed"] == 1
    assert second["reclaimed"] == 0
    assert second["failed"] == 0
    assert worker.requeued == [key]
    assert len(_events(test_db, _scope("agent-1"), "agent.wake.reclaimed")) == 1


def test_no_worker_fails_closed(test_db):
    key = "goal.wake:no-worker"
    _wake(test_db, key)
    report = maintenance.reclaim_stale_wakes_once(
        test_db, None, now=NOW, reclaim_seconds=RECLAIM_SECONDS,
    )
    assert report["skipped"] == "no_worker"
    assert report["reclaimed_keys"] == []
    test_db.expire_all()
    assert test_db.query(AgentWake).filter_by(wake_key=key).one().status == "RUNNING"


# --- task reclaim ------------------------------------------------------------


@pytest.mark.parametrize(
    "status,expires_at",
    [
        (EXPIRED, NOW + timedelta(hours=1)),  # explicit EXPIRED marker
        (ACTIVE, NOW - timedelta(seconds=1)),  # time-based expiry
    ],
)
def test_expired_ownership_marks_task_interrupted_with_action_uncertain(
    test_db, status, expires_at,
):
    _task(test_db, "t-lost", active_action_id="action-1", version=4)
    _ownership(test_db, "t-lost", status=status, expires_at=expires_at)

    report = maintenance.reclaim_interrupted_tasks_once(test_db, now=NOW)

    assert report["interrupted"] == ["t-lost"]
    assert report["count"] == 1
    test_db.expire_all()
    task = test_db.query(Task).filter_by(id="t-lost").one()
    assert task.state == TaskState.INTERRUPTED.value
    assert task.active_action_id is None
    assert task.version == 5
    types = [event_type for event_type, _ in _events(test_db, "t-lost")]
    assert types == ["action.uncertain", "task.interrupted"]


def test_fresh_ownership_never_interrupts_task(test_db):
    _task(test_db, "t-live", active_action_id="action-live")
    _ownership(test_db, "t-live", status=ACTIVE, expires_at=NOW + timedelta(minutes=5))

    report = maintenance.reclaim_interrupted_tasks_once(test_db, now=NOW)

    assert report["count"] == 0
    test_db.expire_all()
    task = test_db.query(Task).filter_by(id="t-live").one()
    assert task.state == TaskState.RUNNING.value
    assert task.active_action_id == "action-live"
    assert _events(test_db, "t-live") == []


def test_expired_runner_lease_with_fresh_ownership_does_not_interrupt(test_db):
    """Ownership, not runner_lease, is the liveness authority (heartbeat renews
    task ownership; runner_lease alone is a stale signal)."""
    _task(test_db, "t-lease-only")
    _ownership(test_db, "t-lease-only", status=ACTIVE,
               expires_at=NOW + timedelta(minutes=5))
    test_db.add(RunnerLease(
        id=1,
        owner_nonce="lease-nonce",
        process_id=123456,
        heartbeat=NOW - timedelta(hours=2),
        expires_at=NOW - timedelta(hours=1),
        last_task_id="t-lease-only",
    ))
    test_db.commit()

    report = maintenance.reclaim_interrupted_tasks_once(test_db, now=NOW)

    assert report["count"] == 0
    test_db.expire_all()
    assert (test_db.query(Task).filter_by(id="t-lease-only").one().state
            == TaskState.RUNNING.value)


def test_task_without_ownership_row_is_left_untouched(test_db):
    _task(test_db, "t-unknown")
    report = maintenance.reclaim_interrupted_tasks_once(test_db, now=NOW)
    assert report["count"] == 0
    test_db.expire_all()
    assert (test_db.query(Task).filter_by(id="t-unknown").one().state
            == TaskState.RUNNING.value)


# --- cycle / loop ------------------------------------------------------------


@pytest.mark.asyncio
async def test_one_cycle_calls_all_sub_sweeps(test_db, monkeypatch):
    calls = []

    async def _pending(db):
        calls.append("pending")
        return True

    async def _wakes(db, worker, **kwargs):
        calls.append("wakes")
        return {"reclaimed_keys": []}

    async def _tasks(db, **kwargs):
        calls.append("tasks")
        return {"count": 0}

    monkeypatch.setattr(maintenance, "_pending_activation_sub_sweep", _pending)
    monkeypatch.setattr(maintenance, "_wake_reclaim_sub_sweep", _wakes)
    monkeypatch.setattr(maintenance, "_task_reclaim_sub_sweep", _tasks)

    report = await maintenance.maintenance_cycle(
        _SharedSessionManager(test_db), _FakeWorker(), now=NOW,
    )

    assert calls == ["pending", "wakes", "tasks"]
    assert report == {"pending_activation": True,
                      "wake_reclaim": {"reclaimed_keys": []},
                      "task_reclaim": {"count": 0}}


@pytest.mark.asyncio
async def test_one_failing_sub_sweep_does_not_stop_the_others(test_db, monkeypatch):
    calls = []

    async def _pending(db):
        calls.append("pending")
        raise RuntimeError("profile unavailable")

    async def _wakes(db, worker, **kwargs):
        calls.append("wakes")
        return {"reclaimed": 0}

    async def _tasks(db, **kwargs):
        calls.append("tasks")
        return {"count": 0}

    monkeypatch.setattr(maintenance, "_pending_activation_sub_sweep", _pending)
    monkeypatch.setattr(maintenance, "_wake_reclaim_sub_sweep", _wakes)
    monkeypatch.setattr(maintenance, "_task_reclaim_sub_sweep", _tasks)

    report = await maintenance.maintenance_cycle(
        _SharedSessionManager(test_db), _FakeWorker(), now=NOW,
    )

    assert calls == ["pending", "wakes", "tasks"]
    assert report["pending_activation"] is None
    assert report["wake_reclaim"] == {"reclaimed": 0}
    assert report["task_reclaim"] == {"count": 0}


@pytest.mark.asyncio
async def test_cancelled_error_propagates_out_of_cycle(test_db, monkeypatch):
    calls = []

    async def _pending(db):
        raise asyncio.CancelledError()

    async def _wakes(db, worker, **kwargs):
        calls.append("wakes")

    async def _tasks(db, **kwargs):
        calls.append("tasks")

    monkeypatch.setattr(maintenance, "_pending_activation_sub_sweep", _pending)
    monkeypatch.setattr(maintenance, "_wake_reclaim_sub_sweep", _wakes)
    monkeypatch.setattr(maintenance, "_task_reclaim_sub_sweep", _tasks)

    with pytest.raises(asyncio.CancelledError):
        await maintenance.maintenance_cycle(
            _SharedSessionManager(test_db), _FakeWorker(), now=NOW,
        )
    assert calls == []  # cancellation must abort the cycle, not continue it


@pytest.mark.asyncio
async def test_sweeper_loop_propagates_cancellation(monkeypatch):
    started = asyncio.Event()

    async def _cycle(*args, **kwargs):
        started.set()
        await asyncio.sleep(30)  # never actually waits: cancelled immediately

    monkeypatch.setattr(maintenance, "maintenance_cycle", _cycle)
    loop = asyncio.create_task(
        maintenance.maintenance_sweeper(object(), None, interval_seconds=0.01),
        name="test-maintenance-sweep",
    )
    try:
        await asyncio.wait_for(started.wait(), timeout=1)
    finally:
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop
