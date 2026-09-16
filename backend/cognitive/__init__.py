"""Cognitive layer (Phase 8): structured understanding, reasoning, and
decision components above the Runtime Kernel.

Core rule: the LLM supplies reasoning; this package supplies STRUCTURE; the
Protected Runtime supplies AUTHORITY. No component here executes tools,
writes Goal/Task state, mutates memory directly, or touches permissions.
"""

from backend.cognitive.state import (
    AmbiguityLevel,
    CandidateAction,
    CognitiveState,
    ContextSource,
    ConfidenceBreakdown,
    DecisionType,
    EpistemicLabel,
    IntentCategory,
    IntentResult,
    RiskLevel,
    SelectedDecision,
)
from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.reasoning import ReasoningEngine, ReasoningArtifact
from backend.cognitive.decision import DecisionSystem, SimulationEngine
from backend.cognitive.pipeline import CognitivePipeline

__all__ = [
    "AmbiguityLevel", "CandidateAction", "CognitiveState", "ContextSource",
    "ConfidenceBreakdown", "DecisionType", "EpistemicLabel", "IntentCategory",
    "IntentResult", "RiskLevel", "SelectedDecision",
    "IntentInterpreter", "ReasoningEngine", "ReasoningArtifact",
    "DecisionSystem", "SimulationEngine", "CognitivePipeline",
]