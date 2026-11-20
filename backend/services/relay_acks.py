"""Durable delivery acknowledgements for the first-party device relay.

A phone confirms receipt of sequenced relay frames over the device WSS scope.
Those acks are stored here so the Core retains a record of which frames a device
actually received, across socket disconnects. An ack is **delivery evidence only**:
it never grants authority, proves an external effect, or changes a tracked action.
Owner visibility is a read-only projection guarded by the control boundary.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlalchemy.orm import Session

from backend.models.database import RelayAck, RelayMessage, RelaySession


class AckError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def _session_belongs_to(db: Session, session_id: str, device_id: str) -> bool:
    row = db.query(RelaySession).filter(
        RelaySession.id == session_id,
        RelaySession.device_id == device_id,
    ).first()
    return row is not None


def record_ack(
    db: Session, *, session_id: str, device_id: str, sequence: int,
) -> dict[str, Any]:
    """Persist one delivery ack. Idempotent on (session_id, sequence).

    Validates that the session belongs to the acknowledging device and that the
    sequence refers to a real message in that session, so a device cannot claim
    receipt of frames it was never sent.
    """
    if not isinstance(sequence, int) or isinstance(sequence, bool) or sequence < 0:
        raise AckError("INVALID_SEQUENCE", "sequence must be a non-negative integer")
    if not _session_belongs_to(db, session_id, device_id):
        raise AckError("SESSION_MISMATCH", "session does not belong to device")

    exists = db.query(RelayMessage.id).filter(
        RelayMessage.session_id == session_id,
        RelayMessage.id == sequence,
    ).first()
    if exists is None:
        raise AckError("UNKNOWN_MESSAGE", "sequence is not a message in this session")

    existing = db.query(RelayAck).filter(
        RelayAck.session_id == session_id,
        RelayAck.sequence == sequence,
    ).first()
    if existing is not None:
        return {
            "id": existing.id, "session_id": session_id,
            "device_id": existing.device_id, "sequence": sequence,
            "duplicate": True, "authorized": False,
        }

    row = RelayAck(
        session_id=session_id, device_id=device_id, sequence=sequence,
    )
    db.add(row)
    db.flush()
    return {
        "id": row.id, "session_id": session_id, "device_id": device_id,
        "sequence": sequence, "duplicate": False, "authorized": False,
    }


def _to_dict(row: RelayAck) -> dict[str, Any]:
    return {
        "id": row.id,
        "session_id": row.session_id,
        "device_id": row.device_id,
        "sequence": row.sequence,
        "created_at": row.created_at.isoformat() if row.created_at else None,
    }


def ack_summary(
    db: Session, *, session_id: str | None = None, limit: int = 50,
) -> dict[str, Any]:
    """Read-only owner view: per-session high-water marks plus recent acks."""
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 100:
        raise ValueError("limit must be between 1 and 100")

    query = db.query(RelayAck)
    if session_id:
        query = query.filter(RelayAck.session_id == session_id)
    recent = query.order_by(RelayAck.created_at.desc(), RelayAck.id.desc()).limit(limit).all()

    high_water = db.query(
        RelayAck.session_id, func.max(RelayAck.sequence),
    )
    if session_id:
        high_water = high_water.filter(RelayAck.session_id == session_id)
    marks = [
        {"session_id": sid, "highest_ack": int(seq)}
        for sid, seq in high_water.group_by(RelayAck.session_id).all()
    ]

    return {
        "acks": [_to_dict(row) for row in recent],
        "sessions": marks,
        "note": (
            "Read-only delivery evidence. An ack does not grant authority, "
            "prove an external effect, or authorize any action."
        ),
        "authorized": False,
        "approval_issued": False,
    }
