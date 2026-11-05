"""Fail-closed read-only cross-day context recall foundation.

Cross-day context recall needs a genuine, authenticated profile/task ownership
boundary before any stored conversation or task context can be returned.  Such
a boundary does not exist in the available durable schema.  Therefore every
public reader in this module is intentionally read-only, performs **no** query
against conversation, task, session, profile, vault, device, or network state,
and returns no records.

Session lineage, UUID syntax, caller-supplied session/profile labels, caller
booleans, and monkeypatched helpers are identifiers or observations only.  None
of them is authentication, and none is ever treated as permission.

Text embedded in immutable records is supplied reference data only when a future
authenticated boundary is available; this module never emits it now.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class CrossDaySessionProvenance:
    """Placeholder provenance marker.

    Returned only as a schema shape when a future authenticated boundary
    exists.  The current foundation never populates session rows.
    """

    session_id: str
    generation: Optional[int]
    system_generated: bool
    provenance: str
    parent_session_id: Optional[str]
    trusted: bool
    included: bool
    exclusion_reason: Optional[str] = None


@dataclass(frozen=True)
class CrossDayContextRecord:
    """Placeholder context record shape.

    This foundation never returns stored text or context records because the
    authenticated profile/task ownership boundary is unavailable.
    """

    record_id: str
    session_id: str
    exchange_id: str
    context_kind: str
    created_at: datetime
    user_text: str
    assistant_text: str
    task_id: Optional[str]
    task_state: Optional[str]
    task_version: Optional[int]
    task_available: bool
    association_present: bool
    integrity_issues: Tuple[str, ...]
    session_provenance: str
    session_trusted: bool


@dataclass(frozen=True)
class CrossDayContextSnapshot:
    """Fail-closed read result for cross-day context recall."""

    requested_session_ids: Tuple[str, ...] = ()
    observed_at: datetime = datetime.utcnow()  # type: ignore[assignment]
    observed_sessions: Tuple[CrossDaySessionProvenance, ...] = ()
    trusted_session_ids: Tuple[str, ...] = ()
    excluded_session_ids: Tuple[str, ...] = ()
    records: Tuple[CrossDayContextRecord, ...] = ()
    has_more: bool = False
    coverage: str = "no_records_without_authenticated_profile_task_boundary"
    ambiguous: bool = True
    ambiguity_reasons: Tuple[str, ...] = ("profile_task_boundary_unauthenticated",)
    read_only: bool = True
    mutations_performed: Tuple[str, ...] = ()
    data_returned: bool = False
    disclosure_allowed: bool = False
    access_allowed: bool = False
    fail_closed: bool = True
    cross_profile_disclosure_blocked: bool = True
    profile_boundary_enforced: bool = False
    profile_boundary_status: str = "not_authenticated_caller_responsible"


def _require_no_autoflush_contract(db: Any) -> None:
    if db is None:
        raise ValueError("db is required")
    no_autoflush = getattr(db, "no_autoflush", None)
    if no_autoflush is None:
        raise ValueError("db must expose no_autoflush")


def _normalize_session_ids(session_ids: Any) -> List[str]:
    """Validate syntax only; never use labels as authority."""
    if session_ids is None:
        return []
    if isinstance(session_ids, str):
        session_ids = [session_ids]
    if not isinstance(session_ids, Iterable):
        raise ValueError("Session scope must be an iterable of strings if supplied")
    normalized: List[str] = []
    seen = set()
    for item in session_ids:
        if not isinstance(item, str) or not item.strip():
            raise ValueError("Every session id must be a non-empty string")
        value = item.strip()
        if value in seen:
            continue
        seen.add(value)
        normalized.append(value)
    return normalized


def _validate_limit(limit: Any) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Candidate limit must be an integer from 1 to 100")
    if not 1 <= limit <= 100:
        raise ValueError("Candidate limit must be an integer from 1 to 100")
    return limit


def read_cross_day_context_recall(
    db: Any,
    session_ids: Any = None,
    *,
    limit: int = 20,
    trusted_only: bool = False,
    allow_untrusted: bool = True,
    include_untrusted: bool = False,
    profile: Any = None,
    profile_id: Any = None,
    profiles: Any = None,
    cross_profile: bool = False,
    **kwargs: Any,
) -> CrossDayContextSnapshot:
    """Return no records because the authenticated boundary is absent.

    All parameters are accepted only for compatibility and are ignored.  No
    boolean, label, lineage row, or injectable helper is treated as proof of
    ownership.
    """
    _require_no_autoflush_contract(db)
    _validate_limit(limit)  # validates caller input without disclosure
    _normalize_session_ids(session_ids)

    # The context manager is entered to honor the read contract, but no SQL
    # or observation is issued.
    with db.no_autoflush:
        return CrossDayContextSnapshot(
            ambiguous=True,
            ambiguity_reasons=("profile_task_boundary_unauthenticated",),
        )


# Compatibility read aliases. All are equally fail-closed.
read_context_recall = read_cross_day_context_recall
read_context_candidates_cross_day = read_cross_day_context_recall
read_preserved_context_recall = read_cross_day_context_recall


class CrossDayContextRecallService:
    def __init__(self, db: Any = None, /, *args: Any, **kwargs: Any):
        self._db = db
        for key in ("database", "db_session", "session", "session_db"):
            if self._db is None and kwargs.get(key) is not None:
                self._db = kwargs.get(key)
        # Retain only the raw db; scope and flags are never trusted.
        self._limit = int(kwargs.get("limit", 20)) if isinstance(kwargs.get("limit", 20), int) else 20

    def read(self, *args: Any, db: Any = None, **kwargs: Any) -> CrossDayContextSnapshot:
        effective_db = self._db if db is None else db
        limit = kwargs.get("limit", self._limit)
        try:
            limit_int = int(limit)
        except (TypeError, ValueError):
            limit_int = self._limit
        return read_cross_day_context_recall(effective_db, None, limit=limit_int)

    recall = read
    read_cross_day_context_recall = staticmethod(read_cross_day_context_recall)
    read_context_recall = staticmethod(read_context_recall)


CrossDayContextRecall = CrossDayContextRecallService
CrossDayContextRecallFoundation = CrossDayContextRecallService
ReadOnlyCrossDayContextRecall = CrossDayContextRecallService


from dataclasses import asdict, fields


def _snapshot_get(self, key, default=None):
    if isinstance(key, str) and hasattr(self, key):
        return getattr(self, key)
    return default


def _snapshot_getitem(self, key):
    if isinstance(key, str) and hasattr(self, key):
        return getattr(self, key)
    raise KeyError(key)


def _snapshot_to_dict(self):
    data = {}
    for field in fields(self):
        value = getattr(self, field.name)
        if isinstance(value, tuple):
            data[field.name] = [
                item.to_dict() if hasattr(item, "to_dict") else item for item in value
            ]
        elif hasattr(value, "to_dict"):
            data[field.name] = value.to_dict()
        else:
            data[field.name] = value
    return data


def _snapshot_keys(self):
    return (field.name for field in fields(self))


CrossDayContextSnapshot.get = _snapshot_get
CrossDayContextSnapshot.__getitem__ = _snapshot_getitem
CrossDayContextSnapshot.to_dict = _snapshot_to_dict
CrossDayContextSnapshot.keys = _snapshot_keys


__all__ = [
    "CrossDaySessionProvenance",
    "CrossDayContextRecord",
    "CrossDayContextSnapshot",
    "read_cross_day_context_recall",
    "read_context_recall",
    "read_context_candidates_cross_day",
    "read_preserved_context_recall",
    "CrossDayContextRecallService",
    "CrossDayContextRecall",
    "CrossDayContextRecallFoundation",
    "ReadOnlyCrossDayContextRecall",
]
