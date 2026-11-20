"""Owner reconciliation for uncertain remote actions.

An uncertain action is terminal: this module NEVER changes its state and NEVER
replays it. It records what the owner established from independent evidence,
and can start one fresh action with a NEW idempotency key. Only the trusted
owner boundary (control token + loopback) may call it; a device cannot reconcile
its own uncertain effect.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Optional

from sqlalchemy.orm import Session

from backend.models.database import RemoteAction, RemoteActionReconciliation
from backend.runtime.remote_action_lifecycle import UNCERTAIN, RemoteActionManager

OUTCOMES = ("effect_confirmed", "effect_not_performed", "abandoned")


class ReconciliationError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _clean(value: Any, limit: int) -> Optional[str]:
    if value is None:
        return None
    if not isinstance(value, str):
        raise ReconciliationError("INVALID_INPUT", "expected a string")
    text = " ".join(value.split())[:limit]
    return text or None


def list_reconciliations(db: Session, action_id: str) -> list[dict[str, Any]]:
    rows = (
        db.query(RemoteActionReconciliation)
        .filter(RemoteActionReconciliation.action_id == action_id)
        .order_by(RemoteActionReconciliation.created_at.asc())
        .all()
    )
    return [{
        "id": row.id,
        "action_id": row.action_id,
        "decision_id": row.decision_id,
        "outcome": row.outcome,
        "evidence_ref": row.evidence_ref,
        "note": row.note,
        "new_action_id": row.new_action_id,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    } for row in rows]


def reconcile_action(
    db: Session,
    action_id: str,
    *,
    decision_id: str,
    outcome: str,
    evidence_ref: Optional[str] = None,
    note: Optional[str] = None,
    new_idempotency_key: Optional[str] = None,
    clock: Optional[Callable[[], datetime]] = None,
) -> dict[str, Any]:
    """Record an owner reconciliation and optionally start a fresh action.

    The original uncertain action is never mutated. A fresh action is created
    only with a NEW idempotency key that differs from the original's key.
    """
    now_fn = clock or (lambda: datetime.utcnow())
    action = db.query(RemoteAction).filter(RemoteAction.action_id == action_id).first()
    if action is None:
        raise ReconciliationError("UNKNOWN_ACTION", "unknown remote action")
    if action.state != UNCERTAIN:
        raise ReconciliationError(
            "NOT_UNCERTAIN", "only an uncertain action can be reconciled")

    clean_decision = _clean(decision_id, 200)
    if not clean_decision:
        raise ReconciliationError(
            "DECISION_REQUIRED", "a trusted UI-supplied decision_id is required")
    if outcome not in OUTCOMES:
        raise ReconciliationError("INVALID_OUTCOME", "unknown reconciliation outcome")
    clean_evidence = _clean(evidence_ref, 500)
    clean_note = _clean(note, 500)
    clean_new_key = _clean(new_idempotency_key, 128)

    new_action_id: Optional[str] = None
    if clean_new_key is not None:
        if clean_new_key == action.idempotency_key:
            raise ReconciliationError(
                "REPLAY_REFUSED",
                "a fresh action requires a different idempotency key")
        existing = db.query(RemoteAction).filter(
            RemoteAction.idempotency_key == clean_new_key).first()
        if existing is not None:
            raise ReconciliationError(
                "KEY_IN_USE", "idempotency key already belongs to another action")
        fresh = RemoteActionManager(db).create(
            idempotency_key=clean_new_key,
            device_id=action.device_id,
            task_id=action.task_id,
        )
        new_action_id = fresh["action_id"]

    record = RemoteActionReconciliation(
        id=f"rec_{uuid.uuid4().hex[:16]}",
        action_id=action_id,
        decision_id=clean_decision,
        outcome=outcome,
        evidence_ref=clean_evidence,
        note=clean_note,
        new_action_id=new_action_id,
        created_at=now_fn(),
    )
    db.add(record)
    db.flush()

    # The uncertain action remains terminal; record the decision in its event
    # sequence as an auditable marker without reviving it.
    manager = RemoteActionManager(db)
    manager.append_marker(
        action_id, "reconciled", f"outcome:{outcome}", clean_decision)

    return {
        "id": record.id,
        "action_id": action_id,
        "decision_id": clean_decision,
        "outcome": outcome,
        "evidence_ref": clean_evidence,
        "note": clean_note,
        "new_action_id": new_action_id,
        "authorized": False,
        "original_state": action.state,
    }
