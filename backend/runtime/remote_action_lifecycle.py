"""Durable lifecycle for actions requested by a remote device.

Foundation primitive, in the style of ``task_ownership`` and
``permission_proposal``: callers own the transaction; no method commits.

States follow the Android integration contract:

    received -> understanding -> planned -> awaiting_approval
             -> executing -> verifying -> completed
    Any open state may fail, be cancelled, or become uncertain.

``uncertain`` is TERMINAL and forbids replay. An uncertain outcome means an
external effect may have occurred and was not verified; a fresh action with a
NEW idempotency key is required to retry, and reusing the old key returns the
same uncertain record. This is the same invariant already enforced by
TaskOwnership and the recovery routes: never auto-repeat an uncertain action.

Approval is bound to a trusted, UI-supplied ``decision_id`` (never model output),
and verified completion requires an ``evidence_ref``.
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import RemoteAction, RemoteActionEvent

RECEIVED = "received"
UNDERSTANDING = "understanding"
PLANNED = "planned"
AWAITING_APPROVAL = "awaiting_approval"
EXECUTING = "executing"
VERIFYING = "verifying"
COMPLETED = "completed"
FAILED = "failed"
CANCELLED = "cancelled"
UNCERTAIN = "uncertain"

STATES = (
    RECEIVED, UNDERSTANDING, PLANNED, AWAITING_APPROVAL, EXECUTING,
    VERIFYING, COMPLETED, FAILED, CANCELLED, UNCERTAIN,
)

TERMINAL = frozenset({COMPLETED, FAILED, CANCELLED, UNCERTAIN})

VALID_TRANSITIONS: Dict[str, frozenset] = {
    RECEIVED: frozenset({UNDERSTANDING, FAILED, CANCELLED, UNCERTAIN}),
    UNDERSTANDING: frozenset({PLANNED, FAILED, CANCELLED, UNCERTAIN}),
    PLANNED: frozenset({AWAITING_APPROVAL, EXECUTING, FAILED, CANCELLED, UNCERTAIN}),
    AWAITING_APPROVAL: frozenset({EXECUTING, FAILED, CANCELLED, UNCERTAIN}),
    EXECUTING: frozenset({VERIFYING, COMPLETED, FAILED, CANCELLED, UNCERTAIN}),
    VERIFYING: frozenset({COMPLETED, FAILED, UNCERTAIN}),
    COMPLETED: frozenset(),
    FAILED: frozenset(),
    CANCELLED: frozenset(),
    UNCERTAIN: frozenset(),
}


class RemoteActionError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _to_dict(row: RemoteAction) -> Dict[str, Any]:
    return {
        "action_id": row.action_id,
        "idempotency_key": row.idempotency_key,
        "device_id": row.device_id,
        "task_id": row.task_id,
        "owned_action_id": row.owned_action_id,
        "state": row.state,
        "version": int(row.version),
        "reason": row.reason,
        "approval_decision_id": row.approval_decision_id,
        "evidence_ref": row.evidence_ref,
        "created_at": row.created_at.isoformat() if row.created_at else None,
        "updated_at": row.updated_at.isoformat() if row.updated_at else None,
        "resolved_at": row.resolved_at.isoformat() if row.resolved_at else None,
        "terminal": row.state in TERMINAL,
        "uncertain": row.state == UNCERTAIN,
    }


class RemoteActionManager:
    def __init__(self, session: Session, *, clock: Optional[Callable[[], datetime]] = None):
        if session is None:
            raise ValueError("session is required")
        self._session = session
        self._clock = clock or (lambda: datetime.utcnow())

    def _now(self) -> datetime:
        return self._clock()

    def get(self, action_id: str) -> Optional[Dict[str, Any]]:
        row = self._session.query(RemoteAction).filter(
            RemoteAction.action_id == action_id).first()
        return _to_dict(row) if row is not None else None

    def by_idempotency_key(self, key: str) -> Optional[Dict[str, Any]]:
        row = self._session.query(RemoteAction).filter(
            RemoteAction.idempotency_key == key).first()
        return _to_dict(row) if row is not None else None

    def events(self, action_id: str) -> List[Dict[str, Any]]:
        rows = (
            self._session.query(RemoteActionEvent)
            .filter(RemoteActionEvent.action_id == action_id)
            .order_by(RemoteActionEvent.sequence.asc())
            .all()
        )
        return [
            {
                "sequence": int(row.sequence),
                "from_state": row.from_state,
                "to_state": row.to_state,
                "reason": row.reason,
                "decision_id": row.decision_id,
                "created_at": row.created_at.isoformat() if row.created_at else None,
            }
            for row in rows
        ]

    def _append_event(self, action_id: str, from_state: Optional[str],
                      to_state: str, reason: Optional[str],
                      decision_id: Optional[str]) -> None:
        next_sequence = self._session.query(
            func.coalesce(func.max(RemoteActionEvent.sequence), 0) + 1
        ).filter(RemoteActionEvent.action_id == action_id).scalar()
        self._session.add(RemoteActionEvent(
            action_id=action_id,
            sequence=int(next_sequence),
            from_state=from_state,
            to_state=to_state,
            reason=reason[:500] if isinstance(reason, str) else None,
            decision_id=decision_id,
            created_at=self._now(),
        ))

    def create(
        self,
        *,
        idempotency_key: str,
        device_id: str,
        task_id: Optional[str] = None,
        owned_action_id: Optional[str] = None,
        action_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Create an action in RECEIVED, or return the existing one (dedupe)."""
        if not isinstance(idempotency_key, str) or not idempotency_key.strip():
            raise RemoteActionError("IDEMPOTENCY_KEY_REQUIRED", "idempotency_key is required")
        if not isinstance(device_id, str) or not device_id.strip():
            raise RemoteActionError("DEVICE_REQUIRED", "device_id is required")

        existing = self.by_idempotency_key(idempotency_key)
        if existing is not None:
            result = dict(existing)
            result["deduplicated"] = True
            return result

        now = self._now()
        row = RemoteAction(
            action_id=action_id or f"ra_{uuid.uuid4().hex[:16]}",
            idempotency_key=idempotency_key,
            device_id=device_id,
            task_id=task_id,
            owned_action_id=owned_action_id,
            state=RECEIVED,
            version=1,
            created_at=now,
            updated_at=now,
        )
        self._session.add(row)
        self._session.flush()
        self._append_event(row.action_id, None, RECEIVED, None, None)
        result = _to_dict(row)
        result["deduplicated"] = False
        return result

    def transition(
        self,
        action_id: str,
        to_state: str,
        *,
        reason: Optional[str] = None,
        decision_id: Optional[str] = None,
        evidence_ref: Optional[str] = None,
        expected_version: Optional[int] = None,
    ) -> Dict[str, Any]:
        """Apply one validated, version-guarded transition. Never auto-retries."""
        if to_state not in STATES:
            raise RemoteActionError("UNKNOWN_STATE", "unknown target state")
        row = self._session.query(RemoteAction).filter(
            RemoteAction.action_id == action_id).first()
        if row is None:
            raise RemoteActionError("UNKNOWN_ACTION", "unknown action")

        current = row.state
        if current == UNCERTAIN:
            # Uncertain is terminal: replay is refused even with a fresh-looking
            # call. Reconciliation happens by inspecting evidence, not retrying.
            raise RemoteActionError(
                "UNCERTAIN_REPLAY_REFUSED",
                "outcome is uncertain; reconcile before creating a new action",
            )
        if to_state not in VALID_TRANSITIONS.get(current, frozenset()):
            raise RemoteActionError(
                "INVALID_TRANSITION", f"{current} -> {to_state} is not allowed")
        if expected_version is not None and int(row.version) != int(expected_version):
            raise RemoteActionError("VERSION_CONFLICT", "action version changed")

        # Starting execution requires an owner decision id and must not proceed
        # while ownership records an unresolved effect for this task.
        if to_state == EXECUTING:
            if current == AWAITING_APPROVAL and not (
                isinstance(decision_id, str) and decision_id.strip()
            ):
                raise RemoteActionError(
                    "TRUSTED_DECISION_REQUIRED",
                    "a trusted UI-supplied decision_id is required to leave awaiting_approval",
                )
            if row.task_id:
                from backend.runtime.task_ownership import TaskOwnershipManager
                ownership = TaskOwnershipManager(self._session).get(row.task_id)
                if ownership is not None and ownership.uncertain_action_ids:
                    raise RemoteActionError(
                        "UNCERTAIN_OWNERSHIP_REFUSED",
                        "task ownership records an uncertain action; execution refused",
                    )

        # Verified completion requires independent evidence, never a bare claim.
        if current == VERIFYING and to_state == COMPLETED and not (
            isinstance(evidence_ref, str) and evidence_ref.strip()
        ):
            raise RemoteActionError(
                "EVIDENCE_REQUIRED", "verified completion requires an evidence_ref")

        now = self._now()
        updates: Dict[str, Any] = {
            "state": to_state,
            "version": RemoteAction.version + 1,
            "updated_at": now,
        }
        if reason is not None:
            updates["reason"] = reason[:500]
        if decision_id is not None:
            updates["approval_decision_id"] = decision_id
        if evidence_ref is not None:
            updates["evidence_ref"] = evidence_ref
        if to_state in TERMINAL:
            updates["resolved_at"] = now

        changed = (
            self._session.query(RemoteAction)
            .filter(
                RemoteAction.action_id == action_id,
                RemoteAction.state == current,
                RemoteAction.version == int(row.version),
            )
            .update(updates, synchronize_session=False)
        )
        if changed != 1:
            raise RemoteActionError("VERSION_CONFLICT", "action changed concurrently")

        self._append_event(action_id, current, to_state, reason, decision_id)
        self._session.flush()
        # The UPDATE used synchronize_session=False; expire the identity-mapped
        # row so the returned snapshot reflects the committed-in-session state.
        self._session.expire(row)
        updated = self.get(action_id)
        assert updated is not None
        return updated

    def list_recent(self, *, limit: int = 20) -> List[Dict[str, Any]]:
        """Most recently updated actions for owner visibility (read-only)."""
        if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
            raise ValueError("limit must be between 1 and 100")
        rows = (
            self._session.query(RemoteAction)
            .order_by(RemoteAction.updated_at.desc(), RemoteAction.action_id.asc())
            .limit(limit)
            .all()
        )
        return [_to_dict(row) for row in rows]

    def list_uncertain(self) -> List[Dict[str, Any]]:
        rows = (
            self._session.query(RemoteAction)
            .filter(RemoteAction.state == UNCERTAIN)
            .order_by(RemoteAction.updated_at.asc())
            .all()
        )
        return [_to_dict(row) for row in rows]
