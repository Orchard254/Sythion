"""CognitivePipeline (Phase 8): bounded UNDERSTAND→CONTEXT→REASON→DECIDE flow.

Produces a CognitiveState + SelectedDecision proposal WITHOUT executing
anything. Execution (if any) remains a Runtime decision made by existing
components. Learning hooks emit structured outcome events for future phases.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from backend.cognitive.decision import (
    ConstraintSet,
    DecisionSystem,
    HardConstraint,
    SimulationEngine,
    SoftPreference,
)
from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.reasoning import ReasoningEngine
from backend.cognitive.state import (
    AmbiguityLevel,
    CandidateAction,
    CognitiveState,
    ConfidenceBreakdown,
    DecisionType,
    EpistemicLabel,
    IntentCategory,
    IntentResult,
    RiskLevel,
    SelectedDecision,
)
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

# Clarification policy: ask only when ambiguity is material; safe/social
# exchanges proceed without asking. This is THE clarification authority —
# callers must not re-implement their own gate on top of it.
_CLARIFICATION_AMBIGUITY = {AmbiguityLevel.AMBIGUOUS,
                            AmbiguityLevel.HIGH_IMPACT_AMBIGUITY}
_HIGH_IMPACT_RISK = RiskLevel.HIGH
_LONG_INPUT_WORD_THRESHOLD = 40

# Named summary/candidate slice limits (values unchanged).
_REASONING_SUMMARY_UNKNOWN_LIMIT = 3
_CANDIDATE_DESCRIPTION_LIMIT = 200
_CANDIDATE_GOAL_DESCRIPTION_LIMIT = 150
_CANDIDATE_OPTIONS_LIMIT = 4
_DECISION_EVENT_VETOED_LIMIT = 4

# Deterministic routing policy (Phase 8.75): when does a request deserve the
# FULL reasoning path (LLM-backed ReasoningEngine) versus the LIGHTWEIGHT
# path (deterministic candidates still evaluated by the DecisionSystem)?
# Deep cognition is intentional, never accidental — and never wasted on
# trivial deterministic requests.
_FULL_REASONING_CATEGORIES = {IntentCategory.GOAL}
_LIGHTWEIGHT_CATEGORIES = {
    IntentCategory.SOCIAL, IntentCategory.CORRECTION,
    IntentCategory.CLARIFICATION,
}
_HIGH_IMPACT_VERBS = re.compile(
    r"\b(delete|remove all|format|wipe|drop|overwrite|deploy|purchase|pay|"
    r"send|email|post|publish|install|uninstall|shutdown|restart|kill)\b",
    re.I)
_MULTI_STEP_MARKERS = re.compile(
    r"\b(then|after that|afterwards|step \d|first .+ (and|then) )\b|[;]", re.I)
# Destructive verbs: a matching COMMAND must go through explicit permission,
# never straight to execution (deterministic; runtime policy still applies).
_DESTRUCTIVE_VERBS = re.compile(
    r"\b(delete|remove|format|wipe|drop|overwrite|uninstall|kill)\b", re.I)
# Phase 9 structural delegation signals: multi-part analytical work where a
# bounded specialist adds value. The LLM may recommend delegation, but these
# deterministic signals + runtime eligibility decide whether DELEGATE is even
# a candidate.
_ANALYSIS_VERBS = re.compile(
    r"\b(analy[sz]e|compare|evaluate|research|investigate|review|assess|"
    r"which .*(best|better)|trade-?offs?)\b", re.I)


def delegation_signals(user_input: str) -> dict:
    """Deterministic complexity signals used for delegation eligibility."""
    text = user_input or ""
    return {
        "multi_step": bool(_MULTI_STEP_MARKERS.search(text)),
        "analysis_oriented": bool(_ANALYSIS_VERBS.search(text)),
        "long": len(text.split()) > _LONG_INPUT_WORD_THRESHOLD,
    }


def requires_full_reasoning(intent: IntentResult, user_input: str) -> bool:
    """Deterministic policy: TRUE → run the LLM-backed ReasoningEngine."""
    category = intent.category
    if category in _LIGHTWEIGHT_CATEGORIES:
        return False
    if category in _FULL_REASONING_CATEGORIES:
        return True
    text = user_input or ""
    return bool(
        _HIGH_IMPACT_VERBS.search(text)
        or _MULTI_STEP_MARKERS.search(text)
        or len(text.split()) > _LONG_INPUT_WORD_THRESHOLD
    )


def reasoning_depth(user_input: str) -> str:
    """Public name for the routing outcome ("lightweight" | "full")."""
    interpreter = IntentInterpreter()
    intent = interpreter.interpret(user_input)
    return "full" if requires_full_reasoning(intent, user_input) \
        else "lightweight"


@dataclass
class PipelineResult:
    state: CognitiveState
    intent: Optional[IntentResult]
    decision: Optional[SelectedDecision]
    reasoning_summary: Optional[Dict[str, Any]]
    needs_clarification: bool = False
    simulation: Optional[Dict[str, Any]] = None
    vetoed: Optional[List[Dict[str, str]]] = None


class CognitivePipeline:
    def __init__(self, session, interpreter=None, reasoner=None,
                 decision_system=None):
        self.db = session
        self.interpreter = interpreter or IntentInterpreter()
        self.reasoner = reasoner or ReasoningEngine(session)
        self.decisions = decision_system or DecisionSystem()
        self.simulation = SimulationEngine()
        self.ledger = DbEventLedger(session)

    # ------------------------------------------------------------------ #

    def process(self, user_input: str, *, include_provider_health=False,
                intent: Optional[IntentResult] = None,
                depth: Optional[str] = None) -> PipelineResult:
        """Run one bounded cognitive cycle. Never executes actions.

        ``depth`` selects the reasoning path deterministically:
          - "lightweight": deterministic artifact, no LLM call (candidates
            are still generated and evaluated by the DecisionSystem);
          - "full": LLM-backed ReasoningEngine via ModelSelector;
          - None: decided by the routing policy (requires_full_reasoning).
        """
        state = CognitiveState(user_input=user_input)

        # 1. UNDERSTAND ---------------------------------------------------
        intent = intent or self.interpreter.interpret(user_input)
        state.intent = intent
        state.uncertainty = intent.ambiguity
        state.add_context("USER_INPUT", user_input, EpistemicLabel.KNOWN)

        # 2. CHECK_CONTEXT --------------------------------------------------
        self._assemble_context(state)

        # Phase 13A: Material ambiguity in actionable requests → clarification beats guessing.
        # Open-ended conversation, social dialogue, and informational questions NEVER trigger clarification.
        # Ambiguous conversation remains conversational.
        is_actionable = intent.category in (IntentCategory.COMMAND, IntentCategory.GOAL)
        if is_actionable and (
                intent.needs_clarification
                or intent.ambiguity in _CLARIFICATION_AMBIGUITY):
            decision = SelectedDecision(
                decision_type=DecisionType.ASK_CLARIFICATION,
                params={"question": "Could you clarify what you'd like me to do?"},
                justification="actionable input lacks concrete parameters; clarification "
                              "preferred over guessing",
                confidence=ConfidenceBreakdown(
                    evidence=None,
                    interpretation=intent.confidence,
                    decision=None),
                risk=RiskLevel.NONE,
            )
            state.selected = decision
            self._emit_decision_event(state)
            return PipelineResult(state, intent, decision, None,
                                  needs_clarification=True)

        # 3. REASON ----------------------------------------------------------
        effective_depth = depth or (
            "full" if requires_full_reasoning(intent, user_input)
            else "lightweight")
        if effective_depth == "full":
            artifact = self.reasoner.analyze(state)
        else:
            from backend.cognitive.reasoning import _fallback_artifact

            artifact = _fallback_artifact(
                state, "lightweight deterministic path (routing policy)")
        reasoning_summary = {
            "depth": effective_depth,
            "recommended_direction": artifact.recommended_direction,
            "unknowns": artifact.unknowns[:_REASONING_SUMMARY_UNKNOWN_LIMIT],
            "options": len(artifact.candidate_options),
        }

        # 4./5. DECIDE --------------------------------------------------------
        candidates = self._deterministic_candidates(state, artifact)
        state.candidates = candidates
        constraints = self._default_constraints()
        # Phase 11: historical strategy evidence enriches selection but never
        # overrides hard constraints / capability vetoes. The selector is
        # consulted before DecisionSystem.evaluate to provide an evidence-aware
        # tie-breaker; the runtime remains authoritative.
        strategy_guidance = self._strategy_guidance(state, candidates)
        # Capability honesty is evaluated against ACTUAL runtime capabilities
        # observed in step 2 — never against model claims.
        evaluation = self.decisions.evaluate(
            candidates, constraints,
            available_capabilities=list(state.capabilities_available),
            confidence=ConfidenceBreakdown(
                evidence=self._evidence_confidence(artifact),
                interpretation=intent.confidence,
                decision=None))
        # Apply non-overriding evidence tie-breaker when scores are close:
        # if the selector's top pick is among the scored candidates and scores
        # differ by < 0.05, surface the historically better strategy.
        if strategy_guidance is not None and evaluation.scored:
            sel_strategy = strategy_guidance.get("strategy")
            if sel_strategy:
                # map strategy back to candidate action_id pattern
                for s in evaluation.scored:
                    cand = s["candidate"]
                    mapped = self._map_candidate_to_strategy(cand)
                    if mapped == sel_strategy and s["score"] >= max(x["score"] for x in evaluation.scored) - 0.05:
                        # re-order scored so evidence-preferred is first, but
                        # do NOT bypass vetoes or hard policy
                        evaluation.scored.sort(key=lambda x: (
                            0 if self._map_candidate_to_strategy(x["candidate"]) == sel_strategy else 1,
                            -x["score"]
                        ))
                        best = evaluation.scored[0]["candidate"]
                        from backend.cognitive.state import ConfidenceBreakdown as _CB, SelectedDecision as _SD
                        evaluation.selected = _SD(
                            decision_type=best.decision_type,
                            params={"description": best.description,
                                    "expected_outcome": best.expected_outcome,
                                    "action_id": best.action_id,
                                    "strategy_evidence": strategy_guidance},
                            justification=(
                                f"highest deterministic score {evaluation.scored[0]['score']} "
                                f"(strategy evidence: {sel_strategy})"),
                            confidence=ConfidenceBreakdown(
                                evidence=self._evidence_confidence(artifact),
                                interpretation=intent.confidence,
                                decision=min(1.0, evaluation.scored[0]["score"]),
                            ),
                            risk=best.risk,
                            candidate=best,
                        )
                        break

        selected = self._category_default(state, evaluation)
        state.selected = selected

        # 6. SIMULATE (dry-run only): high-impact selections get a bounded
        # prediction pass before anything reaches the runtime boundary.
        simulation = None
        if selected.candidate is not None and \
                selected.risk.at_least(RiskLevel.HIGH):
            sim = self.simulation.evaluate(selected.candidate, state)
            simulation = {
                "action_id": sim.action_id,
                "predicted_effects": sim.predicted_effects,
                "risk": sim.risk.value,
                "reversible": sim.reversible,
                "simulated": True,
            }
            selected.params["simulation"] = simulation

        self._emit_decision_event(state, vetoed=evaluation.vetoed,
                                  simulation=simulation)

        return PipelineResult(state, intent, selected, reasoning_summary,
                              needs_clarification=False,
                              simulation=simulation,
                              vetoed=list(evaluation.vetoed))

    # ------------------------------------------------------------------ #

    def _assemble_context(self, state: CognitiveState) -> None:
        try:
            from backend.services.personal_context import PersonalContextBuilder

            context = PersonalContextBuilder(self.db).build(
                query=state.user_input)
            if context["rendered"]:
                state.add_context("PERSONAL_CONTEXT", context["rendered"],
                                  EpistemicLabel.REMEMBERED)
            state.relevant_memory = (
                context["preferences"] + context["facts"])
        except Exception as exc:
            logger.warning("Personal context unavailable: %s", exc)

        try:
            from backend.services.goal_manager import GoalManager

            manager = GoalManager(self.db)
            goals = manager.list_goals(status="active", limit=5)
            for g in goals:
                state.active_goals.append(
                    {"id": g.id, "title": g.title, "status": g.status})
            if goals:
                state.add_context("GOAL_CONTEXT", "\n".join(
                    f"- [{g.status}] {g.title}" for g in goals),
                    EpistemicLabel.KNOWN)
        except Exception as exc:
            logger.warning("Goal context unavailable: %s", exc)

        try:
            from backend.api.dependencies import get_tool_executor

            executor = get_tool_executor()
            state.capabilities_available = sorted(executor.list_tools())
        except Exception as exc:
            logger.warning("Capability context unavailable: %s", exc)

        # SelfModel truth: canonical capability IDs (the vocabulary candidates
        # use in requires_capabilities) resolved against ACTUAL runtime state.
        try:
            from backend.services.capabilities import CapabilityRegistry

            registry = CapabilityRegistry(self.db)
            for spec in registry.list_capabilities(include_details=True):
                if spec.get("status") == "AVAILABLE":
                    state.capabilities_available.append(spec["id"])
            state.capabilities_available = sorted(
                set(state.capabilities_available))
        except Exception as exc:
            logger.warning("Capability registry unavailable: %s", exc)

    def _deterministic_candidates(
        self, state: CognitiveState, artifact
    ) -> List[CandidateAction]:
        """Deterministic candidate generation from category + reasoning.

        The LLM may enrich options; structural candidates come from rules.
        """
        from backend.services.model_selection import requirements_for_task_class

        category = state.intent.category if state.intent else None
        cands: List[CandidateAction] = []
        common = dict(reversible=True, risk=RiskLevel.NONE,
                      requires_capabilities=[])

        if category == IntentCategory.SOCIAL:
            cands.append(CandidateAction(
                action_id="social_reply",
                decision_type=DecisionType.ANSWER,
                description="conversational reply",
                **common))
        elif category in (IntentCategory.INFORMATIONAL, IntentCategory.FOLLOW_UP):
            cands.append(CandidateAction(
                action_id="answer_question",
                decision_type=DecisionType.ANSWER,
                description="answer using available knowledge/context",
                **common))
        elif category == IntentCategory.COMMAND:
            if _DESTRUCTIVE_VERBS.search(state.user_input):
                # Destructive command: the ONLY candidate is permission.
                cands.append(CandidateAction(
                    action_id="request_permission",
                    decision_type=DecisionType.REQUEST_PERMISSION,
                    description=state.user_input[:_CANDIDATE_DESCRIPTION_LIMIT],
                    risk=RiskLevel.HIGH, reversible=False,
                    requires_permission=True,
                    evidence_refs=["user_request"],
                    expected_outcome="explicit user consent before execution"))
            else:
                cands.append(CandidateAction(
                    action_id="execute_task",
                    decision_type=DecisionType.EXECUTE_STEP,
                    description=state.user_input[:_CANDIDATE_DESCRIPTION_LIMIT],
                    risk=RiskLevel.LOW, reversible=True,
                    requires_capabilities=["system_observation"],
                    **{"expected_outcome": "task executed via runtime"}))
        elif category == IntentCategory.GOAL:
            cands.append(CandidateAction(
                action_id="create_goal",
                decision_type=DecisionType.CREATE_GOAL,
                description=f"create long-running goal: {state.user_input[:_CANDIDATE_GOAL_DESCRIPTION_LIMIT]}",
                risk=RiskLevel.LOW,
                expected_outcome="goal + validated plan created"))
            if state.active_goals:
                cands.append(CandidateAction(
                    action_id="update_goal",
                    decision_type=DecisionType.UPDATE_GOAL,
                    description="update progress on an active goal",
                    risk=RiskLevel.LOW, reversible=True,
                    evidence_refs=["goal_context"],
                    expected_outcome="existing goal advanced"))
        elif category == IntentCategory.CORRECTION:
            cands.append(CandidateAction(
                action_id="acknowledge_correction",
                decision_type=DecisionType.ANSWER,
                description="acknowledge correction; record feedback signal",
                **common))
        elif category == IntentCategory.CLARIFICATION:
            cands.append(CandidateAction(
                action_id="process_clarification",
                decision_type=DecisionType.ANSWER,
                description="process user's clarification answer",
                **common))
        else:
            cands.append(CandidateAction(
                action_id="no_action",
                decision_type=DecisionType.NO_ACTION,
                description="unclear intent; take no action",
                **common))

        # Phase 9: bounded delegation. A DELEGATE candidate appears only when
        # deterministic complexity signals justify it AND runtime eligibility
        # holds (specialists enabled + an enabled role definition exists).
        if category in (IntentCategory.INFORMATIONAL, IntentCategory.COMMAND):
            signals = delegation_signals(state.user_input)
            eligible = False
            try:
                from backend.agents.delegation import delegation_available

                eligible = delegation_available(self.db)
            except Exception as exc:
                logger.warning("Delegation eligibility check failed: %s", exc)
            if eligible and (signals["analysis_oriented"]
                             or (signals["multi_step"] and signals["long"])
                             or signals["long"]):
                cands.append(CandidateAction(
                    action_id="delegate_specialist",
                    decision_type=DecisionType.DELEGATE,
                    description=state.user_input[:_CANDIDATE_DESCRIPTION_LIMIT],
                    risk=RiskLevel.LOW, reversible=True,
                    evidence_refs=["delegation_signals"],
                    expected_outcome="bounded specialist result verified "
                                     "and integrated by the main agent"))

        # Reasoning-proposed options become additional candidates with
        # conservative defaults (the model cannot elevate its own authority).
        for idx, opt in enumerate(getattr(artifact, "candidate_options", [])[:_CANDIDATE_OPTIONS_LIMIT]):
            if not isinstance(opt, dict) or not opt.get("description"):
                continue
            risk_raw = str(opt.get("risk", "LOW")).upper()
            risk = RiskLevel(risk_raw) if risk_raw in RiskLevel.__members__ \
                else RiskLevel.MEDIUM
            cands.append(CandidateAction(
                action_id=str(opt.get("id", f"llm_opt_{idx}")),
                decision_type=DecisionType.EXECUTE_STEP,
                description=str(opt.get("description", ""))[:_CANDIDATE_DESCRIPTION_LIMIT],
                risk=risk,
                reversible=bool(opt.get("reversible", True)),
                evidence_refs=["reasoning_artifact"],
            ))
        return cands

    @staticmethod
    def _default_constraints() -> ConstraintSet:
        """HARD constraints dominate preferences — the LLM cannot redefine."""
        hard_text = [
            "never execute destructive actions without runtime approval",
            "never bypass policy/verification/lease/budgets",
            "never exfiltrate secrets",
        ]
        cs = ConstraintSet()
        cs.hard = [HardConstraint(text=t) for t in hard_text]
        # Enforceable variant: any destructive candidate lacking the
        # permission flag is vetoed regardless of its score.
        cs.hard.append(HardConstraint(
            text="no destructive action without explicit permission",
            check=lambda c: bool(_DESTRUCTIVE_VERBS.search(
                str(getattr(c, "description", "") or "")))
            and not c.requires_permission))
        cs.soft = [
            SoftPreference("prefer concise responses"),
            SoftPreference("prefer reversible low-risk actions"),
        ]
        return cs

    @staticmethod
    def _evidence_confidence(artifact) -> Optional[float]:
        facts = getattr(artifact, "relevant_facts", [])
        if not facts:
            return None
        strong = sum(1 for f in facts
                     if f.get("epistemic") in ("KNOWN", "OBSERVED"))
        return min(1.0, strong / max(1, len(facts)))

    def _category_default(self, state: CognitiveState,
                          evaluation: Any) -> SelectedDecision:
        """Deterministic fallback when no candidate scores: map category."""
        if evaluation.selected is not None:
            return evaluation.selected
        category = state.intent.category if state.intent else None
        dtype = {
            IntentCategory.INFORMATIONAL: DecisionType.ANSWER,
            IntentCategory.SOCIAL: DecisionType.ANSWER,
            IntentCategory.CORRECTION: DecisionType.ANSWER,
            IntentCategory.FOLLOW_UP: DecisionType.ANSWER,
            IntentCategory.CLARIFICATION: DecisionType.ANSWER,
            IntentCategory.FEEDBACK: DecisionType.ANSWER,
        }.get(category, DecisionType.NO_ACTION)
        conf = ConfidenceBreakdown(evidence=None, interpretation=None, decision=None)
        return SelectedDecision(
            decision_type=dtype,
            params={"description": "deterministic category default"},
            justification=f"no viable candidate; category default ({dtype})",
            confidence=conf, risk=RiskLevel.NONE)

    def _map_candidate_to_strategy(self, cand) -> Optional[str]:
        from backend.cognitive.state import DecisionType as _DT
        dt = getattr(cand, "decision_type", "")
        aid = getattr(cand, "action_id", "")
        if dt == _DT.DELEGATE:
            # heuristic role mapping via description lower
            desc = (getattr(cand, "description", "") or "").lower()
            if "research" in desc:
                return "DELEGATE_RESEARCH"
            if "code" in desc or "script" in desc:
                return "DELEGATE_CODING"
            if "review" in desc:
                return "DELEGATE_REVIEW"
            if "plan" in desc:
                return "DELEGATE_PLANNING_SUPPORT"
            return "DELEGATE_ANALYSIS"
        if dt == _DT.ASK_CLARIFICATION:
            return "ASK_CLARIFICATION"
        if dt in (_DT.EXECUTE_STEP, _DT.EXECUTE_DIRECTLY):
            return "DIRECT_EXECUTION"
        if dt == _DT.ANSWER:
            return "DIRECT_ANSWER"
        if "multi" in aid.lower():
            return "MULTI_SPECIALIST"
        return None

    def _strategy_guidance(self, state: CognitiveState, candidates: List[Any]) -> Optional[Dict[str, Any]]:
        try:
            from backend.learning.selector import StrategySelector
            from backend.core.config import settings
            if not getattr(settings, "strategy_learning_enabled", True):
                return None
            # Build candidate strategy list for selector
            strat_candidates = []
            for c in candidates:
                mapped = self._map_candidate_to_strategy(c)
                if mapped:
                    strat_candidates.append({"strategy": mapped, "requires_capabilities": list(getattr(c, "requires_capabilities", []) or [])})
            if not strat_candidates:
                return None
            # Context matching via structured task features (section 12)
            task_class = (state.intent.category.value if state.intent else "general")
            availability: Dict[str, bool] = {}
            try:
                from backend.agents.delegation import delegation_available
                avail = delegation_available(self.db)
                for s in ["DELEGATE_RESEARCH","DELEGATE_ANALYSIS","DELEGATE_CODING","DELEGATE_REVIEW","DELEGATE_PLANNING_SUPPORT","MULTI_SPECIALIST"]:
                    availability[s] = bool(avail)
            except Exception:
                pass
            selector = StrategySelector(self.db)
            result = selector.select(
                task_context={"task_class": task_class, "risk_level": getattr(state.selected, "risk", None) and state.selected.risk.value if isinstance(getattr(state.selected, "risk", None), object) else "LOW"},
                candidates=strat_candidates,
                available_capabilities=list(state.capabilities_available or []),
                strategy_availability=availability,
            )
            return result.to_dict()
        except Exception as exc:
            logger.debug("Strategy guidance unavailable: %s", exc)
            return None

    def _emit_decision_event(self, state: CognitiveState,
                             vetoed: Optional[List[Dict[str, str]]] = None,
                             simulation: Optional[Dict[str, Any]] = None) -> None:
        """Learning hook: structured outcome metadata for future learning.
        No chain-of-thought; concise machine-readable rationale only."""
        try:
            sel = state.selected
            self.ledger.append_event("cognitive", "cognitive.decision.made", {
                "input": state.user_input[:300],
                "intent_category": (
                    state.intent.category.value if state.intent else None),
                "ambiguity": state.uncertainty.value,
                "decision_type": sel.decision_type if sel else None,
                "risk": sel.risk.value if sel else None,
                "vetoed": (vetoed or [])[:_DECISION_EVENT_VETOED_LIMIT],
                "simulation": simulation,
                "strategy_evidence": (sel.params.get("strategy_evidence") if sel and sel.params else None),
                "confidence": {
                    "evidence": sel.confidence.evidence if sel else None,
                    "interpretation": sel.confidence.interpretation if sel else None,
                    "decision": sel.confidence.decision if sel else None,
                } if sel else None,
            })
            self.db.commit()
        except Exception as exc:  # pragma: no cover - observability is best-effort
            logger.warning("Cognitive event emission failed: %s", exc)
