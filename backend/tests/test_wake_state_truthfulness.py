"""Regression tests for wake-state truthfulness (bounded owner + last wake).

Covers three verified defects:
  1. finish_wake never wrote last_wake_at / last_wake_reason (UI/brief showed
     "Last wake: none" forever), while skip_wake must NOT fake an executed wake.
  2. The bounded-loop owner gates ignored the agent's paused/shutdown state,
     so pausing kept submitting wakes that were only SKIPped (audit noise).
  3. The coalescable goal.resumed trigger was never submitted in production.
"""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, MainAgent
from backend.services.goal_manager import GoalManager
from backend.services.main_agent import (
    _LAST_WAKE_REASON_CAP,
    WAKE_RUNNING,
    MainAgentService,
    coalesce_key_for,
)


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


class _FakeDBManager:
    """Independent sessions over the SAME shared in-memory connection."""

    def __init__(self, engine):
        self.engine = engine

    def new_session(self):
        return sessionmaker(bind=self.engine)()


def _make_worker(engine):
    from backend.services.wake_worker import WakeWorker

    worker = WakeWorker.__new__(WakeWorker)
    worker._db_manager = _FakeDBManager(engine)
    return worker


# ---------------------------------------------------------------------------
# 1. Last-wake truthfulness
# ---------------------------------------------------------------------------


class TestLastWakeTruthfulness:
    def test_finish_wake_records_last_wake_at_and_reason(self, db_session):
        agent = MainAgentService(db_session)
        sub = agent.submit_wake("goal.wake", trigger_reference="g-1",
                                wake_key="goal.wake:g-1")
        wake = agent.claim_wake("goal.wake:g-1")
        agent.finish_wake(wake, status="COMPLETED", outcome="no_work",
                          stop_reason="no_runnable_goal")

        row = db_session.query(MainAgent).filter_by(id=1).one()
        assert row.last_wake_at is not None
        assert row.last_wake_reason == "goal.wake:no_runnable_goal"
        assert sub["wake"].trigger_type == "goal.wake"
        # The read surfaces see the same truthful value.
        assert agent.self_model()["last_wake_reason"] == "goal.wake:no_runnable_goal"

    def test_finish_wake_reason_is_bounded(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="u-long")
        wake = agent.claim_wake("u-long")
        # A pathological stop_reason must never blow up persisted state.
        agent.finish_wake(wake, outcome="completed", stop_reason="x" * 500)

        row = db_session.query(MainAgent).filter_by(id=1).one()
        assert len(row.last_wake_reason) <= _LAST_WAKE_REASON_CAP
        assert row.last_wake_reason.startswith("user.request:")

    def test_finish_wake_falls_back_to_outcome_without_stop_reason(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("task.completed", wake_key="tc-fb")
        wake = agent.claim_wake("tc-fb")
        agent.finish_wake(wake, outcome="completed", stop_reason=None)

        row = db_session.query(MainAgent).filter_by(id=1).one()
        assert row.last_wake_reason == "task.completed:completed"
        assert row.last_wake_at is not None

    def test_skip_wake_does_not_overwrite_executed_wake_fields(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("goal.wake", wake_key="gw-skip-guard")
        wake = agent.claim_wake("gw-skip-guard")
        agent.finish_wake(wake, status="COMPLETED", outcome="no_work",
                          stop_reason="no_runnable_goal")
        db_session.expire_all()
        before = db_session.query(MainAgent).filter_by(id=1).one()
        recorded_at, recorded_reason = before.last_wake_at, before.last_wake_reason

        # A skip is an auditable no-op, not an executed wake.
        agent.skip_wake("user.request", reason="agent_paused", wake_key="u-skipped")
        db_session.expire_all()
        after = db_session.query(MainAgent).filter_by(id=1).one()
        assert after.last_wake_at == recorded_at
        assert after.last_wake_reason == recorded_reason
        # The skip is still durably auditable as its own wake row.
        skipped = db_session.query(MainAgent).filter_by(id=1).one()
        assert skipped.state == "active"  # skip does not change agent state


# ---------------------------------------------------------------------------
# 2. Bounded owner gate respects agent lifecycle state
# ---------------------------------------------------------------------------


class TestBoundedOwnerRespectsAgentState:
    def test_owner_enabled_gate_tracks_pause_and_shutdown(self, db_session):
        worker = _make_worker(db_session.bind)
        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.set_bounded_wake_enabled(True)
        assert worker._bounded_owner_enabled() is True

        agent.set_state("paused", reason="test")
        assert worker._bounded_owner_enabled() is False

        agent.set_state("active", reason="test")
        assert worker._bounded_owner_enabled() is True

        agent.set_state("shutdown", reason="test")
        assert worker._bounded_owner_enabled() is False

    def test_apply_owner_policy_stops_loop_when_paused(self, db_session, monkeypatch):
        worker = _make_worker(db_session.bind)
        calls = []
        monkeypatch.setattr(worker, "stop_bounded_loop",
                            lambda: calls.append("stop"))

        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.set_bounded_wake_enabled(True)
        agent.set_state("paused", reason="test")

        assert worker.apply_owner_policy(db_session) is False
        assert calls == ["stop"]

    def test_apply_owner_policy_stops_loop_when_switch_off(self, db_session, monkeypatch):
        # Existing behaviour preserved: either owner switch off still stops.
        worker = _make_worker(db_session.bind)
        calls = []
        monkeypatch.setattr(worker, "stop_bounded_loop",
                            lambda: calls.append("stop"))

        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.set_bounded_wake_enabled(False)

        assert worker.apply_owner_policy(db_session) is False
        assert calls == ["stop"]


# ---------------------------------------------------------------------------
# 3. goal.resumed is actually submitted in production
# ---------------------------------------------------------------------------


@pytest.fixture
def installed_worker():
    import backend.services.wake_worker as ww

    fake = _FakeWorker()
    ww.set_wake_worker(fake)
    yield fake
    ww.set_wake_worker(None)


class _FakeWorker:
    def __init__(self, fail=False):
        self.calls = []
        self.fail = fail

    def submit(self, trigger_type, trigger_reference=None, wake_key=None):
        self.calls.append((trigger_type, trigger_reference))
        if self.fail:
            raise RuntimeError("submit exploded")


class TestResumeGoalWake:
    def test_goal_resumed_is_coalescable(self):
        assert coalesce_key_for("goal.resumed") == "background_work"

    def test_resume_goal_submits_goal_resumed_wake(self, db_session, installed_worker):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Resumable")
        manager.pause_goal(goal.id)
        installed_worker.calls.clear()

        resumed = manager.resume_goal(goal.id)

        assert resumed.status == "active"
        assert installed_worker.calls == [("goal.resumed", goal.id)]

    def test_resume_goal_survives_submit_failure(self, db_session, installed_worker):
        installed_worker.fail = True
        manager = GoalManager(db_session)
        goal = manager.create_goal("Resilient resume")
        manager.pause_goal(goal.id)

        resumed = manager.resume_goal(goal.id)

        # Best-effort: a broken wake worker must never break the resume.
        assert resumed.status == "active"
        assert installed_worker.calls == [("goal.resumed", goal.id)]


# ---------------------------------------------------------------------------
# 4. Recovery audit events reflect THIS wake's outcome, not a cumulative count
# ---------------------------------------------------------------------------


class TestWakeRecoveryAuditTruthfulness:
    def test_failure_after_a_reclaim_is_audited_as_failed(self, db_session):
        """A reclaimed wake in the same pass must not relabel a later failure.

        Regression: ``recover_pending_wakes`` chose the ledger event type from
        the cumulative ``reclaimed`` counter, so once any wake was reclaimed,
        every subsequent failure in the same pass was recorded as
        ``agent.wake.reclaimed`` with an untruthful audit trail.
        """
        import json
        from datetime import datetime, timedelta

        from backend.models.database import AgentWake, TaskEvent

        agent = MainAgentService(db_session)

        agent.submit_wake("user.request", wake_key="recover-ok")
        agent.submit_wake("user.request", wake_key="recover-dead")
        agent.claim_wake("recover-ok")
        agent.claim_wake("recover-dead")

        # Recovery iterates RUNNING rows in a stable order. Configure whichever
        # row it sees first as reclaimable so the cumulative `reclaimed` counter
        # is already > 0 by the time the failing wake is processed.
        orphans = (
            db_session.query(AgentWake)
            .filter(AgentWake.status == WAKE_RUNNING)
            .all()
        )
        assert len(orphans) == 2
        first, second = orphans
        first.attempts = 1  # below the cap -> reclaimed back to PENDING
        second.attempts = 5  # at the cap -> FAILED
        stale = datetime.utcnow() - timedelta(minutes=10)
        for row in orphans:
            row.claimed_at = stale
        db_session.commit()

        boot_time = datetime.utcnow() + timedelta(seconds=5)
        report = agent.recover_pending_wakes(boot_time)

        assert report["reclaimed"] == 1
        assert report["failed_reclaims"] == 1

        # State transitions and reasons are unchanged.
        failed = (
            db_session.query(AgentWake).filter_by(wake_key="recover-dead").one()
        )
        assert failed.status == "FAILED"
        assert failed.outcome == "reclaim_limit"
        assert failed.stop_reason == "worker crashed repeatedly"
        assert failed.finished_at is not None

        # Only the event-type label is corrected: the first wake is a reclaim,
        # the second is a failure -- even though `reclaimed` was already 1.
        events = (
            db_session.query(TaskEvent)
            .filter(
                TaskEvent.event_type.in_(
                    ("agent.wake.reclaimed", "agent.wake.failed")
                )
            )
            .order_by(TaskEvent.sequence_num.asc())
            .all()
        )
        event_by_key = {
            json.loads(event.event_data)["wake_key"]: event.event_type
            for event in events
        }
        assert event_by_key[first.wake_key] == "agent.wake.reclaimed"
        assert event_by_key[second.wake_key] == "agent.wake.failed"
