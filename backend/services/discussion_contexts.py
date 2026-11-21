"""Exact named exchange contexts, without matching or execution authority.

Writes participate in the caller's transaction and never commit or roll back.
No model is called. A new exchange receives its own named context unless an
existing same-session context is supplied by an exact validated binding.
"""

import uuid
from dataclasses import dataclass
from datetime import datetime

from sqlalchemy import and_, func

from backend.models.database import (
    ConversationExchange, ConversationTaskAssociation, ConversationTurn,
    DiscussionContext, DiscussionContextMembership, Task,
)

# Read-limit bounds for discussion contexts. The 1..50 inclusive range, the
# 96-character display-name excerpt, and the 400-row prune batch are the
# historical values; changing them changes observable behavior.
MIN_CONTEXT_LIMIT = 1
MAX_CONTEXT_LIMIT = 50
CONTEXT_NAME_MAX_LENGTH = 96
PRUNE_BATCH_SIZE = 400


@dataclass(frozen=True)
class ExchangeContextIdentity:
    context_id: str
    session_id: str
    name: str
    name_source: str
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int


def context_name(user_text):
    """A bounded human-text display label, never a unique key or topic claim."""
    return " ".join(user_text.split())[:CONTEXT_NAME_MAX_LENGTH] or "Conversation"


def read_exchange_context(db, session_id, exchange_id):
    """Read exact membership/name without autoflush, task lookup or fallback."""
    with db.no_autoflush:
        row = (db.query(
            DiscussionContext.id.label("context_id"), DiscussionContext.session_id,
            DiscussionContext.name, DiscussionContext.name_source,
            DiscussionContextMembership.exchange_id, DiscussionContextMembership.user_turn_id,
            DiscussionContextMembership.assistant_turn_id,
        ).select_from(DiscussionContextMembership)
            .join(DiscussionContext, DiscussionContext.id == DiscussionContextMembership.context_id)
            .join(ConversationExchange, ConversationExchange.id == DiscussionContextMembership.exchange_id)
            .filter(DiscussionContext.session_id == session_id,
                    ConversationExchange.session_id == session_id,
                    DiscussionContextMembership.exchange_id == exchange_id).first())
        return ExchangeContextIdentity(**row._mapping) if row is not None else None


def ensure_exchange_context(db, session_id, exchange_id, user_turn_id, assistant_turn_id, *, context_id=None):
    """Attach only exact same-session exchange/turn identities; never reassign.

    Caller commits with its exchange or revision. Existing legacy exchanges
    may be attached only with exact known turn IDs, never by text/timestamps.
    """
    with db.no_autoflush:
        exchange = (db.query(ConversationExchange.user_text, ConversationExchange.created_at)
                    .filter_by(id=exchange_id, session_id=session_id).first())
        if exchange is None:
            raise ValueError("Exact exchange is unavailable in this session")
        for turn_id, role in ((user_turn_id, "user"), (assistant_turn_id, "assistant")):
            if isinstance(turn_id, bool) or not isinstance(turn_id, int):
                raise ValueError("Exact turn identity is required")
            found = (db.query(ConversationTurn.id)
                     .filter_by(id=turn_id, session_id=session_id, role=role).first())
            if found is None:
                raise ValueError("Exact turn identity is unavailable in this session")
        association = (db.query(ConversationTaskAssociation.user_turn_id,
                                 ConversationTaskAssociation.assistant_turn_id)
                       .filter_by(exchange_id=exchange_id).first())
        if association is not None and (association.user_turn_id, association.assistant_turn_id) != (
                user_turn_id, assistant_turn_id):
            raise ValueError("Turn identities differ from the exact task association")
        existing = (db.query(DiscussionContextMembership.context_id,
                             DiscussionContextMembership.user_turn_id,
                             DiscussionContextMembership.assistant_turn_id)
                    .filter_by(exchange_id=exchange_id).first())
        if existing is not None:
            bound = read_exchange_context(db, session_id, exchange_id)
            if (bound is None or existing.user_turn_id != user_turn_id
                    or existing.assistant_turn_id != assistant_turn_id
                    or (context_id is not None and context_id != existing.context_id)):
                raise ValueError("Existing context membership cannot be reassigned")
            return existing.context_id
        if context_id is None:
            context_id = "ctx-" + str(uuid.uuid4())
            context = DiscussionContext(
                id=context_id, session_id=session_id, name=context_name(exchange.user_text),
                name_source="initial_request_excerpt", created_at=exchange.created_at,
            )
            db.add(context)
            db.flush([context])
        elif (db.query(DiscussionContext.id)
              .filter_by(id=context_id, session_id=session_id).first()) is None:
            raise ValueError("Exact context is unavailable in this session")
        membership = DiscussionContextMembership(
            context_id=context_id, exchange_id=exchange_id,
            user_turn_id=user_turn_id, assistant_turn_id=assistant_turn_id,
        )
        db.add(membership)
        db.flush([membership])
        return context_id


def prune_empty_contexts(db, context_ids):
    """Delete only affected contexts that have lost their last retained member."""
    ids = tuple(set(context_ids))
    for start in range(0, len(ids), PRUNE_BATCH_SIZE):
        (db.query(DiscussionContext)
         .filter(DiscussionContext.id.in_(ids[start:start + PRUNE_BATCH_SIZE]),
                 ~db.query(DiscussionContextMembership.exchange_id).filter(
                     DiscussionContextMembership.context_id == DiscussionContext.id).exists())
         .delete(synchronize_session=False))


def unlink_exchange_context(db, session_id, exchange_id, turn_ids):
    """Exact cleanup in the caller's exchange rollback/compensation transaction."""
    with db.no_autoflush:
        existing = db.query(DiscussionContextMembership.context_id).filter_by(exchange_id=exchange_id).first()
        if existing is None:
            return
        bound = read_exchange_context(db, session_id, exchange_id)
        if bound is None or set(turn_ids) != {bound.user_turn_id, bound.assistant_turn_id}:
            raise ValueError("Context membership does not match exact turn identities")
        (db.query(DiscussionContextMembership).filter_by(exchange_id=exchange_id)
         .delete(synchronize_session=False))
        prune_empty_contexts(db, [bound.context_id])


@dataclass(frozen=True)
class NamedDiscussionContext:
    context_id: str
    session_id: str
    name: str
    name_source: str
    created_at: datetime
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int
    task_id: str | None = None
    task_state: str | None = None
    task_version: int | None = None
    original_request: str | None = None
    normalized_goal: str | None = None
    final_result: str | None = None
    user_text: str | None = None
    assistant_text: str | None = None


def _normalize_limit(limit: int) -> int:
    """Validate that a context-read limit is an int within 1..50 inclusive."""
    if isinstance(limit, bool) or not isinstance(limit, int):
        raise ValueError("Context limit must be an integer")
    if not MIN_CONTEXT_LIMIT <= limit <= MAX_CONTEXT_LIMIT:
        raise ValueError(
            f"Context limit must be between {MIN_CONTEXT_LIMIT} and {MAX_CONTEXT_LIMIT}"
        )
    return limit


def _newest_memberships(db, session_id: str):
    """One representative membership per context: its newest exchange.

    A named context can accrue many memberships (follow-ups and task
    instructions join the source exchange's context). Exchange ids are random
    UUIDs, so the old reader's ``exchange_id.asc()`` ordering was arbitrary and
    its membership-level LIMIT silently dropped the freshest corrections. Pick
    the newest membership by exchange ``created_at`` (with an id tie-breaker)
    so each context contributes exactly one row — the latest state of the
    thread — and ``limit`` bounds contexts, not memberships.
    """
    return (
        db.query(
            DiscussionContextMembership.context_id.label("context_id"),
            DiscussionContextMembership.exchange_id.label("exchange_id"),
            DiscussionContextMembership.user_turn_id.label("user_turn_id"),
            DiscussionContextMembership.assistant_turn_id.label("assistant_turn_id"),
            func.row_number().over(
                partition_by=DiscussionContextMembership.context_id,
                order_by=(
                    ConversationExchange.created_at.desc(),
                    ConversationExchange.id.desc(),
                ),
            ).label("rn"),
        )
        .join(
            ConversationExchange,
            ConversationExchange.id == DiscussionContextMembership.exchange_id,
        )
        .filter(ConversationExchange.session_id == session_id)
        .subquery()
    )


def read_session_discussion_contexts(
    db, session_id: str, *, limit: int = 20
) -> tuple[NamedDiscussionContext, ...]:
    """Read the newest same-session named contexts, bounded by ``limit`` (1..50)."""
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("An exact session identity is required")
    session_id = session_id.strip()
    limit = _normalize_limit(limit)
    with db.no_autoflush:
        newest = _newest_memberships(db, session_id)
        rows = (
            db.query(
                DiscussionContext.id.label("context_id"),
                DiscussionContext.session_id.label("stored_session_id"),
                DiscussionContext.name,
                DiscussionContext.name_source,
                DiscussionContext.created_at,
                newest.c.exchange_id.label("exchange_id"),
                newest.c.user_turn_id.label("user_turn_id"),
                newest.c.assistant_turn_id.label("assistant_turn_id"),
                ConversationExchange.user_text.label("exchange_user_text"),
                ConversationExchange.assistant_text.label("exchange_assistant_text"),
                ConversationExchange.session_id.label("exchange_session_id"),
                ConversationTaskAssociation.task_id,
                Task.state.label("task_state"),
                Task.version.label("task_version"),
                Task.original_request,
                Task.normalized_goal,
                Task.final_result,
            )
            .select_from(DiscussionContext)
            .join(newest, and_(newest.c.context_id == DiscussionContext.id, newest.c.rn == 1))
            .join(ConversationExchange, ConversationExchange.id == newest.c.exchange_id)
            .outerjoin(
                ConversationTaskAssociation,
                (ConversationTaskAssociation.exchange_id == newest.c.exchange_id)
                & (ConversationTaskAssociation.user_turn_id == newest.c.user_turn_id)
                & (ConversationTaskAssociation.assistant_turn_id == newest.c.assistant_turn_id),
            )
            .outerjoin(Task, Task.id == ConversationTaskAssociation.task_id)
            .filter(
                DiscussionContext.session_id == session_id,
                ConversationExchange.session_id == session_id,
            )
            .order_by(DiscussionContext.created_at.desc(), DiscussionContext.id.desc())
            .limit(limit)
            .all()
        )
        result = []
        for row in rows:
            result.append(NamedDiscussionContext(
                context_id=row.context_id,
                session_id=session_id,
                name=row.name,
                name_source=row.name_source,
                created_at=row.created_at,
                exchange_id=row.exchange_id,
                user_turn_id=row.user_turn_id,
                assistant_turn_id=row.assistant_turn_id,
                task_id=row.task_id,
                task_state=row.task_state,
                task_version=row.task_version,
                original_request=row.original_request,
                normalized_goal=row.normalized_goal,
                final_result=row.final_result,
                user_text=row.exchange_user_text,
                assistant_text=row.exchange_assistant_text,
            ))
        return tuple(result)


read_named_discussion_contexts = read_session_discussion_contexts
read_session_named_contexts = read_session_discussion_contexts
list_session_discussion_contexts = read_session_discussion_contexts
list_named_discussion_contexts = read_session_discussion_contexts
read_named_contexts = read_session_discussion_contexts


@dataclass(frozen=True)
class DiscussionContextCoverage:
    """Exact scope metadata for a canonical discussion-context read.

    available_count is the number of eligible DISTINCT same-session contexts
    counted in the same SQL statement before LIMIT (a multi-membership thread
    counts once). It is a scope/freshness signal, not a target identity or an
    available older correction.
    """
    available_count: int
    returned_count: int
    omitted_count: int


def _row_value(row, key: str):
    """Read one column from a SQLAlchemy row via its mapping or attributes."""
    mapping = getattr(row, "_mapping", None)
    if mapping is not None:
        return mapping[key]
    return getattr(row, key)


def read_session_discussion_contexts_with_coverage(db, session_id, *, limit=20):
    """Read canonical contexts and exact total eligible count in one snapshot.

    The query keeps the canonical 20-row bound. A SQL COUNT(*) OVER () in the
    same statement gives the eligible total before LIMIT, so there is no second
    query and no separate count/row race. Foreign, orphan, and mismatched
    memberships are excluded exactly as in read_session_discussion_contexts.
    """
    if not isinstance(session_id, str) or not session_id.strip():
        raise ValueError("An exact session identity is required")
    session_id = session_id.strip()
    limit = _normalize_limit(limit)

    with db.no_autoflush:
        newest = _newest_memberships(db, session_id)
        rows = (
            db.query(
                DiscussionContext.id.label("context_id"),
                DiscussionContext.session_id.label("stored_session_id"),
                DiscussionContext.name,
                DiscussionContext.name_source,
                DiscussionContext.created_at,
                newest.c.exchange_id.label("exchange_id"),
                newest.c.user_turn_id.label("user_turn_id"),
                newest.c.assistant_turn_id.label("assistant_turn_id"),
                ConversationExchange.user_text.label("exchange_user_text"),
                ConversationExchange.assistant_text.label("exchange_assistant_text"),
                ConversationExchange.session_id.label("exchange_session_id"),
                ConversationTaskAssociation.task_id,
                Task.state.label("task_state"),
                Task.version.label("task_version"),
                Task.original_request,
                Task.normalized_goal,
                Task.final_result,
                func.count().over().label("eligible_total"),
            )
            .select_from(DiscussionContext)
            .join(newest, and_(newest.c.context_id == DiscussionContext.id, newest.c.rn == 1))
            .join(ConversationExchange, ConversationExchange.id == newest.c.exchange_id)
            .outerjoin(
                ConversationTaskAssociation,
                (ConversationTaskAssociation.exchange_id == newest.c.exchange_id)
                & (ConversationTaskAssociation.user_turn_id == newest.c.user_turn_id)
                & (ConversationTaskAssociation.assistant_turn_id == newest.c.assistant_turn_id),
            )
            .outerjoin(Task, Task.id == ConversationTaskAssociation.task_id)
            .filter(
                DiscussionContext.session_id == session_id,
                ConversationExchange.session_id == session_id,
            )
            .order_by(DiscussionContext.created_at.desc(), DiscussionContext.id.desc())
            .limit(limit)
            .all()
        )

    contexts = []
    eligible_total = 0
    if rows:
        eligible_total = int(_row_value(rows[0], "eligible_total") or 0)
        for row in rows:
            contexts.append(NamedDiscussionContext(
                context_id=row.context_id,
                session_id=session_id,
                name=row.name,
                name_source=row.name_source,
                created_at=row.created_at,
                exchange_id=row.exchange_id,
                user_turn_id=row.user_turn_id,
                assistant_turn_id=row.assistant_turn_id,
                task_id=row.task_id,
                task_state=row.task_state,
                task_version=row.task_version,
                original_request=row.original_request,
                normalized_goal=row.normalized_goal,
                final_result=row.final_result,
                user_text=row.exchange_user_text,
                assistant_text=row.exchange_assistant_text,
            ))

    returned = len(contexts)
    omitted = max(eligible_total - returned, 0)
    coverage = DiscussionContextCoverage(
        available_count=eligible_total,
        returned_count=returned,
        omitted_count=omitted,
    )
    return tuple(contexts), coverage