"""Short-lived evidence that a candidate follows Sythion's real Assistant contract.

Distinct from plain-text and synthetic function-call probes. One bounded
owner-triggered provider request; no tasks, memory writes or assistant
conversation persisted. Do not store prompts, results or credentials.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import time
from typing import Any

from backend.core import profile
from backend.core.secrets import resolve_compatible_provider_key
from backend.core.settings_store import get_setting, set_setting

SETTING_KEY = "cloud_assistant_contract_evidence_v1"
VERSION = 1
MAX_AGE_SECONDS = 3600


def _signature(snapshot: dict, key: str, issued_at: int) -> str:
    payload = json.dumps([
        "sythion-assistant-contract-v1",
        snapshot["provider_id"], snapshot["model_id"],
        snapshot["generation"], issued_at,
    ], separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return hmac.new(key.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def persist_assistant_contract_evidence(db: Any, snapshot: dict, key: str) -> None:
    if not key or snapshot != profile.get_staged_cloud_snapshot():
        raise ValueError("Assistant contract candidate changed")
    issued_at = int(time.time())
    record = {
        "version": VERSION,
        "provider_id": snapshot["provider_id"],
        "model_id": snapshot["model_id"],
        "generation": snapshot["generation"],
        "issued_at": issued_at,
        "mac": _signature(snapshot, key, issued_at),
    }
    set_setting(db, SETTING_KEY, json.dumps(record, separators=(",", ":")))
    db.commit()


def is_assistant_contract_evidence_current(db: Any, *, now: int | None = None) -> bool:
    try:
        snapshot = profile.get_staged_cloud_snapshot()
        if not snapshot:
            return False
        raw = get_setting(db, SETTING_KEY)
        if not isinstance(raw, str) or len(raw) > 2048:
            return False
        record = json.loads(raw)
        if not isinstance(record, dict) or set(record) != {
            "version", "provider_id", "model_id", "generation", "issued_at", "mac",
        } or type(record.get("version")) is not int or record["version"] != VERSION:
            return False
        if any(record.get(field) != snapshot[field] for field in (
            "provider_id", "model_id", "generation",
        )):
            return False
        issued = record["issued_at"]
        current = int(time.time()) if now is None else now
        if type(issued) is not int or type(current) is not int or issued < 0:
            return False
        if current < issued or current - issued > MAX_AGE_SECONDS:
            return False
        secret = resolve_compatible_provider_key(snapshot["provider_id"])
        mac = record.get("mac")
        if not secret or not isinstance(mac, str) or len(mac) != 64:
            return False
        return hmac.compare_digest(_signature(snapshot, secret, issued), mac)
    except Exception:
        return False
