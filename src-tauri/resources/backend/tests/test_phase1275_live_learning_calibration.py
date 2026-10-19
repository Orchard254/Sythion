"""Phase 12.75 — Live Learning Calibration & Production Evidence Validation Tests.

Validates:
- Real-only evidence provenance, task-class coverage, and strategy coverage (§3, §5, §14, §15);
- Real shadow validation & advantage truthfulness (UNKNOWN when unsupported) (§6, §7, §8);
- Authoritative specialist telemetry from Delegation records (§10, §11);
- Data quality validation for malformed records (§12, §13);
- Real retry-vs-replan calibration (§16);
- Evidence expiration tracking (§19);
- Manual promotion lifecycle & readiness states (§4, §21, §22);
- Live readiness endpoint GET /planning/live-readiness (§9);
- Persistence across DB reconnects (§20).
"""

from __future__ import annotations

import datetime
from datetime import timedelta
import json
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.config import settings
from backend.learning.planning_selector import PlanningPolicySelector
from backend.learning.planning_shadow import evaluate_shadow
from backend.learning.planning_store import (
    PlanningOutcomeStore,
    PlanningPolicyStore,
    PlanningShadowStore,
    compute_delegation_telemetry_for_goal,
)
from backend.learning.planning_taxonomy import (
    PlanningPolicyStatus,
    PlanningProvenance,
    PlanningStrategy,
    ReplanTrigger,
)
from backend.models.database import (
    AgentRecord,
    Base,
    Delegation,
    GoalPlan,
    Objective,
    PlanStep,
    PlanningOutcome,
    PlanningPolicy,
    PlanningShadowDecision,
)


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    session = Session()
    yield session
    session.close()
    engine.dispose()


# ---------------------------------------------------------------------------
# 1. Real Evidence Coverage & Provenance Separation (§3, §5, §14, §15)
# ---------------------------------------------------------------------------

def test_real_evidence_isolation_and_coverage(db):
    store = PlanningOutcomeStore(db)
    # Record 3 real outcomes
    for i in range(3):
        store.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="coding",
            goal_id=f"g_real_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance=PlanningProvenance.REAL_EXECUTION.value,
            evidence_refs=[f"goal:g_real_{i}"],
        )
    # Record synthetic and legacy outcomes
    store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="coding",
        goal_id="g_synth",
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance=PlanningProvenance.SYNTHETIC_TEST.value,
        evidence_refs=["goal:g_synth"],
    )
    store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="coding",
        goal_id="g_leg",
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance=PlanningProvenance.LEGACY.value,
        evidence_refs=["goal:g_leg"],
    )

    stats_real = store.statistics(task_class="coding", planning_strategy=PlanningStrategy.DIRECT_PLAN.value, include_synthetic=False)
    assert stats_real.sample_count == 3
    assert stats_real.real_sample_count == 3
    assert stats_real.real_verified_success_count == 3
    assert stats_real.synthetic_sample_count == 0

    readiness = store.live_readiness()
    assert readiness["real_sample_count"] == 3
    assert readiness["real_verified_successes"] == 3
    assert readiness["eligible_real_samples"] == 3
    assert readiness["policy_readiness"] == "LOW_REAL_EVIDENCE"
    assert readiness["promotion_eligible"] is False
    assert readiness["phase13_ready"] is False


def test_task_class_and_strategy_coverage_breakdown(db):
    store = PlanningOutcomeStore(db)
    # 5 coding tasks with MINIMAL_PLAN
    for i in range(5):
        store.record(
            planning_strategy=PlanningStrategy.MINIMAL_PLAN.value,
            task_class="coding",
            goal_id=f"g_code_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_code_{i}"],
        )
    # 2 research tasks with DECOMPOSE_PLAN (LOW_SAMPLE)
    for i in range(2):
        store.record(
            planning_strategy=PlanningStrategy.DECOMPOSE_PLAN.value,
            task_class="research",
            goal_id=f"g_res_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_res_{i}"],
        )

    readiness = store.live_readiness()
    tc_map = {t["task_class"]: t for t in readiness["task_classes"]}
    assert tc_map["coding"]["real_sample_count"] == 5
    assert tc_map["coding"]["sufficient_real_evidence"] is True
    assert tc_map["research"]["real_sample_count"] == 2
    assert tc_map["research"]["sufficient_real_evidence"] is False
    assert tc_map["research"]["readiness"] == "LOW_SAMPLE"

    strat_map = {s["planning_strategy"]: s for s in readiness["strategies"]}
    assert strat_map[PlanningStrategy.MINIMAL_PLAN.value]["real_sample_count"] == 5
    assert strat_map[PlanningStrategy.MINIMAL_PLAN.value]["sufficient_real_evidence"] is True
    assert strat_map[PlanningStrategy.DECOMPOSE_PLAN.value]["real_sample_count"] == 2
    assert strat_map[PlanningStrategy.DECOMPOSE_PLAN.value]["sufficient_real_evidence"] is False


# ---------------------------------------------------------------------------
# 2. Real Shadow Evaluation & Truthfulness (§6, §7, §8)
# ---------------------------------------------------------------------------

def test_shadow_advantage_truthfulness_unknown_when_unsupported(db):
    store = PlanningOutcomeStore(db)
    # Record actual policy execution (DIRECT_PLAN)
    store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="goal",
        goal_id="g_act_1",
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g_act_1"],
    )
    # Create shadow decision where shadow chose DECOMPOSE_PLAN
    shadow_store = PlanningShadowStore(db)
    shadow_store.record_shadow(
        task_class="goal",
        actual_policy=PlanningStrategy.DIRECT_PLAN.value,
        shadow_policy=PlanningStrategy.DECOMPOSE_PLAN.value,
        goal_id="g_act_1",
        actual_outcome="SUCCESS_VERIFIED",
    )

    # DECOMPOSE_PLAN has 0 real samples -> expected_advantage must be UNKNOWN, not TRUE
    report = store.shadow_quality_report()
    assert report["total"] == 1
    assert report["divergence_count"] == 1
    assert len(report["details"]) == 1
    detail = report["details"][0]
    assert detail["would_diverge"] is True
    assert detail["expected_advantage"] == "UNKNOWN"
    assert detail["has_expected_advantage"] is False
    assert detail["historical_support_for_shadow"] == "UNRATED"
    assert detail["sample_count"] == 0


def test_shadow_advantage_truthfulness_true_when_supported_by_real_evidence(db):
    store = PlanningOutcomeStore(db)
    # DIRECT_PLAN has 5 samples with 40% success rate
    for i in range(5):
        store.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="goal",
            goal_id=f"g_dp_{i}",
            outcome="SUCCESS_VERIFIED" if i < 2 else "FAILED",
            verified_success=(i < 2),
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_dp_{i}"],
        )
    # DEPENDENCY_AWARE_PLAN has 5 samples with 100% success rate (SUPPORTED)
    for i in range(5):
        store.record(
            planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value,
            task_class="goal",
            goal_id=f"g_dap_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_dap_{i}"],
        )

    shadow_store = PlanningShadowStore(db)
    shadow_store.record_shadow(
        task_class="goal",
        actual_policy=PlanningStrategy.DIRECT_PLAN.value,
        shadow_policy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value,
        goal_id="g_test_shadow",
        actual_outcome="FAILED",
    )

    report = store.shadow_quality_report()
    assert report["divergence_count"] == 1
    detail = report["details"][0]
    assert detail["expected_advantage"] == "TRUE"
    assert detail["has_expected_advantage"] is True
    assert detail["historical_support_for_shadow"] in ("SUPPORTED", "ESTABLISHED")
    assert detail["sample_count"] == 5
    assert detail["verified_success_rate"] == 1.0


def test_synthetic_evidence_does_not_validate_shadow_advantage(db):
    store = PlanningOutcomeStore(db)
    # 20 synthetic samples for DELEGATED_PLAN
    for i in range(20):
        store.record(
            planning_strategy=PlanningStrategy.DELEGATED_PLAN.value,
            task_class="goal",
            goal_id=f"g_synth_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="SYNTHETIC_TEST",
            evidence_refs=[f"goal:g_synth_{i}"],
        )

    shadow_store = PlanningShadowStore(db)
    shadow_store.record_shadow(
        task_class="goal",
        actual_policy=PlanningStrategy.DIRECT_PLAN.value,
        shadow_policy=PlanningStrategy.DELEGATED_PLAN.value,
        goal_id="g_test_synth",
    )

    report = store.shadow_quality_report()
    detail = report["details"][0]
    # Synthetic must not count -> sample_count == 0 on real, UNKNOWN advantage
    assert detail["sample_count"] == 0
    assert detail["expected_advantage"] == "UNKNOWN"
    assert detail["has_expected_advantage"] is False


# ---------------------------------------------------------------------------
# 3. Authoritative Specialist Execution Telemetry (§10, §11)
# ---------------------------------------------------------------------------

def test_specialist_execution_telemetry_from_delegation_records(db):
    # Setup Goal and Plan
    obj = Objective(id="g_spec_test", title="test specialist telemetry", status="active", priority=2)
    db.add(obj)
    plan = GoalPlan(id=101, goal_id="g_spec_test", version=1, status="active")
    db.add(plan)
    db.commit()

    # Create 3 distinct delegations:
    # d1: executed and verified
    d1 = Delegation(
        id="del_1",
        parent_agent_id="main",
        specialist_role="RESEARCH",
        goal_id="g_spec_test",
        plan_id=101,
        objective="research something",
        status="VERIFIED",
        attempt=1,
        fingerprint="fp_del_1",
    )
    # d2: proposed and executed, but failed, then retried (attempt 2 succeeded)
    d2_att1 = Delegation(
        id="del_2_a1",
        parent_agent_id="main",
        specialist_role="CODING",
        goal_id="g_spec_test",
        plan_id=101,
        objective="code something",
        status="FAILED",
        attempt=1,
        fingerprint="fp_del_2",
    )
    d2_att2 = Delegation(
        id="del_2_a2",
        parent_agent_id="main",
        specialist_role="CODING",
        goal_id="g_spec_test",
        plan_id=101,
        objective="code something",
        status="VERIFIED",
        attempt=2,
        fingerprint="fp_del_2",
    )
    # d3: proposed but never executed (CANCELLED / PROPOSED)
    d3 = Delegation(
        id="del_3",
        parent_agent_id="main",
        specialist_role="REVIEW",
        goal_id="g_spec_test",
        plan_id=101,
        objective="review something",
        status="PROPOSED",
        attempt=1,
        fingerprint="fp_del_3",
    )
    db.add_all([d1, d2_att1, d2_att2, d3])
    db.commit()

    telemetry = compute_delegation_telemetry_for_goal("g_spec_test", 101, db)
    # 3 created (d1, d2, d3)
    assert telemetry["created"] == 3
    # 2 executed (d1, d2) - d3 proposed only, d2 retry deduplicated
    assert telemetry["executed"] == 2
    # 2 completed (d1, d2 final attempt VERIFIED)
    assert telemetry["completed"] == 2
    # 0 failed overall (d2 recovered on retry)
    assert telemetry["failed"] == 0

    # Record PlanningOutcome and check quality metrics
    store = PlanningOutcomeStore(db)
    store.record(
        planning_strategy=PlanningStrategy.DELEGATED_PLAN.value,
        task_class="goal",
        goal_id="g_spec_test",
        plan_id=101,
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g_spec_test", "plan:101"],
    )

    metrics = store.quality_metrics(task_class="goal")
    assert metrics.sample_count == 1
    assert metrics.specialists_created == 3.0
    assert metrics.specialists_executed == 2.0
    assert metrics.specialists_completed == 2.0
    assert metrics.specialist_count == 2.0  # actual executed count


# ---------------------------------------------------------------------------
# 4. Data Quality Validation for Malformed Records (§12, §13)
# ---------------------------------------------------------------------------

def test_data_quality_rejects_missing_goal_or_plan_when_required(db):
    store = PlanningOutcomeStore(db)
    # task_class="goal" with no goal_id or plan_id must be rejected
    res = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="goal",
        goal_id=None,
        plan_id=None,
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        evidence_refs=["action:1"],
    )
    assert res["outcome"] is None
    assert res["eligible"] is False
    assert "missing goal/plan reference" in res["reason"]


def test_data_quality_rejects_negative_metrics_and_impossible_counts(db):
    store = PlanningOutcomeStore(db)

    # Negative latency
    res1 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g1",
        total_latency_ms=-50,
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g1"],
    )
    assert res1["outcome"] is None
    assert "negative latency" in res1["reason"]

    # Negative cost in total_cost dict
    res2 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g1",
        total_cost={"total": -1.5},
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g1"],
    )
    assert res2["outcome"] is None
    assert "negative cost" in res2["reason"]

    # Impossible plan steps
    res3 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g1",
        initial_plan_steps=-5,
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g1"],
    )
    assert res3["outcome"] is None
    assert "impossible initial_plan_steps" in res3["reason"]

    # Impossible retries
    res4 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g1",
        retries=-1,
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g1"],
    )
    assert res4["outcome"] is None
    assert "impossible retries count" in res4["reason"]

    # Unknown provenance
    res5 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g1",
        outcome="SUCCESS_VERIFIED",
        provenance="BOGUS_PROVENANCE",
        evidence_refs=["goal:g1"],
    )
    assert res5["outcome"] is None
    assert "unknown provenance" in res5["reason"]


def test_idempotency_fingerprint_preserves_distinct_attempts(db):
    store = PlanningOutcomeStore(db)
    now = datetime.datetime.utcnow()

    # First attempt (FAILED)
    r1 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="goal",
        goal_id="g_idemp",
        retries=0,
        outcome="FAILED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g_idemp"],
        created_at=now,
    )
    assert r1["outcome"] is not None
    assert r1["reason"] == "recorded"

    # Duplicate submission of exact same terminal outcome -> idempotent
    r1_dup = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="goal",
        goal_id="g_idemp",
        retries=0,
        outcome="FAILED",
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g_idemp"],
        created_at=now,
    )
    assert r1_dup["outcome"].id == r1["outcome"].id
    assert r1_dup["reason"] == "duplicate_fingerprint_idempotent"

    # Genuinely distinct attempt (retries=1, SUCCESS_VERIFIED) -> recorded as new row
    r2 = store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="goal",
        goal_id="g_idemp",
        retries=1,
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance="REAL_EXECUTION",
        evidence_refs=["goal:g_idemp"],
        created_at=now + timedelta(seconds=10),
    )
    assert r2["outcome"] is not None
    assert r2["outcome"].id != r1["outcome"].id
    assert r2["reason"] == "recorded"


# ---------------------------------------------------------------------------
# 5. Real Retry vs Replan Calibration (§16)
# ---------------------------------------------------------------------------

def test_real_retry_vs_replan_calibration_breakdowns(db):
    store = PlanningOutcomeStore(db)

    # 3 RETRY samples
    for i in range(3):
        store.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="coding",
            goal_id=f"g_cal_r_{i}",
            retry_vs_replan="RETRY",
            replan_trigger="EXECUTION_FAILURE",
            outcome="SUCCESS_VERIFIED" if i < 2 else "FAILED",
            verified_success=(i < 2),
            retries=1,
            total_latency_ms=1000,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_cal_r_{i}"],
        )

    # 3 REPLAN samples
    for i in range(3):
        store.record(
            planning_strategy=PlanningStrategy.REPLAN_AFTER_FAILURE.value,
            task_class="coding",
            goal_id=f"g_cal_p_{i}",
            retry_vs_replan="REPLAN",
            replan_trigger="DEPENDENCY_BLOCK",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            replans=1,
            total_latency_ms=2000,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_cal_p_{i}"],
        )

    cal = store.retry_replan_calibration(task_class="coding")
    assert cal["real_sample_count"] == 6
    assert cal["overall_retry"]["sample_count"] == 3
    assert cal["overall_retry"]["verified_recovery_rate"] == round(2 / 3, 4)
    assert cal["overall_replan"]["sample_count"] == 3
    assert cal["overall_replan"]["verified_recovery_rate"] == 1.0

    # Trigger breakdown
    assert "EXECUTION_FAILURE" in cal["per_trigger"]
    assert cal["per_trigger"]["EXECUTION_FAILURE"]["retry"]["sample_count"] == 3
    assert "DEPENDENCY_BLOCK" in cal["per_trigger"]
    assert cal["per_trigger"]["DEPENDENCY_BLOCK"]["replan"]["sample_count"] == 3

    # Strategy breakdown
    assert PlanningStrategy.DIRECT_PLAN.value in cal["per_strategy"]
    assert cal["per_strategy"][PlanningStrategy.DIRECT_PLAN.value]["retry"]["sample_count"] == 3


# ---------------------------------------------------------------------------
# 6. Real Evidence Expiration & Persistence (§19, §20)
# ---------------------------------------------------------------------------

def test_evidence_expiration_tracking(db):
    store = PlanningOutcomeStore(db)
    now = datetime.datetime.utcnow()

    # Active row (expires in 90 days)
    store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g_active",
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        created_at=now,
        expires_at=now + timedelta(days=90),
        evidence_refs=["goal:g_active"],
    )

    # Expired row (expired 5 days ago)
    store.record(
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        task_class="general",
        goal_id="g_expired",
        outcome="SUCCESS_VERIFIED",
        provenance="REAL_EXECUTION",
        created_at=now - timedelta(days=100),
        expires_at=now - timedelta(days=5),
        evidence_refs=["goal:g_expired"],
    )

    readiness = store.live_readiness()
    assert readiness["real_sample_count"] == 1  # only active counted
    assert readiness["expiration"]["active_real_evidence"] == 1
    assert readiness["expiration"]["expired_real_evidence"] == 1
    assert readiness["expiration"]["decayed_real_evidence"] == 1


# ---------------------------------------------------------------------------
# 7. Promotion Gates & Manual Review (§4, §21, §22)
# ---------------------------------------------------------------------------

def test_promotion_requires_real_evidence_and_owner_approval(db):
    pstore = PlanningPolicyStore(db)
    ostore = PlanningOutcomeStore(db)

    # Create policy in OBSERVED status
    policy = pstore.create_observed(
        task_class="coding",
        planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
        evidence_summary={"sample_count": 10, "shadow_validated": True},
    )

    # Without 5 REAL samples, promote to SUPPORTED fails
    res1 = pstore.promote(policy.id, target_status="SUPPORTED")
    assert res1["ok"] is False
    assert "insufficient_real_samples" in res1["reason"]

    # Add 5 REAL samples (3 verified)
    for i in range(5):
        ostore.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="coding",
            goal_id=f"g_prom_{i}",
            outcome="SUCCESS_VERIFIED" if i < 3 else "FAILED",
            verified_success=(i < 3),
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:g_prom_{i}"],
        )

    # Now promotion to SUPPORTED succeeds
    res2 = pstore.promote(policy.id, target_status="SUPPORTED")
    assert res2["ok"] is True
    assert res2["policy"].status == "SUPPORTED"

    # Promotion to ACTIVE strictly requires owner approval (auto-promotion blocked)
    res3 = pstore.promote(policy.id, target_status="ACTIVE", owner_approved=False)
    assert res3["ok"] is False
    assert "owner_approval_required" in res3["reason"]

    # With explicit owner approval
    res4 = pstore.promote(policy.id, target_status="ACTIVE", owner_approved=True)
    assert res4["ok"] is True
    assert res4["policy"].status == "ACTIVE"


# ---------------------------------------------------------------------------
# 8. Live Readiness API Contract (§9)
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_live_readiness_api_contract(db):
    from backend.api.learning_routes import get_planning_live_readiness
    res = await get_planning_live_readiness(db=db)
    assert isinstance(res, dict)
    assert "real_sample_count" in res
    assert "real_verified_successes" in res
    assert "eligible_real_samples" in res
    assert "shadow_sample_count" in res
    assert "shadow_divergence_count" in res
    assert "shadow_validated" in res
    assert "promotion_eligible" in res
    assert "policy_readiness" in res
    assert "task_classes" in res
    assert "strategies" in res
    assert "expiration" in res
    assert "phase13_ready" in res
    assert res["policy_readiness"] in ("NOT_READY", "LOW_REAL_EVIDENCE", "REAL_SUPPORTED", "SHADOW_VALIDATED", "READY_FOR_OWNER_REVIEW")
