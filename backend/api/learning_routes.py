"""Phase 11 learning introspection routes (section 33)."""

from __future__ import annotations

import json
import uuid
from typing import Optional

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.learning.store import StrategyOutcomeStore
from backend.learning.selector import StrategySelector

router = APIRouter(prefix="/learning", tags=["learning"])


@router.get("/strategies")
async def list_strategies(
    task_class: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """Evidence per strategy — never exposes secrets or CoT."""
    store = StrategyOutcomeStore(db)
    all_stats = store.statistics(task_class=task_class)
    # all_stats is dict when no strategy_type filter
    if isinstance(all_stats, dict):
        out = []
        for stype, stats in all_stats.items():
            out.append({
                "strategy_type": stype,
                "task_class": stats.task_class,
                "sample_count": stats.sample_count,
                "success_count": stats.success_count,
                "verified_success_count": stats.verified_success_count,
                "failure_count": stats.failure_count,
                "success_rate": stats.success_rate,
                "verified_success_rate": stats.verified_success_rate,
                "failure_rate": stats.failure_rate,
                "verification_failure_rate": stats.verification_failure_rate,
                "user_correction_rate": stats.user_correction_rate,
                "mean_latency_ms": stats.mean_latency_ms,
                "mean_cost": stats.mean_cost,
                "evidence_strength": stats.evidence_strength,
                "recent_count": stats.recent_count,
                "decayed_count": stats.decayed_count,
            })
        return {"strategies": out}
    return {"strategies": []}


@router.get("/strategies/{strategy_type}")
async def get_strategy(
    strategy_type: str,
    task_class: Optional[str] = None,
    db: Session = Depends(get_db),
):
    from backend.learning.taxonomy import StrategyType
    st = strategy_type.strip().upper()
    if st not in StrategyType.values():
        raise HTTPException(status_code=400, detail=f"unknown strategy_type {strategy_type}")
    store = StrategyOutcomeStore(db)
    stats = store.statistics(task_class=task_class, strategy_type=st)
    selector = StrategySelector(db, store=store)
    provenance = selector.provenance_for(st, task_class=task_class or "general")
    return {
        "strategy_type": st,
        "task_class": task_class or "general",
        "statistics": {
            "sample_count": stats.sample_count,
            "success_count": stats.success_count,
            "verified_success_count": stats.verified_success_count,
            "failure_count": stats.failure_count,
            "success_rate": stats.success_rate,
            "verified_success_rate": stats.verified_success_rate,
            "failure_rate": stats.failure_rate,
            "verification_failure_rate": stats.verification_failure_rate,
            "user_correction_rate": stats.user_correction_rate,
            "mean_latency_ms": stats.mean_latency_ms,
            "mean_cost": stats.mean_cost,
            "total_cost": stats.total_cost,
            "evidence_strength": stats.evidence_strength,
            "recent_count": stats.recent_count,
            "decayed_count": stats.decayed_count,
            "last_updated": provenance["recent_outcomes"][0]["created_at"] if provenance["recent_outcomes"] else None,
        },
        "provenance": provenance,
    }


@router.post("/outcomes")
async def record_outcome(payload: dict, db: Session = Depends(get_db)):
    """Direct ingestion endpoint for tests / internal callers.

    Rejects raw natural language as strategy — only controlled taxonomy.
    """
    from backend.learning.taxonomy import StrategyType, OutcomeClass
    strategy = str(payload.get("strategy_type") or payload.get("strategy") or "").strip().upper()
    if strategy not in StrategyType.values():
        raise HTTPException(status_code=400, detail=f"unknown strategy_type {strategy}")
    outcome = str(payload.get("outcome") or "").strip().upper()
    if outcome not in OutcomeClass.values():
        raise HTTPException(status_code=400, detail=f"unknown outcome {outcome}")
    task_class = str(payload.get("task_class") or "general").strip().lower()
    if len(task_class) > 100:
        raise HTTPException(status_code=400, detail="task_class too long")
    # Reject prompt injection via strategy fields: must be exactly taxonomy value
    if strategy != strategy.upper() or " " in strategy.lower():
        raise HTTPException(status_code=400, detail="strategy must be taxonomy value")

    store = StrategyOutcomeStore(db)
    result = store.record(
        task_class=task_class,
        strategy_type=strategy,
        outcome=outcome,
        role=(payload.get("role") or None),
        model_id=payload.get("model_id"),
        verification=payload.get("verification"),
        user_feedback=payload.get("user_feedback"),
        cost=payload.get("cost"),
        latency_ms=payload.get("latency_ms"),
        attempts=int(payload.get("attempts") or 1),
        evidence_refs=payload.get("evidence_refs"),
        task_id=payload.get("task_id"),
        goal_id=payload.get("goal_id"),
        evidence_fingerprint=payload.get("evidence_fingerprint"),
    )
    if not result.get("eligible") and result.get("outcome") is None:
        raise HTTPException(status_code=422, detail=result.get("reason"))
    row = result["outcome"]
    return {
        "id": row.id if row else None,
        "eligible": result["eligible"],
        "reason": result["reason"],
        "outcome": row.outcome if row else None,
    }


@router.get("/experiments/{exp_id}")
async def get_experiment(exp_id: str, db: Session = Depends(get_db)):
    from backend.learning.experiments import ExperimentService
    svc = ExperimentService(db)
    exp = svc.get(exp_id)
    if exp is None:
        raise HTTPException(status_code=404, detail="experiment not found")
    return {
        "id": exp.id,
        "strategy_a": exp.strategy_a,
        "strategy_b": exp.strategy_b,
        "task_class": exp.task_class,
        "sample_limit": exp.sample_limit,
        "risk_limit": exp.risk_limit,
        "status": exp.status,
        "outcome": json.loads(exp.outcome_json) if exp.outcome_json else None,
    }


# ---------------------------------------------------------------------------
# Phase 12 — Planning learning introspection (§8-18)
# ---------------------------------------------------------------------------

@router.get("/planning/strategies")
async def list_planning_strategies(task_class: Optional[str] = None, include_synthetic: bool = True, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    all_stats = store.statistics(task_class=task_class, include_synthetic=include_synthetic)
    out = []
    for stype, stats in all_stats.items():
        out.append({
            "planning_strategy": stype,
            "task_class": stats.task_class,
            "sample_count": stats.sample_count,
            "real_sample_count": stats.real_sample_count,
            "synthetic_sample_count": stats.synthetic_sample_count,
            "real_verified_success_count": stats.real_verified_success_count,
            "real_success_rate": stats.real_success_rate,
            "success_count": stats.success_count,
            "failure_count": stats.failure_count,
            "success_rate": stats.success_rate,
            "verified_success_rate": stats.verified_success_rate,
            "failure_rate": stats.failure_rate,
            "mean_latency_ms": stats.mean_latency_ms,
            "mean_cost": stats.mean_cost,
            "evidence_strength": stats.evidence_strength,
            "recent_count": stats.recent_count,
            "decayed_count": stats.decayed_count,
            "retry_success_rate": stats.retry_success_rate,
            "replan_success_rate": stats.replan_success_rate,
        })
    return {"strategies": out}


@router.get("/planning/strategies/{planning_strategy}")
async def get_planning_strategy(planning_strategy: str, task_class: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.learning.planning_taxonomy import PlanningStrategy
    from backend.learning.planning_store import PlanningOutcomeStore
    from backend.learning.planning_selector import PlanningPolicySelector
    st = planning_strategy.strip().upper()
    if st not in PlanningStrategy.values():
        raise HTTPException(status_code=400, detail=f"unknown planning_strategy {planning_strategy}")
    store = PlanningOutcomeStore(db)
    stats = store.statistics(task_class=task_class, planning_strategy=st)
    selector = PlanningPolicySelector(db, store=store)
    prov = selector.provenance_for(st, task_class=task_class or "general")
    return {
        "planning_strategy": st,
        "task_class": task_class or "general",
        "statistics": {
            "sample_count": stats.sample_count,
            "success_count": stats.success_count,
            "failure_count": stats.failure_count,
            "success_rate": stats.success_rate,
            "verified_success_rate": stats.verified_success_rate,
            "failure_rate": stats.failure_rate,
            "mean_latency_ms": stats.mean_latency_ms,
            "mean_cost": stats.mean_cost,
            "total_cost": stats.total_cost,
            "evidence_strength": stats.evidence_strength,
            "recent_count": stats.recent_count,
            "decayed_count": stats.decayed_count,
        },
        "provenance": prov,
    }


@router.post("/planning/outcomes")
async def record_planning_outcome_route(payload: dict, db: Session = Depends(get_db)):
    """Controlled ingestion for planning outcomes — taxonomy enforced (§4, §12.5 provenance)."""
    from backend.learning.planning_taxonomy import PlanningStrategy, ReplanTrigger, PlanningProvenance
    strat = str(payload.get("planning_strategy") or payload.get("strategy") or "").strip().upper()
    if strat not in PlanningStrategy.values():
        raise HTTPException(status_code=400, detail=f"unknown planning_strategy {strat}")
    outcome = str(payload.get("outcome") or "SUCCESS_PARTIAL").strip().upper()
    if outcome not in ("SUCCESS_VERIFIED","SUCCESS_PARTIAL","FAILED","FAILED_VERIFICATION","CANCELLED","TIMED_OUT","USER_CORRECTED","USER_REJECTED","UNAVAILABLE"):
        raise HTTPException(status_code=400, detail=f"unknown outcome {outcome}")
    trig = payload.get("replan_trigger")
    if trig and str(trig).strip().upper() not in ReplanTrigger.values():
        raise HTTPException(status_code=400, detail=f"unknown replan_trigger {trig}")
    task_class = str(payload.get("task_class") or "general").strip().lower()
    if len(task_class) > 100:
        raise HTTPException(status_code=400, detail="task_class too long")
    prov = str(payload.get("provenance") or "REAL_EXECUTION").strip().upper()
    if prov not in PlanningProvenance.values():
        raise HTTPException(status_code=400, detail=f"unknown provenance {prov}")
    store = PlanningOutcomeStore(db)
    result = store.record(
        planning_strategy=strat,
        task_class=task_class,
        goal_id=payload.get("goal_id"),
        plan_id=payload.get("plan_id"),
        initial_plan_steps=payload.get("initial_plan_steps"),
        final_plan_steps=payload.get("final_plan_steps"),
        retries=int(payload.get("retries") or 0),
        replans=int(payload.get("replans") or 0),
        verified_success=payload.get("verified_success"),
        outcome=outcome,
        total_cost=payload.get("total_cost"),
        total_latency_ms=payload.get("total_latency_ms"),
        user_feedback=payload.get("user_feedback"),
        evidence_refs=payload.get("evidence_refs"),
        evidence_fingerprint=payload.get("evidence_fingerprint"),
        original_plan_id=payload.get("original_plan_id"),
        new_plan_id=payload.get("new_plan_id"),
        failure_reason=payload.get("failure_reason"),
        replan_trigger=payload.get("replan_trigger"),
        retry_vs_replan=payload.get("retry_vs_replan"),
        dependency_complexity=payload.get("dependency_complexity"),
        risk_level=payload.get("risk_level"),
        goal_type=payload.get("goal_type"),
        provenance=prov,
    )
    if not result.get("eligible") and result.get("outcome") is None:
        raise HTTPException(status_code=422, detail=result.get("reason"))
    row = result["outcome"]
    return {
        "id": row.id if row else None,
        "eligible": result["eligible"],
        "reason": result["reason"],
        "outcome": row.outcome if row else None,
        "provenance": prov,
    }


@router.get("/planning/metrics")
async def get_planning_metrics(task_class: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    m = store.quality_metrics(task_class=task_class)
    return {
        "task_class": m.task_class,
        "sample_count": m.sample_count,
        "verified_goal_success": m.verified_goal_success,
        "verified_step_success": m.verified_step_success,
        "average_steps": m.average_steps,
        "average_plan_length": m.average_plan_length,
        "dependency_depth": m.dependency_depth,
        "critical_path": m.critical_path,
        "replan_count": m.replan_count,
        "retry_count": m.retry_count,
        "replan_rate": m.replan_rate,
        "retry_rate": m.retry_rate,
        "specialist_count": m.specialist_count,
        "model_calls": m.model_calls,
        "runtime_actions": m.runtime_actions,
        "average_total_cost": m.average_total_cost,
        "average_total_latency": m.average_total_latency,
        "latency": m.latency,
        "cost": m.cost,
        "user_correction_rate": m.user_correction_rate,
    }


@router.get("/planning/coverage")
async def get_planning_coverage(db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    return store.coverage_report()


@router.get("/planning/live-readiness")
async def get_planning_live_readiness(db: Session = Depends(get_db)):
    """Deterministic live evidence readiness dashboard (§3, §9, §22)."""
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    return store.live_readiness()


@router.get("/planning/shadow/quality")
async def get_shadow_quality(db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    return store.shadow_quality_report()


@router.get("/planning/calibration/retry-replan")
async def get_retry_replan_calibration(task_class: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore
    store = PlanningOutcomeStore(db)
    return store.retry_replan_calibration(task_class=task_class)


@router.get("/planning/calibration/offline")
async def get_offline_calibration(limit: int = 100, db: Session = Depends(get_db)):
    from backend.learning.planning_calibration import offline_evaluate_selector
    return offline_evaluate_selector(db, limit=min(200, int(limit or 100)))


@router.get("/planning/calibration/selector-stability")
async def get_selector_stability(task_class: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.learning.planning_calibration import selector_stability_check, selector_perturbation_stability
    return {
        "ordering_stability": selector_stability_check(db, task_class=task_class or "goal"),
        "perturbation_stability": selector_perturbation_stability(db, task_class=task_class or "goal"),
    }


@router.get("/planning/policies/{policy_id}/readiness")
async def get_policy_readiness(policy_id: str, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningOutcomeStore, PlanningPolicyStore
    pstore = PlanningPolicyStore(db)
    pol = pstore.get(policy_id)
    if pol is None:
        raise HTTPException(status_code=404, detail="policy not found")
    ostore = PlanningOutcomeStore(db)
    return ostore.promotion_readiness(pol)


@router.get("/planning/dependency/{plan_id}")
async def get_plan_dependency_metrics(plan_id: int, db: Session = Depends(get_db)):
    from backend.learning.planning_store import _compute_dependency_metrics_for_plan
    return _compute_dependency_metrics_for_plan(plan_id, db)


@router.post("/planning/shadow")
async def record_shadow_route(payload: dict, db: Session = Depends(get_db)):
    from backend.learning.planning_shadow import evaluate_shadow
    task_context = payload.get("task_context") or {"task_class": payload.get("task_class") or "general"}
    failure_context = payload.get("failure_context")
    actual_policy = str(payload.get("actual_policy") or "DIRECT_PLAN")
    result = evaluate_shadow(db, task_context=task_context, failure_context=failure_context, actual_policy=actual_policy)
    return result


@router.get("/planning/shadow")
async def list_shadow(task_class: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningShadowStore
    store = PlanningShadowStore(db)
    rows = store.list(task_class=task_class, limit=min(100, int(limit or 50)))
    import json as _json
    return {"shadow_decisions": [
        {
            "id": r.id,
            "goal_id": r.goal_id,
            "task_class": r.task_class,
            "actual_policy": r.actual_policy,
            "shadow_policy": r.shadow_policy,
            "would_diverge": bool(r.would_diverge),
            "created_at": r.created_at.isoformat() if r.created_at else None,
        } for r in rows
    ]}


@router.post("/planning/counterfactual")
async def record_counterfactual_route(payload: dict, db: Session = Depends(get_db)):
    from backend.learning.planning_shadow import record_counterfactual_hypothesis
    actual = str(payload.get("actual_action") or payload.get("actual_policy") or "retry")
    candidate = str(payload.get("counterfactual_candidate") or payload.get("alternative") or "replan")
    # Security: never accept claimed_outcome from caller — hypotheses have no outcome
    if payload.get("claimed_outcome") is not None:
        raise HTTPException(status_code=400, detail="claimed_outcome not allowed — counterfactuals are hypotheses only")
    row = record_counterfactual_hypothesis(
        db,
        goal_id=payload.get("goal_id"),
        actual_action=actual,
        counterfactual_candidate=candidate,
        evidence_refs=payload.get("evidence_refs"),
        hypothesis=payload.get("hypothesis"),
    )
    return row


@router.get("/planning/counterfactuals")
async def list_counterfactuals(goal_id: Optional[str] = None, limit: int = 50, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningCounterfactualStore
    store = PlanningCounterfactualStore(db)
    rows = store.list(goal_id=goal_id, limit=min(100, int(limit or 50)))
    return {"counterfactuals": [
        {"id": r.id, "goal_id": r.goal_id, "actual_action": r.actual_action, "counterfactual_candidate": r.counterfactual_candidate, "claimed_outcome": r.claimed_outcome, "hypothesis": r.hypothesis}
        for r in rows
    ]}


@router.get("/planning/policies")
async def list_planning_policies(task_class: Optional[str] = None, status: Optional[str] = None, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningPolicyStore
    store = PlanningPolicyStore(db)
    rows = store.list_policies(task_class=task_class, status=status)
    import json as _json
    return {"policies": [
        {"id": r.id, "policy_key": r.policy_key, "planning_strategy": r.planning_strategy, "task_class": r.task_class, "status": r.status, "policy_version": r.policy_version, "promoted_at": r.promoted_at.isoformat() if r.promoted_at else None}
        for r in rows
    ]}


@router.get("/planning/policies/{policy_id}")
async def get_planning_policy(policy_id: str, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningPolicyStore
    store = PlanningPolicyStore(db)
    pol = store.get(policy_id)
    if pol is None:
        raise HTTPException(status_code=404, detail="policy not found")
    import json as _json
    return {
        "id": pol.id,
        "policy_key": pol.policy_key,
        "planning_strategy": pol.planning_strategy,
        "task_class": pol.task_class,
        "status": pol.status,
        "policy_version": pol.policy_version,
        "evidence_summary": _json.loads(pol.evidence_summary_json) if pol.evidence_summary_json else None,
        "promoted_at": pol.promoted_at.isoformat() if pol.promoted_at else None,
        "rollback_reason": pol.rollback_reason,
    }


@router.post("/planning/policies/{policy_id}/promote")
async def promote_planning_policy(policy_id: str, payload: dict, db: Session = Depends(get_db)):
    """Promote a planning policy through the gate (§17).

    ACTIVE requires a verified durable owner-approval receipt (``approval_receipt_id``)
    or the explicit safe_for_auto_promotion + config opt-in. A body-only
    ``owner_approved`` flag is NOT authority and never grants promotion.
    """
    from backend.learning.planning_store import PlanningPolicyStore
    store = PlanningPolicyStore(db)
    target = str(payload.get("target_status") or payload.get("status") or "").strip().upper()
    # The receipt is a reference to a durable, server-issued owner confirmation.
    # We never read an approval boolean from the request body.
    receipt_id = (
        payload.get("approval_receipt_id")
        or payload.get("owner_approval_receipt_id")
        or payload.get("receipt_id")
    )
    receipt_id = receipt_id.strip() if isinstance(receipt_id, str) else None
    result = store.promote(
        policy_id, target_status=target, owner_approval_receipt_id=receipt_id or None
    )
    if not result.get("ok"):
        reason = result.get("reason") or "promotion rejected"
        if target == "ACTIVE" and not receipt_id and payload.get("owner_approved"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "owner_approved in the request body is not an approval authority: "
                    "ACTIVE promotion requires a verified durable owner-approval "
                    "receipt (approval_receipt_id) or the safe-auto promotion opt-in"
                ),
            )
        if reason in ("owner_approval_required", "owner_approval_receipt_invalid"):
            raise HTTPException(
                status_code=409,
                detail=(
                    "ACTIVE promotion requires a verified durable owner-approval "
                    f"receipt (approval_receipt_id); {reason}"
                ),
            )
        raise HTTPException(status_code=422, detail=reason)
    pol = result["policy"]
    return {"id": pol.id, "status": pol.status, "policy_version": pol.policy_version}


@router.post("/planning/policies/{policy_id}/approve")
async def approve_planning_policy(
    policy_id: str,
    request: Request,
    payload: Optional[dict] = None,
    db: Session = Depends(get_db),
):
    """Trusted-owner approval that makes ACTIVE promotion reachable (§17).

    This is the ONLY HTTP path that issues a planning-policy owner-approval
    receipt. It is reachable solely through the existing loopback control-token
    owner boundary (``LocalControlBoundaryMiddleware`` guards every ``/api/*``
    path) and explicitly refuses device-channel credentials, so the single
    explicit POST is a genuine, separate owner action. The receipt is
    server-issued and SHA-256 bound to the exact policy state, then consumed by
    the same fail-closed promotion gate. No body boolean is ever treated as
    approval authority, and the safe-auto opt-in remains default OFF.
    """
    # Owner approval is a UI/control-boundary action, never a device channel.
    if request.headers.get("X-Sythion-Device-Id") or request.headers.get("X-Sythion-Device-Token"):
        raise HTTPException(
            status_code=403,
            detail="Owner approval cannot be submitted through a device channel",
        )

    from backend.learning.planning_store import (
        PlanningPolicyApprovalReceipts,
        PlanningPolicyStore,
    )

    data = payload if isinstance(payload, dict) else {}
    # ``decision_id`` is an audit/idempotency identifier, NOT approval authority.
    # A fresh one is minted when the trusted caller does not supply one.
    decision_id = data.get("decision_id")
    if decision_id is not None and (not isinstance(decision_id, str) or not decision_id.strip()):
        raise HTTPException(status_code=400, detail="decision_id must be a non-empty string")
    decision_id = decision_id.strip() if isinstance(decision_id, str) else None

    store = PlanningPolicyStore(db)
    pol = store.get(policy_id)
    if pol is None:
        raise HTTPException(status_code=404, detail="policy not found")

    # Issue the durable, state-bound owner approval here, at the trusted boundary.
    # Promotion never issues its own receipt, so a promoting request cannot
    # approve itself; only this explicit owner action can.
    receipts = PlanningPolicyApprovalReceipts(db)
    try:
        receipt_id = receipts.issue(
            pol, "ACTIVE", decision_id=decision_id or str(uuid.uuid4())
        )
    except ValueError as exc:
        db.rollback()
        raise HTTPException(
            status_code=409,
            detail=f"owner approval receipt could not be issued: {exc}",
        )

    # Consume the receipt through the same fail-closed promotion gate used by
    # /promote. On failure, discard the uncommitted receipt so a later valid
    # approval is never blocked by a stale flushed row.
    result = store.promote(
        policy_id, target_status="ACTIVE", owner_approval_receipt_id=receipt_id
    )
    if not result.get("ok"):
        db.rollback()
        reason = result.get("reason") or "promotion rejected"
        if reason in ("owner_approval_required", "owner_approval_receipt_invalid"):
            raise HTTPException(
                status_code=409,
                detail=f"ACTIVE promotion requires a verified owner-approval receipt; {reason}",
            )
        if reason.startswith("cannot promote") or reason == "policy_not_found":
            raise HTTPException(status_code=409, detail=reason)
        raise HTTPException(status_code=422, detail=reason)

    promoted = result["policy"]
    return {
        "id": promoted.id,
        "status": promoted.status,
        "policy_version": promoted.policy_version,
        "approval_receipt_id": receipt_id,
    }


@router.post("/planning/policies/{policy_id}/rollback")
async def rollback_planning_policy(policy_id: str, payload: dict, db: Session = Depends(get_db)):
    from backend.learning.planning_store import PlanningPolicyStore
    store = PlanningPolicyStore(db)
    reason = str(payload.get("reason") or "rollback")
    result = store.rollback(policy_id, reason=reason)
    if not result.get("ok"):
        raise HTTPException(status_code=404, detail=result.get("reason"))
    pol = result["policy"]
    return {"id": pol.id, "status": pol.status, "rollback_reason": pol.rollback_reason}


@router.post("/planning/select")
async def planning_select(payload: dict, db: Session = Depends(get_db)):
    from backend.learning.planning_selector import PlanningPolicySelector
    selector = PlanningPolicySelector(db)
    task_context = payload.get("task_context") or {"task_class": payload.get("task_class") or "general"}
    failure_context = payload.get("failure_context")
    candidates = payload.get("candidates")
    result = selector.select(
        task_context=task_context,
        failure_context=failure_context,
        candidates=candidates,
        hard_constraints=payload.get("hard_constraints"),
        risk_level=payload.get("risk_level") or task_context.get("risk_level"),
        budget_remaining=payload.get("budget_remaining"),
    )
    return result.to_dict()
