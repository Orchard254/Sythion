"""Derive one plan-step outcome from persisted runtime evidence, not a worker claim.

This is a conservative *observation* only: no tool execution, retry, state
mutation, or permission grant. The TaskRunner/Planner own all transitions.
"""

from __future__ import annotations

from typing import Literal

ObservedOutcome = Literal["completed", "failed", "unverified"]


def classify_step_outcome(
    *,
    requested_task_id: str,
    evidence_task_id: str | None,
    step_status: str | None,
    task_state: str | None,
    completion_error: str | None = None,
) -> ObservedOutcome:
    """Classify an *exactly linked* step/task pair after outcome application.

    The kernel's completion gate must be rechecked for terminal success. A
    nonterminal, missing, foreign or contradictory observation never counts
    as success/failure or permits an automatic follow-on step.
    """
    if not isinstance(requested_task_id, str) or not requested_task_id.strip():
        raise ValueError("a task identity is required")
    if evidence_task_id != requested_task_id:
        return "unverified"
    if step_status == "completed" and task_state == "completed" and completion_error is None:
        return "completed"
    # A completed task can have a FAILED plan step when its runtime evidence
    # was rejected. The step's durable FAILED status, never text, is decisive.
    if step_status == "failed" and task_state in ("completed", "failed"):
        return "failed"
    return "unverified"
