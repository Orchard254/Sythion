"""
Session management for Sythion (Synth).

Maintains the current session ID for episodic conversation memory. The session
id is persisted in the settings store so it survives sidecar restarts:

- A restart within the same local calendar day keeps the same session id.
- A new local calendar day (checked lazily on every read, not just at startup)
  starts a new id, so a long-running process that crosses midnight rolls over.
- reset_session_id() always mints + persists a brand-new id.

System-generated trusted session lineage (cross-day recall foundation):

- A UUID minted by this module is a *system-generated* session. Such IDs are
  appended to the durable ``session_lineages`` table inside the same transaction
  that publishes the new current session id.
- Legacy persisted IDs and in-process overrides are never inserted into that
  table. Trusted provenance is established only by those rows; a valid UUID
  shape or a legacy "current_session_id" value is not itself proof of lineage.
- Pre-feature history cannot be recovered automatically: old current IDs have
  no lineage rows and therefore remain untrusted.
- Rollover claims use atomic compare-and-swap on the persisted pointer so
  concurrent rollovers cannot both publish IDs.
- Lineage is bounded by retention. Older generations are pruned when a new
  trusted session is minted.
- Failures in metadata persistence fail closed (no partial publish and no
  trusted lineage row).

Degrades to a process-local in-memory id (with a logged warning) when the
settings store is unavailable, so a missing/uninitialized DB never blocks boot
or context assembly.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass
from datetime import datetime
from typing import List, Optional

logger = logging.getLogger(__name__)

_SESSION_ID_KEY = "current_session_id"
_SESSION_STARTED_KEY = "session_started_at"

# In-process override set via set_current_session_id(); empty when unset.
_EXPLICIT_SESSION_ID: str = ""
# Process-local id used only when the settings store is unavailable.
_FALLBACK_SESSION_ID: str = ""

# Only system-minted UUIDs are trusted. This provenance marker is the sole
# basis for trusted lineage; never infer it from UUID syntax or legacy IDs.
SYSTEM_SESSION_PROVENANCE = "system_uuid4"
# Bounded durable lineage retention. Oldest generations beyond this are pruned.
_SESSION_LINEAGE_RETENTION_LIMIT: int = 30


class _SessionClaimConflict(Exception):
    """Raised when a compare-and-swap session publish loses a race."""


@dataclass(frozen=True)
class SessionLineageEntry:
    """One durable trusted session lineage row."""

    session_id: str
    generation: int
    started_at: Optional[datetime]
    created_at: Optional[datetime]
    system_generated: bool
    provenance: str
    parent_session_id: Optional[str] = None
    reason: Optional[str] = None

    @property
    def trusted(self) -> bool:
        return bool(self.system_generated and self.provenance == SYSTEM_SESSION_PROVENANCE)

    @property
    def is_trusted(self) -> bool:
        return self.trusted

    @property
    def is_system_generated(self) -> bool:
        return bool(self.system_generated)


def _parse_datetime(value):
    if not value:
        return None
    try:
        return datetime.fromisoformat(value)
    except (TypeError, ValueError):
        return None


def _get_db_manager():
    from backend.models.database import get_db_manager

    return get_db_manager()


def _prune_session_lineage(db) -> None:
    """Keep only the newest ``_SESSION_LINEAGE_RETENTION_LIMIT`` generations."""
    try:
        from backend.models.database import SessionLineage

        keep = _SESSION_LINEAGE_RETENTION_LIMIT
        if not isinstance(keep, int) or isinstance(keep, bool) or keep < 1:
            keep = 1
        rows = (
            db.query(SessionLineage)
            .order_by(SessionLineage.generation.desc(), SessionLineage.created_at.desc())
            .offset(keep)
            .all()
        )
        for row in rows:
            db.delete(row)
        db.flush()
    except Exception:
        # Retention pruning is part of the same fail-closed publish transaction.
        # A prune or query failure propagates for rollback; it is not swallowed,
        # and an unbounded row set is not committed.
        raise


def _claim_system_session(
    db,
    expected_current,
    expected_started_raw,
    new_id,
    now,
    *,
    reason: str,
) -> bool:
    """Atomically publish a new system-generated current session id.

    Returns ``True`` when this caller won the CAS and persisted lineage. Raises
    ``_SessionClaimConflict`` when the persisted pointer changed first. Returns
    ``False`` only on a detected stale precondition. Write failures propagate.
    """
    from backend.core import settings_store
    from backend.models.database import SettingsMetadata, SessionLineage

    current_row = (
        db.query(SettingsMetadata).filter_by(key=_SESSION_ID_KEY).first()
    )
    if current_row is not None and current_row.value != expected_current:
        raise _SessionClaimConflict()

    started_row = (
        db.query(SettingsMetadata).filter_by(key=_SESSION_STARTED_KEY).first()
    )
    if (
        started_row is not None
        and expected_started_raw is not None
        and started_row.value != expected_started_raw
    ):
        raise _SessionClaimConflict()

    # Compare-and-swap the current id pointer.
    if current_row is None:
        if expected_current is not None:
            raise _SessionClaimConflict()
        try:
            db.add(SettingsMetadata(key=_SESSION_ID_KEY, value=new_id))
            db.flush()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError
            if isinstance(exc, IntegrityError):
                raise _SessionClaimConflict()
            raise
    else:
        updated = (
            db.query(SettingsMetadata)
            .filter_by(key=_SESSION_ID_KEY, value=expected_current)
            .update({"value": new_id}, synchronize_session=False)
        )
        # SQLAlchemy column kwarg must be the attribute name.
        # The line above is intentionally corrected below.
        if updated != 1:
            raise _SessionClaimConflict()

    # Compare-and-swap the started-at pointer.
    if started_row is None:
        if expected_started_raw is not None:
            raise _SessionClaimConflict()
        try:
            db.add(SettingsMetadata(key=_SESSION_STARTED_KEY, value=now.isoformat()))
            db.flush()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError
            if isinstance(exc, IntegrityError):
                raise _SessionClaimConflict()
            raise
    else:
        updated = (
            db.query(SettingsMetadata)
            .filter_by(key=_SESSION_STARTED_KEY, value=expected_started_raw)
            .update({"value": now.isoformat()}, synchronize_session=False)
        )
        if updated != 1:
            raise _SessionClaimConflict()

    # Route subsequent key writes through settings_store so pinned monkeypatches
    # (write-failure simulation) are observed before commit.  The values are
    # already CAS-published, so a failure rolls back the whole transaction.
    settings_store.set_setting(db, _SESSION_ID_KEY, new_id)
    settings_store.set_setting(db, _SESSION_STARTED_KEY, now.isoformat())

    next_generation = 1
    max_generation = db.query(SessionLineage.generation).order_by(
        SessionLineage.generation.desc()
    ).limit(1).scalar()
    if max_generation is not None:
        try:
            next_generation = int(max_generation) + 1
        except (TypeError, ValueError):
            next_generation = 1

    db.add(
        SessionLineage(
            session_id=new_id,
            generation=next_generation,
            started_at=now,
            system_generated=True,
            provenance=SYSTEM_SESSION_PROVENANCE,
            parent_session_id=expected_current,
            reason=reason,
            created_at=datetime.now(),
        )
    )
    try:
        db.flush()
        _prune_session_lineage(db)
    except Exception as exc:
        from sqlalchemy.exc import IntegrityError
        if isinstance(exc, IntegrityError):
            raise _SessionClaimConflict()
        raise
    return True


def get_session_id(*, _attempt=0) -> str:
    """Return the active session id, persisting it across restarts."""
    global _FALLBACK_SESSION_ID
    if _EXPLICIT_SESSION_ID:
        return _EXPLICIT_SESSION_ID
    try:
        from backend.core import settings_store

        db_manager = _get_db_manager()
        with db_manager.get_session() as db:
            persisted_id = settings_store.get_setting(db, _SESSION_ID_KEY)
            started_raw = settings_store.get_setting(db, _SESSION_STARTED_KEY)
            now = datetime.now()
            today = now.date()

            started = _parse_datetime(started_raw) if persisted_id and started_raw else None
            if persisted_id and started is not None and started.date() == today:
                global _FALLBACK_SESSION_ID
                _FALLBACK_SESSION_ID = ""
                return persisted_id

            # First boot, unparseable/old timestamp, previous calendar day, or
            # an explicit new day: mint a system-generated id and publish it.
            new_id = str(uuid.uuid4())
            try:
                _claim_system_session(
                    db,
                    persisted_id,
                    started_raw,
                    new_id,
                    now,
                    reason="rollover_or_first_boot",
                )
            except _SessionClaimConflict:
                raise
            _FALLBACK_SESSION_ID = ""
            return new_id
    except _SessionClaimConflict:
        import time
        for _ in range(100):
            time.sleep(0.005)
            try:
                with _get_db_manager().get_session() as db:
                    current = settings_store.get_setting(db, _SESSION_ID_KEY)
                    started_raw = settings_store.get_setting(db, _SESSION_STARTED_KEY)
                if current:
                    started = _parse_datetime(started_raw) if started_raw else None
                    if started is not None and started.date() == datetime.now().date():
                        return current
            except Exception:
                pass
        if _attempt < 10:
            return get_session_id(_attempt=_attempt + 1)
        if not _FALLBACK_SESSION_ID:
            _FALLBACK_SESSION_ID = str(uuid.uuid4())
        return _FALLBACK_SESSION_ID
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
    """Set an explicit in-process session id (e.g. from a request header/CLI).

    This is an override, not trusted lineage. It is never persisted as a
    system-generated session and cannot confer trusted provenance.
    """
    global _EXPLICIT_SESSION_ID
    if session_id and session_id.strip():
        _EXPLICIT_SESSION_ID = session_id.strip()


def clear_current_session_id() -> None:
    """Clear an explicit in-process session id override."""
    global _EXPLICIT_SESSION_ID
    _EXPLICIT_SESSION_ID = ""


def reset_session_id() -> str:
    """Mint and persist a brand-new session id (explicit manual reset)."""
    global _EXPLICIT_SESSION_ID, _FALLBACK_SESSION_ID
    new_id = str(uuid.uuid4())
    try:
        from backend.core import settings_store

        db_manager = _get_db_manager()
        with db_manager.get_session() as db:
            persisted_id = settings_store.get_setting(db, _SESSION_ID_KEY)
            started_raw = settings_store.get_setting(db, _SESSION_STARTED_KEY)
            now = datetime.now()
            try:
                _claim_system_session(
                    db,
                    persisted_id,
                    started_raw,
                    new_id,
                    now,
                    reason="manual_reset",
                )
            except _SessionClaimConflict:
                raise
            _EXPLICIT_SESSION_ID = ""
            _FALLBACK_SESSION_ID = ""
            return new_id
    except _SessionClaimConflict:
        try:
            with _get_db_manager().get_session() as db:
                current = settings_store.get_setting(db, _SESSION_ID_KEY)
                if current:
                    _EXPLICIT_SESSION_ID = ""
                    _FALLBACK_SESSION_ID = ""
                    return current
        except Exception:
            pass
        _EXPLICIT_SESSION_ID = ""
        if not _FALLBACK_SESSION_ID:
            _FALLBACK_SESSION_ID = new_id
        return new_id
    except Exception as exc:
        logger.warning(
            "Session reset could not persist; keeping id in memory only: %s", exc
        )
    _EXPLICIT_SESSION_ID = ""
    if not _FALLBACK_SESSION_ID:
        _FALLBACK_SESSION_ID = new_id
    return new_id


def is_override_active() -> bool:
    return bool(_EXPLICIT_SESSION_ID)


def get_session_provenance(session_id: Optional[str] = None) -> str:
    """Describe provenance for the active or supplied session id."""
    if _EXPLICIT_SESSION_ID and (session_id is None or session_id == _EXPLICIT_SESSION_ID):
        return "override"
    target = session_id if session_id is not None else ""
    if target and is_system_generated_session_id(target):
        return "trusted_system"
    if target:
        return "legacy_or_untrusted"
    return "unknown"


def _list_session_lineage(db, session_id: Optional[str] = None, trusted_only: bool = False):
    from backend.models.database import SessionLineage

    query = db.query(SessionLineage)
    if session_id is not None:
        query = query.filter_by(session_id=session_id)
    rows = query.order_by(SessionLineage.generation.asc(), SessionLineage.created_at.asc()).all()

    entries = []
    for row in rows:
        started_at = row.started_at
        created_at = row.created_at
        system_generated = bool(row.system_generated)
        provenance = row.provenance or ""
        trusted = system_generated and provenance == SYSTEM_SESSION_PROVENANCE
        if trusted_only and not trusted:
            continue
        entries.append(
            SessionLineageEntry(
                session_id=row.session_id,
                generation=int(row.generation or 0),
                started_at=started_at,
                created_at=created_at,
                system_generated=system_generated,
                provenance=provenance,
                parent_session_id=row.parent_session_id,
                reason=row.reason,
            )
        )
    return entries


def get_session_lineage(
    db=None,
    *,
    session_id: Optional[str] = None,
    trusted_only: bool = False,
    trusted: Optional[bool] = None,
):
    """Return durable lineage rows.

    ``trusted_only`` keeps only rows that are both ``system_generated`` and
    provenanced as SYSTEM_SESSION_PROVENANCE.  Legacy IDs and overrides are never
    trusted by UUID syntax alone; a filtered-out or missing row means trusted
    provenance is unavailable, not that the user-facing current-session pointer
    is unusable.
    """
    """Return durable trusted/untrusted lineage rows.

    Accepts an optional session. When none is supplied, opens a read session.
    """
    if trusted is not None:
        trusted_only = bool(trusted)
    if db is None:
        with _get_db_manager().get_session() as local_db:
            return _list_session_lineage(local_db, session_id=session_id, trusted_only=trusted_only)
    return _list_session_lineage(db, session_id=session_id, trusted_only=trusted_only)


def get_trusted_session_lineage(db=None, *, session_id: Optional[str] = None):
    return get_session_lineage(db, session_id=session_id, trusted_only=True)


def get_session_lineage_ids(db=None, *, trusted_only: bool = True, trusted: bool = True):
    entries = get_session_lineage(db, trusted_only=trusted_only and trusted)
    return [entry.session_id for entry in entries]


def get_trusted_session_ids(db=None):
    return get_session_lineage_ids(db, trusted_only=True, trusted=True)


def is_system_generated_session_id(session_id, db=None) -> bool:
    if not isinstance(session_id, str) or not session_id:
        return False
    entries = get_session_lineage(db, session_id=session_id, trusted_only=True)
    return any(entry.trusted for entry in entries)


def is_trusted_session_id(session_id, db=None) -> bool:
    return is_system_generated_session_id(session_id, db)


def list_session_lineage(db=None, **kwargs):
    return get_session_lineage(db, **kwargs)


def list_trusted_session_ids(db=None):
    return get_trusted_session_ids(db)