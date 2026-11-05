"""Additive, caller-transactional, durable indexing state for grounded discussions.

This module is inert at import time.  It does not register with the runtime,
create tables automatically, call a model, or import a provider.  It reuses the
existing grounded-discussion persistence interfaces and keeps new writes inside
the caller's transaction/savepoint.
"""

from __future__ import annotations

import hashlib
import json
import uuid
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple

from sqlalchemy import Column, DateTime, ForeignKey, Index, Integer, String, Text, func, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

try:
    from backend.services.grounded_discussions import (
        GroundedDiscussionManager,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        DiscussionEventRow,
        DiscussionEventSourceLinkRow,
        EVENT_TYPE_CORRECTION,
        EVENT_TYPE_CONFLICT,
        EVENT_TYPE_LINK_REPAIR,
        PROVENANCE_MODEL_INFERRED_UNVERIFIED,
    )
except Exception:  # pragma: no cover
    from services.grounded_discussions import (
        GroundedDiscussionManager,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        DiscussionEventRow,
        DiscussionEventSourceLinkRow,
        EVENT_TYPE_CORRECTION,
        EVENT_TYPE_CONFLICT,
        EVENT_TYPE_LINK_REPAIR,
        PROVENANCE_MODEL_INFERRED_UNVERIFIED,
    )

try:
    from backend.models.database import (
        Base, ConversationExchange, ConversationTurn, Task,
    )
except Exception:  # pragma: no cover
    try:
        from models.database import (
            Base, ConversationExchange, ConversationTurn, Task,
        )
    except Exception:  # pragma: no cover
        from sqlalchemy.orm import declarative_base

        Base = declarative_base()
        ConversationExchange = None
        ConversationTurn = None
        Task = None

try:
    from backend.services.grounded_discussion_semantic_index import (
        SemanticIndexResult,
    )
except Exception:  # pragma: no cover
    from services.grounded_discussion_semantic_index import (
        SemanticIndexResult,
    )

INDEX_STATUS_PENDING = "pending"
INDEX_STATUS_IN_PROGRESS = "in_progress"
INDEX_STATUS_COMPLETE = "complete"
INDEX_STATUS_INSUFFICIENT = "insufficient"
INDEX_STATUS_UNCERTAIN = "uncertain"

__all__ = [
    "DiscussionIndexStateRow",
    "DiscussionIndexManager",
    "INDEX_STATUS_PENDING",
    "INDEX_STATUS_IN_PROGRESS",
    "INDEX_STATUS_COMPLETE",
    "INDEX_STATUS_INSUFFICIENT",
    "INDEX_STATUS_UNCERTAIN",
    "apply_semantic_index_proposals",
    "build_eligible_correction_targets",
    "canonical_exchange_input_digest",
    "CANONICAL_EXCHANGE_INPUT_SCHEMA_VERSION",
]


class DiscussionIndexStateRow(Base):
    __tablename__ = "grounded_discussion_index_states"

    idempotency_key = Column(String, primary_key=True)
    session_id = Column(String, nullable=False, index=True)
    exchange_id = Column(String, nullable=False, index=True)
    user_turn_id = Column(Integer, nullable=False)
    assistant_turn_id = Column(Integer, nullable=False)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=True, index=True)
    input_digest = Column(String, nullable=False)
    status = Column(String, nullable=False, index=True)
    result_json = Column(Text, nullable=True)
    error_code = Column(String, nullable=True)
    created_at = Column(DateTime, nullable=False, default=datetime.utcnow, index=True)
    updated_at = Column(DateTime, nullable=False, default=datetime.utcnow,
                        onupdate=datetime.utcnow)
    completed_at = Column(DateTime, nullable=True)

    __table_args__ = (
        Index(
            "ix_grounded_discussion_index_states_session_exchange",
            "session_id", "exchange_id",
        ),
    )


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
    runner = getattr(db, "runner", None)
    if runner is not None:
        inner = getattr(runner, "db", None)
        if isinstance(inner, Session):
            return runner  # pragma: no cover
    if hasattr(db, "execute") and hasattr(db, "flush"):
        return db
    raise ValueError("A database session is required")


def _clean_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise ValueError(f"Exact {field_name} is required")
    candidate = value.strip()
    if not candidate:
        raise ValueError(f"Exact {field_name} is required")
    if len(candidate) > 256:
        raise ValueError(f"{field_name} exceeds maximum length")
    return candidate


def _int_turn(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError(f"Exact {field_name} turn identity is required")
    return int(value)


def _digest(value: Any) -> str:
    text = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


CANONICAL_EXCHANGE_INPUT_SCHEMA_VERSION = "sythion.canonical_exchange_index_input.v1"


def canonical_exchange_input_digest(
    db: Any,
    session_id: Any,
    exchange_id: Any,
    user_turn_id: Any,
    assistant_turn_id: Any,
) -> str:
    """Server-owned digest over canonical exchange/turn content.

    This detects canonical source mutation without requiring a schema change.
    Caller-supplied text or arbitrary caller digests are never trusted.  The
    digest intentionally excludes non-canonical discovery candidates/batches;
    their freshness remains unknown.
    """
    session = _session_of(db)
    sid = _clean_id(session_id, "session identity")
    eid = _clean_id(exchange_id, "exchange identity")
    ut = _int_turn(user_turn_id, "user")
    at = _int_turn(assistant_turn_id, "assistant")
    _validate_exact_member(session, sid, eid, ut, at)

    exchange = session.query(ConversationExchange).filter_by(
        id=eid, session_id=sid
    ).populate_existing().first()
    user_turn = session.query(ConversationTurn).filter_by(
        id=ut, session_id=sid, role="user"
    ).populate_existing().first()
    assistant_turn = session.query(ConversationTurn).filter_by(
        id=at, session_id=sid, role="assistant"
    ).populate_existing().first()
    if exchange is None or user_turn is None or assistant_turn is None:
        raise ValueError("Canonical exchange identities are unavailable")

    payload = {
        "schema_version": CANONICAL_EXCHANGE_INPUT_SCHEMA_VERSION,
        "session_id": sid,
        "exchange_id": eid,
        "user_turn_id": ut,
        "assistant_turn_id": at,
        "exchange_user_text": exchange.user_text,
        "exchange_assistant_text": exchange.assistant_text,
        "exchange_status": exchange.status if exchange.status is not None else "",
        "user_turn_role": user_turn.role,
        "user_turn_summary": user_turn.summary if user_turn.summary is not None else "",
        "assistant_turn_role": assistant_turn.role,
        "assistant_turn_summary": assistant_turn.summary if assistant_turn.summary is not None else "",
    }
    return hashlib.sha256(
        json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")
    ).hexdigest()


def build_eligible_correction_targets(
    db: Any,
    session_id: str,
    candidate_discussion_ids: Sequence[str],
) -> Tuple[str, ...]:
    """Server-built bounded set of discussion IDs that have an exact linked
    current-session exchange/turn anchor suitable for correction events.
    """
    session_obj = _session_of(db)
    clean_session = _clean_id(session_id, "session identity")
    result = set()
    manager = GroundedDiscussionManager(session_obj)
    for did in candidate_discussion_ids or ():
        clean_did = _clean_id(did, "discussion identity")
        view = manager.read_discussion(session_obj, clean_session, clean_did)
        if view is None:
            continue
        # A correction target is exact only when the linked member actually
        # exists in same-session canonical storage.
        for member in getattr(view, "members", ()) or ():
            exchange_ok = (
                session_obj.query(ConversationExchange).filter_by(
                    id=member.exchange_id, session_id=clean_session
                ).first()
                is not None
            )
            user_ok = (
                session_obj.query(ConversationTurn).filter_by(
                    id=int(member.user_turn_id),
                    session_id=clean_session,
                    role="user",
                ).first()
                is not None
            )
            assistant_ok = (
                session_obj.query(ConversationTurn).filter_by(
                    id=int(member.assistant_turn_id),
                    session_id=clean_session,
                    role="assistant",
                ).first()
                is not None
            )
            if exchange_ok and user_ok and assistant_ok:
                result.add(clean_did)
                break
    return tuple(sorted(result))


class DiscussionIndexManager:
    """Caller-transactional durable indexing lifecycle manager."""

    def __init__(self, session: Any = None, *, db: Any = None, clock: Any = None):
        if db is not None:
            session = db
        self._session = _session_of(session)
        self._clock = clock or (lambda: datetime.utcnow())

    @property
    def db(self) -> Session:
        return self._session

    def _now(self) -> datetime:
        return self._clock()

    def _ensure_transaction(self) -> None:
        dbapi = None
        try:
            dbapi = self._session.connection().connection.dbapi_connection
        except Exception:
            dbapi = None
        try:
            cursor = dbapi.cursor() if dbapi is not None else None
            if cursor is not None:
                cursor.execute("PRAGMA busy_timeout=30000")
                cursor.close()
        except Exception:
            pass
        if dbapi is not None and getattr(dbapi, "in_transaction", False):
            return
        try:
            self._session.execute(text("BEGIN IMMEDIATE"))
        except Exception:
            pass

    def idempotency_key(
        self,
        session_id: str,
        exchange_id: str,
        user_turn_id: int,
        assistant_turn_id: int,
        input_digest: str,
    ) -> str:
        payload = {
            "session_id": session_id,
            "exchange_id": exchange_id,
            "user_turn_id": user_turn_id,
            "assistant_turn_id": assistant_turn_id,
            "input_digest": input_digest,
        }
        return "idx-" + _digest(payload)

    def receive(
        self,
        session_id: Any,
        exchange_id: Any,
        user_turn_id: Any,
        assistant_turn_id: Any,
        *,
        task_id: Any = None,
        input_digest: Any = None,
      expected_canonical_input_digest: Any = None,
        status: str = INDEX_STATUS_PENDING,
    ) -> Tuple[DiscussionIndexStateRow, bool]:
        self._ensure_transaction()
        sid = _clean_id(session_id, "session identity")
        eid = _clean_id(exchange_id, "exchange identity")
        ut = _int_turn(user_turn_id, "user")
        at = _int_turn(assistant_turn_id, "assistant")
        if expected_canonical_input_digest is not None:
            if expected_canonical_input_digest != canonical_exchange_input_digest(
                self._session, sid, eid, ut, at
            ):
                raise ValueError("Canonical model input changed")
        if task_id is not None:
            tid = _clean_id(task_id, "task identity")
            if Task is not None and self._session.query(Task).filter_by(id=tid).first() is None:
                raise ValueError("Exact task identity is required")
        else:
            tid = None
        if isinstance(input_digest, str) and input_digest.strip():
            digest = input_digest.strip()
        elif input_digest is None:
            try:
                digest = canonical_exchange_input_digest(self._session, sid, eid, ut, at)
            except Exception:
                digest = _digest({"session_id": sid, "exchange_id": eid})
        else:
            digest = _digest({"session_id": sid, "exchange_id": eid})
        key = self.idempotency_key(sid, eid, ut, at, digest)

        existing = self._session.query(DiscussionIndexStateRow).filter_by(
            idempotency_key=key
        ).populate_existing().first()
        if existing is not None:
            if (
                existing.session_id != sid
                or existing.exchange_id != eid
                or int(existing.user_turn_id) != ut
                or int(existing.assistant_turn_id) != at
                or existing.input_digest != digest
            ):
                raise ValueError("Conflicting indexing request binding")
            return existing, False

        # Detect a mismatched prior state for the same exchange under any key.
        prior = self._session.query(DiscussionIndexStateRow).filter_by(
            session_id=sid, exchange_id=eid
        ).populate_existing().first()
        if prior is not None and prior.idempotency_key != key:
            raise ValueError("Conflicting indexing request binding")

        row = DiscussionIndexStateRow(
            idempotency_key=key,
            session_id=sid,
            exchange_id=eid,
            user_turn_id=ut,
            assistant_turn_id=at,
            task_id=tid,
            input_digest=digest,
            status=status,
            result_json=None,
            error_code=None,
            created_at=self._now(),
            updated_at=self._now(),
            completed_at=None,
        )
        operation = self._session.begin_nested()
        try:
            self._session.add(row)
            self._session.flush()
            operation.commit()
        except Exception:
            try:
                operation.rollback()
            except Exception:
                pass
            raise
        return row, True

    def transition(self, key: Any, status: str, *, result_json: Any = None,
                   error_code: Any = None, commit: bool = False) -> DiscussionIndexStateRow:
        clean_key = _clean_id(key, "idempotency key")
        row = self._session.query(DiscussionIndexStateRow).filter_by(
            idempotency_key=clean_key
        ).populate_existing().first()
        if row is None:
            raise ValueError("Unknown indexing state")
        row.status = status
        if result_json is not None:
            row.result_json = (
                result_json if isinstance(result_json, str) else json.dumps(
                    result_json, sort_keys=True, separators=(",", ":")
                )
            )
        if error_code is not None:
            row.error_code = str(error_code)
        row.updated_at = self._now()
        if status in (INDEX_STATUS_COMPLETE, INDEX_STATUS_INSUFFICIENT):
            row.completed_at = self._now()
        if commit:
            self._session.commit()
        else:
            self._session.flush()
        return row

    def get(self, key: Any) -> Optional[DiscussionIndexStateRow]:
        try:
            clean_key = _clean_id(key, "idempotency key")
        except ValueError:
            return None
        return self._session.query(DiscussionIndexStateRow).filter_by(
            idempotency_key=clean_key
        ).populate_existing().first()


def _validate_exact_member(
    session: Session,
    session_id: str,
    exchange_id: str,
    user_turn_id: int,
    assistant_turn_id: int,
) -> None:
    if ConversationExchange is None or ConversationTurn is None:
        raise ValueError("Exact exchange identities are unavailable")
    exchange = session.query(ConversationExchange).filter_by(
        id=exchange_id, session_id=session_id
    ).populate_existing().first()
    if exchange is None:
        raise ValueError("Exact exchange is unavailable in this session")
    user_turn = session.query(ConversationTurn).filter_by(
        id=user_turn_id, session_id=session_id, role="user"
    ).populate_existing().first()
    if user_turn is None:
        raise ValueError("Exact user turn identity is unavailable in this session")
    assistant_turn = session.query(ConversationTurn).filter_by(
        id=assistant_turn_id, session_id=session_id, role="assistant"
    ).populate_existing().first()
    if assistant_turn is None:
        raise ValueError("Exact assistant turn identity is unavailable in this session")


def apply_semantic_index_proposals(
    db: Any,
    *,
    session_id: Any,
    exchange_id: Any,
    user_turn_id: Any,
    assistant_turn_id: Any,
    task_id: Any = None,
    semantic_result: Any = None,
    input_digest: Any = None,
    expected_canonical_input_digest: Any = None,
    commit: bool = True,
    manager: Any = None,
) -> dict[str, Any]:
    """Apply a validated semantic-index result under caller transaction control.

    The function preserves unrelated pending writes, fails closed on mismatched
    anchors, and only writes correction/conflict events when the exact target is
    already a linked member after any required server-owned link.
    """
    session = _session_of(db)
    sid = _clean_id(session_id, "session identity")
    eid = _clean_id(exchange_id, "exchange identity")
    ut = _int_turn(user_turn_id, "user")
    at = _int_turn(assistant_turn_id, "assistant")
    tid = _clean_id(task_id, "task identity") if task_id is not None else None

    if ConversationExchange is not None and session.query(ConversationExchange).filter_by(
        id=eid, session_id=sid
    ).first() is None:
        raise ValueError("Canonical exchange is required in this session")
    _validate_exact_member(session, sid, eid, ut, at)

    if semantic_result is None:
        raise ValueError("A validated semantic index result is required")
    if not isinstance(semantic_result, SemanticIndexResult):
        state = getattr(semantic_result, "status", None)
        proposals = getattr(semantic_result, "proposals", None)
        clarification = getattr(semantic_result, "clarification", None)
        reason = getattr(semantic_result, "reason", None)
    else:
        state = semantic_result.status
        proposals = semantic_result.proposals
        clarification = semantic_result.clarification
        reason = semantic_result.reason
    if state not in ("proposals", "insufficient", "ambiguous"):
        raise ValueError("Invalid semantic index status")
    if not isinstance(proposals, (tuple, list)):
        raise ValueError("Semantic index proposals must be a sequence")
    proposals = list(proposals)
    if state == "proposals" and not proposals:
        raise ValueError("Semantic index proposals status requires proposals")
    if state in ("insufficient", "ambiguous") and proposals:
        raise ValueError("Semantic index non-proposal status requires no proposals")

    if expected_canonical_input_digest is not None:
        computed = canonical_exchange_input_digest(session, sid, eid, ut, at)
        if expected_canonical_input_digest != computed:
            raise ValueError("Canonical model input changed")
    digest_value = (
        input_digest if isinstance(input_digest, str) and input_digest.strip() else None
    )
    if digest_value is None:
        try:
            digest_value = canonical_exchange_input_digest(session, sid, eid, ut, at)
        except Exception:
            digest_value = _digest(
                {
                    "session_id": sid,
                    "exchange_id": eid,
                    "status": state,
                    "proposals": proposals,
                }
            )
    index_manager = DiscussionIndexManager(session, db=session)
    state_row, _ = index_manager.receive(
        sid, eid, ut, at, task_id=tid, input_digest=digest_value, status=INDEX_STATUS_PENDING
    )

    if state_row.session_id != sid or state_row.exchange_id != eid:
        raise ValueError("Conflicting indexing request binding")
    if int(state_row.user_turn_id) != ut or int(state_row.assistant_turn_id) != at:
        raise ValueError("Conflicting indexing request binding")
    if state_row.input_digest != digest_value:
        raise ValueError("Conflicting indexing request binding")
    if state_row.status in (INDEX_STATUS_COMPLETE, INDEX_STATUS_INSUFFICIENT):
        return {
            "idempotency_key": state_row.idempotency_key,
            "status": state_row.status,
            "applied_discussion_ids": [],
            "applied_count": 0,
            "replicated": True,
        }

    index_manager.transition(state_row.idempotency_key, INDEX_STATUS_IN_PROGRESS, commit=False)

    preexisting_members: dict[str, set[tuple[int, int]]] = {}
    for raw_proposal in proposals:
        if not isinstance(raw_proposal, Mapping):
            continue
        did = raw_proposal.get("discussion_id")
        if not isinstance(did, str) or not did.strip():
            continue
        did = did.strip()
        links = session.query(DiscussionExchangeLinkRow).filter_by(
            discussion_id=did, session_id=sid
        ).all()
        members = set()
        for link in links:
            try:
                members.add((int(link.exchange_id == link.exchange_id), 0))
            except Exception:
                continue
        # Keep exact prior members as (user_turn_id, assistant_turn_id) keyed by
        # exchange identity separately in a parallel lookup below.
        preexisting_members[did] = members
    # Replace the placeholder above with the precise prior-member map.
    preexisting_members = {}
    for raw_proposal in proposals:
        if not isinstance(raw_proposal, Mapping):
            continue
        did = raw_proposal.get("discussion_id")
        if not isinstance(did, str) or not did.strip():
            continue
        did = did.strip()
        member_set = set()
        for link in session.query(DiscussionExchangeLinkRow).filter_by(
            discussion_id=did, session_id=sid
        ).all():
            member_set.add((str(link.exchange_id), int(link.user_turn_id), int(link.assistant_turn_id)))
        preexisting_members[did] = member_set

    operation = session.begin_nested()
    outcome_status = INDEX_STATUS_COMPLETE
    applied_ids: list[str] = []
    unresolved: list[str] = []
    error_code: Optional[str] = None
    try:
        grounded = manager if manager is not None else GroundedDiscussionManager(session)
        if state in ("insufficient", "ambiguous") or not proposals:
            outcome_status = INDEX_STATUS_INSUFFICIENT if state in ("insufficient", "ambiguous") else INDEX_STATUS_COMPLETE
        else:
            for raw_proposal in proposals:
                if not isinstance(raw_proposal, Mapping):
                    raise ValueError("Semantic index proposal must be a mapping")
                sid_check = raw_proposal.get("session_id", sid)
                if sid_check != sid:
                    raise ValueError("Semantic index proposal session mismatch")
                _validate_exact_member(session, sid, eid, ut, at)

                has_claim = any(
                    isinstance(raw_proposal.get(k), str) and str(raw_proposal.get(k)).strip()
                    for k in ("correction_text", "conflict_text", "repair_text")
                )
                discussion_id = raw_proposal.get("discussion_id")
                label = raw_proposal.get("label")
                new_id: Optional[str] = None

                if discussion_id is not None:
                    discussion_id = _clean_id(discussion_id, "discussion identity")
                    existing = session.query(DiscussionRow).filter_by(
                        discussion_id=discussion_id, session_id=sid
                    ).populate_existing().first()
                    if existing is None:
                        raise ValueError("Discussion is not visible in this session")
                    preexisting = preexisting_members.get(discussion_id, set())
                    # A link may be created for a membership proposal, but not as a
                    # device to fabricate correction lineage for an arbitrary
                    # existing discussion.
                    if not has_claim:
                        try:
                            grounded.link_exchange(
                                discussion_id,
                                sid,
                                eid,
                                ut,
                                at,
                                link_provenance=PROVENANCE_MODEL_INFERRED_UNVERIFIED,
                                validated_proposal={
                                    "discussion_id": discussion_id,
                                    "session_id": sid,
                                    "target_exchange_id": eid,
                                    "target_user_turn_id": ut,
                                    "target_assistant_turn_id": at,
                                },
                            )
                        except ValueError as exc:
                            if "already exists" not in str(exc).lower() and "conflicts" not in str(exc).lower():
                                raise
                elif label is not None:
                    clean_label = str(label).strip()
                    if not clean_label:
                        raise ValueError("A non-empty discussion label is required")
                    created = grounded.create_discussion(
                        session_id=sid,
                        label=clean_label,
                        label_provenance=PROVENANCE_MODEL_INFERRED_UNVERIFIED,
                        task_id=tid,
                        member_exchanges=[
                            {
                                "exchange_id": eid,
                                "user_turn_id": ut,
                                "assistant_turn_id": at,
                            }
                        ],
                        link_provenance=PROVENANCE_MODEL_INFERRED_UNVERIFIED,
                        validated_proposal={
                            "session_id": sid,
                            "target_exchange_id": eid,
                            "target_user_turn_id": ut,
                            "target_assistant_turn_id": at,
                            "label": clean_label,
                        },
                    )
                    try:
                        new_id = getattr(created, "discussion_id", None)
                    except Exception:
                        new_id = None
                    # A newly created discussion has the current exact exchange as a
                    # server-owned linked member, so correction text stored as part
                    # of this same proposal may be applied to that exact anchor.
                    preexisting = None
                else:
                    raise ValueError("Semantic index proposal must define a discussion or label")

                target_discussion = discussion_id or new_id
                if target_discussion is None:
                    raise ValueError("Server did not assign a discussion identity")

                # Select a correction target using DB-proven pre-existing members.
                # If the proposal names a target, that exact target must already be
                # a member. Otherwise, accept only an unambiguous single existing
                # member; never choose among multiple candidates.
                lp = PROVENANCE_MODEL_INFERRED_UNVERIFIED
                claims = []
                for key in ("correction_text", "conflict_text", "repair_text"):
                    value = raw_proposal.get(key)
                    if isinstance(value, str) and value.strip():
                        claims.append((key, value.strip()))

                # Conservative product decision: until the semantic-index/evidence
                # layers can jointly represent the *current correction source* and
                # an independently DB-proven *earlier target* exchange, this
                # application layer does not emit correction/conflict/repair
                # events.  Recording the claim as unresolved avoids pretending that
                # an inferred label mutation is a verified correction.
                if claims:
                    claim_keys = ",".join(key for key, _ in claims)
                    unresolved_reason = (
                        f"{target_discussion}:{claim_keys}:correction-event-"
                        "representation-not-implemented"
                    )
                    if unresolved_reason not in unresolved:
                        unresolved.append(unresolved_reason)

                if label is not None and new_id is not None:
                    applied_ids.append(target_discussion)
                elif discussion_id is not None and not has_claim:
                    # Ordinary membership application only.
                    try:
                        grounded.link_exchange(
                            discussion_id,
                            sid,
                            eid,
                            ut,
                            at,
                            link_provenance=PROVENANCE_MODEL_INFERRED_UNVERIFIED,
                            validated_proposal={
                                "discussion_id": discussion_id,
                                "session_id": sid,
                                "target_exchange_id": eid,
                                "target_user_turn_id": ut,
                                "target_assistant_turn_id": at,
                            },
                        )
                    except ValueError as exc:
                        if "already exists" not in str(exc).lower() and "conflicts" not in str(exc).lower():
                            raise
                    applied_ids.append(target_discussion)

                if has_claim:
                    outcome_status = INDEX_STATUS_INSUFFICIENT

        result_payload = {
            "status": state,
            "applied_discussion_ids": applied_ids,
            "unresolved": unresolved,
            "clarification": clarification,
            "reason": reason,
        }
        if unresolved or state in ("insufficient", "ambiguous"):
            new_status = INDEX_STATUS_INSUFFICIENT
        else:
            new_status = INDEX_STATUS_COMPLETE
        index_manager.transition(
            state_row.idempotency_key,
            new_status,
            result_json=result_payload,
            commit=False,
        )
        session.flush()
        operation.commit()
    except Exception as exc:
        try:
            operation.rollback()
        except Exception:
            pass
        index_manager.transition(
            state_row.idempotency_key,
            INDEX_STATUS_UNCERTAIN,
            error_code=exc.__class__.__name__,
            commit=False,
        )
        session.flush()
        raise
    finally:
        pass

    if commit:
        session.commit()
    else:
        session.flush()
    return {
        "idempotency_key": state_row.idempotency_key,
        "status": index_manager.get(state_row.idempotency_key).status,
        "applied_discussion_ids": applied_ids,
        "applied_count": len(applied_ids),
        "unresolved": unresolved,
        "replicated": False,
    }
