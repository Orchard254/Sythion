"""SpecialistReasoner — bounded specialist cognitive loop (Phase 9.5).

Provides genuine bounded cognitive work for specialists, replacing the
Phase 9 deterministic template result.

Design constraints:
- Uses ModelSelector + ModelProvider (provider-neutral); never imports
  OllamaCloudAdapter or raw adapter singletons.
- Respects role-bounded capabilities: a Coding specialist cannot gain
  research tools because the model asked for them.
- Delegation may NARROW capabilities further, never broaden beyond
  Runtime policy (role defaults are the ceiling).
- Not every specialist needs tools: RESEARCH/ANALYSIS/REVIEW may be
  reasoning-only, but the result must be model-derived, not a static template.
- All proposals are validated against capabilities, budget, cancellation,
  target validation, and tool availability — proposals are not authorization.

Flow:
    Delegation
      ↓
    SpecialistContextPacket
      ↓
    Specialist Model Selection (ModelSelector)
      ↓
    Specialist Reasoning (ModelProvider.chat)
      ↓
    Candidate Work (proposed actions / findings)
      ↓
    Specialist Action / Analysis (via SpecialistRuntime kernel)
      ↓
    Evidence
      ↓
    SpecialistResult
"""

from __future__ import annotations

import inspect
import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from backend.agents.state import (
    BudgetExceeded,
    CapabilityViolation,
    ROLE_CAPABILITIES,
    ROLE_MODEL_REQUIREMENTS,
    SpecialistRole,
)
from backend.core.config import settings

logger = logging.getLogger(__name__)


def _supports_kwarg(fn: Any, name: str) -> bool:
    """Whether ``fn`` accepts ``name=...`` according to its signature.

    Provider implementations and test doubles disagree on whether a model
    identifier may be supplied to ``chat``. The active chat provider resolves
    the model from the bound selection itself; passing an extra keyword would
    raise a TypeError that a broad handler could mistake for a model failure.
    ``**kwargs`` counts as support (mock-based doubles rely on it). Unknown or
    uninspectable callables are treated as NOT supporting the keyword so a
    call-shape mismatch cannot masquerade as a provider error.
    """
    try:
        params = inspect.signature(fn).parameters
    except (TypeError, ValueError):
        return False
    if name in params:
        return True
    return any(p.kind == inspect.Parameter.VAR_KEYWORD for p in params.values())


# --------------------------------------------------------------------------- #
# Role task specs — one per SpecialistRole (Requirement 9)
# --------------------------------------------------------------------------- #

@dataclass(frozen=True)
class SpecialistTaskSpec:
    role: SpecialistRole
    purpose: str
    input_contract: Dict[str, str]
    output_contract: Dict[str, str]
    required_model_capabilities: Dict[str, Any]
    allowed_capability_ids: Tuple[str, ...]
    maximum_actions: int
    maximum_model_calls: int
    reasoning_only: bool = False


def _role_spec(role: SpecialistRole) -> SpecialistTaskSpec:
    caps = ROLE_CAPABILITIES.get(role, ())
    reqs = ROLE_MODEL_REQUIREMENTS.get(role, {})
    # task-type mapping per requirement 9
    mapping = {
        SpecialistRole.RESEARCH: dict(
            purpose="Investigate open-ended questions and gather evidence",
            input_contract={"objective": "question or research goal", "constraints": "scope and focus areas"},
            output_contract={"findings": "evidence-backed observations", "limitations": "gaps and unknowns"},
            reasoning_only=True,
            max_actions=3,
            max_calls=5,
        ),
        SpecialistRole.ANALYSIS: dict(
            purpose="Compare options, evaluate trade-offs, provide structured analysis",
            input_contract={"objective": "analysis prompt with options", "constraints": "evaluation criteria"},
            output_contract={"findings": "comparative assessment", "recommendation": "preferred direction if warranted"},
            reasoning_only=True,
            max_actions=2,
            max_calls=5,
        ),
        SpecialistRole.CODING: dict(
            purpose="Implement, fix, or inspect files within approved roots",
            input_contract={"objective": "coding task description", "constraints": "file targets and acceptance criteria"},
            output_contract={"findings": "what was changed/observed", "evidence_refs": "kernel action evidence"},
            reasoning_only=False,
            max_actions=5,
            max_calls=5,
        ),
        SpecialistRole.REVIEW: dict(
            purpose="Review code, plans, or prior work and produce structured critique",
            input_contract={"objective": "artifact or plan to review", "constraints": "review rubric"},
            output_contract={"findings": "observations and issues", "recommendation": "accept/revise guidance"},
            reasoning_only=True,
            max_actions=2,
            max_calls=5,
        ),
        SpecialistRole.PLANNING_SUPPORT: dict(
            purpose="Decompose goals into validated, ordered steps with dependencies",
            input_contract={"objective": "goal to plan", "constraints": "planning policy and existing context"},
            output_contract={"findings": "proposed steps and dependencies", "limitations": "uncertainties"},
            reasoning_only=True,
            max_actions=2,
            max_calls=5,
        ),
    }
    m = mapping.get(role, mapping[SpecialistRole.ANALYSIS])
    return SpecialistTaskSpec(
        role=role,
        purpose=m["purpose"],
        input_contract=m["input_contract"],
        output_contract=m["output_contract"],
        required_model_capabilities=dict(reqs),
        allowed_capability_ids=tuple(caps),
        maximum_actions=m["max_actions"],
        maximum_model_calls=m["max_calls"],
        reasoning_only=m["reasoning_only"],
    )


# --------------------------------------------------------------------------- #
# SpecialistReasoner
# --------------------------------------------------------------------------- #

class SpecialistReasoner:
    """Bounded specialist cognitive loop.

    Uses ModelSelector for model choice and ModelProvider for chat. Every
    proposed action is validated; the loop is bounded by steps/time/model_calls
    and never broadens capabilities beyond the role binding.
    """

    def __init__(self, db, context_packet, allowed_tools: List[str],
                 budget: Optional[Dict[str, Any]] = None,
                 model_selector=None, model_provider=None):
        self.db = db
        self.packet = context_packet
        self.allowed_tools = frozenset(allowed_tools or [])
        self.budget = dict(budget or {})
        # lazy provider/selector so test doubles apply at CALL time
        self._selector_override = model_selector
        self._provider_override = model_provider
        try:
            self.role = SpecialistRole(str(context_packet.role).upper())
        except ValueError:
            self.role = SpecialistRole.ANALYSIS
        self.spec = _role_spec(self.role)
        self.model_calls_used = 0
        self._action_proposals: List[Dict[str, Any]] = []

    # -- provider/selector (never OllamaCloudAdapter) -----------------------

    def _selector(self):
        if self._selector_override is not None:
            return self._selector_override
        from backend.services.model_selection import ModelSelector
        return ModelSelector(self.db)

    def _provider(self):
        if self._provider_override is not None:
            return self._provider_override
        # Honor the verified active binding automatically. This is the same
        # seam used by the main conversation path; a damaged/cloud selection is
        # never silently exchanged for the native Ollama default. The active
        # resolver itself returns the default only for the legacy sentinel.
        from backend.runtime.active_inference import get_active_chat_provider
        return get_active_chat_provider()

    # -- prompt building ----------------------------------------------------

    def _build_messages(self) -> List[Dict[str, str]]:
        p = self.packet
        # Role-bounded system prompt — the model cannot broaden capabilities
        allowed = ", ".join(sorted(self.spec.allowed_capability_ids)) or "none"
        system = (
            f"You are a bounded {self.role.value} specialist. Purpose: {self.spec.purpose}. "
            f"Allowed capabilities: {allowed}. "
            f"You must respond with STRICT JSON only. "
            f"Never request tools outside your allowed capabilities. "
            f"Budget: steps={self.budget.get('steps', self.spec.maximum_actions)}, "
            f"model_calls={self.budget.get('model_calls', self.spec.maximum_model_calls)}."
        )
        user_sections = [
            f"OBJECTIVE:\n{p.objective}",
            f"ROLE:\n{p.role}",
            f"ALLOWED_CAPABILITIES:\n{allowed}",
            f"CONSTRAINTS:\n{json.dumps(p.constraints)[:1000]}",
            f"EXPECTED_OUTPUT:\n{p.expected_output[:500] or '(no specific contract)'}",
            f"PARENT_GOAL:\n{p.parent_goal_summary or '(none)'}",
        ]
        if p.relevant_memory:
            mem = "\n".join(f"- [{m.get('scope')}] {m.get('key')}: {str(m.get('value',''))[:200]}"
                            for m in p.relevant_memory[:5])
            user_sections.append(f"RELEVANT_MEMORY:\n{mem}")
        # Task-type specific JSON contract
        if self.spec.reasoning_only:
            user_sections.append(
                "Respond with STRICT JSON: "
                '{"findings": ["..."], "analysis": "...", "limitations": ["..."], '
                '"confidence": 0.0-1.0, "recommended_next_action": "..."} '
                "Findings must be substantive (not 'done' or empty). Confidence is your reasoning confidence, not proof."
            )
        else:
            user_sections.append(
                "Respond with STRICT JSON: "
                '{"findings": ["..."], "proposed_actions": [{"capability": "...", "tool": "...", "purpose": "...", "input": {...}}], '
                '"analysis": "...", "limitations": ["..."], "confidence": 0.0-1.0} '
                "Proposed actions must use only allowed capabilities. They are proposals, not authorization."
            )
        return [
            {"role": "system", "content": system},
            {"role": "user", "content": "\n\n".join(user_sections)},
        ]

    # -- reasoning ----------------------------------------------------------

    async def analyze(self) -> Dict[str, Any]:
        """Run one bounded model reasoning call and parse structured output.

        Returns dict with findings, analysis, limitations, confidence,
        proposed_actions (if any). On failure, returns a fallback artifact
        that still marks confidence as unknown and limitations explicit.
        """
        # Budget enforcement
        cap = int(self.budget.get("model_calls", self.spec.maximum_model_calls))
        if self.model_calls_used >= max(1, cap):
            raise BudgetExceeded("max_specialist_model_calls exceeded")

        # Model selection via ModelSelector (never hard-bound)
        selected_model = None
        try:
            from backend.services.model_selection import requirements_for_task_class
            task_class = self.spec.required_model_capabilities.get("task_class", "simple_reasoning")
            reqs = requirements_for_task_class(task_class)
            sel = self._selector().select(reqs)
            selected_model = sel.selected
        except Exception as exc:
            logger.warning("Specialist model selection failed: %s", exc)

        messages = self._build_messages()
        provider = self._provider()
        self.model_calls_used += 1

        # Call provider — bounded by config timeout. The active provider already
        # knows the model from its verified binding, so pass the selector's
        # model only when the concrete client actually accepts that keyword.
        chat_kwargs = {
            "max_tokens": int(settings.max_output_tokens or 4000),
            "timeout": float(settings.decision_timeout_seconds or 60),
        }
        if selected_model and _supports_kwarg(provider.chat, "model_id"):
            chat_kwargs["model_id"] = selected_model
        try:
            response = await provider.chat(messages, **chat_kwargs)
        except TypeError:
            # An uninspectable/odd provider may still reject the keyword; retry
            # the bare call before classifying it as a genuine model failure.
            try:
                response = await provider.chat(messages)
            except Exception as exc:
                logger.warning("Specialist provider chat failed: %s", exc)
                return self._fallback_artifact(f"model failure: {exc}")
        except Exception as exc:
            logger.warning("Specialist provider chat failed: %s", exc)
            return self._fallback_artifact(f"model failure: {exc}")

        text = self._extract_text(response)
        parsed = self._parse_json(text)
        if parsed is None:
            # One bounded repair attempt for malformed output (Requirement 19)
            logger.info("Specialist malformed output, attempting one repair")
            repair_msg = messages + [
                {"role": "assistant", "content": text[:2000]},
                {"role": "user", "content": "Your previous output was not valid STRICT JSON. Reply again with ONLY the requested STRICT JSON object, no prose."},
            ]
            if self.model_calls_used < cap:
                self.model_calls_used += 1
                try:
                    response2 = await provider.chat(repair_msg, max_tokens=2000, timeout=30)
                    text2 = self._extract_text(response2)
                    parsed2 = self._parse_json(text2)
                    if parsed2 is not None:
                        return self._normalize(parsed2, repaired=True)
                except Exception:
                    pass
            return self._fallback_artifact("malformed model output after repair attempt")

        return self._normalize(parsed, repaired=False)

    def _fallback_artifact(self, reason: str) -> Dict[str, Any]:
        return {
            "findings": [],
            "analysis": "",
            "limitations": [reason, "fallback: no substantive model findings available"],
            "confidence": None,
            "proposed_actions": [],
            "repaired": False,
            "fallback": True,
            "_raw": reason,
        }

    @staticmethod
    def _extract_text(response: Any) -> str:
        if not isinstance(response, dict):
            return str(response or "")
        msg = response.get("message") or {}
        content = msg.get("content") or ""
        if not content and response.get("choices"):
            content = (response["choices"][0].get("message") or {}).get("content", "")
        return (content or "").strip()

    @staticmethod
    def _parse_json(text: str) -> Optional[Dict[str, Any]]:
        if not text:
            return None
        # Find first JSON object
        m = re.search(r"\{.*\}", text, re.DOTALL)
        if not m:
            return None
        try:
            data = json.loads(m.group(0))
            return data if isinstance(data, dict) else None
        except json.JSONDecodeError:
            return None

    def _normalize(self, parsed_result: Dict[str, Any], repaired: bool = False) -> Dict[str, Any]:
        """Normalize parsed specialist output into a validated artifact.

        Truncates free-text fields, drops empty findings/limitations, clamps
        confidence to [0, 1] (unknown stays None), and validates proposed
        actions against the role binding and allowed tools.

        Args:
            parsed_result: Parsed model output dictionary.
            repaired: Whether this result came from the repair retry.

        Returns:
            Normalized artifact dict with findings, analysis, limitations,
            confidence, proposed_actions, flags, and raw input.
        """
        # Named truncation caps (values preserved from previous literals).
        _FINDING_CHAR_CAP = 500
        _FINDINGS_COUNT_CAP = 20
        _FIELD_CHAR_CAP = 300
        _ANALYSIS_CHAR_CAP = 2000
        findings = [str(f)[:_FINDING_CHAR_CAP] for f in (parsed_result.get("findings") or []) if str(f).strip()][:_FINDINGS_COUNT_CAP]
        proposed = parsed_result.get("proposed_actions") or parsed_result.get("candidate_actions") or []
        if not isinstance(proposed, list):
            proposed = []
        # Validate each proposed action against role binding
        validated: List[Dict[str, Any]] = []
        for item in proposed[: self.spec.maximum_actions]:
            if not isinstance(item, dict):
                continue
            cap = str(item.get("capability") or item.get("capability_id") or "").strip()
            tool = str(item.get("tool") or item.get("tool_name") or "").strip()
            purpose = str(item.get("purpose") or item.get("description") or "")[:_FIELD_CHAR_CAP]
            action_input = item.get("input") or item.get("tool_input") or {}
            if not isinstance(action_input, dict):
                action_input = {}
            # Capability must be within role's allowed set
            if cap and cap not in self.spec.allowed_capability_ids:
                logger.info("Specialist proposed capability %s outside role binding, dropping", cap)
                continue
            # If capability not specified but tool is, infer capability
            if not cap and tool:
                # Find capability that owns this tool
                from backend.services.capabilities import _specs
                for spec in _specs():
                    if tool in spec.tool_names and spec.id in self.spec.allowed_capability_ids:
                        cap = spec.id
                        break
                if not cap:
                    continue
            if tool and tool not in self.allowed_tools:
                logger.info("Specialist proposed tool %s not in allowed_tools, dropping", tool)
                continue
            validated.append({"capability": cap, "tool": tool, "purpose": purpose, "input": action_input})
        # Confidence separation: reasoning confidence vs evidence confidence
        # Model self-confidence is NOT proof; unknown stays explicit.
        try:
            conf = parsed_result.get("confidence")
            confidence = float(conf) if conf is not None else None
            if confidence is not None and not (0.0 <= confidence <= 1.0):
                confidence = None
        except (TypeError, ValueError):
            confidence = None
        limitations = [str(l)[:_FIELD_CHAR_CAP] for l in (parsed_result.get("limitations") or []) if str(l).strip()][:10]
        if not limitations:
            limitations = ["model provided no explicit limitations"]
        return {
            "findings": findings,
            "analysis": str(parsed_result.get("analysis") or parsed_result.get("summary") or "")[:_ANALYSIS_CHAR_CAP],
            "limitations": limitations,
            "confidence": confidence,
            "recommended_next_action": str(parsed_result.get("recommended_next_action") or "")[:_FINDING_CHAR_CAP],
            "proposed_actions": validated,
            "repaired": repaired,
            "fallback": False,
            "_raw": parsed_result,
        }

    # -- action planning validation -----------------------------------------

    def validate_proposal(self, proposal: Dict[str, Any], runtime) -> Tuple[bool, Optional[str]]:
        """Validate a single proposed action against all gates (Requirement 11)."""
        tool = str(proposal.get("tool") or "").strip()
        cap = str(proposal.get("capability") or "").strip()
        # 1. capability/binding
        if cap and cap not in self.spec.allowed_capability_ids:
            return False, f"capability {cap} outside role {self.role.value}"
        if tool and tool not in self.allowed_tools:
            return False, f"tool {tool} not in allowed_tools"
        # 2. tool availability
        if tool:
            try:
                from backend.api.dependencies import get_tool_executor
                ex = getattr(runtime, 'executor', None) or get_tool_executor()
                if tool not in ex.list_tools():
                    return False, f"tool {tool} not registered"
            except Exception:
                pass
        # 3. budget (checked by runtime, but pre-check here)
        if runtime.actions_used >= int(self.budget.get("steps", self.spec.maximum_actions)):
            return False, "budget steps exceeded"
        if runtime.elapsed_seconds >= int(self.budget.get("time_seconds", 300)):
            return False, "budget time exceeded"
        # 4. target validation (if tool has validate_targets, run it)
        # Caller will run full executor policy; this is a pre-check.
        return True, None
