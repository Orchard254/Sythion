"""AgentService (Phase 9) — Agent rows, lifecycle transitions, authority.

The Main Agent row reuses the persistent Phase 4 identity
(``main_agents.agent_id``) as its ``agents.id`` so there is exactly ONE main
agent per profile across both domains. Specialists are always created FROM an
enabled :class:`SpecialistDefinition`, are children of the Main Agent
(max_specialist_depth = 1), and temporary instances expire after their work
finishes so active runtime state never accumulates.

Authority rules enforced HERE (source-level):
- Only a MAIN actor may transition the Main Agent's lifecycle, create or
  cancel specialists, or mutate definitions.
- Specialists never appear in the parents' authority chain: a specialist
  cannot create another specialist (parent must be MAIN).
"""

from __future__ import annotations

import json
import logging
import uuid
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.agents.state import (
    AGENT_TRANSITIONS,
    AgentLifecycle,
    AgentType,
    AuthorityError,
    InvalidTransition,
    ROLE_CAPABILITIES,
    ROLE_MODEL_REQUIREMENTS,
    SpecialistRole,
    TERMINAL_AGENT_STATES,
    assert_agent_transition,
)
from backend.core.config import settings
from backend.models.database import AgentRecord, SpecialistDefinition
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)


def agent_scope(agent_id: str) -> str:
    return f"agent:{agent_id}"


class AgentService:
    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # ------------------------------------------------------------------ #
    # Main Agent                                                           #
    # ------------------------------------------------------------------ #

    def get_or_create_main(self) -> AgentRecord:
        """The unique MAIN row; identity is the Phase 4 persistent agent_id."""
        from backend.services.main_agent import MainAgentService

        identity = MainAgentService(self.db).get_or_create()
        row = self.db.query(AgentRecord).filter_by(
            type=AgentType.MAIN.value, id=identity.agent_id).first()
        if row is None:
            row = AgentRecord(
                id=identity.agent_id,
                type=AgentType.MAIN.value,
                state=AgentLifecycle.READY.value,
                parent_agent_id=None,
                config_json=json.dumps({}),
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(row)
            self.db.flush()
            self.ledger.append_event(agent_scope(row.id), "agent.registered", {
                "agent_id": row.id, "type": row.type,
            })
            self.db.commit()
        return row

    def get_agent(self, agent_id: str) -> Optional[AgentRecord]:
        return self.db.query(AgentRecord).filter_by(id=agent_id).first()

    def require_main_actor(self, actor_id: str) -> AgentRecord:
        """Authority guard: the acting agent must be the MAIN agent."""
        actor = self.get_agent(actor_id)
        if actor is None or actor.type != AgentType.MAIN.value:
            raise AuthorityError(
                "only the MAIN agent may perform this operation"
            )
        return actor

    # ------------------------------------------------------------------ #
    # Definitions                                                          #
    # ------------------------------------------------------------------ #

    def ensure_definitions(self) -> List[SpecialistDefinition]:
        """Seed the bounded initial role system once (idempotent).

        Capability bindings are validated against the CapabilityRegistry
        vocabulary at seed time; unknown capability IDs are never seeded
        (a role cannot claim a capability the registry does not expose).
        """
        try:
            from backend.services.capabilities import _specs

            registry_ids = {s.id for s in _specs()}
            specs_by_id = {s.id: s for s in _specs()}
        except Exception:  # pragma: no cover - registry is importable in prod
            registry_ids = set()
            specs_by_id = {}

        # --- strict validation: fail fast if role bindings drift ---
        for role in SpecialistRole:
            expected = set(ROLE_CAPABILITIES.get(role, ()))
            unknown = expected - registry_ids if registry_ids else set()
            if unknown:
                raise ValueError(
                    f"Role {role.value} references unknown capability IDs: {sorted(unknown)}")
            # also validate that each capability's tool_names are non-empty real tools
            # (when executor is available, this is checked at runtime; here we just ensure spec exists)
            for cap_id in expected:
                spec = specs_by_id.get(cap_id)
                if spec is None:
                    raise ValueError(f"Capability {cap_id} has no spec")
                if not spec.tool_names and cap_id not in ("voice", "reasoning_model"):
                    # voice/reasoning_model are intentionally tool-less
                    logger.warning("Capability %s has no tool_names", cap_id)

        existing = {
            d.role for d in
            self.db.query(SpecialistDefinition).all()
        }
        seeded = []
        for role in SpecialistRole:
            if role.value in existing:
                continue
            caps = [c for c in ROLE_CAPABILITIES[role]
                    if not registry_ids or c in registry_ids]
            definition = SpecialistDefinition(
                id=role.value.lower(),
                role=role.value,
                name=f"{role.value.title()} Specialist",
                description=f"Bounded {role.value.lower()} worker "
                            f"(Phase 9 default definition)",
                capabilities_json=json.dumps(caps),
                model_requirements_json=json.dumps(
                    ROLE_MODEL_REQUIREMENTS[role]),
                enabled=True,
            )
            self.db.add(definition)
            seeded.append(definition)
        if seeded:
            self.db.commit()
        return self.list_definitions()

    def validate_role_bindings(self, tool_executor=None) -> list:
        """Consistency check: every role → valid capability → real tool.

        Returns list of issues dicts; empty means consistent.
        """
        issues = []
        try:
            from backend.services.capabilities import _specs
            specs = {s.id: s for s in _specs()}
        except Exception:
            specs = {}
        # executor-registered tools for tool-level check
        registered = set()
        if tool_executor is not None:
            try:
                registered = set(tool_executor.list_tools())
            except Exception:
                registered = set()
        else:
            try:
                from backend.api.dependencies import get_tool_executor
                registered = set(get_tool_executor().list_tools())
            except Exception:
                registered = set()
        for role in SpecialistRole:
            caps = ROLE_CAPABILITIES.get(role, ())
            for cap_id in caps:
                if cap_id not in specs:
                    issues.append({"role": role.value, "code": "UNKNOWN_CAPABILITY", "capability": cap_id})
                    continue
                spec = specs[cap_id]
                for tool in spec.tool_names:
                    if registered and tool not in registered:
                        issues.append({"role": role.value, "capability": cap_id, "code": "MISSING_TOOL", "tool": tool})
        return issues

    def list_definitions(self, enabled_only: bool = False) -> List[SpecialistDefinition]:
        q = self.db.query(SpecialistDefinition)
        if enabled_only:
            q = q.filter(SpecialistDefinition.enabled.is_(True))
        return q.order_by(SpecialistDefinition.role).all()

    def set_definition_enabled(self, actor_id: str, role: str,
                               enabled: bool) -> SpecialistDefinition:
        self.require_main_actor(actor_id)
        definition = (
            self.db.query(SpecialistDefinition)
            .filter_by(role=str(role).upper()).first()
        )
        if definition is None:
            raise ValueError(f"unknown specialist role: {role}")
        definition.enabled = bool(enabled)
        self.db.commit()
        return definition

    # ------------------------------------------------------------------ #
    # Specialist instantiation                                             #
    # ------------------------------------------------------------------ #

    def create_specialist(
        self,
        actor_id: str,
        role: str,
        goal_id: Optional[str] = None,
        temporary: bool = True,
        ttl_seconds: Optional[int] = None,
        config: Optional[Dict[str, Any]] = None,
    ) -> AgentRecord:
        """Instantiate a specialist FROM a definition.

        Bounded by construction:
        - only the MAIN agent may instantiate;
        - the parent is always the MAIN agent (depth 1, no recursion);
        - temporary instances carry an expiry timestamp.
        """
        actor = self.require_main_actor(actor_id)

        depth = int((settings.max_specialist_depth or 1))
        if depth < 1:
            raise ValueError("max_specialist_depth must be >= 1")

        definition = (
            self.db.query(SpecialistDefinition)
            .filter_by(role=str(role).upper(), enabled=True).first()
        )
        if definition is None:
            raise ValueError(f"no enabled specialist definition for role {role}")

        cfg: Dict[str, Any] = dict(config or {})
        cfg.setdefault("capabilities", json.loads(definition.capabilities_json))
        cfg.setdefault("model_requirements",
                       json.loads(definition.model_requirements_json))

        expires_at = None
        if temporary:
            ttl = int(ttl_seconds if ttl_seconds is not None
                      else settings.specialist_ttl_seconds)
            expires_at = datetime.utcnow() + timedelta(seconds=max(60, ttl))

        row = AgentRecord(
            id=str(uuid.uuid4()),
            type=(AgentType.TEMPORARY_SPECIALIST.value if temporary
                  else AgentType.SPECIALIST.value),
            role=definition.role,
            state=AgentLifecycle.CREATED.value,
            parent_agent_id=actor.id,
            definition_id=definition.id,
            goal_id=goal_id,
            config_json=json.dumps(cfg),
            expires_at=expires_at,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(row)
        self.db.flush()
        self.ledger.append_event(agent_scope(row.id), "agent.created", {
            "agent_id": row.id, "type": row.type, "role": row.role,
            "parent": row.parent_agent_id, "temporary": temporary,
        })
        self.db.commit()
        return row

    # ------------------------------------------------------------------ #
    # Lifecycle                                                            #
    # ------------------------------------------------------------------ #

    def transition(self, agent_id: str, new_state: str,
                   reason: Optional[str] = None,
                   actor_id: Optional[str] = None) -> AgentRecord:
        """Validated lifecycle transition with audit event.

        Transitions of the MAIN row additionally require a MAIN actor.
        """
        row = self.get_agent(agent_id)
        if row is None:
            raise ValueError(f"agent {agent_id} not found")
        if row.type == AgentType.MAIN.value and actor_id is not None:
            self.require_main_actor(actor_id)

        current = AgentLifecycle(row.state)
        nxt = AgentLifecycle(str(new_state).upper())
        assert_agent_transition(current.value, nxt.value)
        row.state = nxt.value
        row.updated_at = datetime.utcnow()

        event_name = {
            AgentLifecycle.RUNNING: "agent.started",
            AgentLifecycle.COMPLETED: "agent.completed",
            AgentLifecycle.FAILED: "agent.failed",
            AgentLifecycle.CANCELLED: "agent.cancelled",
            AgentLifecycle.EXPIRED: "agent.expired",
        }.get(nxt, "agent.progress")
        self.ledger.append_event(agent_scope(row.id), event_name, {
            "agent_id": row.id, "previous": current.value,
            "state": nxt.value, "reason": reason,
        })
        self.db.commit()
        return row

    def expire_due(self, now: Optional[datetime] = None) -> int:
        """Expire COMPLETED/IDLE temporary specialists whose TTL has passed.

        Active runtime state must not accumulate forever; history remains in
        rows + ledger. Returns the number of agents expired.
        """
        now = now or datetime.utcnow()
        due = (
            self.db.query(AgentRecord)
            .filter(AgentRecord.expires_at.isnot(None))
            .filter(AgentRecord.expires_at < now)
            .filter(AgentRecord.type == AgentType.TEMPORARY_SPECIALIST.value)
            .all()
        )
        count = 0
        for row in due:
            if row.state in (s.value for s in TERMINAL_AGENT_STATES):
                continue
            try:
                terminal_target = (
                    AgentLifecycle.EXPIRED
                    if row.state in (AgentLifecycle.COMPLETED.value,
                                     AgentLifecycle.CREATED.value,
                                     AgentLifecycle.READY.value,
                                     AgentLifecycle.WAITING.value)
                    else None
                )
                if row.state == AgentLifecycle.RUNNING.value:
                    # Still RUNNING past TTL: expired, but honestly marked —
                    # its delegation owns failure semantics.
                    self.transition(row.id, AgentLifecycle.CANCELLED.value,
                                    reason="ttl_expired_while_running")
                elif terminal_target is not None:
                    self.transition(row.id, terminal_target.value,
                                    reason="ttl_expired")
                else:
                    continue
                count += 1
            except InvalidTransition:  # pragma: no cover - defensive
                continue
        return count

    # ------------------------------------------------------------------ #
    # Queries                                                              #
    # ------------------------------------------------------------------ #

    def list_agents(self, include_expired: bool = False) -> List[Dict[str, Any]]:
        q = self.db.query(AgentRecord)
        rows = q.order_by(AgentRecord.created_at.desc()).limit(200).all()
        out = []
        for r in rows:
            if not include_expired and \
                    r.state == AgentLifecycle.EXPIRED.value and \
                    r.type == AgentType.TEMPORARY_SPECIALIST.value:
                continue
            out.append({
                "id": r.id, "type": r.type, "role": r.role,
                "state": r.state, "parent_agent_id": r.parent_agent_id,
                "goal_id": r.goal_id, "expires_at": r.expires_at,
                "created_at": r.created_at,
            })
        return out
