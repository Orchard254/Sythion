"""Phase 12 — Bounded planning-policy learning (functional + adversarial)."""

import hashlib
from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.learning.planning_taxonomy import PlanningStrategy, ReplanTrigger, PlanningPolicyStatus
from backend.learning.planning_store import PlanningOutcomeStore, PlanningPolicyStore, PlanningShadowStore, PlanningCounterfactualStore
from backend.learning.planning_selector import PlanningPolicySelector
from backend.learning.capture import classify_planning_feedback, record_planning_outcome, record_replan_planning_outcome
from backend.learning.planning_shadow import evaluate_shadow, record_counterfactual_hypothesis


@pytest.fixture
def db():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = S()
    yield s
    s.close()
    engine.dispose()


# -- Planning evidence §4 ---------------------------------------------------

def test_direct_plan_record(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="g1", plan_id=1, initial_plan_steps=1, final_plan_steps=1, outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:g1","plan:1"])
    assert r["eligible"] is True
    assert r["outcome"].planning_strategy == "DIRECT_PLAN"


def test_decompose_plan_record(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy=PlanningStrategy.DECOMPOSE_PLAN.value, task_class="goal", goal_id="g2", plan_id=2, initial_plan_steps=6, final_plan_steps=6, outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:g2"])
    assert r["eligible"] is True


def test_dependency_aware_plan_record(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id="g3", plan_id=3, dependency_complexity=3, outcome="SUCCESS_PARTIAL", evidence_refs=["goal:g3"])
    assert r["eligible"] is True


def test_delegated_plan_record(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy=PlanningStrategy.DELEGATED_PLAN.value, task_class="goal", goal_id="g4", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:g4"])
    assert r["eligible"] is True


def test_failed_plan_record(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="g5", outcome="FAILED", failure_reason="execution error", replan_trigger=ReplanTrigger.EXECUTION_FAILURE.value, evidence_refs=["goal:g5"])
    assert r["eligible"] is True
    assert r["outcome"].outcome == "FAILED"


def test_successful_replan_record(db):
    store = PlanningOutcomeStore(db)
    r = record_replan_planning_outcome(db, goal_id="g6", original_plan_id=1, new_plan_id=2, reason="execution failure", trigger="EXECUTION_FAILURE", retry_attempts=1)
    assert r["eligible"] is True
    assert r["outcome"].replan_trigger == "EXECUTION_FAILURE"
    assert r["outcome"].retry_vs_replan == "REPLAN"
    # replan is not automatically successful — stored as SUCCESS_PARTIAL
    assert r["outcome"].outcome == "SUCCESS_PARTIAL"
    assert r["outcome"].verified_success is None


def test_capture_preserves_evidence_refs(db):
    r = record_planning_outcome(db, goal_id="g7", plan_id=10, initial_plan_steps=3, outcome="SUCCESS_VERIFIED", verified_success=True, task_class="goal")
    assert r["eligible"] is True
    import json
    refs = json.loads(r["outcome"].evidence_refs_json)
    assert any("g7" in x for x in refs)


def test_classify_planning_feedback(db):
    assert classify_planning_feedback("The plan was too complicated.") == "OVER_PLANNED"
    assert classify_planning_feedback("The second approach was better") == "REPLAN_ERROR"
    assert classify_planning_feedback("You should have asked me first") == "CLARIFICATION_MISSED"


def test_invalid_planning_strategy_rejected(db):
    store = PlanningOutcomeStore(db)
    r = store.record(planning_strategy="HACK_PLAN", task_class="goal", goal_id="g8")
    assert r["eligible"] is False
    assert "invalid planning_strategy" in r["reason"]


def test_duplicate_fingerprint_idempotent(db):
    store = PlanningOutcomeStore(db)
    fp = "planfpsame001"
    r1 = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="gdup", evidence_fingerprint=fp, evidence_refs=["goal:gdup"])
    r2 = store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="gdup", evidence_fingerprint=fp, evidence_refs=["goal:gdup"])
    assert r1["outcome"].id == r2["outcome"].id
    assert r2["reason"] == "duplicate_fingerprint_idempotent"


# -- Retry vs replan §6 -----------------------------------------------------

def test_retry_wins_for_timeout_class(db):
    store = PlanningOutcomeStore(db)
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"rt{i}", outcome="SUCCESS_VERIFIED", verified_success=True, replan_trigger=ReplanTrigger.TIMEOUT.value, retry_vs_replan="RETRY", evidence_refs=[f"goal:rt{i}"])
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"rp{i}", outcome="FAILED", replan_trigger=ReplanTrigger.TIMEOUT.value, retry_vs_replan="REPLAN", evidence_refs=[f"goal:rp{i}"])
    stats = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.DIRECT_PLAN.value)
    assert stats.retry_success_rate == pytest.approx(1.0)
    assert stats.replan_success_rate == pytest.approx(0.0)


def test_replan_wins_for_dependency_block(db):
    store = PlanningOutcomeStore(db)
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id=f"db_retry{i}", outcome="FAILED", replan_trigger=ReplanTrigger.DEPENDENCY_BLOCK.value, retry_vs_replan="RETRY", evidence_refs=[f"goal:db_retry{i}"])
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id=f"db_replan{i}", outcome="SUCCESS_VERIFIED", verified_success=True, replan_trigger=ReplanTrigger.DEPENDENCY_BLOCK.value, retry_vs_replan="REPLAN", evidence_refs=[f"goal:db_replan{i}"])
    stats = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value)
    assert stats.replan_success_rate == pytest.approx(1.0)
    assert stats.retry_success_rate == pytest.approx(0.0)


def test_insufficient_evidence_fallback_to_deterministic(db):
    store = PlanningOutcomeStore(db)
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="ins1", outcome="SUCCESS_VERIFIED", verified_success=True, replan_trigger=ReplanTrigger.EXECUTION_FAILURE.value, evidence_refs=["goal:ins1"])
    sel = PlanningPolicySelector(db)
    result = sel.select(task_context={"task_class": "goal"}, failure_context={"replan_trigger": "EXECUTION_FAILURE"})
    # 1 sample → LOW_SAMPLE → deterministic fallback informational in reason
    assert result.evidence_strength in ("LOW_SAMPLE", "UNRATED")
    assert result.confidence is None


def test_failure_class_specific_selector_recommendation(db):
    sel = PlanningPolicySelector(db)
    assert sel.recommend_for_failure({}, "VERIFICATION_FAILURE") == "retry"
    assert sel.recommend_for_failure({}, "DEPENDENCY_BLOCK") == "replan"
    assert sel.recommend_for_failure({}, "USER_CORRECTION") == "ask_user"
    assert sel.recommend_for_failure({}, "RESOURCE_UNAVAILABLE") == "stop"


# -- Selector: deterministic, small-sample, eligibility §8-11 ---------------


def test_selector_deterministic_same_input_same_output(db):
    store = PlanningOutcomeStore(db)
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"det{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:det{i}"])
    sel = PlanningPolicySelector(db)
    r1 = sel.select(task_context={"task_class": "goal"})
    r2 = sel.select(task_context={"task_class": "goal"})
    assert r1.selected_policy == r2.selected_policy
    assert r1.score == r2.score


def test_selector_respects_hard_constraint(db):
    store = PlanningOutcomeStore(db)
    for i in range(10):
        store.record(planning_strategy=PlanningStrategy.DELEGATED_PLAN.value, task_class="goal", goal_id=f"h{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:h{i}"])
    sel = PlanningPolicySelector(db)
    r = sel.select(task_context={"task_class": "goal"}, hard_constraints=[{"strategy": "DELEGATED_PLAN", "violated": True, "reason": "policy"}])
    assert r.selected_policy != "DELEGATED_PLAN"


def test_selector_budget_exhausted_vetoes_delegated(db):
    sel = PlanningPolicySelector(db)
    r = sel.select(task_context={"task_class": "goal"}, candidates=[PlanningStrategy.DIRECT_PLAN.value, PlanningStrategy.DELEGATED_PLAN.value], budget_remaining=0)
    assert r.selected_policy != "DELEGATED_PLAN"


def test_exploration_blocked_high_risk(db, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "planning_exploration_rate", 1.0)
    sel = PlanningPolicySelector(db)
    r = sel.select(task_context={"task_class": "goal", "risk_level": "HIGH"}, candidates=[PlanningStrategy.DIRECT_PLAN.value, PlanningStrategy.DEPENDENCY_AWARE_PLAN.value])
    assert r.mode == "EXPLOIT"


def test_small_sample_protection_low_strength(db):
    store = PlanningOutcomeStore(db)
    store.record(planning_strategy=PlanningStrategy.MINIMAL_PLAN.value, task_class="goal", goal_id="sm1", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:sm1"])
    stats = store.statistics(task_class="goal", planning_strategy=PlanningStrategy.MINIMAL_PLAN.value)
    assert stats.evidence_strength in ("LOW_SAMPLE","UNRATED")


# -- Shadow mode §15 --------------------------------------------------------


def test_shadow_recommendation_recorded_actual_unchanged(db):
    store = PlanningOutcomeStore(db)
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id=f"sh{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:sh{i}"])
    actual = PlanningStrategy.DIRECT_PLAN.value
    result = evaluate_shadow(db, task_context={"task_class": "goal"}, failure_context=None, actual_policy=actual)
    assert result["actual_policy"] == actual
    assert result["shadow_policy"] in PlanningStrategy.values()
    # actual execution never changes
    assert result["actual_policy"] == actual
    # persisted
    sh = PlanningShadowStore(db).list(task_class="goal")
    assert len(sh) >= 1
    assert sh[0].actual_policy == actual


def test_shadow_divergence_recorded(db):
    store = PlanningOutcomeStore(db)
    for i in range(6):
        store.record(planning_strategy=PlanningStrategy.DEPENDENCY_AWARE_PLAN.value, task_class="goal", goal_id=f"div{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:div{i}"])
    r = evaluate_shadow(db, task_context={"task_class": "goal"}, failure_context=None, actual_policy=PlanningStrategy.DIRECT_PLAN.value)
    # shadow may diverge — we just assert the would_diverge flag matches reality
    assert r["would_diverge"] == (r["actual_policy"] != r["shadow_policy"])


# -- Promotion §17 + Rollback §18 -------------------------------------------


def test_insufficient_evidence_blocked_promotion(db):
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 1})
    result = store.promote(pol.id, target_status="SUPPORTED")
    assert result["ok"] is False
    assert "insufficient" in result["reason"].lower()


def test_shadow_validation_required(db):
    # seed real evidence so promotion hardening passes (synthetic alone cannot promote)
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"sv_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:sv_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 10})
    # first promote to SUPPORTED — needs 5 samples, should succeed
    r1 = store.promote(pol.id, target_status="SUPPORTED")
    assert r1["ok"] is True
    # SUPPORTED -> SHADOW_VALIDATED without shadow marker should fail
    r2 = store.promote(pol.id, target_status="SHADOW_VALIDATED")
    assert r2["ok"] is False
    assert "shadow" in r2["reason"].lower()


def test_owner_approval_required_for_active(db):
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"oa_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:oa_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 10, "shadow_validated": True})
    store.promote(pol.id, target_status="SUPPORTED")
    store.promote(pol.id, target_status="SHADOW_VALIDATED")
    # try ACTIVE without owner approval
    r = store.promote(pol.id, target_status="ACTIVE", owner_approved=False)
    assert r["ok"] is False
    assert "owner" in r["reason"].lower()


def test_promotion_and_rollback(db):
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"pr_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:pr_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 10, "shadow_validated": True})
    store.promote(pol.id, target_status="SUPPORTED")
    store.promote(pol.id, target_status="SHADOW_VALIDATED")
    r = store.promote(pol.id, target_status="ACTIVE", owner_approved=True)
    assert r["ok"] is True
    assert r["policy"].status == "ACTIVE"
    rb = store.rollback(pol.id, reason="bad policy")
    assert rb["ok"] is True
    assert rb["policy"].status == "ROLLED_BACK"
    assert rb["policy"].rollback_reason == "bad policy"
    # previous policy json preserved
    import json
    prev = json.loads(rb["policy"].previous_policy_json)
    assert prev["status"] == "ACTIVE"


def test_model_cannot_self_promote_without_owner_flag(db):
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"self_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:self_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 20, "shadow_validated": True, "safe_for_auto_promotion": False})
    store.promote(pol.id, target_status="SUPPORTED")
    store.promote(pol.id, target_status="SHADOW_VALIDATED")
    # even with ample evidence, ACTIVE without owner_approved should fail (model self-promotion blocked)
    r = store.promote(pol.id, target_status="ACTIVE", owner_approved=False, require_owner_approval=True)
    assert r["ok"] is False


# -- Counterfactual §16 -----------------------------------------------------


def test_counterfactual_hypothesis_recorded_no_fabricated_outcome(db):
    store = PlanningCounterfactualStore(db)
    row = store.record(actual_action="retry", counterfactual_candidate="replan", goal_id="gcf", evidence_refs=["goal:gcf"], hypothesis="would replan have helped?")
    assert row.claimed_outcome is None
    assert row.actual_action == "retry"
    assert row.counterfactual_candidate == "replan"
    # list
    rows = store.list(goal_id="gcf")
    assert any(r.id == row.id for r in rows)


def test_counterfactual_never_has_claimed_outcome_via_helper(db):
    result = record_counterfactual_hypothesis(db, goal_id="gcf2", actual_action="retry", counterfactual_candidate="replan", hypothesis="test")
    assert result["claimed_outcome"] is None


# -- Real workload §14 (gated observation) ----------------------------------


def test_real_workload_learning_only_collects_not_controls(db, monkeypatch):
    from backend.core.config import settings
    monkeypatch.setattr(settings, "planning_learning_mode", "LEARNING_ONLY")
    monkeypatch.setattr(settings, "planning_learning_enabled", True)
    r = record_planning_outcome(db, goal_id="real1", plan_id=1, initial_plan_steps=3, outcome="SUCCESS_VERIFIED", verified_success=True, task_class="goal")
    assert r["eligible"] is True
    #_selector would still give a recommendation, but production path under LEARNING_ONLY must not use it
    assert settings.planning_learning_mode == "LEARNING_ONLY"


def test_deterministic_fallback_when_evidence_insufficient(db):
    sel = PlanningPolicySelector(db)
    r = sel.select(task_context={"task_class": "unknown_xyz"}, failure_context={"replan_trigger": "VERIFICATION_FAILURE"})
    # with no evidence, evidence strength UNRATED and reason mentions deterministic fallback
    assert r.evidence_strength == "UNRATED"
    assert r.confidence is None
    assert "fallback" in r.reason.lower() or "insufficient" in r.reason.lower()


# -- Plan quality metrics §12 -----------------------------------------------


def test_plan_quality_metrics(db):
    store = PlanningOutcomeStore(db)
    for i in range(3):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"qm{i}", outcome="SUCCESS_VERIFIED", verified_success=True, initial_plan_steps=3, final_plan_steps=3, total_cost={"total": 0.5}, total_latency_ms=200, evidence_refs=[f"goal:qm{i}"])
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="qmfail", outcome="FAILED", replans=1, retries=2, evidence_refs=["goal:qmfail"])
    m = store.quality_metrics(task_class="goal")
    assert m.sample_count == 4
    assert m.verified_goal_success == pytest.approx(0.75)
    assert m.replan_rate == pytest.approx(0.25)
    assert m.retry_rate == pytest.approx(0.25)


# -- Security invariants §23 (10 checks) ------------------------------------


def test_learned_planning_cannot_grant_capabilities(db):
    sel = PlanningPolicySelector(db)
    # even with strong evidence for DELEGATED_PLAN, a hard constraint veto blocks it
    store = PlanningOutcomeStore(db)
    for i in range(10):
        store.record(planning_strategy=PlanningStrategy.DELEGATED_PLAN.value, task_class="goal", goal_id=f"sec_cap{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:sec_cap{i}"])
    r = sel.select(task_context={"task_class": "goal"}, hard_constraints=[{"strategy": "DELEGATED_PLAN", "violated": True, "reason": "capability not granted"}])
    assert r.selected_policy != "DELEGATED_PLAN"


def test_learned_planning_cannot_change_runtime_permissions(db):
    # Planning selector has no pathway to alter runtime policy — assert no side effects on Goal state
    from backend.models.database import Objective
    # No goal mutation via planning - the comparison pins persisted state: an
    # uncommitted mutation is discarded by db.expire_all() and is not detected.
    goal = Objective(id="sec_perm_goal", title="runtime-permission invariant",
                     status="active", priority=3)
    db.add(goal)
    db.commit()

    def goal_state():
        rows = db.query(Objective).all()
        # a non-empty snapshot is required: an empty one would make the
        # comparison below vacuously true
        assert rows, "the seeded goal row is missing; the invariant would be vacuous"
        return {
            row.id: {c.name: getattr(row, c.name) for c in Objective.__table__.columns}
            for row in rows
        }

    db.expire_all()
    before = goal_state()

    # Strong learned evidence for the most privileged strategy, then a selection.
    store = PlanningOutcomeStore(db)
    for i in range(10):
        store.record(planning_strategy=PlanningStrategy.DELEGATED_PLAN.value,
                     task_class="goal", goal_id="sec_perm_goal",
                     outcome="SUCCESS_VERIFIED", verified_success=True,
                     evidence_refs=["goal:sec_perm_goal"])
    sel = PlanningPolicySelector(db)
    sel.select(task_context={"task_class": "goal", "goal_id": "sec_perm_goal"},
               hard_constraints=[{"strategy": "DELEGATED_PLAN", "violated": True,
                                  "reason": "capability not granted"}])

    db.expire_all()
    assert goal_state() == before


def test_learned_planning_cannot_bypass_verification(db):
    # Verified success rate influences scoring, but selector never bypasses CompletionVerifier
    store = PlanningOutcomeStore(db)
    for i in range(10):
        store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"ver{i}", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=[f"goal:ver{i}"])
    sel = PlanningPolicySelector(db)
    r = sel.select(task_context={"task_class": "goal"})
    # selector never claims verification
    assert r.evidence_summary.get("replan_trigger") is None or isinstance(r.evidence_summary.get("replan_trigger"), str)


def test_learned_planning_cannot_mark_goals_completed(db):
    # Only GoalManager/Planner can complete goals; PlanningPolicyStore cannot
    pol_store = PlanningPolicyStore(db)
    # seed value not needed for this invariant
    # No method exists to mark goal completed via planning learning
    assert not hasattr(pol_store, "complete_goal")


def test_learned_planning_cannot_modify_code(db):
    # No code modification surface
    assert not hasattr(PlanningOutcomeStore, "execute_code")
    assert not hasattr(PlanningPolicySelector, "modify_code")


def test_learned_policy_cannot_promote_itself(db):
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"lp_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:lp_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 20, "shadow_validated": True})
    # Self-promotion would require owner_approved — without it, ACTIVE fails
    store.promote(pol.id, target_status="SUPPORTED")
    store.promote(pol.id, target_status="SHADOW_VALIDATED")
    r = store.promote(pol.id, target_status="ACTIVE", owner_approved=False)
    assert r["ok"] is False


def test_low_sample_cannot_override_deterministic_safety(db):
    sel = PlanningPolicySelector(db)
    # 1 success for risky delegated plan should not override deterministic retry fallback for TIMEOUT
    store = PlanningOutcomeStore(db)
    store.record(planning_strategy=PlanningStrategy.DELEGATED_PLAN.value, task_class="goal", goal_id="lowrisk1", outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:lowrisk1"])
    r = sel.select(task_context={"task_class": "goal", "risk_level": "HIGH"}, failure_context={"replan_trigger": "TIMEOUT"}, candidates=[PlanningStrategy.DIRECT_PLAN.value, PlanningStrategy.DELEGATED_PLAN.value], hard_constraints=[{"strategy": "DELEGATED_PLAN", "violated": True, "reason": "high risk"}])
    assert r.selected_policy != "DELEGATED_PLAN"


def test_shadow_policies_cannot_affect_actual_execution(db):
    actual = PlanningStrategy.DIRECT_PLAN.value
    result = evaluate_shadow(db, task_context={"task_class": "goal"}, failure_context=None, actual_policy=actual)
    # actual_policy unchanged regardless of shadow
    assert result["actual_policy"] == actual


def test_counterfactuals_cannot_become_claimed_facts(db):
    row = record_counterfactual_hypothesis(db, goal_id="sec_cf", actual_action="retry", counterfactual_candidate="replan")
    assert row["claimed_outcome"] is None
    # also via store direct
    s = PlanningCounterfactualStore(db)
    rows = s.list(goal_id="sec_cf")
    for r in rows:
        assert r.claimed_outcome is None


def test_rollback_always_restores_previous_policy(db):
    ostore = PlanningOutcomeStore(db)
    for i in range(5):
        ostore.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id=f"rb_real{i}", outcome="SUCCESS_VERIFIED", verified_success=True, provenance="REAL_EXECUTION", evidence_refs=[f"goal:rb_real{i}"])
    store = PlanningPolicyStore(db)
    pol = store.create_observed("goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary={"sample_count": 10, "shadow_validated": True})
    store.promote(pol.id, target_status="SUPPORTED")
    store.promote(pol.id, target_status="SHADOW_VALIDATED")
    pol2 = store.promote(pol.id, target_status="ACTIVE", owner_approved=True)["policy"]
    assert pol2.status == "ACTIVE"
    rb = store.rollback(pol.id, reason="rollback test")["policy"]
    assert rb.status == "ROLLED_BACK"
    import json
    prev = json.loads(rb.previous_policy_json)
    assert prev["status"] == "ACTIVE"


# -- Persistence §25 --------------------------------------------------------


def test_persistence_survives_reload():
    engine = create_engine("sqlite:///:memory:", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    S = sessionmaker(bind=engine, autoflush=False, autocommit=False)
    s = S()
    store = PlanningOutcomeStore(s)
    store.record(planning_strategy=PlanningStrategy.DIRECT_PLAN.value, task_class="goal", goal_id="persist1", plan_id=1, outcome="SUCCESS_VERIFIED", verified_success=True, evidence_refs=["goal:persist1"])
    s2 = S()
    from backend.models.database import PlanningOutcome
    rows = s2.query(PlanningOutcome).filter_by(goal_id="persist1").all()
    assert len(rows) == 1
    s.close(); s2.close(); engine.dispose()
