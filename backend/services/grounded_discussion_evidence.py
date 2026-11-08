"""Opt-in exact selected-discussion evidence read/projection adapter.

This module does not belong to any runtime path: importing it does not register
it with routes, engines, or bootstrap.  It reads grounded discussion rows that
already exist in the caller's database and re-reads canonical exchange/turn/task
state by exact same-session joins, returning an inert, model-ready, explicitly
*untrusted* evidence projection.  No model/provider calls, commands, mutations,
flushes, commits, rollbacks, dispatch, or authorization are performed.

Selected discussion IDs are backend-authorized input.  This module never infers a
session, label, topic, or link, never merges labels, and never rewrites an older
recorded value into a corrected conclusion.  Append-only correction/conflict/
link-repair rows are faithfully represented as fallible evidence only.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import asdict, dataclass, field, replace
from datetime import datetime
from collections.abc import Mapping
from types import MappingProxyType
from typing import Any, Iterable, Optional, Sequence, Tuple

_MAX_TEXT = 2000
_PROVENANCE_VERIFIED = ("human_stated", "explicit_validated_proposal")


# ---------------------------------------------------------------------------
# Compatibility aliases and import helpers.
# ---------------------------------------------------------------------------
try:
    from backend.models.database import (
        Task as _Task,
        ConversationExchange as _ConversationExchange,
        ConversationTurn as _ConversationTurn,
        ConversationTaskAssociation as _ConversationTaskAssociation,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from models.database import (
            Task as _Task,
            ConversationExchange as _ConversationExchange,
            ConversationTurn as _ConversationTurn,
            ConversationTaskAssociation as _ConversationTaskAssociation,
        )
    except Exception:  # pragma: no cover
        _Task = None
        _ConversationExchange = None
        _ConversationTurn = None
        _ConversationTaskAssociation = None

try:
    from backend.services.grounded_discussions import (
        DiscussionRow as _DiscussionRow,
        DiscussionExchangeLinkRow as _LinkRow,
        DiscussionEventRow as _EventRow,
    )
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from services.grounded_discussions import (
            DiscussionRow as _DiscussionRow,
            DiscussionExchangeLinkRow as _LinkRow,
            DiscussionEventRow as _EventRow,
        )
    except Exception:  # pragma: no cover
        _DiscussionRow = None
        _LinkRow = None
        _EventRow = None


# ---------------------------------------------------------------------------
# Compatibility error / result names.
# ---------------------------------------------------------------------------
class SelectedDiscussionEvidenceError(ValueError):
    """Raised when exact selected discussion evidence cannot be read."""




# ---------------------------------------------------------------------------
# Normalizers (strict, bool-excluding).
# ---------------------------------------------------------------------------
def _session_of(db: Any) -> Any:
    """Resolve the session wrapper by fixed priority; fail closed without unification.

    Priority (first match wins): ``db.db`` session-like (``execute`` +
    ``flush``), then ``db.session``, then ``db.runner.db`` with ``execute``,
    then ``db`` itself when session-like (``execute`` + ``flush``). The shapes
    are kept separate compatibility checks, not a unified session factory.
    ``None``/string input and any candidate lacking the checked session-like
    surface raise ``ValueError`` without coercion, creation, or mutation.
    """
    if db is None:
        raise ValueError("A database session is required")
    if isinstance(db, str):
        raise ValueError("A database session is required")
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
    raise ValueError("A database session is required")


def _clean_exact_id(value: Any, field_name: str) -> str:
    if isinstance(value, bool) or not isinstance(value, str):
        raise SelectedDiscussionEvidenceError(f"Exact {field_name} is required")
    if not value or value != value.strip():
        raise SelectedDiscussionEvidenceError(f"Exact {field_name} is required")
    if len(value) > 256:
        raise SelectedDiscussionEvidenceError(f"{field_name} exceeds maximum length")
    return value


def _strict_bool_excluding_int(value: Any, field_name: str) -> Optional[int]:
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int):
        raise SelectedDiscussionEvidenceError(f"{field_name} must be an integer or null")
    if value < 0:
        raise SelectedDiscussionEvidenceError(f"{field_name} must be non-negative")
    return int(value)


def _as_int(value: Any, field_name: str) -> int:
    if isinstance(value, bool) or not isinstance(value, int):
        raise SelectedDiscussionEvidenceError(f"Exact {field_name} identity is required")
    if value < 1:
        raise SelectedDiscussionEvidenceError(f"Exact {field_name} identity is required")
    return int(value)


def _normalize_discussion_ids(value: Any) -> Tuple[str, ...]:
    if isinstance(value, (str, bytes, bytearray)):
        # A single exact ID may be supplied as convenience, but it is still
        # validated as one exact ID.  No splitting or coercion is performed.
        if isinstance(value, str):
            return (_clean_exact_id(value, "discussion identity"),)
        raise SelectedDiscussionEvidenceError("Discussion identifiers must be strings")
    if value is None:
        raise SelectedDiscussionEvidenceError("Discussion identifiers are required")
    if isinstance(value, (dict, set, frozenset)):
        if isinstance(value, (set, frozenset)):
            items = list(value)
        else:
            raise SelectedDiscussionEvidenceError("Discussion identifiers must be a sequence of strings")
    else:
        try:
            items = list(value)
        except TypeError as exc:
            raise SelectedDiscussionEvidenceError("Discussion identifiers must be a sequence of strings") from exc
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        clean = _clean_exact_id(item, "discussion identity")
        if clean in seen:
            raise SelectedDiscussionEvidenceError("Discussion identifiers must be unique")
        seen.add(clean)
        result.append(clean)
    if not result:
        raise SelectedDiscussionEvidenceError("Discussion identifiers are required")
    return tuple(result)


def _cap_text(value: Any) -> Tuple[Any, bool]:
    if not isinstance(value, str):
        return value, False
    if len(value) <= _MAX_TEXT:
        return value, False
    return value[:_MAX_TEXT], True


def _dt_text(value: Any) -> Optional[str]:
    if value is None:
        return None
    try:
        return value.isoformat() if hasattr(value, "isoformat") else str(value)
    except Exception:
        return str(value)


def _related_task_fields(task_row: Any) -> dict[str, Any]:
    if task_row is None:
        return {
            "task_id": None,
            "task_state": None,
            "task_version": None,
            "task_original_request": None,
            "task_normalized_goal": None,
            "task_final_result": None,
        }
    return {
        "task_id": getattr(task_row, "id", None),
        "task_state": getattr(task_row, "state", None),
        "task_version": getattr(task_row, "version", None),
        "task_original_request": getattr(task_row, "original_request", None),
        "task_normalized_goal": getattr(task_row, "normalized_goal", None),
        "task_final_result": getattr(task_row, "final_result", None),
    }


def _deep_freeze(value: Any) -> Any:
    """Return a recursively immutable copy for inert model evidence.

    Source objects are never mutated. Mutable containers are copied and sealed.
    """
    if isinstance(value, (str, bytes, int, float, bool, type(None))):
        return value
    if isinstance(value, datetime):
        return value
    if isinstance(value, Mapping):
        return MappingProxyType({key: _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple, set, frozenset)):
        return tuple(_deep_freeze(item) for item in value)
    return value


def _deep_thaw(value: Any) -> Any:
    """Return a fresh mutable copy when serializing frozen evidence."""
    if isinstance(value, MappingProxyType):
        return {key: _deep_thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_deep_thaw(item) for item in value]
    return value


# ---------------------------------------------------------------------------
# Evidence data transfer objects (inert, frozen).
# ---------------------------------------------------------------------------

@dataclass(frozen=True, slots=True)
class SourceExchangeEvidence:
    """One canonical source exchange linked to a selected discussion.

    ``user_text``/``assistant_text`` are the bounded model projections.  The
    full canonical DB values are retained under ``canonical_*_text``.
    """
    discussion_id: str
    session_id: str
    exchange_id: str
    user_turn_id: int
    assistant_turn_id: int
    link_provenance: str
    link_created_at: Optional[str]
    source_available: bool
    # Canonical, unmodified database text.
    canonical_user_text: Optional[str]
    canonical_assistant_text: Optional[str]
    # Bounded projection for model input.
    user_text: Optional[str]
    assistant_text: Optional[str]
    user_text_truncated: bool
    assistant_text_truncated: bool
    # Task state freshly read from the canonical Task row when an exact same-session
    # association with matching turn identities exists.
    task_id: Optional[str]
    task_state: Optional[str]
    task_version: Optional[int]
    task_original_request: Optional[str]
    task_normalized_goal: Optional[str]
    task_final_result: Optional[str]
    exact_task_association: bool

    # Alias compatibility.
    @property
    def user_text_model(self) -> Optional[str]:
        return self.user_text

    @property
    def assistant_text_model(self) -> Optional[str]:
        return self.assistant_text

    @property
    def user_text_projection(self) -> Optional[str]:
        return self.user_text

    @property
    def assistant_text_projection(self) -> Optional[str]:
        return self.assistant_text

    @property
    def source_user_text(self) -> Optional[str]:
        return self.canonical_user_text

    @property
    def source_assistant_text(self) -> Optional[str]:
        return self.canonical_assistant_text

    @property
    def full_user_text(self) -> Optional[str]:
        return self.canonical_user_text

    @property
    def full_assistant_text(self) -> Optional[str]:
        return self.canonical_assistant_text

    @property
    def source_complete(self) -> bool:
        return self.source_available

    @property
    def user_text_was_truncated(self) -> bool:
        return self.user_text_truncated

    @property
    def assistant_text_was_truncated(self) -> bool:
        return self.assistant_text_truncated

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class DiscussionHistoryEventEvidence:
    """One raw append-only correction/conflict/link-repair/member-linked event.

    This is history, not an effective projection or corrected conclusion.
    """
    event_id: int
    discussion_id: str
    session_id: str
    event_type: str
    target_exchange_id: Optional[str]
    target_user_turn_id: Optional[int]
    target_assistant_turn_id: Optional[int]
    prior_event_id: Optional[int]
    # Full canonical payload text.
    payload_text: Optional[str]
    # Bounded projection.
    payload_text_model: Optional[str]
    payload_text_truncated: bool
    provenance: str
    created_at: Optional[str]
    target_source_complete: bool
    is_correction: bool
    is_conflict: bool
    is_link_repair: bool
    is_member_linked: bool

    @property
    def payload_text_projection(self) -> Optional[str]:
        return self.payload_text_model

    @property
    def raw_payload_text(self) -> Optional[str]:
        return self.payload_text

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class EvidenceCoverage:
    member_available_count: int
    member_returned_count: int
    member_omitted_count: int
    event_available_count: int
    event_returned_count: int
    event_omitted_count: int
    link_count: int
    unresolved_member_count: int
    member_source_complete: bool
    event_unresolved_subject_count: int
    event_source_complete: bool

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


@dataclass(frozen=True, slots=True)
class SelectedDiscussionEvidenceEntry:
    """Exact, untrusted evidence for one selected grounded discussion."""
    discussion_id: str
    session_id: str
    label: str
    label_provenance: str
    label_unverified: bool
    task_id: Optional[str]
    created_at: Optional[str]
    updated_at: Optional[str]
    member_count_available: int
    members: Tuple[SourceExchangeEvidence, ...]
    member_coverage: EvidenceCoverage
    events: Tuple[DiscussionHistoryEventEvidence, ...]
    event_coverage: EvidenceCoverage
    discussion_task_fields: Optional[MappingProxyType]
    # Raw provenance summary.
    provenance_values: Tuple[str, ...]
    has_unverified_or_legacy_provenance: bool
    has_role_text_truncation: bool
    is_structurally_complete: bool
    semantic_complete: bool
    insufficiency_reasons: Tuple[str, ...]
    # Opt-in dual-source readback for correction/conflict/repair events only.  Absent
    # unless a caller supplies a source-pair reader; this keeps the normal adapter
    # schema inert and legacy callers unchanged.
    source_pair_readback: bool = False
    source_pairs: Tuple[Any, ...] = field(default_factory=tuple)
    source_pair_event_count: int = 0
    source_pair_count: int = 0
    source_pair_available_count: int = 0
    source_pair_structural_complete_count: int = 0
    source_pair_unverified_count: int = 0
    source_pair_text_truncated_count: int = 0
    source_pair_complete: bool = True
    source_pair_declaration_invalid: bool = False

    # Alias compatibility.
    @property
    def discussion(self) -> "SelectedDiscussionEvidenceEntry":
        return self

    @property
    def entry(self) -> "SelectedDiscussionEvidenceEntry":
        return self

    @property
    def coverage(self) -> EvidenceCoverage:
        # Backward-compatible combined coverage.
        return self.member_coverage

    @property
    def links(self) -> Tuple[SourceExchangeEvidence, ...]:
        return self.members

    @property
    def member_links(self) -> Tuple[SourceExchangeEvidence, ...]:
        return self.members

    @property
    def source_members(self) -> Tuple[SourceExchangeEvidence, ...]:
        return self.members

    @property
    def evidence_members(self) -> Tuple[SourceExchangeEvidence, ...]:
        return self.members

    @property
    def exchanges(self) -> Tuple[SourceExchangeEvidence, ...]:
        return self.members

    @property
    def history_events(self) -> Tuple[DiscussionHistoryEventEvidence, ...]:
        return self.events

    @property
    def correction_events(self) -> Tuple[DiscussionHistoryEventEvidence, ...]:
        return self.events

    @property
    def member_source_complete(self) -> bool:
        return self.member_coverage.member_source_complete

    @property
    def event_source_complete(self) -> bool:
        return self.event_coverage.event_source_complete

    def to_dict(self) -> dict[str, Any]:
        # Do not let dataclasses.asdict deepcopy the frozen MappingProxy task
        # bundle (or any nested frozen map).  Thaw it explicitly into fresh,
        # JSON-ready mutable containers.
        data = asdict(replace(self, discussion_task_fields=None, source_pairs=()))
        data["members"] = [m.to_dict() for m in self.members]
        data["events"] = [e.to_dict() for e in self.events]
        data["member_coverage"] = self.member_coverage.to_dict()
        data["event_coverage"] = self.event_coverage.to_dict()
        data["discussion_task_fields"] = _deep_thaw(self.discussion_task_fields)
        data["source_pairs"] = [_deep_thaw(pair) for pair in self.source_pairs]
        return data

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


@dataclass(frozen=True, slots=True)
class SelectedDiscussionEvidenceResult:
    """Exact untrusted evidence for a set of backend-authorized discussions."""
    session_id: str
    requested_discussion_ids: Tuple[str, ...]
    selected_discussion_ids: Tuple[str, ...]
    discussion_count: int
    entries: Tuple[SelectedDiscussionEvidenceEntry, ...]
    # Aggregate coverage.
    member_available_count: int
    member_returned_count: int
    member_omitted_count: int
    event_available_count: int
    event_returned_count: int
    event_omitted_count: int
    unresolved_member_count: int
    event_unresolved_subject_count: int
    member_source_complete: bool
    event_source_complete: bool
    has_role_text_truncation: bool
    has_unverified_or_legacy_provenance: bool
    is_structurally_complete: bool
    semantic_complete: bool
    # Conservative: semantic/provenance uncertainty means evidence is not permission.
    untrusted: bool
    insufficiency_reasons: Tuple[str, ...]
    insufficient: bool
    revalidation_required_before_dispatch: bool
    complete: bool
    source_pair_readback: bool = False
    source_pairs: Tuple[Any, ...] = field(default_factory=tuple)
    source_pair_event_count: int = 0
    source_pair_count: int = 0
    source_pair_available_count: int = 0
    source_pair_structural_complete_count: int = 0
    source_pair_unverified_count: int = 0
    source_pair_text_truncated_count: int = 0
    source_pair_complete: bool = True
    source_pair_declaration_invalid: bool = False

    # Alias compatibility.
    @property
    def discussions(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def items(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def evidence(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def selected_discussions(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def selected(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def discussion_evidence(self) -> Tuple[SelectedDiscussionEvidenceEntry, ...]:
        return self.entries

    @property
    def coverage(self) -> dict[str, int]:
        return {
            "member_available_count": self.member_available_count,
            "member_returned_count": self.member_returned_count,
            "member_omitted_count": self.member_omitted_count,
            "event_available_count": self.event_available_count,
            "event_returned_count": self.event_returned_count,
            "event_omitted_count": self.event_omitted_count,
            "unresolved_member_count": self.unresolved_member_count,
            "event_unresolved_subject_count": self.event_unresolved_subject_count,
            "member_source_complete": self.member_source_complete,
            "event_source_complete": self.event_source_complete,
        }

    def to_dict(self) -> dict[str, Any]:
        # Avoid asdict traversing entries frozen dataclasses/maps; serialize each
        # entry through its own safe to_dict implementation.
        data = asdict(replace(self, entries=(), source_pairs=()))
        data["entries"] = [e.to_dict() for e in self.entries]
        data["source_pairs"] = [_deep_thaw(pair) for pair in self.source_pairs]
        return data

    def __getitem__(self, key: Any) -> Any:
        return self.to_dict()[key]


# Backward/alias shim classes.


# ---------------------------------------------------------------------------
# Internal read snapshot.
# ---------------------------------------------------------------------------
def _empty_coverage() -> EvidenceCoverage:
    return EvidenceCoverage(
        member_available_count=0,
        member_returned_count=0,
        member_omitted_count=0,
        event_available_count=0,
        event_returned_count=0,
        event_omitted_count=0,
        link_count=0,
        unresolved_member_count=0,
        member_source_complete=True,
        event_unresolved_subject_count=0,
        event_source_complete=True,
    )


def _read_one_discussion(session: Any, discussion_row: Any, member_limit: Optional[int],
                         event_limit: Optional[int], *,
                         source_pair_reader: Optional[Any] = None,
                         source_pair_max_text_chars: int = _MAX_TEXT,
                         source_pair_readback: bool = False,
                         source_pair_declaration_invalid: bool = False) -> SelectedDiscussionEvidenceEntry:
    # Defensive converter from DiscussionRow (an ORM alias: grounded DiscussionRow).
    # Note: session may be used for canonical reads but all querying already eager
    # in outer snapshot.  We keep it for strict use.
    clean_session = str(discussion_row.session_id)
    clean_discussion = str(discussion_row.discussion_id)

    link_rows = list(
        session.query(_LinkRow)
        .filter_by(discussion_id=clean_discussion, session_id=clean_session)
        .order_by(_LinkRow.created_at.asc(), _LinkRow.exchange_id.asc())
        .all()
    )

    # Linked event rows.
    event_rows = list(
        session.query(_EventRow)
        .filter_by(discussion_id=clean_discussion, session_id=clean_session)
        .order_by(_EventRow.id.asc())
        .all()
    )

    link_keys = {
        (str(l.exchange_id), int(l.user_turn_id), int(l.assistant_turn_id)) for l in link_rows
    }

    #Members
    members_all: list[SourceExchangeEvidence] = []
    unresolved_member_count = 0
    provenance_values: set[str] = set()
    has_unverified = False
    has_truncation = False
    label_prov = str(discussion_row.label_provenance)
    if label_prov not in _PROVENANCE_VERIFIED:
        has_unverified = True
    provenance_values.add(label_prov)

    for link in link_rows:
        # Resolve canonical source by strict exact joins.
        exchange = session.query(_ConversationExchange).filter_by(
            id=str(link.exchange_id), session_id=clean_session).first()
        user_turn = session.query(_ConversationTurn).filter_by(
            id=int(link.user_turn_id), session_id=clean_session, role="user").first()
        assistant_turn = session.query(_ConversationTurn).filter_by(
            id=int(link.assistant_turn_id), session_id=clean_session, role="assistant").first()

        source_available = (exchange is not None and user_turn is not None and assistant_turn is not None)

        # Exact task association with refreshed task state.
        exact_task = False
        task_fields: dict[str, Any] = {
            "task_id": None,
            "task_state": None,
            "task_version": None,
            "task_original_request": None,
            "task_normalized_goal": None,
            "task_final_result": None,
        }
        if _ConversationTaskAssociation is not None and source_available:
            # Explicit canonical proof: exchange + exact turn pair + task id. The
            # ``exchange_id`` is already constrained to the same session because
            # ``source_available`` required that exact exchange. Keep the turn and
            # task identities explicit in the association lookup as well.
            association = session.query(_ConversationTaskAssociation).filter_by(
                exchange_id=str(link.exchange_id),
                user_turn_id=int(link.user_turn_id),
                assistant_turn_id=int(link.assistant_turn_id),
            ).first()
            if association is not None:
                etid = getattr(association, "task_id", None)
                if etid is not None and _Task is not None:
                    task_row = session.query(_Task).filter_by(id=str(etid)).first()
                    if task_row is not None:
                        exact_task = True
                        task_fields = _related_task_fields(task_row)
                        task_fields["task_id"] = str(etid)

        if not source_available:
            unresolved_member_count += 1

        # Canonical DB text preserved (None if source unavailable).
        if source_available:
            canonical_user = getattr(exchange, "user_text", None)
            canonical_assistant = getattr(exchange, "assistant_text", None)
        else:
            canonical_user = None
            canonical_assistant = None

        projected_user, user_trunc = _cap_text(canonical_user)
        projected_assistant, assistant_trunc = _cap_text(canonical_assistant)
        if user_trunc or assistant_trunc:
            has_truncation = True
        link_prov = str(getattr(link, "link_provenance", "") or "")
        if link_prov and link_prov not in (provenance_values):
            provenance_values.add(link_prov)
        if link_prov and link_prov not in _PROVENANCE_VERIFIED:
            has_unverified = True

        member = SourceExchangeEvidence(
            discussion_id=clean_discussion,
            session_id=clean_session,
            exchange_id=str(link.exchange_id),
            user_turn_id=int(link.user_turn_id),
            assistant_turn_id=int(link.assistant_turn_id),
            link_provenance=link_prov,
            link_created_at=_dt_text(getattr(link, "created_at", None)),
            source_available=bool(source_available),
            canonical_user_text=canonical_user,
            canonical_assistant_text=canonical_assistant,
            user_text=projected_user,
            assistant_text=projected_assistant,
            user_text_truncated=bool(user_trunc),
            assistant_text_truncated=bool(assistant_trunc),
            task_id=task_fields["task_id"],
            task_state=task_fields["task_state"],
            task_version=task_fields["task_version"],
            task_original_request=task_fields["task_original_request"],
            task_normalized_goal=task_fields["task_normalized_goal"],
            task_final_result=task_fields["task_final_result"],
            exact_task_association=bool(exact_task),
        )
        members_all.append(member)

    member_total = len(members_all)
    member_returned = list(members_all if member_limit is None else members_all[:member_limit])

    # Event unresolved subject count.
    event_unresolved = 0
    for ev in event_rows:
        if (ev.target_exchange_id is None and ev.target_user_turn_id is None
                and ev.target_assistant_turn_id is None):
            continue
        if (ev.target_exchange_id is None or ev.target_user_turn_id is None
                or ev.target_assistant_turn_id is None):
            event_unresolved += 1
            continue
        resolved = False
        key = (str(ev.target_exchange_id), int(ev.target_user_turn_id), int(ev.target_assistant_turn_id))
        if key in link_keys:
            ex = session.query(_ConversationExchange).filter_by(
                id=str(ev.target_exchange_id), session_id=clean_session).first()
            ut = session.query(_ConversationTurn).filter_by(
                id=int(ev.target_user_turn_id), session_id=clean_session, role="user").first()
            at = session.query(_ConversationTurn).filter_by(
                id=int(ev.target_assistant_turn_id), session_id=clean_session, role="assistant").first()
            if ex is not None and ut is not None and at is not None:
                resolved = True
        if not resolved:
            event_unresolved += 1

    event_values_list = []
    for ev in event_rows:
        payload = getattr(ev, "payload_text", None)
        projected_payload, payload_trunc = _cap_text(payload)
        if payload_trunc:
            has_truncation = True
        ev_prov = str(getattr(ev, "provenance", "") or "")
        provenance_values.add(ev_prov)
        if ev_prov and ev_prov not in _PROVENANCE_VERIFIED:
            has_unverified = True
        # target source complete?
        target_complete = True
        if ev.target_exchange_id is not None or ev.target_user_turn_id is not None or ev.target_assistant_turn_id is not None:
            if ev.target_exchange_id is None or ev.target_user_turn_id is None or ev.target_assistant_turn_id is None:
                target_complete = False
            else:
                if (str(ev.target_exchange_id), int(ev.target_user_turn_id), int(ev.target_assistant_turn_id)) not in link_keys:
                    target_complete = False
                else:
                    ex = session.query(_ConversationExchange).filter_by(id=str(ev.target_exchange_id), session_id=clean_session).first()
                    ut = session.query(_ConversationTurn).filter_by(id=int(ev.target_user_turn_id), session_id=clean_session, role="user").first()
                    at = session.query(_ConversationTurn).filter_by(id=int(ev.target_assistant_turn_id), session_id=clean_session, role="assistant").first()
                    if not (ex is not None and ut is not None and at is not None):
                        target_complete = False
        evt_type = str(getattr(ev, "event_type", "") or "")
        event_values_list.append(DiscussionHistoryEventEvidence(
            event_id=int(getattr(ev, "id")),
            discussion_id=clean_discussion,
            session_id=clean_session,
            event_type=evt_type,
            target_exchange_id=getattr(ev, "target_exchange_id", None),
            target_user_turn_id=getattr(ev, "target_user_turn_id", None),
            target_assistant_turn_id=getattr(ev, "target_assistant_turn_id", None),
            prior_event_id=getattr(ev, "prior_event_id", None),
            payload_text=payload,
            payload_text_model=projected_payload,
            payload_text_truncated=bool(payload_trunc),
            provenance=ev_prov,
            created_at=_dt_text(getattr(ev, "created_at", None)),
            target_source_complete=bool(target_complete),
            is_correction=(evt_type == "correction"),
            is_conflict=(evt_type == "conflict"),
            is_link_repair=(evt_type == "link_repair"),
            is_member_linked=(evt_type == "member_linked"),
        ))

    event_total = len(event_values_list)
    event_returned = list(event_values_list if event_limit is None else event_values_list[:event_limit])

    member_omitted = max(member_total - len(member_returned), 0)
    event_omitted = max(event_total - len(event_returned), 0)

    member_coverage = EvidenceCoverage(
        member_available_count=member_total,
        member_returned_count=len(member_returned),
        member_omitted_count=member_omitted,
        event_available_count=event_total,
        event_returned_count=len(event_returned),
        event_omitted_count=event_omitted,
        link_count=len(link_rows),
        unresolved_member_count=unresolved_member_count,
        member_source_complete=(unresolved_member_count == 0),
        event_unresolved_subject_count=event_unresolved,
        event_source_complete=(event_unresolved == 0),
    )
    event_coverage = EvidenceCoverage(
        member_available_count=member_total,
        member_returned_count=len(member_returned),
        member_omitted_count=member_omitted,
        event_available_count=event_total,
        event_returned_count=len(event_returned),
        event_omitted_count=event_omitted,
        link_count=len(link_rows),
        unresolved_member_count=unresolved_member_count,
        member_source_complete=(unresolved_member_count == 0),
        event_unresolved_subject_count=event_unresolved,
        event_source_complete=(event_unresolved == 0),
    )

    # Optional opt-in dual-anchor source pair readback.  Default mode stays inert and
    # does no helper import, query, or schema-dependent work.
    source_pairs: list[Any] = []
    source_pair_active = bool(source_pair_readback or source_pair_reader is not None)
    source_pair_event_count = 0
    source_pair_count = 0
    source_pair_declaration_invalid = bool(source_pair_declaration_invalid)
    if source_pair_reader is None and not source_pair_readback:
        source_pair_declaration_invalid = False
    source_pair_available_count = 0
    source_pair_structural_complete_count = 0
    source_pair_unverified_count = 0
    source_pair_text_truncated_count = 0
    source_pair_complete = True
    if source_pair_active:
        reader = source_pair_reader
        if reader is None and source_pair_readback:
            try:
                from backend.services.grounded_discussion_event_source_links import read_event_source_pair as _helper
                reader = _helper
            except Exception:
                reader = None
                source_pair_active = False
        if reader is not None:
            candidate_events = [
                ev for ev in event_returned
                if getattr(ev, "is_correction", False) or getattr(ev, "is_conflict", False)
                or getattr(ev, "is_link_repair", False)
            ]
            source_pair_event_count = len(candidate_events)
            for event in candidate_events:
                raw_pair = None
                try:
                    try:
                        raw_pair = reader(session, int(getattr(event, "event_id")), max_text_chars=source_pair_max_text_chars)
                    except TypeError:
                        try:
                            raw_pair = reader(int(getattr(event, "event_id")), max_text_chars=source_pair_max_text_chars)
                        except TypeError:
                            try:
                                raw_pair = reader(session, int(getattr(event, "event_id")))
                            except TypeError:
                                raw_pair = reader(int(getattr(event, "event_id")))
                except Exception:
                    raw_pair = None
                if isinstance(raw_pair, Mapping):
                    pair = raw_pair
                elif raw_pair is None:
                    pair = {
                        "event_id": int(getattr(event, "event_id")),
                        "available": False,
                        "structural_complete": False,
                        "source": None,
                        "target": None,
                        "provenance_verified": False,
                        "gaps": ["missing-source-pair-readback"],
                    }
                else:
                    try:
                        pair = {
                            "event_id": int(getattr(event, "event_id")),
                            "available": bool(getattr(raw_pair, "available", False)),
                            "structural_complete": bool(getattr(raw_pair, "structural_complete", False)),
                            "source": getattr(raw_pair, "source", None),
                            "target": getattr(raw_pair, "target", None),
                            "provenance_verified": bool(getattr(raw_pair, "provenance_verified", False)),
                            "gaps": tuple(getattr(raw_pair, "gaps", ())),
                        }
                    except Exception:
                        pair = {
                            "event_id": int(getattr(event, "event_id")),
                            "available": False,
                            "structural_complete": False,
                            "source": None,
                            "target": None,
                            "provenance_verified": False,
                            "gaps": ["invalid-source-pair-readback"],
                        }
                pair = _deep_freeze(pair)
                source_pairs.append(pair)
                available = bool(pair.get("available")) if isinstance(pair, Mapping) else bool(getattr(pair, "available", False))
                if available:
                    source_pair_available_count += 1
                structural = bool(pair.get("structural_complete")) if isinstance(pair, Mapping) else bool(getattr(pair, "structural_complete", False))
                if structural:
                    source_pair_structural_complete_count += 1
                else:
                    source_pair_complete = False
                provenance_verified = bool(pair.get("provenance_verified")) if isinstance(pair, Mapping) else bool(getattr(pair, "provenance_verified", False))
                if not provenance_verified:
                    source_pair_unverified_count += 1
                source_payload = pair.get("source") if isinstance(pair, Mapping) else getattr(pair, "source", None)
                target_payload = pair.get("target") if isinstance(pair, Mapping) else getattr(pair, "target", None)
                truncated = False
                if isinstance(source_payload, Mapping):
                    truncated = truncated or bool(source_payload.get("text_truncated", False))
                if isinstance(target_payload, Mapping):
                    truncated = truncated or bool(target_payload.get("text_truncated", False))
                if truncated:
                    source_pair_text_truncated_count += 1
                    source_pair_complete = False
                if source_payload is None or target_payload is None:
                    source_pair_complete = False
                if source_pair_event_count != len(source_pairs):
                    source_pair_complete = False
            if source_pair_event_count > 0 and len(source_pairs) != source_pair_event_count:
                source_pair_complete = False
            if source_pair_available_count != source_pair_event_count:
                source_pair_complete = False
            if source_pair_structural_complete_count != source_pair_event_count:
                source_pair_complete = False
            source_pair_count = len(source_pairs)
            for p in source_pairs:
                try:
                    gaps = p.get("gaps") if isinstance(p, Mapping) else getattr(p, "gaps", ())
                except Exception:
                    gaps = ()
                for gap in (gaps or ()):
                    if gap != "unverified-provenance":
                        source_pair_complete = False
        else:
            source_pair_readback = False
            source_pair_complete = False if (source_pair_reader is not None) else True
        if not source_pair_active:
            source_pair_readback = False
            if not source_pair_readback:
                source_pair_complete = True

    # Discussion-level task fields: only when at least one exact same-session association exists.
    discussion_task_fields = None
    assoc_task_ids = [m.task_id for m in members_all if m.exact_task_association and m.task_id is not None]
    if assoc_task_ids:
        # Use first exact associated task's row (already refreshed in member fields, but we read fresh).
        if _Task is not None:
            task_row = session.query(_Task).filter_by(id=assoc_task_ids[0]).first()
            tf = _related_task_fields(task_row)
            if task_row is not None:
                tf["task_id"] = assoc_task_ids[0]
            discussion_task_fields = _deep_freeze(tf)
        else:
            discussion_task_fields = None
    else:
        # Allow stored discussion.task_id only if also backed by a persisted member association; none -> None.
        discussion_task_fields = _deep_freeze(_related_task_fields(None))

    # Note: discussion_row.task_id is metadata; we intentionally do not trust it.
    # The label itself remains stored verbatim: no merging or reinterpretation.

    is_structurally_complete = (
        unresolved_member_count == 0
        and member_omitted == 0
        and event_omitted == 0
        and event_unresolved == 0
        and not has_truncation
    )

    insufficiency_reasons: list[str] = []
    if unresolved_member_count:
        insufficiency_reasons.append("unresolved_or_missing_member_source")
    if member_omitted:
        insufficiency_reasons.append("omitted_member_evidence_under_caller_bounds")
    if event_omitted:
        insufficiency_reasons.append("omitted_event_evidence_under_caller_bounds")
    if event_unresolved:
        insufficiency_reasons.append("unresolved_event_subject")
    if has_truncation:
        insufficiency_reasons.append("role_or_event_text_projection_truncated")
    if has_unverified:
        insufficiency_reasons.append("unverified_or_legacy_provenance_present")
    if assoc_task_ids:
        pass
    # Task-state proof uncertainty is not a structural row omission but is
    # important; the result never claims authority over task state.
    if not assoc_task_ids and getattr(discussion_row, "task_id", None) is not None:
        insufficiency_reasons.append("discussion_task_metadata_requires_exact_association_proof")

    entry = SelectedDiscussionEvidenceEntry(
        discussion_id=clean_discussion,
        session_id=clean_session,
        label=str(discussion_row.label),
        label_provenance=label_prov,
        label_unverified=(label_prov not in _PROVENANCE_VERIFIED),
        task_id=str(discussion_row.task_id) if getattr(discussion_row, "task_id", None) is not None else None,
        created_at=_dt_text(getattr(discussion_row, "created_at", None)),
        updated_at=_dt_text(getattr(discussion_row, "updated_at", None)),
        member_count_available=member_total,
        members=tuple(member_returned),
        member_coverage=member_coverage,
        events=tuple(event_returned),
        event_coverage=event_coverage,
        discussion_task_fields=discussion_task_fields,
        provenance_values=tuple(sorted(provenance_values)),
        has_unverified_or_legacy_provenance=has_unverified,
        has_role_text_truncation=has_truncation,
        is_structurally_complete=is_structurally_complete,
        semantic_complete=False,
        insufficiency_reasons=tuple(insufficiency_reasons),
        source_pair_readback=bool(source_pair_active),
        source_pairs=tuple(source_pairs),
        source_pair_event_count=int(source_pair_event_count),
        source_pair_count=int(source_pair_count),
        source_pair_available_count=int(source_pair_available_count),
        source_pair_structural_complete_count=int(source_pair_structural_complete_count),
        source_pair_unverified_count=int(source_pair_unverified_count),
        source_pair_text_truncated_count=int(source_pair_text_truncated_count),
        source_pair_complete=bool(source_pair_active and source_pair_complete),
        source_pair_declaration_invalid=bool(source_pair_declaration_invalid),
    )
    return entry


def read_selected_discussion_evidence(*args: Any, **kwargs: Any) -> SelectedDiscussionEvidenceResult:
    """Read exact untrusted evidence for backend-selected discussion IDs.

    Positional: (db, session_id, discussion_ids, ...).  Numerous keyword
    aliases are accepted for each parameter; bounds are optional integers and
    default to no cap.
    """
    # Extract db.
    db = kwargs.pop("db", None)
    if db is None:
        db = kwargs.pop("session", None)  # some callers name session as db
    # Do not pop 'session' if it is str ID; handle below.
    session_id = kwargs.pop("session_id", None)
    # If session was popped as db mistakenly (string), recover.
    if isinstance(db, str) and session_id is None:
        session_id = db
        db = None

    discussion_ids = None
    for alias in ("discussion_ids", "selected_discussion_ids", "selected_ids", "ids",
                  "discussion_id", "discussion", "id"):
        if alias in kwargs and discussion_ids is None:
            discussion_ids = kwargs.pop(alias)

    member_limit = kwargs.pop("member_limit", None)
    for alias in ("member_bounds", "member_count", "max_members", "member_max"):
        if alias in kwargs and member_limit is None:
            member_limit = kwargs.pop(alias)
    event_limit = kwargs.pop("event_limit", None)
    for alias in ("event_bounds", "event_count", "max_events", "event_max"):
        if alias in kwargs and event_limit is None:
            event_limit = kwargs.pop(alias)

    source_pair_reader = kwargs.pop("source_pair_reader", None)
    for alias in ("source_pair_readback_reader", "pair_reader", "dual_anchor_reader", "source_pairs_reader"):
        if alias in kwargs and source_pair_reader is None:
            source_pair_reader = kwargs.pop(alias)

    source_pair_max_text_chars = kwargs.pop("source_pair_max_text_chars", None)
    if source_pair_max_text_chars is None:
        source_pair_max_text_chars = kwargs.pop("max_text_chars", None)
    if source_pair_max_text_chars is None:
        source_pair_max_text_chars = kwargs.pop("pair_max_text_chars", None)
    if source_pair_max_text_chars is None:
        source_pair_max_text_chars = _MAX_TEXT
    else:
        source_pair_max_text_chars = _strict_bool_excluding_int(source_pair_max_text_chars, "source pair max text")
        if source_pair_max_text_chars is None:
            source_pair_max_text_chars = _MAX_TEXT

    source_pair_readback = False
    source_pair_declaration_invalid = False
    for alias in ("source_pair_readback", "include_source_pairs", "source_pairs", "enable_source_pair_readback", "pair_readback"):
        if alias in kwargs:
            raw = kwargs.pop(alias)
            if raw is True:
                source_pair_readback = True
            elif raw is False or raw is None:
                source_pair_readback = source_pair_readback  # explicit boolean contract
            else:
                # Invalid declarations do not enable readback, but they are fatal
                # structural evidence declarations at the gate level.
                source_pair_declaration_invalid = True
            if source_pair_reader is None and raw is True:
                source_pair_readback = True
    if source_pair_reader is not None:
        source_pair_readback = True

    pos = list(args)
    # db
    if db is None and pos:
        first = pos[0]
        if not isinstance(first, str) and (hasattr(first, "execute") or hasattr(first, "db") or hasattr(first, "session")):
            db = pos.pop(0)
    # session_id
    if session_id is None and pos:
        if isinstance(pos[0], str):
            session_id = pos.pop(0)
    # discussion_ids
    if discussion_ids is None and pos:
        discussion_ids = pos.pop(0)

    if session_id is None:
        session_id = kwargs.pop("session", None)
    # session may be string ID if passed this way
    if isinstance(db, str) and not isinstance(session_id, str):
        session_id = db
        db = None

    # If still no db but kwargs had 'db' already handled; try default self-less.
    session_obj = _session_of(db) if db is not None else None
    if session_obj is None:
        # Last resort: if kwargs still has a session-like object.
        for key in ("db_session", "database", "conn", "connection"):
            if key in kwargs:
                session_obj = _session_of(kwargs.pop(key))
                break
    if session_obj is None:
        raise SelectedDiscussionEvidenceError("A database session is required")

    clean_session = _clean_exact_id(session_id, "session identity")
    norm_ids = _normalize_discussion_ids(discussion_ids)
    ml = _strict_bool_excluding_int(member_limit, "member limit")
    el = _strict_bool_excluding_int(event_limit, "event limit")

    # Snapshot all reads in one no-autoflush block.
    with session_obj.no_autoflush:
        if _DiscussionRow is None:
            raise SelectedDiscussionEvidenceError("Grounded discussion model is unavailable")
        rows = list(
            session_obj.query(_DiscussionRow)
            .filter_by(session_id=clean_session)
            .filter(_DiscussionRow.discussion_id.in_(norm_ids))
            .all()
        )
        if len(rows) != len(norm_ids):
            raise SelectedDiscussionEvidenceError("Unknown or foreign discussion identity")
        row_map = {str(r.discussion_id): r for r in rows}
        # Preserve input order.
        ordered_rows = [row_map[i] for i in norm_ids]

        entries = []
        for r in ordered_rows:
            entries.append(_read_one_discussion(
                session_obj, r, ml, el,
                source_pair_reader=source_pair_reader,
                source_pair_max_text_chars=source_pair_max_text_chars,
                source_pair_readback=source_pair_readback,
                source_pair_declaration_invalid=source_pair_declaration_invalid,
            ))
    # Aggregate.
    member_available = sum(e.member_coverage.member_available_count for e in entries)
    member_returned = sum(e.member_coverage.member_returned_count for e in entries)
    member_omitted = sum(e.member_coverage.member_omitted_count for e in entries)
    event_available = sum(e.event_coverage.event_available_count for e in entries)
    event_returned = sum(e.event_coverage.event_returned_count for e in entries)
    event_omitted = sum(e.event_coverage.event_omitted_count for e in entries)
    unresolved_members = sum(e.member_coverage.unresolved_member_count for e in entries)
    unresolved_events = sum(e.event_coverage.event_unresolved_subject_count for e in entries)
    member_complete = all(e.member_coverage.member_source_complete for e in entries) and len(entries) > 0
    event_complete = all(e.event_coverage.event_source_complete for e in entries) and len(entries) > 0
    truncation = any(e.has_role_text_truncation for e in entries)
    unverified = any(e.has_unverified_or_legacy_provenance for e in entries)
    structurally_complete = (
        len(entries) > 0
        and unresolved_members == 0
        and member_omitted == 0
        and event_omitted == 0
        and unresolved_events == 0
        and not truncation
        and all(e.is_structurally_complete for e in entries)
    )
    insufficiency: list[str] = []
    for e in entries:
        insufficiency.extend(e.insufficiency_reasons)
    # de-duplicate, stable
    seen_reasons: set[str] = set()
    dedup: list[str] = []
    for r in insufficiency:
        if r not in seen_reasons:
            seen_reasons.add(r)
            dedup.append(r)
    if not entries:
        dedup.append("no_discussion_entries")
    insufficient = bool(dedup) or unverified
    is_complete = False  # semantic completeness is never asserted for fallible evidence.

    source_pair_readback = any(getattr(e, "source_pair_readback", False) for e in entries)
    source_pair_declaration_invalid = any(getattr(e, "source_pair_declaration_invalid", False) for e in entries)
    source_pairs = tuple(pair for e in entries for pair in (getattr(e, "source_pairs", ()) or ()))
    source_pair_event_count = sum(getattr(e, "source_pair_event_count", 0) for e in entries)
    source_pair_count = len(source_pairs)
    source_pair_available_count = sum(getattr(e, "source_pair_available_count", 0) for e in entries)
    source_pair_structural_complete_count = sum(getattr(e, "source_pair_structural_complete_count", 0) for e in entries)
    source_pair_unverified_count = sum(getattr(e, "source_pair_unverified_count", 0) for e in entries)
    source_pair_text_truncated_count = sum(getattr(e, "source_pair_text_truncated_count", 0) for e in entries)
    if source_pair_event_count != len(source_pairs):
        source_pair_complete = False
    else:
        # Keep all-complete semantics but do not mark eventless/legacy result incomplete.
        if source_pair_event_count == 0:
            source_pair_complete = True
        else:
            source_pair_complete = (
                source_pair_available_count == source_pair_event_count
                and source_pair_structural_complete_count == source_pair_event_count
                and source_pair_text_truncated_count == 0
            )
            for pair in source_pairs:
                try:
                    gaps = pair.get("gaps") if isinstance(pair, Mapping) else getattr(pair, "gaps", ())
                except Exception:
                    gaps = ()
                if any(g not in {"unverified-provenance"} for g in (gaps or ())):
                    source_pair_complete = False

    result = SelectedDiscussionEvidenceResult(
        session_id=clean_session,
        requested_discussion_ids=norm_ids,
        selected_discussion_ids=norm_ids,
        discussion_count=len(entries),
        entries=tuple(entries),
        member_available_count=member_available,
        member_returned_count=member_returned,
        member_omitted_count=member_omitted,
        event_available_count=event_available,
        event_returned_count=event_returned,
        event_omitted_count=event_omitted,
        unresolved_member_count=unresolved_members,
        event_unresolved_subject_count=unresolved_events,
        member_source_complete=member_complete,
        event_source_complete=event_complete,
        has_role_text_truncation=truncation,
        has_unverified_or_legacy_provenance=unverified,
        is_structurally_complete=structurally_complete,
        semantic_complete=False,
        untrusted=True,
        insufficiency_reasons=tuple(dedup),
        insufficient=insufficient,
        revalidation_required_before_dispatch=True,
        complete=is_complete,
        source_pair_readback=bool(source_pair_readback),
        source_pairs=tuple(source_pairs),
        source_pair_event_count=int(source_pair_event_count),
        source_pair_count=int(source_pair_count),
        source_pair_available_count=int(source_pair_available_count),
        source_pair_structural_complete_count=int(source_pair_structural_complete_count),
        source_pair_unverified_count=int(source_pair_unverified_count),
        source_pair_text_truncated_count=int(source_pair_text_truncated_count),
        source_pair_complete=bool(source_pair_complete),
        source_pair_declaration_invalid=bool(source_pair_declaration_invalid),
    )
    return result


__all__ = [
    "SelectedDiscussionEvidenceError",
    "SourceExchangeEvidence",
    "DiscussionHistoryEventEvidence",
    "EvidenceCoverage",
    "SelectedDiscussionEvidenceEntry",
    "SelectedDiscussionEvidenceResult",
    "read_selected_discussion_evidence",
]
