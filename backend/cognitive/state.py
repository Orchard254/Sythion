"""Structured cognitive state and vocabulary (Phase 8).

CognitiveState is a STRUCTURED representation passed between the cognitive
components — not a prompt string. It carries no chain-of-thought; reasoning
artifacts are separate concise result objects.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

MAX_CONTEXT_SOURCE_CHARS = 1500
MAX_STATE_PREVIEW_CHARS = 500


class IntentCategory(str, enum.Enum):
    COMMAND = "command"                 # immediate action on this machine
    INFORMATIONAL = "informational"     # question / lookup → answer
    SOCIAL = "social"                   # greeting/chat/farewell → converse
    GOAL = "goal"                       # long-running, multi-session objective
    FOLLOW_UP = "follow_up"             # refers to prior context
    CLARIFICATION = "clarification"     # user answering our question
    CORRECTION = "correction"           # user correcting us
    FEEDBACK = "feedback"               # preference/strategy feedback


class InteractionType(str, enum.Enum):
    """Phase 13A: Top-level interaction boundary separating dialogue from executable work."""
    CONVERSATION = "CONVERSATION"                   # small talk, opinion, chit-chat, thoughts
    INFORMATIONAL = "INFORMATIONAL"                 # factual Q&A, explanations, lookups
    ACTION = "ACTION"                               # single-turn direct tool/system action
    GOAL = "GOAL"                                   # multi-step/planned objective
    CLARIFICATION_REQUIRED = "CLARIFICATION_REQUIRED" # missing required parameters for action/goal



class AmbiguityLevel(str, enum.Enum):
    CLEAR = "CLEAR"
    LOW_AMBIGUITY = "LOW_AMBIGUITY"
    AMBIGUOUS = "AMBIGUOUS"
    HIGH_IMPACT_AMBIGUITY = "HIGH_IMPACT_AMBIGUITY"


class RiskLevel(str, enum.Enum):
    NONE = "NONE"
    LOW = "LOW"
    MEDIUM = "MEDIUM"
    HIGH = "HIGH"
    CRITICAL = "CRITICAL"

    @classmethod
    def order(cls) -> List["RiskLevel"]:
        return [cls.NONE, cls.LOW, cls.MEDIUM, cls.HIGH, cls.CRITICAL]

    def at_least(self, other: "RiskLevel") -> bool:
        return self.order().index(self) >= self.order().index(other)


class EpistemicLabel(str, enum.Enum):
    """Evidence-aware labels distinguishing how we know what we know."""
    KNOWN = "KNOWN"            # from current authoritative runtime state
    OBSERVED = "OBSERVED"      # from a live tool observation this session
    REMEMBERED = "REMEMBERED"  # from persisted memory (may be stale)
    INFERRED = "INFERRED"      # derived by reasoning (propositional)
    ASSUMED = "ASSUMED"        # unverified assumption
    UNKNOWN = "UNKNOWN"


@dataclass
class Interpretation:
    """One candidate reading of the input. Inference ≠ fact."""
    description: str
    category: IntentCategory
    confidence: float = 0.5               # relative 0..1 (not calibrated)
    entities: List[str] = field(default_factory=list)
    needs_clarification: bool = False


@dataclass
class IntentResult:
    category: IntentCategory
    interpretations: List[Interpretation] = field(default_factory=list)
    desired_outcome: Optional[str] = None
    ambiguity: AmbiguityLevel = AmbiguityLevel.CLEAR
    confidence: float = 1.0                # interpretation confidence
    needs_clarification: bool = False
    source: str = "deterministic"          # deterministic | llm_refined

    def primary(self) -> Optional[Interpretation]:
        return self.interpretations[0] if self.interpretations else None


@dataclass
class ConfidenceBreakdown:
    evidence: Optional[float]              # None = unknown
    interpretation: Optional[float]
    decision: Optional[float]


@dataclass
class ContextSource:
    """One labeled context fragment. Sources stay labeled — never merged
    into indistinguishable prose before the cognitive layer sees them."""
    label: str                             # USER_INPUT | GOAL_CONTEXT | MEMORY_DATA | ...
    content: str
    epistemic: EpistemicLabel = EpistemicLabel.KNOWN


@dataclass
class CandidateAction:
    """A candidate decision produced by reasoning (proposal only)."""
    action_id: str
    decision_type: str                     # DecisionType value
    description: str
    expected_outcome: str = ""
    risk: RiskLevel = RiskLevel.LOW
    reversible: bool = True
    requires_capabilities: List[str] = field(default_factory=list)
    requires_permission: bool = False
    evidence_refs: List[str] = field(default_factory=list)
    cost_estimate: Optional[float] = None  # unknown stays None


@dataclass
class SelectedDecision:
    decision_type: str
    params: Dict[str, Any]
    justification: str
    confidence: ConfidenceBreakdown
    risk: RiskLevel
    candidate: Optional[CandidateAction] = None


@dataclass
class CognitiveState:
    """Structured snapshot consumed by reasoning/decision components."""
    user_input: str
    intent: Optional[IntentResult] = None
    context_sources: List[ContextSource] = field(default_factory=list)
    active_goals: List[Dict[str, Any]] = field(default_factory=list)
    relevant_memory: List[Dict[str, Any]] = field(default_factory=list)
    capabilities_available: List[str] = field(default_factory=list)
    uncertainty: AmbiguityLevel = AmbiguityLevel.CLEAR
    candidates: List[CandidateAction] = field(default_factory=list)
    selected: Optional[SelectedDecision] = None
    created_at: datetime = field(default_factory=datetime.utcnow)

    def add_context(self, label: str, content: str,
                    epistemic: EpistemicLabel = EpistemicLabel.KNOWN) -> None:
        if content:
            self.context_sources.append(
                ContextSource(label=label, content=content[:MAX_CONTEXT_SOURCE_CHARS],
                              epistemic=epistemic))

    def to_dict(self) -> Dict[str, Any]:
        return {
            "user_input": self.user_input[:MAX_STATE_PREVIEW_CHARS],
            "intent": {
                "category": self.intent.category.value if self.intent else None,
                "ambiguity": self.uncertainty.value,
                "confidence": self.intent.confidence if self.intent else None,
            } if self.intent else None,
            "context_labels": [c.label for c in self.context_sources],
            "active_goals": len(self.active_goals),
            "relevant_memory": len(self.relevant_memory),
            "capabilities": len(self.capabilities_available),
            "candidates": [c.action_id for c in self.candidates],
            "selected": (
                {"type": self.selected.decision_type,
                 "risk": self.selected.risk.value}
                if self.selected else None),
        }


class DecisionType:
    ANSWER = "ANSWER"
    ASK_CLARIFICATION = "ASK_CLARIFICATION"
    CREATE_GOAL = "CREATE_GOAL"
    UPDATE_GOAL = "UPDATE_GOAL"
    EXECUTE_STEP = "EXECUTE_STEP"
    EXECUTE_DIRECTLY = "EXECUTE_DIRECTLY"   # explicit direct-execution label
    DELEGATE = "DELEGATE"                   # bounded specialist delegation (Phase 9)
    DEFER = "DEFER"
    REFUSE = "REFUSE"
    WAIT = "WAIT"
    REQUEST_PERMISSION = "REQUEST_PERMISSION"
    NO_ACTION = "NO_ACTION"