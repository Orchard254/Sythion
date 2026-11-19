"""Device registry for the Synth OS.

Records each endpoint (this PC, a phone, a future machine) with an identity, a
trust level, a last-seen timestamp and its capabilities. A device is a sensor
and actuator — never the system, never an authority on its own. Re-registering
a device (e.g. on every process start) refreshes observation fields but can
NEVER raise its trust level: only an explicit owner action does that.
"""

from __future__ import annotations

import hashlib
import json
import socket
import uuid
from datetime import datetime
from typing import Any

from sqlalchemy.orm import Session

TRUST_LEVELS = ("observed", "trusted", "privileged")
_TRUST_RANK = {level: i for i, level in enumerate(TRUST_LEVELS)}

_CAPABILITY_DOMAINS = {
    "system": "system", "file": "files", "app": "apps",
    "package": "packages", "gui": "gui", "browser": "browser",
    "memory": "memory", "activity": "activity", "objective": "goals",
}

_KIND = "local_windows"


def _clean(value: Any, fallback: str, limit: int = 80) -> str:
    if not isinstance(value, str):
        return fallback
    text = " ".join(value.split())
    text = "".join(ch for ch in text if ch >= " " or ch in "\t")
    return (text or fallback)[:limit]


def local_device_id() -> str:
    """A stable, non-reversible identifier for this machine.

    Hashes the platform node so no MAC address or hostname is exposed. Reboots
    and process restarts keep the same id; nothing here is a secret.
    """
    node = uuid.getnode()
    digest = hashlib.sha256(f"synth-device:{node}".encode("utf-8")).hexdigest()[:20]
    return f"dev_{digest}"


def local_device_name() -> str:
    try:
        return _clean(socket.gethostname(), "this-pc")
    except Exception:  # pragma: no cover - platform dependent
        return "this-pc"


def local_capabilities() -> list[str]:
    """Deterministic capability domains from the registered tool inventory."""
    from backend.tools.registry import create_all_tools

    domains: set[str] = set()
    for tool in create_all_tools():
        prefix = str(getattr(tool, "name", "")).split("_", 1)[0]
        domain = _CAPABILITY_DOMAINS.get(prefix)
        if domain:
            domains.add(domain)
    return sorted(domains)


def _to_dict(row) -> dict[str, Any]:
    try:
        capabilities = json.loads(row.capabilities)
        if not isinstance(capabilities, list):
            capabilities = []
    except (TypeError, ValueError):  # pragma: no cover - corrupt row
        capabilities = []
    return {
        "device_id": row.device_id,
        "name": row.name,
        "kind": row.kind,
        "trust_level": row.trust_level,
        "first_seen": row.first_seen.isoformat() if row.first_seen else None,
        "last_seen": row.last_seen.isoformat() if row.last_seen else None,
        "capabilities": capabilities,
    }


def register_local_device(db: Session, *, now: datetime | None = None) -> dict[str, Any]:
    """Upsert this machine's observation record. Never changes trust level."""
    from backend.models.database import RegisteredDevice

    now = now or datetime.utcnow()
    device_id = local_device_id()
    capabilities = json.dumps(local_capabilities(), ensure_ascii=False, sort_keys=True)
    row = db.query(RegisteredDevice).filter(RegisteredDevice.device_id == device_id).first()
    if row is None:
        row = RegisteredDevice(
            device_id=device_id,
            name=local_device_name(),
            kind=_KIND,
            trust_level="observed",
            first_seen=now,
            last_seen=now,
            capabilities=capabilities,
        )
        db.add(row)
    else:
        row.name = local_device_name()
        row.last_seen = now
        row.capabilities = capabilities
    db.flush()
    return _to_dict(row)


def list_devices(db: Session) -> list[dict[str, Any]]:
    from backend.models.database import RegisteredDevice

    rows = (
        db.query(RegisteredDevice)
        .order_by(RegisteredDevice.last_seen.desc(), RegisteredDevice.device_id.asc())
        .all()
    )
    return [_to_dict(row) for row in rows]


def get_device(db: Session, device_id: str) -> dict[str, Any] | None:
    from backend.models.database import RegisteredDevice

    row = db.query(RegisteredDevice).filter(RegisteredDevice.device_id == device_id).first()
    return _to_dict(row) if row is not None else None


def trust_rank(level: str) -> int:
    """Numeric rank for a trust level; used by policy, never as authority itself."""
    if level not in _TRUST_RANK:
        raise ValueError("unknown trust level")
    return _TRUST_RANK[level]


def set_trust_level(db: Session, device_id: str, level: str) -> dict[str, Any]:
    """Explicit owner action: set a device's trust level. Audited by the caller."""
    from backend.models.database import RegisteredDevice

    if level not in TRUST_LEVELS:
        raise ValueError("unknown trust level")
    row = db.query(RegisteredDevice).filter(RegisteredDevice.device_id == device_id).first()
    if row is None:
        raise LookupError("unknown device")
    row.trust_level = level
    db.flush()
    return _to_dict(row)
