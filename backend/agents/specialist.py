"""Specialist execution machinery (Phase 9).

Three pieces:

- ``SpecialistContextPacket`` — the BOUNDED structured context a specialist
  receives (never a copy of the Main Agent's full context).
- ``ScopedMemoryAccess`` — memory read/write scoping. Specialists may read
  permitted scopes and use their own working memory; permanent user/global
  writes are structurally impossible here (proposals go back to the parent).
- ``SpecialistRuntime`` — the ONLY way a specialist reaches the outside
  world. Every action flows through the protected kernel:
  SpecialistRuntime.request_action → capability-binding check → budget
  check → ToolExecutor.execute_tool (policy + verification authoritative) →
  audited evidence event.

A specialist never receives a ToolExecutor, DB session write path, GoalManager,
or AgentService reference — only this facade.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.agents.state import (
    BudgetExceeded,
    CapabilityViolation,
)
from backend.core.config import settings
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

# Read scopes permitted for specialists: their own task/working scope plus
# (read-only) parent goal scope. USER/GLOBAL reads are allowed only when
# explicitly listed in the delegation constraints ("memory_access").
_SPECIALIST_READ_SCOPES = ("TASK", "GOAL")


@dataclass
class SpecialistContextPacket:
    """Bounded context handed to a specialist at assignment time."""
    objective: str
    specialist_agent_id: str
    role: str
    required_capabilities: List[str]
    constraints: Dict[str, Any] = field(default_factory=dict)
    expected_output: str = ""
    budget: Dict[str, Any] = field(default_factory=dict)
    parent_goal_summary: Optional[str] = None
    relevant_memory: List[Dict[str, Any]] = field(default_factory=list)
    model_requirements: Dict[str, Any] = field(default_factory=dict)

    def to_json(self) -> str:
        return json.dumps({
            "objective": self.objective[:1000],
            "specialist_agent_id": self.specialist_agent_id,
            "role": self.role,
            "required_capabilities": self.required_capabilities,
            "constraints": self.constraints,
            "expected_output": self.expected_output[:500],
            "budget": self.budget,
            "parent_goal_summary": (self.parent_goal_summary or "")[:500],
            "model_requirements": self.model_requirements,
            "relevant_memory": [
                {"key": m.get("key"), "value": str(m.get("value", ""))[:300]}
                for m in self.relevant_memory[:10]
            ],
        })

    @staticmethod
    def from_json(raw: Optional[str]) -> "SpecialistContextPacket":
        data = json.loads(raw or "{}")
        return SpecialistContextPacket(
            objective=data.get("objective", ""),
            specialist_agent_id=data.get("specialist_agent_id", ""),
            role=data.get("role", ""),
            required_capabilities=list(data.get("required_capabilities", [])),
            constraints=dict(data.get("constraints", {})),
            expected_output=data.get("expected_output", ""),
            budget=dict(data.get("budget", {})),
            parent_goal_summary=data.get("parent_goal_summary"),
            relevant_memory=list(data.get("relevant_memory", [])),
            model_requirements=dict(data.get("model_requirements") or {}),
        )


class ScopedMemoryAccess:
    """Specialist-scoped memory access over MemoryService.

    - READS: only TASK/GOAL scopes by default; USER/GLOBAL reads require the
      delegation to explicitly grant them via constraints['memory_access'].
    - WRITES: permanent scopes (USER/GLOBAL/ENTITY) are REFUSED outright —
      a specialist cannot silently write permanent user memories. Working
      writes land in TASK scope with source MODEL_INFERENCE (which MemoryService
      itself restricts to ASSUMPTION/BELIEF, UNVERIFIED).
    - PROPOSALS: `propose_permanent_memory` records a proposal event for the
      Main Agent; it NEVER writes a permanent entry itself.
    """

    def __init__(self, db, agent_id: str, delegation_id: str,
                 grants: Optional[List[str]] = None):
        from backend.services.memory_service import MemoryService

        self.db = db
        self.agent_id = agent_id
        self.delegation_id = delegation_id
        self.service = MemoryService(db)
        self.ledger = DbEventLedger(db)
        extra = {str(s).upper() for s in (grants or [])}
        self.read_scopes = tuple(_SPECIALIST_READ_SCOPES) + tuple(
            s for s in extra if s in ("USER", "GLOBAL"))
        self.can_read_user_global = bool(extra & {"USER", "GLOBAL"})

    def read_relevant(self, query: str, limit: int = 5) -> List[Dict[str, Any]]:
        rows = self.service.retrieve(
            query=query, scopes=list(self.read_scopes),
            max_results=max(1, int(limit)))
        return [
            {"id": m.id, "kind": m.kind, "scope": m.scope, "key": m.key,
             "value": m.value[:300], "verification": m.verification_status}
            for m in rows
        ]

    def write_working(self, key: str, value: str,
                      kind: str = "ASSUMPTION",
                      kind_override: Optional[str] = None) -> Any:
        """Write scoped working memory (TASK scope only).

        MODEL_INFERENCE may only create ASSUMPTION/BELIEF; callers requesting
        FACT/PREFERENCE/EPISODIC are downgraded deterministically to
        ASSUMPTION so permanent user memory is never polluted.
        """
        norm = (kind or "ASSUMPTION").upper()
        # Anything outside ASSUMPTION/BELIEF is coerced to ASSUMPTION for the
        # MODEL_INFERENCE authority ceiling.
        target_kind = kind_override or (
            norm if norm in ("ASSUMPTION", "BELIEF")
            else "ASSUMPTION")
        return self.service.create(
            key=f"spec:{self.agent_id}:{key}",
            value=str(value)[:2000],
            kind=target_kind,
            scope="TASK",
            scope_ref=self.delegation_id,
            source_type="MODEL_INFERENCE",
            source_ref=self.agent_id,
        )

    def propose_permanent_memory(self, key: str, value: str,
                                 reason: str = "") -> Dict[str, Any]:
        """Record a memory PROPOSAL for the Main Agent — never a write."""
        proposal = {
            "proposed_by": self.agent_id,
            "delegation_id": self.delegation_id,
            "key": key[:200],
            "value": str(value)[:500],
            "reason": reason[:300],
        }
        self.ledger.append_event(
            f"delegation:{self.delegation_id}",
            "agent.memory.proposed", proposal)
        self.db.commit()
        return proposal


class SpecialistRuntime:
    """The single kernel-boundary facade for one specialist execution.

    Enforced here (source-level security boundary):
    1. capability binding — tools outside the specialist's assigned set are
       refused before anything reaches the executor;
    2. budgets — step/time/model-call caps from configuration;
    3. runtime policy — every call goes through ToolExecutor.execute_tool with
       PolicyContext(direct_user_instruction=False), so confirmation-gated and
       admin-gated policy stays non-bypassable;
    4. evidence — each action appends an auditable evidence event used later
       for result verification (a specialist cannot forge completion without
       matching ledger evidence).
    """

    def __init__(self, *, db, agent_id: str, delegation_id: str,
                 allowed_tools: List[str], task_executor,
                 task_id: str, model_call_fn=None):
        self.db = db
        self.agent_id = agent_id
        self.delegation_id = delegation_id
        self.allowed_tools = frozenset(allowed_tools)
        self.executor = task_executor          # ToolExecutor (kernel authority)
        self.task_id = task_id
        self._model_call_fn = model_call_fn    # optional; counted + bounded
        self.ledger = DbEventLedger(db)
        self.actions_used = 0
        self.model_calls_used = 0
        self.started_at = time.monotonic()

    # -- budget -------------------------------------------------------------

    @property
    def elapsed_seconds(self) -> float:
        return time.monotonic() - self.started_at

    def _enforce_budget(self) -> None:
        if self.actions_used >= max(1, int(settings.max_specialist_steps)):
            raise BudgetExceeded("max_specialist_steps exceeded")
        if self.elapsed_seconds >= max(1, int(settings.max_specialist_time_seconds)):
            raise BudgetExceeded("max_specialist_time_seconds exceeded")

    # -- actions ---------------------------------------------------------------

    async def request_action(self, tool_name: str,
                             input_data: Dict[str, Any]) -> Any:
        """Execute ONE tool action through the protected kernel."""
        if tool_name not in self.allowed_tools:
            raise CapabilityViolation(
                f"tool '{tool_name}' is not assigned to specialist "
                f"{self.agent_id} (allowed: {sorted(self.allowed_tools)})"
            )
        self._enforce_budget()

        # Narrow fail-closed containment: specialist direct ToolExecutor actions
        # cannot cause state-changing or privacy-sensitive effects until a
        # trusted task/consent fence exists.  Metadata is authoritative; missing
        # or malformed metadata fails closed.  Model events/status are NOT consent.
        # Preserve the usual specialist failure contract (failed ToolOutput, no
        # exception) so higher-level runners can aggregate the denied action
        # without changing mutation-state semantics.
        try:
            from backend.api.schemas import ToolOutput
        except ImportError:
            from api.schemas import ToolOutput

        tool = self.executor.get_tool(tool_name)
        reqs = _policy_requirements(tool)
        try:
            from backend.tools.executor import is_reviewed_safe_builtin
        except Exception:
            try:
                from tools.executor import is_reviewed_safe_builtin
            except Exception:
                is_reviewed_safe_builtin = lambda _t: False
        # Specialist dispatch must mirror the reviewed-identity skill fence: a
        # substitute or self-reported tool is never trusted merely because its
        # name/metadata looks safe.
        if (not is_reviewed_safe_builtin(tool)
                or _specialist_tool_blocked(reqs)):
            containment_output = ToolOutput(
                success=False,
                error={
                    "code": "SPECIALIST_CONTAINMENT_DENIED",
                    "message": (
                        "specialist direct action is fail-closed for "
                        "state-changing or privacy-sensitive tools until a "
                        "trusted task/consent fence exists"
                    ),
                },
                duration_ms=0,
            )
            self.actions_used += 1
            self.ledger.append_event(self.task_id, "specialist.action", {
                "delegation_id": self.delegation_id,
                "agent_id": self.agent_id,
                "tool": tool_name,
                "success": False,
                "error_code": "SPECIALIST_CONTAINMENT_DENIED",
                "seq": self.actions_used,
            })
            self.db.commit()
            return containment_output

        from backend.tools.executor import PolicyContext
        start = time.monotonic()
        output = await self.executor.execute_tool(
            tool_name, dict(input_data or {}),
            policy_context=PolicyContext(
                direct_user_instruction=False,   # never impersonate the user
                task_id=self.task_id,
            ),
        )
        self.actions_used += 1
        self.ledger.append_event(self.task_id, "specialist.action", {
            "delegation_id": self.delegation_id,
            "agent_id": self.agent_id,
            "tool": tool_name,
            "success": bool(output.success),
            "error_code": (output.error or {}).get("code")
            if getattr(output, "error", None) else None,
            "seq": self.actions_used,
        })
        self.db.commit()
        # Observability: machine-readable, no secrets/CoT
        try:
            import json as _j
            logger.info("concurrency.specialist_action %s", _j.dumps({
                "specialist_id": self.agent_id,
                "delegation_id": self.delegation_id,
                "task_id": self.task_id,
                "tool": tool_name,
                "seq": self.actions_used,
                "elapsed": round(time.monotonic() - start, 3),
                "success": bool(output.success),
            }))
        except Exception:
            pass
        return output

    async def request_model_call(self, messages: List[Dict[str, Any]],
                                 **kwargs: Any) -> Optional[Dict[str, Any]]:
        """Bounded model usage via the configured provider — never a hard
        model binding. Counted against max_specialist_model_calls."""
        if self.model_calls_used >= max(1, int(settings.max_specialist_model_calls)):
            raise BudgetExceeded("max_specialist_model_calls exceeded")
        if self._model_call_fn is None:
            return None
        self.model_calls_used += 1
        result = await self._model_call_fn(messages, **kwargs)
        self.ledger.append_event(self.task_id, "specialist.model_call", {
            "delegation_id": self.delegation_id,
            "agent_id": self.agent_id,
            "calls_used": self.model_calls_used,
        })
        self.db.commit()
        return result

    def cost_snapshot(self) -> Dict[str, Any]:
        return {
            "actions": self.actions_used,
            "model_calls": self.model_calls_used,
            "elapsed_seconds": round(self.elapsed_seconds, 3),
            "caps": {
                "steps": settings.max_specialist_steps,
                "time_seconds": settings.max_specialist_time_seconds,
                "model_calls": settings.max_specialist_model_calls,
            },
        }


def _policy_requirements(tool: Any) -> Optional[Dict[str, Any]]:
    """Return authoritative policy metadata, or None when missing/malformed."""
    if tool is None:
        return None
    getter = getattr(tool, "get_policy_requirements", None)
    if not callable(getter):
        return None
    try:
        reqs = getter()
    except Exception:
        return None
    return reqs if isinstance(reqs, dict) else None


def _specialist_tool_blocked(reqs: Any) -> bool:
    """Fail closed for specialist direct mutations/privacy-sensitive tools."""
    if not isinstance(reqs, dict):
        return True
    bool_keys = (
        "mutating",
        "destructive",
        "read_only",
        "app_internal",
        "requires_confirmation",
        "privacy_sensitive",
        "requires_screen_access",
        "requires_ui_automation",
        "requires_admin",
    )
    for key in bool_keys:
        if key in reqs and reqs.get(key) is not None and not isinstance(reqs.get(key), bool):
            return True
    risk_level = reqs.get("risk_level", "none")
    if risk_level not in {"none", "low", None}:
        return True
    if reqs.get("requires_admin") is True:
        return True
    if reqs.get("mutating") is True or reqs.get("destructive") is True:
        return True
    if reqs.get("read_only") is False:
        return True
    if reqs.get("requires_confirmation") is True:
        return True
    if risk_level in {"medium", "high"}:
        return True
    if (
        reqs.get("privacy_sensitive") is True
        or reqs.get("requires_screen_access") is True
        or reqs.get("requires_ui_automation") is True
    ):
        return True
    return False


def resolve_allowed_tools(role_capabilities: List[str],
                          executor) -> List[str]:
    """Deterministic binding: capability IDs → registered tool names.

    Uses the CapabilityRegistry specs as the vocabulary but INTERSECTS with
    actually-registered tools so the facade never offers a tool the runtime
    cannot authorize anyway. The registry stays descriptive; enforcement is
    both here (binding) and in the executor policy (authority).
    """
    from backend.services.capabilities import _specs

    wanted = {c.strip() for c in role_capabilities or []}
    registered = set(executor.list_tools()) if executor is not None else set()
    tools: List[str] = []
    for spec in _specs():
        if spec.id not in wanted:
            continue
        tools.extend(t for t in spec.tool_names if t in registered)
    return sorted(set(tools))
