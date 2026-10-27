"""
Non-secret settings persistence.

Owner-configurable, non-secret settings (voice/TTS selection, retention, file
roots, provider config) live in the `settings_metadata` table. Secrets never
touch this table — they belong to the write-only secret store
(backend/core/secrets.py). Values are validated against a whitelist before
being persisted.
"""

from __future__ import annotations

import logging
import os
import threading
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.database import SettingsMetadata

logger = logging.getLogger(__name__)
_settings_update_lock = threading.RLock()

# Keys that may be read/updated through the local API. Anything not listed is
# rejected so the model cannot inject arbitrary configuration.
ALLOWED_SETTINGS = frozenset(
    {
        "voice_enabled",
        "voice_recording_enabled",
        "voice_tts_voice",
        "retention_days",
        "file_roots",
        # Phase 4: explicit operator switch for event-driven background
        # orchestration. Default OFF (safe); enabling is an owner decision.
        "background_orchestration_enabled",
    }
)

_INT_KEYS = frozenset({"retention_days"})
_BOOL_KEYS = frozenset({"voice_enabled", "voice_recording_enabled",
                        "background_orchestration_enabled"})


def get_setting(db: Session, key: str) -> Optional[str]:
    """Read one persisted setting value (None when unset)."""
    row = db.query(SettingsMetadata).filter_by(key=key).populate_existing().first()
    return row.value if row else None


def set_setting(db: Session, key: str, value: str) -> None:
    """Persist one non-secret internal setting, bypassing the public whitelist.

    For internal system keys only (e.g. session state) that are never
    model-configurable through ALLOWED_SETTINGS/update_settings(). The row is
    upserted; the caller's session owns the commit.
    """
    existing = db.query(SettingsMetadata).filter_by(key=key).first()
    if existing:
        existing.value = value
    else:
        db.add(SettingsMetadata(key=key, value=value))


def get_settings(db: Session) -> Dict[str, Any]:
    """Return all persisted non-secret settings as typed values."""
    rows = db.query(SettingsMetadata).all()
    values: Dict[str, Any] = {}
    for row in rows:
        values[row.key] = _coerce_value(row.key, row.value)
    return values


def resolved_file_roots(db: Session) -> List[str]:
    """Approved file roots as enforced today: boot defaults + live grants.

    `SYTHION_FILE_ROOTS` from the environment are the boot defaults for this
    device; `file_roots` granted at runtime through the settings API (persisted
    in this table) take effect immediately, without a restart. The two are
    merged and de-duplicated so a grant adds access rather than replacing it.
    Returns an empty list when nothing is configured.
    """
    from backend.core.config import settings

    raw: List[str] = list(settings.file_roots or [])
    persisted = get_setting(db, "file_roots")
    if persisted is not None:
        import json

        parsed = json.loads(persisted)
        if not isinstance(parsed, list) or any(not isinstance(r, str) for r in parsed):
            raise ValueError("Invalid persisted file roots")
        raw.extend(r.strip() for r in parsed if r.strip())

    seen = set()
    merged: List[str] = []
    for entry in raw:
        norm_key = os.path.normcase(os.path.expanduser(entry))
        if norm_key not in seen:
            seen.add(norm_key)
            merged.append(entry)
    return merged


def update_settings(db: Session, updates: Dict[str, Any], *, fallback_ack: Any = None) -> Dict[str, Any]:
    """Validate and persist a batch of non-secret settings.

    Returns the full typed settings dict after the update. Raises ValueError
    for unknown keys or invalid values (nothing is persisted on failure).
    """
    if not isinstance(updates, dict):
        raise ValueError("Settings update must be an object")

    validated: Dict[str, str] = {}
    for key, raw_value in updates.items():
        if key not in ALLOWED_SETTINGS:
            raise ValueError(f"Unknown or non-configurable setting: {key}")
        validated[key] = _validate_value(key, raw_value)

    # Serialize the check and write against all settings_store writers in this
    # process. A stale confirmation must not authorize a different grant set.
    with _settings_update_lock:
        guarded_root_value = None
        if "file_roots" in validated:
            import json
            from backend.core.config import settings
            from backend.tools.file_tools import _effective_roots

            new_roots = json.loads(validated["file_roots"])
            if fallback_ack is not None and (new_roots or settings.file_roots):
                raise ValueError("File-root state changed; refresh settings and confirm again")
            if not new_roots and not settings.file_roots:
                persisted_raw = get_setting(db, "file_roots")
                persisted_roots = json.loads(persisted_raw) if persisted_raw is not None else []
                if not isinstance(persisted_roots, list) or any(not isinstance(root, str) for root in persisted_roots):
                    raise ValueError("Invalid persisted file roots")
                guarded_root_value = persisted_raw
                if any(root.strip() for root in persisted_roots):
                    expected = {
                        "persisted_file_roots": persisted_roots,
                        "fallback_file_roots": [str(root) for root in _effective_roots([])],
                    }
                    if fallback_ack != expected:
                        raise ValueError("Clearing the final file grant requires acknowledgement of the current grants and fallback file roots; refresh settings and confirm")
                elif fallback_ack is not None:
                    raise ValueError("File-root state changed; refresh settings and confirm again")
        elif fallback_ack is not None:
            raise ValueError("File-root acknowledgement requires a file_roots update")

        for key, value in validated.items():
            if key == "file_roots" and guarded_root_value is not None:
                changed = db.query(SettingsMetadata).filter_by(key=key, value=guarded_root_value).update(
                    {SettingsMetadata.value: value}, synchronize_session=False
                )
                if changed != 1:
                    db.rollback()
                    raise ValueError("File-root state changed; refresh settings and confirm again")
                db.expire_all()
                continue
            existing = db.query(SettingsMetadata).filter_by(key=key).first()
            if existing:
                existing.value = value
            else:
                db.add(SettingsMetadata(key=key, value=value))
        db.commit()
    logger.info("Settings updated: %s", sorted(validated.keys()))
    return get_settings(db)


def _validate_value(key: str, raw_value: Any) -> str:
    if key in _BOOL_KEYS:
        if isinstance(raw_value, bool):
            return "true" if raw_value else "false"
        if isinstance(raw_value, str) and raw_value.strip().lower() in ("true", "false"):
            return "true" if raw_value.strip().lower() == "true" else "false"
        raise ValueError(f"Setting {key} must be a boolean")
    if key in _INT_KEYS:
        try:
            value = int(raw_value)
        except (TypeError, ValueError):
            raise ValueError(f"Setting {key} must be an integer")
        if value < 1:
            raise ValueError(f"Setting {key} must be >= 1")
        return str(value)
    if key == "file_roots":
        import json

        if isinstance(raw_value, str):
            raw_value = raw_value.split(",")
        if not isinstance(raw_value, list):
            raise ValueError("Setting file_roots must be a list")
        roots = [str(r).strip() for r in raw_value if str(r).strip()]
        return json.dumps(roots)
    # String values: reject control characters and cap length.
    text = str(raw_value)
    if len(text) > 4096:
        raise ValueError(f"Setting {key} is too long")
    if any(ch < " " for ch in text):
        raise ValueError(f"Setting {key} contains control characters")
    return text


def _coerce_value(key: str, value: str) -> Any:
    if key in _BOOL_KEYS:
        return value.strip().lower() == "true"
    if key in _INT_KEYS:
        try:
            return int(value)
        except ValueError:
            return 0
    if key == "file_roots":
        import json

        try:
            parsed = json.loads(value)
            return parsed if isinstance(parsed, list) else []
        except ValueError:
            return []
    return value
