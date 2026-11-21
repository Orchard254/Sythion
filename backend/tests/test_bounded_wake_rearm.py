"""Continuous bounded wake: fresh windows are re-armed, never unbounded.

Verifies that a normal budget exhaustion re-arms a NEW BoundedWakeScheduler
(fresh cycle window) while both real owner switches remain on, that re-arming
stops when an owner switch is turned off, that fatal errors are surfaced via
``last_fatal_error()``, and that CancelledError propagates without a rearm.
"""

import asyncio
import logging

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.services.bounded_wake_scheduler import WakePolicy
from backend.services.wake_worker import WakeWorker

_LOG = "backend.services.wake_worker"
_REARM = "Re-arming bounded wake window"


class _DbManager:
    """Minimal real-DB manager backed by in-memory SQLite."""

    def __init__(self):
        self._engine = create_engine(
            "sqlite://",
            connect_args={"check_same_thread": False},
            poolclass=StaticPool,
        )
        from backend.models.database import Base
        Base.metadata.create_all(self._engine)
        self._factory = sessionmaker(bind=self._engine)

    def new_session(self):
        return self._factory()


@pytest.fixture
def db_manager():
    return _DbManager()


class _RunnableSelector:
    def __init__(self, session):
        pass

    def pick_goal(self, *, respect_backoff=True):
        return {"goal_id": "goal-1", "priority": 1}


def _set_owner(db_manager, enabled):
    from backend.services.main_agent import MainAgentService

    session = db_manager.new_session()
    try:
        svc = MainAgentService(session)
        svc.set_background_enabled(enabled)
        svc.set_bounded_wake_enabled(enabled)
    finally:
        session.close()


def _rearm_logs(caplog):
    return [r for r in caplog.records if _REARM in r.getMessage()]


async def _wait_until(predicate, timeout=5.0):
    """Poll a cheap predicate without long sleeps."""
    loop = asyncio.get_running_loop()
    deadline = loop.time() + timeout
    while not predicate():
        if loop.time() >= deadline:
            raise AssertionError("timed out waiting for condition")
        await asyncio.sleep(0.01)


@pytest.fixture
def runnable_selector(monkeypatch):
    import backend.services.goal_selector as goal_selector

    monkeypatch.setattr(goal_selector, "RunnableGoalSelector", _RunnableSelector)


@pytest.mark.asyncio
async def test_bounded_loop_rearms_and_runs_many_cycles(
    db_manager, monkeypatch, caplog, runnable_selector
):
    _set_owner(db_manager, True)
    worker = WakeWorker(db_manager=db_manager)
    keys = []
    monkeypatch.setattr(
        worker, "submit", lambda *a, **k: keys.append(k["wake_key"])
    )

    with caplog.at_level(logging.INFO, logger=_LOG):
        assert worker.start_bounded_loop(WakePolicy(
            enabled=True, interval_seconds=0.1, max_cycles=2)) is True
        # Eight cycles == four fresh 2-cycle windows (and >=3 re-arms).
        await _wait_until(lambda: len(keys) >= 8)
        worker.stop_bounded_loop()

    assert len(keys) >= 8
    # Every key is still unique and bounded-wake scoped.
    assert len(set(keys)) == len(keys)
    assert all(k.startswith("goal.wake:goal-1:") for k in keys)
    assert len(_rearm_logs(caplog)) >= 1
    assert worker.bounded_loop_running() is False
    assert worker.last_fatal_error() is None


@pytest.mark.asyncio
async def test_disabling_owner_switches_stops_rearm(
    db_manager, monkeypatch, caplog, runnable_selector
):
    _set_owner(db_manager, True)
    worker = WakeWorker(db_manager=db_manager)
    keys = []

    def submit_then_disable(*a, **k):
        keys.append(k["wake_key"])
        if len(keys) == 1:
            _set_owner(db_manager, False)
        return "key"

    monkeypatch.setattr(worker, "submit", submit_then_disable)

    with caplog.at_level(logging.INFO, logger=_LOG):
        worker.start_bounded_loop(WakePolicy(
            enabled=True, interval_seconds=0.1, max_cycles=2))
        await asyncio.wait_for(worker._bounded_task, timeout=5)

    # First window finishes its bound; the rearm check sees switches off.
    assert len(keys) == 2
    assert _rearm_logs(caplog) == []
    assert worker.bounded_loop_running() is False


@pytest.mark.asyncio
async def test_fatal_error_sets_getter_and_stops_rearm(
    db_manager, monkeypatch, caplog, runnable_selector
):
    _set_owner(db_manager, True)
    worker = WakeWorker(db_manager=db_manager)

    def failing_submit(*a, **k):
        raise RuntimeError("boom-fatal")

    monkeypatch.setattr(worker, "submit", failing_submit)

    with caplog.at_level(logging.INFO, logger=_LOG):
        worker.start_bounded_loop(WakePolicy(
            enabled=True, interval_seconds=0.1, max_cycles=2))
        await asyncio.wait_for(worker._bounded_task, timeout=5)

    err = worker.last_fatal_error()
    assert err is not None and "boom-fatal" in err
    assert _rearm_logs(caplog) == []
    assert worker.bounded_loop_running() is False


@pytest.mark.asyncio
async def test_cancelled_error_propagates_without_rearm(
    db_manager, monkeypatch, caplog, runnable_selector
):
    _set_owner(db_manager, True)

    import backend.services.wake_worker as ww

    calls = {"n": 0}

    class CancellingScheduler:
        def stop(self):
            pass

        async def run(self, selector, wake_handler, policy):
            calls["n"] += 1
            raise asyncio.CancelledError()

    monkeypatch.setattr(ww, "BoundedWakeScheduler", CancellingScheduler)
    worker = WakeWorker(db_manager=db_manager)

    with caplog.at_level(logging.INFO, logger=_LOG):
        assert worker.start_bounded_loop(WakePolicy(
            enabled=True, interval_seconds=0.1, max_cycles=2)) is True
        with pytest.raises(asyncio.CancelledError):
            await asyncio.wait_for(worker._bounded_task, timeout=5)

    assert calls["n"] == 1
    assert _rearm_logs(caplog) == []
    assert worker.last_fatal_error() is None
