"""
Phase 4.5 hardening tests: durable wakes, state machine, dedup/coalescing,
fairness backoff, service accounting, worker-crash recovery, startup
ordering, and the state-separation matrix.
"""

import asyncio
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import AgentWake, Base, GoalPlan, Objective, PlanStep, Task
from backend.services.goal_manager import GoalManager
from backend.services.goal_selector import RunnableGoalSelector
from backend.services.main_agent import (
    MainAgentService,
    WAKE_PENDING,
    WAKE_RUNNING,
    WAKE_COMPLETED,
    WAKE_SKIPPED,
)
from backend.services.planner import CandidateStepModel, Planner
from backend.services.task_orchestrator import TaskOrchestrator


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


@pytest.fixture
def agent(db_session):
    return MainAgentService(db_session)


def _goal_with_step(db, manager, planner, title="G", priority=3):
    goal = manager.create_goal(title, priority=priority)
    plan = planner.create_plan(goal.id, [
        CandidateStepModel(key="only", description=f"work for {title}")
    ])
    return goal, planner.plan_steps(plan.id)[0]


# ===========================================================================
# Durable wake state machine + submission-first persistence
# ===========================================================================


class TestDurableWakes:
    def test_submit_persists_pending_before_queue(self, agent):
        rec = agent.submit_wake("task.completed", trigger_reference="t9",
                                wake_key="task.completed:t9")
        assert rec["deduplicated"] is False
        row = agent.db.query(AgentWake).filter_by(wake_key="task.completed:t9").one()
        assert row.status == "PENDING"
        assert row.coalesce_key == "background_work"  # task.* coalesce class

    def test_user_request_never_coalesced(self, agent):
        rec = agent.submit_wake("user.request", wake_key="u-1")
        assert rec["wake"].coalesce_key == "user.request"

    def test_duplicate_submission_is_flagged_not_duplicated(self, agent):
        first = agent.submit_wake("user.request", wake_key="dup-1")
        second = agent.submit_wake("user.request", wake_key="dup-1")
        assert first["deduplicated"] is False
        assert second["deduplicated"] is True
        assert agent.db.query(AgentWake).filter_by(wake_key="dup-1").count() == 1

    def test_claim_is_atomic_single_winner(self, agent):
        agent.submit_wake("user.request", wake_key="claim-1")
        winner = agent.claim_wake("claim-1")
        loser = agent.claim_wake("claim-1")
        assert winner is not None and winner.status == WAKE_RUNNING
        assert loser is None  # exactly one logical execution

    def test_finish_maps_status_and_human_outcome(self, agent):
        agent.submit_wake("user.request", wake_key="fin-1")
        wake = agent.claim_wake("fin-1")
        agent.finish_wake(wake, status="COMPLETED", outcome="completed",
                          stop_reason="no_runnable_goal")
        row = agent.db.query(AgentWake).filter_by(wake_key="fin-1").one()
        assert row.status == "COMPLETED"
        assert row.outcome == "completed"
        assert row.finished_at is not None


# ===========================================================================
# Worker crash / restart recovery of durable wakes
# ===========================================================================


class TestWakeRecovery:
    def test_orphaned_running_wake_reclaimed_on_recovery(self, db_session, agent):
        agent.set_background_enabled(True)
        agent.submit_wake("user.request", wake_key="orphan-1")
        wake = agent.claim_wake("orphan-1")
        assert wake.status == WAKE_RUNNING

        from datetime import datetime, timedelta

        # Simulate a process crash: claim predates this boot.
        boot_time = datetime.utcnow() + timedelta(seconds=5)
        report = agent.recover_pending_wakes(boot_time)

        assert report["reclaimed"] == 1
        row = db_session.query(AgentWake).filter_by(wake_key="orphan-1").one()
        assert row.status == "PENDING"
        assert row.attempts == 1

    def test_live_wake_of_current_process_not_reclaimed(self, db_session, agent):
        agent.submit_wake("user.request", wake_key="live-1")
        agent.claim_wake("live-1")

        boot_now = agent.db.query(AgentWake).filter_by(
            wake_key="live-1").first().claimed_at
        report = agent.recover_pending_wakes(boot_now)

        assert report["reclaimed"] == 0
        row = db_session.query(AgentWake).filter_by(wake_key="live-1").one()
        assert row.status == WAKE_RUNNING

    def test_reclaim_limit_fails_after_five_attempts(self, db_session, agent):
        from datetime import timedelta

        agent.submit_wake("user.request", wake_key="flappy")
        for _ in range(5):
            agent.claim_wake("flappy")
            future = __import__("datetime").datetime.utcnow() + timedelta(minutes=10)
            agent.recover_pending_wakes(future)

        row = db_session.query(AgentWake).filter_by(wake_key="flappy").one()
        assert row.status == "FAILED"
        assert row.outcome == "reclaim_limit"
        # Bounded: no infinite reclaim loop.
        fresh_report = agent.recover_pending_wakes(
            __import__("datetime").datetime.utcnow() + timedelta(hours=1))
        assert fresh_report["reclaimed"] == 0

    def test_worker_drain_processes_durable_pending(self, db_session, monkeypatch):
        """Queue-full scenario: the PENDING row is processed by drain."""
        from backend.core.config import settings
        from backend.services.wake_worker import WakeWorker

        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal, _step = _goal_with_step(db_session, manager, planner, "Drained")

        states = {}

        class FakeRunner:
            def __init__(self, db):
                self.db = db

            async def acquire_lease(self):
                return True

            async def release_lease(self):
                return None

            async def create_task(self, original_request, idempotency_key=None):
                import uuid as _uuid
                t = Task(id=str(_uuid.uuid4()),
                         original_request=original_request,
                         idempotency_key=idempotency_key,
                         state="created", version=1)
                self.db.add(t)
                self.db.commit()
                self.db.refresh(t)
                return t

            def set_task_objective(self, task_id, objective_id):
                pass

            async def transition_state(self, task_id, new_state, event_type,
                                       event_data, **kw):
                row = self.db.query(Task).filter_by(id=task_id).first()
                row.state = new_state.value
                self.db.commit()

        async def run_ok(db, task_id, tool_executor):
            row = db_session.query(Task).filter_by(id=task_id).first()
            row.state = "completed"
            row.final_result = "done"
            db_session.commit()

        worker = WakeWorker.__new__(WakeWorker)
        import asyncio as _aio
        worker._queue = _aio.Queue()
        worker._queued_keys = set()
        worker._task = None
        worker._max_queue = 0  # queue effectively unusable â†’ drain must work
        worker._db_manager = _FakeDBManager(db_session.bind)

        # Patch the kernel worker at its MODULE import point so the real
        # orchestrator (constructed inside _process) uses our stub too.
        async def stub_run(db, task_id, tool_executor):
            row = db_session.query(Task).filter_by(id=task_id).first()
            row.state = "completed"
            row.final_result = "ok"
            db_session.commit()

        monkeypatch.setattr(
            "backend.services.task_execution_loop.run_task", stub_run
        )

        submitted = worker.submit("user.request", wake_key="saturated",
                                  trigger_reference=goal.id)
        assert submitted == "saturated"
        # Even though the queue never delivered it:
        processed = asyncio.run(worker.drain_durable())

        assert processed >= 1
        row = db_session.query(AgentWake).filter_by(wake_key="saturated").one()
        assert row.status == "COMPLETED"
        db_session.expire_all()
        assert manager.get_goal(goal.id).last_served_at is not None


class _FakeDBManager:
    """Independent sessions over the SAME shared in-memory connection."""

    def __init__(self, engine):
        self.engine = engine

    def new_session(self):
        return sessionmaker(bind=self.engine)()


# ===========================================================================
# Coalescing
# ===========================================================================


class TestCoalescing:
    def test_absorb_collapses_same_class_pending_wakes(self, db_session, agent):
        agent.set_background_enabled(True)
        a = agent.submit_wake("task.completed", trigger_reference="t1",
                              wake_key="tc:t1")
        b = agent.submit_wake("task.failed", trigger_reference="t2",
                              wake_key="tf:t2")
        c = agent.submit_wake("user.request", wake_key="ur:1")  # distinct class

        claimed = agent.claim_wake("tc:t1")
        absorbed = agent.absorb_coalescable(claimed)

        assert absorbed == 1  # only tf:t2 absorbed
        row_b = db_session.query(AgentWake).filter_by(wake_key="tf:t2").one()
        row_c = db_session.query(AgentWake).filter_by(wake_key="ur:1").one()
        assert row_b.status == WAKE_COMPLETED
        assert row_b.outcome == "coalesced"
        assert row_b.merged_into == claimed.id
        # Distinct explicit user request is preserved, untouched.
        assert row_c.status == WAKE_PENDING

    def test_distinct_events_not_coalesced_away_entirely(self, db_session, agent):
        """Absorption still leaves ONE live execution; nothing is lost â€” the
        surviving session re-evaluates all runnable goals."""
        agent.submit_wake("task.completed", trigger_reference="a",
                          wake_key="tc:a")
        agent.submit_wake("task.completed", trigger_reference="b",
                          wake_key="tc:b")
        agent.claim_wake("tc:a")
        agent.absorb_coalescable(
            db_session.query(AgentWake).filter_by(wake_key="tc:a").first())
        live = [
            w for w in db_session.query(AgentWake).all()
            if w.status in (WAKE_PENDING, WAKE_RUNNING)
        ]
        assert len(live) == 1


# ===========================================================================
# Fairness: failure backoff + service accounting
# ===========================================================================


class TestFairnessBackoff:
    @pytest.fixture
    def fast_backoff(self, monkeypatch):
        from backend.core.config import settings

        monkeypatch.setattr(settings, "backoff_failure_threshold", 2)
        monkeypatch.setattr(settings, "backoff_base_seconds", 300)
        monkeypatch.setattr(settings, "backoff_max_seconds", 3600)

    def test_backoff_applied_after_threshold_and_expires(self, db_session, fast_backoff):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Flaky high priority", priority=1)

        r1 = manager.record_service_outcome(goal.id, step_failed=True)
        assert r1["backoff_applied"] is False  # streak 1 < threshold 2

        r2 = manager.record_service_outcome(goal.id, step_failed=True)
        assert r2["backoff_applied"] is True
        assert manager.in_backoff(goal.id) is True

        # Expiry: bounded by timestamp.
        from datetime import datetime, timedelta
        after = manager.get_goal(goal.id).backoff_until + timedelta(seconds=1)
        assert manager.in_backoff(goal.id, now=after) is False

    def test_success_clears_backoff(self, db_session, fast_backoff):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Recovers", priority=1)
        manager.record_service_outcome(goal.id, step_failed=True)
        manager.record_service_outcome(goal.id, step_failed=True)
        assert manager.in_backoff(goal.id)

        manager.record_service_outcome(goal.id, step_failed=False)
        assert manager.in_backoff(goal.id) is False
        assert manager.get_goal(goal.id).consecutive_failed_sessions == 0

    def test_owner_override_clears_backoff(self, db_session, fast_backoff):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Owner override", priority=1)
        manager.record_service_outcome(goal.id, step_failed=True)
        manager.record_service_outcome(goal.id, step_failed=True)
        assert manager.in_backoff(goal.id)

        manager.clear_backoff(goal.id, reason="owner_override")
        assert manager.in_backoff(goal.id) is False

    def test_selector_respects_backoff_but_can_override(self, db_session, fast_backoff):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)
        goal, _ = _goal_with_step(db_session, manager, planner, "BackedOff", priority=1)
        manager.record_service_outcome(goal.id, step_failed=True)
        manager.record_service_outcome(goal.id, step_failed=True)

        assert selector.pick_goal(respect_backoff=True) is None
        override = selector.pick_goal(respect_backoff=False)
        assert override["goal_id"] == goal.id

    def test_high_priority_failures_do_not_starve_low_priority(self, db_session, fast_backoff):
        """THE Phase 4.5 fairness invariant: repeated high-priority failures
        eventually hand service to the lower-priority eligible goal."""
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)

        high, _ = _goal_with_step(db_session, manager, planner, "HighFail", priority=1)
        low, _ = _goal_with_step(db_session, manager, planner, "LowOk", priority=5)

        served = []
        # Drive selection manually through two failing sessions for HIGH.
        for _ in range(2):
            picked = selector.pick_goal(respect_backoff=True)
            assert picked["goal_id"] == high.id  # priority wins initially
            served.append(picked["goal_id"])
            manager.record_service_outcome(high.id, step_failed=True)

        # After threshold, HIGH is backed off â†’ LOW finally gets service.
        picked = selector.pick_goal(respect_backoff=True)
        assert picked is not None
        assert picked["goal_id"] == low.id
        served.append(picked["goal_id"])

        # And the owner can STILL explicitly serve HIGH (override).
        override_pick = selector.pick_goal(respect_backoff=False)
        assert override_pick["goal_id"] == high.id

    def test_service_accounting_requires_execution(self, db_session):
        """Selection alone must not update last_served_at."""
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)
        goal, _ = _goal_with_step(db_session, manager, planner, "Accounted")

        selector.list_runnable_goals()  # selected/inspected only
        assert manager.get_goal(goal.id).last_served_at is None

        selector.mark_served(goal.id)  # actual execution slot granted
        db_session.expire_all()
        assert manager.get_goal(goal.id).last_served_at is not None


# ===========================================================================
# State-separation matrix
# ===========================================================================


class TestStateSeparationMatrix:
    def test_paused_agent_with_pending_wake_skips(self, db_session, agent):
        agent.set_background_enabled(True)
        agent.set_state("paused", reason="test")
        agent.submit_wake("user.request", wake_key="pa-wake")
        allowed, reason = agent.may_execute_background()
        assert (allowed, reason) == (False, "agent_paused")
        res = agent.skip_wake("user.request", reason, wake_key="pa-wake")
        assert res["wake"].status == WAKE_SKIPPED

    def test_paused_goal_with_pending_wake_yields_no_work(self, db_session, agent):
        agent.set_background_enabled(True)
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal, _ = _goal_with_step(db_session, manager, planner, "PausedGoal")
        manager.pause_goal(goal.id)
        agent.submit_wake("user.request", wake_key="pg-wake")

        selector = RunnableGoalSelector(db_session)
        picked = selector.pick_goal(respect_backoff=False)
        assert picked is None  # paused goal structurally ineligible

    def test_shutdown_agent_blocks_all_background(self, db_session, agent):
        agent.set_background_enabled(True)
        agent.set_state("shutdown", reason="test")
        allowed, reason = agent.may_execute_background()
        assert (allowed, reason) == (False, "agent_shutdown")

    def test_active_agent_ineligible_goal_vs_eligible_goal(self, db_session):
        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)

        # Ineligible: only unblocked step is RUNNING.
        blocked = manager.create_goal("BlockedOnly")
        b_plan = planner.create_plan(blocked.id, [
            CandidateStepModel(key="a", description="step a"),
            CandidateStepModel(key="b", description="step b", depends_on=["a"]),
        ])
        planner.plan_steps(b_plan.id)[0].status = "running"
        db_session.commit()

        eligible = manager.create_goal("Eligible")
        planner.create_plan(eligible.id, [
            CandidateStepModel(key="only", description="work for Eligible")
        ])

        picks = {selector.pick_goal()["goal_id"] for _ in range(2)}
        assert blocked.id not in picks
        assert picks == {eligible.id}

# ===========================================================================
# WAKE-TX: owned zero-row absorb transaction + caller guard
# ===========================================================================

def _physical_in_transaction(session):
    try:
        conn = session.connection()
        dbapi = getattr(conn, "connection", None)
        dbapi = getattr(dbapi, "dbapi_connection", dbapi)
        return bool(getattr(dbapi, "in_transaction", False))
    except Exception:
        return bool(session.in_transaction())


class TestWakeOwnedTransaction:
    def test_zero_absorb_ends_owned_physical_transaction_for_runner_lease(self, db_session):
        from backend.services.task_runner import TaskRunner

        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.submit_wake("user.request", wake_key="zero-owned-lease")
        wake = agent.claim_wake("zero-owned-lease")
        assert agent.absorb_coalescable(wake) == 0
        assert _physical_in_transaction(db_session) is False

        runner = TaskRunner(db_session)
        assert asyncio.run(runner.acquire_lease()) is True
        asyncio.run(runner.release_lease())

    def test_positive_absorb_preserves_coalescing_outcome(self, db_session):
        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.submit_wake("task.completed", trigger_reference="p1",
                          wake_key="pos-owned-main")
        agent.submit_wake("task.failed", trigger_reference="p2",
                          wake_key="pos-owned-peer")
        wake = agent.claim_wake("pos-owned-main")
        assert agent.absorb_coalescable(wake) == 1

        peer = db_session.query(AgentWake).filter_by(
            wake_key="pos-owned-peer").one()
        assert peer.status == WAKE_COMPLETED
        assert peer.outcome == "coalesced"
        assert peer.merged_into == wake.id
        assert _physical_in_transaction(db_session) is False

    def test_absorb_allows_clean_read_only_logical_session(self, db_session):
        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.submit_wake("user.request", wake_key="readonly-owned")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="readonly-owned").first()

        # A plain read creates a logical SQLAlchemy scope but no physical write.
        db_session.commit()
        db_session.query(AgentWake).count()
        assert _physical_in_transaction(db_session) is False
        assert agent.absorb_coalescable(target) == 0

    def test_absorb_rejects_new_caller_orm_state_without_flush(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="absorb-refuse-new")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="absorb-refuse-new").first()

        from backend.models.database import AgentWake as WakeModel

        marker = WakeModel(
            agent_id=target.agent_id,
            wake_key="caller-new",
            trigger_type="user.request",
            coalesce_key="user.request",
            status=WAKE_PENDING,
            started_at=datetime.utcnow(),
        )
        db_session.add(marker)

        with pytest.raises(RuntimeError, match="caller-owned"):
            agent.absorb_coalescable(target)

        assert marker in db_session.new
        db_session.rollback()
        assert db_session.query(WakeModel).filter_by(
            wake_key="caller-new").count() == 0

    def test_absorb_rejects_dirty_caller_orm_state(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="absorb-refuse-dirty")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="absorb-refuse-dirty").first()

        from backend.models.database import AgentWake as WakeModel

        marker = WakeModel(
            agent_id=target.agent_id,
            wake_key="caller-dirty",
            trigger_type="user.request",
            coalesce_key="user.request",
            status=WAKE_PENDING,
            started_at=datetime.utcnow(),
        )
        db_session.add(marker)
        db_session.commit()
        marker.status = "FAILED"

        with pytest.raises(RuntimeError, match="caller-owned"):
            agent.absorb_coalescable(target)

        assert marker in db_session.dirty
        db_session.rollback()
        refreshed = db_session.query(WakeModel).filter_by(
            wake_key="caller-dirty").first()
        assert refreshed is not None
        assert refreshed.status == WAKE_PENDING

    def test_absorb_rejects_deleted_caller_orm_state(self, db_session):
        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="absorb-refuse-deleted")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="absorb-refuse-deleted").first()

        from backend.models.database import AgentWake as WakeModel

        marker = WakeModel(
            agent_id=target.agent_id,
            wake_key="caller-deleted",
            trigger_type="user.request",
            coalesce_key="user.request",
            status=WAKE_PENDING,
            started_at=datetime.utcnow(),
        )
        db_session.add(marker)
        db_session.commit()
        db_session.delete(marker)

        with pytest.raises(RuntimeError, match="caller-owned"):
            agent.absorb_coalescable(target)

        assert marker in db_session.deleted
        db_session.rollback()
        assert db_session.query(WakeModel).filter_by(
            wake_key="caller-deleted").count() == 1

    def test_absorb_rejects_rawPhysicalCaller_update_without_changing_state(
            self, db_session, monkeypatch):
        from sqlalchemy import text

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="absorb-refuse-raw")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="absorb-refuse-raw").first()

        db_session.execute(text("BEGIN IMMEDIATE"))
        db_session.execute(
            text(
                "INSERT INTO main_agent_wakes (agent_id, wake_key, trigger_type, "
                "coalesce_key, status, attempts, started_at) VALUES (:agent_id, "
                ":wake_key, :trigger_type, :coalesce_key, :status, 0, "
                "CURRENT_TIMESTAMP)"
            ),
            {
                "agent_id": target.agent_id,
                "wake_key": "caller-raw-owned",
                "trigger_type": "user.request",
                "coalesce_key": "user.request",
                "status": WAKE_PENDING,
            },
        )
        assert _physical_in_transaction(db_session) is True

        with pytest.raises(RuntimeError, match="caller-owned"):
            agent.absorb_coalescable(target)

        assert _physical_in_transaction(db_session) is True
        rows = db_session.execute(
            text("SELECT COUNT(*) FROM main_agent_wakes WHERE wake_key = "
                 "'caller-raw-owned'")
        ).scalar()
        assert rows == 1
        db_session.rollback()
        rows = db_session.execute(
            text("SELECT COUNT(*) FROM main_agent_wakes WHERE wake_key = "
                 "'caller-raw-owned'")
        ).scalar()
        assert rows == 0

    def test_absorb_unknown_physical_probe_fails_closed(self, db_session, monkeypatch):
        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="absorb-unknown-probe")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="absorb-unknown-probe").first()

        monkeypatch.setattr(db_session, "connection",
                            lambda: (_ for _ in ()).throw(RuntimeError("probe boom")))
        with pytest.raises(RuntimeError, match="could not determine"):
            agent.absorb_coalescable(target)

    class _ProbeSession:
        """Minimal real SQLite session proxy for physical-probe fail-closed tests."""

    def test_physical_probe_rejects_falsey_non_bool_values(self, db_session, monkeypatch):
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="probe-falsey")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="probe-falsey").first()

        for falsey in (0, None, [], "not-a-bool"):
            class _Driver:
                in_transaction = falsey

            class _Connection:
                connection = _Driver()

            monkeypatch.setattr(db_session, "connection", lambda: _Connection())
            with pytest.raises(RuntimeError, match="could not determine"):
                agent.absorb_coalescable(target)

        db_session.rollback()

    def test_physical_probe_rejects_callable_flag(self, db_session, monkeypatch):
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="probe-callable")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="probe-callable").first()

        class _Driver:
            in_transaction = lambda self: False

        class _Connection:
            connection = _Driver()

        monkeypatch.setattr(db_session, "connection", lambda: _Connection())
        with pytest.raises(RuntimeError, match="could not determine"):
            agent.absorb_coalescable(target)
        db_session.rollback()

    def test_physical_probe_connection_accessor_raises(self, db_session, monkeypatch):
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="probe-connection")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="probe-connection").first()

        class _Connection:
            @property
            def connection(self):
                raise RuntimeError("connection boom")

        monkeypatch.setattr(db_session, "connection", lambda: _Connection())
        with pytest.raises(RuntimeError, match="could not determine"):
            agent.absorb_coalescable(target)
        db_session.rollback()

    def test_physical_probe_driver_accessor_raises(self, db_session, monkeypatch):
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="probe-driver")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="probe-driver").first()

        class _Outer:
            @property
            def connection(self):
                raise RuntimeError("driver boom")

        monkeypatch.setattr(db_session, "connection", lambda: _Outer())
        with pytest.raises(RuntimeError, match="could not determine"):
            agent.absorb_coalescable(target)
        db_session.rollback()

    def test_physical_probe_flag_accessor_raises(self, db_session, monkeypatch):
        from backend.services.main_agent import MainAgentService

        agent = MainAgentService(db_session)
        agent.submit_wake("user.request", wake_key="probe-flag")
        db_session.commit()
        target = db_session.query(AgentWake).filter_by(
            wake_key="probe-flag").first()

        class _Driver:
            @property
            def in_transaction(self):
                raise RuntimeError("flag boom")

        class _Outer:
            connection = _Driver()

        monkeypatch.setattr(db_session, "connection", lambda: _Outer())
        with pytest.raises(RuntimeError, match="could not determine"):
            agent.absorb_coalescable(target)
        db_session.rollback()

class TestTerminalTaskWakeReconciliation:
    def test_creates_exactly_one_pending_wake_and_second_recovery_is_idempotent(self, db_session, agent):
        import uuid
        from datetime import datetime, timedelta

        from backend.models.database import Task

        now = datetime.utcnow()
        boot_time = now + timedelta(seconds=5)
        task = Task(
            id=str(uuid.uuid4()),
            original_request="terminal wake reconciliation test",
            state="completed",
            created_at=now,
            updated_at=now,
        )
        db_session.add(task)
        db_session.commit()

        wake_key = "task.completed:" + task.id
        first = agent.recover_pending_wakes(boot_time)
        rows = db_session.query(AgentWake).filter_by(wake_key=wake_key).all()
        assert first["reconciled_terminal_wakes"] == 1
        assert len(rows) == 1
        assert rows[0].status == WAKE_PENDING

        second = agent.recover_pending_wakes(boot_time)
        assert second["reconciled_terminal_wakes"] == 0
        assert db_session.query(AgentWake).filter_by(wake_key=wake_key).count() == 1
