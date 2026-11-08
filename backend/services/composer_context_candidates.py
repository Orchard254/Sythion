"""Read-only initiating-discussion candidates for a future context resolver.

This is NOT topic creation, semantic matching, routing, or authorization. In
particular, an empty/bounded result cannot establish that a request is new work.
Use a clean read session for committed-only data: this reader owns no transaction
and will neither flush, commit, nor roll back the caller's transaction.
"""

from dataclasses import dataclass
from datetime import datetime

from sqlalchemy.orm import Query, Session, aliased

from backend.models.database import (
    ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
)


@dataclass(frozen=True)
class InitiatingDiscussionCandidate:
    exchange_id: str
    session_id: str
    task_id: str
    user_turn_id: int
    assistant_turn_id: int
    created_at: datetime
    user_text: str
    assistant_text: str
    original_request: str | None
    normalized_goal: str | None
    task_state: str | None
    task_version: int | None
    final_result: str | None
    integrity_issues: tuple[str, ...]


@dataclass(frozen=True)
class ContextCandidateSnapshot:
    session_id: str
    observed_at: datetime
    candidates: tuple[InitiatingDiscussionCandidate, ...]
    has_more_associations: bool
    # This scope is deliberately narrower than all conversations/topics.
    coverage: str = "retained_initiating_task_associations_in_session"


def _query(db: Session, session_id: str) -> Query:
    """Build the read-only candidate query for one exact session.

    Callers execute the returned query inside ``db.no_autoflush``; building
    this query neither flushes, commits, nor rolls back the caller's
    transaction.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("An exact session identity is required")
    user_turn = aliased(ConversationTurn)
    assistant_turn = aliased(ConversationTurn)
    return (db.query(
        ConversationTaskAssociation.exchange_id,
        ConversationTaskAssociation.task_id,
        ConversationTaskAssociation.user_turn_id,
        ConversationTaskAssociation.assistant_turn_id,
        ConversationExchange.session_id,
        ConversationExchange.created_at,
        ConversationExchange.user_text,
        ConversationExchange.assistant_text,
        Task.original_request,
        Task.normalized_goal,
        Task.state.label("task_state"),
        Task.version.label("task_version"),
        Task.final_result,
        user_turn.id.label("observed_user_turn_id"),
        user_turn.session_id.label("user_turn_session"),
        user_turn.role.label("user_turn_role"),
        assistant_turn.id.label("observed_assistant_turn_id"),
        assistant_turn.session_id.label("assistant_turn_session"),
        assistant_turn.role.label("assistant_turn_role"),
    ).select_from(ConversationTaskAssociation)
        .join(ConversationExchange, ConversationExchange.id == ConversationTaskAssociation.exchange_id)
        .outerjoin(Task, Task.id == ConversationTaskAssociation.task_id)
        .outerjoin(user_turn, user_turn.id == ConversationTaskAssociation.user_turn_id)
        .outerjoin(assistant_turn, assistant_turn.id == ConversationTaskAssociation.assistant_turn_id)
        .filter(ConversationExchange.session_id == session_id))


def _candidate(row) -> InitiatingDiscussionCandidate:
    """Project one already-fetched row to a candidate (pure, no DB access).

    This helper performs no session I/O, so it cannot flush pending caller
    work; it must only be called on rows fetched within the caller's
    ``db.no_autoflush`` scope.
    """
    issues = []
    if row.task_version is None:
        issues.append("task_unavailable")
    if (row.observed_user_turn_id is None or row.user_turn_session != row.session_id
            or row.user_turn_role != "user"):
        issues.append("user_turn_identity_unavailable")
    if (row.observed_assistant_turn_id is None or row.assistant_turn_session != row.session_id
            or row.assistant_turn_role != "assistant"):
        issues.append("assistant_turn_identity_unavailable")
    return InitiatingDiscussionCandidate(
        exchange_id=row.exchange_id, session_id=row.session_id, task_id=row.task_id,
        user_turn_id=row.user_turn_id, assistant_turn_id=row.assistant_turn_id,
        created_at=row.created_at, user_text=row.user_text, assistant_text=row.assistant_text,
        original_request=row.original_request, normalized_goal=row.normalized_goal,
        task_state=row.task_state, task_version=row.task_version, final_result=row.final_result,
        integrity_issues=tuple(issues),
    )


def read_context_candidates(db: Session, session_id: str, *, limit: int = 20) -> ContextCandidateSnapshot:
    """Preserve exact candidates, terminal tasks, identical text and coverage.

    No textual deduplication or summary truncation is performed. Text is
    untrusted reference data; callers must bound any later model projection
    explicitly. Missing retained references are reported, not silently dropped.
    Read errors propagate rather than masquerading as 'no matching context'.
    """
    if isinstance(limit, bool) or not isinstance(limit, int) or not 1 <= limit <= 50:
        raise ValueError("Candidate limit must be an integer from 1 to 50")
    with db.no_autoflush:
        rows = (_query(db, session_id)
                .order_by(ConversationExchange.created_at.desc(), ConversationExchange.id.desc())
                .limit(limit + 1).all())
        return ContextCandidateSnapshot(
            session_id=session_id, observed_at=datetime.utcnow(),
            candidates=tuple(_candidate(row) for row in rows[:limit]),
            has_more_associations=len(rows) > limit,
        )


def read_context_candidate(db: Session, session_id: str, exchange_id: str) -> InitiatingDiscussionCandidate | None:
    """Exact retained initiating exchange lookup; never fall back to a newer task."""
    if not isinstance(exchange_id, str) or not exchange_id.strip():
        raise ValueError("An exact initiating exchange identity is required")
    with db.no_autoflush:
        row = (_query(db, session_id)
               .filter(ConversationTaskAssociation.exchange_id == exchange_id).first())
        return _candidate(row) if row is not None else None
