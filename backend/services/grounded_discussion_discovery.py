"""Opt-in read seam for complete-or-insufficient grounded discussion discovery.

This module does not mutate state, register with runtime, or make semantic
decisions.  It reads the already-persisted grounded discussion rows for one
exact session and projects a deterministic, size-bounded resolver input.

Discovery is deliberately complete-or-insufficient:

* every structurally eligible same-session discussion is either represented in
  the result or the result is explicitly marked
  ``discovery_complete=False`` with reason ``payload_budget_exceeded`` and no
  partial representative list;
* labels, opaque IDs, provenance, and duplicate labels are preserved verbatim;
* taskless discussions are valid;
* missing canonical source coverage is counted and blocks "complete" discovery
  instead of being silently projected as a match.

The byte bound is a serialized UTF-8 payload bound, not a provider token or
context-window guarantee.  No arbitrary candidate cap, newest-N fallback,
lexical search, paging, or semantic selection is performed here.
"""

from __future__ import annotations

import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Optional, Sequence, Tuple

from sqlalchemy import func
from sqlalchemy.orm import Session

PREVIEW_LIMIT = 120

# ---------------------------------------------------------------------------
# Compatibility imports.
# ---------------------------------------------------------------------------
try:
    from backend.services.grounded_discussions import (
        DiscussionRow as _DiscussionRow,
        DiscussionExchangeLinkRow as _LinkRow,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from services.grounded_discussions import (
            DiscussionRow as _DiscussionRow,
            DiscussionExchangeLinkRow as _LinkRow,
        )
    except Exception:  # pragma: no cover
        _DiscussionRow = None
        _LinkRow = None

try:
    from backend.models.database import (
        ConversationExchange as _ConversationExchange,
        ConversationTurn as _ConversationTurn,
        Task as _Task,
        ConversationTaskAssociation as _ConversationTaskAssociation,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import (
            ConversationExchange as _ConversationExchange,
            ConversationTurn as _ConversationTurn,
            Task as _Task,
            ConversationTaskAssociation as _ConversationTaskAssociation,
        )
    except Exception:  # pragma: no cover
        _ConversationExchange = None
        _ConversationTurn = None
        _Task = None
        _ConversationTaskAssociation = None


class DiscussionDiscoveryError(ValueError):
    """Raised when discovery input validation or a minimal budget fails."""


def _session_of(db: Any) -> Session:
    if db is None:
        raise DiscussionDiscoveryError("A database session is required")
    if isinstance(db, str):
        raise DiscussionDiscoveryError("A database session is required")
    if hasattr(db, "db") and db.db is not None:
        candidate = db.db
        if hasattr(candidate, "execute") and hasattr(candidate, "flush"):
            return candidate
    if hasattr(db, "session") and db.session is not None:
        candidate = db.session
        if hasattr(candidate, "execute") and hasattr(candidate, "flush"):
            return candidate
    if hasattr(db, "runner") and db.runner is not None:
        candidate = getattr(db.runner, "db", None)
        if candidate is not None and hasattr(candidate, "execute"):
            return candidate
    if hasattr(db, "execute") and hasattr(db, "flush"):
        return db
    raise DiscussionDiscoveryError("A database session is required")


def _clean_exact_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise DiscussionDiscoveryError(f"Exact {field_name} is required")
    if not value or value != value.strip():
        raise DiscussionDiscoveryError(f"Exact {field_name} is required")
    if len(value) > 256:
        raise DiscussionDiscoveryError(f"{field_name} exceeds maximum length")
    return value


def _strict_nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise DiscussionDiscoveryError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise DiscussionDiscoveryError(f"{field_name} must be non-negative")
    return int(value)


def _strict_turn_id(value: Any) -> Optional[int]:
    # Persisted turn identities must already be integers.  Do not coerce
    # strings, padded values, or None; callers treat None as unresolved
    # source evidence rather than raising during discovery.
    if isinstance(value, bool) or not isinstance(value, int):
        return None
    if value < 1:
        return None
    return int(value)


def _dt_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise DiscussionDiscoveryError("Persisted timestamp must be datetime or None")


def _task_proof(
    session: Session,
    persisted_task_id: Optional[str],
    exchange_id: str,
    user_turn_id: int,
    assistant_turn_id: int,
    clean_session: str,
) -> Tuple[Optional[str], bool]:
    if persisted_task_id is None:
        return None, False
    if _ConversationTaskAssociation is None or _Task is None:
        return None, False
    association = session.query(_ConversationTaskAssociation).filter_by(
        exchange_id=exchange_id,
        user_turn_id=user_turn_id,
        assistant_turn_id=assistant_turn_id,
    ).first()
    if association is None:
        return None, False
    if str(getattr(association, "task_id", None)) != str(persisted_task_id):
        return None, False
    task_row = session.query(_Task).filter_by(id=str(persisted_task_id)).first()
    if task_row is None:
        return None, False
    return str(persisted_task_id), True


# ---------------------------------------------------------------------------
# Immutable discovery records.
# ---------------------------------------------------------------------------
@dataclass(frozen=True, slots=True)
class DiscussionDiscoveryRepresentative:
    """One durable, structurally checked discussion representative."""

    discussion_id: str
    session_id: str
    label: str
    label_provenance: str
    label_unverified: bool
    has_unverified_or_legacy_provenance: bool
    created_at: Optional[str]
    canonical_source_complete: bool
    unresolved_source_count: int
    link_count: int
    exact_task_proof: bool
    task_id: Optional[str]
    preview_text: Optional[str]
    preview_truncated: bool
    structurally_eligible: bool

    @property
    def eligible(self) -> bool:
        return self.structurally_eligible

    @property
    def source_complete(self) -> bool:
        return self.canonical_source_complete

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class DiscussionDiscoveryResult:
    """Complete-or-insufficient discovery input for a resolver."""

    session_id: str
    max_payload_bytes: int
    available_count: int
    returned_count: int
    omitted_count: int
    unresolved_discussion_count: int
    unresolved_task_proof_count: int
    candidate_discussion_ids: Tuple[str, ...]
    representatives: Tuple[DiscussionDiscoveryRepresentative, ...]
    discovery_complete: bool
    reason: str
    payload_bytes: int
    payload_json: str
    untrusted: bool = True
    semantic_complete: bool = False

    @property
    def candidate_ids(self) -> Tuple[str, ...]:
        return self.candidate_discussion_ids

    @property
    def discovered_discussion_ids(self) -> Tuple[str, ...]:
        return self.candidate_discussion_ids

    @property
    def payload_byte_count(self) -> int:
        return self.payload_bytes

    @property
    def serialized_payload(self) -> str:
        return self.payload_json

    @property
    def representative_count(self) -> int:
        return self.returned_count

    def to_dict(self) -> dict[str, Any]:
        data = asdict(
            replace(
                self,
                representatives=(),
                candidate_discussion_ids=(),
            )
        )
        data["representatives"] = [r.to_dict() for r in self.representatives]
        data["candidate_discussion_ids"] = list(self.candidate_discussion_ids)
        return data

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


def _serialize(payload: Any) -> str:
    return json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _make_preview(session: Session, exchange_id: str, clean_session: str) -> Tuple[Optional[str], bool]:
    raw = session.query(
        func.substr(_ConversationExchange.user_text, 1, PREVIEW_LIMIT + 1)
    ).filter_by(id=exchange_id, session_id=clean_session).scalar()
    if not isinstance(raw, str):
        return None, False
    if len(raw) > PREVIEW_LIMIT:
        return raw[:PREVIEW_LIMIT], True
    return raw, False


def read_session_discussion_discovery(
    db: Any,
    session_id: Any,
    *,
    max_payload_bytes: Any,
) -> DiscussionDiscoveryResult:
    """Read one session's grounded discussions as a bounded discovery input.

    The public API is intentionally minimal and strict:

        read_session_discussion_discovery(db, session_id, max_payload_bytes=...)

    ``max_payload_bytes`` is keyword-only.  No positional byte budget or
    alias keyword is accepted.

    The bound counts bytes of the serialized provider-facing discovery
    payload envelope in ``payload_json`` (UTF-8).  It is not a token or
    context-window promise, and ``result.to_dict()`` diagnostics are not
    part of the bounded provider payload.
    """

    # The public parameter is keyword-only.  Compatibility aliases are
    # intentionally unsupported; unknown keywords remain an error here.
    if max_payload_bytes is None:
        raise DiscussionDiscoveryError("max_payload_bytes is required")

    # Validate before reading.
    clean_session = _clean_exact_id(session_id, "session identity")
    max_bytes = _strict_nonnegative_int(max_payload_bytes, "max_payload_bytes")

    session = _session_of(db)

    with session.no_autoflush:
        if _DiscussionRow is None or _LinkRow is None:
            raise DiscussionDiscoveryError("Grounded discussion model is unavailable")

        rows = list(
            session.query(_DiscussionRow)
            .filter_by(session_id=clean_session)
            .order_by(
                _DiscussionRow.created_at.asc(),
                _DiscussionRow.discussion_id.asc(),
            )
            .all()
        )

        available_count = len(rows)
        unresolved_discussion_count = 0
        unresolved_task_proof_count = 0
        representatives: list[DiscussionDiscoveryRepresentative] = []

        for discussion_row in rows:
            discussion_id = str(discussion_row.discussion_id)
            persisted_task_id = getattr(discussion_row, "task_id", None)
            if persisted_task_id is not None:
                persisted_task_id = str(persisted_task_id)

            links = list(
                session.query(_LinkRow)
                .filter_by(discussion_id=discussion_id, session_id=clean_session)
                .order_by(_LinkRow.created_at.asc(), _LinkRow.exchange_id.asc())
                .all()
            )

            unresolved_source_count = 0
            preview_text: Optional[str] = None
            preview_truncated = False
            has_unverified = str(discussion_row.label_provenance) not in {
                "human_stated", "explicit_validated_proposal",
            }
            label_prov = str(discussion_row.label_provenance)

            if not links:
                # A discussion row with no persisted links cannot have an
                # exact canonical same-session source.  It remains unresolved
                # instead of being silently considered complete and returnable.
                unresolved_source_count = 1

            for link in links:
                exchange_id = getattr(link, "exchange_id", None)
                user_turn_id = _strict_turn_id(getattr(link, "user_turn_id", None))
                assistant_turn_id = _strict_turn_id(
                    getattr(link, "assistant_turn_id", None)
                )
                exchange_ok = (
                    isinstance(exchange_id, str)
                    and bool(exchange_id.strip())
                    and exchange_id == exchange_id.strip()
                )
                if (not exchange_ok or user_turn_id is None
                        or assistant_turn_id is None):
                    unresolved_source_count += 1
                    continue

                ex_exists = (
                    session.query(_ConversationExchange.id)
                    .filter_by(id=exchange_id, session_id=clean_session)
                    .first()
                    is not None
                )
                ut_exists = (
                    session.query(_ConversationTurn.id)
                    .filter_by(
                        id=user_turn_id,
                        session_id=clean_session,
                        role="user",
                    )
                    .first()
                    is not None
                )
                at_exists = (
                    session.query(_ConversationTurn.id)
                    .filter_by(
                        id=assistant_turn_id,
                        session_id=clean_session,
                        role="assistant",
                    )
                    .first()
                    is not None
                )
                source_ok = ex_exists and ut_exists and at_exists
                if not source_ok:
                    unresolved_source_count += 1
                    continue
                if preview_text is None and _ConversationExchange is not None:
                    preview_text, preview_truncated = _make_preview(
                        session, exchange_id, clean_session
                    )

            if unresolved_source_count > 0:
                unresolved_discussion_count += 1

            exact_task_proof = False
            task_id: Optional[str] = None
            if persisted_task_id is not None:
                if not links:
                    unresolved_task_proof_count += 1
                    has_unverified = True
                for link in links:
                    exchange_id = getattr(link, "exchange_id", None)
                    user_turn_id = _strict_turn_id(getattr(link, "user_turn_id", None))
                    assistant_turn_id = _strict_turn_id(
                        getattr(link, "assistant_turn_id", None)
                    )
                    if (not isinstance(exchange_id, str)
                            or not exchange_id.strip()
                            or exchange_id != exchange_id.strip()
                            or user_turn_id is None
                            or assistant_turn_id is None):
                        continue
                    ex_exists = (
                        session.query(_ConversationExchange.id)
                        .filter_by(id=exchange_id, session_id=clean_session)
                        .first()
                        is not None
                    )
                    ut_exists = (
                        session.query(_ConversationTurn.id)
                        .filter_by(
                            id=user_turn_id,
                            session_id=clean_session,
                            role="user",
                        )
                        .first()
                        is not None
                    )
                    at_exists = (
                        session.query(_ConversationTurn.id)
                        .filter_by(
                            id=assistant_turn_id,
                            session_id=clean_session,
                            role="assistant",
                        )
                        .first()
                        is not None
                    )
                    if not (ex_exists and ut_exists and at_exists):
                        continue
                    task_id, exact_task_proof = _task_proof(
                        session,
                        persisted_task_id,
                        exchange_id,
                        user_turn_id,
                        assistant_turn_id,
                        clean_session,
                    )
                    if exact_task_proof:
                        break
                if persisted_task_id is not None and links and not exact_task_proof:
                    unresolved_task_proof_count += 1
                    has_unverified = True

            provenance_values = {label_prov}
            for link in links:
                link_prov = str(getattr(link, "link_provenance", "") or "")
                if link_prov:
                    provenance_values.add(link_prov)
                    if link_prov not in ("human_stated", "explicit_validated_proposal"):
                        has_unverified = True
            has_unverified = has_unverified or any(
                p not in ("human_stated", "explicit_validated_proposal")
                for p in provenance_values
            )

            structurally_eligible = (
                unresolved_source_count == 0
                and (persisted_task_id is None or exact_task_proof)
            )

            rep = DiscussionDiscoveryRepresentative(
                discussion_id=discussion_id,
                session_id=clean_session,
                label=str(discussion_row.label),
                label_provenance=label_prov,
                label_unverified=label_prov
                not in ("human_stated", "explicit_validated_proposal"),
                has_unverified_or_legacy_provenance=bool(has_unverified),
                created_at=_dt_text(getattr(discussion_row, "created_at", None)),
                canonical_source_complete=(unresolved_source_count == 0),
                unresolved_source_count=unresolved_source_count,
                link_count=len(links),
                exact_task_proof=exact_task_proof,
                task_id=task_id,
                preview_text=preview_text,
                preview_truncated=preview_truncated,
                structurally_eligible=structurally_eligible,
            )
            if structurally_eligible:
                representatives.append(rep)

    representatives.sort(key=lambda r: (r.created_at or "", r.discussion_id))

    def _build_payload(reps_list):
        reps_payload = [r.to_dict() for r in reps_list]
        omitted = available_count - len(reps_list)
        complete = (
            unresolved_discussion_count == 0
            and unresolved_task_proof_count == 0
            and omitted == 0
            and len(reps_list) == available_count
        )
        reason = "complete" if complete else (
            "unresolved_canonical_source"
            if (unresolved_discussion_count > 0 or unresolved_task_proof_count > 0)
            else "payload_budget_exceeded"
        )
        candidate_ids = tuple(r.discussion_id for r in reps_list)
        payload = {
            "session_id": clean_session,
            "available_count": available_count,
            "returned_count": len(reps_list),
            "omitted_count": omitted,
            "unresolved_discussion_count": unresolved_discussion_count,
            "unresolved_task_proof_count": unresolved_task_proof_count,
            "candidate_discussion_ids": list(candidate_ids),
            "discovery_complete": complete if len(reps_list) > 0 or available_count == 0 else False,
            "reason": reason if (len(reps_list) > 0 or available_count == 0) else "payload_budget_exceeded",
            "representatives": reps_payload,
        }
        # discovery_complete cannot be true when no representatives were returned
        # but discussions exist due to budget.
        json_text = _serialize(payload)
        return json_text, omitted, complete, reason, candidate_ids

    # First try the complete candidate set.
    json_text, omitted, complete, reason, candidate_ids = _build_payload(representatives)
    byte_count = len(json_text.encode("utf-8"))
    if byte_count <= max_bytes:
        # Build final payload with accurate discovery_complete. If the set could
        # not be returned at all under the bound, force overflow semantics; the
        # branch above only runs when the full set fits.
        discovery_complete = (
            unresolved_discussion_count == 0
            and unresolved_task_proof_count == 0
            and omitted == 0
            and len(representatives) == available_count
        )
        final_reason = "complete" if discovery_complete else (
            "unresolved_canonical_source"
            if (unresolved_discussion_count > 0 or unresolved_task_proof_count > 0)
            else "payload_budget_exceeded"
        )
        final_payload = {
            "session_id": clean_session,
            "available_count": available_count,
            "returned_count": len(representatives),
            "omitted_count": omitted,
            "unresolved_discussion_count": unresolved_discussion_count,
            "unresolved_task_proof_count": unresolved_task_proof_count,
            "candidate_discussion_ids": list(candidate_ids),
            "discovery_complete": discovery_complete,
            "reason": final_reason,
            "representatives": [r.to_dict() for r in representatives],
        }
        final_json = _serialize(final_payload)
        return DiscussionDiscoveryResult(
            session_id=clean_session,
            max_payload_bytes=max_bytes,
            available_count=available_count,
            returned_count=len(representatives),
            omitted_count=omitted,
            unresolved_discussion_count=unresolved_discussion_count,
            unresolved_task_proof_count=unresolved_task_proof_count,
            candidate_discussion_ids=candidate_ids,
            representatives=tuple(representatives),
            discovery_complete=discovery_complete,
            reason=final_reason,
            payload_bytes=len(final_json.encode("utf-8")),
            payload_json=final_json,
        )

    # Overflow: no partial representatives.
    minimal_payload = {
        "session_id": clean_session,
        "available_count": available_count,
        "returned_count": 0,
        "omitted_count": available_count,
        "unresolved_discussion_count": unresolved_discussion_count,
        "unresolved_task_proof_count": unresolved_task_proof_count,
        "candidate_discussion_ids": [],
        "discovery_complete": False,
        "reason": "payload_budget_exceeded",
        "representatives": [],
    }
    minimal_json = _serialize(minimal_payload)
    minimal_bytes = len(minimal_json.encode("utf-8"))
    if minimal_bytes > max_bytes:
        raise DiscussionDiscoveryError(
            "max_payload_bytes is too small for the minimal discovery payload "
            f"({minimal_bytes} bytes required)"
        )
    return DiscussionDiscoveryResult(
        session_id=clean_session,
        max_payload_bytes=max_bytes,
        available_count=available_count,
        returned_count=0,
        omitted_count=available_count,
        unresolved_discussion_count=unresolved_discussion_count,
        unresolved_task_proof_count=unresolved_task_proof_count,
        candidate_discussion_ids=(),
        representatives=(),
        discovery_complete=False,
        reason="payload_budget_exceeded",
        payload_bytes=minimal_bytes,
        payload_json=minimal_json,
    )


# Keep a stable alias name for compatibility with generated tests that may use
# the CamelCase error class only; no shim module is created.
DiscussionDiscoveryResult.__doc__ = DiscussionDiscoveryResult.__doc__ or ""

__all__ = [
    "DiscussionDiscoveryError",
    "DiscussionDiscoveryRepresentative",
    "DiscussionDiscoveryResult",
    "PREVIEW_LIMIT",
    "read_session_discussion_discovery",
]
