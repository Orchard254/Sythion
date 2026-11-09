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


def failed_goal_task_has_matching_evidence(
    *,
    task_state: str | None,
    task_id: str | None,
    evidence_task_id: str | None,
    linked_task_id: str | None,
    task_goal_id: str | None,
    step_goal_id: str,
) -> bool:
    """Only a matching FAILED task can be considered by the retry policy.

    This is necessary, not sufficient, for a safe retry. It grants neither
    permission nor execution: the normal failure taxonomy, retry budget,
    runner lease, and external-action admission still apply. In particular,
    a completed-but-unverified task must never be repeated automatically.
    """
    return (
        task_state == "failed"
        and bool(task_id)
        and task_id == evidence_task_id == linked_task_id
        and task_goal_id in (None, step_goal_id)
    )
