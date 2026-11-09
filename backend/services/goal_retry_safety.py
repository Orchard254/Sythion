"""Read-only retry preflight for FAILED goal tasks; not an execution grant.

A failed task can have already produced an external effect. A matching task ID
and a retryable failure category alone never prove that repeating it is safe.
The caller still enforces attempt budgets, goal/step binding, runner lease,
execution admission and consequence permissions.
"""

from __future__ import annotations

import json
from datetime import datetime
from typing import Any


def evaluate_failed_goal_retry_safety(
    *, task_state: str, owner_state: str, has_pending_action: bool,
    has_task_action: bool, has_owner_action: bool, has_owner_uncertainty: bool,
    has_uncertain_event: bool, has_prior_mutation: bool,
    unresolved_calls: int, has_corrupt_receipt: bool,
) -> dict[str, Any]:
    """Classify evidence only, without issuing permission or doing work."""
    flags = (has_pending_action, has_task_action, has_owner_action,
             has_owner_uncertainty, has_uncertain_event, has_prior_mutation,
             has_corrupt_receipt)
    if (not isinstance(task_state, str) or not isinstance(owner_state, str)
            or any(type(flag) is not bool for flag in flags)
            or type(unresolved_calls) is not int or unresolved_calls < 0):
        raise ValueError("invalid retry evidence")

    if task_state != "failed":
        reason = "task_not_failed_review_required"
    elif has_task_action or has_owner_action or has_owner_uncertainty or has_uncertain_event:
        reason = "external_effect_review_required"
    elif has_prior_mutation:
        reason = "prior_mutating_effect_review_required"
    elif has_pending_action:
        reason = "pending_action_review_required"
    elif has_corrupt_receipt:
        reason = "corrupt_reasoning_receipt_review_required"
    elif unresolved_calls:
        reason = "unresolved_reasoning_review_required"
    elif owner_state not in ("missing", "released"):
        reason = "ownership_recovery_required"
    else:
        reason = "clear_for_retry_policy"
    return {
        "reason": reason,
        "safe_to_consider_retry": reason == "clear_for_retry_policy",
        "authorization_issued": False,
        "execution_started": False,
    }


def read_failed_goal_retry_safety(db: Any, task_id: str, *, now: datetime | None = None) -> dict[str, Any]:
    """Inspect committed task, owner, event and reasoning records, read-only.

    Lease state is observational, not proof a worker stopped. The caller must
    enforce the existing runner's exclusive execution gate at task creation.
    """
    from sqlalchemy import or_
    from backend.models.database import Task, TaskEvent
    from backend.runtime.events import decode_event_data
    from backend.runtime.task_ownership import TaskOwnershipRow
    from backend.services.reasoning_receipt_integrity import audit_reasoning_receipts
    from backend.services.reasoning_recovery import _owner_lease_state

    if db.new or db.dirty or db.deleted:
        raise RuntimeError("retry preflight requires a clean ORM session")
    with db.no_autoflush:
        task = db.query(Task).filter_by(id=task_id).populate_existing().first()
        if task is None:
            raise LookupError("retry evidence task not found")
        owner = db.query(TaskOwnershipRow).filter_by(task_id=task_id).populate_existing().first()
        snapshot = None
        owner_action = False
        owner_uncertainty = False
        if owner is not None:
            owner_action = bool(owner.active_action_id)
            raw = owner.uncertain_action_ids
            if raw is not None:
                try:
                    # Anything other than an explicit empty list is unknown.
                    owner_uncertainty = json.loads(raw) != []
                except (ValueError, TypeError):
                    owner_uncertainty = True
            snapshot = {"status": owner.status, "expires_at": owner.expires_at}

        rows = (db.query(TaskEvent)
                .filter(TaskEvent.task_id == task_id,
                        or_(TaskEvent.event_type.like("reasoning.%"),
                            TaskEvent.event_type.in_(("action.uncertain", "action.started", "tool.executed"))))
                .order_by(TaskEvent.sequence_num.asc()).all())
        uncertain = False
        prior_mutation = False
        reasoning = []
        for row in rows:
            if row.event_type == "action.uncertain":
                uncertain = True
            elif row.event_type == "action.started":
                prior_mutation = True
            elif row.event_type == "tool.executed":
                payload = decode_event_data(row.event_data)
                if not isinstance(payload, dict) or payload.get("mutating") is not False:
                    prior_mutation = True
            else:
                reasoning.append({"sequence_num": row.sequence_num,
                                  "event_type": row.event_type,
                                  "event_data": decode_event_data(row.event_data)})
        audit = audit_reasoning_receipts(reasoning)
        return evaluate_failed_goal_retry_safety(
            task_state=task.state,
            owner_state=_owner_lease_state(snapshot, now or datetime.utcnow()),
            has_pending_action=bool(task.pending_action),
            has_task_action=bool(task.active_action_id),
            has_owner_action=owner_action, has_owner_uncertainty=owner_uncertainty,
            has_uncertain_event=uncertain, has_prior_mutation=prior_mutation,
            unresolved_calls=audit.unresolved_count, has_corrupt_receipt=not audit.valid,
        )
