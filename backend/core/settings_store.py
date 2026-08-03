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
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.database import SettingsMetadata

logger = logging.getLogger(__name__)

# Keys that may be read/updated through the local API. Anything not listed is
# rejected so the model cannot inject arbitrary configuration.
ALLOWED_SETTINGS = frozenset(
    {
        "voice_enabled",
        "voice_recording_enabled",
        "voice_tts_voice",
        "retention_days",
        "file_roots",
    }
)

_INT_KEYS = frozenset({"retention_days"})
_BOOL_KEYS = frozenset({"voice_enabled", "voice_recording_enabled"})


def get_setting(db: Session, key: str) -> Optional[str]:
    """Read one persisted setting value (None when unset)."""
    row = db.query(SettingsMetadata).filter_by(key=key).first()
    return row.value if row else None


def get_settings(db: Session) -> Dict[str, Any]:
    """Return all persisted non-secret settings as typed values."""
    rows = db.query(SettingsMetadata).all()
    values: Dict[str, Any] = {}
    for row in rows:
        values[row.key] = _coerce_value(row.key, row.value)
    return values


def update_settings(db: Session, updates: Dict[str, Any]) -> Dict[str, Any]:
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

    for key, value in validated.items():
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
