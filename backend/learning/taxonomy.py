"""Phase 11 — Controlled vocabularies for learning (no open-ended LLM vocabulary)."""

from __future__ import annotations

import enum
from typing import Dict, List


class StrategyType(str, enum.Enum):
    DIRECT_EXECUTION = "DIRECT_EXECUTION"
    DIRECT_ANSWER = "DIRECT_ANSWER"
    DELEGATE_RESEARCH = "DELEGATE_RESEARCH"
    DELEGATE_ANALYSIS = "DELEGATE_ANALYSIS"
    DELEGATE_CODING = "DELEGATE_CODING"
    DELEGATE_REVIEW = "DELEGATE_REVIEW"
    DELEGATE_PLANNING_SUPPORT = "DELEGATE_PLANNING_SUPPORT"
    MULTI_SPECIALIST = "MULTI_SPECIALIST"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]

    @classmethod
    def is_delegated(cls, value: str) -> bool:
        return str(value).startswith("DELEGATE") or value == cls.MULTI_SPECIALIST.value

    @classmethod
    def role_for(cls, strategy: str) -> str | None:
        mapping = {
            cls.DELEGATE_RESEARCH.value: "RESEARCH",
            cls.DELEGATE_ANALYSIS.value: "ANALYSIS",
            cls.DELEGATE_CODING.value: "CODING",
            cls.DELEGATE_REVIEW.value: "REVIEW",
            cls.DELEGATE_PLANNING_SUPPORT.value: "PLANNING_SUPPORT",
        }
        return mapping.get(str(strategy))


class OutcomeClass(str, enum.Enum):
    SUCCESS_VERIFIED = "SUCCESS_VERIFIED"
    SUCCESS_PARTIAL = "SUCCESS_PARTIAL"
    FAILED = "FAILED"
    FAILED_VERIFICATION = "FAILED_VERIFICATION"
    CANCELLED = "CANCELLED"
    TIMED_OUT = "TIMED_OUT"
    USER_CORRECTED = "USER_CORRECTED"
    USER_REJECTED = "USER_REJECTED"
    UNAVAILABLE = "UNAVAILABLE"

    @classmethod
    def values(cls) -> List[str]:
        return [m.value for m in cls]

    @classmethod
    def is_success(cls, value: str) -> bool:
        return str(value) in (cls.SUCCESS_VERIFIED.value, cls.SUCCESS_PARTIAL.value)

    @classmethod
    def is_failure(cls, value: str) -> bool:
        return str(value) in (
            cls.FAILED.value,
            cls.FAILED_VERIFICATION.value,
            cls.TIMED_OUT.value,
            cls.UNAVAILABLE.value,
        )


class EvidenceStrength(str, enum.Enum):
    UNRATED = "UNRATED"
    LOW_SAMPLE = "LOW_SAMPLE"
    SUPPORTED = "SUPPORTED"
    ESTABLISHED = "ESTABLISHED"


class UserFeedbackKind(str, enum.Enum):
    INTERPRETATION_ERROR = "INTERPRETATION_ERROR"
    STRATEGY_ERROR = "STRATEGY_ERROR"
    DELEGATION_ERROR = "DELEGATION_ERROR"
    SPECIALIST_ROLE_ERROR = "SPECIALIST_ROLE_ERROR"
    OUTPUT_QUALITY_ERROR = "OUTPUT_QUALITY_ERROR"
    PREFERENCE_FEEDBACK = "PREFERENCE_FEEDBACK"


# Deterministic thresholds (conservative)
EVIDENCE_THRESHOLDS: Dict[str, int] = {
    EvidenceStrength.UNRATED.value: 0,
    EvidenceStrength.LOW_SAMPLE.value: 1,
    EvidenceStrength.SUPPORTED.value: 5,
    EvidenceStrength.ESTABLISHED.value: 20,
}

# Scoring weights for cost-aware strategy scoring (documented, deterministic)
SCORING_WEIGHTS: Dict[str, float] = {
    "success": 0.40,
    "verification": 0.20,
    "latency": 0.15,
    "cost": 0.10,
    "failure_risk": 0.15,
}

# Task class vocabulary — structured features only (never raw prompt text)
TASK_CLASSES = {
    "command",
    "informational",
    "goal",
    "follow_up",
    "clarification",
    "correction",
    "social",
    "general",
    "planning",
    "simple_reasoning",
    "structured_output",
    "context_following",
    "error_recovery",
    "tool_calling",
    "coding",
}
