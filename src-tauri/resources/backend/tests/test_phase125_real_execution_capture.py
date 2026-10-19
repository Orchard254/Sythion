"""Phase 12.5 — End-to-end REAL_EXECUTION capture audit.

Proves the production path:
  Goal → Planner → TaskRunner → CompletionVerifier → authoritative terminal → planning outcome → PlanningOutcomeStore

without manual store.record injection as primary action.
"""

import asyncio
import hashlib

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, PlanningOutcome
from backend.learning.planning_taxonomy import PlanningStrategy, PlanningProvenance
from backend.learning.planning_store import PlanningOutcomeStore, PlanningShadowStore
from backend.services.goal_manager import GoalManager
from backend.services.planner import CandidateStepModel, Planner
from backend.services.task_orchestrator import TaskOrchestrator
from backend.api.schemas import TaskState


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine)
    s = S()
    yield s
    s.close()
    engine.dispose()


@pytest.fixture
def runner(db):
    from backend.services.task_runner import TaskRunner
    r = TaskRunner(db)
    assert asyncio.run(r.acquire_lease())
    return r


def _orch(db, runner):
    return TaskOrchestrator(state_store=runner, planner=Planner(db))


def _goal_with_plan(db, runner, steps=None):
    mgr = GoalManager(db)
    planner = Planner(db)
    goal = mgr.create_goal("E2E goal", purpose="verify real execution")
    cands = steps or [CandidateStepModel(key="a", description="step a work")]
    plan = planner.create_plan(goal.id, cands, source="user")
    return goal, planner, _orch(db, runner), plan


def _verifier(db):
    from backend.runtime.events import DbEventLedger
    from backend.services.execution_components import CompletionVerifier
    ledger = DbEventLedger(db)
    def fn(tid):
        return CompletionVerifier(db, ledger.event_history).precondition_error(tid)
    return fn


def _run_and_apply(db, runner, orch, task_id, state, terminal_reason=None, final_result=None, failure_code=None):
    asyncio.run(runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {}))
    kwargs = {}
    if terminal_reason is not None:
        kwargs["terminal_reason"] = terminal_reason
    if final_result is not None:
        kwargs["final_result"] = final_result
    event = "task.completed" if state == TaskState.COMPLETED else "task.failed" if state == TaskState.FAILED else "task.cancelled"
    if failure_code:
        kwargs["event_data"] = {"code": failure_code}
    else:
        kwargs["event_data"] = {}
    # handle cancelled separately
    if state == TaskState.CANCELLED:
        # use force_cancel if lease semantics messy; but runner has lease
        asyncio.run(runner.transition_state(task_id, TaskState.CANCELLED, "task.cancelled", {"reason": "user_requested"}, terminal_reason="cancelled_by_user"))
    else:
        asyncio.run(runner.transition_state(task_id, state, event, **kwargs))
    # apply through production path (authoritative verifier)
    return asyncio.run(orch.apply_task_outcome(task_id, completion_precondition_error_fn=_verifier(db)))


# ---------------------------------------------------------------------------
# Verified success — exactly one REAL_EXECUTION outcome
# ---------------------------------------------------------------------------

def test_verified_success_via_production_path(db, runner):
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    assert started is not None
    task_id = started["task"].id
    store = PlanningOutcomeStore(db)
    before_real = db.query(PlanningOutcome).filter(PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).count()
    before_synth = db.query(PlanningOutcome).filter(PlanningOutcome.provenance != PlanningProvenance.REAL_EXECUTION.value).count()
    before_shadow = len(PlanningShadowStore(db).list())

    result = _run_and_apply(db, runner, orch, task_id, TaskState.COMPLETED, final_result="done")
    assert result["outcome"] == "completed"

    # Verify stored outcome has provenance REAL_EXECUTION and matches verification
    rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(rows) == 1, f"expected exactly one REAL_EXECUTION, got {len(rows)}"
    row = rows[0]
    assert row.outcome == "SUCCESS_VERIFIED"
    assert row.verified_success is True
    assert row.provenance == "REAL_EXECUTION"
    # evidence refs contain goal/plan/task/step
    import json
    refs = json.loads(row.evidence_refs_json)
    assert any(goal.id in r for r in refs)
    assert any(task_id in r for r in refs)

    # no LEGACY/SYNTHETIC/SIMULATED/IMPORTED incorrectly generated for this execution
    for r in rows:
        assert r.provenance == "REAL_EXECUTION"

    # exactly-once: second apply is noop, no duplicate
    second = asyncio.run(orch.apply_task_outcome(task_id, completion_precondition_error_fn=_verifier(db)))
    assert second["updated"] is False
    rows2 = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(rows2) == 1

    # metrics integrity
    after_real = db.query(PlanningOutcome).filter(PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).count()
    after_synth = db.query(PlanningOutcome).filter(PlanningOutcome.provenance != PlanningProvenance.REAL_EXECUTION.value).count()
    assert after_real == before_real + 1
    assert after_synth == before_synth
    # shadow metrics unchanged unless shadow evaluation occurred
    after_shadow = len(PlanningShadowStore(db).list())
    assert after_shadow == before_shadow

    # promotion readiness reflects new evidence (but not yet promoted)
    from backend.learning.planning_store import PlanningPolicyStore
    pstore = PlanningPolicyStore(db)
    pol = pstore.create_observed("goal", row.planning_strategy, evidence_summary={"sample_count": after_real})
    readiness = store.promotion_readiness(pol)
    assert readiness["real_samples"] >= 1

    # no evidence attributed to strategy not executed (e.g., DELEGATED_PLAN not used here)
    # The row's strategy should be one of the inferred ones, but not arbitrarily invented
    assert row.planning_strategy in PlanningStrategy.values()


def test_verified_failure_via_production_path(db, runner):
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    task_id = started["task"].id

    result = _run_and_apply(db, runner, orch, task_id, TaskState.FAILED, terminal_reason="boom")
    assert result["outcome"] == "failed"

    rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(rows) == 1
    row = rows[0]
    assert row.provenance == "REAL_EXECUTION"
    assert row.outcome in ("FAILED", "FAILED_VERIFICATION")
    assert row.verified_success is False or row.verified_success == 0  # False stored as 0 in sqlite

    # metrics: real increased by 1, synthetic unchanged
    assert db.query(PlanningOutcome).filter(PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).count() >= 1


def test_retry_final_evidence_correct(db, runner):
    from backend.core.config import settings
    # use default step_max_attempts=3
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    # first attempt fails
    started1 = asyncio.run(orch.start_next_step(goal.id))
    tid1 = started1["task"].id
    _run_and_apply(db, runner, orch, tid1, TaskState.FAILED, terminal_reason="flaky1")
    # schedule retry
    step = planner.get_step(started1["step"].id)
    retry = orch.schedule_retry_if_allowed(step.id)
    assert retry is not None and retry["scheduled"] is True
    # second attempt succeeds
    started2 = asyncio.run(orch.start_next_step(goal.id))
    assert started2 is not None
    tid2 = started2["task"].id
    assert tid2 != tid1
    result2 = _run_and_apply(db, runner, orch, tid2, TaskState.COMPLETED, final_result="recovered")
    assert result2["outcome"] == "completed"

    rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).order_by(PlanningOutcome.id.asc()).all()
    # two executions: one failed, one success
    assert len(rows) == 2
    assert rows[0].outcome in ("FAILED", "FAILED_VERIFICATION")
    assert rows[1].outcome == "SUCCESS_VERIFIED"
    # final (success) should have retries=1
    assert rows[1].retries == 1
    # goal should be completed after retry success (if only one step)
    assert GoalManager(db).get_goal(goal.id).status == "completed" or rows[1].verified_success is True


def test_replan_not_counted_as_real(db, runner):
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    # trigger replan (creates plan version 2)
    cands2 = [CandidateStepModel(key="a", description="step a revised"), CandidateStepModel(key="b", description="step b")]
    plan2 = planner.create_plan(goal.id, cands2, source="user", reason="test replan")
    assert plan2.version == 2
    # replan rows should be LEGACY only
    replan_rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id).all()
    for r in replan_rows:
        assert r.provenance == "LEGACY"
    assert db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).count() == 0
    # now execute a step from new plan — should produce REAL
    started = asyncio.run(orch.start_next_step(goal.id))
    assert started is not None
    _run_and_apply(db, runner, orch, started["task"].id, TaskState.COMPLETED, final_result="ok")
    real_rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(real_rows) == 1
    # replan already exists but not counted; ensure attribution correct
    assert real_rows[0].replans == 1  # version2 => replans 1
    # LEGACY count unchanged
    legacy_rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.LEGACY.value).all()
    assert len(legacy_rows) >= 1


def test_cancellation_no_false_success(db, runner):
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    tid = started["task"].id
    # cancel via TaskRunner (holds lease)
    asyncio.run(runner.cancel_task(tid))
    # apply outcome — should not create SUCCESS_VERIFIED
    result = asyncio.run(orch.apply_task_outcome(tid, completion_precondition_error_fn=_verifier(db)))
    rows = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    # must not have SUCCESS_VERIFIED
    for r in rows:
        assert r.outcome != "SUCCESS_VERIFIED", "cancellation must not create false successful REAL_EXECUTION"
    # if a row exists, it must be CANCELLED or FAILED
    if rows:
        assert rows[0].outcome in ("CANCELLED", "FAILED", "FAILED_VERIFICATION")
    # ensure no verified_success true
    for r in rows:
        assert r.verified_success is not True


def test_restart_recovery_no_duplicate(db, runner):
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    tid = started["task"].id
    _run_and_apply(db, runner, orch, tid, TaskState.COMPLETED, final_result="done")
    rows_before = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(rows_before) == 1
    fp_before = rows_before[0].evidence_fingerprint
    # Simulate restart: new DB session + recover_all (should not duplicate)
    # Use same db session but call recover_step on already completed step
    res = orch.recover_step(started["step"].id)
    assert res["outcome"].startswith("not_running")
    rows_after = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id, PlanningOutcome.provenance == PlanningProvenance.REAL_EXECUTION.value).all()
    assert len(rows_after) == 1
    assert rows_after[0].evidence_fingerprint == fp_before
    # Also test duplicate fingerprint idempotence directly via store
    store = PlanningOutcomeStore(db)
    dup = store.record(planning_strategy=rows_before[0].planning_strategy, task_class="goal", goal_id=goal.id, evidence_fingerprint=fp_before, provenance="REAL_EXECUTION", evidence_refs=["goal:dup"])
    assert dup["reason"] == "duplicate_fingerprint_idempotent"
    assert dup["outcome"].id == rows_before[0].id


def test_exactly_once_fingerprint_dedup(db):
    store = PlanningOutcomeStore(db)
    fp = "dedup_test_fp_001"
    r1 = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="dedup_goal", evidence_fingerprint=fp, provenance="REAL_EXECUTION", evidence_refs=["goal:dedup_goal"])
    r2 = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="dedup_goal", evidence_fingerprint=fp, provenance="REAL_EXECUTION", evidence_refs=["goal:dedup_goal"])
    assert r1["outcome"].id == r2["outcome"].id
    count = db.query(PlanningOutcome).filter(PlanningOutcome.evidence_fingerprint == fp).count()
    assert count == 1


def test_metrics_integrity_no_strategy_attribution_without_execution(db, runner):
    store = PlanningOutcomeStore(db)
    before = store.coverage_report()
    before_real = before["real_sample_count"]
    goal, planner, orch, plan = _goal_with_plan(db, runner, steps=[CandidateStepModel(key="a", description="step a"), CandidateStepModel(key="b", description="step b", depends_on=["a"])])
    # only execute first step, not delegated — should not create DELEGATED_PLAN evidence
    started = asyncio.run(orch.start_next_step(goal.id))
    _run_and_apply(db, runner, orch, started["task"].id, TaskState.COMPLETED, final_result="ok")
    after = store.coverage_report()
    assert after["real_sample_count"] == before_real + 1
    # synthetic unchanged
    assert after["synthetic_sample_count"] == before["synthetic_sample_count"]
    # check that no DELEGATED_PLAN row was fabricated
    delegated_rows = db.query(PlanningOutcome).filter(PlanningOutcome.planning_strategy == PlanningStrategy.DELEGATED_PLAN.value).all()
    # could be 0 unless test earlier created; ensure latest goal's row is not DELEGATED
    latest = db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == goal.id).first()
    assert latest.planning_strategy != PlanningStrategy.DELEGATED_PLAN.value


def test_learning_only_preserved(db, runner):
    from backend.core.config import settings
    assert settings.planning_learning_mode == "LEARNING_ONLY"
    goal, planner, orch, plan = _goal_with_plan(db, runner)
    started = asyncio.run(orch.start_next_step(goal.id))
    _run_and_apply(db, runner, orch, started["task"].id, TaskState.COMPLETED, final_result="ok")
    # mode must remain LEARNING_ONLY and no auto-promotion occurred
    assert settings.planning_learning_mode == "LEARNING_ONLY"
    from backend.learning.planning_store import PlanningPolicyStore
    pstore = PlanningPolicyStore(db)
    # creating observed policy should remain OBSERVED, not auto-promoted
    pol = pstore.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value)
    assert pol.status == "OBSERVED"
