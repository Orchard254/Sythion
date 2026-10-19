"""
Session management for Sythion (Synth).

Maintains the current session ID for episodic conversation memory. The session
id is persisted in the settings store so it survives sidecar restarts:

- A restart within the same local calendar day keeps the same session id.
- A new local calendar day (checked lazily on every read, not just at startup)
  starts a new id, so a long-running process that crosses midnight rolls over.
- reset_session_id() always mints + persists a brand-new id.

Degrades to a process-local in-memory id (with a logged warning) when the
settings store is unavailable, so a missing/uninitialized DB never blocks boot
or context assembly.
"""

import logging
import uuid
from datetime import datetime

logger = logging.getLogger(__name__)

_SESSION_ID_KEY = "current_session_id"
_SESSION_STARTED_KEY = "session_started_at"

# In-process override set via set_current_session_id(); empty when unset.
_EXPLICIT_SESSION_ID: str = ""
# Process-local id used only when the settings store is unavailable.
_FALLBACK_SESSION_ID: str = ""


def get_session_id() -> str:
    """Return the active session id, persisting it across restarts.

    Reuses the persisted id unless nothing is persisted yet (first boot) or the
    persisted `session_started_at` is on a previous local calendar day -- then a
    fresh id is minted and persisted. Checks on every read so a long-running
    process rolls over at midnight without a restart. Falls back to a stable
    in-memory id (and logs) if the settings store is unavailable.
    """
    global _FALLBACK_SESSION_ID
    if _EXPLICIT_SESSION_ID:
        return _EXPLICIT_SESSION_ID
    try:
        from backend.core.settings_store import get_setting, set_setting
        from backend.models.database import get_db_manager

        with get_db_manager().get_session() as db:
            persisted_id = get_setting(db, _SESSION_ID_KEY)
            started_raw = get_setting(db, _SESSION_STARTED_KEY)
            now = datetime.now()
            today = now.date()

            if persisted_id and started_raw:
                try:
                    started = datetime.fromisoformat(started_raw)
                except ValueError:
                    started = None
                if started is not None and started.date() == today:
                    return persisted_id

            # First boot, unparseable/old timestamp, or a previous calendar day.
            new_id = str(uuid.uuid4())
            set_setting(db, _SESSION_ID_KEY, new_id)
            set_setting(db, _SESSION_STARTED_KEY, now.isoformat())
            _FALLBACK_SESSION_ID = ""
            return new_id
    except Exception as exc:
        logger.warning(
            "Session persistence unavailable; using in-memory session id: %s", exc
        )
        if not _FALLBACK_SESSION_ID:
            _FALLBACK_SESSION_ID = str(uuid.uuid4())
        return _FALLBACK_SESSION_ID


def get_current_session_id() -> str:
    """Return the active session id (backward-compatible accessor)."""
    return get_session_id()


def set_current_session_id(session_id: str) -> None:
    """Set an explicit in-process session id (e.g. from a request header or CLI).

    An explicit id takes precedence over persisted/rollover state until
    reset_session_id() is called.
    """
    global _EXPLICIT_SESSION_ID
    if session_id and session_id.strip():
        _EXPLICIT_SESSION_ID = session_id.strip()


def reset_session_id() -> str:
    """Mint and persist a brand-new session id (explicit manual reset).

    Clears any in-process override so the freshly persisted id is what
    subsequent reads return. Falls back to in-memory if persistence fails.
    """
    global _EXPLICIT_SESSION_ID, _FALLBACK_SESSION_ID
    new_id = str(uuid.uuid4())
    try:
        from backend.core.settings_store import set_setting
        from backend.models.database import get_db_manager

        with get_db_manager().get_session() as db:
            set_setting(db, _SESSION_ID_KEY, new_id)
            set_setting(db, _SESSION_STARTED_KEY, datetime.now().isoformat())
    except Exception as exc:
        logger.warning(
            "Session reset could not persist; keeping id in memory only: %s", exc
        )
    _EXPLICIT_SESSION_ID = ""
    _FALLBACK_SESSION_ID = new_id
    return new_id
