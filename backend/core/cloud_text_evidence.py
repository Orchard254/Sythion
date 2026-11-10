"""Time-bounded, identity-scoped proof of a successful plain-text cloud reply.

Text capability is separate from function-call capability. This is evidence
that ONE stateless text request succeeded, not authorization for tasks, normal
assistant routing, memory access, or ongoing provider availability.

The record holds no prompts, answer content or credential. It binds the
exact staged provider + model + stage generation and current credential with
a purpose-separated HMAC. Expiry, rotation or tampering fails closed.
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

SETTING_KEY = "cloud_preview_text_evidence_v1"
VERSION = 1
MAX_AGE_SECONDS = 3600


def _mac(snapshot: Dict[str, str], credential: str, timestamp: int) -> str:
    payload = json.dumps([
        "sythion-text-preview-v1",
        snapshot["provider_id"], snapshot["model_id"],
        snapshot["generation"], timestamp,
    ], separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hmac.new(credential.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def persist_text_evidence(db: Any, snapshot: Dict[str, str], credential: str) -> None:
    """Call only after one successful plain-text reply, never from listing."""
    if not credential or snapshot != profile.get_staged_cloud_snapshot():
        raise ValueError("Text preview identity not current")
    timestamp = int(time.time())
    record = {
        "version": VERSION,
        "provider_id": snapshot["provider_id"],
        "model_id": snapshot["model_id"],
        "generation": snapshot["generation"],
        "issued_at": timestamp,
        "mac": _mac(snapshot, credential, timestamp),
    }
    set_setting(db, SETTING_KEY, json.dumps(record, separators=(",", ":")))
    db.commit()


def is_text_evidence_current(db: Any, *, now: Optional[int] = None) -> bool:
    """Always return False on missing/changed credentials, stage, time or DB."""
    try:
        snap = profile.get_staged_cloud_snapshot()
        if not snap:
            return False
        raw = get_setting(db, SETTING_KEY)
        if not isinstance(raw, str) or len(raw) > 2048:
            return False
        record = json.loads(raw)
        if not isinstance(record, dict) or type(record.get("version")) is not int:
            return False
        if record["version"] != VERSION:
            return False
        if any(record.get(k) != snap[k] for k in (
            "provider_id", "model_id", "generation",
        )):
            return False
        issued = record.get("issued_at")
        current = int(time.time()) if now is None else now
        if type(issued) is not int or type(current) is not int:
            return False
        if issued < 0 or current < issued or current - issued > MAX_AGE_SECONDS:
            return False
        sig = record.get("mac")
        if not isinstance(sig, str) or len(sig) != 64:
            return False
        credential = resolve_compatible_provider_key(snap["provider_id"])
        if not credential:
            return False
        return hmac.compare_digest(_mac(snap, credential, issued), sig)
    except Exception:
        # No logging of sensitive third-party or storage errors in readiness.
        return False
