"""Bounded provider-scoped evidence for *staged* cloud capability probes.

This is not an activation, health, or task-execution authorization primitive.
It stores non-secret metadata in the existing settings table only after a
genuine synthetic function-call probe. Evidence is bound to:
- allowlisted provider + exact model;
- a random new generation on *every* stage operation;
- the exact API key via HMAC (the key is never persisted);
- a short time window. Clock rollback / damaged rows fail closed.

The final Sythion runtime must still verify ALL inference paths independently
before any provider can be activated for real work.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any, Dict, Optional

from backend.core import profile
from backend.core.secrets import resolve_compatible_provider_key
from backend.core.settings_store import get_setting, set_setting


SETTING_KEY = "cloud_preview_tool_evidence_v1"
FORMAT_VERSION = 1
MAX_AGE_SECONDS = 3600


def _signature(
    credential: str, provider_id: str, model_id: str, generation: str, issued_at: int,
) -> str:
    # Domain separation prevents unrelated credential HMAC uses from validating
    # as model capability evidence. This digest cannot serve as an API token.
    payload = json.dumps(
        ["sythion-cloud-preview-tool-v1", provider_id, model_id, generation, issued_at],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(credential.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def make_evidence(
    snapshot: Dict[str, str],
    credential: str,
    *,
    issued_at: Optional[int] = None,
) -> Dict[str, Any]:
    """Build record after an observed true capability probe, never on catalog metadata."""
    provider_id = snapshot["provider_id"]
    model_id = snapshot["model_id"]
    generation = snapshot["generation"]
    if not credential:
        raise ValueError("Missing provider credential")
    now = int(time.time()) if issued_at is None else issued_at
    if type(now) is not int or now < 0:
        raise ValueError("Invalid issue time")
    return {
        "version": FORMAT_VERSION,
        "provider_id": provider_id,
        "model_id": model_id,
        "generation": generation,
        "issued_at": now,
        "mac": _signature(credential, provider_id, model_id, generation, now),
    }


def persist_probe_evidence(db: Any, snapshot: Dict[str, str], credential: str) -> None:
    """Persist successful probe in one existing local settings transaction."""
    if snapshot != profile.get_staged_cloud_snapshot():
        raise ValueError("Cloud candidate changed during verification")
    record = make_evidence(snapshot, credential)
    # No secret, result text, prompts or external provider error body stored.
    set_setting(db, SETTING_KEY, json.dumps(record, separators=(",", ":")))
    db.commit()


def is_probe_evidence_current(
    db: Any,
    *,
    now: Optional[int] = None,
) -> bool:
    """False on invalid state, expired evidence, key rotation, or DB failure."""
    try:
        snapshot = profile.get_staged_cloud_snapshot()
        if not snapshot:
            return False
        raw = get_setting(db, SETTING_KEY)
        if not isinstance(raw, str) or len(raw) > 2048:
            return False
        record = json.loads(raw)
        if not isinstance(record, dict):
            return False
        if record.get("version") != FORMAT_VERSION or type(record.get("version")) is not int:
            return False
        for field in ("provider_id", "model_id", "generation"):
            if record.get(field) != snapshot[field]:
                return False
        issued = record.get("issued_at")
        current = int(time.time()) if now is None else now
        if type(issued) is not int or type(current) is not int:
            return False
        if issued < 0 or current < issued or current - issued > MAX_AGE_SECONDS:
            return False
        mac = record.get("mac")
        if not isinstance(mac, str) or len(mac) != 64:
            return False
        secret = resolve_compatible_provider_key(snapshot["provider_id"])
        if not secret:
            return False
        expected = _signature(
            secret, snapshot["provider_id"], snapshot["model_id"],
            snapshot["generation"], issued,
        )
        return hmac.compare_digest(expected, mac)
    except (ValueError, KeyError, OSError, TypeError, AttributeError):
        return False
    except Exception:
        # A missing table, SQLite outage or ambiguous profile must never
        # manufacture success; no secrets or DB exception body are logged.
        return False
