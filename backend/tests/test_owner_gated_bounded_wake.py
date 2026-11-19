"""Owner-gated bounded background goal wake: default OFF, bounded, non-authorizing.

Verifies the integration added around WakeWorker and Settings:
  * the time-driven loop is inert unless the owner explicitly enabled it;
  * when enabled it runs only a bounded number of cycles and always submits
    ordinary durable wakes (never invoking a model or granting authority);
  * invalid bounds are rejected by startup validation only when enabled.
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core import config
from backend.core.config import validate_startup_config
from backend.services.bounded_wake_scheduler import WakePolicy
from backend.services.wake_worker import WakeWorker


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


def test_bounded_wake_is_off_by_default():
    from backend.core.config import Settings
    defaults = Settings(_env_file=None)
    assert defaults.bounded_wake_enabled is False
    assert defaults.bounded_wake_interval_seconds == 30.0
    assert defaults.bounded_wake_max_cycles == 12


def test_disabled_policy_creates_no_loop_and_no_wakes(db_manager, monkeypatch):
    worker = WakeWorker(db_manager=db_manager)
    assert worker.bounded_loop_running() is False
    assert worker.start_bounded_loop(WakePolicy(enabled=False)) is False
    assert worker.bounded_loop_running() is False
    assert worker._bounded_task is None


def test_disabled_loop_never_submits_a_wake(db_manager, monkeypatch):
    # Even if a caller tried to build a goal, a disabled policy must not run.
    worker = WakeWorker(db_manager=db_manager)
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda *a, **k: submitted.append(a))
    assert worker.start_bounded_loop(WakePolicy(enabled=False)) is False
    assert submitted == []


@pytest.mark.asyncio
async def test_enabled_loop_runs_bounded_idle_cycles(db_manager, monkeypatch):
    # Idle selector: the loop cycles, finishes, and never submits a wake.
    import backend.services.goal_selector as goal_selector

    class IdleSelector:
        def __init__(self, session):
            pass

        def pick_goal(self, *, respect_backoff=True):
            return None

    monkeypatch.setattr(goal_selector, "RunnableGoalSelector", IdleSelector)
    worker = WakeWorker(db_manager=db_manager)
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda *a, **k: submitted.append(a))

    started = worker.start_bounded_loop(WakePolicy(
        enabled=True, interval_seconds=0.1, max_cycles=3))
    assert started is True
    assert worker.bounded_loop_running() is True

    task = worker._bounded_task
    await asyncio.wait_for(task, timeout=10)
    # The loop exits on its own after the hard cap; no wakes were submitted.
    assert worker.bounded_loop_running() is False
    assert submitted == []
    worker.stop_bounded_loop()


@pytest.mark.asyncio
async def test_enabled_loop_submits_ordinary_durable_wakes(db_manager, monkeypatch):
    # A runnable goal yields a normal durable wake through submit(); execution
    # of that wake still passes through the may_execute_background policy gate
    # in _process (tested separately), so the loop never executes work itself.
    import backend.services.goal_selector as goal_selector

    class RunnableSelector:
        def __init__(self, session):
            pass

        def pick_goal(self, *, respect_backoff=True):
            return {"goal_id": "goal-1", "priority": 1}

    monkeypatch.setattr(goal_selector, "RunnableGoalSelector", RunnableSelector)
    worker = WakeWorker(db_manager=db_manager)
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda *a, **k: submitted.append((a, k)))

    worker.start_bounded_loop(WakePolicy(
        enabled=True, interval_seconds=0.1, max_cycles=2))
    await asyncio.wait_for(worker._bounded_task, timeout=10)

    assert len(submitted) == 2
    for args, kwargs in submitted:
        assert args[0] == "goal.wake"
        assert kwargs["wake_key"] == "goal.wake:goal-1"
    worker.stop_bounded_loop()


@pytest.mark.asyncio
async def test_stop_ends_the_loop_early(db_manager, monkeypatch):
    import backend.services.goal_selector as goal_selector

    class RunnableSelector:
        def __init__(self, session):
            pass

        def pick_goal(self, *, respect_backoff=True):
            return {"goal_id": "goal-1", "priority": 1}

    monkeypatch.setattr(goal_selector, "RunnableGoalSelector", RunnableSelector)
    worker = WakeWorker(db_manager=db_manager)
    submitted = []
    monkeypatch.setattr(worker, "submit", lambda *a, **k: submitted.append(a))

    worker.start_bounded_loop(WakePolicy(
        enabled=True, interval_seconds=0.1, max_cycles=100))
    await asyncio.sleep(0.15)
    worker.stop_bounded_loop()
    assert worker.bounded_loop_running() is False
    assert len(submitted) < 100


def test_invalid_bounds_rejected_by_validation_when_enabled(monkeypatch):
    monkeypatch.setattr(config.settings, "bounded_wake_enabled", True)
    monkeypatch.setattr(config.settings, "ollama_api_key", "k")
    monkeypatch.setattr(config.settings, "selected_model", "m")
    monkeypatch.setattr(config.settings, "bounded_wake_interval_seconds", 0)
    monkeypatch.setattr(config.settings, "bounded_wake_max_cycles", 5000)
    issues = validate_startup_config()
    messages = " ".join(msg for _, msg in issues)
    assert "bounded_wake_interval_seconds" in messages
    assert "bounded_wake_max_cycles" in messages


def test_invalid_bounds_ignored_when_disabled(monkeypatch):
    monkeypatch.setattr(config.settings, "bounded_wake_enabled", False)
    monkeypatch.setattr(config.settings, "ollama_api_key", "k")
    monkeypatch.setattr(config.settings, "selected_model", "m")
    monkeypatch.setattr(config.settings, "bounded_wake_interval_seconds", 0)
    monkeypatch.setattr(config.settings, "bounded_wake_max_cycles", 5000)
    messages = " ".join(msg for _, msg in validate_startup_config())
    assert "bounded_wake_" not in messages


@pytest.mark.asyncio
async def test_loop_submitted_wake_is_still_blocked_by_owner_gate(db_manager, monkeypatch):
    """A goal.wake created by the loop cannot execute while background is off.

    The loop may submit a durable wake, but execution always re-checks the
    single MainAgent policy gate. With the default owner state (background
    disabled) the wake is resolved as an auditable SKIP, never run.
    """
    from backend.models.database import AgentWake
    from backend.services.main_agent import WAKE_SKIPPED, MainAgentService

    # The loop's time switch is ON, but the actual owner gate is OFF.
    monkeypatch.setattr(config.settings, "bounded_wake_enabled", True)

    worker = WakeWorker(db_manager=db_manager)
    key = "goal.wake:goal-1"
    worker.submit("goal.wake", trigger_reference="goal-1", wake_key=key)

    # Confirm it is durably PENDING before processing.
    s = db_manager.new_session()
    try:
        row = s.query(AgentWake).filter_by(wake_key=key).one()
        assert row.status == "PENDING"
    finally:
        s.close()

    # Process it: the owner gate (background disabled by default) must skip it.
    await worker._process({"wake_key": key, "trigger_type": "goal.wake"})

    s = db_manager.new_session()
    try:
        row = s.query(AgentWake).filter_by(wake_key=key).one()
        assert row.status == WAKE_SKIPPED
        assert row.stop_reason == "background_disabled"
    finally:
        s.close()

    # And enabling the owner gate flips the same wake policy to allowed.
    s = db_manager.new_session()
    try:
        MainAgentService(s).set_background_enabled(True)
        assert MainAgentService(s).may_execute_background() == (True, "ok")
    finally:
        s.close()
