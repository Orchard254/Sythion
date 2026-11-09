"""Deterministic attention decision for an already authorized background wake.

This is a read/selection boundary, not an independent scheduler or authority.
It must be called *inside* the existing task-runner lease. The durable wake
worker checks the MainAgent policy gate before a caller reaches this module.
Neither this module nor the decision object may invoke a model or run a tool.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum
from typing import Any, Mapping


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
    return AttentionDecision(
        AttentionAction.RUN_GOAL, reason="eligible_goal", goal_id=goal_id
    )
