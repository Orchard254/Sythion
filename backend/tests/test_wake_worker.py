"""Regression tests for the bounded wake worker's fatal-error and startup keys.

D2: a fatal submit failure must be reported as a FAILED cycle (never as
successful work) and must still stop the bounded loop.
D3: the application.startup wake key must carry a boot-unique token so a fresh
process boot actually wakes once, while repeats within one boot still dedup.
"""

import asyncio
import logging

import pytest

from backend.services.bounded_wake_scheduler import (
    BoundedWakeScheduler,
    WakePolicy,
)
from backend.services.wake_worker import WakeWorker


class _Session:
    def close(self):
        pass


class _DB:
    """Minimal db_manager: sessions with no real I/O needed by the loop."""

    def new_session(self):
        return _Session()


# ---------------------------------------------------------------------------
# D2 - fatal submit failure is a failed cycle, not work, and stops the loop
# ---------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_fatal_submit_is_failed_cycle_and_stops_loop(monkeypatch, caplog):
    import backend.services.goal_selector as goal_selector

    class _Selector:
        def __init__(self, session):
            pass

        def pick_goal(self, *, respect_backoff=True):
            # Force attention to RUN_GOAL so wake_handler (and submit) is hit.
            return {"goal_id": "g-1", "priority": 0}

    monkeypatch.setattr(goal_selector, "RunnableGoalSelector", _Selector)

    worker = WakeWorker(_DB())
    worker._stop = asyncio.Event()

    submitted = []

    def _explode(trigger_type, *, trigger_reference=None, wake_key=None):
        submitted.append(wake_key)
        raise RuntimeError("submit exploded")

    monkeypatch.setattr(worker, "submit", _explode)

    policy = WakePolicy(enabled=True, interval_seconds=0.1, max_cycles=5)
    with caplog.at_level(logging.INFO, logger="backend.services.wake_worker"):
        await worker._run_bounded_loop(policy)

    # Only one cycle ran: the fatal stopped the window (no rearm, no extra
    # submits) even though the policy allowed five cycles.
    assert len(submitted) == 1
    assert worker.last_fatal_error() == "submit exploded"
    # The window log must report a failed cycle, never successful work.
    assert "Bounded wake window complete" in caplog.text
    assert "work=0 failed=1" in caplog.text


@pytest.mark.asyncio
async def test_scheduler_counts_reraised_handler_as_failed_not_work():
    """A re-raised handler exception is the mechanism the worker relies on."""
    scheduler = BoundedWakeScheduler()

    class _Selector:
        def pick_goal(self, *, respect_backoff=True):
            return {"goal_id": "g-1", "priority": 0}

    calls = []

    async def _handler(goal_id):
        calls.append(goal_id)
        raise RuntimeError("boom")

    report = await scheduler.run(
        _Selector(), _handler,
        WakePolicy(enabled=True, interval_seconds=0.1, max_cycles=3),
    )
    assert report.work_cycles == 0
    assert report.failed_cycles == len(calls)
    assert report.failed_cycles > 0
    assert report.idle_cycles == 0


# ---------------------------------------------------------------------------
# D3 - boot-unique startup wake key, durable dedup within a boot
# ---------------------------------------------------------------------------


def _capture_submit(sink):
    def _submit(trigger_type, *, trigger_reference=None, wake_key=None):
        sink.append((trigger_type, trigger_reference, wake_key))
        return wake_key
    return _submit


def test_startup_key_is_boot_unique_but_stable_within_worker():
    w1, w2 = WakeWorker(_DB()), WakeWorker(_DB())
    keys1, keys2 = [], []
    w1.submit = _capture_submit(keys1)
    w2.submit = _capture_submit(keys2)

    w1.submit_startup("agent-1")
    w1.submit_startup("agent-1")  # same boot: same key -> deduped
    w2.submit_startup("agent-1")  # fresh boot: different key

    assert len(keys1) == 2 and keys1[0] == keys1[1]
    assert len(keys2) == 1
    assert keys1[0] != keys2[0]
    assert keys1[0][2].startswith("application.startup:agent-1:")
    assert keys2[0][2].startswith("application.startup:agent-1:")
    assert keys1[0][0] == "application.startup" and keys1[0][1] == "agent-1"


def test_startup_wake_is_durable_and_deduped_per_boot():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.models.database import AgentWake, Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)

    class _DBManager:
        def new_session(self):
            return Session()

    db = _DBManager()

    w1 = WakeWorker(db)
    k1 = w1.submit_startup("agent-1")
    k2 = w1.submit_startup("agent-1")  # same boot: deduped
    assert k1 == k2

    s = Session()
    try:
        assert s.query(AgentWake).filter(AgentWake.wake_key == k1).count() == 1
    finally:
        s.close()

    # A fresh worker simulates a fresh process boot with the same persistent
    # agent_id: its startup wake must produce a new durable row, not be dropped.
    w2 = WakeWorker(db)
    k3 = w2.submit_startup("agent-1")
    assert k3 != k1

    s = Session()
    try:
        assert s.query(AgentWake).filter(AgentWake.wake_key == k3).count() == 1
        assert s.query(AgentWake).count() == 2
    finally:
        s.close()


# ---------------------------------------------------------------------------
# Maintenance recovery helpers: liveness view + durable re-queue.
# ---------------------------------------------------------------------------


def test_is_processing_is_a_read_only_liveness_view():
    worker = WakeWorker(_DB())
    assert worker.is_processing("goal.wake:x") is False
    worker._queued_keys.add("goal.wake:x")
    assert worker.is_processing("goal.wake:x") is True
    worker._queued_keys.discard("goal.wake:x")
    assert worker.is_processing("goal.wake:x") is False


@pytest.mark.asyncio
async def test_requeue_reclaimed_enqueues_once_and_bypasses_submit_dedup():
    worker = WakeWorker(_DB())

    def _explode(*args, **kwargs):
        raise AssertionError("requeue_reclaimed must not call submit (it dedups)")

    worker.submit = _explode
    worker.requeue_reclaimed("goal.wake:reclaimed")
    worker.requeue_reclaimed("goal.wake:reclaimed")  # idempotent

    assert worker.is_processing("goal.wake:reclaimed")
    assert worker._queue.qsize() == 1
    assert worker._queue.get_nowait() == {"wake_key": "goal.wake:reclaimed"}
