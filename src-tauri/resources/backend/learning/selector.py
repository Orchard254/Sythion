"""StrategySelector — deterministic, bounded, provider-independent (Phase 11)."""

from __future__ import annotations

import json
import math
import random
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.learning.taxonomy import EvidenceStrength, OutcomeClass, SCORING_WEIGHTS, StrategyType
from backend.learning.store import StrategyOutcomeStore, StrategyStatistics


@dataclass
class SelectionResult:
    strategy: str
    confidence: Optional[float]
    evidence_strength: str
    evidence_summary: Dict[str, Any]
    alternatives: List[Dict[str, Any]]
    reason: str
    mode: str = "EXPLOIT"  # EXPLOIT | EXPLORE
    score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "strategy": self.strategy,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "evidence_summary": self.evidence_summary,
            "alternatives": self.alternatives,
            "reason": self.reason,
            "mode": self.mode,
            "score": self.score,
        }


def _sanitize_strategy(value: str) -> Optional[str]:
    v = str(value).strip().upper()
    if v in StrategyType.values():
        return v
    return None


def _risk_value(risk: str | None) -> int:
    order = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return order.get(str(risk or "LOW").upper(), 1)


class StrategySelector:
    """Deterministic selector over a small controlled strategy taxonomy.

    Ordering (section 11):
      hard constraints → capability compatibility → safety/runtime eligibility
      → current availability → strategy evidence → cost/latency preferences.

    The selector never overrides a hard constraint or an unavailable capability.
    """

    def __init__(self, session, store: Optional[StrategyOutcomeStore] = None):
        self.db = session
        self.store = store or StrategyOutcomeStore(session)

    def select(
        self,
        task_context: Dict[str, Any],
        candidates: List[str | Dict[str, Any]],
        *,
        hard_constraints: Optional[List[Dict[str, Any]]] = None,
        available_capabilities: Optional[List[str]] = None,
        strategy_availability: Optional[Dict[str, bool]] = None,
        risk_level: Optional[str] = None,
    ) -> SelectionResult:
        """Select a strategy deterministically.

        task_context: {task_class, goal_type, complexity, required_capability, specialist_role, risk_level, estimated_steps}
        candidates: list of strategy_type strings or {strategy, requires_capabilities}
        hard_constraints: [{strategy, violated: bool, reason}]
        available_capabilities: list of cap ids actually available
        strategy_availability: {strategy: bool} — current runtime availability (e.g., specialists_enabled)
        """
        from backend.core.config import settings

        # Normalize candidates to list of {strategy, requires_capabilities}
        norm: List[Dict[str, Any]] = []
        for c in candidates:
            if isinstance(c, str):
                s = _sanitize_strategy(c)
                if s:
                    norm.append({"strategy": s, "requires_capabilities": []})
            elif isinstance(c, dict):
                s = _sanitize_strategy(c.get("strategy") or c.get("strategy_type") or "")
                if s:
                    norm.append({
                        "strategy": s,
                        "requires_capabilities": list(c.get("requires_capabilities") or c.get("required_capabilities") or []),
                    })
        if not norm:
            return SelectionResult(
                strategy=StrategyType.DIRECT_ANSWER.value,
                confidence=None,
                evidence_strength=EvidenceStrength.UNRATED.value,
                evidence_summary={"reason": "no valid candidates; default"},
                alternatives=[],
                reason="no valid candidates",
            )

        # Phase: veto by hard constraints
        vetoed: Dict[str, str] = {}
        if hard_constraints:
            for hc in hard_constraints:
                if hc.get("violated"):
                    vet = hc.get("strategy")
                    if vet:
                        vetoed[str(vet).upper()] = hc.get("reason") or "hard constraint"

        # Veto by capability mismatch
        caps = set(available_capabilities or [])
        for item in list(norm):
            reqs = item.get("requires_capabilities") or []
            missing = [r for r in reqs if r not in caps]
            # only veto if caller explicitly supplied availability list and there is a gap
            if available_capabilities is not None and missing:
                vetoed[item["strategy"]] = f"capabilities unavailable: {missing}"

        # Veto by current availability (e.g., specialists disabled)
        if strategy_availability is not None:
            for item in list(norm):
                avail = strategy_availability.get(item["strategy"])
                if avail is False:
                    vetoed[item["strategy"]] = "currently unavailable"

        # Filter to eligible after vetoes
        eligible = [c for c in norm if c["strategy"] not in vetoed]
        if not eligible:
            # all vetoed — return best vetoed as explanation but signal unavailable
            return SelectionResult(
                strategy=norm[0]["strategy"],
                confidence=None,
                evidence_strength=EvidenceStrength.UNRATED.value,
                evidence_summary={"vetoed": vetoed},
                alternatives=[{"strategy": c["strategy"], "reason": vetoed.get(c["strategy"], "vetoed")} for c in norm],
                reason="all candidates vetoed by hard constraints/capability/availability",
            )

        # Gather evidence for each eligible strategy
        task_class = (task_context or {}).get("task_class") or "general"
        # Context matching: filter evidence to relevant task_class (structured, not raw text)
        # Use deterministic scoring
        scored: List[Dict[str, Any]] = []
        for item in eligible:
            stype = item["strategy"]
            role = StrategyType.role_for(stype)
            stats: StrategyStatistics = self.store.statistics(task_class=task_class, strategy_type=stype, role=role)
            # cost-aware deterministic score
            score = self._score(stats)
            # evidence strength
            scored.append({
                "strategy": stype,
                "role": role,
                "stats": stats,
                "score": score,
            })

        # Sort by score desc, then by evidence strength, then deterministic id
        def _rank_key(s):
            return (-s["score"], -s["stats"].sample_count, s["strategy"])

        scored.sort(key=_rank_key)
        best = scored[0]
        best_stats: StrategyStatistics = best["stats"]

        # Confidence handling (section 19): decision confidence vs evidence strength are distinct
        # Decision confidence is the score; evidence strength is sample-based.
        # We expose both. Confidence is None when evidence is UNRATED/LOW_SAMPLE.
        if best_stats.evidence_strength in (EvidenceStrength.UNRATED.value, EvidenceStrength.LOW_SAMPLE.value):
            confidence = None
        else:
            # confidence derived from success rate, capped
            sr = best_stats.success_rate or 0.0
            # Require at least SUPPORTED to have confidence
            confidence = round(min(1.0, sr * 0.9 + 0.1), 3) if best_stats.sample_count >= 5 else None

        # Evidence summary with provenance fields (section 24)
        evidence_summary = {
            "task_class": task_class,
            "strategy_type": best["strategy"],
            "role": best["role"],
            "sample_count": best_stats.sample_count,
            "success_rate": best_stats.success_rate,
            "verified_success_rate": best_stats.verified_success_rate,
            "failure_rate": best_stats.failure_rate,
            "mean_latency_ms": best_stats.mean_latency_ms,
            "mean_cost": best_stats.mean_cost,
            "evidence_strength": best_stats.evidence_strength,
            "recent_count": best_stats.recent_count,
            "decayed_count": best_stats.decayed_count,
            "vetoed": vetoed,
        }

        alternatives = [
            {
                "strategy": s["strategy"],
                "score": round(s["score"], 4),
                "sample_count": s["stats"].sample_count,
                "evidence_strength": s["stats"].evidence_strength,
                "success_rate": s["stats"].success_rate,
            }
            for s in scored[1:]
        ]

        # Exploration vs exploitation (section 20/21)
        mode = "EXPLOIT"
        risk = risk_level or (task_context or {}).get("risk_level") or "LOW"
        # Exploration disabled for high-risk or UNRATED baseline
        exploration_allowed = best_stats.evidence_strength != EvidenceStrength.UNRATED.value and best_stats.sample_count > 0
        try:
            high_threshold = getattr(settings, "strategy_high_risk_threshold", "HIGH")
            if _risk_value(risk) >= _risk_value(high_threshold):
                exploration_allowed = False
        except Exception:
            pass
        # Also disabled if best is already SUPPORTED/ESTABLISHED with high confidence and low risk alt would add cost
        if best_stats.evidence_strength in (EvidenceStrength.SUPPORTED.value, EvidenceStrength.ESTABLISHED.value) and confidence and confidence >= float(getattr(settings, "strategy_min_confidence_to_exploit", 0.65)):
            exploration_allowed = False

        # Deterministic exploration flag: use hash of task_class for reproducibility
        if exploration_allowed and len(scored) > 1:
            try:
                rate = float(getattr(settings, "strategy_exploration_rate", 0.10))
                # deterministic: hash task_class + date bucket
                import hashlib
                bucket = datetime.utcnow().strftime("%Y-%m-%d")
                h = hashlib.sha256(f"{task_class}:{bucket}".encode()).hexdigest()
                # map first 4 hex chars to [0,1)
                frac = int(h[:4], 16) / 65535.0
                max_cost = float(getattr(settings, "strategy_max_exploration_cost", 2.0))
                alt_cost = scored[1]["stats"].mean_cost
                alt_cost_ok = alt_cost is None or alt_cost <= max_cost
                # Controlled exploration must not promote a candidate whose
                # supporting evidence has expired/decayed (sample_count == 0).
                alt_supported = scored[1]["stats"].sample_count > 0
                if frac < rate and alt_cost_ok and alt_supported:
                    # EXPLORE: pick the best alternative (credible) when cost/risk low
                    mode = "EXPLORE"
                    # For now we still return best as strategy but mark mode EXPLORE and include alternative
                    # Alternatively could return the alternative itself — we return alternative as chosen to demonstrate
                    # but ensure it is not high-risk. Caller can decide to use it.
                    # Here we implement controlled explore by selecting the alternative.
                    best = scored[1]
                    best_stats = best["stats"]
                    evidence_summary = {
                        "task_class": task_class,
                        "strategy_type": best["strategy"],
                        "role": best["role"],
                        "sample_count": best_stats.sample_count,
                        "success_rate": best_stats.success_rate,
                        "evidence_strength": best_stats.evidence_strength,
                        "exploration": True,
                        "vetoed": vetoed,
                    }
                    confidence = None  # exploration has low confidence
            except Exception:
                pass

        reason = (
            f"highest deterministic score {round(best['score'], 4)} "
            f"(evidence_strength={best_stats.evidence_strength}, "
            f"n={best_stats.sample_count})"
            if mode == "EXPLOIT"
            else f"controlled exploration: trying alternative {best['strategy']} "
                 f"(evidence_strength={best_stats.evidence_strength})"
        )

        return SelectionResult(
            strategy=best["strategy"],
            confidence=confidence,
            evidence_strength=best_stats.evidence_strength,
            evidence_summary=evidence_summary,
            alternatives=alternatives,
            reason=reason,
            mode=mode,
            score=round(best["score"], 4),
        )

    def _score(self, stats: StrategyStatistics) -> float:
        """Cost-aware deterministic scoring (section 17, documented formula).

        strategy_score = success evidence + verification quality
                         - latency cost - model cost - specialist cost - failure risk

        Unknown values do not become fabricated zeros: missing latency/cost
        contributes neutral 0.5 normalized.
        """
        w = SCORING_WEIGHTS
        # success evidence (0..1)
        success = stats.success_rate if stats.success_rate is not None else 0.5
        # verification quality (0..1) — verified success rate
        verification = stats.verified_success_rate if stats.verified_success_rate is not None else (0.5 if stats.sample_count else 0.0)
        # latency cost normalized: lower is better; unknown = neutral 0.5
        if stats.mean_latency_ms is None:
            latency_cost = 0.5
        else:
            # normalize against 5000ms ceiling
            latency_cost = min(1.0, stats.mean_latency_ms / 5000.0)
        # model/specialist cost normalized: unknown = neutral 0.5
        if stats.mean_cost is None:
            cost_component = 0.5
        else:
            cost_component = min(1.0, stats.mean_cost / 5.0)
        # failure risk (0..1) — higher is worse
        failure_risk = stats.failure_rate if stats.failure_rate is not None else 0.5
        # bounded score in [-1, 1.5] range, then shift to 0..1
        raw = (
            w["success"] * success
            + w["verification"] * verification
            - w["latency"] * latency_cost
            - w["cost"] * cost_component
            - w["failure_risk"] * failure_risk
        )
        # add small prior for unknown so unrated not zeroed out (don't fabricate)
        if stats.sample_count == 0:
            raw += 0.05  # tiny neutral prior, not a learned preference
        return max(0.0, min(1.5, raw))

    def provenance_for(self, strategy_type: str, task_class: str = "general") -> Dict[str, Any]:
        """Answer 'which executions support this?' (section 24)."""
        stats = self.store.statistics(task_class=task_class, strategy_type=strategy_type)
        recent = self.store.list_outcomes(task_class=task_class, strategy_type=strategy_type, limit=10)
        return {
            "strategy_type": strategy_type,
            "task_class": task_class,
            "statistics": {
                "sample_count": stats.sample_count,
                "success_rate": stats.success_rate,
                "failure_rate": stats.failure_rate,
                "evidence_strength": stats.evidence_strength,
            },
            "recent_outcomes": [
                {
                    "id": r.id,
                    "outcome": r.outcome,
                    "verification": r.verification,
                    "cost_json": r.cost_json,
                    "latency_ms": r.latency_ms,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "evidence_refs": json.loads(r.evidence_refs_json or "[]"),
                }
                for r in recent
            ],
        }
