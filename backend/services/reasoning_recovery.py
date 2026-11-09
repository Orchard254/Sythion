"""Read-only preflight for reconciling reasoning receipts with task ownership.

This deliberately does not reclaim a lease, resume a task, replay a tool,
write an event, or infer provider success. An unexpired ACTIVE lease means a
worker *may* still be running; an expired lease is not proof of process death.
"""

from __future__ import annotations

from collections.abc import Mapping
from datetime import datetime
from typing import Any

_TERMINAL = frozenset({"completed", "failed", "cancelled"})


def _owner_lease_state(owner: Mapping[str, Any] | None, now: datetime) -> str:
    if owner is None:
        return "missing"
    status = owner.get("status")
    if status == "ACTIVE":
        expires_at = owner.get("expires_at")
        if not isinstance(expires_at, datetime):
            return "indeterminate"
        try:
            return "expired" if expires_at <= now else "current"
        except TypeError:  # naive/aware mismatch: never guess a live owner
            return "indeterminate"
    if status in ("RELEASED", "RECOVERED", "EXPIRED"):
        return status.lower()
    return "indeterminate"


def assess_reasoning_recovery(
    task_state: str,
    unresolved_calls: int,
    owner: Mapping[str, Any] | None,
    *,
    now: datetime,
) -> dict[str, Any]:
    """Classify an observation only; the result is never execution authority.

    ``owner`` may contain ``status``, ``expires_at`` and booleans
    ``active_action``/``uncertain_actions``. It must not include model text,
    owner nonces or action parameters. Caller is responsible for goal scoping
    and committed evidence selection before invoking this function.
    """
    if not isinstance(task_state, str) or not task_state:
        raise ValueError("task state is required")
    if type(unresolved_calls) is not int or unresolved_calls < 0:
        raise ValueError("unresolved_calls must be a nonnegative integer")
    if not isinstance(now, datetime):
        raise ValueError("now must be a datetime")
    if owner is not None and not isinstance(owner, Mapping):
        raise ValueError("owner must be a mapping or None")
    if owner is not None and (
        type(owner.get("active_action", False)) is not bool
        or type(owner.get("uncertain_actions", False)) is not bool
    ):
        raise ValueError("uncertain action indicators must be booleans")

    lease = _owner_lease_state(owner, now)
    action_uncertain = bool(owner and (
        owner.get("active_action", False) or owner.get("uncertain_actions", False)
    ))
    if action_uncertain:
        state = "external_effect_review"
    elif task_state in _TERMINAL:
        state = "historical_ambiguity_review" if unresolved_calls else "terminal"
    elif task_state == "waiting_for_user":
        state = "waiting_for_user" if not unresolved_calls else "unresolved_reasoning_review"
    elif task_state == "paused":
        state = "paused" if not unresolved_calls else "unresolved_reasoning_review"
    elif task_state == "interrupted":
        state = "interrupted_review"
    elif unresolved_calls:
        if lease == "current":
            state = "possibly_running"
        elif lease == "expired":
            state = "stale_reasoning_review"
        else:
            state = "unresolved_reasoning_review"
    elif task_state == "running":
        if lease == "current":
            state = "possibly_running"
        elif lease == "expired":
            state = "stale_task_review"
        else:
            state = "unowned_running_review"
    elif task_state == "created":
        state = "not_started"
    else:
        state = "unknown_task_state_review"

    review = state.endswith("_review")
    return {
        "state": state,
        "owner_lease": lease,
        "unresolved_calls": unresolved_calls,
        "external_effect_uncertain": action_uncertain,
        "review_required": review,
        "may_still_be_running": state == "possibly_running",
        # An assessment never grants execution/replay authority; only the
        # existing task state machine, ownership and action gates can do so.
        "automatic_replay_allowed": False,
    }
