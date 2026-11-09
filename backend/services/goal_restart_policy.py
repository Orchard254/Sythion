"""Conservative startup classification for a goal's interrupted work.

Startup observation is not an execution request. In particular, task absence,
interruption, cancellation, or an unfinalized worker never authorizes the
planner to turn an old RUNNING step into a fresh READY/PENDING attempt.
Only a genuine terminal completed/failed task may be reconciled through
the existing evidence-checked outcome path.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class GoalRestartDecision:
    outcome: str
    reconcile_terminal: bool
    requires_review: bool
    replay_allowed: bool = False
    authorization_issued: bool = False


def classify_goal_restart(task_state: str | None) -> GoalRestartDecision:
    """Pure, sanitized startup policy; no task IDs, secrets, or side effects."""
    if task_state == "completed":
        return GoalRestartDecision("verify_completed_task", True, False)
    if task_state == "failed":
        return GoalRestartDecision("record_failed_task", True, False)
    if task_state is None:
        return GoalRestartDecision("deferred_missing_task_review_required", False, True)
    if task_state in ("interrupted", "cancelled"):
        return GoalRestartDecision(
            f"deferred_{task_state}_manual_review_required", False, True
        )
    if task_state in ("created", "running", "waiting_for_user", "paused"):
        return GoalRestartDecision(f"deferred_{task_state}", False, True)
    return GoalRestartDecision("deferred_unrecognized_task_state", False, True)
