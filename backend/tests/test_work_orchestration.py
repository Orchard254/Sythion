"""
Phase 3 work orchestration & recovery tests.

Covers: crash recovery state machine (terminal success/failure, interrupted,
cancelled, missing task, deferred stale-running), idempotency (repeated
recovery/outcome/continuation), bounded retry policy with persisted attempts,
pause/resume/abandon lifecycle protection, automatic continuation within an
explicit session, runnable-goal query, and the Phase 3 security boundaries.
"""

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.models.database import Base, GoalPlan, PlanStep, Task, TaskEvent
from backend.services.goal_manager import GoalManager, InvalidGoalTransitionError
from backend.services.planner import CandidateStepModel, Planner
from backend.services.task_orchestrator import (
    FailureCategory,
    RetryPolicy,
    TaskOrchestrator,
    categorize_failure,
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


@pytest.fixture
def runner(db_session):
    from backend.services.task_runner import TaskRunner

    r = TaskRunner(db_session)
    assert asyncio.run(r.acquire_lease())
    return r


def _orch(db, runner=None):
    from backend.services.planner import Planner

    return TaskOrchestrator(state_store=runner, planner=Planner(db))


def _goal_with_steps(db, runner, *specs):
    manager = GoalManager(db)
    planner = Planner(db)
    orchestrator = _orch(db, runner)
    goal = manager.create_goal("Phase 3 goal")
    plan = planner.create_plan(goal.id, [
        CandidateStepModel(key=k, description=f"step {k}",
                           depends_on=(deps.split(",") if deps else []))
        for k, deps in specs
    ])
    return goal, planner, orchestrator


def _run_and_finish(db, runner, orchestrator, task_id, state,
                    terminal_reason=None, final_result=None, failure_code=None):
    asyncio.run(runner.transition_state(
        task_id, TaskState.RUNNING, "task.execution_started", {}))
    kwargs = {"event_data": {}}
    if terminal_reason is not None:
        kwargs["terminal_reason"] = terminal_reason
    if final_result is not None:
        kwargs["final_result"] = final_result
    event = "task.completed" if state == TaskState.COMPLETED else "task.failed"
    if failure_code:
        kwargs["event_data"] = {"code": failure_code}
    asyncio.run(runner.transition_state(task_id, state, event, **kwargs))


# ===========================================================================
# Failure taxonomy + retry policy
# ===========================================================================


class TestFailureTaxonomy:
    def test_categorization(self):
        assert categorize_failure("interrupted", None, None) is FailureCategory.INTERRUPTION
        # Machine-readable code from the kernel's task.failed event.
        assert categorize_failure("failed", "budget text", None,
                                  machine_code="ELAPSED_TIME_EXCEEDED") is FailureCategory.TIMEOUT
        # Human-readable strings alone are NOT classified (conservative).
        assert categorize_failure("failed", "ELAPSED_TIME_EXCEEDED budget", None) \
            is FailureCategory.EXECUTION_FAILURE
        assert categorize_failure("completed", None, "verification not passed") \
            is FailureCategory.VERIFICATION_FAILURE
        assert categorize_failure("failed", "boom", None) is FailureCategory.EXECUTION_FAILURE

    def test_retry_policy_bounds(self):
        policy = RetryPolicy(max_attempts=2)
        assert policy.can_retry(0, FailureCategory.EXECUTION_FAILURE)
        assert policy.can_retry(1, FailureCategory.EXECUTION_FAILURE)
        assert not policy.can_retry(2, FailureCategory.EXECUTION_FAILURE)
        # Non-retryable categories never retry regardless of attempts.
        assert not policy.can_retry(0, FailureCategory.INTERRUPTION)
        assert not policy.can_retry(0, FailureCategory.TIMEOUT)


# ===========================================================================
# Crash recovery
# ===========================================================================


class TestRecovery:
    def test_terminal_completed_task_recovers_step_to_completed(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        # Process "crashes" here; on restart the task is already completed.
        _run_and_finish(db_session, runner, orch, task_id, TaskState.COMPLETED,
                        final_result="done")

        result = orch.recover_step(started["step"].id)
        assert result == {"step_id": started["step"].id, "resolved": True,
                          "outcome": "completed"}
        step = planner.get_step(step_id := started["step"].id)
        assert step.status == "completed"
        assert step.evidence_task_id == task_id

    def test_terminal_failed_task_marks_step_failed(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.FAILED, terminal_reason="disk full")
        result = orch.recover_step(started["step"].id)
        assert result["outcome"] == "failed"
        assert planner.get_step(started["step"].id).status == "failed"
        assert "disk full" in planner.get_step(started["step"].id).failure_reason

    def test_interrupted_task_is_recoverable_not_failed(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.INTERRUPTED, terminal_reason="runner crash")
        result = orch.recover_step(started["step"].id)
        assert result["outcome"] == "deferred_interrupted_manual_review_required"
        assert result["resolved"] is False
        step = planner.get_step(started["step"].id)
        assert step.status == "running"
        # Attempt NOT re-counted by recovery.
        assert step.attempt_count == 1

    def test_cancelled_task_is_recoverable(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.CANCELLED, terminal_reason="user stop")
        result = orch.recover_step(started["step"].id)
        assert result["outcome"] == "deferred_cancelled_manual_review_required"
        assert result["resolved"] is False
        assert planner.get_step(started["step"].id).status == "running"

    def test_missing_task_safe_state_never_forged(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        step = started["step"]
        # Simulate a lost/orphaned task reference.
        step.evidence_task_id = "ghost-task"
        step.linked_task_id = "ghost-task"
        db_session.commit()

        result = orch.recover_step(step.id)
        assert result["outcome"] == "deferred_missing_task_review_required"
        assert result["resolved"] is False
        fresh = planner.get_step(step.id)
        assert fresh.status == "running"
        assert fresh.status != "completed"

    def test_still_running_task_defers_to_lease_path(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        # Task exists but the kernel never reached a terminal state.
        result = orch.recover_step(started["step"].id)
        assert result["outcome"] == "deferred_created"
        assert result["resolved"] is False
        assert planner.get_step(started["step"].id).status == "running"
        before_events = len(planner.ledger.event_history(f"goal:{goal.id}"))
        assert orch.recover_step(started["step"].id) == result
        assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before_events

    def test_recovery_of_verified_only_completion(self, db_session, runner):
        """A 'completed' task whose last mutating action failed verification
        must NOT recover to COMPLETED.

        Public completion is evidence-gated now, so a task carrying a failed
        mutating action cannot be moved to COMPLETED through the runner.  We
        assert that guard explicitly, then seed an explicit legacy/corrupt
        terminal fixture (a task row already stored as ``completed`` despite
        the failed evidence) to exercise the recovery path only.
        """
        from backend.models.database import Task

        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        asyncio.run(runner.record_event(task_id, "tool.executed", {
            "tool_name": "file_write", "success": False,
            "mutating": True, "verification": "failed",
        }))
        # Guard: public completion after a failed mutating action is rejected.
        with pytest.raises(RuntimeError, match="final mutating tool action failed"):
            _run_and_finish(db_session, runner, orch, task_id, TaskState.COMPLETED)

        # Explicit legacy corrupt terminal fixture for recovery testing only:
        # a persisted task already claiming COMPLETED even though its final
        # mutating action failed verification.  This is never produced by the
        # public API; recovery must refuse to forge COMPLETED from it.
        task = db_session.query(Task).filter_by(id=task_id).first()
        task.state = TaskState.COMPLETED.value
        db_session.commit()

        result = orch.recover_step(started["step"].id)
        assert result["outcome"] in ("failed", "retry_scheduled")
        assert planner.get_step(started["step"].id).status != "completed"

    def test_recover_all_and_full_restart_equivalence(self, db_session):
        """Recovery works through a brand-new session (process restart)."""
        engine = db_session.bind
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Restart goal")
        plan = planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        step = planner.plan_steps(plan.id)[0]

        writer = runner_for(db_session)
        assert asyncio.run(writer.acquire_lease())
        task = asyncio.run(writer.create_task("work"))
        step.status = "running"
        step.linked_task_id = task.id
        step.evidence_task_id = task.id
        step.attempt_count = 1

        # Pending plan-step work must NOT be mutated over by the runner: the
        # caller-owned transaction guard protects the transaction.
        with pytest.raises(RuntimeError, match="pending work"):
            asyncio.run(writer.transition_state(
                task.id, TaskState.RUNNING, "task.execution_started", {}))

        db_session.commit()

        # With a committed baseline, runner mutations proceed normally.
        asyncio.run(writer.transition_state(
            task.id, TaskState.RUNNING, "task.execution_started", {}))
        asyncio.run(writer.transition_state(
            task.id, TaskState.COMPLETED, "task.completed",
            {}, final_result="finished work"))
        db_session.commit()

        fresh = sessionmaker(bind=engine)()
        try:
            orch = _orch(fresh)
            results = orch.recover_all()
            assert len(results) == 1
            assert results[0]["outcome"] == "completed"
            fresh_step = Planner(fresh).get_step(step.id)
            assert fresh_step.status == "completed"
        finally:
            fresh.close()


def runner_for(db):
    from backend.services.task_runner import TaskRunner

    return TaskRunner(db)



# ===========================================================================
# Recovery cannot authorize a new attempt after uncertain old work
# ===========================================================================


class TestRestartNoReplay:
    def test_interrupted_with_uncertain_action_is_not_requeued(self, db_session, runner):
        from backend.services.goal_selector import RunnableGoalSelector

        goal, planner, orch = _goal_with_steps(
            db_session, runner, ("first", ""), ("next", "first"),
        )
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        asyncio.run(runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {}))
        asyncio.run(runner.record_event(task_id, "action.uncertain", {
            "action_id": "previous-unresolved-effect", "reason": "crash",
        }))
        asyncio.run(runner.transition_state(
            task_id, TaskState.INTERRUPTED, "task.interrupted", {"reason": "crash"},
        ))
        before = len(planner.ledger.event_history(f"goal:{goal.id}"))
        initial_attempts = planner.get_step(started["step"].id).attempt_count
        for _ in range(2):
            result = orch.recover_step(started["step"].id)
            assert result["outcome"] == "deferred_interrupted_manual_review_required"
            assert result["resolved"] is False
        assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before
        assert planner.get_step(started["step"].id).status == "running"
        assert planner.get_step(started["step"].id).attempt_count == initial_attempts
        assert planner.select_next_step(goal.id) is None
        assert RunnableGoalSelector(db_session).list_runnable_goals() == []
        assert db_session.query(Task).filter_by(objective_id=goal.id).count() == 1

    def test_mismatched_link_blocks_recovery_even_when_task_is_completed(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        _run_and_finish(db_session, runner, orch, task_id, TaskState.COMPLETED, final_result="done")
        step = planner.get_step(started["step"].id)
        step.linked_task_id = "different-task-id"
        db_session.commit()
        before = len(planner.ledger.event_history(f"goal:{goal.id}"))
        result = orch.recover_step(step.id)
        assert result["outcome"] == "deferred_evidence_binding_review_required"
        assert result["resolved"] is False
        assert planner.get_step(step.id).status == "running"
        assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before

    def test_foreign_goal_task_cannot_complete_local_step(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task = db_session.query(Task).filter_by(id=started["task"].id).one()
        task.objective_id = "different-objective"
        db_session.commit()
        result = orch.recover_step(started["step"].id)
        assert result["outcome"] == "deferred_evidence_binding_review_required"
        assert result["resolved"] is False
        assert planner.get_step(started["step"].id).status == "running"



# ===========================================================================
# Idempotency
# ===========================================================================


class TestIdempotency:
    def test_repeated_recovery_is_idempotent(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.FAILED, terminal_reason="x")
        first = orch.recover_step(started["step"].id)
        assert first["resolved"] is True
        second = orch.recover_step(started["step"].id)
        third = orch.recover_step(started["step"].id)
        assert second["outcome"] == "not_running(failed)"
        assert third["resolved"] is False
        # No duplicate events.
        events = [
            e for e in planner.ledger.event_history(f"goal:{goal.id}")
            if e["event_type"] == "plan_step.failed"
        ]
        assert len(events) == 1

    def test_repeated_outcome_application_is_noop(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.COMPLETED, final_result="done")
        first = asyncio.run(orch.apply_task_outcome(started["task"].id))
        second = asyncio.run(orch.apply_task_outcome(started["task"].id))
        assert first["outcome"] == "completed"
        assert second["updated"] is False
        events = [
            e for e in planner.ledger.event_history(f"goal:{goal.id}")
            if e["event_type"] == "plan_step.completed"
        ]
        assert len(events) == 1

    async def _duplicate_start_helper(self, orch, goal):
        started = await orch.start_next_step(goal.id)
        return started

    def test_duplicate_task_creation_prevented_by_idempotency_key(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(self._duplicate_start_helper(orch, goal))
        # Creating another task with the SAME key returns the existing task.
        duplicate = asyncio.run(runner.create_task(
            original_request=started["step"].description,
            idempotency_key=started["task"].idempotency_key,
        ))
        assert duplicate.id == started["task"].id


# ===========================================================================
# Retry semantics
# ===========================================================================


class TestRetry:
    @pytest.mark.parametrize("event_kind,payload", [
        ("action.uncertain", {"action_id": "previous-effect"}),
        ("action.started", {"action_id": "maybe-applied"}),
        ("tool.executed", {"mutating": True, "success": True}),
        ("tool.executed", {"success": False}),
        ("reasoning.started", {"job_id": "unfinished-reasoning", "purpose": "task_decision"}),
    ])
    def test_failed_task_with_uncertain_receipts_is_never_retried(
        self, db_session, runner, event_kind, payload,
    ):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        asyncio.run(runner.transition_state(
            task_id, TaskState.RUNNING, "task.execution_started", {},
        ))
        asyncio.run(runner.record_event(task_id, event_kind, payload))
        asyncio.run(runner.transition_state(
            task_id, TaskState.FAILED, "task.failed", {},
            terminal_reason="execution stopped",
        ))
        asyncio.run(orch.apply_task_outcome(task_id))
        step_id = started["step"].id
        assert planner.get_step(step_id).status == "failed"
        before = len(planner.ledger.event_history(f"goal:{goal.id}"))
        decision = orch.schedule_retry_if_allowed(step_id)
        assert decision["scheduled"] is False
        assert decision["requires_review"] is True
        assert planner.get_step(step_id).status == "failed"
        assert db_session.query(Task).filter_by(objective_id=goal.id).count() == 1
        assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before

    def test_explicit_readonly_receipt_preserves_bounded_retry_policy(
        self, db_session, runner,
    ):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        asyncio.run(runner.transition_state(
            task_id, TaskState.RUNNING, "task.execution_started", {},
        ))
        asyncio.run(runner.record_event(
            task_id, "tool.executed", {"mutating": False, "success": True},
        ))
        asyncio.run(runner.transition_state(
            task_id, TaskState.FAILED, "task.failed", {},
            terminal_reason="transient read-only failure",
        ))
        asyncio.run(orch.apply_task_outcome(task_id))
        decision = orch.schedule_retry_if_allowed(started["step"].id)
        assert decision["scheduled"] is True
        assert planner.get_step(started["step"].id).status == "ready"

    @pytest.mark.parametrize("corruption", [
        "completed_but_unverified", "missing_task", "mismatched_link", "foreign_goal",
    ])
    def test_uncertain_or_mismatched_task_never_authorizes_retry(self, db_session, runner, corruption):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        _run_and_finish(db_session, runner, orch, task_id,
                        TaskState.FAILED, terminal_reason="flaky")
        asyncio.run(orch.apply_task_outcome(task_id))
        step = planner.get_step(started["step"].id)
        assert step.status == "failed"
        task = db_session.query(Task).filter_by(id=task_id).one()
        if corruption == "completed_but_unverified":
            # Legacy/corrupt record: it may already have caused an effect.
            task.state = "completed"
        elif corruption == "missing_task":
            step.evidence_task_id = "ghost-task"
            step.linked_task_id = "ghost-task"
        elif corruption == "mismatched_link":
            step.linked_task_id = "another-task"
        else:
            task.objective_id = "foreign-goal"
        db_session.commit()
        before = len(planner.ledger.event_history(f"goal:{goal.id}"))
        attempts = planner.get_step(step.id).attempt_count

        result = orch.schedule_retry_if_allowed(step.id)
        assert result["scheduled"] is False
        assert result["requires_review"] is True
        assert planner.get_step(step.id).status == "failed"
        assert planner.get_step(step.id).attempt_count == attempts
        assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before

    def test_retry_allowed_then_exhausted(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))

        for expected_attempt in (1, 2, 3):
            started = asyncio.run(orch.start_next_step(goal.id))
            assert started is not None
            assert started["step"].attempt_count == expected_attempt
            _run_and_finish(db_session, runner, orch, started["task"].id,
                            TaskState.FAILED, terminal_reason="flaky")
            asyncio.run(orch.apply_task_outcome(started["task"].id))

            step = planner.get_step(started["step"].id)
            decision = orch.schedule_retry_if_allowed(step.id)
            if expected_attempt < orch.retry_policy.max_attempts:
                assert decision["scheduled"] is True
                assert planner.get_step(step.id).status == "ready"
            else:
                assert decision["scheduled"] is False

        # Exhausted: goal paused once, step stays FAILED.
        assert GoalManager(db_session).get_goal(goal.id).status == "paused"

    def test_retry_events_audited(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.FAILED, terminal_reason="flaky")
        asyncio.run(orch.apply_task_outcome(started["task"].id))
        step = planner.get_step(started["step"].id)
        orch.schedule_retry_if_allowed(step.id)

        types = [e["event_type"] for e in
                 planner.ledger.event_history(f"goal:{goal.id}")]
        assert types.count("plan_step.attempt.retry") == 1

    def test_non_retryable_category_never_retries(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        # TIMEOUT category via machine-readable budget code — non-retryable.
        _run_and_finish(db_session, runner, orch, started["task"].id,
                        TaskState.FAILED, terminal_reason="budget spent",
                        failure_code="ELAPSED_TIME_EXCEEDED")
        asyncio.run(orch.apply_task_outcome(started["task"].id))
        decision = orch.schedule_retry_if_allowed(started["step"].id)
        assert decision["scheduled"] is False
        assert decision["category"] == FailureCategory.TIMEOUT.value

    def test_attempt_persistence_across_sessions(self, db_session):
        engine = db_session.bind
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Attempts persist")
        plan = planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        step = planner.plan_steps(plan.id)[0]
        step.attempt_count = 2
        db_session.commit()

        fresh = sessionmaker(bind=engine)()
        try:
            assert Planner(fresh).get_step(step.id).attempt_count == 2
        finally:
            fresh.close()


# ===========================================================================
# Lifecycle: pause/resume/abandon protection + continuation bounds
# ===========================================================================


class TestLifecycleProtection:
    def test_paused_goal_cannot_start_new_work(self, db_session, runner):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orch = _orch(db_session, runner)
        goal = manager.create_goal("Paused goal")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        manager.pause_goal(goal.id)

        assert asyncio.run(orch.start_next_step(goal.id)) is None

    def test_abandoned_goal_cannot_resume_into_work(self, db_session, runner):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Abandoned")
        manager.abandon_goal(goal.id)
        with pytest.raises(InvalidGoalTransitionError):
            manager.resume_goal(goal.id)

    def test_terminal_goals_cannot_restart(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Done")
        db_session.query(type(goal)).filter_by(id=goal.id).update({"criteria_verified": True})
        db_session.commit()
        manager.complete_goal(goal.id)
        for action in ("resume_goal", "pause_goal"):
            with pytest.raises(InvalidGoalTransitionError):
                getattr(manager, action)(goal.id)

    def test_pause_survives_restart(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Persistently paused")
        manager.pause_goal(goal.id)

        engine = db_session.bind
        fresh = sessionmaker(bind=engine)()
        try:
            assert GoalManager(fresh).get_goal(goal.id).status == "paused"
        finally:
            fresh.close()

    def test_running_task_not_destroyed_by_pause(self, db_session, runner):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orch = _orch(db_session, runner)
        goal = manager.create_goal("Pause with running step")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        started = asyncio.run(orch.start_next_step(goal.id))
        manager.pause_goal(goal.id)
        # The running task remains untouched.
        assert asyncio.run(runner.get_task(started["task"].id)).state == "created"


class TestAutomaticContinuation:
    @pytest.fixture
    def kernel_scaffold(self, db_session):
        """Fake runner + worker over REAL Task rows so the evidence gate runs
        for real; only the OS-facing worker body is stubbed."""
        from backend.models.database import Task

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

                task = Task(
                    id=str(_uuid.uuid4()),
                    original_request=original_request,
                    idempotency_key=idempotency_key,
                    state="created",
                    version=1,
                )
                self.db.add(task)
                self.db.commit()
                self.db.refresh(task)
                return task

            def set_task_objective(self, task_id, objective_id):
                row = self.db.query(Task).filter_by(id=task_id).first()
                row.objective_id = objective_id
                self.db.commit()

            async def transition_state(self, task_id, new_state, event_type,
                                       event_data, **kw):
                row = self.db.query(Task).filter_by(id=task_id).first()
                row.state = new_state.value
                for k, v in kw.items():
                    setattr(row, k, v)
                self.db.commit()

        async def fake_run_task(db, task_id, tool_executor):
            # The "kernel work": every attempt succeeds cleanly.
            row = db_session.query(Task).filter_by(id=task_id).first()
            row.state = "running"
            db_session.commit()
            row.state = "completed"
            row.final_result = f"done {task_id}"
            db_session.commit()

        return FakeRunner(db_session), fake_run_task

    def test_continuation_runs_dependent_steps_within_session(
        self, db_session, kernel_scaffold
    ):
        fake_runner, run_fn = kernel_scaffold
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=fake_runner, planner=planner)
        goal = manager.create_goal("Continuation goal")
        planner.create_plan(goal.id, [
            CandidateStepModel(key="a", description="first"),
            CandidateStepModel(key="b", description="second", depends_on=["a"]),
        ])

        result = asyncio.run(orchestrator.execute_session(goal.id, run_task_fn=run_fn))

        assert [e["outcome"] for e in result["executed"]] == ["completed", "completed"]
        assert result["stopped_reason"] == "no_eligible_step"
        assert GoalManager(db_session).progress(goal.id)["fraction"] == 1.0
        # Goal auto-completed deterministically after all steps done.
        assert GoalManager(db_session).get_goal(goal.id).status == "completed"

    def test_budget_bounds_continuation(self, db_session, monkeypatch, kernel_scaffold):
        from backend.core.config import settings

        fake_runner, run_fn = kernel_scaffold
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=fake_runner, planner=planner)
        goal = manager.create_goal("Bounded continuation")
        planner.create_plan(goal.id, [
            CandidateStepModel(key=f"s{i}", description=f"step number {i}")
            for i in range(5)
        ])
        monkeypatch.setattr(settings, "max_steps_per_goal_execution", 2)

        result = asyncio.run(orchestrator.execute_session(goal.id, run_task_fn=run_fn))
        assert len(result["executed"]) == 2
        assert result["stopped_reason"] == "step_budget_reached"

    def test_retry_loop_within_session_then_exhaustion(self, db_session, monkeypatch,
                                                       kernel_scaffold):
        """A failing step is retried inside the session until the persisted
        limit, then the goal pauses and the session stops."""
        from backend.core.config import settings

        fake_runner, run_ok = kernel_scaffold
        monkeypatch.setattr(settings, "step_max_attempts", 2)

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=fake_runner, planner=planner)
        goal = manager.create_goal("Retry session goal")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])

        calls = {"n": 0}

        async def failing_run(db, task_id, tool_executor):
            from backend.models.database import Task

            calls["n"] += 1
            row = db_session.query(Task).filter_by(id=task_id).first()
            row.state = "failed"
            row.terminal_reason = f"flaky failure #{calls['n']}"
            db_session.commit()

        result = asyncio.run(orchestrator.execute_session(
            goal.id, run_task_fn=failing_run))

        assert calls["n"] == 2  # initial attempt + ONE bounded retry
        assert result["stopped_reason"] in ("step_retry_exhausted", "no_eligible_step")
        step = planner.plan_steps(planner.active_plan(goal.id).id)[0]
        assert step.status == "failed"
        assert step.attempt_count == 2
        assert GoalManager(db_session).get_goal(goal.id).status == "paused"


# ===========================================================================
# Runnable-goal query (scheduler boundary)
# ===========================================================================


class TestRunnableGoalsQuery:
    def test_runnable_listing_executes_nothing(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orch = _orch(db_session, runner=None)
        goal = manager.create_goal("Runnable")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])

        runnable = orch.list_runnable_goals()
        assert any(g["goal_id"] == goal.id for g in runnable)
        # Query had zero side effects: still ready, nothing running.
        assert planner.plan_steps(planner.active_plan(goal.id).id)[0].status == "ready"

    def test_paused_goal_not_runnable(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orch = _orch(db_session, runner=None)
        goal = manager.create_goal("Not runnable")
        planner.create_plan(goal.id, [CandidateStepModel(key="a", description="step a")])
        manager.pause_goal(goal.id)
        assert all(g["goal_id"] != goal.id for g in orch.list_runnable_goals())


# ===========================================================================
# Security boundaries
# ===========================================================================


class TestSecurityBoundaries:
    def test_orchestration_layer_has_no_tool_execution_surface(self):
        import inspect

        import backend.services.task_orchestrator as to

        src = inspect.getsource(to)
        assert "execute_tool" not in src
        assert "get_adapter()" not in src

    def test_recovery_cannot_forged_complete_missing_task(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(db_session, runner, ("a", ""))
        started = asyncio.run(orch.start_next_step(goal.id))
        step = started["step"]
        step.evidence_task_id = None
        step.linked_task_id = None
        db_session.commit()
        orch.recover_step(step.id)
        assert planner.get_step(step.id).status == "pending"  # never completed

    def test_blocked_step_never_selected_even_after_recovery_loop(self, db_session, runner):
        goal, planner, orch = _goal_with_steps(
            db_session, runner, ("a", ""), ("b", "a"),
        )
        started = asyncio.run(orch.start_next_step(goal.id))
        # Recovery loop over everything while A runs: B must stay blocked.
        orch.recover_all()
        assert planner.get_step(started["step"].id).status == "running"
        blocked = planner.plan_steps(planner.active_plan(goal.id).id)[1]
        assert blocked.status == "blocked"


# ===========================================================================
# Terminal task evidence cannot be borrowed from a foreign or old plan
# ===========================================================================

class TestGoalEvidenceBinding:
    @pytest.mark.parametrize("corruption", [
        "wrong_link", "foreign_goal", "superseded_plan", "duplicate_binding",
    ])
    def test_terminal_outcome_requires_unique_active_goal_binding(
        self, db_session, runner, corruption,
    ):
        from backend.runtime.events import DbEventLedger

        goal, planner, orch = _goal_with_steps(
            db_session, runner, ("a", ""), ("b", ""),
        )
        started = asyncio.run(orch.start_next_step(goal.id))
        task_id = started["task"].id
        step_id = started["step"].id
        _run_and_finish(
            db_session, runner, orch, task_id,
            TaskState.COMPLETED, final_result="worker says done",
        )
        step = planner.get_step(step_id)
        if corruption == "wrong_link":
            step.linked_task_id = "unrelated-task"
        elif corruption == "foreign_goal":
            foreign = GoalManager(db_session).create_goal("Foreign objective")
            db_session.query(Task).filter_by(id=task_id).one().objective_id = foreign.id
        elif corruption == "superseded_plan":
            planner.create_plan(goal.id, [
                CandidateStepModel(key="replacement", description="New work"),
            ])
        else:
            sibling = next(s for s in planner.plan_steps(step.plan_id) if s.id != step_id)
            sibling.evidence_task_id = task_id
            sibling.linked_task_id = task_id
        db_session.commit()

        scope = f"goal:{goal.id}"
        before = len(DbEventLedger(db_session).event_history(scope))
        with pytest.raises(ValueError, match="binding"):
            planner.complete_step_from_evidence(step_id, task_id)
        outcome = asyncio.run(orch.apply_task_outcome(task_id))
        assert outcome is not None
        assert outcome["outcome"] == "unverified"
        assert outcome["updated"] is False
        assert planner.get_step(step_id).status == "running"
        assert orch._observed_step_outcome(
            goal_id=goal.id, step_id=step_id, task_id=task_id,
        ) == "unverified"
        assert len(DbEventLedger(db_session).event_history(scope)) == before
        assert GoalManager(db_session).get_goal(goal.id).status == "active"


@pytest.mark.parametrize("blocking_field,value", [
    ("active_action_id", "unresolved-action"),
    ("pending_action", '{"status":"approved"}'),
])
def test_runtime_verifier_cannot_be_bypassed_by_optional_callback(
    db_session, runner, blocking_field, value,
):
    """A terminal task projection plus permissive callback is not evidence."""
    goal, planner, orch = _goal_with_steps(db_session, runner, ("one", ""))
    started = asyncio.run(orch.start_next_step(goal.id))
    task_id = started["task"].id
    task = db_session.query(Task).filter_by(id=task_id).one()
    task.state = "completed"
    setattr(task, blocking_field, value)
    db_session.commit()
    step_id = started["step"].id
    before = len(planner.ledger.event_history(f"goal:{goal.id}"))
    with pytest.raises(ValueError, match="runtime evidence unacceptable"):
        planner.complete_step_from_evidence(
            step_id, task_id, completion_precondition_error_fn=lambda _: None,
        )
    assert planner.get_step(step_id).status == "running"
    assert len(planner.ledger.event_history(f"goal:{goal.id}")) == before
    assert GoalManager(db_session).get_goal(goal.id).status == "active"
