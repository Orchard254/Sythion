"""Conservative task-resume admission; never itself resumes or recovers work.

The task runner owns state changes and must evaluate this policy again under its
writer transaction. A successful preflight is not an execution grant: task
ownership, runner lease, approvals and the normal execution gates still apply.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from backend.services.reasoning_recovery import _owner_lease_state

RESUMABLE_STATES = frozenset({"interrupted", "paused"})


def evaluate_resume_admission(
    *,
    task_state: str,
    unresolved_calls: int,
    owner: dict[str, Any] | None,
    has_pending_action: bool,
    has_task_action: bool,
    has_uncertain_event: bool,
    now: datetime,
    has_prior_mutation: bool = False,
    has_corrupt_receipt: bool = False,
) -> dict[str, Any]:
    """Return a privacy-minimal admission decision, not an authorization.

    Any evidence that an external effect might be unresolved is a hard stop.
    A possibly-live or stale ACTIVE task ownership is not stolen or released.
    Only a deliberate user resume attempt can consume a clear decision.
    """
    if not isinstance(task_state, str) or not task_state:
        raise ValueError("task state is required")
    if type(unresolved_calls) is not int or unresolved_calls < 0:
        raise ValueError("invalid unresolved count")
    if not isinstance(now, datetime):
        raise ValueError("now must be a datetime")
    if owner is not None and not isinstance(owner, dict):
        raise ValueError("invalid owner snapshot")
    for flag in (has_pending_action, has_task_action, has_uncertain_event, has_prior_mutation, has_corrupt_receipt):
        if type(flag) is not bool:
            raise ValueError("resume flags must be booleans")

    owner_state = _owner_lease_state(owner, now)
    owner_uncertain = bool(owner and owner.get("uncertain_actions"))
    owner_action = bool(owner and owner.get("active_action"))
    if task_state not in RESUMABLE_STATES:
        reason = "task_not_resumable"
    elif has_task_action or owner_action or owner_uncertain or has_uncertain_event:
        reason = "external_effect_review_required"
    elif has_prior_mutation:
        reason = "prior_mutating_effect_review_required"
    elif has_pending_action:
        reason = "pending_action_review_required"
    elif has_corrupt_receipt:
        reason = "corrupt_reasoning_receipt_review_required"
    elif unresolved_calls:
        reason = "unresolved_reasoning_review_required"
    elif owner_state in {"current", "expired", "indeterminate", "recovered"}:
        reason = "ownership_recovery_required"
    else:
        # Missing/released ownership can be valid for older interrupted tasks
        # and for paused work. This is a preflight only; the runner must still
        # acquire the execution lease and check task state atomically.
        reason = "clear_for_explicit_resume"

    clear = reason == "clear_for_explicit_resume"
    return {
        "task_state": task_state,
        "owner_lease": owner_state,
        "unresolved_reasoning_calls": unresolved_calls,
        "reason": reason,
        "preflight_clear": clear,
        "review_required": not clear,
        "automatic_replay_allowed": False,
    }


def read_task_resume_admission(db: Any, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Read task ownership, action uncertainty and *committed* reasoning receipts.

    Does no mutations. When used for a resume transition, this read MUST run
    under the TaskRunner writer lock immediately before the state transition.
    A standalone read is only a potentially stale preflight observation.
    """
    import json
    from backend.models.database import Task, TaskEvent
    from backend.runtime.task_ownership import TaskOwnershipRow
    from backend.runtime.events import decode_event_data
    from backend.services.reasoning_receipt_integrity import audit_reasoning_receipts
    from sqlalchemy import or_

    task = db.query(Task).filter_by(id=task_id).populate_existing().first()
    if task is None:
        raise LookupError("task not found")
    owner = db.query(TaskOwnershipRow).filter_by(task_id=task_id).populate_existing().first()
    snapshot = None
    if owner is not None:
        raw = owner.uncertain_action_ids
        if raw is None:
            uncertain_actions = False
        else:
            try:
                value = json.loads(raw)
                uncertain_actions = not (isinstance(value, list) and len(value) == 0)
            except (ValueError, TypeError):
                uncertain_actions = True
        snapshot = {
            "status": owner.status,
            "expires_at": owner.expires_at,
            "active_action": bool(owner.active_action_id),
            "uncertain_actions": uncertain_actions,
        }

    action_types = ("action.uncertain", "action.started", "tool.executed")
    rows = (db.query(TaskEvent)
            .filter(TaskEvent.task_id == task_id,
                    or_(TaskEvent.event_type.like("reasoning.%"),
                        TaskEvent.event_type.in_(action_types)))
            .order_by(TaskEvent.sequence_num.asc()).all())
    events = []
    uncertain_effect_event = False
    prior_mutation = False
    corrupt_receipt = False
    for event in rows:
        if event.event_type == "action.uncertain":
            uncertain_effect_event = True
            continue
        if event.event_type == "action.started":
            prior_mutation = True
            continue
        data = decode_event_data(event.event_data)
        if event.event_type == "tool.executed":
            # Only an explicit boolean false proves this tool was read-only.
            # A successful mutation is not permission to replay that effect.
            if not isinstance(data, dict) or data.get("mutating") is not False:
                prior_mutation = True
            continue
        events.append({"sequence_num": event.sequence_num, "event_type": event.event_type, "event_data": data})
    audit = audit_reasoning_receipts(events)
    corrupt_receipt = not audit.valid
    result = evaluate_resume_admission(
        task_state=task.state,
        unresolved_calls=audit.unresolved_count,
        owner=snapshot,
        has_pending_action=bool(task.pending_action),
        has_task_action=bool(task.active_action_id),
        has_uncertain_event=uncertain_effect_event,
        has_prior_mutation=prior_mutation,
        has_corrupt_receipt=corrupt_receipt,
        now=now if now is not None else datetime.utcnow(),
    )
    return result
