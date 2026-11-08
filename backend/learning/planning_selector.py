"""PlanningPolicySelector — deterministic planning/replanning policy (§8-11, §19).

The selector never authorizes privileged actions; it only recommends policies.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.learning.planning_taxonomy import (
    PlanningStrategy,
    ReplanTrigger,
    DEFAULT_RETRY_REPLAN_POLICY,
)
from backend.learning.planning_store import PlanningOutcomeStore


@dataclass
class PlanningPolicyResult:
    selected_policy: str
    confidence: Optional[float]
    evidence_strength: str
    evidence_summary: Dict[str, Any]
    alternatives: List[Dict[str, Any]]
    reason: str
    mode: str = "EXPLOIT"  # EXPLOIT | EXPLORE
    score: Optional[float] = None

    def to_dict(self) -> Dict[str, Any]:
        return {
            "selected_policy": self.selected_policy,
            "confidence": self.confidence,
            "evidence_strength": self.evidence_strength,
            "evidence_summary": self.evidence_summary,
            "alternatives": self.alternatives,
            "reason": self.reason,
            "mode": self.mode,
            "score": self.score,
        }


def _sanitize_planning_strategy(value: str) -> Optional[str]:
    """Normalize *value* to a canonical planning strategy.

    Returns the upper-cased, stripped *value* when it names a known
    ``PlanningStrategy``, otherwise ``None`` (unknown/invalid input).
    """
    normalized = str(value).strip().upper()
    if normalized in PlanningStrategy.values():
        return normalized
    return None


def _sanitize_trigger(value: str) -> Optional[str]:
    """Normalize *value* to a canonical replan trigger.

    Returns the upper-cased, stripped *value* when it names a known
    ``ReplanTrigger``, otherwise ``None`` (unknown/invalid input).
    """
    normalized = str(value).strip().upper()
    if normalized in ReplanTrigger.values():
        return normalized
    return None


def _risk_value(risk: str | None) -> int:
    """Return the 0-4 ordinal for *risk* (``NONE``=0 ... ``CRITICAL``=4).

    ``None`` / missing / unknown values default to ``LOW`` (1).
    """
    RISK_ORDER = {"NONE": 0, "LOW": 1, "MEDIUM": 2, "HIGH": 3, "CRITICAL": 4}
    return RISK_ORDER.get(str(risk or "LOW").upper(), 1)


class PlanningPolicySelector:
    """Deterministic selector over the planning strategy taxonomy (§8).

    Ordering: hard constraints → capability availability → Goal/Plan validity
              → budget/risk → historical evidence → safe deterministic fallback.

    Learning never overrides user approval, Runtime policy, Goal terminal state,
    or verification requirements.
    """

    def __init__(self, session, store: Optional[PlanningOutcomeStore] = None):
        self.db = session
        self.store = store or PlanningOutcomeStore(session)

    def select(
        self,
        task_context: Dict[str, Any],
        current_plan: Optional[Dict[str, Any]] = None,
        failure_context: Optional[Dict[str, Any]] = None,
        *,
        candidates: Optional[List[str]] = None,
        hard_constraints: Optional[List[Dict[str, Any]]] = None,
        available_capabilities: Optional[List[str]] = None,
        risk_level: Optional[str] = None,
        budget_remaining: Optional[float] = None,
    ) -> PlanningPolicyResult:
        """Select a planning policy deterministically.

        task_context: {task_class, goal_type, estimated_steps, dependency_complexity, risk_level, capability_count, delegation_available}
        failure_context: {replan_trigger, previous_failure_class, retries, replans}
        candidates: list of PlanningStrategy strings (if None, all strategies considered)
        hard_constraints: [{strategy, violated, reason}]
        available_capabilities: list of capability ids actually available
        """
        from backend.core.config import settings

        # Normalize candidates to planning strategies
        all_strats = candidates or PlanningStrategy.values()
        norm: List[str] = []
        for c in all_strats:
            s = _sanitize_planning_strategy(c)
            if s:
                norm.append(s)
        if not norm:
            return PlanningPolicyResult(
                selected_policy=PlanningStrategy.DIRECT_PLAN.value,
                confidence=None,
                evidence_strength="UNRATED",
                evidence_summary={"reason": "no valid candidates; default"},
                alternatives=[],
                reason="no valid candidates",
            )

        # Veto by hard constraints (§9)
        vetoed: Dict[str, str] = {}
        if hard_constraints:
            for hc in hard_constraints:
                if hc.get("violated"):
                    vet = str(hc.get("strategy") or hc.get("planning_strategy") or "").upper()
                    if vet:
                        vetoed[vet] = hc.get("reason") or "hard constraint"

        # Veto by capability compatibility (§9)
        # DELEGATED_PLAN requires delegation_available
        task_ctx = task_context or {}
        if task_ctx.get("delegation_available") is False and "DELEGATED_PLAN" in norm:
            # Only veto if explicitly unavailable
            pass  # handled via hard_constraints or explicit flag; not auto-vetoed blindly
        # Generic capability gate: if candidate requires capabilities not available, veto
        # For planning, capability check is informational — only veto if explicit unavailable list provided
        if available_capabilities is not None:
            caps = set(available_capabilities)
            # DELEGATED_PLAN requires at least one specialist capability
            if "DELEGATED_PLAN" in norm and not any(c.startswith("specialist") or c in caps for c in ["delegation", "specialist"]):
                # Don't auto-veto — specialist enablement is separate from capability list
                pass

        # Goal/Plan validity gate (§9)
        if current_plan is not None:
            # Invalid plan forces deterministic fallback to replan
            plan_valid = current_plan.get("valid", True)
            if not plan_valid and "REPLAN_AFTER_FAILURE" not in vetoed:
                # Prefer replan deterministically when plan is invalid
                pass

        # Budget gate (§9)
        if budget_remaining is not None and float(budget_remaining) <= 0:
            # No budget — cannot recommend delegation (costly)
            if "DELEGATED_PLAN" in norm:
                vetoed["DELEGATED_PLAN"] = "budget exhausted"

        eligible = [c for c in norm if c not in vetoed]
        if not eligible:
            return PlanningPolicyResult(
                selected_policy=norm[0],
                confidence=None,
                evidence_strength="UNRATED",
                evidence_summary={"vetoed": vetoed},
                alternatives=[{"planning_strategy": c, "reason": vetoed.get(c, "vetoed")} for c in norm],
                reason="all candidates vetoed by hard constraints/capability/availability",
            )

        # Gather evidence (§6-7, §10)
        task_class = (task_context or {}).get("task_class") or "general"
        failure_trigger = None
        if failure_context:
            failure_trigger = _sanitize_trigger(failure_context.get("replan_trigger") or failure_context.get("failure_class") or "")

        scored: List[Dict[str, Any]] = []
        for stype in eligible:
            stats = self.store.statistics(task_class=task_class, planning_strategy=stype)
            # Filter by trigger if failure context present: prefer strategies with evidence for that trigger
            if failure_trigger:
                trig_stats = self.store.statistics(task_class=task_class, planning_strategy=stype, replan_trigger=failure_trigger)
                # blended score: overall + trigger-specific small boost
                score = self._score(stats) + (0.05 if (trig_stats.sample_count or 0) > 0 else 0)
            else:
                score = self._score(stats)
            scored.append({"planning_strategy": stype, "stats": stats, "score": score})

        def _rank_key(s):
            return (-s["score"], -s["stats"].sample_count, s["planning_strategy"])

        scored.sort(key=_rank_key)
        best = scored[0]
        best_stats = best["stats"]

        # Confidence vs evidence strength separation (§10)
        if best_stats.evidence_strength in ("UNRATED", "LOW_SAMPLE"):
            confidence = None
        else:
            sr = best_stats.success_rate or 0.0
            confidence = round(min(1.0, sr * 0.9 + 0.1), 3) if best_stats.sample_count >= 5 else None

        evidence_summary = {
            "task_class": task_class,
            "planning_strategy": best["planning_strategy"],
            "sample_count": best_stats.sample_count,
            "success_rate": best_stats.success_rate,
            "verified_success_rate": best_stats.verified_success_rate,
            "failure_rate": best_stats.failure_rate,
            "mean_latency_ms": best_stats.mean_latency_ms,
            "mean_cost": best_stats.mean_cost,
            "evidence_strength": best_stats.evidence_strength,
            "recent_count": best_stats.recent_count,
            "decayed_count": best_stats.decayed_count,
            "replan_trigger": failure_trigger,
            "vetoed": vetoed,
        }

        alternatives = [
            {
                "planning_strategy": s["planning_strategy"],
                "score": round(s["score"], 4),
                "sample_count": s["stats"].sample_count,
                "evidence_strength": s["stats"].evidence_strength,
                "success_rate": s["stats"].success_rate,
            }
            for s in scored[1:]
        ]

        # Exploration bounds (§19)
        mode = "EXPLOIT"
        risk = risk_level or (task_context or {}).get("risk_level") or "LOW"
        exploration_allowed = True
        try:
            high_threshold = getattr(settings, "planning_high_risk_threshold", "HIGH")
            if _risk_value(risk) >= _risk_value(high_threshold):
                exploration_allowed = False
        except Exception:
            pass
        if best_stats.evidence_strength == "ESTABLISHED" and confidence and confidence >= float(getattr(settings, "planning_min_confidence_to_exploit", 0.65)):
            exploration_allowed = False
        # Budget gate for exploration
        if budget_remaining is not None and float(budget_remaining) < 1.0:
            exploration_allowed = False

        if exploration_allowed and len(scored) > 1:
            try:
                rate = float(getattr(settings, "planning_exploration_rate", 0.05))
                import hashlib
                bucket = datetime.utcnow().strftime("%Y-%m-%d")
                h = hashlib.sha256(f"planning:{task_class}:{bucket}".encode()).hexdigest()
                frac = int(h[:4], 16) / 65535.0
                max_cost = float(getattr(settings, "planning_max_exploration_cost", 2.0))
                alt_cost = scored[1]["stats"].mean_cost
                alt_cost_ok = alt_cost is None or alt_cost <= max_cost
                if frac < rate and alt_cost_ok:
                    mode = "EXPLORE"
                    best = scored[1]
                    best_stats = best["stats"]
                    evidence_summary = {
                        "task_class": task_class,
                        "planning_strategy": best["planning_strategy"],
                        "sample_count": best_stats.sample_count,
                        "success_rate": best_stats.success_rate,
                        "evidence_strength": best_stats.evidence_strength,
                        "exploration": True,
                        "vetoed": vetoed,
                    }
                    confidence = None
            except Exception:
                pass

        # Deterministic fallback when evidence is insufficient (§10-11)
        if best_stats.evidence_strength in ("UNRATED", "LOW_SAMPLE"):
            # Use the deterministic default policy for the failure class
            if failure_trigger and failure_trigger in DEFAULT_RETRY_REPLAN_POLICY:
                fallback_action = DEFAULT_RETRY_REPLAN_POLICY[failure_trigger]
                # Map action to strategy preference
                if fallback_action == "replan":
                    # Prefer a replan strategy if available and not vetoed
                    for cand in ["REPLAN_AFTER_FAILURE", "REPLAN_BEFORE_RETRY", "DEPENDENCY_AWARE_PLAN"]:
                        if cand in eligible:
                            # Only use fallback if learned best is not supported
                            best = next((s for s in scored if s["planning_strategy"] == cand), best)
                            break
                    best = best  # keep scored best; fallback is informational in reason
                    reason = (
                        f"insufficient evidence (n={best_stats.sample_count}, strength={best_stats.evidence_strength}); "
                        f"deterministic fallback prefers {fallback_action} for trigger {failure_trigger}"
                    )
                else:
                    reason = (
                        f"insufficient evidence (n={best_stats.sample_count}, strength={best_stats.evidence_strength}); "
                        f"deterministic fallback: {fallback_action} for trigger {failure_trigger}"
                    )
            else:
                reason = (
                    f"highest deterministic score {round(best['score'], 4)} "
                    f"(evidence_strength={best_stats.evidence_strength}, n={best_stats.sample_count}) — insufficient evidence, deterministic fallback active"
                )
        else:
            reason = (
                f"highest deterministic score {round(best['score'], 4)} "
                f"(evidence_strength={best_stats.evidence_strength}, n={best_stats.sample_count})"
                if mode == "EXPLOIT"
                else f"controlled exploration: trying alternative {best['planning_strategy']} "
                     f"(evidence_strength={best_stats.evidence_strength})"
            )

        return PlanningPolicyResult(
            selected_policy=best["planning_strategy"],
            confidence=confidence,
            evidence_strength=best_stats.evidence_strength,
            evidence_summary=evidence_summary,
            alternatives=alternatives,
            reason=reason,
            mode=mode,
            score=round(best["score"], 4),
        )

    def _score(self, stats) -> float:
        """Cost-aware deterministic scoring for planning strategies."""
        # Weights: prefer verified success, penalize cost/latency/failure
        success = stats.success_rate if stats.success_rate is not None else 0.5
        verification = stats.verified_success_rate if stats.verified_success_rate is not None else (0.5 if stats.sample_count else 0.0)
        if stats.mean_latency_ms is None:
            latency_cost = 0.5
        else:
            latency_cost = min(1.0, stats.mean_latency_ms / 8000.0)
        if stats.mean_cost is None:
            cost_component = 0.5
        else:
            cost_component = min(1.0, stats.mean_cost / 5.0)
        failure_risk = stats.failure_rate if stats.failure_rate is not None else 0.5
        raw = (
            0.40 * success
            + 0.20 * verification
            - 0.15 * latency_cost
            - 0.10 * cost_component
            - 0.15 * failure_risk
        )
        if stats.sample_count == 0:
            raw += 0.05
        return max(0.0, min(1.5, raw))

    def recommend_for_failure(self, task_context: Dict[str, Any], replan_trigger: str) -> str:
        """Deterministic retry-vs-replan recommendation for a failure class (§6-7)."""
        trigger = _sanitize_trigger(replan_trigger) or ReplanTrigger.EXECUTION_FAILURE.value
        return DEFAULT_RETRY_REPLAN_POLICY.get(trigger, "retry")

    def provenance_for(self, planning_strategy: str, task_class: str = "general") -> Dict[str, Any]:
        stats = self.store.statistics(task_class=task_class, planning_strategy=planning_strategy)
        recent = self.store.list_outcomes(task_class=task_class, planning_strategy=planning_strategy, limit=10)
        import json as _json
        return {
            "planning_strategy": planning_strategy,
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
                    "planning_strategy": r.planning_strategy,
                    "replan_trigger": r.replan_trigger,
                    "created_at": r.created_at.isoformat() if r.created_at else None,
                    "evidence_refs": _json.loads(r.evidence_refs_json or "[]"),
                }
                for r in recent
            ],
        }
