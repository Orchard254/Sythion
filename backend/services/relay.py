"""First-party relay: durable message sessions between the Core and a device.

This is a broker inside the Core, not a third-party service. A registered device
opens a session, then polls durable messages (briefings, replies) and posts its
own text. Nothing here grants authority, clears a blocker, or runs a model: a
session only carries messages. The route verifies the device credential and
``conversation.write`` grant before any of this is reachable.

Messages are durable so a phone that was offline still receives what it missed.
"""

from __future__ import annotations

import json
import uuid
from datetime import datetime, timedelta
from typing import Any

from sqlalchemy.orm import Session

_MAX_TEXT = 8000
_MAX_PAGE = 50
_SESSION_TTL_SECONDS = 7 * 24 * 3600  # one week


def _bound(value: Any, limit: int = _MAX_TEXT) -> str:
    if not isinstance(value, str):
        return ""
    return value[:limit]


def _enqueue(
    db: Session, session_id: str, role: str, text: str, kind: str = "text",
) -> dict[str, Any]:
    from backend.models.database import RelayMessage

    row = RelayMessage(
        session_id=session_id, role=role, kind=kind,
        text=_bound(text), created_at=datetime.utcnow(),
    )
    db.add(row)
    db.flush()
    return {
        "id": row.id, "role": row.role, "kind": row.kind,
        "text": row.text, "created_at": row.created_at.isoformat(),
    }


def _session_row(db: Session, session_id: str):
    from backend.models.database import RelaySession

    return db.query(RelaySession).filter(RelaySession.id == session_id).first()


def open_session(db: Session, device_id: str, *, transport: str = "http") -> dict[str, Any]:
    """Open a relay session for a device and seed it with current briefings.

    ``transport`` is "wss" for the device-authenticated WebSocket gateway (which
    can emit delivery acks) or "http" for the polling fallback (which cannot).
    Caller must have already verified the device credential and grant.
    """
    from backend.models.database import RelaySession
    from backend.services.morning_brief import read_morning_brief
    from backend.services.user_briefing import build_user_briefing

    if transport not in ("wss", "http"):
        raise ValueError("transport must be 'wss' or 'http'")
    now = datetime.utcnow()
    session = RelaySession(
        id=f"rs_{uuid.uuid4().hex[:16]}",
        device_id=device_id,
        status="open",
        transport=transport,
        created_at=now,
        expires_at=now + timedelta(seconds=_SESSION_TTL_SECONDS),
    )
    db.add(session)
    db.flush()

    briefing = build_user_briefing(db)
    if briefing["has_blockers"]:
        _enqueue(db, session.id, "synth", json.dumps(briefing, ensure_ascii=False),
                 kind="briefing")
    morning = read_morning_brief(db)
    _enqueue(db, session.id, "synth", json.dumps(morning, ensure_ascii=False),
             kind="morning_brief")
    return {
        "session_id": session.id,
        "device_id": session.device_id,
        "status": session.status,
        "transport": session.transport,
        "expires_at": session.expires_at.isoformat() if session.expires_at else None,
    }


def session_belongs_to(db: Session, session_id: str, device_id: str) -> bool:
    row = _session_row(db, session_id)
    if row is None or row.device_id != device_id or row.status != "open":
        return False
    if row.expires_at is not None and row.expires_at <= datetime.utcnow():
        return False
    return True


def poll_messages(
    db: Session, session_id: str, *, after_id: int = 0, limit: int = _MAX_PAGE,
) -> list[dict[str, Any]]:
    from backend.models.database import RelayMessage

    if not isinstance(after_id, int) or after_id < 0:
        raise ValueError("after_id must be a non-negative integer")
    if not isinstance(limit, int) or not 1 <= limit <= _MAX_PAGE:
        raise ValueError("limit is out of bounds")
    rows = (
        db.query(RelayMessage)
        .filter(
            RelayMessage.session_id == session_id,
            RelayMessage.id > after_id,
        )
        .order_by(RelayMessage.id.asc())
        .limit(limit)
        .all()
    )
    result = [
        {
            "id": row.id, "role": row.role, "kind": row.kind,
            "text": row.text, "created_at": row.created_at.isoformat(),
        }
        for row in rows
    ]
    session = _session_row(db, session_id)
    if session is not None:
        session.last_poll_at = datetime.utcnow()
        db.flush()
    return result


def post_message(db: Session, session_id: str, text: str) -> dict[str, Any]:
    return _enqueue(db, session_id, "owner", text, kind="text")


def enqueue_reply(db: Session, session_id: str, text: str) -> dict[str, Any]:
    return _enqueue(db, session_id, "synth", text, kind="text")


def close_session(db: Session, session_id: str) -> bool:
    session = _session_row(db, session_id)
    if session is None or session.status == "closed":
        return False
    session.status = "closed"
    db.flush()
    return True
