"""Pure, conservative proof of a goal step's task-evidence binding.

A matching task id by itself does not prove step ownership: plan version,
goal scope, primary and display links, and uniqueness must all agree before
terminal outcomes may be applied or reported. This function performs no I/O.
"""
from __future__ import annotations


def goal_step_task_binding_is_valid(
    *,
    requested_task_id: str | None,
    evidence_task_id: str | None,
    linked_task_id: str | None,
    step_goal_id: str | None,
    task_goal_id: str | None,
    plan_goal_id: str | None,
    plan_status: str | None,
    active_plan_count: int,
    matching_step_count: int,
) -> bool:
    """Only an unambiguous task bound to an active plan is authoritative.

    Legacy task records may lack objective_id, but must still have both exact
    plan-step links. The actual task state and completion verifier are checked
    separately by TaskRunner and Planner; this is not an authorization grant.
    """
    return (
        type(requested_task_id) is str
        and bool(requested_task_id.strip())
        and evidence_task_id == requested_task_id
        and linked_task_id == requested_task_id
        and type(step_goal_id) is str
        and bool(step_goal_id)
        and plan_goal_id == step_goal_id
        and plan_status == "active"
        and type(active_plan_count) is int
        and active_plan_count == 1
        and task_goal_id in (None, step_goal_id)
        and type(matching_step_count) is int
        and matching_step_count == 1
    )
