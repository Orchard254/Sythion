"""Opt-in read seam for indexing coverage of canonical conversation exchanges.

This module is inert at import time.  It does not register tables, mutate
state, import a provider, create migrations, or touch the live database.  It
only reads already-persisted canonical :class:`ConversationExchange` rows for one
exact session and durable :class:`DiscussionIndexStateRow` rows, comparing them
to report which exchanges are indexed, pending, unresolved, or missing state.

Coverage is deliberately complete-only-when-everything-is-complete:

* an empty session is valid and complete;
* a canonical exchange with no durable index state is visible as unindexed;
* durable states whose status is pending, in-progress, insufficient, or
  uncertain are visible unresolved states;
* a durable state counts as complete only when its exact session, exchange,
  user/assistant turn anchors, and digest binding match canonical persistence;
* no newest-N, partial representative list, or silent truncation is produced;
  if the bounded UTF-8 envelope cannot contain every relevant row, the result
  reports incomplete rather than guessing;
* no conversation text is returned.
* freshness is trusted caller-asserted only: by default it remains unknown
  because no independently authenticated canonical-source digest exists.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from typing import Any, Callable, Optional, Sequence, Tuple

INDEX_STATUS_PENDING = "pending"
INDEX_STATUS_IN_PROGRESS = "in_progress"
INDEX_STATUS_COMPLETE = "complete"
INDEX_STATUS_INSUFFICIENT = "insufficient"
INDEX_STATUS_UNCERTAIN = "uncertain"
INDEX_STATUSES = (
    INDEX_STATUS_PENDING,
    INDEX_STATUS_IN_PROGRESS,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    INDEX_STATUS_UNCERTAIN,
)


class IndexCoverageError(ValueError):
    """Raised when coverage input validation or a minimal budget fails."""


def _session_of(db: Any) -> Any:
    if db is None or isinstance(db, str):
        raise IndexCoverageError("A database session is required")
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
    raise IndexCoverageError("A database session is required")


def _clean_exact_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise IndexCoverageError(f"Exact {field_name} is required")
    if not value or value != value.strip():
        raise IndexCoverageError(f"Exact {field_name} is required")
    if len(value) > 256:
        raise IndexCoverageError(f"{field_name} exceeds maximum length")
    return value


def _strict_nonnegative_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise IndexCoverageError(f"{field_name} must be a non-negative integer")
    if value < 0:
        raise IndexCoverageError(f"{field_name} must be non-negative")
    return int(value)


def _dt_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    if isinstance(value, str):
        return value
    raise IndexCoverageError("Persisted timestamp must be datetime or None")


def _load_persistence() -> tuple[Any, Any, Any]:
    """Lazily load canonical persistence classes (opt-in side effects)."""
    try:
        from backend.models.database import ConversationExchange, ConversationTurn
        from backend.services.grounded_discussion_index import DiscussionIndexStateRow
        return ConversationExchange, ConversationTurn, DiscussionIndexStateRow
    except Exception:
        try:
            from models.database import ConversationExchange, ConversationTurn  # type: ignore
            from services.grounded_discussion_index import DiscussionIndexStateRow  # type: ignore
            return ConversationExchange, ConversationTurn, DiscussionIndexStateRow
        except Exception as exc:
            raise IndexCoverageError("Required persistence is unavailable") from exc


def _idempotency_key(
    session_id: str, exchange_id: str, user_turn_id: int, assistant_turn_id: int,
    input_digest: str,
) -> str:
    payload = {
        "session_id": session_id,
        "exchange_id": exchange_id,
        "user_turn_id": user_turn_id,
        "assistant_turn_id": assistant_turn_id,
        "input_digest": input_digest,
    }
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "idx-" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _serialize(payload: Any) -> str:
    return json.dumps(payload, ensure_ascii=False, separators=(",", ":"), sort_keys=True)


@dataclass(frozen=True, slots=True)
class IndexCoverageEntry:
    """One canonical exchange's durable indexing coverage row."""

    session_id: str
    exchange_id: str
    created_at: Optional[str]
    structurally_eligible: bool
    state_present: bool
    state_idempotency_key: Optional[str]
    index_status: str
    user_turn_id: Optional[int]
    assistant_turn_id: Optional[int]
    input_digest: Optional[str]
    anchors_match: bool
    digest_match: bool
    is_complete: bool
    reasons: Tuple[str, ...]
    canonical_source_digest: Optional[str] = None
    canonical_source_digest_match: bool = False

    @property
    def status(self) -> str:
        return self.index_status

    @property
    def complete(self) -> bool:
        return self.is_complete

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class IndexCoverageResult:
    """Complete-or-incomplete coverage scan for one exact session."""

    session_id: str
    max_payload_bytes: int
    available_count: int
    returned_count: int
    omitted_count: int
    structurally_eligible_count: int
    indexed_count: int
    unindexed_count: int
    pending_count: int
    in_progress_count: int
    insufficient_count: int
    uncertain_count: int
    mismatched_count: int
    entries: Tuple[IndexCoverageEntry, ...]
    coverage_complete: bool
    structural_state_complete: bool = False
    source_freshness_verified: bool = False
    end_to_end_complete: bool = False
    enumerated_exchange_count: int = 0
    max_examined_exchanges: int = 0
    max_examined_state_rows: int = 0
    untrusted: bool = True
    semantic_complete: bool = False
    reason: str = "complete"
    payload_bytes: int = 0
    payload_json: str = ""

    @property
    def candidate_exchange_ids(self) -> Tuple[str, ...]:
        return tuple(entry.exchange_id for entry in self.entries)

    @property
    def discovered_exchange_ids(self) -> Tuple[str, ...]:
        return self.candidate_exchange_ids

    @property
    def unindexed_exchange_ids(self) -> Tuple[str, ...]:
        return tuple(entry.exchange_id for entry in self.entries if not entry.state_present)

    @property
    def incomplete_exchange_ids(self) -> Tuple[str, ...]:
        return tuple(entry.exchange_id for entry in self.entries if not entry.is_complete)

    @property
    def payload_byte_count(self) -> int:
        return self.payload_bytes

    @property
    def serialized_payload(self) -> str:
        return self.payload_json

    @property
    def discovery_complete(self) -> bool:
        return self.coverage_complete

    @property
    def complete(self) -> bool:
        return self.coverage_complete

    @property
    def representatives(self) -> Tuple[IndexCoverageEntry, ...]:
        return self.entries

    @property
    def exchanges(self) -> Tuple[IndexCoverageEntry, ...]:
        return self.entries

    @property
    def items(self) -> Tuple[IndexCoverageEntry, ...]:
        return self.entries

    @property
    def coverage_entries(self) -> Tuple[IndexCoverageEntry, ...]:
        return self.entries

    def to_dict(self) -> dict[str, Any]:
        data = asdict(
            replace(self, entries=())
        )
        projected_entries = [entry.to_dict() for entry in self.entries]
        data["entries"] = projected_entries
        data["exchanges"] = projected_entries
        data["representatives"] = projected_entries
        data["items"] = projected_entries
        data["candidate_exchange_ids"] = list(self.candidate_exchange_ids)
        data["discovery_complete"] = self.coverage_complete
        return data

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


def _scan(
    session: Any,
    clean_session: str,
    exchanges: Sequence[Any],
    index_state_by_exchange: dict[str, Any],
    created_lookup: dict[int, bool],
) -> Tuple[IndexCoverageEntry, ...]:
    ConversationExchange, ConversationTurn, DiscussionIndexStateRow = _load_persistence()
    try:
        from backend.services.grounded_discussion_index import canonical_exchange_input_digest
    except Exception:  # pragma: no cover - alternate invocation layout
        try:
            from services.grounded_discussion_index import canonical_exchange_input_digest  # type: ignore
        except Exception:
            canonical_exchange_input_digest = None
    entries: list[IndexCoverageEntry] = []
    for exchange in exchanges:
        exchange_id = str(exchange.id)
        reasons: list[str] = []
        structurally_eligible = True
        state = index_state_by_exchange.get(exchange_id)
        state_present = state is not None
        state_key: Optional[str] = None
        status: str = "unindexed"
        user_turn_id: Optional[int] = None
        assistant_turn_id: Optional[int] = None
        input_digest: Optional[str] = None

        anchors_match = True
        digest_match = True

        if state is None:
            status = "unindexed"
            reasons.append("no_index_state")
            anchors_match = False
            digest_match = False
        else:
            state_key = str(getattr(state, "idempotency_key", "") or "")
            status = str(getattr(state, "status", "") or "")
            raw_ut = getattr(state, "user_turn_id", None)
            raw_at = getattr(state, "assistant_turn_id", None)
            if isinstance(raw_ut, bool) or not isinstance(raw_ut, int) or raw_ut < 1:
                user_turn_id = None
                anchors_match = False
                reasons.append("invalid_user_turn_anchor")
            else:
                user_turn_id = int(raw_ut)
            if isinstance(raw_at, bool) or not isinstance(raw_at, int) or raw_at < 1:
                assistant_turn_id = None
                anchors_match = False
                reasons.append("invalid_assistant_turn_anchor")
            else:
                assistant_turn_id = int(raw_at)

            raw_digest = getattr(state, "input_digest", None)
            if isinstance(raw_digest, str) and raw_digest.strip():
                input_digest = raw_digest.strip()
            else:
                input_digest = None
                anchors_match = False
                digest_match = False
                reasons.append("missing_input_digest")

            # Session/exchange canonical scope.
            if str(getattr(state, "session_id", "")) != clean_session:
                anchors_match = False
                reasons.append("state_session_scope_mismatch")
            if str(getattr(state, "exchange_id", "")) != exchange_id:
                anchors_match = False
                reasons.append("state_exchange_scope_mismatch")

            # Canonical anchor existence.
            if user_turn_id is not None:
                exists = (
                    session.query(ConversationTurn)
                    .filter_by(id=user_turn_id, session_id=clean_session, role="user")
                    .first()
                    is not None
                )
                if not exists:
                    anchors_match = False
                    reasons.append("user_turn_anchor_missing")
            if assistant_turn_id is not None:
                exists = (
                    session.query(ConversationTurn)
                    .filter_by(id=assistant_turn_id, session_id=clean_session, role="assistant")
                    .first()
                    is not None
                )
                if not exists:
                    anchors_match = False
                    reasons.append("assistant_turn_anchor_missing")

            # Idempotency digest binding.
            if input_digest is not None and user_turn_id is not None and assistant_turn_id is not None and anchors_match:
                expected_key = _idempotency_key(
                    clean_session, exchange_id, user_turn_id, assistant_turn_id, input_digest
                )
                if expected_key != state_key:
                    digest_match = False
                    reasons.append("idempotency_digest_binding_mismatch")

            if status not in INDEX_STATUSES:
                reasons.append("unknown_index_status")
                if status != INDEX_STATUS_COMPLETE:
                    anchors_match = False

            if status != INDEX_STATUS_COMPLETE:
                # Pending/in-progress/insufficient/uncertain are intentionally
                # unresolved.  None of them is retried by this read seam.
                reasons.append(f"status_{status}")
                if status == INDEX_STATUS_IN_PROGRESS:
                    reasons.append("index_in_progress")
                if status in (INDEX_STATUS_INSUFFICIENT, INDEX_STATUS_UNCERTAIN):
                    reasons.append("index_not_complete")

            # Missing anchors always prevent completion.
            if not anchors_match:
                if "anchor_mismatch" not in reasons:
                    reasons.append("anchor_mismatch")
            if not digest_match:
                if "digest_mismatch" not in reasons:
                    reasons.append("digest_mismatch")

        current_canonical_digest = None
        canonical_match = False
        if state_present and canonical_exchange_input_digest is not None and user_turn_id is not None and assistant_turn_id is not None:
            try:
                current_canonical_digest = canonical_exchange_input_digest(
                    session, clean_session, exchange_id, user_turn_id, assistant_turn_id
                )
                canonical_match = bool(
                    isinstance(input_digest, str)
                    and input_digest == current_canonical_digest
                )
            except Exception:
                current_canonical_digest = None
                canonical_match = False
        # Canonical source-text digest comparison is report-only metadata; it does
        # not alter the legacy structural ``is_complete`` contract. Overall freshness
        # remains unknown because descriptor/batch/model-input proof are separate.


        is_complete = bool(
            state_present
            and status == INDEX_STATUS_COMPLETE
            and structurally_eligible
            and anchors_match
            and digest_match
            and reasons == []
        )
        # reasons above already included status for non-complete; ensure complete rows
        # with no problems have no stale reason.
        if is_complete:
            reasons = []

        entries.append(
            IndexCoverageEntry(
                session_id=clean_session,
                exchange_id=exchange_id,
                created_at=_dt_text(getattr(exchange, "created_at", None)),
                structurally_eligible=structurally_eligible,
                state_present=state_present,
                state_idempotency_key=state_key,
                index_status=status,
                user_turn_id=user_turn_id,
                assistant_turn_id=assistant_turn_id,
                input_digest=input_digest,
                anchors_match=anchors_match,
                digest_match=digest_match,
                is_complete=is_complete,
                reasons=tuple(sorted(set(reasons))),
                canonical_source_digest=current_canonical_digest,
                canonical_source_digest_match=canonical_match,
            )
        )
    return tuple(entries)


def read_session_index_coverage(
    db: Any,
    session_id: Any,
    *,
    max_payload_bytes: Any,
    max_examined_exchanges: Any = 1000,
    source_freshness_verifier: Any = None,
    **legacy_options: Any,
) -> IndexCoverageResult:
    """Read one session's canonical-exchange indexing coverage.

    ``max_payload_bytes`` is keyword-only and counts bytes of the serialized
    UTF-8 coverage envelope in ``payload_json``.

    ``max_examined_exchanges`` bounds database enumeration work, independent
    of the byte envelope.  Aliases ``max_exchange_count``, ``max_rows``,
    ``max_examined_rows``, ``max_canonical_exchanges`` and
    ``enumeration_limit`` are accepted for caller compatibility.
    """

    alias_values = []
    for key in (
        "max_examined_exchanges", "max_exchange_count", "max_rows",
        "max_examined_rows", "max_canonical_exchanges", "enumeration_limit",
    ):
        if key in legacy_options:
            alias_values.append(legacy_options.pop(key))
    if legacy_options:
        raise IndexCoverageError(
            "Unknown coverage option(s): " + ", ".join(sorted(legacy_options))
        )
    if alias_values:
        if any(value != alias_values[0] for value in alias_values):
            raise IndexCoverageError("Conflicting enumeration bound options")
        max_examined_exchanges = alias_values[0]
    if max_payload_bytes is None:
        raise IndexCoverageError("max_payload_bytes is required")
    if max_examined_exchanges is None:
        raise IndexCoverageError("max_examined_exchanges is required")
    clean_session = _clean_exact_id(session_id, "session identity")
    max_bytes = _strict_nonnegative_int(max_payload_bytes, "max_payload_bytes")
    max_examined = _strict_nonnegative_int(max_examined_exchanges, "max_examined_exchanges")
    session = _session_of(db)

    with session.no_autoflush:
        ConversationExchange, ConversationTurn, DiscussionIndexStateRow = _load_persistence()

        # Bound database enumeration work explicitly.  The byte envelope only
        # bounds serialized output, not SQL reads.
        try:
            rows = list(
                session.query(ConversationExchange)
                .filter_by(session_id=clean_session)
                .order_by(ConversationExchange.created_at.asc(), ConversationExchange.id.asc())
                .limit(max_examined + 1)
                .all()
            )
        except Exception as exc:
            return _incomplete_result(
                clean_session, max_bytes, 0, "enumeration_incomplete",
                error=str(exc.__class__.__name__),
                enumerated_exchange_count=0,
                max_examined_exchanges=max_examined,
                max_examined_state_rows=max_examined,
            )
        if len(rows) > max_examined:
            return _incomplete_result(
                clean_session, max_bytes, len(rows), "enumeration_limit_exceeded",
                enumerated_exchange_count=len(rows),
                max_examined_exchanges=max_examined,
                max_examined_state_rows=max_examined,
            )

        available_count = len(rows)
        try:
            state_rows = list(
                session.query(DiscussionIndexStateRow)
                .filter_by(session_id=clean_session)
                .limit(max_examined + 1)
                .all()
            )
        except Exception as exc:
            return _incomplete_result(
                clean_session, max_bytes, available_count, "enumeration_incomplete",
                error=str(exc.__class__.__name__),
                enumerated_exchange_count=available_count,
                max_examined_exchanges=max_examined,
                max_examined_state_rows=max_examined,
            )
        if len(state_rows) > max_examined:
            return _incomplete_result(
                clean_session, max_bytes, available_count, "enumeration_limit_exceeded",
                enumerated_exchange_count=available_count,
                max_examined_exchanges=max_examined,
                max_examined_state_rows=max_examined,
            )
        index_state_by_exchange: dict[str, Any] = {}
        for state in state_rows:
            key = str(getattr(state, "exchange_id", "") or "")
            # Duplicate rows for the same exchange are not collapsed; but for coverage
            # the first exact-session-scoped row is used.  Multiple rows make the
            # session unresolvable and therefore not complete.
            if key not in index_state_by_exchange:
                index_state_by_exchange[key] = state
            else:
                index_state_by_exchange[key] = state  # last wins is not ideal; handled below

        # If multiple durable states exist for one exchange, coverage is incomplete.
        duplicate_states: set[str] = set()
        seen_state_exchanges: set[str] = set()
        for state in state_rows:
            eid = str(getattr(state, "exchange_id", "") or "")
            if eid in seen_state_exchanges:
                duplicate_states.add(eid)
            seen_state_exchanges.add(eid)

        try:
            entries = _scan(session, clean_session, rows, index_state_by_exchange, {})
        except Exception as exc:
            return _incomplete_result(
                clean_session, max_bytes, available_count, "enumeration_incomplete",
                error=str(exc.__class__.__name__),
            )

        # Mark exchanges with duplicate durable states as incomplete.
        entries_list = list(entries)
        if duplicate_states:
            fixed_entries = []
            for entry in entries_list:
                if entry.exchange_id in duplicate_states:
                    fixed_entries.append(
                        replace_entry_reasons(entry, ("duplicate_index_state",))
                    )
                else:
                    fixed_entries.append(entry)
            entries = tuple(fixed_entries)
            entries_list = list(entries)

        indexed_count = sum(1 for e in entries if e.is_complete)
        unindexed_count = sum(1 for e in entries if not e.state_present)
        pending_count = sum(1 for e in entries if e.index_status == INDEX_STATUS_PENDING)
        in_progress_count = sum(1 for e in entries if e.index_status == INDEX_STATUS_IN_PROGRESS)
        insufficient_count = sum(1 for e in entries if e.index_status == INDEX_STATUS_INSUFFICIENT)
        uncertain_count = sum(1 for e in entries if e.index_status == INDEX_STATUS_UNCERTAIN)
        mismatched_count = sum(
            1 for e in entries if e.state_present and (not e.anchors_match or not e.digest_match)
        )
        structurally_eligible_count = sum(1 for e in entries if e.structurally_eligible)

        # Determine coverage completeness before byte bounding.
        base_complete = (
            available_count == len(entries)
            and unindexed_count == 0
            and (available_count - indexed_count) == 0
            and mismatched_count == 0
            and duplicate_states == set()
            and pending_count == 0
            and in_progress_count == 0
            and insufficient_count == 0
            and uncertain_count == 0
        )
        if available_count == 0:
            base_complete = True

        # The durable index digest is self-consistent only.  There is no
        # authoritative versioned canonical-source digest in the current schema,
        # so stronger end-to-end freshness is fail-closed unless an explicit
        # verifier is supplied by a caller that can prove it.
        structural_state_complete = bool(base_complete)
        coverage_complete = bool(base_complete)
        source_freshness_verified = False
        verifier = source_freshness_verifier
        if verifier is None:
            verifier = legacy_options.pop("source_freshness_verifier", None)
        if verifier is None:
            verifier = legacy_options.pop("digest_verifier", None)
        if legacy_options:
            raise IndexCoverageError(
                "Unknown coverage option(s): " + ", ".join(sorted(legacy_options))
            )
        if callable(verifier):
            # The verifier is caller-asserted freshness evidence.  This service
            # has no independently authenticated canonical-source digest, so an
            # untrusted or failed verifier remains unknown rather than verified.
            # A synchronous verifier must return exactly True.  Awaitables are
            # closed to avoid an unawaited-coroutine warning and are rejected; we
            # never await them.  Ordinary exceptions fail closed, while control-
            # flow BaseExceptions such as asyncio.CancelledError propagate.
            try:
                import inspect

                raw_result = verifier(session, clean_session, entries, state_rows)
                if inspect.isawaitable(raw_result):
                    close = getattr(raw_result, "close", None)
                    if callable(close):
                        try:
                            close()
                        except RuntimeError:
                            pass
                    source_freshness_verified = False
                else:
                    source_freshness_verified = raw_result is True
            except Exception:
                source_freshness_verified = False
        end_to_end_complete = bool(base_complete and source_freshness_verified)

        # Build full payload.
        payload_entries = [entry.to_dict() for entry in entries]
        omitted = 0
        reason = "complete" if base_complete else (
            "unindexed_or_unresolved_exchange"
            if any(not e.is_complete for e in entries)
            else "incomplete_index_coverage"
        )
        candidate_ids = tuple(entry.exchange_id for entry in entries)

        def _build_full(entries_payload, candidate_tuple, omitted_count, complete_flag,
                        reason_value):
            payload = {
                "session_id": clean_session,
                "available_count": available_count,
                "returned_count": len(entries_payload),
                "omitted_count": omitted_count,
                "structurally_eligible_count": structurally_eligible_count,
                "indexed_count": indexed_count,
                "unindexed_count": unindexed_count,
                "pending_count": pending_count,
                "in_progress_count": in_progress_count,
                "insufficient_count": insufficient_count,
                "uncertain_count": uncertain_count,
                "mismatched_count": mismatched_count,
                "coverage_complete": complete_flag,
                "reason": reason_value,
                "candidate_exchange_ids": list(candidate_tuple),
                "entries": entries_payload,
            }
            json_text = _serialize(payload)
            return json_text

        full_json = _build_full(
            payload_entries, candidate_ids, omitted, base_complete and True, reason,
        )
        byte_count = len(full_json.encode("utf-8"))
        if byte_count <= max_bytes:
            coverage_complete = base_complete
            final_reason = reason if base_complete is False else "complete"
            if available_count == 0:
                coverage_complete = True
                final_reason = "complete"
            final_payload = {
                "session_id": clean_session,
                "available_count": available_count,
                "returned_count": len(entries),
                "omitted_count": omitted,
                "structurally_eligible_count": structurally_eligible_count,
                "indexed_count": indexed_count,
                "unindexed_count": unindexed_count,
                "pending_count": pending_count,
                "in_progress_count": in_progress_count,
                "insufficient_count": insufficient_count,
                "uncertain_count": uncertain_count,
                "mismatched_count": mismatched_count,
                "coverage_complete": coverage_complete,
                "reason": final_reason,
                "candidate_exchange_ids": list(candidate_ids),
                "entries": payload_entries,
            }
            final_json = _serialize(final_payload)
            return IndexCoverageResult(
                session_id=clean_session,
                max_payload_bytes=max_bytes,
                available_count=available_count,
                returned_count=len(entries),
                omitted_count=omitted,
                structurally_eligible_count=structurally_eligible_count,
                indexed_count=indexed_count,
                unindexed_count=unindexed_count,
                pending_count=pending_count,
                in_progress_count=in_progress_count,
                insufficient_count=insufficient_count,
                uncertain_count=uncertain_count,
                mismatched_count=mismatched_count,
                entries=entries,
                coverage_complete=coverage_complete,
                structural_state_complete=structural_state_complete,
                source_freshness_verified=source_freshness_verified,
                end_to_end_complete=end_to_end_complete,
                enumerated_exchange_count=available_count,
                max_examined_exchanges=max_examined,
                max_examined_state_rows=max_examined,
                untrusted=True,
                semantic_complete=False,
                reason=final_reason,
                payload_bytes=len(final_json.encode("utf-8")),
                payload_json=final_json,
            )

        # Overflow: no partial representative list.
        minimal_payload = {
            "session_id": clean_session,
            "available_count": available_count,
            "returned_count": 0,
            "omitted_count": available_count,
            "structurally_eligible_count": structurally_eligible_count,
            "indexed_count": indexed_count,
            "unindexed_count": unindexed_count,
            "pending_count": pending_count,
            "in_progress_count": in_progress_count,
            "insufficient_count": insufficient_count,
            "uncertain_count": uncertain_count,
            "mismatched_count": mismatched_count,
            "coverage_complete": False,
            "reason": "payload_budget_exceeded",
            "candidate_exchange_ids": [],
            "entries": [],
        }
        minimal_json = _serialize(minimal_payload)
        minimal_bytes = len(minimal_json.encode("utf-8"))
        if minimal_bytes > max_bytes:
            raise IndexCoverageError(
                "max_payload_bytes is too small for the minimal coverage payload "
                f"({minimal_bytes} bytes required)"
            )
        return IndexCoverageResult(
            session_id=clean_session,
            max_payload_bytes=max_bytes,
            available_count=available_count,
            returned_count=0,
            omitted_count=available_count,
            structurally_eligible_count=structurally_eligible_count,
            indexed_count=indexed_count,
            unindexed_count=unindexed_count,
            pending_count=pending_count,
            in_progress_count=in_progress_count,
            insufficient_count=insufficient_count,
            uncertain_count=uncertain_count,
            mismatched_count=mismatched_count,
            entries=(),
            coverage_complete=False,
            structural_state_complete=False,
            source_freshness_verified=False,
            end_to_end_complete=False,
            enumerated_exchange_count=available_count,
            max_examined_exchanges=max_examined,
            max_examined_state_rows=max_examined,
            untrusted=True,
            semantic_complete=False,
            reason="payload_budget_exceeded",
            payload_bytes=minimal_bytes,
            payload_json=minimal_json,
        )


def _incomplete_result(
    session_id: str, max_bytes: int, available_count: int, reason: str, *,
    error: Optional[str] = None,
    enumerated_exchange_count: int = 0,
    max_examined_exchanges: int = 0,
    max_examined_state_rows: int = 0,
) -> IndexCoverageResult:
    payload = {
        "session_id": session_id,
        "available_count": available_count,
        "returned_count": 0,
        "omitted_count": available_count,
        "structurally_eligible_count": 0,
        "indexed_count": 0,
        "unindexed_count": 0,
        "pending_count": 0,
        "in_progress_count": 0,
        "insufficient_count": 0,
        "uncertain_count": 0,
        "mismatched_count": 0,
        "coverage_complete": False,
        "structural_state_complete": False,
        "source_freshness_verified": False,
        "end_to_end_complete": False,
        "enumerated_exchange_count": enumerated_exchange_count,
        "max_examined_exchanges": max_examined_exchanges,
        "max_examined_state_rows": max_examined_state_rows,
        "reason": reason,
        "candidate_exchange_ids": [],
        "entries": [],
    }
    if error:
        payload["enumeration_error"] = error
    json_text = _serialize(payload)
    return IndexCoverageResult(
        session_id=session_id,
        max_payload_bytes=max_bytes,
        available_count=available_count,
        returned_count=0,
        omitted_count=available_count,
        structurally_eligible_count=0,
        indexed_count=0,
        unindexed_count=0,
        pending_count=0,
        in_progress_count=0,
        insufficient_count=0,
        uncertain_count=0,
        mismatched_count=0,
        entries=(),
        coverage_complete=False,
        structural_state_complete=False,
        source_freshness_verified=False,
        end_to_end_complete=False,
        enumerated_exchange_count=enumerated_exchange_count,
        max_examined_exchanges=max_examined_exchanges,
        max_examined_state_rows=max_examined_state_rows,
        untrusted=True,
        semantic_complete=False,
        reason=reason,
        payload_bytes=len(json_text.encode("utf-8")),
        payload_json=json_text,
    )


def replace_entry_reasons(entry: IndexCoverageEntry, reasons: Sequence[str]) -> IndexCoverageEntry:
    return IndexCoverageEntry(
        session_id=entry.session_id,
        exchange_id=entry.exchange_id,
        created_at=entry.created_at,
        structurally_eligible=entry.structurally_eligible,
        state_present=entry.state_present,
        state_idempotency_key=entry.state_idempotency_key,
        index_status=entry.index_status,
        user_turn_id=entry.user_turn_id,
        assistant_turn_id=entry.assistant_turn_id,
        input_digest=entry.input_digest,
        anchors_match=False,
        digest_match=False,
        is_complete=False,
        reasons=tuple(sorted(set(reasons))),
    )


# Compatibility function and error aliases expected by callers/tests following the
# discovery seam naming pattern.
read_session_discussion_index_coverage = read_session_index_coverage
read_session_exchange_index_coverage = read_session_index_coverage
read_index_coverage = read_session_index_coverage
scan_index_coverage = read_session_index_coverage


# Result/entry/error aliases for naming variants.
DiscussionIndexCoverageError = IndexCoverageError
DiscussionIndexCoverageResult = IndexCoverageResult
IndexCoverageScanResult = IndexCoverageResult
DiscussionIndexCoverageEntry = IndexCoverageEntry
IndexExchangeCoverage = IndexCoverageEntry
ExchangeIndexCoverage = IndexCoverageEntry
IndexedExchangeCoverage = IndexCoverageEntry
CoverageResult = IndexCoverageResult
IndexingCoverageError = IndexCoverageError


class IndexCoverageManager:
    """Thin transactional read wrapper around the coverage seam."""

    def __init__(self, session: Any = None, *, db: Any = None):
        self._session = _session_of(db if db is not None else session)

    @property
    def db(self) -> Any:
        return self._session

    def scan(self, session_id: Any, *, max_payload_bytes: Any) -> IndexCoverageResult:
        return read_session_index_coverage(self._session, session_id, max_payload_bytes=max_payload_bytes)

    def read(self, session_id: Any, *, max_payload_bytes: Any) -> IndexCoverageResult:
        return self.scan(session_id, max_payload_bytes=max_payload_bytes)

    def read_session_index_coverage(self, session_id: Any, *, max_payload_bytes: Any) -> IndexCoverageResult:
        return self.scan(session_id, max_payload_bytes=max_payload_bytes)


# Keep no runtime registration or activation at import time.
__all__ = [
    "IndexCoverageError",
    "IndexCoverageEntry",
    "IndexCoverageResult",
    "IndexCoverageManager",
    "INDEX_STATUS_PENDING",
    "INDEX_STATUS_IN_PROGRESS",
    "INDEX_STATUS_COMPLETE",
    "INDEX_STATUS_INSUFFICIENT",
    "INDEX_STATUS_UNCERTAIN",
    "read_session_index_coverage",
    "read_session_discussion_index_coverage",
    "read_session_exchange_index_coverage",
    "read_index_coverage",
    "scan_index_coverage",
    # Compatibility aliases
    "DiscussionIndexCoverageError",
    "DiscussionIndexCoverageResult",
    "IndexCoverageScanResult",
    "DiscussionIndexCoverageEntry",
    "IndexExchangeCoverage",
    "ExchangeIndexCoverage",
    "IndexedExchangeCoverage",
    "CoverageResult",
    "IndexingCoverageError",
]
