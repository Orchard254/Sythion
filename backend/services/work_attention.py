"""Deterministic attention decision for an already authorized background wake.

This is a read/selection boundary, not an independent scheduler or authority.
It must be called *inside* the existing task-runner lease. The durable wake
worker checks the MainAgent policy gate before a caller reaches this module.
Neither this module nor the decision object may invoke a model or run a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping, Sequence


class WorkKind(str, Enum):
    """The three reasoning entry paths, selected by the same runtime policy."""

    CONVERSATION = "conversation"
    TASK_DECISION = "task_decision"
    GOAL_PLANNING = "goal_planning"


@dataclass(frozen=True, slots=True)
class WorkItem:
    """One candidate work item. A model never creates or promotes one."""

    kind: WorkKind
    work_id: str
    priority: int = 0
    ready: bool = True
    arrival: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.kind, WorkKind):
            raise ValueError("unsupported work kind")
        if not isinstance(self.work_id, str) or not self.work_id.strip():
            raise ValueError("work_id must be a non-empty string")
        if self.work_id != self.work_id.strip():
            raise ValueError("work_id must be trimmed")
        if isinstance(self.priority, bool) or not isinstance(self.priority, int):
            raise ValueError("priority must be an integer")
        if not isinstance(self.ready, bool):
            raise ValueError("ready must be a boolean")
        if isinstance(self.arrival, bool) or not isinstance(self.arrival, int) or self.arrival < 0:
            raise ValueError("arrival must be a non-negative integer")


@dataclass(frozen=True, slots=True)
class WorkSelection:
    """Deterministic selection result; selected=False means call no model."""

    selected: bool
    kind: WorkKind | None = None
    work_id: str | None = None
    reason: str = "no_ready_work"

    def __post_init__(self) -> None:
        if not isinstance(self.selected, bool):
            raise ValueError("selected must be a boolean")
        if self.selected:
            if not isinstance(self.kind, WorkKind) or not isinstance(self.work_id, str) or not self.work_id:
                raise ValueError("a selection requires a validated kind and work_id")
        elif self.kind is not None or self.work_id is not None:
            raise ValueError("an idle selection must not carry work identity")


def select_next_work(items: Sequence[WorkItem]) -> WorkSelection:
    """Choose at most one ready item by priority, then arrival, then identity.

    This is a runtime-owned selection contract, not a background scheduler: it
    performs no I/O, grants no permission, and never invokes a model. Equal
    scores are broken deterministically so the same input always selects the
    same work. No ready work is an explicit ``selected=False`` outcome.
    """
    if items is None or isinstance(items, (str, bytes)):
        raise TypeError("items must be a sequence of WorkItem")
    items_list = list(items)
    if any(not isinstance(item, WorkItem) for item in items_list):
        raise TypeError("items contains an invalid work item")
    ready = [item for item in items_list if item.ready]
    if not ready:
        return WorkSelection(selected=False, reason="no_ready_work")
    best = min(
        ready,
        key=lambda item: (-item.priority, item.arrival, item.kind.value, item.work_id),
    )
    return WorkSelection(
        selected=True, kind=best.kind, work_id=best.work_id,
        reason="selected_by_policy",
    )


def reasoning_signals_for(
    selection: WorkSelection,
    *,
    pending_mutating_action: bool = False,
    budget_available: bool = True,
    provider_available: bool = True,
):
    """Translate a work selection into the shared reasoning admission signals."""
    from backend.services.reasoning_dispatch import AttentionSignals

    if not isinstance(selection, WorkSelection):
        raise TypeError("selection must be a WorkSelection")
    return AttentionSignals(
        has_new_input=selection.selected and selection.kind is WorkKind.CONVERSATION,
        work_active=selection.selected and selection.kind is not WorkKind.CONVERSATION,
        pending_mutating_action=pending_mutating_action,
        budget_available=budget_available,
        provider_available=provider_available,
    )


class AttentionAction(str, Enum):
    RUN_GOAL = "run_goal"
    IDLE = "idle"


@dataclass(frozen=True, slots=True)
class AttentionDecision:
    """One ephemeral *selection*, not a claim of started or completed work."""

    action: AttentionAction
    reason: str
    goal_id: str | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.action, AttentionAction):
            raise ValueError("unsupported attention action")
        if self.action == AttentionAction.IDLE:
            if self.goal_id is not None or self.reason != "no_runnable_goal":
                raise ValueError("idle attention decision must not select work")
        elif (
            not isinstance(self.goal_id, str)
            or not self.goal_id
            or self.goal_id != self.goal_id.strip()
            or self.reason != "eligible_goal"
        ):
            raise ValueError("run_goal requires a validated goal reference")


def decide_background_attention(selector: Any) -> AttentionDecision:
    """Choose RUN_GOAL or IDLE without an LLM, retries, or side effects.

    ``selector`` is the repository's existing RunnableGoalSelector. It owns
    readiness, backoff and fairness; this function does not override any of
    those policies. The orchestrator must recheck step eligibility under its
    lease before execution. Caller keeps the selected work durable through the
    ordinary task/step/event machinery, never through this ephemeral object.
    """
    choice = selector.pick_goal(respect_backoff=True)
    if choice is None:
        return AttentionDecision(AttentionAction.IDLE, reason="no_runnable_goal")
    if not isinstance(choice, Mapping):
        raise TypeError("goal selector returned an invalid selection")
    goal_id = choice.get("goal_id")
    if not isinstance(goal_id, str) or not goal_id or goal_id != goal_id.strip():
        raise ValueError("goal selector returned an invalid goal reference")
    priority = choice.get("priority", 0)
    if isinstance(priority, bool) or not isinstance(priority, int):
        raise ValueError("goal selector returned an invalid priority")
    # The same work-selection contract used by the online entry paths now
    # governs the leased wake path. The selector still owns eligibility,
    # fairness and backoff; this only re-affirms the selection deterministically.
    selection = select_next_work([
        WorkItem(
            kind=WorkKind.GOAL_PLANNING,
            work_id=goal_id,
            priority=priority,
            arrival=int(choice.get("arrival", 0) or 0),
            ready=True,
        ),
    ])
    if not selection.selected:
        return AttentionDecision(AttentionAction.IDLE, reason="no_runnable_goal")
    return AttentionDecision(
        AttentionAction.RUN_GOAL, reason="eligible_goal", goal_id=selection.work_id
    )
