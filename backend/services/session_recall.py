"""Explicit-scoped read-only multi-session recall candidates.

Foundation only.  This reader never creates, selects, routes, authorizes, or
mutates work, and never touches session state.  It returns committed data for an
explicitly provided session scope.

A clean read session is required (``autoflush=False`` sessions are assumed):
this reader uses ``db.no_autoflush`` and performs no flush, commit, or rollback.
"""

from __future__ import annotations

from dataclasses import dataclass, FrozenInstanceError
from datetime import datetime
from typing import Iterable, List, Optional, Tuple

try:
    from backend.models.database import ConversationExchange
except Exception:  # pragma: no cover - alternate invocation layout
    from models.database import ConversationExchange  # type: ignore


@dataclass(frozen=True)
class SessionRecallCandidate:
    """One retained exchange candidate for explicit session recall."""

    exchange_id: str
    session_id: str
    created_at: datetime
    user_text: str
    assistant_text: str


@dataclass(frozen=True)
class SessionRecallSnapshot:
    """Bounded explicit-scope session recall projection."""

    session_ids: Tuple[str, ...]
    observed_at: datetime
    candidates: Tuple[SessionRecallCandidate, ...]
    has_more: bool
    coverage: str = "retained_exchanges_in_explicit_sessions"


def _normalize_session_ids(session_ids) -> List[str]:
    if isinstance(session_ids, str):
        session_ids = [session_ids]
    if not isinstance(session_ids, Iterable):
        raise ValueError("An explicit session scope is required")
    normalized: List[str] = []
    seen = set()
    for item in session_ids:
        if not isinstance(item, str) or not item:
            raise ValueError("Every session id must be a non-empty string")
        if item in seen:
            continue
        seen.add(item)
        normalized.append(item)
    if not normalized:
        raise ValueError("An explicit non-empty session scope is required")
    return normalized


def _validate_limit(limit) -> int:
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Candidate limit must be an integer from 1 to 50")
    if not 1 <= limit <= 50:
        raise ValueError("Candidate limit must be an integer from 1 to 50")
    return limit


def read_session_recall_candidates(
    db,
    session_ids,
    *,
    limit: int = 20,
) -> SessionRecallSnapshot:
    """Read retained exchanges for an explicitly provided session scope."""
    scope = _normalize_session_ids(session_ids)
    bounded = _validate_limit(limit)
    with db.no_autoflush:
        rows = (
            db.query(ConversationExchange)
            .filter(ConversationExchange.session_id.in_(scope))
            .order_by(ConversationExchange.created_at.desc(), ConversationExchange.id.desc())
            .limit(bounded + 1)
            .all()
        )
        return SessionRecallSnapshot(
            session_ids=tuple(scope),
            observed_at=datetime.now(),
            candidates=tuple(
                SessionRecallCandidate(
                    exchange_id=row.id,
                    session_id=row.session_id,
                    created_at=row.created_at,
                    user_text=row.user_text,
                    assistant_text=row.assistant_text,
                )
                for row in rows[:bounded]
            ),
            has_more=len(rows) > bounded,
        )


def read_session_recall_candidate(db, session_id: str, exchange_id: str) -> Optional[SessionRecallCandidate]:
    """Exact retained exchange lookup within one explicit session."""
    if not isinstance(session_id, str) or not session_id:
        raise ValueError("An exact session identity is required")
    if not isinstance(exchange_id, str) or not exchange_id.strip():
        raise ValueError("An exact exchange identity is required")
    with db.no_autoflush:
        row = (
            db.query(ConversationExchange)
            .filter(ConversationExchange.session_id == session_id)
            .filter(ConversationExchange.id == exchange_id)
            .first()
        )
        if row is None:
            return None
        return SessionRecallCandidate(
            exchange_id=row.id,
            session_id=row.session_id,
            created_at=row.created_at,
            user_text=row.user_text,
            assistant_text=row.assistant_text,
        )


# Compatibility aliases for likely public names.
SessionCandidate = SessionRecallCandidate
CrossDaySessionCandidate = SessionRecallCandidate
MultiSessionCandidate = SessionRecallCandidate
SessionCandidateSnapshot = SessionRecallSnapshot
SessionRecallSnapshot = SessionRecallSnapshot
CrossDaySessionCandidateSnapshot = SessionRecallSnapshot
CrossDaySessionRecallSnapshot = SessionRecallSnapshot
MultiSessionCandidateSnapshot = SessionRecallSnapshot

read_session_candidates = read_session_recall_candidates
read_session_candidate = read_session_recall_candidate
read_cross_day_session_candidates = read_session_recall_candidates
read_cross_day_session_recall = read_session_recall_candidates
read_multi_session_candidates = read_session_recall_candidates
read_multi_session_candidate = read_session_recall_candidate