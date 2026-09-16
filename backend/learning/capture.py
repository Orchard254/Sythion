"""Evidence capture — transforms verified outcomes into StrategyOutcome rows (Phase 11)."""

from __future__ import annotations

import hashlib
import json
import logging
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.learning.feedback import classify_user_feedback
from backend.learning.store import StrategyOutcomeStore
from backend.learning.taxonomy import OutcomeClass, StrategyType

# Phase 12.5 final gate: capture-failure observability.
# Learning capture is best-effort (LEARNING_ONLY): a failure must never affect
# runtime execution, but it must also never disappear silently — otherwise
# REAL_EXECUTION evidence would be lost invisibly. This is plain stdlib logging:
# no new subsystem, no ledger event (which would recurse into learning), no
# secrets or prompts (only ids, exception type/message, occurrence counts).
CAPTURE_LOGGER = logging.getLogger("sythion.learning.capture")

_CAPTURE_FAIL_COUNTS: Dict[str, int] = {}
_CAPTURE_FAIL_REPEAT_EVERY = 50


def log_capture_failure(site: str, exc: BaseException, **context: Any) -> None:
    """Record an observable, rate-limited trace of a learning-capture failure.

    - never raises;
    - first occurrence of each (site, exception-type) signature logs at WARNING;
    - repeats are counted silently and re-logged every Nth occurrence so a
      persistently broken capture cannot flood the log while still surfacing;
    - context values are diagnostic ids only (task/step/goal/plan).
    """
    try:
        sig = f"{site}|{type(exc).__name__}"
        count = _CAPTURE_FAIL_COUNTS.get(sig, 0) + 1
        # bounded signature space (sites x exception types); defensive cap anyway
        if len(_CAPTURE_FAIL_COUNTS) < 256:
            _CAPTURE_FAIL_COUNTS[sig] = count
        ctx = " ".join(f"{k}={v}" for k, v in sorted(context.items()) if v is not None)
        if count == 1 or count % _CAPTURE_FAIL_REPEAT_EVERY == 0:
            CAPTURE_LOGGER.warning(
                "learning_capture_failed site=%s error=%s occurrences=%d %s",
                site,
                exc,
                count,
                ctx,
            )
    except Exception:
        pass


def report_capture_result(site: str, result: Any, **context: Any) -> None:
    """Surface non-recorded store results (record() returns reason strings
    instead of raising on validation/persistence problems)."""
    try:
        if isinstance(result, dict) and result.get("outcome") is None:
            reason = str(result.get("reason") or "unknown")
            log_capture_failure(site, CaptureRejected(reason), **context)
    except Exception:
        pass


class CaptureRejected(Exception):
    """Raised internally by report_capture_result to give a rejected capture
    a stable exception signature for dedup/logging."""

    def __init__(self, reason: str):
        super().__init__(reason)
        self.reason = reason


def infer_strategy_type(
    decision_type: Optional[str] = None,
    role: Optional[str] = None,
    multi: bool = False,
) -> str:
    if multi:
        return StrategyType.MULTI_SPECIALIST.value
    if decision_type == "DELEGATE" and role:
        mapping = {
            "RESEARCH": StrategyType.DELEGATE_RESEARCH.value,
            "ANALYSIS": StrategyType.DELEGATE_ANALYSIS.value,
            "CODING": StrategyType.DELEGATE_CODING.value,
            "REVIEW": StrategyType.DELEGATE_REVIEW.value,
            "PLANNING_SUPPORT": StrategyType.DELEGATE_PLANNING_SUPPORT.value,
        }
        return mapping.get(str(role).upper(), StrategyType.DELEGATE_ANALYSIS.value)
    if decision_type == "DELEGATE":
        return StrategyType.DELEGATE_ANALYSIS.value
    if decision_type in ("EXECUTE_STEP", "EXECUTE_DIRECTLY"):
        return StrategyType.DIRECT_EXECUTION.value
    if decision_type == "ASK_CLARIFICATION":
        return StrategyType.ASK_CLARIFICATION.value
    if decision_type == "ANSWER":
        return StrategyType.DIRECT_ANSWER.value
    return StrategyType.DIRECT_EXECUTION.value


def infer_task_class(intent_category: Optional[str], task_class_hint: Optional[str] = None) -> str:
    if task_class_hint:
        return str(task_class_hint).lower()
    if intent_category:
        mapping = {
            "command": "command",
            "informational": "informational",
            "goal": "goal",
            "follow_up": "follow_up",
            "clarification": "clarification",
            "correction": "correction",
            "social": "social",
            "feedback": "general",
        }
        return mapping.get(str(intent_category).lower(), "general")
    return "general"


def infer_outcome(
    task_state: Optional[str] = None,
    verification: Optional[str] = None,
    failure_code: Optional[str] = None,
    user_corrected: bool = False,
    user_rejected: bool = False,
    timed_out: bool = False,
    cancelled: bool = False,
) -> str:
    if cancelled:
        return OutcomeClass.CANCELLED.value
    if timed_out or failure_code == "TIMEOUT" or task_state == "timed_out":
        return OutcomeClass.TIMED_OUT.value
    if user_corrected:
        return OutcomeClass.USER_CORRECTED.value
    if user_rejected:
        return OutcomeClass.USER_REJECTED.value
    if verification == "failed" or failure_code == "FAILED_VERIFICATION":
        return OutcomeClass.FAILED_VERIFICATION.value
    if task_state == "completed" and verification in (None, "verified", "SUCCESS_VERIFIED"):
        # need to check correction path already
        return OutcomeClass.SUCCESS_VERIFIED.value
    if task_state == "completed":
        return OutcomeClass.SUCCESS_PARTIAL.value
    if task_state in ("failed", "failed_verification"):
        return OutcomeClass.FAILED.value
    if task_state in ("cancelled",):
        return OutcomeClass.CANCELLED.value
    # unavailable case: no model/capability
    if failure_code == "UNAVAILABLE":
        return OutcomeClass.UNAVAILABLE.value
    return OutcomeClass.FAILED.value


def record_task_outcome(
    db,
    *,
    task_id: str,
    intent_category: Optional[str] = None,
    decision_type: Optional[str] = None,
    role: Optional[str] = None,
    multi: bool = False,
    task_state: Optional[str] = None,
    verification: Optional[str] = None,
    model_id: Optional[str] = None,
    cost: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    attempts: int = 1,
    evidence_refs: Optional[List[str]] = None,
    user_feedback_text: Optional[str] = None,
    failure_code: Optional[str] = None,
    goal_id: Optional[str] = None,
) -> Dict[str, Any]:
    """Deterministic capture helper used by task/delegation completion paths."""
    from backend.learning.store import StrategyOutcomeStore

    strategy = infer_strategy_type(decision_type, role, multi=multi)
    task_class = infer_task_class(intent_category)
    user_fb_kind = classify_user_feedback(user_feedback_text) if user_feedback_text else None
    corrected = user_fb_kind is not None and user_fb_kind != "PREFERENCE_FEEDBACK"
    outcome = infer_outcome(
        task_state=task_state,
        verification=verification,
        failure_code=failure_code,
        user_corrected=bool(corrected and outcome_would_be_corrected(user_fb_kind)),
        cancelled=(task_state == "cancelled"),
        timed_out=(task_state == "timed_out"),
    )
    # if user feedback is a correction, override outcome to USER_CORRECTED for negative signals
    if corrected and outcome_would_be_corrected(user_fb_kind):
        outcome = OutcomeClass.USER_CORRECTED.value

    store = StrategyOutcomeStore(db)
    return store.record(
        task_id=task_id,
        goal_id=goal_id,
        task_class=task_class,
        strategy_type=strategy,
        role=role.upper() if role else None,
        model_id=model_id,
        outcome=outcome,
        verification=verification,
        user_feedback=user_fb_kind,
        cost=cost,
        latency_ms=latency_ms,
        attempts=attempts,
        evidence_refs=evidence_refs,
    )


def outcome_would_be_corrected(kind: Optional[str]) -> bool:
    if kind is None:
        return False
    return kind in (
        "INTERPRETATION_ERROR",
        "STRATEGY_ERROR",
        "DELEGATION_ERROR",
        "SPECIALIST_ROLE_ERROR",
        "OUTPUT_QUALITY_ERROR",
    )


# ---------------------------------------------------------------------------
# Retry / Replan / Goal-level capture (Phase 11.5 §3-5)
# ---------------------------------------------------------------------------

def record_retry_outcome(
    db,
    *,
    task_id: str,
    step_id: int,
    attempt: int,
    outcome: str,
    strategy_type: Optional[str] = None,
    goal_id: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
    cost: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    task_class: str = "goal",
) -> Dict[str, Any]:
    """Record a per-attempt outcome preserving retry context."""
    from backend.learning.store import StrategyOutcomeStore

    stype = strategy_type or StrategyType.DIRECT_EXECUTION.value
    store = StrategyOutcomeStore(db)
    # Fingerprint includes attempt so attempt 1 vs 2 are distinct, but same attempt re-emit is idempotent
    fp = hashlib.sha256(f"retry|{task_id}|{step_id}|{attempt}|{outcome}".encode()).hexdigest()[:16]
    return store.record(
        task_id=task_id,
        goal_id=goal_id,
        task_class=task_class,
        strategy_type=stype,
        outcome=outcome,
        verification="failed" if outcome in ("FAILED", "FAILED_VERIFICATION") else None,
        cost=cost,
        latency_ms=latency_ms,
        attempts=attempt,
        evidence_refs=evidence_refs or [f"task:{task_id}", f"plan_step:{step_id}"],
        evidence_fingerprint=fp,
    )


def record_replan_outcome(
    db,
    *,
    goal_id: str,
    original_plan_id: Optional[int],
    new_plan_id: Optional[int],
    reason: str,
    original_strategy: Optional[str] = None,
    new_strategy: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
    cost: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
) -> Dict[str, Any]:
    """Record replanning trigger + revised plan as evidence."""
    from backend.learning.store import StrategyOutcomeStore

    # Replan is not a success/failure of a strategy but a meta-event; we record
    # it as SUCCESS_PARTIAL with the reason as verification to preserve provenance
    # without letting the selector learn "replan is superior" from one sample (§4).
    fp = hashlib.sha256(f"replan|{goal_id}|{original_plan_id}|{new_plan_id}|{reason}".encode()).hexdigest()[:16]
    store = StrategyOutcomeStore(db)
    return store.record(
        task_id=None,
        goal_id=goal_id,
        task_class="goal",
        strategy_type=new_strategy or StrategyType.DIRECT_EXECUTION.value,
        outcome=OutcomeClass.SUCCESS_PARTIAL.value,
        verification=f"replan:{reason[:100]}",
        cost=cost,
        latency_ms=latency_ms,
        attempts=1,
        evidence_refs=evidence_refs or ([f"goal:{goal_id}"] + ([f"plan:{original_plan_id}"] if original_plan_id else [])),
        evidence_fingerprint=fp,
    )


def record_goal_outcome(
    db,
    *,
    goal_id: str,
    outcome: str,
    evidence_refs: Optional[List[str]] = None,
    cost: Optional[Dict[str, Any]] = None,
    latency_ms: Optional[int] = None,
    attempts: int = 1,
    task_class: str = "goal",
) -> Dict[str, Any]:
    """Record a long-running Goal terminal outcome distinct from step outcomes (§5)."""
    from backend.learning.store import StrategyOutcomeStore

    fp = hashlib.sha256(f"goal|{goal_id}|{outcome}|{attempts}".encode()).hexdigest()[:16]
    store = StrategyOutcomeStore(db)
    return store.record(
        task_id=None,
        goal_id=goal_id,
        task_class=task_class,
        strategy_type=StrategyType.DIRECT_EXECUTION.value,
        outcome=outcome,
        cost=cost,
        latency_ms=latency_ms,
        attempts=attempts,
        evidence_refs=evidence_refs or [f"goal:{goal_id}"],
        evidence_fingerprint=fp,
    )


def record_cancellation_outcome(
    db,
    *,
    task_id: Optional[str] = None,
    plan_step_id: Optional[int] = None,
    goal_id: Optional[str] = None,
    strategy_type: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
) -> Dict[str, Any]:
    from backend.learning.store import StrategyOutcomeStore

    fp = hashlib.sha256(f"cancel|{task_id or goal_id or plan_step_id}|{strategy_type or 'DIRECT_EXECUTION'}".encode()).hexdigest()[:16]
    store = StrategyOutcomeStore(db)
    return store.record(
        task_id=task_id,
        goal_id=goal_id,
        task_class="goal" if goal_id else "command",
        strategy_type=strategy_type or StrategyType.DIRECT_EXECUTION.value,
        outcome=OutcomeClass.CANCELLED.value,
        attempts=1,
        evidence_refs=evidence_refs or ([f"task:{task_id}"] if task_id else [f"goal:{goal_id}"] if goal_id else [f"plan_step:{plan_step_id}"]),
        evidence_fingerprint=fp,
    )


# ---------------------------------------------------------------------------
# Phase 12 — Planning outcome + feedback + replan evidence (§4-8, §20)
# ---------------------------------------------------------------------------

def classify_planning_feedback(text: Optional[str]) -> Optional[str]:
    """Deterministic planning feedback classifier (§20)."""
    if not text or not text.strip():
        return None
    import re
    from backend.learning.planning_taxonomy import PlanningFeedbackKind, PLANNING_FEEDBACK_PATTERNS
    lowered = text.strip()
    for pat_str, kind in PLANNING_FEEDBACK_PATTERNS:
        if re.search(pat_str, lowered, re.IGNORECASE):
            return kind.value
    # generic planning feedback fallback
    if any(w in lowered.lower() for w in ("plan", "replan", "steps")):
        return PlanningFeedbackKind.PLANNING_QUALITY.value
    return None


def infer_planning_strategy(
    plan_steps: Optional[int] = None,
    has_dependencies: bool = False,
    delegated: bool = False,
    is_replan: bool = False,
    replan_before_retry: bool = False,
    explicit_strategy: Optional[str] = None,
) -> str:
    """Map plan characteristics to the bounded planning taxonomy (§3)."""
    from backend.learning.planning_taxonomy import PlanningStrategy
    if explicit_strategy and explicit_strategy.upper() in PlanningStrategy.values():
        return explicit_strategy.upper()
    if replan_before_retry:
        return PlanningStrategy.REPLAN_BEFORE_RETRY.value
    if is_replan:
        return PlanningStrategy.REPLAN_AFTER_FAILURE.value
    if delegated:
        return PlanningStrategy.DELEGATED_PLAN.value
    if has_dependencies:
        return PlanningStrategy.DEPENDENCY_AWARE_PLAN.value
    if plan_steps is not None and plan_steps <= 2:
        return PlanningStrategy.MINIMAL_PLAN.value
    if plan_steps is not None and plan_steps >= 5:
        return PlanningStrategy.DECOMPOSE_PLAN.value
    return PlanningStrategy.DIRECT_PLAN.value


def _sanitize_replan_trigger(trigger: Optional[str]) -> Optional[str]:
    if not trigger:
        return None
    from backend.learning.planning_taxonomy import ReplanTrigger
    v = str(trigger).strip().upper()
    if v in ReplanTrigger.values():
        return v
    # map common aliases deterministically
    alias = {
        "FAILED_VERIFICATION": "VERIFICATION_FAILURE",
        "EXECUTION": "EXECUTION_FAILURE",
        "TIMEOUT_EXCEEDED": "TIMEOUT",
        "DEPENDENCY": "DEPENDENCY_BLOCK",
    }
    mapped = alias.get(v)
    if mapped and mapped in ReplanTrigger.values():
        return mapped
    return None


def record_planning_outcome(
    db,
    *,
    goal_id: str,
    plan_id: Optional[int] = None,
    planning_strategy: Optional[str] = None,
    task_class: str = "goal",
    goal_type: Optional[str] = None,
    initial_plan_steps: Optional[int] = None,
    final_plan_steps: Optional[int] = None,
    retries: int = 0,
    replans: int = 0,
    verified_success: Optional[bool] = None,
    outcome: str = "SUCCESS_PARTIAL",
    total_cost: Optional[Dict[str, Any]] = None,
    total_latency_ms: Optional[int] = None,
    user_feedback_text: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
    replan_trigger: Optional[str] = None,
    failure_reason: Optional[str] = None,
    retry_vs_replan: Optional[str] = None,
    dependency_complexity: Optional[int] = None,
    risk_level: Optional[str] = None,
    original_plan_id: Optional[int] = None,
    new_plan_id: Optional[int] = None,
    provenance: str = "REAL_EXECUTION",
) -> Dict[str, Any]:
    """Record a planning-specific outcome (§4, §12.5 provenance)."""
    from backend.learning.planning_store import PlanningOutcomeStore
    if planning_strategy is None:
        planning_strategy = infer_planning_strategy(
            plan_steps=initial_plan_steps,
            has_dependencies=bool(dependency_complexity and dependency_complexity > 0),
        )
    fb_kind = classify_planning_feedback(user_feedback_text) if user_feedback_text else None
    trigger = _sanitize_replan_trigger(replan_trigger)
    store = PlanningOutcomeStore(db)
    return store.record(
        planning_strategy=planning_strategy,
        task_class=task_class,
        goal_id=goal_id,
        plan_id=plan_id,
        goal_type=goal_type,
        initial_plan_steps=initial_plan_steps,
        final_plan_steps=final_plan_steps,
        retries=retries,
        replans=replans,
        verified_success=verified_success,
        outcome=outcome,
        total_cost=total_cost,
        total_latency_ms=total_latency_ms,
        user_feedback=fb_kind,
        evidence_refs=evidence_refs or [f"goal:{goal_id}"] + ([f"plan:{plan_id}"] if plan_id else []),
        original_plan_id=original_plan_id,
        new_plan_id=new_plan_id,
        failure_reason=failure_reason,
        replan_trigger=trigger,
        retry_vs_replan=retry_vs_replan,
        dependency_complexity=dependency_complexity,
        risk_level=risk_level,
        provenance=provenance,
    )


def record_replan_planning_outcome(
    db,
    *,
    goal_id: str,
    original_plan_id: Optional[int],
    new_plan_id: Optional[int],
    reason: str,
    trigger: Optional[str] = None,
    retry_attempts: int = 0,
    planning_strategy: Optional[str] = None,
    evidence_refs: Optional[List[str]] = None,
    total_cost: Optional[Dict[str, Any]] = None,
    total_latency_ms: Optional[int] = None,
    provenance: str = "REAL_EXECUTION",
) -> Dict[str, Any]:
    """Structured replan evidence (§5, §12.5 provenance)."""
    from backend.learning.planning_store import PlanningOutcomeStore
    strat = planning_strategy or "REPLAN_AFTER_FAILURE"
    trig = _sanitize_replan_trigger(trigger) or _sanitize_replan_trigger(reason) or "EXECUTION_FAILURE"
    fp = hashlib.sha256(
        f"replan_planning|{goal_id}|{original_plan_id}|{new_plan_id}|{reason}|{retry_attempts}|{provenance}".encode()
    ).hexdigest()[:16]
    store = PlanningOutcomeStore(db)
    return store.record(
        planning_strategy=strat,
        task_class="goal",
        goal_id=goal_id,
        plan_id=new_plan_id,
        initial_plan_steps=None,
        final_plan_steps=None,
        retries=int(retry_attempts or 0),
        replans=1,
        verified_success=None,
        outcome="SUCCESS_PARTIAL",
        total_cost=total_cost,
        total_latency_ms=total_latency_ms,
        evidence_refs=evidence_refs or ([f"goal:{goal_id}"] + ([f"plan:{original_plan_id}"] if original_plan_id else []) + ([f"plan:{new_plan_id}"] if new_plan_id else [])),
        evidence_fingerprint=fp,
        original_plan_id=original_plan_id,
        new_plan_id=new_plan_id,
        failure_reason=(reason or "")[:1000],
        replan_trigger=trig,
        retry_vs_replan="REPLAN",
        dependency_complexity=None,
        risk_level=None,
        provenance=provenance,
    )
