"""Transport-agnostic trust boundary for remote Synth clients.

A remote client identifies as a registered device and presents a per-device
credential. Access then requires BOTH an explicit owner grant and a trust level
at or above the capability's floor. Nothing here depends on how the client
reaches the Core (tunnel, relay, or LAN) — that is a later stage. Credentials are
only ever stored as hashes; grants are explicit, additive, owner-controlled, and
can never widen themselves.
"""

from __future__ import annotations

import hashlib
import hmac
import secrets
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

from backend.services.device_registry import get_device, trust_rank

# Capability floor: the minimum trust level a device must have before a grant
# is even meaningful. The owner still must grant each capability explicitly.
CAPABILITY_MIN_TRUST = {
    "briefing.read": "observed",
    "agent.status": "observed",
    "devices.read": "trusted",
    "conversation.write": "trusted",
    "goals.submit": "privileged",
    "messages.read": "privileged",
}

CAPABILITIES = tuple(CAPABILITY_MIN_TRUST)


def _hash(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def _require_device(db: Session, device_id: str) -> None:
    if get_device(db, device_id) is None:
        raise LookupError("unknown device")


def issue_device_credential(
    db: Session, device_id: str, *, now: datetime | None = None,
) -> str:
    """Mint a new credential for a registered device. Returns plaintext ONCE."""
    from backend.models.database import DeviceCredential

    _require_device(db, device_id)
    now = now or datetime.utcnow()
    token = secrets.token_urlsafe(32)
    digest = _hash(token)
    row = db.query(DeviceCredential).filter(
        DeviceCredential.device_id == device_id).first()
    if row is None:
        db.add(DeviceCredential(
            device_id=device_id, token_hash=digest,
            created_at=now, revoked_at=None,
        ))
    else:
        row.token_hash = digest
        row.created_at = now
        row.revoked_at = None
    db.flush()
    return token


def revoke_device_credential(
    db: Session, device_id: str, *, now: datetime | None = None,
) -> bool:
    from backend.models.database import DeviceCredential

    row = db.query(DeviceCredential).filter(
        DeviceCredential.device_id == device_id).first()
    if row is None or row.revoked_at is not None:
        return False
    row.revoked_at = now or datetime.utcnow()
    db.flush()
    return True


def verify_device_credential(db: Session, device_id: str, token: str) -> bool:
    from backend.models.database import DeviceCredential

    row = db.query(DeviceCredential).filter(
        DeviceCredential.device_id == device_id).first()
    if row is None or row.revoked_at is not None:
        return False
    if not isinstance(token, str) or not token:
        return False
    return hmac.compare_digest(row.token_hash, _hash(token))


def list_grants(db: Session, device_id: str) -> list[str]:
    from backend.models.database import DeviceGrant

    rows = (
        db.query(DeviceGrant)
        .filter(DeviceGrant.device_id == device_id)
        .order_by(DeviceGrant.capability.asc())
        .all()
    )
    return [row.capability for row in rows]


def grant_capability(db: Session, device_id: str, capability: str) -> dict[str, Any]:
    from backend.models.database import DeviceGrant

    _require_device(db, device_id)
    if capability not in CAPABILITY_MIN_TRUST:
        raise ValueError("unknown capability")
    existing = db.query(DeviceGrant).filter(
        DeviceGrant.device_id == device_id,
        DeviceGrant.capability == capability,
    ).first()
    if existing is None:
        db.add(DeviceGrant(
            device_id=device_id, capability=capability,
            granted_at=datetime.utcnow(),
        ))
        db.flush()
    return {"device_id": device_id, "capability": capability, "granted": True}


def revoke_capability(db: Session, device_id: str, capability: str) -> bool:
    from backend.models.database import DeviceGrant

    row = db.query(DeviceGrant).filter(
        DeviceGrant.device_id == device_id,
        DeviceGrant.capability == capability,
    ).first()
    if row is None:
        return False
    db.delete(row)
    db.flush()
    return True


def check_access(db: Session, device_id: str, capability: str) -> tuple[bool, str]:
    """Return (allowed, reason). Requires a grant AND sufficient trust."""
    device = get_device(db, device_id)
    if device is None:
        return False, "unknown_device"
    if capability not in CAPABILITY_MIN_TRUST:
        return False, "unknown_capability"
    if capability not in list_grants(db, device_id):
        return False, "no_grant"
    floor = CAPABILITY_MIN_TRUST[capability]
    if trust_rank(device["trust_level"]) < trust_rank(floor):
        return False, "insufficient_trust"
    return True, "ok"
