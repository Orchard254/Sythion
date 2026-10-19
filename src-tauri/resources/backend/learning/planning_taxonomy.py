"""Phase 12 — Planning/replanning bounded taxonomies (no open-ended LLM vocabulary)."""

from __future__ import annotations

import enum
from typing import Dict, List


class PlanningStrategy(str, enum.Enum):
    """Small controlled planning strategy taxonomy (§3).

    Only values listed here are ever persisted or learned.
    LLM-generated names are rejected.
    """
    DIRECT_PLAN = "DIRECT_PLAN"
    MINIMAL_PLAN = "MINIMAL_PLAN"
    DECOMPOSE_PLAN = "DECOMPOSE_PLAN"
    DEPENDENCY_AWARE_PLAN = "DEPENDENCY_AWARE_PLAN"
    DELEGATED_PLAN = "DELEGATED_PLAN"
    REPLAN_AFTER_FAILURE = "REPLAN_AFTER_FAILURE"
    REPLAN_BEFORE_RETRY = "REPLAN_BEFORE_RETRY"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


class ReplanTrigger(str, enum.Enum):
    """Structured reasons for replanning (§7)."""
    VERIFICATION_FAILURE = "VERIFICATION_FAILURE"
    EXECUTION_FAILURE = "EXECUTION_FAILURE"
    TIMEOUT = "TIMEOUT"
    DEPENDENCY_BLOCK = "DEPENDENCY_BLOCK"
    CHANGED_CONTEXT = "CHANGED_CONTEXT"
    USER_CORRECTION = "USER_CORRECTION"
    RESOURCE_UNAVAILABLE = "RESOURCE_UNAVAILABLE"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


class RetryReplanAction(str, enum.Enum):
    """What to do for a failure class (§7)."""
    RETRY = "retry"
    REPLAN = "replan"
    ASK_USER = "ask_user"
    STOP = "stop"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


class PlanningFeedbackKind(str, enum.Enum):
    """User feedback about planning (§20)."""
    PLANNING_QUALITY = "PLANNING_QUALITY"
    STRATEGY_QUALITY = "STRATEGY_QUALITY"
    CLARIFICATION_MISSED = "CLARIFICATION_MISSED"
    OVER_PLANNED = "OVER_PLANNED"
    UNDER_PLANNED = "UNDER_PLANNED"
    REPLAN_ERROR = "REPLAN_ERROR"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


class PlanningPolicyStatus(str, enum.Enum):
    """Promotion lifecycle for a learned planning policy (§17)."""
    OBSERVED = "OBSERVED"
    SUPPORTED = "SUPPORTED"
    SHADOW_VALIDATED = "SHADOW_VALIDATED"
    OWNER_APPROVED = "OWNER_APPROVED"
    ACTIVE = "ACTIVE"
    ROLLED_BACK = "ROLLED_BACK"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


class PlanningProvenance(str, enum.Enum):
    """Evidence provenance (§4, §12.5 legacy). REAL_EXECUTION only after verified execution."""
    REAL_EXECUTION = "REAL_EXECUTION"
    SYNTHETIC_TEST = "SYNTHETIC_TEST"
    IMPORTED = "IMPORTED"
    SIMULATED = "SIMULATED"
    LEGACY = "LEGACY"  # historical row where provenance cannot be proven; never counts as REAL

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]


# Deterministic fallbacks for planning decisions (§11)
# These are the existing behaviors used when evidence is insufficient.
DEFAULT_RETRY_REPLAN_POLICY: Dict[str, str] = {
    ReplanTrigger.VERIFICATION_FAILURE.value: RetryReplanAction.RETRY.value,
    ReplanTrigger.EXECUTION_FAILURE.value: RetryReplanAction.RETRY.value,
    ReplanTrigger.TIMEOUT.value: RetryReplanAction.RETRY.value,
    ReplanTrigger.DEPENDENCY_BLOCK.value: RetryReplanAction.REPLAN.value,
    ReplanTrigger.CHANGED_CONTEXT.value: RetryReplanAction.REPLAN.value,
    ReplanTrigger.USER_CORRECTION.value: RetryReplanAction.ASK_USER.value,
    ReplanTrigger.RESOURCE_UNAVAILABLE.value: RetryReplanAction.STOP.value,
}

# Reuse Phase 11 evidence strength thresholds
PLANNING_EVIDENCE_THRESHOLDS: Dict[str, int] = {
    "UNRATED": 0,
    "LOW_SAMPLE": 1,
    "SUPPORTED": 5,
    "ESTABLISHED": 20,
}

# Goal-class context dimensions (§13) — structured features only
GOAL_CLASS_DIMENSIONS = {
    "task_class",
    "goal_type",
    "estimated_steps",
    "dependency_complexity",
    "risk_level",
    "capability_count",
    "delegation_available",
    "previous_failure_class",
}

# User feedback textual patterns → PlanningFeedbackKind (§20) — deterministic
PLANNING_FEEDBACK_PATTERNS = [
    (r"\b(too complicated|too many steps|over-?planned)\b", PlanningFeedbackKind.OVER_PLANNED),
    (r"\b(too simple|too few steps|under-?planned)\b", PlanningFeedbackKind.UNDER_PLANNED),
    (r"\b(should have asked|ask me first|clarification missed)\b", PlanningFeedbackKind.CLARIFICATION_MISSED),
    (r"\b(second approach|better plan|replan error|wrong replan)\b", PlanningFeedbackKind.REPLAN_ERROR),
    (r"\b(plan was (bad|poor|wrong)|bad plan|planning quality)\b", PlanningFeedbackKind.PLANNING_QUALITY),
    (r"\b(strategy quality|wrong strategy|bad strategy)\b", PlanningFeedbackKind.STRATEGY_QUALITY),
]
