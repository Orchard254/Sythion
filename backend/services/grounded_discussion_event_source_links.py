"""Additive opt-in storage/evidence helpers for dual-anchor correction links.

This module does not call a model, activate correction events, or create
runtime tables. Importing it registers the additive sidecar class on the shared
Base.metadata; callers must still run create_all or an authorized migration.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from typing import Any, Mapping, Optional, Sequence, Tuple

try:
    from backend.models.database import (
        Base, ConversationExchange, ConversationTurn,
    )
except Exception:  # pragma: no cover
    try:
        from models.database import (
            Base, ConversationExchange, ConversationTurn,
        )
    except Exception:  # pragma: no cover
        from sqlalchemy.orm import declarative_base
        Base = declarative_base()
        ConversationExchange = None
        ConversationTurn = None

try:
    from backend.services.grounded_discussions import (
        GroundedDiscussionManager,
        DiscussionEventSourceLinkRow,
        DiscussionEventRow,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        PROVENANCE_MODEL_INFERRED_UNVERIFIED,
    )
except Exception:  # pragma: no cover
    from services.grounded_discussions import (
        GroundedDiscussionManager,
        DiscussionEventSourceLinkRow,
        DiscussionEventRow,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        PROVENANCE_MODEL_INFERRED_UNVERIFIED,
    )

from sqlalchemy.orm import Session

INDEX_DUAL_ANCHOR_PROVEN = "proven"
INDEX_DUAL_ANCHOR_UNKNOWN = "unknown"
INDEX_DUAL_ANCHOR_MISSING_SIDECAR = "missing-sidecar"
INDEX_DUAL_ANCHOR_UNORDERED = "unordered-target"


@dataclass(frozen=True)
class DualAnchorCoverage:
    event_count: int
    sidecar_count: int
    proven_count: int
    missing_sidecar_count: int
    unproven_count: int


@dataclass(frozen=True)
class DualAnchorReadResult:
    event_id: int
    source_exchange_id: str
    source_user_turn_id: int
    source_assistant_turn_id: int
    target_exchange_id: str
    target_user_turn_id: int
    target_assistant_turn_id: int
    provenance: str
    proven: bool
    reason: str
    provenance_verified: bool = False
    structural_proven: bool = False


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


def _exact_member(session, session_id, discussion_id, exchange_id, user_turn_id, assistant_turn_id):
    link = session.query(DiscussionExchangeLinkRow).filter_by(
        discussion_id=discussion_id,
        session_id=session_id,
        exchange_id=exchange_id,
        user_turn_id=user_turn_id,
        assistant_turn_id=assistant_turn_id,
    ).first()
    return link is not None


def _exact_exchange(session, session_id, exchange_id, user_turn_id, assistant_turn_id):
    if ConversationExchange is None or ConversationTurn is None:
        raise ValueError("Canonical identities unavailable")
    exchange = session.query(ConversationExchange).filter_by(
        id=exchange_id, session_id=session_id
    ).first()
    user = session.query(ConversationTurn).filter_by(
        id=user_turn_id, session_id=session_id, role="user"
    ).first()
    assistant = session.query(ConversationTurn).filter_by(
        id=assistant_turn_id, session_id=session_id, role="assistant"
    ).first()
    if exchange is None or user is None or assistant is None:
        return None
    return exchange, user, assistant


def attach_event_source_link(
    db: Any,
    *,
    event_id: Any,
    session_id: Any,
    discussion_id: Any,
    source_exchange_id: Any,
    source_user_turn_id: Any,
    source_assistant_turn_id: Any,
    target_exchange_id: Any,
    target_user_turn_id: Any,
    target_assistant_turn_id: Any,
    provenance: Any = None,
    commit: bool = True,
) -> DiscussionEventSourceLinkRow:
    """Create a dual-anchor row only when both endpoints are proved.

    The target must be a genuinely earlier exact canonical member of the same
    discussion. Self or equal/future targets are rejected.  The caller owns the
    outer transaction; this helper never commits or rolls it back.
    """
    if commit:
        raise ValueError("Caller owns transaction commit; commit=True is not supported")
    session = _session_of(db)
    sid = _clean_id(session_id, "session identity")
    did = _clean_id(discussion_id, "discussion identity")
    eid = _int_turn_event_id(event_id)
    source_exchange = _clean_id(source_exchange_id, "source exchange identity")
    source_user = _int_turn(source_user_turn_id, "source user turn")
    source_assistant = _int_turn(source_assistant_turn_id, "source assistant turn")
    target_exchange = _clean_id(target_exchange_id, "target exchange identity")
    target_user = _int_turn(target_user_turn_id, "target user turn")
    target_assistant = _int_turn(target_assistant_turn_id, "target assistant turn")

    # Ensure one caller-owned outer transaction before validation/savepoint.
    if not getattr(session.connection().connection.dbapi_connection, "in_transaction", False):
        # SQLAlchemy normally handles transactions; only seed SQLite explicitly
        # when the connection is genuinely inactive.
        session.execute(__import__("sqlalchemy", fromlist=["text"]).text("BEGIN IMMEDIATE"))

    event = session.query(DiscussionEventRow).filter_by(
        id=eid, session_id=sid, discussion_id=did
    ).first()
    if event is None:
        raise ValueError("Exact event is required in this session and discussion")
    if str(event.event_type) not in {"correction", "conflict", "link_repair"}:
        raise ValueError("Unsupported event type for dual-anchor source linking")

    source_rows = _exact_exchange(session, sid, source_exchange, source_user, source_assistant)
    target_rows = _exact_exchange(session, sid, target_exchange, target_user, target_assistant)
    if source_rows is None or target_rows is None:
        raise ValueError("Exact source and target exchange identities are required")
    if source_exchange == target_exchange:
        raise ValueError("Source and target must be distinct exchanges")

    # Reliable chronological proof is strict created_at ordering in canonical
    # storage. Distinct IDs are not chronology, and equality is insufficient.
    source_created = source_rows[0].created_at
    target_created = target_rows[0].created_at
    if source_created is None or target_created is None:
        raise ValueError("Canonical chronology is unavailable for source and target")
    try:
        ordered = target_created < source_created
    except (TypeError, ValueError, OverflowError):
        raise ValueError("Canonical chronology is not comparable for source and target")
    if not ordered:
        raise ValueError("Target must be a genuinely earlier canonical exchange")

    if not _exact_member(session, sid, did, source_exchange, source_user, source_assistant):
        raise ValueError("Source must already be a linked member of the discussion")
    if not _exact_member(session, sid, did, target_exchange, target_user, target_assistant):
        raise ValueError("Target must already be a linked member of the discussion")

    prov = provenance if provenance is not None else PROVENANCE_MODEL_INFERRED_UNVERIFIED
    if not isinstance(prov, str) or prov.strip() not in {
        "model_inferred_unverified",
        "explicit_validated_proposal",
        "human_stated",
        "legacy_unverified",
    }:
        raise ValueError("Unsupported dual-anchor provenance")
    prov = prov.strip()

    from sqlalchemy.exc import IntegrityError
    savepoint = session.begin_nested()
    try:
        row = DiscussionEventSourceLinkRow(
            event_id=eid,
            session_id=sid,
            discussion_id=did,
            source_exchange_id=source_exchange,
            source_user_turn_id=source_user,
            source_assistant_turn_id=source_assistant,
            target_exchange_id=target_exchange,
            target_user_turn_id=target_user,
            target_assistant_turn_id=target_assistant,
            provenance=prov,
            created_at=datetime.utcnow(),
        )
        session.add(row)
        session.flush()
        savepoint.commit()
        return row
    except IntegrityError as exc:
        savepoint.rollback()
        raise ValueError("Dual-anchor source link already exists for this event") from exc
    except Exception:
        savepoint.rollback()
        raise


def _int_turn_event_id(value: Any) -> int:
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise ValueError("Exact event identity is required")
    return int(value)


def read_event_source_link(db: Any, event_id: Any) -> Optional[DualAnchorReadResult]:
    session = _session_of(db)
    eid = _int_turn_event_id(event_id)
    try:
        row = session.query(DiscussionEventSourceLinkRow).filter_by(event_id=eid).first()
    except (ValueError, TypeError, AttributeError, OverflowError):
        return None
    if row is None:
        return None
    event = session.query(DiscussionEventRow).filter_by(
        id=eid, session_id=row.session_id, discussion_id=row.discussion_id
    ).first()
    if event is None or str(event.event_type) not in {"correction", "conflict", "link_repair"}:
        return DualAnchorReadResult(
            event_id=eid,
            source_exchange_id=row.source_exchange_id,
            source_user_turn_id=int(row.source_user_turn_id),
            source_assistant_turn_id=int(row.source_assistant_turn_id),
            target_exchange_id=row.target_exchange_id,
            target_user_turn_id=int(row.target_user_turn_id),
            target_assistant_turn_id=int(row.target_assistant_turn_id),
            provenance=str(row.provenance or ""),
            proven=False,
            reason="unsupported-event",
            provenance_verified=False,
            structural_proven=False,
        )
    source_exchange = None
    target_exchange = None
    try:
        source_exchange = session.query(ConversationExchange).filter_by(
            id=row.source_exchange_id, session_id=row.session_id
        ).first()
        target_exchange = session.query(ConversationExchange).filter_by(
            id=row.target_exchange_id, session_id=row.session_id
        ).first()
    except (ValueError, TypeError, AttributeError, OverflowError):
        source_exchange = None
        target_exchange = None
    provenance = str(row.provenance or "")
    provenance_verified = provenance in {"human_stated", "explicit_validated_proposal"}
    proven = True
    structural_proven = True
    reason = INDEX_DUAL_ANCHOR_PROVEN
    chronological = True
    if source_exchange is None or target_exchange is None:
        proven = False
        structural_proven = False
        reason = "missing-canonical-source-or-target"
    elif row.source_exchange_id == row.target_exchange_id:
        proven = False
        structural_proven = False
        reason = INDEX_DUAL_ANCHOR_UNORDERED
    elif source_exchange.created_at is None or target_exchange.created_at is None:
        proven = False
        structural_proven = False
        reason = "incomparable-chronology"
        chronological = False
    else:
        try:
            chronological = target_exchange.created_at < source_exchange.created_at
        except (TypeError, ValueError, OverflowError):
            chronological = False
            reason = "incomparable-chronology"
        if not chronological:
            proven = False
            structural_proven = False
            reason = "incomparable-chronology" if reason == "incomparable-chronology" else INDEX_DUAL_ANCHOR_UNORDERED
    if proven:
        if not _exact_member(session, row.session_id, row.discussion_id, row.source_exchange_id,
                             row.source_user_turn_id, row.source_assistant_turn_id):
            proven = False
            structural_proven = False
            reason = "missing-source-member"
        elif not _exact_member(session, row.session_id, row.discussion_id, row.target_exchange_id,
                               row.target_user_turn_id, row.target_assistant_turn_id):
            proven = False
            structural_proven = False
            reason = "missing-target-member"
        elif not provenance_verified:
            proven = False
            reason = "unverified-provenance"
    return DualAnchorReadResult(
        event_id=eid,
        source_exchange_id=row.source_exchange_id,
        source_user_turn_id=int(row.source_user_turn_id),
        source_assistant_turn_id=int(row.source_assistant_turn_id),
        target_exchange_id=row.target_exchange_id,
        target_user_turn_id=int(row.target_user_turn_id),
        target_assistant_turn_id=int(row.target_assistant_turn_id),
        provenance=provenance,
        proven=proven,
        reason=reason,
        provenance_verified=provenance_verified,
        structural_proven=structural_proven,
    )


def read_discussion_event_source_links(db: Any, session_id: Any, discussion_id: Any) -> list[DualAnchorReadResult]:
    session = _session_of(db)
    sid = _clean_id(session_id, "session identity")
    did = _clean_id(discussion_id, "discussion identity")
    event_ids = [
        int(row.id)
        for row in session.query(DiscussionEventRow).filter_by(
            session_id=sid, discussion_id=did
        ).all()
        if str(row.event_type) in ("correction", "conflict", "link_repair")
    ]
    results = []
    for eid in event_ids:
        result = read_event_source_link(session, eid)
        if result is not None:
            results.append(result)
    # Do not claim completeness for missing sidecars.
    return results


def assess_dual_anchor_coverage(db: Any, session_id: Any, discussion_id: Any) -> DualAnchorCoverage:
    session = _session_of(db)
    sid = _clean_id(session_id, "session identity")
    did = _clean_id(discussion_id, "discussion identity")
    event_count = sum(
        1 for row in session.query(DiscussionEventRow).filter_by(
            session_id=sid, discussion_id=did
        ).all()
        if str(row.event_type) in ("correction", "conflict", "link_repair")
    )
    sidecars = session.query(DiscussionEventSourceLinkRow).filter_by(
        session_id=sid, discussion_id=did
    ).all()
    sidecar_count = len(sidecars)
    proven_count = 0
    for sidecar in sidecars:
        result = read_event_source_link(session, int(sidecar.event_id))
        if result is not None and result.proven:
            proven_count += 1
    missing = max(event_count - sidecar_count, 0)
    unproven = max(sidecar_count - proven_count, 0)
    return DualAnchorCoverage(
        event_count=event_count,
        sidecar_count=sidecar_count,
        proven_count=proven_count,
        missing_sidecar_count=missing,
        unproven_count=unproven,
    )


from typing import Optional
__all__ = [
    "read_event_source_pair",
    "INDEX_DUAL_ANCHOR_PROVEN",
    "INDEX_DUAL_ANCHOR_UNKNOWN",
    "INDEX_DUAL_ANCHOR_MISSING_SIDECAR",
    "INDEX_DUAL_ANCHOR_UNORDERED",
    "DualAnchorCoverage",
    "DualAnchorReadResult",
    "attach_event_source_link",
    "read_event_source_link",
    "read_discussion_event_source_links",
    "assess_dual_anchor_coverage",
]
def read_event_source_pair(db: Any, event_id: Any, *, max_text_chars: int = 4000) -> dict[str, Any]:
    session = _session_of(db)
    eid = _int_turn_event_id(event_id)
    result: dict[str, Any] = {
        "event_id": eid,
        "available": False,
        "structural_complete": False,
        "gaps": [],
        "source": None,
        "target": None,
        "provenance_verified": False,
    }
    event = session.query(DiscussionEventRow).filter_by(id=eid).first()
    if event is None:
        result["gaps"].append("missing-canonical-event")
        return result
    result["session_id"] = event.session_id
    result["discussion_id"] = event.discussion_id
    result["event_type"] = event.event_type

    read_link = read_event_source_link(session, eid)
    if read_link is None:
        result["gaps"].append(INDEX_DUAL_ANCHOR_MISSING_SIDECAR)
        return result

    source_exchange = None
    target_exchange = None
    try:
        source_exchange = session.query(ConversationExchange).filter_by(
            id=read_link.source_exchange_id, session_id=event.session_id
        ).first()
        target_exchange = session.query(ConversationExchange).filter_by(
            id=read_link.target_exchange_id, session_id=event.session_id
        ).first()
    except (ValueError, TypeError, AttributeError, OverflowError):
        source_exchange = None
        target_exchange = None

    source_member = _exact_member(
        session, event.session_id, event.discussion_id,
        read_link.source_exchange_id, read_link.source_user_turn_id,
        read_link.source_assistant_turn_id,
    )
    target_member = _exact_member(
        session, event.session_id, event.discussion_id,
        read_link.target_exchange_id, read_link.target_user_turn_id,
        read_link.target_assistant_turn_id,
    )

    def _source_payload(exchange, uid, aid, member):
        if exchange is None:
            return None
        user_text = exchange.user_text
        assistant_text = exchange.assistant_text
        truncated = False
        if isinstance(user_text, str) and len(user_text) > max_text_chars:
            user_text = user_text[:max_text_chars]
            truncated = True
        if isinstance(assistant_text, str) and len(assistant_text) > max_text_chars:
            assistant_text = assistant_text[:max_text_chars]
            truncated = True
        return {
            "exchange_id": exchange.id,
            "user_turn_id": int(uid),
            "assistant_turn_id": int(aid),
            "user_text": user_text,
            "assistant_text": assistant_text,
            "created_at": exchange.created_at.isoformat() if getattr(exchange, "created_at", None) is not None else None,
            "member_proven": bool(member),
            "text_truncated": truncated,
        }

    source_payload = _source_payload(
        source_exchange, read_link.source_user_turn_id, read_link.source_assistant_turn_id, source_member
    )
    target_payload = _source_payload(
        target_exchange, read_link.target_user_turn_id, read_link.target_assistant_turn_id, target_member
    )
    if source_payload is None:
        result["gaps"].append("missing-canonical-source")
    if target_payload is None:
        result["gaps"].append("missing-canonical-target")
    if source_payload is not None and source_payload["text_truncated"]:
        result["gaps"].append("source-text-truncated")
    if target_payload is not None and target_payload["text_truncated"]:
        result["gaps"].append("target-text-truncated")

    result["source"] = source_payload
    result["target"] = target_payload
    result["provenance"] = read_link.provenance
    result["provenance_verified"] = read_link.provenance_verified
    result["structural_proven"] = read_link.structural_proven
    result["available"] = source_payload is not None and target_payload is not None

    gaps = []
    if read_link.proven is False:
        gaps.append(read_link.reason)
    if source_exchange is None:
        gaps.append("missing-canonical-source")
    if target_exchange is None:
        gaps.append("missing-canonical-target")
    if not source_member:
        gaps.append("missing-source-member")
    if not target_member:
        gaps.append("missing-target-member")
    if not read_link.structural_proven:
        gaps.append("structural-incomplete")
    if not read_link.provenance_verified:
        gaps.append("unverified-provenance")
    result["gaps"] = sorted(set(result["gaps"] + gaps))

    result["structural_complete"] = (
        result["available"] is True
        and read_link.structural_proven
        and bool(source_member)
        and bool(target_member)
    )
    if not result["structural_complete"]:
        result["source"] = None
        result["target"] = None
        result["available"] = False
    return result
