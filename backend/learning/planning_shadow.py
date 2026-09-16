"""Phase 12 — Shadow evaluation helpers (§15-16)."""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.learning.planning_selector import PlanningPolicySelector
from backend.learning.planning_store import PlanningShadowStore, PlanningCounterfactualStore


def evaluate_shadow(
    db,
    task_context: Dict[str, Any],
    failure_context: Optional[Dict[str, Any]],
    actual_policy: str,
) -> Dict[str, Any]:
    """Run shadow policy evaluation without affecting execution (§15).

    Returns {actual_policy, shadow_policy, would_diverge, recorded}.
    actual_policy is what deterministic fallback chose; shadow_policy is what
    learned PlanningPolicySelector would recommend given historical evidence.
    Neither alters the task.
    """
    selector = PlanningPolicySelector(db)
    learned = selector.select(
        task_context=task_context or {},
        failure_context=failure_context,
    )
    shadow_policy = learned.selected_policy
    would_diverge = str(actual_policy).upper() != str(shadow_policy).upper()
    store = PlanningShadowStore(db)
    row = store.record_shadow(
        task_class=(task_context or {}).get("task_class") or "general",
        actual_policy=str(actual_policy),
        shadow_policy=str(shadow_policy),
        goal_id=(task_context or {}).get("goal_id"),
        failure_context=str(failure_context) if failure_context else None,
        evidence_refs=[f"actual:{actual_policy}", f"shadow:{shadow_policy}"],
    )
    return {
        "actual_policy": str(actual_policy).upper(),
        "shadow_policy": str(shadow_policy).upper(),
        "would_diverge": would_diverge,
        "evidence_strength": learned.evidence_strength,
        "confidence": learned.confidence,
        "shadow_row_id": row.id,
    }


def record_counterfactual_hypothesis(
    db,
    *,
    goal_id: Optional[str],
    actual_action: str,
    counterfactual_candidate: str,
    evidence_refs: Optional[List[str]] = None,
    hypothesis: Optional[str] = None,
) -> Dict[str, Any]:
    """Record a counterfactual as hypothesis, never a claimed fact (§16)."""
    store = PlanningCounterfactualStore(db)
    row = store.record(
        actual_action=str(actual_action),
        counterfactual_candidate=str(counterfactual_candidate),
        goal_id=goal_id,
        evidence_refs=evidence_refs,
        hypothesis=hypothesis or f"actual {actual_action} vs alternative {counterfactual_candidate}",
    )
    return {
        "id": row.id,
        "actual_action": row.actual_action,
        "counterfactual_candidate": row.counterfactual_candidate,
        "claimed_outcome": row.claimed_outcome,  # always None
        "hypothesis": row.hypothesis,
    }
