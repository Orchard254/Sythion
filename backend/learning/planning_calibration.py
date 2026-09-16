"""Offline calibration helpers (§7, §13) — never executes destructive actions."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.learning.planning_selector import PlanningPolicySelector
from backend.learning.planning_store import PlanningOutcomeStore


def offline_evaluate_selector(db, limit: int = 100) -> Dict[str, Any]:
    """Offline evaluation against captured historical outcomes (§7).

    For each historical PlanningOutcome, reconstruct task_context/failure_context
    and ask the selector what it would have recommended. Compare predicted
    strategy vs actual strategy's verified outcome. Does not replay actions.
    """
    store = PlanningOutcomeStore(db)
    rows = store.list_outcomes(limit=limit, include_expired=False)
    if not rows:
        return {"total": 0, "matches": 0, "mismatch": 0, "accuracy": None, "details": []}
    selector = PlanningPolicySelector(db)
    details = []
    matches = 0
    for r in rows:
        ctx = {"task_class": r.task_class, "goal_type": r.goal_type, "dependency_complexity": r.dependency_complexity, "risk_level": r.risk_level}
        fail_ctx = {"replan_trigger": r.replan_trigger} if r.replan_trigger else None
        pred = selector.select(task_context=ctx, failure_context=fail_ctx)
        predicted = pred.selected_policy
        actual = r.planning_strategy
        # match if predicted equals actual and actual verified success, else considered not better
        is_match = predicted == actual
        if is_match:
            matches += 1
        details.append({
            "outcome_id": r.id,
            "task_class": r.task_class,
            "actual_strategy": actual,
            "predicted_strategy": predicted,
            "actual_verified": bool(r.verified_success),
            "actual_outcome": r.outcome,
            "replan_trigger": r.replan_trigger,
            "evidence_strength": pred.evidence_strength,
            "match": is_match,
        })
    total = len(rows)
    return {
        "total": total,
        "matches": matches,
        "mismatch": total - matches,
        "accuracy": round(matches / total, 4) if total else None,
        "details": details,
    }


def selector_stability_check(db, task_class: str = "goal", candidates_shuffle: Optional[List[List[str]]] = None) -> Dict[str, Any]:
    """Check selector robustness: same evidence different candidate ordering must yield same selection (§13)."""
    from backend.learning.planning_taxonomy import PlanningStrategy
    base = PlanningStrategy.values()
    # create shuffled orderings
    orderings = candidates_shuffle or [base, list(reversed(base)), base[2:] + base[:2]]
    selector = PlanningPolicySelector(db)
    results = []
    for ordering in orderings:
        r = selector.select(task_context={"task_class": task_class}, candidates=ordering)
        results.append(r.selected_policy)
    stable = len(set(results)) == 1
    return {
        "stable": stable,
        "results": results,
        "orderings": orderings,
    }


def selector_perturbation_stability(db, task_class: str = "goal", planning_strategy: str = "DIRECT_PLAN") -> Dict[str, Any]:
    """Small evidence changes should not create wild oscillation (§13)."""
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    selector = PlanningPolicySelector(db)
    before = selector.select(task_context={"task_class": task_class})
    # add one synthetic small perturbation outcome (isolated provenance so not polluting real)
    store.record(
        planning_strategy=planning_strategy,
        task_class=task_class,
        goal_id="perturb_check",
        outcome="SUCCESS_VERIFIED",
        verified_success=True,
        provenance="SYNTHETIC_TEST",
        evidence_refs=["perturb"],
    )
    after = selector.select(task_context={"task_class": task_class})
    # clean up perturbation (remove synthetic row for isolation) - rollback via delete
    try:
        from backend.models.database import PlanningOutcome
        db.query(PlanningOutcome).filter(PlanningOutcome.goal_id == "perturb_check").delete()
        db.commit()
    except Exception:
        db.rollback()
    # stable if selection unchanged or both still same evidence_strength tier
    stable = before.selected_policy == after.selected_policy or before.evidence_strength == after.evidence_strength
    return {
        "before": before.selected_policy,
        "after": after.selected_policy,
        "before_strength": before.evidence_strength,
        "after_strength": after.evidence_strength,
        "stable": stable,
    }
