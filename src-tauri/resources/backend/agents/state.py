"""Agent and delegation state machines (Phase 9).

Two independent, deterministic state machines:

- ``AgentLifecycle`` for Agent rows (main / specialist / temporary_specialist).
  The Main Agent lifecycle (active/paused/degraded/shutdown in
  services/main_agent.py) remains a SEPARATE domain; this machine governs the
  Phase 9 ``agents`` table rows only.
- ``DelegationStatus`` for delegation records
  (PROPOSED → … → INTEGRATED plus terminal failure states).

Every transition is validated against an explicit table. Invalid transitions
fail deterministically with :class:`InvalidTransition` — nothing "falls
forward" silently.
"""

from __future__ import annotations

import enum
from typing import Dict, FrozenSet


class InvalidTransition(ValueError):
    """A state transition not permitted by the explicit transition table."""


class AuthorityError(PermissionError):
    """An actor attempted an operation reserved for a different authority."""


class AgentType(str, enum.Enum):
    MAIN = "MAIN"
    SPECIALIST = "SPECIALIST"
    TEMPORARY_SPECIALIST = "TEMPORARY_SPECIALIST"


class AgentLifecycle(str, enum.Enum):
    CREATED = "CREATED"
    READY = "READY"
    RUNNING = "RUNNING"
    WAITING = "WAITING"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


AGENT_TRANSITIONS: Dict[AgentLifecycle, FrozenSet[AgentLifecycle]] = {
    AgentLifecycle.CREATED: frozenset({
        AgentLifecycle.READY,
        AgentLifecycle.FAILED,
        AgentLifecycle.CANCELLED,
        AgentLifecycle.EXPIRED,
    }),
    AgentLifecycle.READY: frozenset({
        AgentLifecycle.RUNNING,
        AgentLifecycle.FAILED,
        AgentLifecycle.CANCELLED,
        AgentLifecycle.EXPIRED,
    }),
    AgentLifecycle.RUNNING: frozenset({
        AgentLifecycle.WAITING,
        AgentLifecycle.COMPLETED,
        AgentLifecycle.FAILED,
        AgentLifecycle.CANCELLED,
    }),
    AgentLifecycle.WAITING: frozenset({
        AgentLifecycle.RUNNING,
        AgentLifecycle.READY,
        AgentLifecycle.CANCELLED,
        AgentLifecycle.EXPIRED,
    }),
    # A temporary specialist that finished its work expires so active runtime
    # state never accumulates forever (history/audit rows are retained).
    AgentLifecycle.COMPLETED: frozenset({AgentLifecycle.EXPIRED}),
    AgentLifecycle.FAILED: frozenset(),
    AgentLifecycle.CANCELLED: frozenset(),
    AgentLifecycle.EXPIRED: frozenset(),
}

TERMINAL_AGENT_STATES: FrozenSet[AgentLifecycle] = frozenset({
    AgentLifecycle.FAILED,
    AgentLifecycle.CANCELLED,
    AgentLifecycle.EXPIRED,
})


def assert_agent_transition(current: str, new: str) -> None:
    cur = AgentLifecycle(str(current).upper())
    nxt = AgentLifecycle(str(new).upper())
    if nxt not in AGENT_TRANSITIONS[cur]:
        raise InvalidTransition(
            f"agent lifecycle: {cur.value} -> {nxt.value} is not allowed "
            f"(allowed: {sorted(s.value for s in AGENT_TRANSITIONS[cur])})"
        )


class DelegationStatus(str, enum.Enum):
    PROPOSED = "PROPOSED"
    VALIDATED = "VALIDATED"
    ASSIGNED = "ASSIGNED"
    RUNNING = "RUNNING"
    RESULT_READY = "RESULT_READY"
    VERIFIED = "VERIFIED"
    INTEGRATED = "INTEGRATED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    EXPIRED = "EXPIRED"


DELEGATION_TRANSITIONS: Dict[DelegationStatus, FrozenSet[DelegationStatus]] = {
    DelegationStatus.PROPOSED: frozenset({
        DelegationStatus.VALIDATED,
        DelegationStatus.FAILED,
        DelegationStatus.CANCELLED,
    }),
    DelegationStatus.VALIDATED: frozenset({
        DelegationStatus.ASSIGNED,
        DelegationStatus.FAILED,
        DelegationStatus.CANCELLED,
    }),
    DelegationStatus.ASSIGNED: frozenset({
        DelegationStatus.RUNNING,
        DelegationStatus.FAILED,
        DelegationStatus.CANCELLED,
        DelegationStatus.EXPIRED,
    }),
    DelegationStatus.RUNNING: frozenset({
        DelegationStatus.RESULT_READY,
        DelegationStatus.FAILED,
        DelegationStatus.CANCELLED,
        DelegationStatus.EXPIRED,
    }),
    DelegationStatus.RESULT_READY: frozenset({
        DelegationStatus.VERIFIED,
        DelegationStatus.FAILED,
        DelegationStatus.CANCELLED,
    }),
    # Raw model output can never jump to INTEGRATED: only a VERIFIED result
    # may be integrated by the Main Agent.
    DelegationStatus.VERIFIED: frozenset({
        DelegationStatus.INTEGRATED,
        DelegationStatus.VALIDATED,   # bounded revision loop
        DelegationStatus.CANCELLED,
    }),
    DelegationStatus.INTEGRATED: frozenset(),
    DelegationStatus.FAILED: frozenset(),
    DelegationStatus.CANCELLED: frozenset(),
    DelegationStatus.EXPIRED: frozenset(),
}

TERMINAL_DELEGATION_STATES: FrozenSet[DelegationStatus] = frozenset({
    DelegationStatus.INTEGRATED,
    DelegationStatus.FAILED,
    DelegationStatus.CANCELLED,
    DelegationStatus.EXPIRED,
})

ACTIVE_DELEGATION_STATES: FrozenSet[DelegationStatus] = frozenset({
    DelegationStatus.PROPOSED,
    DelegationStatus.VALIDATED,
    DelegationStatus.ASSIGNED,
    DelegationStatus.RUNNING,
    DelegationStatus.RESULT_READY,
    DelegationStatus.VERIFIED,
})


def assert_delegation_transition(current: str, new: str) -> None:
    cur = DelegationStatus(str(current).upper())
    nxt = DelegationStatus(str(new).upper())
    if nxt not in DELEGATION_TRANSITIONS[cur]:
        raise InvalidTransition(
            f"delegation status: {cur.value} -> {nxt.value} is not allowed "
            f"(allowed: {sorted(s.value for s in DELEGATION_TRANSITIONS[cur])})"
        )


class SpecialistRole(str, enum.Enum):
    """Initial bounded role system.

    Roles map ONLY onto capability IDs that exist in the CapabilityRegistry
    vocabulary. A role never claims a capability the registry does not expose,
    and the registry stays descriptive — the runtime remains authoritative.
    """

    RESEARCH = "RESEARCH"
    ANALYSIS = "ANALYSIS"
    CODING = "CODING"
    REVIEW = "REVIEW"
    PLANNING_SUPPORT = "PLANNING_SUPPORT"


# Default capability bindings per role (subsets of the CapabilityRegistry
# vocabulary; validated at definition-seeding time).
ROLE_CAPABILITIES: Dict[SpecialistRole, tuple] = {
    SpecialistRole.RESEARCH: ("system_observation", "activity_recall"),
    SpecialistRole.ANALYSIS: ("system_observation",),
    SpecialistRole.CODING: ("file_operations",),
    SpecialistRole.REVIEW: ("system_observation", "activity_recall"),
    SpecialistRole.PLANNING_SUPPORT: ("system_observation",),
}

# Model requirements are REQUESTS resolved by ModelSelector — specialists
# never hard-bind to a concrete model id.
ROLE_MODEL_REQUIREMENTS: Dict[SpecialistRole, dict] = {
    SpecialistRole.RESEARCH: {"task_class": "context_following"},
    SpecialistRole.ANALYSIS: {"task_class": "simple_reasoning"},
    SpecialistRole.CODING: {"task_class": "planning"},
    SpecialistRole.REVIEW: {"task_class": "structured_output"},
    SpecialistRole.PLANNING_SUPPORT: {"task_class": "planning"},
}


class BudgetExceeded(RuntimeError):
    """The specialist exceeded its configured action/time/model budget."""


class CapabilityViolation(PermissionError):
    """The specialist requested a capability/tool outside its binding."""


# ------------------------------------------------------------------ #
# Phase 10 — delegation plan / parallelism types
# ------------------------------------------------------------------ #

class DelegationPlanStatus(str, enum.Enum):
    DRAFT = "DRAFT"
    VALIDATED = "VALIDATED"
    ACTIVE = "ACTIVE"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"


DELEGATION_PLAN_TRANSITIONS: Dict[DelegationPlanStatus, FrozenSet[DelegationPlanStatus]] = {
    DelegationPlanStatus.DRAFT: frozenset({
        DelegationPlanStatus.VALIDATED,
        DelegationPlanStatus.FAILED,
        DelegationPlanStatus.CANCELLED,
    }),
    DelegationPlanStatus.VALIDATED: frozenset({
        DelegationPlanStatus.ACTIVE,
        DelegationPlanStatus.FAILED,
        DelegationPlanStatus.CANCELLED,
    }),
    DelegationPlanStatus.ACTIVE: frozenset({
        DelegationPlanStatus.COMPLETED,
        DelegationPlanStatus.FAILED,
        DelegationPlanStatus.CANCELLED,
    }),
    DelegationPlanStatus.COMPLETED: frozenset(),
    DelegationPlanStatus.FAILED: frozenset(),
    DelegationPlanStatus.CANCELLED: frozenset(),
}

TERMINAL_PLAN_STATES: FrozenSet[DelegationPlanStatus] = frozenset({
    DelegationPlanStatus.COMPLETED,
    DelegationPlanStatus.FAILED,
    DelegationPlanStatus.CANCELLED,
})


def assert_plan_transition(current: str, new: str) -> None:
    cur = DelegationPlanStatus(str(current).upper())
    nxt = DelegationPlanStatus(str(new).upper())
    if nxt not in DELEGATION_PLAN_TRANSITIONS[cur]:
        raise InvalidTransition(
            f"delegation plan: {cur.value} -> {nxt.value} not allowed "
            f"(allowed: {sorted(s.value for s in DELEGATION_PLAN_TRANSITIONS[cur])})"
        )


class CompletionPolicy(str, enum.Enum):
    ALL_REQUIRED = "ALL_REQUIRED"
    MAJORITY = "MAJORITY"
    OPTIONAL_RESULTS = "OPTIONAL_RESULTS"


class ParallelismStage(str, enum.Enum):
    STAGE_0_SERIALIZED = "STAGE_0_SERIALIZED"
    STAGE_1_REASONING_ONLY = "STAGE_1_REASONING_ONLY"
    STAGE_2_NON_DESTRUCTIVE = "STAGE_2_NON_DESTRUCTIVE"
    STAGE_3_FULL = "STAGE_3_FULL"
