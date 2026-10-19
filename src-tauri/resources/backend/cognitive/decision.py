"""DecisionSystem + SimulationEngine (Phase 8).

The DecisionSystem is DETERMINISTIC: it evaluates candidate decisions
produced by reasoning against hard constraints, risk, evidence, and
reversibility. It never executes anything and never invents candidates.

Hard constraints dominate: a candidate violating any HARD_CONSTRAINT is
vetoed regardless of score. Preferences only break ties among compatible
candidates.

SimulationEngine is dry-run ONLY: it predicts effects from candidate
metadata; it can never invoke real tools or mutate state.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.cognitive.state import (
    CandidateAction,
    ConfidenceBreakdown,
    DecisionType,
    RiskLevel,
    SelectedDecision,
)


# Documented scoring weights (deterministic; sum of positive weights = 1.0).
WEIGHTS = {
    "risk": 0.30,            # lower risk scores higher
    "evidence": 0.25,        # quality/quantity of supporting evidence
    "reversibility": 0.20,   # reversible actions preferred
    "capability_fit": 0.15,  # required capabilities actually available
    "cost": 0.10,            # lower estimated cost scores higher (None=neutral)
}

RISK_SCORE = {RiskLevel.NONE: 1.0, RiskLevel.LOW: 0.85,
              RiskLevel.MEDIUM: 0.6, RiskLevel.HIGH: 0.3,
              RiskLevel.CRITICAL: 0.05}


@dataclass
class HardConstraint:
    text: str
    check: Optional[Any] = None   # Optional callable(candidate)->bool (True=veto)


@dataclass
class SoftPreference:
    text: str
    weight: float = 1.0


@dataclass
class ConstraintSet:
    hard: List[HardConstraint] = field(default_factory=list)
    soft: List[SoftPreference] = field(default_factory=list)


@dataclass
class DecisionEvaluation:
    selected: Optional[SelectedDecision]
    vetoed: List[Dict[str, str]]
    scored: List[Dict[str, Any]]
    reason: str


class DecisionSystem:
    """Deterministic candidate evaluation with documented weights."""

    def __init__(self, available_capabilities: Optional[List[str]] = None):
        self.available_capabilities = set(available_capabilities or [])

    def evaluate(
        self,
        candidates: List[CandidateAction],
        constraints: ConstraintSet,
        confidence: Optional[ConfidenceBreakdown] = None,
        available_capabilities: Optional[List[str]] = None,
    ) -> DecisionEvaluation:
        # Live capability truth (SelfModel) wins over constructor defaults:
        # callers pass ACTUAL runtime capabilities so missing-capability
        # vetoes reflect reality, not stale configuration.
        caps = set(available_capabilities) if available_capabilities is not None \
            else self.available_capabilities
        vetoed: List[Dict[str, str]] = []
        scored: List[Dict[str, Any]] = []

        for cand in candidates:
            # HARD CONSTRAINTS dominate: first violation vetoes instantly.
            violated = self._violates_hard(cand, constraints.hard)
            if violated:
                vetoed.append({"action_id": cand.action_id,
                               "reason": f"hard constraint: {violated}"})
                continue

            # Capability honesty: requiring an unavailable capability is a veto.
            missing = [c for c in cand.requires_capabilities
                       if c not in caps]
            if missing:
                vetoed.append({"action_id": cand.action_id,
                               "reason": f"capabilities unavailable: {missing}"})
                continue

            score = self._score(cand, caps)
            scored.append({"candidate": cand, "score": round(score, 4),
                           "risk": cand.risk.value})

        if not scored:
            return DecisionEvaluation(
                selected=None, vetoed=vetoed, scored=[],
                reason="all candidates vetoed or none provided")

        scored.sort(key=lambda s: -s["score"])
        best: CandidateAction = scored[0]["candidate"]
        decision_confidence = min(1.0, scored[0]["score"]) if scored else None
        conf = confidence or ConfidenceBreakdown(None, None, None)
        final_conf = ConfidenceBreakdown(
            evidence=conf.evidence,
            interpretation=conf.interpretation,
            decision=decision_confidence,
        )

        selected = SelectedDecision(
            decision_type=best.decision_type,
            params={"description": best.description,
                    "expected_outcome": best.expected_outcome,
                    "action_id": best.action_id},
            justification=(
                f"highest deterministic score {scored[0]['score']} "
                f"(risk={best.risk.value}, "
                f"reversible={best.reversible})"),
            confidence=final_conf,
            risk=best.risk,
            candidate=best,
        )
        rationale_note = (
            f"{len(vetoed)} candidate(s) vetoed by hard constraints/capability gaps"
            if vetoed else "no candidates vetoed")
        return DecisionEvaluation(selected, vetoed, scored, rationale_note)

    # -- internals -----------------------------------------------------------

    @staticmethod
    def _violates_hard(cand: CandidateAction,
                       hard: List[HardConstraint]) -> Optional[str]:
        """Return the text of the first violated hard constraint.

        ``constraint.check(candidate)`` must return True when the candidate
        VIOLATES the constraint.
        """
        for constraint in hard:
            if constraint.check is None:
                continue
            try:
                if constraint.check(cand):
                    return constraint.text
            except Exception:
                return constraint.text
        return None

    def _score(self, cand: CandidateAction, caps: Optional[set] = None) -> float:
        caps = caps if caps is not None else self.available_capabilities
        cap_fit = (
            1.0 if not cand.requires_capabilities
            else len([c for c in cand.requires_capabilities
                      if c in caps])
            / max(1, len(cand.requires_capabilities))
        )
        cost_component = (
            1.0 - min(1.0, cand.cost_estimate) if cand.cost_estimate is not None
            else 0.5  # unknown cost = neutral
        )
        return (
            WEIGHTS["risk"] * RISK_SCORE[cand.risk]
            + WEIGHTS["evidence"] * min(1.0, len(cand.evidence_refs) / 3.0)
            + WEIGHTS["reversibility"] * (1.0 if cand.reversible else 0.0)
            + WEIGHTS["capability_fit"] * cap_fit
            + WEIGHTS["cost"] * cost_component
        )


class SimulationEngine:
    """Dry-run prediction. NEVER invokes real tools or mutates state."""

    @dataclass
    class SimulatedOutcome:
        action_id: str
        predicted_effects: List[str]
        risk: RiskLevel
        reversible: bool
        simulated: bool = True

    def evaluate(self, candidate: CandidateAction,
                 state: Optional[Any] = None) -> "SimulationEngine.SimulatedOutcome":
        effects = [f"expected: {candidate.expected_outcome}"]
        if not candidate.reversible:
            effects.append("WARNING: irreversible action")
        if candidate.risk.at_least(RiskLevel.HIGH):
            effects.append("high-risk: runtime policy/approval will gate execution")
        if candidate.requires_capabilities:
            effects.append(f"uses capabilities: {', '.join(candidate.requires_capabilities)}")
        effects.append("dry-run only — no real action performed")
        return SimulationEngine.SimulatedOutcome(
            action_id=candidate.action_id,
            predicted_effects=effects,
            risk=candidate.risk,
            reversible=candidate.reversible,
        )