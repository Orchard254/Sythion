"""Cognitive↔Runtime bridge (Phase 8.5 / completed in Phase 8.75).

The single place where Phase 8 cognitive decisions become live runtime
operations. The adapter uses ONLY authoritative runtime interfaces
(TaskRunner / GoalManager / DbEventLedger); it never executes tools, never
writes task rows directly, and never grants permissions.

Live entry point: ``route_new_input`` is called by POST /tasks BEFORE any
execution starts. It runs the FULL cognitive pipeline under a deterministic
routing policy:

    lightweight requests → deterministic candidates + DecisionSystem
    complex/high-impact  → + LLM-backed ReasoningEngine (ModelSelector path)

The pipeline's SelectedDecision is then bridged to a runtime outcome:

    ASK_CLARIFICATION            → park WAITING_FOR_USER (kind=clarification)
    REQUEST_PERMISSION / HIGH+   → park WAITING_FOR_USER (kind=permission)
    NO_ACTION / REFUSE / DEFER /
    WAIT                         → park WAITING_FOR_USER (kind=wait)
    ANSWER / EXECUTE_STEP /
    CREATE_GOAL / UPDATE_GOAL    → execute via the existing worker loop
                                   (ModelDecisionEngine retained AS WORKER)

Parking is durable: it survives restarts via the normal task lifecycle.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any, Dict, Optional

from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.pipeline import CognitivePipeline
from backend.cognitive.state import (
    CognitiveState,
    DecisionType,
    EpistemicLabel,
    IntentCategory,
    IntentResult,
    InteractionType,
    RiskLevel,
    SelectedDecision,
)
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

CLARIFICATION_KIND = "clarification"

# Decision types that hand control back to the user instead of acting.
_PARK_DECISIONS = {
    DecisionType.NO_ACTION,
    DecisionType.REFUSE,
    DecisionType.DEFER,
    DecisionType.WAIT,
}


@dataclass
class RoutingDecision:
    """Outcome of the live cognitive gate for a new input."""
    mode: str                      # "execute" | "clarify" | "park" | "delegate"
    intent: IntentResult
    decision: Any = None           # SelectedDecision from the pipeline
    clarification: Optional[Dict[str, Any]] = None
    interaction_type: InteractionType = InteractionType.CONVERSATION


class CognitiveDecisionAdapter:
    """Bridges cognitive decisions to existing runtime primitives."""

    def __init__(self, runner, session):
        self.runner = runner          # TaskRunner (TaskStateStore authority)
        self.db = session
        self.interpreter = IntentInterpreter()
        self.ledger = DbEventLedger(session)
        self._pipeline: Optional[CognitivePipeline] = None

    def pipeline(self) -> CognitivePipeline:
        if self._pipeline is None:
            self._pipeline = CognitivePipeline(self.db)
        return self._pipeline

    # ------------------------------------------------------------------ #
    # Live entry point                                                     #
    # ------------------------------------------------------------------ #

    async def route_new_input(self, task_id: str, user_input: str, context: Optional[dict] = None) -> RoutingDecision:
        """Full cognitive gate for a freshly created task.

        One authority: the CognitivePipeline decides; this adapter only
        BRIDGES the selected decision to durable runtime outcomes.
        """
        context = context or {}
        intent = self.interpreter.interpret(user_input, context=context)
        self._emit_interpreted(task_id, intent)

        if intent.category == IntentCategory.CORRECTION:
            self._record_correction(task_id)

        result = self.pipeline().process(user_input, intent=intent)
        decision = result.decision
        dtype = decision.decision_type if decision else None

        interaction_type = self.interpreter.determine_interaction(user_input, intent=intent, context=context)

        # Clarification beats guessing (pipeline is THE clarification
        # authority).
        if result.needs_clarification or \
                dtype == DecisionType.ASK_CLARIFICATION or \
                intent.needs_clarification:
            question = (
                decision.params.get("question")
                if decision and decision.params else None
            ) or "Your request is ambiguous. Could you clarify exactly what " \
                 "you'd like me to do?"
            return RoutingDecision(
                mode="clarify", intent=intent, decision=decision,
                clarification=await self.to_clarification(
                    task_id=task_id, question=question, intent=intent),
                interaction_type=InteractionType.CLARIFICATION_REQUIRED,
            )

        # Permission boundary: high-impact selections require explicit user
        # consent BEFORE execution begins (runtime policy still applies after).
        if dtype == DecisionType.REQUEST_PERMISSION or (
                decision is not None
                and decision.risk.at_least(RiskLevel.HIGH)):
            description = (decision.params.get("description", "")
                           if decision else "")[:300]
            metadata = await self.park(
                task_id=task_id,
                kind="permission",
                question=(f"I'd like approval before proceeding: "
                          f"{description}. May I continue?"),
                extra={
                    "decision_type": dtype,
                    "risk": decision.risk.value if decision else None,
                    "simulation": result.simulation,
                },
            )
            return RoutingDecision(mode="park", intent=intent,
                                   decision=decision,
                                   clarification=metadata,
                                   interaction_type=interaction_type)

        # No viable action: hand control back durably instead of guessing.
        if dtype in _PARK_DECISIONS:
            reason = (decision.justification if decision
                      else "no viable action determined")[:200]
            metadata = await self.park(
                task_id=task_id,
                kind="wait",
                question=("I couldn't determine a safe action for this "
                          "request. Could you rephrase or confirm what you "
                          f"want? ({reason})"),
                extra={"decision_type": dtype},
            )
            return RoutingDecision(mode="park", intent=intent,
                                   decision=decision,
                                   clarification=metadata,
                                   interaction_type=interaction_type)

        # Phase 9: bounded delegation. The cognitive layer decided DELEGATE;
        # this adapter only BRIDGES it to the durable delegation machinery.
        if dtype == DecisionType.DELEGATE:
            return RoutingDecision(mode="delegate", intent=intent,
                                   decision=decision,
                                   interaction_type=interaction_type)

        # ANSWER / EXECUTE_STEP / CREATE_GOAL / UPDATE_GOAL / EXECUTE_DIRECTLY:
        # proceed to the worker loop. The cognitive layer stays authoritative
        # for WHAT to do; the loop (ModelDecisionEngine as WORKER) decides
        # tool-level steps inside the protected runtime.
        return RoutingDecision(mode="execute", intent=intent,
                               decision=decision,
                               interaction_type=interaction_type)

    # ------------------------------------------------------------------ #
    # Phase 9: delegation bridge                                           #
    # ------------------------------------------------------------------ #

    _ROLE_HINTS = (
        ("research", "RESEARCH"), ("find out", "RESEARCH"),
        ("investigate", "RESEARCH"),
        ("code", "CODING"), ("script", "CODING"), ("program", "CODING"),
        ("review", "REVIEW"), ("audit", "REVIEW"),
        ("plan", "PLANNING_SUPPORT"),
    )

    def _select_role(self, user_input: str, task_class: str = "general") -> str:
        """Deterministic role selection — now evidence-informed where sufficient.

        Historical evidence influences the choice but hard role constraints
        (registry enabled check) still dominate. If evidence is UNRATED/LOW_SAMPLE
        we fall back to the deterministic hint path.
        """
        # Try evidence-informed selection first (Phase 11)
        try:
            from backend.core.config import settings as _settings
            if getattr(_settings, "strategy_learning_enabled", True):
                from backend.learning.selector import StrategySelector
                from backend.learning.taxonomy import StrategyType
                # Build candidates for the roles that are actually available
                try:
                    from backend.models.database import SpecialistDefinition
                    enabled_roles = [
                        r.role for r in self.db.query(SpecialistDefinition).filter_by(enabled=True).all()
                    ]
                except Exception:
                    enabled_roles = ["RESEARCH", "ANALYSIS", "CODING", "REVIEW", "PLANNING_SUPPORT"]
                candidates = []
                mapping = {
                    "RESEARCH": StrategyType.DELEGATE_RESEARCH.value,
                    "ANALYSIS": StrategyType.DELEGATE_ANALYSIS.value,
                    "CODING": StrategyType.DELEGATE_CODING.value,
                    "REVIEW": StrategyType.DELEGATE_REVIEW.value,
                    "PLANNING_SUPPORT": StrategyType.DELEGATE_PLANNING_SUPPORT.value,
                }
                for role in enabled_roles:
                    strat = mapping.get(role)
                    if strat:
                        candidates.append({"strategy": strat})
                if candidates:
                    selector = StrategySelector(self.db)
                    result = selector.select(
                        task_context={"task_class": task_class},
                        candidates=candidates,
                    )
                    # Only trust SUPPORTED/ESTABLISHED evidence
                    if result.evidence_strength in ("SUPPORTED", "ESTABLISHED") and result.confidence is not None:
                        chosen = result.strategy
                        for role, strat in mapping.items():
                            if strat == chosen:
                                return role
        except Exception:
            pass
        text = (user_input or "").lower()
        for hint, role in self._ROLE_HINTS:
            if hint in text:
                return role
        return "ANALYSIS"

    async def run_delegate_decision(self, task_id: str, user_input: str):
        """End-to-end bounded delegation for a DELEGATE decision.

        propose → validate → assign → run through the protected kernel →
        verify → Main-Agent integration (ACCEPT). On ACCEPT the PARENT task
        is completed with the integrated result; on any validation failure or
        unverified result the parent task parks in WAITING_FOR_USER instead —
        never a forged completion.
        """
        from backend.agents.delegation import (
            DelegationRequest,
            DelegationService,
        )
        from backend.agents.service import AgentService

        from backend.core.config import settings as _settings

        agents = AgentService(self.db)
        main = agents.get_or_create_main()
        svc = DelegationService(self.db)

        if not _settings.specialists_enabled:
            return await self.park(
                task_id=task_id, kind="wait",
                question="Specialist delegation is currently disabled; "
                         "please retry directly or re-enable it.")

        # Phase 11: evidence-informed role where available
        try:
            from backend.cognitive.pipeline import delegation_signals
            _sig = delegation_signals(user_input)
            _task_class = "informational" if _sig.get("analysis_oriented") else "command"
        except Exception:
            _task_class = "general"
        proposal = svc.propose(DelegationRequest(
            parent_agent_id=main.id,
            specialist_role=self._select_role(user_input, task_class=_task_class),
            objective=user_input,
            task_id=task_id,
            expected_output="structured findings with evidence refs",
        ))
        delegation = proposal["delegation"]
        if delegation is None or proposal.get("error"):
            return await self.park(
                task_id=task_id, kind="wait",
                question="Delegation could not be proposed "
                         f"({proposal.get('error')}); please rephrase.")

        validation = svc.validate(delegation.id)
        if not validation["ok"]:
            return await self.park(
                task_id=task_id, kind="wait",
                question=("Delegation was rejected by policy validation "
                          f"({'; '.join(validation['reasons'])})."))

        svc.assign(delegation.id)

        from backend.api.dependencies import get_tool_executor

        outcome = await svc.run(
            delegation.id, state_store=self.runner,
            executor=get_tool_executor(), assume_lease_held=True)

        if outcome.get("status") == "VERIFIED" and outcome.get("verified"):
            integration = svc.integrate(delegation.id, "ACCEPT",
                                        actor_id=main.id)
            result = outcome.get("result", {})
            cost = outcome.get("cost", {})
            summary = (f"[delegated:{delegation.specialist_role.lower()}] "
                       f"{result.get('summary', '')}")
            findings = "\n".join(f"- {f}"
                                 for f in result.get("findings", [])[:10])
            final = (f"{summary}\n{findings}\n"
                     f"(specialist cost: actions={cost.get('actions', 0)}, "
                     f"model_calls={cost.get('model_calls', 0)}, "
                     f"elapsed={cost.get('elapsed_seconds', 0)}s)")
            from backend.api.schemas import TaskState

            await self.runner.transition_state(
                task_id=task_id, new_state=TaskState.RUNNING,
                event_type="task.execution_started",
                event_data={"delegated": True,
                            "delegation_id": delegation.id},
            )
            await self.runner.transition_state(
                task_id=task_id, new_state=TaskState.COMPLETED,
                event_type="task.completed",
                event_data={"via": "specialist_integration",
                            "delegation_id": delegation.id},
                final_result=final[:2000],
            )
            return {"integrated": True, "delegation_id": delegation.id,
                    "final_result": final}

        # Unverified/failed specialist work must never complete the parent.
        reason = outcome.get("failure_reason") or outcome.get("status") or \
            "specialist result failed verification"
        return await self.park(
            task_id=task_id, kind="wait",
            question=(f"The delegated specialist did not produce a verified "
                      f"result ({str(reason)[:200]}). Would you like me to "
                      f"retry directly?"))

    # ------------------------------------------------------------------ #
    # Decision → runtime bridges                                           #
    # ------------------------------------------------------------------ #

    async def to_clarification(self, *, task_id: str, question: str,
                               intent: Optional[IntentResult] = None) -> Dict[str, Any]:
        """Park a task in WAITING_FOR_USER with durable cognitive metadata."""
        interpretations = [
            {"description": i.description,
             "category": i.category.value,
             "confidence": i.confidence}
            for i in (intent.interpretations if intent else [])
        ]
        return await self.park(
            task_id=task_id, kind=CLARIFICATION_KIND, question=question,
            extra={
                "interpretations": interpretations,
                "ambiguity": intent.ambiguity.value if intent else "UNKNOWN",
                "reason": "materially ambiguous input",
            },
        )

    async def park(self, *, task_id: str, kind: str, question: str,
                   extra: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        """Durable WAITING_FOR_USER parking through the TaskRunner lifecycle.

        No new state mechanism: the existing task lifecycle IS the recovery
        mechanism, so parked cognitive decisions need no transient state.
        """
        import json as _json

        metadata = {
            "kind": kind,
            "question": question,
            "awaiting_user_reply": True,
        }
        if extra:
            metadata.update(extra)

        from backend.api.schemas import TaskState

        await self.runner.transition_state(
            task_id=task_id,
            new_state=TaskState.WAITING_FOR_USER,
            event_type=f"cognitive.{kind}.requested",
            event_data={"question": question,
                        **{k: v for k, v in (extra or {}).items()
                           if k != "interpretations"}},
            pending_question=question,
            pending_action=_json.dumps(metadata),
        )
        return metadata

    async def apply_clarification_answer(self, task_id: str, answer: str) -> None:
        """Merge a user's reply into the task's goal context so resumed
        execution continues the ORIGINAL intent."""
        if not self.runner._verify_lease():
            return  # reply handler owns the lease; nothing to do here
        task = await self.runner.get_task(task_id)
        if task is None:
            return
        merged = f"{task.original_request}\n[clarification] {answer}"
        try:
            task.normalized_goal = merged[:2000]
            task.updated_at = __import__("datetime").datetime.utcnow()
            self.db.commit()
        except Exception as exc:  # pragma: no cover - best-effort enrichment
            logger.warning("Could not merge clarification answer: %s", exc)

    def record_correction_signal(self, task_id: str, detail: str) -> None:
        """Structured learning signal for future phases (no auto-fixes)."""
        self.ledger.append_event("cognitive", "cognitive.correction.recorded", {
            "task_id": task_id, "detail": detail[:300],
        })
        self.db.commit()

    # ------------------------------------------------------------------ #

    def _emit_interpreted(self, task_id: str, intent: IntentResult) -> None:
        self.ledger.append_event("cognitive", "cognitive.input.interpreted", {
            "category": intent.category.value,
            "confidence": intent.confidence,
            "ambiguity": intent.ambiguity.value,
        })

    def _record_correction(self, task_id: str) -> None:
        try:
            from backend.models.database import Task

            previous = (
                self.db.query(Task)
                .filter(Task.state.in_(("completed", "failed")))
                .order_by(Task.updated_at.desc())
                .first()
            )
            self.record_correction_signal(
                task_id,
                detail=f"correction regarding prior task "
                       f"{previous.id if previous else '(none)'}",
            )
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("Correction signal failed: %s", exc)
