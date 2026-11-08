"""Memory Service (Phase 7) — single authoritative interface over typed,
provenance-bearing memory.

Core invariants ():
- Memory is DATA. It never overrides runtime state, permissions, evidence,
  or completion authority.
- Write authority is enforced deterministically by source_type × kind
  (MODEL_INFERENCE can only create ASSUMPTION/BELIEF, always UNVERIFIED;
  only TOOL_EVIDENCE/USER_EXPLICIT create verified entries).
- Contradictions are established by deterministic code and represented as
  CONTRADICTS edges — never resolved by silent overwrite.
- Forgetting is a lifecycle status (FORGOTTEN): content stays auditable but
  is permanently excluded from retrieval/context.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field, fields as _dataclass_fields
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from backend.models.database import MemoryEdge, MemoryEntry
from backend.runtime.events import DbEventLedger

# --- enums ---------------------------------------------------------------------

MEMORY_KINDS = {
    "FACT", "PREFERENCE", "ASSUMPTION", "BELIEF",
    "EPISODIC", "PROCEDURAL", "INSTRUCTION", "UNKNOWN",
}
MEMORY_SCOPES = {"GLOBAL", "USER", "GOAL", "TASK", "SESSION", "ENTITY"}
SOURCE_TYPES = {
    "USER_EXPLICIT", "USER_CONVERSATION", "TOOL_EVIDENCE",
    "SYSTEM_OBSERVATION", "MODEL_INFERENCE", "CONSOLIDATION",
    "IMPORTED", "UNKNOWN",
}
MEMORY_STATUS = {"ACTIVE", "SUPERSEDED", "INVALIDATED", "ARCHIVED", "FORGOTTEN"}
VERIFICATION = {"VERIFIED_TOOL", "VERIFIED_USER", "UNVERIFIED"}

# Per-kind decay: unaccessed ACTIVE entries older than the window are
# archived by decay_pass(). None = no automatic decay.
DECAY_DAYS: Dict[str, Optional[int]] = {
    "ASSUMPTION": 7,
    "BELIEF": 30,
    "EPISODIC": 45,
    "UNKNOWN": 60,
    "FACT": None, "PREFERENCE": None, "INSTRUCTION": None,
    "PROCEDURAL": None,
}

# Evidence hierarchy (higher wins when resolving contradictions). Live
# runtime observations ALWAYS outrank any memory regardless of this table.
EVIDENCE_RANK = {
    "TOOL_EVIDENCE": 100,
    "USER_EXPLICIT": 90,
    "USER_CONVERSATION": 70,
    "SYSTEM_OBSERVATION": 50,
    "CONSOLIDATION": 40,
    "MODEL_INFERENCE": 20,
    "IMPORTED": 10,
    "UNKNOWN": 0,
}

# Write-authority policy: source_type → allowed kinds + verification ceiling.
WRITE_AUTHORITY = {
    "USER_EXPLICIT": {"kinds": {"FACT", "PREFERENCE", "INSTRUCTION", "BELIEF"},
                      "max_verification": "VERIFIED_USER"},
    "USER_CONVERSATION": {"kinds": {"PREFERENCE", "FACT", "INSTRUCTION"},
                          "max_verification": "UNVERIFIED"},
    "TOOL_EVIDENCE": {"kinds": {"FACT"}, "max_verification": "VERIFIED_TOOL"},
    "SYSTEM_OBSERVATION": {"kinds": {"FACT", "EPISODIC"},
                           "max_verification": "VERIFIED_TOOL"},
    "MODEL_INFERENCE": {"kinds": {"ASSUMPTION", "BELIEF"},
                        "max_verification": "UNVERIFIED"},
    "CONSOLIDATION": {"kinds": {"FACT", "PREFERENCE", "ASSUMPTION", "BELIEF"},
                      "max_verification": "UNVERIFIED"},
    "IMPORTED": {"kinds": set(MEMORY_KINDS), "max_verification": "UNVERIFIED"},
    "UNKNOWN": {"kinds": {"UNKNOWN"}, "max_verification": "UNVERIFIED"},
}


class MemoryAuthorityError(ValueError):
    """A write violates the source×kind authority policy."""


@dataclass
class RetrievedMemory:
    id: int
    kind: str
    scope: str
    scope_ref: Optional[str]
    key: str
    value: str
    confidence: Optional[float]
    verification_status: str
    source_type: str
    observed_at: Optional[datetime]
    updated_at: Optional[datetime]
    score: float = 0.0


# --- read access context (minimal) -----------------------------------------

_SCOPED_REF_FIELDS = {
    "USER": "user_id",
    "GLOBAL": None,
    "SESSION": "session_id",
    "TASK": "task_id",
    "GOAL": "goal_id",
    "ENTITY": "entity_id",
}


@dataclass(frozen=True)
class MemoryAccessContext:
    """Minimal scope identity used to filter memory reads.

    An active memory entry is readable when its ``scope`` is permitted and,
    for scoped scopes (TASK/SESSION/GOAL/ENTITY), its ``scope_ref`` exactly
    matches the corresponding identity carried here.  USER/GLOBAL scopes are
    treated as scope-wide unless explicitly disabled.
    """

    session_id: Optional[str] = None
    session: Optional[str] = None
    task_id: Optional[str] = None
    task: Optional[str] = None
    goal_id: Optional[str] = None
    goal: Optional[str] = None
    user_id: Optional[str] = None
    user: Optional[str] = None
    entity_id: Optional[str] = None
    entity: Optional[str] = None
    profile_id: Optional[str] = None
    profile: Optional[str] = None

    scopes: Optional[Any] = None
    allowed_scopes: Optional[Any] = None
    readable_scopes: Optional[Any] = None

    scope: Optional[str] = None
    scope_ref: Optional[str] = None
    scope_refs: Optional[Any] = None
    accessible_scope_refs: Optional[Any] = None

    include_global: bool = True
    include_user: bool = True
    include_session: bool = True
    include_task: bool = True
    include_goal: bool = True
    include_entity: bool = True
    include_profile: bool = True
    allow_global: Optional[bool] = None

    @property
    def resolved_session_id(self) -> Optional[str]:
        return self.session_id if self.session_id is not None else self.session

    @property
    def resolved_task_id(self) -> Optional[str]:
        return self.task_id if self.task_id is not None else self.task

    @property
    def resolved_goal_id(self) -> Optional[str]:
        return self.goal_id if self.goal_id is not None else self.goal

    @property
    def resolved_user_id(self) -> Optional[str]:
        return self.user_id if self.user_id is not None else self.user

    @property
    def resolved_entity_id(self) -> Optional[str]:
        return self.entity_id if self.entity_id is not None else self.entity

    @property
    def resolved_profile_id(self) -> Optional[str]:
        return self.profile_id if self.profile_id is not None else self.profile

    @property
    def resolved_global_allowed(self) -> bool:
        if self.allow_global is not None:
            return bool(self.allow_global)
        return bool(self.include_global)

    def _ref_for_scope(self, scope: str) -> Optional[str]:
        scope = scope.upper()
        if isinstance(self.scope_refs, dict):
            if scope in self.scope_refs:
                return self.scope_refs.get(scope)
        if isinstance(self.accessible_scope_refs, dict):
            if scope in self.accessible_scope_refs:
                return self.accessible_scope_refs.get(scope)
        if self.scope is not None and self.scope.strip().upper() == scope:
            return self.scope_ref
        if scope == "SESSION":
            return self.resolved_session_id
        if scope == "TASK":
            return self.resolved_task_id
        if scope == "GOAL":
            return self.resolved_goal_id
        if scope == "USER":
            return self.resolved_user_id
        if scope == "ENTITY":
            return self.resolved_entity_id
        if scope == "PROFILE":
            return self.resolved_profile_id
        return None

    def _explicit_scope_list(self) -> Optional[List[str]]:
        raw = None
        if self.allowed_scopes is not None:
            raw = self.allowed_scopes
        elif self.readable_scopes is not None:
            raw = self.readable_scopes
        elif self.scopes is not None:
            raw = self.scopes
        if raw is None:
            return None
        if isinstance(raw, str):
            raw = [raw]
        try:
            return [str(x).strip().upper() for x in raw if str(x).strip()]
        except TypeError:
            return None

    def allowed_pairs(self) -> set:
        """Return {(scope, ref or None)} readable under this context."""
        pairs: set = set()
        explicit = self._explicit_scope_list()
        if explicit is not None:
            scopes = explicit
        else:
            scopes = []
            if self.include_user:
                scopes.append("USER")
            if self.resolved_global_allowed:
                scopes.append("GLOBAL")
            if self.include_session and self.resolved_session_id is not None:
                scopes.append("SESSION")
            if self.include_task and self.resolved_task_id is not None:
                scopes.append("TASK")
            if self.include_goal and self.resolved_goal_id is not None:
                scopes.append("GOAL")
            if self.include_entity and self.resolved_entity_id is not None:
                scopes.append("ENTITY")
            if self.include_profile and self.resolved_profile_id is not None:
                scopes.append("PROFILE")
            # A context with no positive identities still permits USER memory by
            # default; empty derived sets collapse to USER only.
            if not scopes:
                scopes = ["USER"] if self.include_user else []
        # Single-scope convenience form.
        if self.scope is not None and self.scope.strip():
            scopes = [self.scope.strip().upper()]

        for scope in scopes:
            scope = scope.upper()
            if scope in ("USER", "GLOBAL"):
                pairs.add((scope, None))
            elif scope in ("SESSION", "TASK", "GOAL", "ENTITY", "PROFILE"):
                ref = self._ref_for_scope(scope)
                if ref is not None:
                    pairs.add((scope, ref))
            else:
                # Unknown scopes are not readable by default.
                pass
        return pairs

    @property
    def visible_scopes(self) -> set:
        return {p[0] for p in self.allowed_pairs()}

    def allows(self, entry: Any) -> bool:
        scope = getattr(entry, "scope", None)
        if scope is None and isinstance(entry, dict):
            scope = entry.get("scope")
        scope = (scope or "").upper()
        ref = getattr(entry, "scope_ref", None)
        if ref is None and isinstance(entry, dict):
            ref = entry.get("scope_ref")
        for p_scope, p_ref in self.allowed_pairs():
            if scope != p_scope:
                continue
            if p_scope in ("USER", "GLOBAL"):
                return True
            if p_ref is None or p_ref == ref:
                return True
        return False

    def can_read(self, entry: Any) -> bool:
        return self.allows(entry)

    def readable(self, entry: Any) -> bool:
        return self.allows(entry)

    def is_readable(self, entry: Any) -> bool:
        return self.allows(entry)

    def visible(self, entry: Any) -> bool:
        return self.allows(entry)

    def filter(self, entries: Any) -> List[Any]:
        return [e for e in (entries or []) if self.allows(e)]

    def filter_entries(self, entries: Any) -> List[Any]:
        return self.filter(entries)

    def filter_readable(self, entries: Any) -> List[Any]:
        return self.filter(entries)

    def readable_entries(self, entries: Any) -> List[Any]:
        return self.filter(entries)


def _coerce_memory_access_context(value: Any) -> Optional["MemoryAccessContext"]:
    if value is None:
        return None
    if isinstance(value, MemoryAccessContext):
        return value
    if isinstance(value, dict):
        names = {f.name for f in _dataclass_fields(MemoryAccessContext)}
        kwargs = {k: v for k, v in value.items() if k in names}
        try:
            return MemoryAccessContext(**kwargs)
        except (TypeError, ValueError):
            return None
    # Duck-typed source object.
    names = {f.name for f in _dataclass_fields(MemoryAccessContext)}
    kwargs = {}
    for name in names:
        if hasattr(value, name):
            kwargs[name] = getattr(value, name)
    # Common aliases not spelled as dataclass fields.
    alias_map = {
        "session": "session",
        "task": "task",
        "goal": "goal",
        "user": "user",
        "entity": "entity",
        "profile": "profile",
    }
    if not kwargs:
        return None
    try:
        return MemoryAccessContext(**kwargs)
    except (TypeError, ValueError):
        return None


def _norm(text: str) -> str:
    """Normalization for deterministic contradiction comparison."""
    return re.sub(r"[^a-z0-9]+", " ", (text or "").lower()).strip()


# Retrieval scoring constants (values unchanged from former literals).
_RETRIEVE_CANDIDATE_LIMIT = 500
_SCORE_TERM_WEIGHT = 3.0
_SCORE_CONFIDENCE_WEIGHT = 2.0
_SCORE_VERIFIED_BONUS = 2.0
_SCORE_RECENCY_MAX = 2.0
_RECENCY_DECAY_DAYS = 30.0
_MISSING_TIMESTAMP_RECENCY_DAYS = 999


def _scope_pair_filter(pairs):
    """Build the OR filter for allowed (scope, scope_ref) pairs.

    USER/GLOBAL pairs match on scope alone; all other scopes also require
    an exact scope_ref match. Returns None for an empty pair set so callers
    can apply their existing empty-set handling.
    """
    if not pairs:
        return None
    from sqlalchemy import or_ as _or

    conditions = []
    for scope, ref in pairs:
        if scope in ("USER", "GLOBAL"):
            conditions.append(MemoryEntry.scope == scope)
        else:
            conditions.append(
                (MemoryEntry.scope == scope)
                & (MemoryEntry.scope_ref == ref)
            )
    if not conditions:
        return None
    return _or(*conditions)


class MemoryService:
    _scope_pair_filter = staticmethod(_scope_pair_filter)

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # ------------------------------------------------------------------ #
    # Write paths                                                          #
    # ------------------------------------------------------------------ #

    def create(
        self,
        *,
        key: str,
        value: str,
        kind: str = "UNKNOWN",
        scope: str = "USER",
        scope_ref: Optional[str] = None,
        source_type: str = "UNKNOWN",
        source_ref: Optional[str] = None,
        confidence: Optional[float] = None,
        category: Optional[str] = None,
        check_contradictions: bool = True,
    ) -> MemoryEntry:
        """Validated creation with authority policy + contradiction detection."""
        kind = (kind or "UNKNOWN").upper()
        scope = (scope or "USER").upper()
        source_type = (source_type or "UNKNOWN").upper()
        if kind not in MEMORY_KINDS:
            raise ValueError(f"unknown memory kind: {kind}")
        if scope not in MEMORY_SCOPES:
            raise ValueError(f"unknown memory scope: {scope}")
        if source_type not in SOURCE_TYPES:
            raise ValueError(f"unknown source type: {source_type}")

        policy = WRITE_AUTHORITY[source_type]
        if kind not in policy["kinds"]:
            raise MemoryAuthorityError(
                f"source '{source_type}' may not create kind '{kind}' "
                f"(allowed: {sorted(policy['kinds'])})"
            )

        existing = (
            self.db.query(MemoryEntry).filter_by(key=key, status="ACTIVE").first()
        )
        if existing is not None:
            # Same subject already active → supersede via update path so the
            # history/conflict rules apply instead of blind overwrite.
            return self.update(
                existing.id, new_value=value, new_kind=kind if kind != "UNKNOWN"
                else None,
                source_type=source_type, source_ref=source_ref,
                confidence=confidence, category=category,
            )

        verification = self._verification_for(source_type, policy)
        entry = MemoryEntry(
            key=key,
            value=value,
            category=category or self._legacy_category(kind),
            kind=kind,
            scope=scope,
            scope_ref=scope_ref,
            source_type=source_type,
            source_ref=source_ref,
            observed_at=datetime.utcnow(),
            confidence=confidence,
            verification_status=verification,
            status="ACTIVE",
        )
        # Concurrency-safe insert: same-key concurrent creates serialize via
        # UNIQUE key + INSERT; loser retries via supersession path.
        try:
            self.db.add(entry)
            self.db.flush()
        except Exception as exc:
            # UNIQUE constraint violation -> another writer won the same key.
            from sqlalchemy.exc import IntegrityError
            if isinstance(exc, IntegrityError) and "UNIQUE" in str(exc) or "unique" in str(exc).lower():
                try:
                    self.db.rollback()
                except Exception:
                    pass
                # Re-read winner; if ACTIVE exists, supersede deterministically.
                existing2 = self.db.query(MemoryEntry).filter_by(key=key, status="ACTIVE").first()
                if existing2 is not None:
                    return self.update(
                        existing2.id, new_value=value,
                        new_kind=kind if kind != "UNKNOWN" else None,
                        source_type=source_type, source_ref=source_ref,
                        confidence=confidence, category=category,
                    )
                # If no ACTIVE (race left superseded tombstone), retry create once.
                retry = MemoryEntry(
                    key=key, value=value,
                    category=category or self._legacy_category(kind),
                    kind=kind, scope=scope, scope_ref=scope_ref,
                    source_type=source_type, source_ref=source_ref,
                    observed_at=datetime.utcnow(), confidence=confidence,
                    verification_status=verification, status="ACTIVE",
                )
                self.db.add(retry)
                self.db.flush()
                entry = retry
            else:
                raise

        if check_contradictions:
            contradictions = self.detect_contradictions(entry)
            for other in contradictions:
                self.link_contradiction(entry, other)
                if self._evidence_rank(entry) > self._evidence_rank(other):
                    other.status = "SUPERSEDED"
                    other.superseded_by = entry.id

        self.ledger.append_event("memory", "memory.created", {
            "id": entry.id, "key": key, "kind": kind, "scope": scope,
            "source_type": source_type,
            "contradicts": [o.id for o in contradictions] if check_contradictions else [],
        })
        try:
            self.db.commit()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError as _IE
            if isinstance(exc, _IE):
                try:
                    self.db.rollback()
                except Exception:
                    pass
                # Lost race at commit time — re-resolve via update if winner exists.
                existing3 = self.db.query(MemoryEntry).filter_by(key=key, status="ACTIVE").first()
                if existing3 is not None and existing3.id != entry.id:
                    # Winner already ACTIVE, our entry was rolled back.
                    return self.update(
                        existing3.id, new_value=value,
                        new_kind=kind if kind != "UNKNOWN" else None,
                        source_type=source_type, source_ref=source_ref,
                        confidence=confidence, category=category,
                    )
                raise
            raise
        self.db.refresh(entry)
        return entry

    def update(
        self,
        memory_id: int,
        *,
        new_value: str,
        new_kind: Optional[str] = None,
        source_type: str = "UNKNOWN",
        source_ref: Optional[str] = None,
        confidence: Optional[float] = None,
        category: Optional[str] = None,
    ) -> MemoryEntry:
        """Supersede an ACTIVE memory with a corrected/reinforced version.

        The old row remains inspectable as SUPERSEDED; the new row inherits
        key/scope/kind unless the authority policy permits a kind change.
        """
        old = self.db.query(MemoryEntry).filter_by(id=memory_id).first()
        if old is None:
            raise ValueError(f"memory {memory_id} not found")
        if old.status == "FORGOTTEN":
            raise ValueError("cannot update a forgotten memory")

        # Normalize like create(): source_type is matched case-insensitively by
        # its canonical uppercase token. (Fix: update previously used the raw
        # value, so lowercase/variant labels fell through to the UNKNOWN
        # policy even though create() uppercased them.)
        source_type = (source_type or "UNKNOWN").upper()
        policy = WRITE_AUTHORITY.get(source_type, WRITE_AUTHORITY["UNKNOWN"])
        target_kind = (new_kind or old.kind).upper()
        if target_kind != old.kind and target_kind not in policy["kinds"]:
            raise MemoryAuthorityError(
                f"source '{source_type}' may not change kind to '{target_kind}'"
            )
        # A MODEL_INFERENCE / CONSOLIDATION update can never VERIFY a memory.
        verification = self._verification_for(source_type, policy)
        # A prior verified badge survives only a genuine reinforcement of the
        # SAME value; a changed value must not inherit the old verified badge
        # merely because MemoryService.update() was called. This preserves
        # legitimate verified values (reinforcement) while refusing to mint
        # verification for changed/unvalidated claims.
        if (old.verification_status.startswith("VERIFIED")
                and verification == "UNVERIFIED"
                and (new_value or "") == (old.value or "")):
            verification = old.verification_status  # verified survives weak reinforcement

        now = datetime.utcnow()
        if old.status != "ACTIVE":
            # Idempotency guard: a superseded/forgotten row cannot be
            # superseded again; return its active successor if one exists.
            if old.superseded_by:
                successor = self.db.query(MemoryEntry).filter_by(
                    id=old.superseded_by).first()
                if successor is not None and successor.status == "ACTIVE":
                    return successor
            raise ValueError(f"memory {memory_id} is {old.status}, not ACTIVE")

        # Free the UNIQUE key for the successor while keeping history
        # inspectable under a namespaced archival key. Flush the UPDATE
        # first — unit-of-work would otherwise INSERT the successor before
        # the rename and trip the UNIQUE constraint.
        successor_key = old.key  # capture BEFORE renaming
        old.status = "SUPERSEDED"
        old.key = f"{old.key}:superseded:{old.id}"
        old.updated_at = now
        self.db.flush()

        entry = MemoryEntry(
            key=successor_key,
            value=new_value,
            category=category or old.category,
            kind=target_kind,
            scope=old.scope,
            scope_ref=old.scope_ref,
            source_type=source_type,
            source_ref=source_ref,
            observed_at=now,
            confidence=confidence,
            verification_status=verification,
            status="ACTIVE",
        )
        # Concurrency: two concurrent supersessions for same key -> second
        # successor insert hits UNIQUE. Treat as idempotent: return winner.
        try:
            self.db.add(entry)
            self.db.flush()
        except Exception as exc:
            from sqlalchemy.exc import IntegrityError as _IE2
            if isinstance(exc, _IE2) and ("UNIQUE" in str(exc) or "unique" in str(exc).lower()):
                try:
                    self.db.rollback()
                except Exception:
                    pass
                # Winner already ACTIVE for this key — return it.
                winner = self.db.query(MemoryEntry).filter_by(key=successor_key, status="ACTIVE").first()
                if winner is not None:
                    # Old row already superseded by winner? ensure link
                    try:
                        old2 = self.db.query(MemoryEntry).filter_by(id=memory_id).first()
                        if old2 and old2.status == "SUPERSEDED" and old2.superseded_by is None:
                            old2.superseded_by = winner.id
                            self.db.commit()
                    except Exception:
                        pass
                    return winner
                raise
            raise
        old.superseded_by = entry.id

        self.link(old, entry, "supercedes")
        contradictions = self.detect_contradictions(entry)
        for other in contradictions:
            if other.id == old.id:
                continue
            self.link_contradiction(entry, other)

        self.ledger.append_event("memory", "memory.superseded", {
            "old_id": old.id, "new_id": entry.id,
            "key": old.key, "source_type": source_type,
        })
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def forget(self, memory_id: int, reason: str = "user_requested") -> MemoryEntry:
        """Explicit forgetting: excluded from retrieval/context forever but
        retained as an audit record of what was forgotten and why."""
        entry = self.db.query(MemoryEntry).filter_by(id=memory_id).first()
        if entry is None:
            raise ValueError(f"memory {memory_id} not found")
        entry.status = "FORGOTTEN"
        entry.updated_at = datetime.utcnow()
        self.ledger.append_event("memory", "memory.forgotten", {
            "id": entry.id, "key": entry.key, "reason": reason[:200],
        })
        self.db.commit()
        self.db.refresh(entry)
        return entry

    def archive(self, memory_id: int, reason: str = "decay") -> MemoryEntry:
        """Archive: out of context injection, cheaper than forgetting."""
        entry = self.db.query(MemoryEntry).filter_by(id=memory_id).first()
        if entry is None:
            raise ValueError(f"memory {memory_id} not found")
        entry.status = "ARCHIVED"
        entry.updated_at = datetime.utcnow()
        self.ledger.append_event("memory", "memory.archived", {
            "id": entry.id, "key": entry.key, "reason": reason[:200],
        })
        self.db.commit()
        return entry

    def link(self, source: MemoryEntry, target: MemoryEntry,
             relation: str, weight: float = 1.0) -> MemoryEdge:
        edge = MemoryEdge(source_key=str(source.id), relation=relation,
                          target_key=str(target.id), weight=weight)
        self.db.add(edge)
        self.db.flush()
        return edge

    # ------------------------------------------------------------------ #
    # Contradictions                                                       #
    # ------------------------------------------------------------------ #

    CONTRADICTING_KINDS = {"FACT", "PREFERENCE", "INSTRUCTION"}

    def detect_contradictions(self, candidate: MemoryEntry) -> List[MemoryEntry]:
        """Deterministic detection: overlapping subject tokens with an
        incompatible value, among ACTIVE contradicting-kind entries."""
        base_tokens = set(_norm(candidate.key).split())
        conflicts: List[MemoryEntry] = []
        rows = (
            self.db.query(MemoryEntry)
            .filter(MemoryEntry.status == "ACTIVE")
            .filter(MemoryEntry.kind.in_(self.CONTRADICTING_KINDS))
            .filter(MemoryEntry.id != candidate.id)
            .all()
        )
        cand_value = _norm(candidate.value)
        for row in rows:
            row_tokens = set(_norm(row.key).split())
            overlap = base_tokens & row_tokens
            if not overlap:
                continue  # unrelated subjects coexist
            if row.scope != candidate.scope:
                continue  # different scope: coexists, not conflicting
            if _norm(row.value) == cand_value:
                continue  # identical claim: reinforcement, not contradiction
            conflicts.append(row)
        return conflicts

    def link_contradiction(self, newer: MemoryEntry, older: MemoryEntry) -> MemoryEdge:
        return self.link(newer, older, "contradicts")

    def resolve_contradiction(self, winner_id: int, loser_id: int,
                              reason: str = "stronger_evidence") -> Dict[str, Any]:
        """Deterministic resolution: loser becomes SUPERSEDED pointing at the
        winner; both remain inspectable. History preserved."""
        winner = self.db.query(MemoryEntry).filter_by(id=winner_id).first()
        loser = self.db.query(MemoryEntry).filter_by(id=loser_id).first()
        if winner is None or loser is None:
            raise ValueError("winner/loser memory not found")
        loser.status = "SUPERSEDED"
        loser.superseded_by = winner.id
        loser.updated_at = datetime.utcnow()
        self.link(winner, loser, "supercedes")
        self.ledger.append_event("memory", "memory.contradiction.resolved", {
            "winner": winner.id, "loser": loser.id, "reason": reason[:200],
        })
        self.db.commit()
        return {"winner": winner.id, "loser": loser.id}

    def _evidence_rank(self, entry: MemoryEntry) -> int:
        rank = EVIDENCE_RANK.get(entry.source_type, 0)
        if entry.verification_status.startswith("VERIFIED"):
            rank += 10
        return rank

    def _verification_for(self, source_type: str, policy: Dict[str, Any]) -> str:
        return policy["max_verification"]

    @staticmethod
    def _legacy_category(kind: str) -> str:
        return {
            "PREFERENCE": "user_preference", "FACT": "user_profile",
            "INSTRUCTION": "user_instruction", "EPISODIC": "episodic",
            "PROCEDURAL": "procedural", "ASSUMPTION": "assumption",
            "BELIEF": "belief", "UNKNOWN": "unknown",
        }.get(kind, "unknown")

    # ------------------------------------------------------------------ #
    # Retrieval                                                            #
    # ------------------------------------------------------------------ #

    def retrieve(
        self,
        query: str = "",
        *,
        kinds: Optional[List[str]] = None,
        scopes: Optional[List[str]] = None,
        max_results: int = 25,
        min_confidence: Optional[float] = None,
        require_verified: bool = False,
        recency_preference: bool = True,
        access_context: Any = None,
        context: Any = None,
        memory_access_context: Any = None,
    ) -> List[RetrievedMemory]:
        """Deterministic retrieval over ACTIVE memories only.

        FORGOTTEN/SUPERSEDED/INVALIDATED/ARCHIVED are structurally excluded.
        """
        ctx = _coerce_memory_access_context(
            access_context if access_context is not None else
            (context if context is not None else memory_access_context)
        )
        q = self.db.query(MemoryEntry).filter(MemoryEntry.status == "ACTIVE")
        if kinds:
            q = q.filter(MemoryEntry.kind.in_([k.upper() for k in kinds]))
        if ctx is not None:
            pairs = ctx.allowed_pairs()
            if scopes is not None:
                allowed_scopes = {str(s).upper() for s in scopes}
                pairs = {(s, r) for (s, r) in pairs if s in allowed_scopes}
            scope_filter = _scope_pair_filter(pairs)
            if scope_filter is not None:
                q = q.filter(scope_filter)
            else:
                q = q.filter(MemoryEntry.id.is_(None))
        elif scopes:
            q = q.filter(MemoryEntry.scope.in_([s.upper() for s in scopes]))
        if min_confidence is not None:
            q = q.filter(MemoryEntry.confidence >= min_confidence)
        if require_verified:
            q = q.filter(MemoryEntry.verification_status.like("VERIFIED%"))

        rows = q.order_by(MemoryEntry.updated_at.desc()).limit(
            _RETRIEVE_CANDIDATE_LIMIT).all()

        tokens = [t for t in re.split(r"[^a-z0-9]+", query.lower()) if len(t) > 2]
        scored: List[RetrievedMemory] = []
        now = datetime.utcnow()
        for r in rows:
            hay = f"{r.key} {r.value}".lower()
            term_hits = sum(1 for t in tokens if t in hay) if tokens else 0
            if tokens and term_hits == 0:
                continue
            recency_days = (
                (now - (r.observed_at or r.created_at)).days
                if (r.observed_at or r.created_at)
                else _MISSING_TIMESTAMP_RECENCY_DAYS
            )
            score = term_hits * _SCORE_TERM_WEIGHT \
                + (r.confidence or 0.5) * _SCORE_CONFIDENCE_WEIGHT \
                + (_SCORE_VERIFIED_BONUS
                   if r.verification_status.startswith("VERIFIED") else 0.0)
            if recency_preference:
                score += max(
                    0.0, _SCORE_RECENCY_MAX - recency_days / _RECENCY_DECAY_DAYS)
            scored.append(RetrievedMemory(
                id=r.id, kind=r.kind, scope=r.scope, scope_ref=r.scope_ref,
                key=r.key, value=r.value, confidence=r.confidence,
                verification_status=r.verification_status,
                source_type=r.source_type, observed_at=r.observed_at,
                updated_at=r.updated_at, score=round(score, 3),
            ))
        scored.sort(key=lambda m: (-m.score, -(m.updated_at or now).timestamp()))
        return scored[:max_results]

    def get_active(
        self,
        key: str,
        *,
        access_context: Any = None,
        context: Any = None,
        memory_access_context: Any = None,
    ) -> Optional[MemoryEntry]:
        ctx = _coerce_memory_access_context(
            access_context if access_context is not None else
            (context if context is not None else memory_access_context)
        )
        q = self.db.query(MemoryEntry).filter_by(key=key, status="ACTIVE")
        if ctx is not None:
            pairs = ctx.allowed_pairs()
            scope_filter = _scope_pair_filter(pairs)
            if scope_filter is not None:
                q = q.filter(scope_filter)
            else:
                return None
        row = q.first()
        return row

    # ------------------------------------------------------------------ #
    # Listing / browsing (truthful, paginated, scope-safe)                 #
    # ------------------------------------------------------------------ #
    #
    # ``retrieve()`` above is a *semantic-ish* scorer that pulls a recent
    # candidate pool (ordered, capped) and then ranks in Python. That is the
    # right trade-off for context injection, but it makes older matching rows
    # invisible in a browser list. ``list_items`` is the stable, database-level
    # listing path used by the read-only memory UI: every ACTIVE row in the
    # requested scope(s) is eligible regardless of age, filtering happens in
    # SQL where it can be expressed (kind/scope/confidence/verification and
    # a lexical key/value search), and pagination is applied by the database in
    # a stable order. It deliberately does not claim global semantic scoring:
    # the keyword match is a substring test over ``key``/``value`` only and
    # results are ordered by recency, not by the retrieve() score.

    def list_items(
        self,
        *,
        query: str = "",
        kinds: Optional[List[str]] = None,
        scopes: Optional[List[str]] = None,
        max_results: Optional[int] = None,
        limit: Optional[int] = None,
        offset: int = 0,
        min_confidence: Optional[float] = None,
        require_verified: bool = False,
        access_context: Any = None,
        context: Any = None,
        memory_access_context: Any = None,
    ) -> List[RetrievedMemory]:
        ctx = _coerce_memory_access_context(
            access_context if access_context is not None else
            (context if context is not None else memory_access_context)
        )
        page_size = 25
        if limit is not None:
            page_size = limit
        if max_results is not None:
            page_size = max_results
        if page_size is None or page_size < 1:
            page_size = 25

        # Canonicalise filters exactly like retrieve().
        q = self.db.query(MemoryEntry).filter(MemoryEntry.status == "ACTIVE")
        if kinds:
            q = q.filter(MemoryEntry.kind.in_([k.upper() for k in kinds]))
        if ctx is not None:
            from sqlalchemy import or_ as _or
            pairs = ctx.allowed_pairs()
            if scopes is not None:
                allowed_scopes = {str(s).upper() for s in scopes}
                pairs = {(s, r) for (s, r) in pairs if s in allowed_scopes}
            if pairs:
                conditions = []
                for scope, ref in pairs:
                    if scope in ("USER", "GLOBAL"):
                        conditions.append(MemoryEntry.scope == scope)
                    else:
                        conditions.append(
                            (MemoryEntry.scope == scope)
                            & (MemoryEntry.scope_ref == ref)
                        )
                q = q.filter(_or(*conditions))
            else:
                q = q.filter(MemoryEntry.id.is_(None))
        elif scopes:
            q = q.filter(MemoryEntry.scope.in_([s.upper() for s in scopes]))
        if min_confidence is not None:
            q = q.filter(MemoryEntry.confidence >= min_confidence)
        if require_verified:
            q = q.filter(MemoryEntry.verification_status.like("VERIFIED%"))

        tokens = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if t]
        # Lexical key/value search. A row matches if ANY token appears as a
        # substring of key or value. This is a complete (within these columns)
        # but deliberately non-semantic filter; see the method docstring.
        if tokens:
            from sqlalchemy import or_
            conditions = []
            for t in tokens:
                like = f"%{t}%"
                conditions.append(MemoryEntry.key.ilike(like))
                conditions.append(MemoryEntry.value.ilike(like))
            q = q.filter(or_(*conditions))

        ordered = q.order_by(
            MemoryEntry.updated_at.desc(),
            MemoryEntry.id.desc(),
        )
        rows = ordered.offset(int(offset or 0)).limit(int(page_size)).all()

        out: List[RetrievedMemory] = []
        for r in rows:
            out.append(RetrievedMemory(
                id=r.id, kind=r.kind, scope=r.scope, scope_ref=r.scope_ref,
                key=r.key, value=r.value, confidence=r.confidence,
                verification_status=r.verification_status,
                source_type=r.source_type, observed_at=r.observed_at,
                updated_at=r.updated_at, score=0.0,
            ))
        return out

    def count_items(
        self,
        *,
        query: str = "",
        kinds: Optional[List[str]] = None,
        scopes: Optional[List[str]] = None,
        min_confidence: Optional[float] = None,
        require_verified: bool = False,
        access_context: Any = None,
        context: Any = None,
        memory_access_context: Any = None,
    ) -> int:
        ctx = _coerce_memory_access_context(
            access_context if access_context is not None else
            (context if context is not None else memory_access_context)
        )
        q = self.db.query(MemoryEntry).filter(MemoryEntry.status == "ACTIVE")
        if kinds:
            q = q.filter(MemoryEntry.kind.in_([k.upper() for k in kinds]))
        if ctx is not None:
            from sqlalchemy import or_ as _or
            pairs = ctx.allowed_pairs()
            if scopes is not None:
                allowed_scopes = {str(s).upper() for s in scopes}
                pairs = {(s, r) for (s, r) in pairs if s in allowed_scopes}
            if pairs:
                conditions = []
                for scope, ref in pairs:
                    if scope in ("USER", "GLOBAL"):
                        conditions.append(MemoryEntry.scope == scope)
                    else:
                        conditions.append(
                            (MemoryEntry.scope == scope)
                            & (MemoryEntry.scope_ref == ref)
                        )
                q = q.filter(_or(*conditions))
            else:
                q = q.filter(MemoryEntry.id.is_(None))
        elif scopes:
            q = q.filter(MemoryEntry.scope.in_([s.upper() for s in scopes]))
        if min_confidence is not None:
            q = q.filter(MemoryEntry.confidence >= min_confidence)
        if require_verified:
            q = q.filter(MemoryEntry.verification_status.like("VERIFIED%"))
        tokens = [t for t in re.split(r"[^a-z0-9]+", (query or "").lower()) if t]
        if tokens:
            from sqlalchemy import or_
            conditions = []
            for t in tokens:
                like = f"%{t}%"
                conditions.append(MemoryEntry.key.ilike(like))
                conditions.append(MemoryEntry.value.ilike(like))
            q = q.filter(or_(*conditions))
        return int(q.count())


    # ------------------------------------------------------------------ #
    # Decay                                                                #
    # ------------------------------------------------------------------ #

    def decay_pass(self, now: Optional[datetime] = None) -> Dict[str, int]:
        """Per-kind decay: ASSUMPTION/BELIEF/EPISODIC/UNKNOWN unaccessed past
        their window are ARCHIVED (recoverable), never deleted. High-value
        kinds (FACT/PREFERENCE/INSTRUCTION/PROCEDURAL) never auto-decay."""
        now = now or datetime.utcnow()
        archived = 0
        rows = (
            self.db.query(MemoryEntry)
            .filter(MemoryEntry.status == "ACTIVE")
            .all()
        )
        for r in rows:
            window = DECAY_DAYS.get(r.kind)
            if window is None:
                continue
            last = r.last_accessed_at or r.observed_at or r.created_at
            if last is None:
                continue
            if now - last > timedelta(days=window):
                r.status = "ARCHIVED"
                r.updated_at = now
                archived += 1
        if archived:
            self.ledger.append_event("memory", "memory.decay.pass", {
                "archived": archived,
            })
            self.db.commit()
        return {"archived": archived}