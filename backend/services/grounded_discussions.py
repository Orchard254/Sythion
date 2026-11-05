"""Standalone additive persistence for grounded labelled discussions.

This module is deliberately isolated: it persists stable discussion IDs, labels
with explicit provenance, many-to-many exact exchange links, and append-only
storage-level correction/conflict/link-repair events.  It never commits or
rolls back the caller's transaction, never calls a model or embedding service,
and does not register itself at runtime import time in ``models.database``.
Table creation is the caller's responsibility (``Base.metadata.create_all`` in
tests).

Event rows are durable append-only history.  They are *not* by themselves an
effective projection: a successful repair must be interpreted by a later
projection/import layer instead of replacing storage history.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, Callable, Iterable, Mapping, Optional, Sequence, Tuple

from sqlalchemy import (
    Boolean, CheckConstraint, Column, DateTime, ForeignKey, Index, Integer,
    String, Text, UniqueConstraint, func, text,
)
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.models.database import (
        Base, ConversationExchange, ConversationTurn, Task,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import (
            Base, ConversationExchange, ConversationTurn, Task,
        )
    except Exception:  # pragma: no cover
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()
        ConversationExchange = None  # type: ignore
        ConversationTurn = None  # type: ignore
        Task = None  # type: ignore

try:  # optional exact task join
    from backend.models.database import ConversationTaskAssociation as _ConversationTaskAssociation
except Exception:  # pragma: no cover
    try:
        from models.database import ConversationTaskAssociation as _ConversationTaskAssociation
    except Exception:
        _ConversationTaskAssociation = None


# ---------------------------------------------------------------------------
# Provenance vocabulary.  Model-inferred facts are explicitly unverified.
# ---------------------------------------------------------------------------
PROVENANCE_MODEL_INFERRED_UNVERIFIED = "model_inferred_unverified"
PROVENANCE_EXPLICIT_VALIDATED_PROPOSAL = "explicit_validated_proposal"
PROVENANCE_HUMAN_STATED = "human_stated"
PROVENANCE_LEGACY_UNVERIFIED = "legacy_unverified"

_PROVENANCE_VALUES = frozenset({
    PROVENANCE_MODEL_INFERRED_UNVERIFIED,
    PROVENANCE_EXPLICIT_VALIDATED_PROPOSAL,
    PROVENANCE_HUMAN_STATED,
    PROVENANCE_LEGACY_UNVERIFIED,
})

EVENT_TYPE_CORRECTION = "correction"
EVENT_TYPE_CONFLICT = "conflict"
EVENT_TYPE_LINK_REPAIR = "link_repair"
EVENT_TYPE_MEMBER_LINKED = "member_linked"

_EVENT_TYPE_VALUES = frozenset({
    EVENT_TYPE_CORRECTION,
    EVENT_TYPE_CONFLICT,
    EVENT_TYPE_LINK_REPAIR,
    EVENT_TYPE_MEMBER_LINKED,
})


def _now(clock: Callable[[], datetime]) -> datetime:
    return clock()


def _clean_exact_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"Exact {field_name} is required")
    candidate = value
    if not candidate or candidate != candidate.strip():
        raise ValueError(f"Exact {field_name} is required")
    if len(candidate) > 256:
        raise ValueError(f"{field_name} exceeds maximum length")
    return candidate


def _as_int_turn(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise ValueError(f"Exact {field_name} turn identity is required")
    if value < 1:
        raise ValueError(f"Exact {field_name} turn identity is required")
    return int(value)


def _clean_label(value: Any) -> str:
    if not isinstance(value, str) or not value.strip():
        raise ValueError("A non-empty discussion label is required")
    return value


def _clean_provenance(value: Any, default: str = PROVENANCE_MODEL_INFERRED_UNVERIFIED) -> str:
    if value is None:
        return default
    if not isinstance(value, str):
        raise ValueError("Discussion provenance must be a string")
    candidate = value.strip()
    if candidate not in _PROVENANCE_VALUES:
        raise ValueError("Unsupported discussion provenance")
    return candidate


def _validate_proposal(proposal: Any, *, expected: Mapping[str, Any]) -> None:
    """Validate an explicit caller-supplied provenance proposal fail-closed.

    The proposal is evidence *input* only; mismatches are rejected rather than
    guessed.  There is no semantic inference here.
    """
    if proposal is None:
        return
    if not isinstance(proposal, Mapping):
        raise ValueError("Validated discussion proposal must be a mapping")
    allowed = {
        "discussion_id", "session_id", "target_exchange_id", "target_user_turn_id",
        "target_assistant_turn_id", "correction_text", "conflict_text", "repair_text",
        "label", "provenance", "session",
    }
    unknown = set(proposal.keys()) - allowed
    if unknown:
        raise ValueError("Unknown field in validated discussion proposal")
    # Required identity fields where the caller supplied them.
    for key, expected_value in expected.items():
        if key in proposal and proposal.get(key) != expected_value:
            raise ValueError("Validated discussion proposal does not match the request")
    # When extra identity fields are present, they must be internally consistent.
    if "session" in proposal and "session_id" in proposal:
        if proposal.get("session") != proposal.get("session_id"):
            raise ValueError("Validated discussion proposal is internally inconsistent")
    # Text must match if both are supplied.
    for text_key in ("correction_text", "conflict_text", "repair_text"):
        if text_key in proposal and "correction_text" in expected and text_key != "correction_text":
            # Cross-text aliases are not allowed to muddy which text applies.
            if proposal.get(text_key) != expected.get("correction_text"):
                raise ValueError("Validated discussion proposal does not match the request")
    if "label" in proposal and "label" in expected:
        if proposal.get("label") != expected.get("label"):
            raise ValueError("Validated discussion proposal does not match the request")


def _session_of(db: Any) -> Session:
    if db is None:
        raise ValueError("A database session is required")
    if isinstance(db, Session):
        return db
    session = getattr(db, "db", None)
    if isinstance(session, Session):
        return session
    session = getattr(db, "session", None)
    if isinstance(session, Session):
        return session
    session = getattr(db, "runner", None)
    if session is not None:
        inner = getattr(session, "db", None)
        if isinstance(inner, Session):
            return session  # pragma: no cover
    if hasattr(db, "execute") and hasattr(db, "flush"):
        return db  # duck-typed session-like
    raise ValueError("A database session is required")


# ---------------------------------------------------------------------------
# Row models (additive only).
# ---------------------------------------------------------------------------
class DiscussionRow(Base):
    __tablename__ = "grounded_discussions"

    discussion_id = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    label = Column(Text, nullable=False)
    label_provenance = Column(String, nullable=False, default=PROVENANCE_MODEL_INFERRED_UNVERIFIED)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True, index=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow, onupdate=datetime.utcnow)

    __table_args__ = (
        CheckConstraint(
            "label_provenance IN ('model_inferred_unverified', 'explicit_validated_proposal', "
            "'human_stated', 'legacy_unverified')",
            name="check_grounded_discussion_provenance",
        ),
    )


class DiscussionExchangeLinkRow(Base):
    __tablename__ = "discussion_exchange_links"

    discussion_id = Column(
        String, ForeignKey("grounded_discussions.discussion_id", ondelete="CASCADE"),
        primary_key=True,
    )
    exchange_id = Column(
        String, ForeignKey("conversation_exchanges.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id = Column(String, nullable=False, index=True)
    user_turn_id = Column(Integer, ForeignKey("conversation_turns.id"), nullable=False)
    assistant_turn_id = Column(Integer, ForeignKey("conversation_turns.id"), nullable=False)
    link_provenance = Column(String, nullable=False, default=PROVENANCE_MODEL_INFERRED_UNVERIFIED)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        UniqueConstraint(
            "discussion_id", "exchange_id",
            name="uq_discussion_exchange_link",
        ),
        CheckConstraint(
            "link_provenance IN ('model_inferred_unverified', 'explicit_validated_proposal', "
            "'human_stated', 'legacy_unverified')",
            name="check_discussion_link_provenance",
        ),
    )


class DiscussionEventRow(Base):
    __tablename__ = "discussion_events"

    id = Column(Integer, primary_key=True, autoincrement=True)
    discussion_id = Column(
        String, ForeignKey("grounded_discussions.discussion_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    session_id = Column(String, nullable=False, index=True)
    event_type = Column(String, nullable=False, index=True)
    target_exchange_id = Column(String, ForeignKey("conversation_exchanges.id"), nullable=True, index=True)
    target_user_turn_id = Column(Integer, ForeignKey("conversation_turns.id"), nullable=True)
    target_assistant_turn_id = Column(Integer, ForeignKey("conversation_turns.id"), nullable=True)
    prior_event_id = Column(Integer, ForeignKey("discussion_events.id"), nullable=True, index=True)
    payload_text = Column(Text, nullable=True)
    provenance = Column(String, nullable=False, default=PROVENANCE_MODEL_INFERRED_UNVERIFIED)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        CheckConstraint(
            "event_type IN ('correction', 'conflict', 'link_repair', 'member_linked')",
            name="check_discussion_event_type",
        ),
        CheckConstraint(
            "provenance IN ('model_inferred_unverified', 'explicit_validated_proposal', "
            "'human_stated', 'legacy_unverified')",
            name="check_discussion_event_provenance",
        ),
    )


# ---------------------------------------------------------------------------
# Data transfer objects.
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class DiscussionMember:
    discussion_id: str
    session_id: str
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int
    link_provenance: str
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class DiscussionEvent:
    id: int
    discussion_id: str
    session_id: str
    event_type: str
    target_exchange_id: Optional[str] = None
    target_user_turn_id: Optional[int] = None
    target_assistant_turn_id: Optional[int] = None
    prior_event_id: Optional[int] = None
    payload_text: Optional[str] = None
    provenance: str = PROVENANCE_MODEL_INFERRED_UNVERIFIED
    created_at: Optional[datetime] = None


@dataclass(frozen=True)
class Discussion:
    discussion_id: str
    session_id: str
    label: str
    label_provenance: str
    task_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class DiscussionView:
    discussion_id: str
    session_id: str
    label: str
    label_provenance: str
    task_id: Optional[str]
    created_at: Optional[datetime]
    updated_at: Optional[datetime]
    members: Tuple[DiscussionMember, ...] = field(default_factory=tuple)
    events: Tuple[DiscussionEvent, ...] = field(default_factory=tuple)
    coverage: "DiscussionCoverage" = None  # type: ignore


@dataclass(frozen=True)
class DiscussionCoverage:
    member_available_count: int
    member_returned_count: int
    member_omitted_count: int
    event_available_count: int
    event_returned_count: int
    event_omitted_count: int
    link_count: int = 0
    unresolved_member_count: int = 0
    member_source_complete: bool = True
    event_unresolved_subject_count: int = 0
    event_source_complete: bool = True


@dataclass(frozen=True)
class DiscussionSummary:
    discussion_id: str
    session_id: str
    label: str
    label_provenance: str
    task_id: Optional[str] = None
    created_at: Optional[datetime] = None
    updated_at: Optional[datetime] = None


@dataclass(frozen=True)
class DiscussionListCoverage:
    available_count: int
    returned_count: int
    omitted_count: int


def _row_to_discussion(row: DiscussionRow) -> Discussion:
    return Discussion(
        discussion_id=row.discussion_id,
        session_id=row.session_id,
        label=row.label,
        label_provenance=row.label_provenance,
        task_id=row.task_id,
        created_at=row.created_at,
        updated_at=row.updated_at,
    )


def _row_to_member(row: DiscussionExchangeLinkRow) -> DiscussionMember:
    return DiscussionMember(
        discussion_id=row.discussion_id,
        session_id=row.session_id,
        exchange_id=row.exchange_id,
        user_turn_id=int(row.user_turn_id),
        assistant_turn_id=int(row.assistant_turn_id),
        link_provenance=row.link_provenance,
        created_at=row.created_at,
    )


def _row_to_event(row: DiscussionEventRow) -> DiscussionEvent:
    return DiscussionEvent(
        id=int(row.id),
        discussion_id=row.discussion_id,
        session_id=row.session_id,
        event_type=row.event_type,
        target_exchange_id=row.target_exchange_id,
        target_user_turn_id=row.target_user_turn_id,
        target_assistant_turn_id=row.target_assistant_turn_id,
        prior_event_id=row.prior_event_id,
        payload_text=row.payload_text,
        provenance=row.provenance,
        created_at=row.created_at,
    )


# ---------------------------------------------------------------------------
# Manager.
# ---------------------------------------------------------------------------
class GroundedDiscussionManager:
    """Caller-transactional persistence manager. Never commits or rolls back."""

    def __init__(self, session: Any = None, *, db: Any = None, clock: Any = None, **_kwargs: Any) -> None:
        if db is not None:
            session = db
        if session is None:
            raise ValueError("A database session is required")
        if isinstance(session, str):
            raise ValueError("A database session is required")
        if not (hasattr(session, "execute") and hasattr(session, "flush")) and hasattr(session, "db"):
            session = session.db
        self._session = _session_of(session)
        self._clock = clock or (lambda: datetime.utcnow())

    @property
    def db(self) -> Session:
        return self._session

    @property
    def session(self) -> Session:
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> None:
        dbapi_connection = None
        try:
            dbapi_connection = self._session.connection().connection.dbapi_connection
        except Exception:
            dbapi_connection = None
        try:
            cursor = dbapi_connection.cursor() if dbapi_connection is not None else None
            if cursor is not None:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
        except Exception:
            pass
        if dbapi_connection is not None and getattr(dbapi_connection, "in_transaction", False):
            return
        # SQLite does not expose an outer in-transaction marker reliably across
        # savepoint nesting; begin explicitly only when no DBAPI transaction is
        # active.  This avoids RELEASE-of-savepoint accidentally committing
        # unrelated caller data.
        self._session.execute(text("BEGIN IMMEDIATE"))

    def _query_task(self, task_id: str):
        if Task is None:
            return None
        try:
            return self._session.query(Task).filter_by(id=task_id).populate_existing().first()
        except Exception:
            try:
                return self._session.query(Task).filter_by(id=task_id).first()
            except Exception:
                return None

    def _validate_exact_member(self, session_id: str, exchange_id: str,
                               user_turn_id: int, assistant_turn_id: int,
                               *, task_id: Optional[str] = None) -> None:
        if ConversationExchange is None or ConversationTurn is None:
            raise ValueError("Exact exchange identities are unavailable")
        exchange = (
            self._session.query(ConversationExchange)
            .filter_by(id=exchange_id, session_id=session_id)
            .populate_existing().first()
        )
        if exchange is None:
            raise ValueError("Exact exchange is unavailable in this session")
        user_turn = (
            self._session.query(ConversationTurn)
            .filter_by(id=user_turn_id, session_id=session_id, role="user")
            .populate_existing().first()
        )
        if user_turn is None:
            raise ValueError("Exact user turn identity is unavailable in this session")
        assistant_turn = (
            self._session.query(ConversationTurn)
            .filter_by(id=assistant_turn_id, session_id=session_id, role="assistant")
            .populate_existing().first()
        )
        if assistant_turn is None:
            raise ValueError("Exact assistant turn identity is unavailable in this session")
        if task_id is not None:
            if _ConversationTaskAssociation is None:
                raise ValueError("Exact task association is required")
            association = (
                self._session.query(_ConversationTaskAssociation)
                .filter_by(exchange_id=exchange_id)
                .populate_existing().first()
            )
            if association is None:
                raise ValueError("Exact task association is required")
            if (int(association.user_turn_id), int(association.assistant_turn_id)) != (user_turn_id, assistant_turn_id):
                raise ValueError("Exact turn identity differs from the task association")
            if str(association.task_id) != task_id:
                raise ValueError("Exact task identity differs from the exchange association")
        elif _ConversationTaskAssociation is not None:
            association = (
                self._session.query(_ConversationTaskAssociation)
                .filter_by(exchange_id=exchange_id)
                .populate_existing().first()
            )
            if association is not None:
                if (int(association.user_turn_id), int(association.assistant_turn_id)) != (user_turn_id, assistant_turn_id):
                    raise ValueError("Exact turn identity differs from the task association")
        # Optional discussion context membership anchoring.
        try:
            from backend.services.discussion_contexts import read_exchange_context
        except Exception:
            try:
                from services.discussion_contexts import read_exchange_context
            except Exception:
                read_exchange_context = None
        if read_exchange_context is not None:
            bound = read_exchange_context(self._session, session_id, exchange_id)
            if bound is not None and (int(bound.user_turn_id), int(bound.assistant_turn_id)) != (user_turn_id, assistant_turn_id):
                raise ValueError("Exact turn identity differs from the context membership")

    # ---------------------------------------------------------------- create
    def create_discussion(self, session_id: Any, label: Any, *,
                          label_provenance: Any = None,
                          provenance: Any = None,
                          task_id: Any = None,
                          member_exchanges: Any = None,
                          members: Any = None,
                          link_provenance: Any = None,
                          validated_proposal: Any = None,
                          **kwargs: Any) -> Discussion:
        self._ensure_transaction()
        clean_session = _clean_exact_id(session_id, "session identity")
        clean_label = _clean_label(label)
        lp = _clean_provenance(label_provenance if label_provenance is not None else provenance)
        tid = None
        if task_id is not None:
            tid = _clean_exact_id(task_id, "task identity")
            if self._query_task(tid) is None:
                raise ValueError("Exact task identity is required")
        lp_link = _clean_provenance(link_provenance if link_provenance is not None else provenance, default=PROVENANCE_MODEL_INFERRED_UNVERIFIED)

        raw_members = member_exchanges if member_exchanges is not None else members
        if raw_members is None:
            raw_members = []
        if not isinstance(raw_members, (list, tuple, set, frozenset)):
            raise ValueError("Member exchanges must be a list")
        normalized_members = []
        seen_exchanges = set()
        for spec in raw_members:
            if not isinstance(spec, Mapping):
                raise ValueError("Each member exchange must be a mapping")
            exchange_id = _clean_exact_id(spec.get("exchange_id"), "exchange identity")
            user_turn_id = _as_int_turn(spec.get("user_turn_id"), "exchange")
            assistant_turn_id = _as_int_turn(spec.get("assistant_turn_id"), "exchange")
            if exchange_id in seen_exchanges:
                raise ValueError("Duplicate member exchange in one discussion request")
            seen_exchanges.add(exchange_id)
            normalized_members.append((exchange_id, user_turn_id, assistant_turn_id))
            self._validate_exact_member(clean_session, exchange_id, user_turn_id, assistant_turn_id, task_id=tid)

        if validated_proposal is not None:
            _validate_proposal(validated_proposal, expected={
                "session_id": clean_session,
                "label": clean_label,
            })

        discussion_id = "disc-" + str(uuid.uuid4())
        # One operation-scoped savepoint covers the discussion row and all
        # owned link/event rows.  Validation has already happened; any later
        # failure reverts only this operation's writes within the caller's
        # transaction (no caller commit/rollback and no unrelated pending
        # data is discarded).
        operation_savepoint = self._session.begin_nested()
        try:
            row = DiscussionRow(
                discussion_id=discussion_id,
                session_id=clean_session,
                label=clean_label,
                label_provenance=lp,
                task_id=tid,
                created_at=self._now(),
                updated_at=self._now(),
            )
            self._session.add(row)
            self._session.flush()

            for exchange_id, user_turn_id, assistant_turn_id in normalized_members:
                link = DiscussionExchangeLinkRow(
                    discussion_id=discussion_id,
                    exchange_id=exchange_id,
                    session_id=clean_session,
                    user_turn_id=user_turn_id,
                    assistant_turn_id=assistant_turn_id,
                    link_provenance=lp_link,
                    created_at=self._now(),
                )
                try:
                    self._session.add(link)
                    self._session.flush()
                except IntegrityError as exc:
                    raise ValueError("Discussion exchange link already exists or conflicts") from exc
                event = DiscussionEventRow(
                    discussion_id=discussion_id,
                    session_id=clean_session,
                    event_type=EVENT_TYPE_MEMBER_LINKED,
                    target_exchange_id=exchange_id,
                    target_user_turn_id=user_turn_id,
                    target_assistant_turn_id=assistant_turn_id,
                    payload_text=None,
                    provenance=lp_link,
                    created_at=self._now(),
                )
                self._session.add(event)
                self._session.flush()
            operation_savepoint.commit()
        except Exception:
            try:
                operation_savepoint.rollback()
            except Exception:
                pass
            raise

        return _row_to_discussion(row)

    # Compatibility aliases used by callers/tests.
    def create(self, *args, **kwargs):
        # Accept create(session_id=..., label=...) or positional.
        session_id = kwargs.pop("session_id", kwargs.pop("session", None))
        if session_id is None and args:
            session_id = args[0]
            args = args[1:]
        label = kwargs.pop("label", kwargs.pop("label_text", None))
        if label is None and args:
            label = args[0]
            args = args[1:]
        return self.create_discussion(session_id, label, *args, **kwargs)

    def add_discussion(self, *args, **kwargs):
        return self.create(*args, **kwargs)

    # ---------------------------------------------------------------- links
    def link_exchange(self, discussion_id: Any, session_id: Any, exchange_id: Any,
                      user_turn_id: Any, assistant_turn_id: Any, *,
                      link_provenance: Any = None,
                      provenance: Any = None,
                      validated_proposal: Any = None,
                      **_kwargs: Any) -> DiscussionMember:
        self._ensure_transaction()
        clean_discussion = _clean_exact_id(discussion_id, "discussion identity")
        clean_session = _clean_exact_id(session_id, "session identity")
        clean_exchange = _clean_exact_id(exchange_id, "exchange identity")
        ut = _as_int_turn(user_turn_id, "exchange")
        at = _as_int_turn(assistant_turn_id, "exchange")
        lp = _clean_provenance(link_provenance if link_provenance is not None else provenance)

        disc = self._session.query(DiscussionRow).filter_by(
            discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
        if disc is None:
            raise ValueError("Exact discussion identity is required in this session")
        # A task-bound discussion later link must carry canonical proof for the
        # same task.  Taskless discussions intentionally remain valid.
        self._validate_exact_member(clean_session, clean_exchange, ut, at, task_id=disc.task_id)

        if validated_proposal is not None:
            _validate_proposal(validated_proposal, expected={
                "discussion_id": clean_discussion,
                "session_id": clean_session,
                "target_exchange_id": clean_exchange,
                "target_user_turn_id": ut,
                "target_assistant_turn_id": at,
            })

        operation_savepoint = self._session.begin_nested()
        try:
            link = DiscussionExchangeLinkRow(
                discussion_id=clean_discussion,
                exchange_id=clean_exchange,
                session_id=clean_session,
                user_turn_id=ut,
                assistant_turn_id=at,
                link_provenance=lp,
                created_at=self._now(),
            )
            try:
                self._session.add(link)
                self._session.flush()
            except IntegrityError as exc:
                raise ValueError("Discussion exchange link already exists or conflicts") from exc
            event = DiscussionEventRow(
                discussion_id=clean_discussion,
                session_id=clean_session,
                event_type=EVENT_TYPE_MEMBER_LINKED,
                target_exchange_id=clean_exchange,
                target_user_turn_id=ut,
                target_assistant_turn_id=at,
                payload_text=None,
                provenance=lp,
                created_at=self._now(),
            )
            self._session.add(event)
            self._session.flush()
            operation_savepoint.commit()
        except Exception:
            try:
                operation_savepoint.rollback()
            except Exception:
                pass
            raise

        return _row_to_member(link)

    def add_member(self, *args, **kwargs):
        return self.link_exchange(*args, **kwargs)

    def link(self, *args, **kwargs):
        return self.link_exchange(*args, **kwargs)

    # ---------------------------------------------------------------- events
    def _target_link_exists(self, discussion_id, session_id, exchange_id, user_turn_id, assistant_turn_id):
        link = (
            self._session.query(DiscussionExchangeLinkRow)
            .filter_by(
                discussion_id=discussion_id,
                exchange_id=exchange_id,
                session_id=session_id,
                user_turn_id=user_turn_id,
                assistant_turn_id=assistant_turn_id,
            ).populate_existing().first()
        )
        return link is not None

    def add_correction(self, discussion_id: Any, session_id: Any, *,
                       target_exchange_id: Any = None,
                       target_user_turn_id: Any = None,
                       target_assistant_turn_id: Any = None,
                       correction_text: Any = None,
                       correction: Any = None,
                       text: Any = None,
                       provenance: Any = None,
                       label_provenance: Any = None,
                       validated_proposal: Any = None,
                       **_kwargs: Any) -> DiscussionEvent:
        self._ensure_transaction()
        clean_discussion = _clean_exact_id(discussion_id, "discussion identity")
        clean_session = _clean_exact_id(session_id, "session identity")
        clean_exchange = _clean_exact_id(target_exchange_id, "exchange identity")
        ut = _as_int_turn(target_user_turn_id, "exchange")
        at = _as_int_turn(target_assistant_turn_id, "exchange")
        ct = correction_text if correction_text is not None else (correction if correction is not None else text)
        if not isinstance(ct, str) or not ct.strip():
            raise ValueError("Correction text is required")
        prov = _clean_provenance(provenance if provenance is not None else label_provenance)

        disc = self._session.query(DiscussionRow).filter_by(
            discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
        if disc is None:
            raise ValueError("Exact discussion identity is required in this session")
        if not self._target_link_exists(clean_discussion, clean_session, clean_exchange, ut, at):
            raise ValueError("Correction target is not an existing linked member")

        if validated_proposal is not None:
            _validate_proposal(validated_proposal, expected={
                "discussion_id": clean_discussion,
                "session_id": clean_session,
                "target_exchange_id": clean_exchange,
                "target_user_turn_id": ut,
                "target_assistant_turn_id": at,
                "correction_text": ct,
            })

        event = DiscussionEventRow(
            discussion_id=clean_discussion,
            session_id=clean_session,
            event_type=EVENT_TYPE_CORRECTION,
            target_exchange_id=clean_exchange,
            target_user_turn_id=ut,
            target_assistant_turn_id=at,
            payload_text=ct,
            provenance=prov,
            created_at=self._now(),
        )
        self._session.add(event)
        self._session.flush()
        return _row_to_event(event)

    def add_conflict(self, discussion_id: Any, session_id: Any, *,
                     target_exchange_id: Any = None,
                     target_user_turn_id: Any = None,
                     target_assistant_turn_id: Any = None,
                     conflict_text: Any = None,
                     conflict: Any = None,
                     text: Any = None,
                     provenance: Any = None,
                     validated_proposal: Any = None,
                     **_kwargs: Any) -> DiscussionEvent:
        self._ensure_transaction()
        clean_discussion = _clean_exact_id(discussion_id, "discussion identity")
        clean_session = _clean_exact_id(session_id, "session identity")
        clean_exchange = _clean_exact_id(target_exchange_id, "exchange identity")
        ut = _as_int_turn(target_user_turn_id, "exchange")
        at = _as_int_turn(target_assistant_turn_id, "exchange")
        ct = conflict_text if conflict_text is not None else (conflict if conflict is not None else text)
        if not isinstance(ct, str) or not ct.strip():
            raise ValueError("Conflict text is required")
        prov = _clean_provenance(provenance)

        disc = self._session.query(DiscussionRow).filter_by(
            discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
        if disc is None:
            raise ValueError("Exact discussion identity is required in this session")
        if not self._target_link_exists(clean_discussion, clean_session, clean_exchange, ut, at):
            raise ValueError("Conflict target is not an existing linked member")

        if validated_proposal is not None:
            _validate_proposal(validated_proposal, expected={
                "discussion_id": clean_discussion,
                "session_id": clean_session,
                "target_exchange_id": clean_exchange,
                "target_user_turn_id": ut,
                "target_assistant_turn_id": at,
                "conflict_text": ct,
            })

        event = DiscussionEventRow(
            discussion_id=clean_discussion,
            session_id=clean_session,
            event_type=EVENT_TYPE_CONFLICT,
            target_exchange_id=clean_exchange,
            target_user_turn_id=ut,
            target_assistant_turn_id=at,
            payload_text=ct,
            provenance=prov,
            created_at=self._now(),
        )
        self._session.add(event)
        self._session.flush()
        return _row_to_event(event)

    def repair_link(self, discussion_id: Any, session_id: Any, *,
                    prior_event_id: Any = None,
                    target_exchange_id: Any = None,
                    target_user_turn_id: Any = None,
                    target_assistant_turn_id: Any = None,
                    repair_text: Any = None,
                    repair: Any = None,
                    text: Any = None,
                    provenance: Any = None,
                    validated_proposal: Any = None,
                    **_kwargs: Any) -> DiscussionEvent:
        self._ensure_transaction()
        clean_discussion = _clean_exact_id(discussion_id, "discussion identity")
        clean_session = _clean_exact_id(session_id, "session identity")
        prior_id: int
        if isinstance(prior_event_id, bool) or not isinstance(prior_event_id, int):
            raise ValueError("Exact prior event identity is required")
        prior_id = int(prior_event_id)
        if prior_id < 1:
            raise ValueError("Exact prior event identity is required")
        clean_exchange = _clean_exact_id(target_exchange_id, "exchange identity")
        ut = _as_int_turn(target_user_turn_id, "exchange")
        at = _as_int_turn(target_assistant_turn_id, "exchange")
        rt = repair_text if repair_text is not None else (repair if repair is not None else text)
        if not isinstance(rt, str) or not rt.strip():
            raise ValueError("Repair text is required")
        prov = _clean_provenance(provenance)

        disc = self._session.query(DiscussionRow).filter_by(
            discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
        if disc is None:
            raise ValueError("Exact discussion identity is required in this session")
        prior = self._session.query(DiscussionEventRow).filter_by(
            id=prior_id, discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
        if prior is None:
            raise ValueError("Exact prior event identity is required in this session")
        if not self._target_link_exists(clean_discussion, clean_session, clean_exchange, ut, at):
            raise ValueError("Repair target is not an existing linked member")
        # A prior event without an exact member subject cannot be safely
        # repaired onto an arbitrary target. Storage history stays append-only.
        if (prior.target_exchange_id is None or prior.target_user_turn_id is None
                or prior.target_assistant_turn_id is None):
            raise ValueError("Prior event has no exact member subject")
        if (prior.target_exchange_id, prior.target_user_turn_id, prior.target_assistant_turn_id) != (
                clean_exchange, ut, at):
            raise ValueError("Repair target does not match the prior event subject")

        if validated_proposal is not None:
            _validate_proposal(validated_proposal, expected={
                "discussion_id": clean_discussion,
                "session_id": clean_session,
                "target_exchange_id": clean_exchange,
                "target_user_turn_id": ut,
                "target_assistant_turn_id": at,
                "repair_text": rt,
                "prior_event_id": prior_id,
            })

        event = DiscussionEventRow(
            discussion_id=clean_discussion,
            session_id=clean_session,
            event_type=EVENT_TYPE_LINK_REPAIR,
            target_exchange_id=clean_exchange,
            target_user_turn_id=ut,
            target_assistant_turn_id=at,
            prior_event_id=prior_id,
            payload_text=rt,
            provenance=prov,
            created_at=self._now(),
        )
        self._session.add(event)
        self._session.flush()
        return _row_to_event(event)

    def add_repair(self, *args, **kwargs):
        return self.repair_link(*args, **kwargs)

    def repair(self, *args, **kwargs):
        return self.repair_link(*args, **kwargs)

    def correct(self, *args, **kwargs):
        return self.add_correction(*args, **kwargs)

    def conflict(self, *args, **kwargs):
        return self.add_conflict(*args, **kwargs)

    # ---------------------------------------------------------------- reads
    def read_discussion(self, db: Any = None, session_id: Any = None,
                        discussion_id: Any = None, *,
                        member_limit: Any = None,
                        event_limit: Any = None,
                        **_kwargs: Any) -> Optional[DiscussionView]:
        # Allow read_discussion(db, session_id=..., discussion_id=...)
        if db is None:
            db = self._session
        if session_id is None:
            session_id = _kwargs.get("session")
        if discussion_id is None:
            discussion_id = _kwargs.get("id", _kwargs.get("discussion"))
        session = _session_of(db) if not isinstance(db, Session) and db is not None else db
        # Actually normalize:
        session_obj = _session_of(db) if db is not None else self._session
        clean_session = _clean_exact_id(session_id, "session identity")
        clean_discussion = _clean_exact_id(discussion_id, "discussion identity")
        ml = self._normalize_optional_limit(member_limit, "member limit")
        el = self._normalize_optional_limit(event_limit, "event limit")

        with session_obj.no_autoflush:
            disc = session_obj.query(DiscussionRow).filter_by(
                discussion_id=clean_discussion, session_id=clean_session).populate_existing().first()
            if disc is None:
                return None

            # Members via exact joins.
            members_all: list[DiscussionMember] = []
            links = (
                session_obj.query(DiscussionExchangeLinkRow)
                .filter_by(discussion_id=clean_discussion, session_id=clean_session)
                .order_by(DiscussionExchangeLinkRow.created_at.asc(), DiscussionExchangeLinkRow.exchange_id.asc())
                .all()
            )
            for link in links:
                exchange = (
                    session_obj.query(ConversationExchange)
                    .filter_by(id=link.exchange_id, session_id=clean_session)
                    .populate_existing().first()
                )
                if exchange is None:
                    continue
                user_turn = (
                    session_obj.query(ConversationTurn)
                    .filter_by(id=int(link.user_turn_id), session_id=clean_session, role="user")
                    .populate_existing().first()
                )
                if user_turn is None:
                    continue
                assistant_turn = (
                    session_obj.query(ConversationTurn)
                    .filter_by(id=int(link.assistant_turn_id), session_id=clean_session, role="assistant")
                    .populate_existing().first()
                )
                if assistant_turn is None:
                    continue
                members_all.append(_row_to_member(link))

            link_count = len(links)
            member_total = len(members_all)
            unresolved_member_count = max(link_count - member_total, 0)

            events_all = [
                _row_to_event(r) for r in (
                    session_obj.query(DiscussionEventRow)
                    .filter_by(discussion_id=clean_discussion, session_id=clean_session)
                    .order_by(DiscussionEventRow.id.asc())
                    .all()
                )
            ]
            link_keys = {(l.exchange_id, int(l.user_turn_id), int(l.assistant_turn_id)) for l in links}
            event_subject_unresolved = 0
            for ev in events_all:
                if (ev.target_exchange_id is None and ev.target_user_turn_id is None
                        and ev.target_assistant_turn_id is None):
                    continue
                if (ev.target_exchange_id is None or ev.target_user_turn_id is None
                        or ev.target_assistant_turn_id is None):
                    event_subject_unresolved += 1
                    continue
                resolved = False
                if (ev.target_exchange_id, ev.target_user_turn_id, ev.target_assistant_turn_id) in link_keys:
                    exchange = session_obj.query(ConversationExchange).filter_by(
                        id=ev.target_exchange_id, session_id=clean_session).populate_existing().first()
                    uturn = session_obj.query(ConversationTurn).filter_by(
                        id=ev.target_user_turn_id, session_id=clean_session, role="user").populate_existing().first()
                    aturn = session_obj.query(ConversationTurn).filter_by(
                        id=ev.target_assistant_turn_id, session_id=clean_session, role="assistant").populate_existing().first()
                    resolved = exchange is not None and uturn is not None and aturn is not None
                if not resolved:
                    event_subject_unresolved += 1

            event_total = len(events_all)
            member_returned = members_all if ml is None else members_all[:ml]
            event_returned = events_all if el is None else events_all[:el]
            coverage = DiscussionCoverage(
                member_available_count=member_total,
                member_returned_count=len(member_returned),
                member_omitted_count=max(member_total - len(member_returned), 0),
                event_available_count=event_total,
                event_returned_count=len(event_returned),
                event_omitted_count=max(event_total - len(event_returned), 0),
                link_count=link_count,
                unresolved_member_count=unresolved_member_count,
                member_source_complete=(unresolved_member_count == 0),
                event_unresolved_subject_count=event_subject_unresolved,
                event_source_complete=(event_subject_unresolved == 0),
            )

            return DiscussionView(
                discussion_id=disc.discussion_id,
                session_id=clean_session,
                label=disc.label,
                label_provenance=disc.label_provenance,
                task_id=disc.task_id,
                created_at=disc.created_at,
                updated_at=disc.updated_at,
                members=tuple(member_returned),
                events=tuple(event_returned),
                coverage=coverage,
            )

    def _normalize_optional_limit(self, value: Any, field_name: str) -> Optional[int]:
        if value is None:
            return None
        if isinstance(value, bool) or not isinstance(value, int):
            raise ValueError(f"{field_name} must be an integer")
        # 0 is a valid bounded empty slice; only negative values are invalid.
        if value < 0:
            raise ValueError(f"{field_name} must be non-negative")
        return int(value)

    def read_discussion_with_coverage(self, *args, **kwargs):
        return self.read_discussion(*args, **kwargs)

    def get_discussion(self, *args, **kwargs):
        # get_discussion(session_id=..., discussion_id=...) or db/session.
        session_id = kwargs.pop("session_id", kwargs.pop("session", None))
        discussion_id = kwargs.pop("discussion_id", kwargs.pop("id", kwargs.pop("discussion", None)))
        db = kwargs.pop("db", None)
        if session_id is None and args:
            if isinstance(args[0], str) and len(args) >= 2:
                session_id = args[0]
                discussion_id = args[1]
                args = args[2:]
        if db is None and args and hasattr(args[0], "execute"):
            db = args[0]
            args = args[1:]
        if db is None:
            db = self._session
        return self.read_discussion(db, session_id, discussion_id, **kwargs)

    def read_session_discussions(self, db: Any = None, session_id: Any = None, *,
                                limit: Any = 20, **_kwargs: Any):
        session_obj = _session_of(db) if db is not None else self._session
        clean_session = _clean_exact_id(session_id, "session identity")
        ml = self._normalize_optional_limit(limit, "discussion limit") if limit is not None else None
        with session_obj.no_autoflush:
            rows = (
                session_obj.query(DiscussionRow)
                .filter_by(session_id=clean_session)
                .order_by(DiscussionRow.created_at.asc(), DiscussionRow.discussion_id.asc())
                .all()
            )
        total = len(rows)
        returned_rows = rows if ml is None else rows[:ml]
        items = tuple(
            DiscussionSummary(
                discussion_id=r.discussion_id,
                session_id=clean_session,
                label=r.label,
                label_provenance=r.label_provenance,
                task_id=r.task_id,
                created_at=r.created_at,
                updated_at=r.updated_at,
            )
            for r in returned_rows
        )
        coverage = DiscussionListCoverage(
            available_count=total,
            returned_count=len(items),
            omitted_count=max(total - len(items), 0),
        )
        return items, coverage

    def list_discussions(self, *args, **kwargs):
        return self.read_session_discussions(*args, **kwargs)

    def list(self, *args, **kwargs):
        return self.read_session_discussions(*args, **kwargs)


# Module-level aliases / managers.
GroundedDiscussionPersistence = GroundedDiscussionManager
DiscussionManager = GroundedDiscussionManager
DiscussionStore = GroundedDiscussionManager
DiscussionLabelStore = GroundedDiscussionManager
DiscussionService = GroundedDiscussionManager
GroundedDiscussionService = GroundedDiscussionManager
DiscussionLabelManager = GroundedDiscussionManager

# Row aliases for import compatibility.
Discussion = DiscussionRow
DiscussionLabel = DiscussionRow
GroundedDiscussion = DiscussionRow
DiscussionExchangeLink = DiscussionExchangeLinkRow
DiscussionLink = DiscussionExchangeLinkRow
DiscussionEvent = DiscussionEventRow

__all__ = [
    "Base",
    "DiscussionRow", "DiscussionExchangeLinkRow", "DiscussionEventRow",
    "DiscussionEventSourceLinkRow",
    "Discussion", "DiscussionLabel", "GroundedDiscussion", "DiscussionExchangeLink",
    "DiscussionLink", "DiscussionEvent",
    "DiscussionMember", "DiscussionView", "DiscussionCoverage",
    "DiscussionSummary", "DiscussionListCoverage",
    "GroundedDiscussionManager", "GroundedDiscussionPersistence",
    "DiscussionManager", "DiscussionStore", "DiscussionLabelStore",
    "DiscussionService", "GroundedDiscussionService", "DiscussionLabelManager",
    "PROVENANCE_MODEL_INFERRED_UNVERIFIED", "PROVENANCE_EXPLICIT_VALIDATED_PROPOSAL",
    "PROVENANCE_HUMAN_STATED", "PROVENANCE_LEGACY_UNVERIFIED",
    "EVENT_TYPE_CORRECTION", "EVENT_TYPE_CONFLICT", "EVENT_TYPE_LINK_REPAIR",
    "EVENT_TYPE_MEMBER_LINKED",
]

# Additive opt-in sidecar for a future dual-anchor correction lineage.
# It is imported only when grounded_discussions is imported.  Runtime table
# creation remains the caller's responsibility, so ordinary storage cannot claim
# this schema exists unless tests or a future authorized migration create it.
class DiscussionEventSourceLinkRow(Base):
    __tablename__ = "discussion_event_source_links"

    event_id = Column(
        Integer, ForeignKey("discussion_events.id", ondelete="CASCADE"),
        primary_key=True,
    )
    session_id = Column(String, nullable=False, index=True)
    discussion_id = Column(
        String, ForeignKey("grounded_discussions.discussion_id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_exchange_id = Column(
        String, ForeignKey("conversation_exchanges.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    source_user_turn_id = Column(
        Integer, ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        nullable=False,
    )
    source_assistant_turn_id = Column(
        Integer, ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_exchange_id = Column(
        String, ForeignKey("conversation_exchanges.id", ondelete="CASCADE"),
        nullable=False, index=True,
    )
    target_user_turn_id = Column(
        Integer, ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        nullable=False,
    )
    target_assistant_turn_id = Column(
        Integer, ForeignKey("conversation_turns.id", ondelete="CASCADE"),
        nullable=False,
    )
    provenance = Column(String, nullable=False, default=PROVENANCE_MODEL_INFERRED_UNVERIFIED)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)

    __table_args__ = (
        # Equal endpoints would erase chronology and make a current turn masquerade
        # as an earlier target.
        CheckConstraint(
            "source_exchange_id <> target_exchange_id",
            name="check_discussion_event_source_target_distinct",
        ),
    )
