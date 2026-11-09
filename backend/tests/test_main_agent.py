"""
Phase 4 tests: Persistent Main Agent + event-driven background orchestration.

Covers: identity persistence, lifecycle state separation, wake policy gate,
durable deduplication, deterministic goal selection with same-priority
fairness, bounded background cycles across goals, cross-session continuity,
shutdown semantics, and the security boundary (the agent never bypasses the
kernel).
"""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import AgentWake, Base, GoalPlan, MainAgent, PlanStep, Task
from backend.services.goal_manager import GoalManager
from backend.services.goal_selector import RunnableGoalSelector
from backend.services.main_agent import MainAgentService
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


# ===========================================================================
# Identity & persistence
# ===========================================================================


class TestAgentPersistence:
    def test_identity_minted_once_and_stable_across_restart(self, db_session):
        svc = MainAgentService(db_session)
        first = svc.get_or_create()
        # Fresh "process": brand-new session on the same database.
        fresh = sessionmaker(bind=db_session.bind)()
        try:
            again = MainAgentService(fresh).get_or_create()
            assert again.agent_id == first.agent_id  # stable identity
            assert db_session.query(MainAgent).count() == 1
        finally:
            fresh.close()

    def test_shutdown_state_resets_to_active_on_next_boot(self, db_session):
        from datetime import datetime, timedelta

        svc = MainAgentService(db_session)
        svc.set_state("shutdown", reason="test")

        # Same-process check: SHUTDOWN is sticky (must NOT resurrect).
        assert svc.get_or_create().state == "shutdown"

        # Simulate a real NEW process boot: last write predates this boot.
        row = db_session.query(MainAgent).filter_by(id=1).one()
        row.updated_at = datetime.utcnow() - timedelta(days=1)
        db_session.commit()

        fresh = sessionmaker(bind=db_session.bind)()
        try:
            booted = MainAgentService(fresh).get_or_create()
            assert booted.state == "active"
        finally:
            fresh.close()

    def test_shutdown_is_sticky_within_same_process(self, db_session):
        """PHASE 4.5: runtime policy checks must not resurrect a shutdown."""
        svc = MainAgentService(db_session)
        svc.set_state("shutdown", reason="test")
        assert svc.get_or_create().state == "shutdown"

    def test_agent_exists_with_zero_goals_and_tasks(self, db_session):
        svc = MainAgentService(db_session)
        row = svc.get_or_create()
        assert row.state == "active"
        assert db_session.query(GoalPlan).count() == 0
        model = svc.self_model()
        assert model["goals_active"] == 0

    def test_state_domains_are_separate(self, db_session):
        """Pausing the AGENT does not touch GOAL or TASK rows."""
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Domain separation")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        task = Task(id="t1", original_request="x", state="running", version=1)
        db_session.add(task)

        MainAgentService(db_session).set_state("paused")

        assert manager.get_goal(goal.id).status == "active"
        assert db_session.query(Task).filter_by(id="t1").first().state == "running"


# ===========================================================================
# Policy + wake logic
# ===========================================================================


class TestWakePolicyAndDedup:
    def test_default_policy_is_disabled(self, agent):
        allowed, reason = agent.may_execute_background()
        assert allowed is False
        assert reason == "background_disabled"

    def test_enabled_allows_but_paused_blocks(self, agent):
        agent.set_background_enabled(True)
        assert agent.may_execute_background()[0] is True
        agent.set_state("paused", reason="test")
        allowed, reason = agent.may_execute_background()
        assert (allowed, reason) == (False, "agent_paused")
        # Policy flag itself unchanged by pausing.
        assert agent.background_enabled() is True

    def test_policy_persists_across_restart(self, db_session):
        MainAgentService(db_session).set_background_enabled(True)
        fresh = sessionmaker(bind=db_session.bind)()
        try:
            assert MainAgentService(fresh).background_enabled() is True
        finally:
            fresh.close()

    def test_wake_recorded_and_deduplicated_durably(self, agent):
        rec1 = agent.begin_wake("task.completed", trigger_reference="t-1",
                                wake_key="task.completed:t-1")
        rec2 = agent.begin_wake("task.completed", trigger_reference="t-1",
                                wake_key="task.completed:t-1")
        assert rec1["deduplicated"] is False
        assert rec2["deduplicated"] is True
        assert rec2["wake"].id == rec1["wake"].id
        assert agent.db.query(AgentWake).filter_by(
            wake_key="task.completed:t-1").count() == 1

    def test_duplicate_survives_restart(self, db_session):
        svc = MainAgentService(db_session)
        svc.begin_wake("application.startup", wake_key="startup:x")
        fresh = sessionmaker(bind=db_session.bind)()
        try:
            rec = MainAgentService(fresh).begin_wake(
                "application.startup", wake_key="startup:x")
            assert rec["deduplicated"] is True
        finally:
            fresh.close()

    def test_skipped_wake_is_audited(self, agent):
        result = agent.skip_wake("user.request", reason="agent_paused",
                                 wake_key="u-1")
        assert result["wake"].outcome == "skipped"
        events = [
            e["event_type"]
            for e in agent.ledger.event_history(
                f"agent:{agent.get().agent_id}")
        ]
        assert "agent.wake.skipped" in events


# ===========================================================================
# Wake worker end-to-end (queue â†’ policy â†’ bounded cycle)
# ===========================================================================


class TestWakeWorker:
    @pytest.fixture
    def scaffold(self, db_session, monkeypatch):
        from backend.core.config import settings
        from backend.services.wake_worker import WakeWorker

        monkeypatch.setattr(settings, "max_steps_per_background_cycle", 3)

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
                t = Task(id=str(_uuid.uuid4()), original_request=original_request,
                         idempotency_key=idempotency_key, state="created", version=1)
                self.db.add(t)
                self.db.commit()
                self.db.refresh(t)
                return t

            def set_task_objective(self, task_id, objective_id):
                pass

            async def transition_state(self, task_id, new_state, event_type, event_data, **kw):
                row = self.db.query(Task).filter_by(id=task_id).first()
                row.state = new_state.value
                self.db.commit()

        async def fake_run(db, task_id, tool_executor):
            row = db_session.query(Task).filter_by(id=task_id).first()
            row.state = "completed"
            row.final_result = "ok"
            db_session.commit()

        worker = WakeWorker.__new__(WakeWorker)
        import asyncio as _aio
        worker._queue = _aio.Queue()
        worker._queued_keys = set()
        worker._task = None
        worker._max_queue = 16
        orchestrator = TaskOrchestrator(
            state_store=FakeRunner(db_session),
            planner=Planner(db_session),
        )
        selector = RunnableGoalSelector(db_session)
        return worker, orchestrator, selector, fake_run

    def test_worker_runs_bounded_cycle_when_enabled(self, db_session, scaffold, monkeypatch):
        from backend.core.config import settings

        worker, orchestrator, selector, fake_run = scaffold
        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Worker goal")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])

        processed = {}

        async def process_capture(item):
            # Drive the REAL worker processing path manually.
            allowed, reason = agent.may_execute_background()
            if not allowed:
                agent.skip_wake(item["trigger_type"], reason,
                                wake_key=item["wake_key"])
                processed["skipped"] = reason
                return
            record = agent.begin_wake(item["trigger_type"],
                                      wake_key=item["wake_key"])
            result = await orchestrator.run_background_cycle(
                selector, max_steps=settings.max_steps_per_background_cycle,
                run_task_fn=fake_run,
            )
            agent.finish_wake(record["wake"], outcome="completed",
                              stop_reason=result["stopped_reason"],
                              steps_attempted=len(result["executed"]),
                              steps_completed=sum(
                                  1 for e in result["executed"]
                                  if e["outcome"] == "completed"))
            processed.update(result)

        # Patch orchestrator.apply_task_outcome to use our stub rows.
        real_apply = orchestrator.apply_task_outcome

        async def apply_stub(task_id, completion_precondition_error_fn=None):
            row = db_session.query(Task).filter_by(id=task_id).first()
            if row and row.state != "completed":
                row.state = "completed"
                row.final_result = "done"
                db_session.commit()
            return await real_apply(task_id)

        orchestrator.apply_task_outcome = apply_stub

        asyncio.run(process_capture({
            "trigger_type": "user.request", "wake_key": "w-test",
        }))

        assert processed.get("stopped_reason") == "no_runnable_goal"
        wakes = agent.recent_wakes()
        assert wakes[0]["steps_completed"] == 1
        assert agent.self_model()["last_session_outcome"].startswith("COMPLETED")

    def test_worker_skips_audited_when_paused(self, db_session):
        """Direct unit of the skip path (no execution when paused)."""
        from backend.models.database import GoalPlan as _GP

        agent = MainAgentService(db_session)
        agent.set_background_enabled(True)
        agent.set_state("paused", reason="test")

        allowed, reason = agent.may_execute_background()
        assert allowed is False
        res = agent.skip_wake("user.request", reason, wake_key="skip-1")
        assert res["wake"].outcome == "skipped"
        assert res["wake"].stop_reason == "agent_paused"

    def test_unknown_trigger_type_is_still_safe(self, agent):
        """Unknown triggers cannot execute anything unsafe: they follow the
        exact same policy gate + durable dedup path."""
        rec = agent.begin_wake("external_event", trigger_reference="mystery",
                               wake_key="ext-1")
        assert rec["deduplicated"] is False
        again = agent.begin_wake("external_event", trigger_reference="mystery",
                                 wake_key="ext-1")
        assert again["deduplicated"] is True
        assert agent.db.query(AgentWake).filter_by(wake_key="ext-1").count() == 1


# ===========================================================================
# Goal selection: priority + fairness
# ===========================================================================


def _mk_goal_with_ready_step(db, manager, planner, title, priority):
    goal = manager.create_goal(title, priority=priority)
    plan = planner.create_plan(goal.id, [
        CandidateStepModel(key="only", description=f"work for {title}")
    ])
    return goal, planner.plan_steps(plan.id)[0]


class TestGoalSelection:
    def test_priority_ordering(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)

        low, _ = _mk_goal_with_ready_step(db_session, manager, planner, "low", 5)
        high, _ = _mk_goal_with_ready_step(db_session, manager, planner, "high", 1)

        runnable = selector.list_runnable_goals()
        assert [g["goal_id"] for g in runnable] == [high.id, low.id]
        assert selector.pick_goal()["goal_id"] == high.id

    def test_paused_terminal_blocked_not_runnable(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)

        paused = manager.create_goal("Paused")
        p_plan = planner.create_plan(paused.id, [
            CandidateStepModel(key="a", description="step a")])
        manager.pause_goal(paused.id)

        done = manager.create_goal("Done")
        d_plan = planner.create_plan(done.id, [
            CandidateStepModel(key="a", description="step a")])
        step = planner.plan_steps(d_plan.id)[0]
        step.status = "completed"
        db_session.commit()

        blocked_goal = manager.create_goal("AllBlocked")
        b_plan = planner.create_plan(blocked_goal.id, [
            CandidateStepModel(key="a", description="step a"),
            CandidateStepModel(key="b", description="step b", depends_on=["a"]),
        ])
        # Nothing eligible right now: the only unblocked step is RUNNING.
        planner.plan_steps(b_plan.id)[0].status = "running"
        db_session.commit()

        runnable_ids = {g["goal_id"] for g in selector.list_runnable_goals()}
        assert paused.id not in runnable_ids
        assert done.id not in runnable_ids
        assert blocked_goal.id not in runnable_ids
        assert selector.explain_ineligibility(paused.id)["reasons"] == ["goal_state_paused"]

    def test_same_priority_fairness_no_starvation(self, db_session):
        """Two equal-priority goals alternate â€” one cannot monopolize."""
        manager = GoalManager(db_session)
        selector = RunnableGoalSelector(db_session)
        planner = Planner(db_session)

        ga, sa = _mk_goal_with_ready_step(db_session, manager, planner, "A", 3)
        gb, sb = _mk_goal_with_ready_step(db_session, manager, planner, "B", 3)

        served = []
        for _ in range(4):
            chosen = selector.pick_goal()
            served.append(chosen["goal_id"])
            # Simulate serving without completing (fairness key updates only).
            selector.mark_served(chosen["goal_id"])

        # Strict alternation: no goal appears twice in a row.
        for prev, cur in zip(served, served[1:]):
            assert prev != cur
        assert set(served) == {ga.id, gb.id}

    def test_explain_ineligibility_details(self, db_session):
        manager = GoalManager(db_session)
        selector = RunnableGoalSelector(db_session)
        missing = selector.explain_ineligibility("nope")
        assert missing["runnable"] is False
        assert "goal_not_found" in missing["reasons"]


# ===========================================================================
# Cross-session continuity + bounds via background cycle
# ===========================================================================


@pytest.fixture
def kernel(db_session):
    """Fake runner + worker over REAL Task rows; only the OS body is stubbed."""
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
            t = Task(id=str(_uuid.uuid4()), original_request=original_request,
                     idempotency_key=idempotency_key, state="created", version=1)
            self.db.add(t)
            self.db.commit()
            self.db.refresh(t)
            return t

        def set_task_objective(self, task_id, objective_id):
            pass

        async def transition_state(self, task_id, new_state, event_type, event_data, **kw):
            row = self.db.query(Task).filter_by(id=task_id).first()
            row.state = new_state.value
            self.db.commit()

    async def run_ok(db, task_id, tool_executor):
        row = db_session.query(Task).filter_by(id=task_id).first()
        row.state = "completed"
        row.final_result = f"done {task_id}"
        db_session.commit()

    orchestrator = TaskOrchestrator(
        state_store=FakeRunner(db_session),
        planner=Planner(db_session),
    )
    return orchestrator, run_ok


class TestBackgroundCycle:


    def test_multi_goal_cycle_is_fair_and_bounded(self, db_session, kernel):
        orchestrator, run_ok = kernel
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)

        for name in ("First", "Second"):
            goal = manager.create_goal(name)
            planner.create_plan(goal.id, [
                CandidateStepModel(key=f"{name.lower()}1", description=f"{name} one"),
                CandidateStepModel(key=f"{name.lower()}2", description=f"{name} two"),
            ])

        result = asyncio.run(orchestrator.run_background_cycle(
            selector, max_steps=2, run_task_fn=run_ok))

        goals_served = [e["goal_id"] for e in result["executed"]]
        assert len(set(goals_served)) == 2  # both goals got a slot
        assert result["stopped_reason"] == "step_budget_reached"
        # Fairness recorded.
        for g in goals_served:
            assert manager.get_goal(g).last_served_at is not None

    def test_cross_session_continuity_via_persisted_state_only(self, db_session, kernel):
        """Session 1 completes Step A; a LATER, memory-less session runs B."""
        orchestrator, run_ok = kernel
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)
        goal = manager.create_goal("Continuity")
        planner.create_plan(goal.id, [
            CandidateStepModel(key="a", description="step a"),
            CandidateStepModel(key="b", description="step b", depends_on=["a"]),
        ])

        first = asyncio.run(orchestrator.run_background_cycle(
            selector, max_steps=1, run_task_fn=run_ok))
        assert len(first["executed"]) == 1

        # Simulate full restart: brand-new everything, same DB.
        fresh_engine = db_session.bind
        fresh = sessionmaker(bind=fresh_engine)()
        try:
            from backend.models.database import Task as _Task

            class FreshRunner:
                def __init__(self, db):
                    self.db = db

                async def acquire_lease(self):
                    return True

                async def release_lease(self):
                    return None

                async def create_task(self, original_request, idempotency_key=None):
                    import uuid as _uuid
                    t = _Task(id=str(_uuid.uuid4()),
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
                    row = self.db.query(_Task).filter_by(id=task_id).first()
                    row.state = new_state.value
                    self.db.commit()

            orch2 = TaskOrchestrator(
                state_store=FreshRunner(fresh),
                planner=Planner(fresh),
            )
            second_selector = RunnableGoalSelector(fresh)
            second = asyncio.run(orch2.run_background_cycle(
                second_selector, max_steps=1,
                run_task_fn=lambda db, tid, te: _complete_in(fresh, tid)))
            assert second["executed"][0]["outcome"] == "completed"
            progress = manager.progress(goal.id)
            assert progress["fraction"] == 1.0
        finally:
            fresh.close()


async def _complete_in(session, task_id):
    row = session.query(Task).filter_by(id=task_id).first()
    row.state = "completed"
    row.final_result = "done later"
    session.commit()


# ===========================================================================
# Security boundaries
# ===========================================================================


class TestSecurityBoundaries:
    def test_agent_modules_never_touch_tool_execution_or_adapter(self):
        import inspect

        import backend.services.main_agent as ma
        import backend.services.goal_selector as gs
        import backend.services.wake_worker as ww

        for module in (ma, gs, ww):
            src = inspect.getsource(module)
            assert "execute_tool" not in src, module.__name__
            assert "get_adapter()" not in src, module.__name__

    def test_llm_output_cannot_create_runtime_success(self, db_session, kernel):
        orchestrator, run_ok = kernel
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        selector = RunnableGoalSelector(db_session)
        goal = manager.create_goal("No forged success")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])

        # Even if some LLM text claims success, without a completed task row
        # the outcome path fails conservatively.
        started = asyncio.run(orchestrator.start_next_step(goal.id))
        outcome = asyncio.run(orchestrator.apply_task_outcome(started["task"].id))
        assert outcome["outcome"] == "unverified"
        assert planner.get_step(started["step"].id).status == "running"
