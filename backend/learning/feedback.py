"""Deterministic user-feedback classifier (Phase 11, section 18)."""

from __future__ import annotations

import re
from typing import Optional

from backend.learning.taxonomy import UserFeedbackKind


_PATTERNS = [
    (re.compile(r"\b(you misunderstood|misunderstood me|not what i meant|wrong interpretation)\b", re.I), UserFeedbackKind.INTERPRETATION_ERROR),
    (re.compile(r"\b(wrong strategy|bad strategy|should have (done )?direct|should have delegated)\b", re.I), UserFeedbackKind.STRATEGY_ERROR),
    (re.compile(r"\b(should not have delegated|delegation (error|wrong)|wrong specialist|should have used \w+ specialist)\b", re.I), UserFeedbackKind.DELEGATION_ERROR),
    (re.compile(r"\b(wrong role|role error|should have been \w+ role|specialist role)\b", re.I), UserFeedbackKind.SPECIALIST_ROLE_ERROR),
    (re.compile(r"\b(bad output|poor quality|not what i wanted|output (error|quality)|not useful)\b", re.I), UserFeedbackKind.OUTPUT_QUALITY_ERROR),
    (re.compile(r"\b(prefer|i like|from now on|do it this way next time|my preference)\b", re.I), UserFeedbackKind.PREFERENCE_FEEDBACK),
]


def classify_user_feedback(text: str) -> Optional[str]:
    """Deterministic classification of correction/feedback text.

    Returns the UserFeedbackKind value or None if unclassified.
    Never uses LLM — purely rule-based.
    """
    if not text or not text.strip():
        return None
    lowered = text.strip()
    for pat, kind in _PATTERNS:
        if pat.search(lowered):
            return kind.value
    # Fallback heuristic: very short corrections are interpretation errors
    if len(lowered.split()) <= 6 and any(w in lowered.lower() for w in ("no", "wrong", "not")):
        return UserFeedbackKind.INTERPRETATION_ERROR.value
    return None


def feedback_is_correction(kind: Optional[str]) -> bool:
    return kind in (
        UserFeedbackKind.INTERPRETATION_ERROR.value,
        UserFeedbackKind.STRATEGY_ERROR.value,
        UserFeedbackKind.DELEGATION_ERROR.value,
        UserFeedbackKind.SPECIALIST_ROLE_ERROR.value,
        UserFeedbackKind.OUTPUT_QUALITY_ERROR.value,
    )
