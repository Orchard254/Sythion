"""Pure conservative completion projection for exactly one active plan."""
from __future__ import annotations


def may_complete_active_goal(
    *,
    goal_status: str,
    active_plan_count: int,
    step_statuses: list[str] | tuple[str, ...],
    criteria_required: bool,
    criteria_verified: bool,
) -> bool:
    """Only terminal verified required steps of one active plan can finish a goal."""
    if (
        not isinstance(goal_status, str)
        or type(active_plan_count) is not int
        or active_plan_count < 0
        or not isinstance(step_statuses, (list, tuple))
        or any(not isinstance(s, str) for s in step_statuses)
        or type(criteria_required) is not bool
        or type(criteria_verified) is not bool
    ):
        raise ValueError("invalid goal completion projection")
    if goal_status != "active" or active_plan_count != 1:
        return False
    required = [state for state in step_statuses if state != "cancelled"]
    return (
        bool(required)
        and all(state == "completed" for state in required)
        and (not criteria_required or criteria_verified)
    )
