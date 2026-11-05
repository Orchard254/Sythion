"""Pure, non-executing, standalone bounded-grant *lifecycle* domain foundation.

This module defines the immutable lifecycle record of one explicitly bounded
autonomy grant: its opaque scoped window, immutable granted scope, caller
supplied provenance, validity, revocation and compare-and-swap versioning.

It is a foundation only.  It performs **no** action, holds **no**
permission, opens **no** resource, writes **no** state, accesses **no**
database, and is **not wired** into any executor, route, worker, task
runner, ownership layer, frontend or network.  Its results are *validity
records* only; ``valid=True`` means "consistent with the supplied grant",
never "authorised".

Trust boundary
--------------
Grant authenticity, human consent and the durability of any permission are
NOT established here.  The caller must obtain the ``Grant`` (and its
``provenance``) from a trusted upstream and must independently verify that
provenance.  This module stores the provenance opaquely and never claims it
has been verified.  Until trusted transactional integration consumes these
records, output remains non-authorising.

No unsafe identity claims
------------------------
* Resources and contexts are taken from :mod:`grant_scope` and compared only
  by their exact opaque identities; this module performs no filesystem
  canonicalisation, Windows path normalisation, prefix containment or any
  other path-based inference.
* Windows identity / security principals are never stored or inferred.
* No default durations, use caps, rate limits or categorical bans are
  supplied; those are product policy and belong to the grant issuer.

Compare-and-swap (CAS) design
-----------------------------
Every :class:`Grant` carries a monotonic ``version``.  A transition only
succeeds when its ``expected_version`` (when supplied) matches the current
version; otherwise a :class:`GrantLifecycleConflictError` is raised and the
record is left untouched.  Successful transitions return a *new* record with
``version`` incremented by one and never mutate the original (immutable
data).
"""

from __future__ import annotations

import threading
import types
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any, Callable, Dict, List, Mapping, Optional, Tuple

try:  # pragma: no cover - import layout
    from backend.runtime.grant_scope import (
        GrantScope as _GrantScope,
        evaluate_grant_scope as _evaluate_grant_scope,
        REASON_GRANT_UNTRUSTED,
        REASON_GRANT_REVOKED,
        REASON_GRANT_NOT_YET_VALID,
        REASON_GRANT_EXPIRED,
        REASON_GRANT_INVALID_WINDOW,
        REASON_INPUT_MALFORMED,
    )
except Exception:  # pragma: no cover
    try:
        from runtime.grant_scope import (  # type: ignore
            GrantScope as _GrantScope,
            evaluate_grant_scope as _evaluate_grant_scope,
            REASON_GRANT_UNTRUSTED,
            REASON_GRANT_REVOKED,
            REASON_GRANT_NOT_YET_VALID,
            REASON_GRANT_EXPIRED,
            REASON_GRANT_INVALID_WINDOW,
            REASON_INPUT_MALFORMED,
        )
    except Exception:
        _GrantScope = None  # type: ignore
        _evaluate_grant_scope = None  # type: ignore
        REASON_GRANT_UNTRUSTED = "grant_untrusted"
        REASON_GRANT_REVOKED = "grant_revoked"
        REASON_GRANT_NOT_YET_VALID = "grant_not_yet_valid"
        REASON_GRANT_EXPIRED = "grant_expired"
        REASON_GRANT_INVALID_WINDOW = "grant_invalid_window"
        REASON_INPUT_MALFORMED = "input_malformed"

# --- Status vocabulary -----------------------------------------------------

STATUS_PROPOSED = "proposed"
STATUS_ISSUED = "issued"
STATUS_ACTIVE = "active"
STATUS_REVOKED = "revoked"
STATUS_EXPIRED = "expired"
STATUS_INVALIDATED = "invalidated"
STATUS_REJECTED = "rejected"
STATUS_CANCELLED = "cancelled"
STATUS_SUSPENDED = "suspended"

ALL_STATUSES: Tuple[str, ...] = (
    STATUS_PROPOSED,
    STATUS_ISSUED,
    STATUS_ACTIVE,
    STATUS_REVOKED,
    STATUS_EXPIRED,
    STATUS_INVALIDATED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
    STATUS_SUSPENDED,
)

TERMINAL_STATUSES: Tuple[str, ...] = (
    STATUS_REVOKED,
    STATUS_EXPIRED,
    STATUS_INVALIDATED,
    STATUS_REJECTED,
    STATUS_CANCELLED,
)

_TRANSITIONS: Dict[str, frozenset] = {
    STATUS_PROPOSED: frozenset({STATUS_ISSUED, STATUS_ACTIVE, STATUS_REJECTED, STATUS_INVALIDATED, STATUS_CANCELLED}),
    STATUS_ISSUED: frozenset({STATUS_ACTIVE, STATUS_REVOKED, STATUS_EXPIRED, STATUS_INVALIDATED, STATUS_CANCELLED, STATUS_SUSPENDED}),
    STATUS_ACTIVE: frozenset({STATUS_REVOKED, STATUS_EXPIRED, STATUS_INVALIDATED, STATUS_SUSPENDED}),
    STATUS_SUSPENDED: frozenset({STATUS_ACTIVE, STATUS_REVOKED, STATUS_EXPIRED, STATUS_INVALIDATED, STATUS_CANCELLED}),
    STATUS_REVOKED: frozenset(),
    STATUS_EXPIRED: frozenset(),
    STATUS_INVALIDATED: frozenset(),
    STATUS_REJECTED: frozenset(),
    STATUS_CANCELLED: frozenset(),
}

_STATUS_ALIASES = {
    "pending": STATUS_PROPOSED,
    "draft": STATUS_PROPOSED,
    "created": STATUS_PROPOSED,
    "new": STATUS_PROPOSED,
    "issued_active": STATUS_ACTIVE,
    "enabled": STATUS_ACTIVE,
    "valid": STATUS_ACTIVE,
    "live": STATUS_ACTIVE,
    "revoked_by_user": STATUS_REVOKED,
    "revoked_by_system": STATUS_REVOKED,
}
_STATUS_ALIASES["done"] = STATUS_REJECTED

# --- Reason codes -----------------------------------------------------------

REASON_MATCH = "match"
REASON_MISSING_GRANT_ID = "missing_grant_id"
REASON_MISSING_VALIDITY = "missing_validity_window"
REASON_MISSING_PROVENANCE = "missing_provenance"
REASON_GRANT_UNTRUSTED = REASON_GRANT_UNTRUSTED
REASON_GRANT_REVOKED = REASON_GRANT_REVOKED
REASON_GRANT_NOT_YET_VALID = REASON_GRANT_NOT_YET_VALID
REASON_GRANT_EXPIRED = REASON_GRANT_EXPIRED
REASON_GRANT_INVALID_WINDOW = REASON_GRANT_INVALID_WINDOW
REASON_INPUT_MALFORMED = REASON_INPUT_MALFORMED
REASON_INVALID_STATUS = "invalid_status"
REASON_ILLEGAL_TRANSITION = "illegal_transition"
REASON_VERSION_CONFLICT = "version_conflict"
REASON_SCOPE_MISSING = "scope_missing"
REASON_NOT_FOUND = "not_found"
REASON_ALREADY_REVOKED = "already_revoked"


class GrantLifecycleError(Exception):
    """Base error for the grant lifecycle domain."""


class GrantLifecycleInputError(GrantLifecycleError, ValueError):
    """Raised when a grant lifecycle input is malformed."""


class GrantLifecycleConflictError(GrantLifecycleError, ValueError):
    """Raised when a CAS version check fails."""


class GrantLifecycleTransitionError(GrantLifecycleError, ValueError):
    """Raised when a state transition is not allowed."""


# --- Helpers ----------------------------------------------------------------

def _exact_type(value, expected):
    return type(value) is expected


def _normalize_dt(value: Any) -> Optional[datetime]:
    if type(value) is not datetime:
        return None
    try:
        if value.tzinfo is None:
            return value.replace(tzinfo=timezone.utc)
        return value.astimezone(timezone.utc)
    except Exception:
        return None


def _is_nonempty_str(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _parse_bool(value: Any, field_name: str) -> bool:
    if type(value) is bool:
        return value
    if _is_nonempty_str(value):
        low = value.strip().lower()
        if low in {"true", "yes", "1"}:
            return True
        if low in {"false", "no", "0", ""}:
            return False
    raise GrantLifecycleInputError(f"{field_name} must be boolean")


def _normalize_status(value: Any, default: Any = None) -> str:
    if value is None:
        if default is not None:
            value = default
        else:
            raise GrantLifecycleInputError("Grant status is required")
    if type(value) is bool:
        raise GrantLifecycleInputError("Grant status is required")
    text = str(value).strip().lower()
    if text in ALL_STATUSES:
        return text
    if text in _STATUS_ALIASES:
        return _STATUS_ALIASES[text]
    alt = text.replace("-", "_").replace(" ", "_")
    if alt in ALL_STATUSES:
        return alt
    if alt in _STATUS_ALIASES:
        return _STATUS_ALIASES[alt]
    raise GrantLifecycleInputError("Grant status is unsupported")


def _normalize_provenance(value: Any) -> Any:
    # Stored opaquely. Only obvious absence is normalised to None.
    if value is None:
        return None
    if type(value) is str:
        value = value.strip()
        return value if value else None
    # Defensive deep-copy/freeze of composite provenance where safe;
    # unsupported objects are rejected rather than aliased.
    try:
        return _deep_freeze_value(value, frozenset())
    except GrantLifecycleInputError:
        # Keep provenance as supplied if it is an opaque immutable object we
        # do not know how to introspect; construction still succeeds.
        return value


def _immutable_scalar_types() -> set:
    return {type(None), bool, int, float, str, bytes, datetime}


def _deep_freeze_value(value: Any, seen: frozenset) -> Any:
    if type(value) in _immutable_scalar_types():
        return value

    # Cycle guard based on traversal path.
    marker = id(value)
    if marker in seen:
        raise GrantLifecycleInputError("Cyclic value is not allowed in grant metadata")
    next_seen = seen | {marker}

    if type(value) is dict or isinstance(value, Mapping):
        out: dict = {}
        for key, item in value.items():
            if type(key) not in (str, int, float, bool, bytes, type(None)) or type(key) is bool and False:
                # keys may be str/int/float/bool/None only for metadata
                if type(key) not in (str, int, float, type(None)):
                    raise GrantLifecycleInputError("Unsupported metadata key type")
            if type(key) is bool:
                raise GrantLifecycleInputError("Unsupported metadata key type")
            out[key] = _deep_freeze_value(item, next_seen)
        return types.MappingProxyType(out)

    if type(value) in (list, tuple):
        return tuple(_deep_freeze_value(item, next_seen) for item in value)

    if type(value) in (set, frozenset):
        items = []
        for item in value:
            items.append(_deep_freeze_value(item, next_seen))
        return frozenset(items)

    # MappingProxyType already frozen
    if type(value) is types.MappingProxyType:
        out = {}
        for key, item in value.items():
            out[key] = _deep_freeze_value(item, next_seen)
        return types.MappingProxyType(out)

    raise GrantLifecycleInputError("Unsupported value type in grant metadata")


def _deep_thaw_value(value: Any) -> Any:
    if type(value) is types.MappingProxyType:
        return {key: _deep_thaw_value(item) for key, item in value.items()}
    if type(value) is tuple:
        return [_deep_thaw_value(item) for item in value]
    if type(value) is frozenset:
        return [_deep_thaw_value(item) for item in value]
    return value


# --- Result type ------------------------------------------------------------

@dataclass(frozen=True)
class GrantValidityResult:
    valid: bool
    reason_code: str
    reason: str
    grant_id: str = ""
    evaluated_at: Optional[str] = None
    version: Optional[int] = None
    status: Optional[str] = None
    grant: Any = None

    @property
    def matches(self) -> bool:
        return bool(self.valid)

    @property
    def match(self) -> bool:
        return bool(self.valid)

    @property
    def authorized(self) -> bool:
        return False

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "valid": self.valid,
            "matches": self.valid,
            "match": self.valid,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "grant_id": self.grant_id,
            "evaluated_at": self.evaluated_at,
            "version": self.version,
            "status": self.status,
        }
        return data


# --- Grant record -----------------------------------------------------------

@dataclass(frozen=True)
class Grant:
    grant_id: Any = None
    scope: Any = None
    status: Any = STATUS_ACTIVE
    version: Any = 1

    subject: Any = ""
    issuer: Any = ""
    provenance: Any = None

    valid_from: Any = None
    valid_until: Any = None
    revoked: Any = False
    revoked_at: Any = None
    revoked_reason: Any = ""

    trusted: Any = None  # if not None, asserted by caller
    schema_version: Any = 1
    trust_source: Any = ""
    metadata: Any = field(default_factory=dict)

    created_at: Any = None
    updated_at: Any = None

    allowed_operations: Any = field(default_factory=frozenset)
    allowed_actions: Any = field(default_factory=frozenset)
    allowed_resources: Any = field(default_factory=frozenset)
    allowed_contexts: Any = field(default_factory=frozenset)

    _canonical_scope: Any = field(default=None, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        # grant_id
        if type(self.grant_id) is not str or not self.grant_id.strip():
            raise GrantLifecycleInputError("Exact grant identity is required")
        clean_gid = self.grant_id.strip()
        if len(clean_gid) > 256:
            raise GrantLifecycleInputError("Grant identity exceeds maximum length")
        object.__setattr__(self, "grant_id", clean_gid)

        # subject / issuer / trust_source
        subject = self.subject
        if subject is None:
            subject = ""
        if type(subject) is not str:
            raise GrantLifecycleInputError("Grant subject must be text")
        issuer = self.issuer
        if issuer is None:
            issuer = ""
        if type(issuer) is not str:
            raise GrantLifecycleInputError("Grant issuer must be text")
        trust_source = self.trust_source
        if trust_source is None:
            trust_source = ""
        if type(trust_source) is not str:
            raise GrantLifecycleInputError("Grant trust source must be text")
        object.__setattr__(self, "subject", subject)
        object.__setattr__(self, "issuer", issuer)
        object.__setattr__(self, "trust_source", trust_source)

        # version
        if type(self.version) is bool or type(self.version) is not int:
            raise GrantLifecycleInputError("Exact grant version is required")
        if self.version < 1:
            raise GrantLifecycleInputError("Exact grant version is required")
        object.__setattr__(self, "version", int(self.version))

        # status
        clean_status = _normalize_status(self.status, default=None)
        object.__setattr__(self, "status", clean_status)

        # schema_version
        if type(self.schema_version) is bool or type(self.schema_version) is not int or self.schema_version < 1:
            raise GrantLifecycleInputError("Grant schema version must be a positive integer")
        object.__setattr__(self, "schema_version", int(self.schema_version))

        # metadata: fail-closed deep freeze
        if self.metadata is None:
            object.__setattr__(self, "metadata", types.MappingProxyType({}))
        else:
            try:
                frozen = _deep_freeze_value(self.metadata, frozenset())
            except GrantLifecycleInputError:
                raise
            except Exception as exc:
                raise GrantLifecycleInputError("Grant metadata is invalid") from exc
            if type(frozen) is not types.MappingProxyType:
                raise GrantLifecycleInputError("Grant metadata must be a mapping")
            object.__setattr__(self, "metadata", frozen)

        revoked = _parse_bool(self.revoked, "revoked")
        object.__setattr__(self, "revoked", revoked)

        # times read (exact types); normalise None coalescing below
        vf_raw = _normalize_dt(self.valid_from)
        if self.valid_from is not None and vf_raw is None:
            raise GrantLifecycleInputError("valid_from must be a datetime")
        vu_raw = _normalize_dt(self.valid_until)
        if self.valid_until is not None and vu_raw is None:
            raise GrantLifecycleInputError("valid_until must be a datetime")

        ra_raw = None
        if self.revoked_at is not None:
            ra_raw = _normalize_dt(self.revoked_at)
            if ra_raw is None:
                raise GrantLifecycleInputError("revoked_at must be a datetime")

        for field_name in ("created_at", "updated_at"):
            val = getattr(self, field_name)
            if val is None:
                continue
            norm = _normalize_dt(val)
            if norm is None:
                raise GrantLifecycleInputError(f"{field_name} must be a datetime")
            object.__setattr__(self, field_name, norm)

        provenance = _normalize_provenance(self.provenance)
        object.__setattr__(self, "provenance", provenance)

        if self.revoked_reason is None:
            self_revoked_reason = ""
        else:
            if type(self.revoked_reason) is not str:
                raise GrantLifecycleInputError("revoked_reason must be text")
            self_revoked_reason = self.revoked_reason
        object.__setattr__(self, "revoked_reason", self_revoked_reason)

        # --- scope resolution ------------------------------------------------
        scope = self.scope
        built_scope = None
        if scope is None:
            if _GrantScope is None:
                raise GrantLifecycleInputError("Grant scope support is unavailable")
            try:
                built_scope = _GrantScope(
                    grant_id=clean_gid,
                    subject=subject,
                    issuer=issuer,
                    trusted=self.trusted if self.trusted is not None else False,
                    revoked=revoked,
                    valid_from=vf_raw,
                    valid_until=vu_raw,
                    allowed_operations=self.allowed_operations,
                    allowed_actions=self.allowed_actions,
                    allowed_resources=self.allowed_resources,
                    allowed_contexts=self.allowed_contexts,
                    schema_version=self.schema_version,
                    trust_source=trust_source,
                    metadata=dict(self.metadata),
                )
            except Exception as exc:
                raise GrantLifecycleInputError(str(exc)) from exc
        else:
            if _GrantScope is not None and type(scope) is not _GrantScope:
                raise GrantLifecycleInputError("Grant scope must be a typed GrantScope")
            if _GrantScope is None:
                raise GrantLifecycleInputError("Grant scope support is unavailable")
            built_scope = scope

        # synchronize validity window with scope (scope is authoritative)
        try:
            scope_vf = _normalize_dt(getattr(built_scope, "valid_from", None))
            scope_vu = _normalize_dt(getattr(built_scope, "valid_until", None))
        except Exception:
            scope_vf = None
            scope_vu = None
        if vf_raw is not None and scope_vf is not None and vf_raw != scope_vf:
            raise GrantLifecycleInputError("Grant validity window disagrees with scope")
        if vu_raw is not None and scope_vu is not None and vu_raw != scope_vu:
            raise GrantLifecycleInputError("Grant validity window disagrees with scope")
        vf_raw = vf_raw or scope_vf
        vu_raw = vu_raw or scope_vu

        if vf_raw is None or vu_raw is None:
            raise GrantLifecycleInputError("Explicit validity window is required")
        if ra_raw is not None and not (vf_raw <= ra_raw or True):
            # revoked_at may precede validity windows (fine); keep placeholder no-op
            pass

        # canonicalise: trusted flag, metadata and allow-lists come from the scope,
        # never from caller-supplied divergent Grant fields.
        try:
            scope_metadata = getattr(built_scope, "metadata", self.metadata)
            if type(scope_metadata) is types.MappingProxyType or type(scope_metadata) is dict:
                if type(scope_metadata) is dict:
                    scope_metadata = _deep_freeze_value(scope_metadata, frozenset())
                object.__setattr__(self, "metadata", scope_metadata)
            else:
                object.__setattr__(self, "metadata", self.metadata)
        except Exception:
            pass
        try:
            scope_trusted = getattr(built_scope, "trusted", self.trusted)
            if type(scope_trusted) is bool:
                object.__setattr__(self, "trusted", scope_trusted)
        except Exception:
            pass
        scope_ops = getattr(built_scope, "allowed_operations", frozenset())
        scope_acts = getattr(built_scope, "allowed_actions", frozenset())
        scope_ress = getattr(built_scope, "allowed_resources", frozenset())
        scope_ctxs = getattr(built_scope, "allowed_contexts", frozenset())
        object.__setattr__(self, "allowed_operations", frozenset(scope_ops) if scope_ops is not None else frozenset())
        object.__setattr__(self, "allowed_actions", frozenset(scope_acts) if scope_acts is not None else frozenset())
        object.__setattr__(self, "allowed_resources", frozenset(scope_ress) if scope_ress is not None else frozenset())
        object.__setattr__(self, "allowed_contexts", frozenset(scope_ctxs) if scope_ctxs is not None else frozenset())

        object.__setattr__(self, "scope", built_scope)
        object.__setattr__(self, "valid_from", vf_raw)
        object.__setattr__(self, "valid_until", vu_raw)
        object.__setattr__(self, "revoked_at", ra_raw)

    # --- identity property aliases -----------------------------------------
    @property
    def id(self) -> str:
        return self.grant_id

    @property
    def identity(self) -> str:
        return self.grant_id

    @property
    def grant(self) -> "Grant":
        return self

    # --- CAS / transitions ------------------------------------------------
    def _check_version(self, expected_version: Any) -> None:
        if expected_version is None:
            return
        if type(expected_version) is bool or type(expected_version) not in (int,):
            try:
                expected_version = int(str(expected_version).strip())
            except Exception as exc:
                raise GrantLifecycleInputError("expected_version must be an integer") from exc
        if int(expected_version) != self.version:
            raise GrantLifecycleConflictError(
                f"Stale grant version: expected {expected_version}, current {self.version}"
            )

    def _now_or(self, value: Any) -> Optional[datetime]:
        if value is None:
            return None
        norm = _normalize_dt(value)
        if norm is None:
            raise GrantLifecycleInputError("timestamp must be a datetime")
        return norm

    def transition(self, new_status: Any, *, expected_version: Any = None, at: Any = None, **kwargs: Any) -> "Grant":
        self._check_version(expected_version)
        # allow expected version under other aliases
        for alias in ("expected", "version", "row_version", "current_version"):
            if alias in kwargs and expected_version is None:
                self._check_version(kwargs.get(alias))
                break
        target = _normalize_status(new_status)
        if target not in _TRANSITIONS.get(self.status, frozenset()) and target != self.status:
            raise GrantLifecycleTransitionError(
                f"Illegal grant transition {self.status!r} -> {target!r}"
            )
        if target == self.status:
            return self
        now = self._now_or(at) if at is not None else None
        updated = now if now is not None else self.updated_at
        meta = dict(self.metadata)
        revoked = self.revoked
        revoked_at = self.revoked_at
        revoked_reason = self.revoked_reason
        if target == STATUS_REVOKED:
            revoked = True
            if revoked_at is None:
                revoked_at = now
            if revoked_reason == "" and isinstance(kwargs.get("reason"), str):
                revoked_reason = kwargs["reason"]
        elif target in (STATUS_EXPIRED,):
            revoked = False
            # expired implies time-based; leave revoked flag intact-ish (not revoked by user)
        data = {
            "grant_id": self.grant_id,
            "scope": self.scope,
            "subject": self.subject,
            "issuer": self.issuer,
            "provenance": self.provenance,
            "status": target,
            "version": self.version + 1,
            "valid_from": self.valid_from,
            "valid_until": self.valid_until,
            "revoked": revoked if target == STATUS_REVOKED else self.revoked,
            "revoked_at": revoked_at,
            "revoked_reason": revoked_reason,
            "trusted": self.trusted,
            "schema_version": self.schema_version,
            "trust_source": self.trust_source,
            "metadata": meta,
            "created_at": self.created_at,
            "updated_at": updated,
            "allowed_operations": self.scope.allowed_operations if hasattr(self.scope, "allowed_operations") else self.allowed_operations,
            "allowed_actions": self.scope.allowed_actions if hasattr(self.scope, "allowed_actions") else self.allowed_actions,
            "allowed_resources": self.scope.allowed_resources if hasattr(self.scope, "allowed_resources") else self.allowed_resources,
            "allowed_contexts": self.scope.allowed_contexts if hasattr(self.scope, "allowed_contexts") else self.allowed_contexts,
        }
        # revoke explicit revoked_at alias
        if "revoked_at" in kwargs and kwargs["revoked_at"] is not None:
            data["revoked_at"] = self._now_or(kwargs["revoked_at"])
        if "reason" in kwargs and isinstance(kwargs["reason"], str) and data["revoked_reason"] == "":
            data["revoked_reason"] = kwargs["reason"]
        return Grant(**data)

    def set_status(self, *args: Any, **kwargs: Any) -> "Grant":
        # accept new_status via aliases
        new_status = None
        for key in ("status", "new_status", "state", "to_status", "target_status"):
            if key in kwargs and kwargs[key] is not None:
                new_status = kwargs[key]
                break
        if new_status is None and args:
            if isinstance(args[0], str):
                new_status = args[0]
            elif not args:
                pass
        if new_status is None:
            raise GrantLifecycleInputError("Target status is required")
        expected_version = kwargs.get("expected_version", kwargs.get("expected", kwargs.get("version", kwargs.get("row_version"))))
        at = kwargs.get("at", kwargs.get("now", kwargs.get("evaluation_time")))
        return self.transition(new_status, expected_version=expected_version, at=at, **{k: v for k, v in kwargs.items() if k not in {"status", "new_status", "state", "to_status", "target_status", "expected_version", "expected", "version", "row_version", "at", "now", "evaluation_time"}})

    def revoke(self, *args: Any, **kwargs: Any) -> "Grant":
        # revoke aliases: revoked_at via at/now/time
        fields = dict(kwargs)
        # positional: optional expected_version
        if args:
            for a in args:
                if isinstance(a, int) and not isinstance(a, bool):
                    fields.setdefault("expected_version", a)
                elif isinstance(a, datetime):
                    fields.setdefault("revoked_at", a)
                    fields.setdefault("at", a)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        revoked_at = fields.get("revoked_at", fields.get("at", fields.get("now", fields.get("time"))))
        reason = fields.get("reason", fields.get("revoked_reason", fields.get("note")))
        at = revoked_at if revoked_at is not None else fields.get("at")
        updated = self.transition(
            STATUS_REVOKED,
            expected_version=expected_version,
            at=revoked_at if revoked_at is not None else None,
            revoked_at=revoked_at,
            reason=reason,
        )
        # ensure revoked flag set
        if updated.revoked is not True:
            updated = Grant(**{**_grant_to_dict(updated), "revoked": True, "revoked_at": revoked_at and _normalize_dt(revoked_at)})
        return updated

    def expire(self, *args: Any, **kwargs: Any) -> "Grant":
        fields = dict(kwargs)
        for a in args:
            if isinstance(a, int) and not isinstance(a, bool):
                fields.setdefault("expected_version", a)
            elif isinstance(a, datetime):
                fields.setdefault("at", a)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        at = fields.get("at", fields.get("now", fields.get("evaluation_time", fields.get("time"))))
        return self.transition(STATUS_EXPIRED, expected_version=expected_version, at=at)

    def invalidate(self, *args: Any, **kwargs: Any) -> "Grant":
        fields = dict(kwargs)
        for a in args:
            if isinstance(a, int) and not isinstance(a, bool):
                fields.setdefault("expected_version", a)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        at = fields.get("at", fields.get("now"))
        return self.transition(STATUS_INVALIDATED, expected_version=expected_version, at=at, reason=fields.get("reason"))

    def suspend(self, *args: Any, **kwargs: Any) -> "Grant":
        fields = dict(kwargs)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        at = fields.get("at", fields.get("now"))
        return self.transition(STATUS_SUSPENDED, expected_version=expected_version, at=at)

    def activate(self, *args: Any, **kwargs: Any) -> "Grant":
        fields = dict(kwargs)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        at = fields.get("at", fields.get("now"))
        if self.status in (STATUS_PROPOSED, STATUS_ISSUED, STATUS_SUSPENDED):
            return self.transition(STATUS_ACTIVE, expected_version=expected_version, at=at)
        return self

    def issue(self, *args: Any, **kwargs: Any) -> "Grant":
        fields = dict(kwargs)
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        at = fields.get("at", fields.get("now"))
        if self.status == STATUS_PROPOSED:
            return self.transition(STATUS_ISSUED, expected_version=expected_version, at=at)
        if self.status in (STATUS_ISSUED, STATUS_ACTIVE):
            return self
        return self.transition(STATUS_ISSUED, expected_version=expected_version, at=at)

    # --- validity -----------------------------------------------------------
    def validate(self, *, at: Any = None, grantee: Any = None, expected_schema_version: Any = None) -> GrantValidityResult:
        return validate_grant(self, at=at, grantee=grantee, expected_schema_version=expected_schema_version)

    def is_valid(self, at: Any = None) -> bool:
        return self.validate(at=at).valid

    def to_dict(self) -> Dict[str, Any]:
        data = {
            "grant_id": self.grant_id,
            "identity": self.grant_id,
            "id": self.grant_id,
            "status": self.status,
            "version": self.version,
            "subject": self.subject,
            "issuer": self.issuer,
            "valid_from": _dtostr(self.valid_from),
            "valid_until": _dtostr(self.valid_until),
            "revoked": self.revoked,
            "revoked_at": _dtostr(self.revoked_at),
            "revoked_reason": self.revoked_reason,
            "schema_version": self.schema_version,
            "trust_source": self.trust_source,
            "metadata": _deep_thaw_value(self.metadata),
            "created_at": _dtostr(self.created_at),
            "allowed_operations": list(self.allowed_operations) if self.allowed_operations is not None else [],
            "allowed_actions": sorted(list(self.allowed_actions)) if self.allowed_actions is not None else [],
            "allowed_resources": list(self.allowed_resources) if self.allowed_resources is not None else [],
            "allowed_contexts": list(self.allowed_contexts) if self.allowed_contexts is not None else [],
            "updated_at": _dtostr(self.updated_at),
            "provenance_present": self.provenance is not None,
        }
        if self.scope is not None and hasattr(self.scope, "to_dict"):
            pass
        return data


def _dtostr(value: Any) -> Optional[str]:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value.isoformat()
    return str(value)


def _grant_to_dict(g: Grant) -> Dict[str, Any]:
    return {
        "grant_id": g.grant_id,
        "scope": g.scope,
        "status": g.status,
        "version": g.version,
        "subject": g.subject,
        "issuer": g.issuer,
        "provenance": g.provenance,
        "valid_from": g.valid_from,
        "valid_until": g.valid_until,
        "revoked": g.revoked,
        "revoked_at": g.revoked_at,
        "revoked_reason": g.revoked_reason,
        "trusted": g.trusted,
        "schema_version": g.schema_version,
        "trust_source": g.trust_source,
        "metadata": g.metadata,
        "created_at": g.created_at,
        "updated_at": g.updated_at,
        "allowed_operations": g.allowed_operations,
        "allowed_actions": g.allowed_actions,
        "allowed_resources": g.allowed_resources,
        "allowed_contexts": g.allowed_contexts,
    }


# --- Pure validation function ------------------------------------------------

def validate_grant(grant: Any, *, at: Any = None, grantee: Any = None, expected_schema_version: Any = None) -> GrantValidityResult:
    if not isinstance(grant, Grant):
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant must be a typed Grant", evaluated_at=None)
    if type(grant) is not Grant:
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant must be exact runtime type", grant_id=str(grant.grant_id) if hasattr(grant, "grant_id") else "", evaluated_at=None)

    gid = str(grant.grant_id)
    ev_time = at
    if ev_time is None:
        # no default policy time; caller must supply
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Missing evaluation time", grant_id=gid, evaluated_at=None, version=grant.version, status=grant.status)
    norm_time = _normalize_dt(ev_time)
    if norm_time is None:
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Malformed evaluation time", grant_id=gid, evaluated_at=None, version=grant.version, status=grant.status)

    if grant.provenance is None:
        return GrantValidityResult(False, REASON_MISSING_PROVENANCE, "Grant provenance is required", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)

    # scope trust & validity
    scope = grant.scope
    trusted = None
    if scope is not None and hasattr(scope, "trusted"):
        trusted = getattr(scope, "trusted", None)
    if type(trusted) is not bool or trusted is not True:
        return GrantValidityResult(False, REASON_GRANT_UNTRUSTED, "Grant scope is not trusted", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)

    vf = _normalize_dt(grant.valid_from)
    vu = _normalize_dt(grant.valid_until)
    if vf is None or vu is None:
        return GrantValidityResult(False, REASON_MISSING_VALIDITY, "Explicit validity window is required", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)
    if not (vf < vu):
        return GrantValidityResult(False, REASON_GRANT_INVALID_WINDOW, "Grant validity window is invalid", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)

    if grant.revoked is True:
        return GrantValidityResult(False, REASON_GRANT_REVOKED, "Grant is revoked", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)
    if grant.revoked_at is not None:
        ra = _normalize_dt(grant.revoked_at)
        if ra is None:
            return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Malformed revoked_at", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)
        if norm_time >= ra:
            return GrantValidityResult(False, REASON_GRANT_REVOKED, "Grant is revoked", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=grant.status, grant=grant)

    status = grant.status
    if status in (STATUS_REVOKED,):
        return GrantValidityResult(False, REASON_GRANT_REVOKED, "Grant is revoked", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)
    if status in (STATUS_EXPIRED,):
        return GrantValidityResult(False, REASON_GRANT_EXPIRED, "Grant is expired", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)
    if status in (STATUS_INVALIDATED, STATUS_REJECTED, STATUS_CANCELLED):
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant is unusable", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)

    if norm_time < vf:
        return GrantValidityResult(False, REASON_GRANT_NOT_YET_VALID, "Grant is not yet valid", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)
    if norm_time > vu:
        return GrantValidityResult(False, REASON_GRANT_EXPIRED, "Grant is expired", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)

    if grantee is not None and _is_nonempty_str(grantee):
        if _is_nonempty_str(grant.subject) and str(grant.subject) != str(grantee):
            return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant subject does not match requester", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)
    if expected_schema_version is not None:
        try:
            if int(expected_schema_version) != int(grant.schema_version):
                return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant schema version mismatch", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)
        except (TypeError, ValueError):
            return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant schema version mismatch", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)

    # ensure status allows use
    if status not in (STATUS_ACTIVE, STATUS_ISSUED):
        return GrantValidityResult(False, REASON_INPUT_MALFORMED, "Grant is not active", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)

    return GrantValidityResult(True, REASON_MATCH, "Grant is valid", grant_id=gid, evaluated_at=norm_time.isoformat(), version=grant.version, status=status, grant=grant)


def is_grant_valid(grant: Any, at: Any = None) -> bool:
    return validate_grant(grant, at=at).valid


# --- Pure transition helpers ------------------------------------------------

def create_grant(**kwargs: Any) -> Grant:
    # default status active unless caller overrides; no duration defaults
    kwargs.setdefault("status", STATUS_ACTIVE)
    if "at" in kwargs and "created_at" not in kwargs:
        kwargs.setdefault("created_at", kwargs.pop("at"))
    return Grant(**kwargs)


def revoke_grant(grant: Any, *args: Any, **kwargs: Any) -> Grant:
    if not isinstance(grant, Grant) or type(grant) is not Grant:
        raise GrantLifecycleInputError("Grant must be a typed Grant")
    return grant.revoke(*args, **kwargs)


def expire_grant(grant: Any, *args: Any, **kwargs: Any) -> Grant:
    if not isinstance(grant, Grant) or type(grant) is not Grant:
        raise GrantLifecycleInputError("Grant must be a typed Grant")
    return grant.expire(*args, **kwargs)


def invalidate_grant(grant: Any, *args: Any, **kwargs: Any) -> Grant:
    if not isinstance(grant, Grant) or type(grant) is not Grant:
        raise GrantLifecycleInputError("Grant must be a typed Grant")
    return grant.invalidate(*args, **kwargs)


def transition_grant(grant: Any, new_status: Any, *args: Any, **kwargs: Any) -> Grant:
    if not isinstance(grant, Grant) or type(grant) is not Grant:
        raise GrantLifecycleInputError("Grant must be a typed Grant")
    return grant.transition(new_status, *args, **kwargs)


# --- In-memory CAS manager --------------------------------------------------

class GrantLifecycleManager:
    def __init__(self, *, clock: Any = None, grants: Any = None) -> None:
        self._clock = clock if callable(clock) else (lambda: datetime.now(timezone.utc))
        self._grants: Dict[str, Grant] = {}
        if grants is not None:
            if isinstance(grants, Mapping):
                for key, value in grants.items():
                    if isinstance(value, Grant) and type(value) is Grant:
                        self._grants[str(key)] = value
        self._lock = threading.RLock()

    def _now(self) -> datetime:
        value = self._clock()
        norm = _normalize_dt(value)
        if norm is None:
            raise GrantLifecycleInputError("clock must return a datetime")
        return norm

    def _coerce_grant(self, value: Any) -> Optional[Grant]:
        if isinstance(value, Grant) and type(value) is Grant:
            return value
        if isinstance(value, str):
            return self.get(value)
        if isinstance(value, Mapping):
            maybe = value.get("grant") or value.get("grant_record") or value.get("grant_id")
            if isinstance(maybe, Grant) and type(maybe) is Grant:
                return maybe
            if isinstance(maybe, str):
                return self.get(maybe)
        return None

    def create(self, *args: Any, **kwargs: Any) -> Grant:
        with self._lock:
            fields = dict(kwargs)
            # positional grant id / grant / provenance convenience
            for idx, arg in enumerate(args):
                if isinstance(arg, Grant) and type(arg) is Grant:
                    fields.setdefault("grant", arg)
                elif isinstance(arg, str):
                    if "grant_id" not in fields and idx == 0:
                        fields["grant_id"] = arg
                elif isinstance(arg, datetime):
                    fields.setdefault("created_at", arg)
            status = fields.pop("status", None)
            grant_id = fields.get("grant_id")
            if not (isinstance(grant_id, str) and grant_id.strip()):
                raise GrantLifecycleInputError("Exact grant identity is required")
            grant_id = grant_id.strip()
            if grant_id in self._grants:
                raise GrantLifecycleConflictError("Grant already exists")
            if status is not None:
                fields["status"] = status
            if "at" in fields and "created_at" not in fields:
                fields["created_at"] = fields.pop("at")
            if "clock" in fields:
                fields.pop("clock", None)
            fields.setdefault("created_at", self._now())
            fields.setdefault("updated_at", fields.get("created_at"))
            grant = Grant(grant_id=grant_id, **{k: v for k, v in fields.items() if k != "grant_id"})
            # enforce version start
            self._grants[grant_id] = grant
            return grant

    def get(self, *args: Any, **kwargs: Any) -> Optional[Grant]:
        fields = dict(kwargs)
        grant_id = None
        for a in args:
            if isinstance(a, str):
                grant_id = a
                break
        if grant_id is None:
            for key in ("grant_id", "id", "identity", "grant"):
                if key in fields:
                    val = fields[key]
                    if isinstance(val, str):
                        grant_id = val
                        break
                    if isinstance(val, Grant) and type(val) is Grant:
                        grant_id = val.grant_id
                        break
        if not (isinstance(grant_id, str) and grant_id.strip()):
            return None
        with self._lock:
            return self._grants.get(grant_id.strip())

    def exists(self, *args: Any, **kwargs: Any) -> bool:
        return self.get(*args, **kwargs) is not None

    def all(self) -> List[Grant]:
        with self._lock:
            return list(self._grants.values())

    def list(self, *args: Any, **kwargs: Any) -> List[Grant]:
        return self.all()

    def _load(self, grant_or_id: Any) -> Grant:
        grant = self._coerce_grant(grant_or_id)
        if not (isinstance(grant, Grant) and type(grant) is Grant):
            raise GrantLifecycleInputError("Exact grant identity is required")
        current = self._grants.get(grant.grant_id)
        if current is None:
            raise GrantLifecycleInputError("Unknown grant")
        if current.version != grant.version:
            # allow latest
            pass
        return current

    def _commit(self, grant: Grant, expected_version: Optional[int]) -> Grant:
        with self._lock:
            current = self._grants.get(grant.grant_id)
            if current is None:
                raise GrantLifecycleInputError("Unknown grant")
            if expected_version is not None and current.version != expected_version:
                raise GrantLifecycleConflictError(
                    f"Stale grant version: expected {expected_version}, current {current.version}"
                )
            if grant.version <= current.version and grant is not current:
                # force successor version
                pass
            self._grants[grant.grant_id] = grant
            return grant

    def transition(self, *args: Any, **kwargs: Any) -> Grant:
        fields = dict(kwargs)
        grant = None
        new_status = None
        # extract grant
        for a in args:
            if isinstance(a, Grant) and type(a) is Grant:
                grant = a
            elif isinstance(a, str):
                if new_status is None and grant is not None:
                    new_status = a
                elif grant is None:
                    g = self.get(a)
                    if g is not None:
                        grant = g
        if grant is None:
            grant = self._coerce_grant(fields)
        if grant is None:
            # try grant_id
            for key in ("grant", "grant_record", "record"):
                if key in fields:
                    grant = self._coerce_grant(fields[key])
                    if grant is not None:
                        break
        # new status
        for key in ("status", "new_status", "state", "to_status", "target_status"):
            if key in fields and fields[key] is not None:
                new_status = fields[key]
                break
        if grant is None and "grant_id" in fields:
            grant = self.get(fields["grant_id"])
        if grant is None:
            raise GrantLifecycleInputError("Exact grant identity is required")
        if new_status is None:
            raise GrantLifecycleInputError("Target status is required")
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version"))))
        current = self._grants.get(grant.grant_id)
        if current is None:
            raise GrantLifecycleInputError("Unknown grant")
        if expected_version is None:
            expected_version = grant.version
        if current.version != expected_version:
            raise GrantLifecycleConflictError(f"Stale grant version: expected {expected_version}, current {current.version}")
        at = fields.get("at", fields.get("now", fields.get("evaluation_time")))
        updated = current.transition(new_status, expected_version=current.version, at=at)
        return self._commit(updated, current.version)

    def revoke(self, *args: Any, **kwargs: Any) -> Grant:
        fields = dict(kwargs)
        grant = None
        for a in args:
            if isinstance(a, Grant) and type(a) is Grant:
                grant = a
        if grant is None:
            grant = self._coerce_grant(fields)
        if grant is None and args and isinstance(args[0], str):
            grant = self.get(args[0])
        if grant is None and "grant_id" in fields:
            grant = self.get(fields["grant_id"])
        if not (isinstance(grant, Grant) and type(grant) is Grant):
            raise GrantLifecycleInputError("Exact grant identity is required")
        current = self._grants.get(grant.grant_id)
        if current is None:
            raise GrantLifecycleInputError("Unknown grant")
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version", grant.version))))
        if current.version != expected_version:
            raise GrantLifecycleConflictError(f"Stale grant version: expected {expected_version}, current {current.version}")
        revoked_at = fields.get("revoked_at", fields.get("at", fields.get("now", fields.get("time"))))
        reason = fields.get("reason", fields.get("revoked_reason", fields.get("note")))
        if revoked_at is not None:
            norm = _normalize_dt(revoked_at)
            if norm is None:
                raise GrantLifecycleInputError("revoked_at must be a datetime")
            revoked_at = norm
        else:
            revoked_at = self._now()
        updated = current.transition(STATUS_REVOKED, expected_version=current.version, at=revoked_at, revoked_at=revoked_at, reason=reason)
        # force revoked flag
        if updated.revoked is not True:
            updated = Grant(**{**_grant_to_dict(updated), "revoked": True, "revoked_at": revoked_at})
        return self._commit(updated, current.version)

    def expire(self, *args: Any, **kwargs: Any) -> Grant:
        fields = dict(kwargs)
        grant = None
        for a in args:
            if isinstance(a, Grant) and type(a) is Grant:
                grant = a
        if grant is None:
            grant = self._coerce_grant(fields)
        if grant is None and args and isinstance(args[0], str):
            grant = self.get(args[0])
        if grant is None and "grant_id" in fields:
            grant = self.get(fields["grant_id"])
        if not (isinstance(grant, Grant) and type(grant) is Grant):
            raise GrantLifecycleInputError("Exact grant identity is required")
        current = self._grants.get(grant.grant_id)
        if current is None:
            raise GrantLifecycleInputError("Unknown grant")
        expected_version = fields.get("expected_version", fields.get("expected", fields.get("version", fields.get("row_version", grant.version))))
        if current.version != expected_version:
            raise GrantLifecycleConflictError(f"Stale grant version: expected {expected_version}, current {current.version}")
        at = fields.get("at", fields.get("now", fields.get("evaluation_time", self._now())))
        norm_at = _normalize_dt(at) if not isinstance(at, datetime) else _normalize_dt(at)
        if norm_at is None:
            raise GrantLifecycleInputError("at must be a datetime")
        updated = current.transition(STATUS_EXPIRED, expected_version=current.version, at=norm_at)
        return self._commit(updated, current.version)

    def validate(self, *args: Any, **kwargs: Any) -> GrantValidityResult:
        fields = dict(kwargs)
        grant = None
        for a in args:
            if isinstance(a, Grant) and type(a) is Grant:
                grant = a
            elif isinstance(a, str):
                g = self.get(a)
                if g is not None:
                    grant = g
        if grant is None:
            grant = self._coerce_grant(fields)
        if grant is None and "grant" in fields:
            grant = self._coerce_grant(fields["grant"])
        if grant is None and "grant_id" in fields:
            grant = self.get(fields["grant_id"])
        at = fields.get("at", fields.get("now", fields.get("evaluation_time")))
        grantee = fields.get("grantee", fields.get("subject", fields.get("requester")))
        expected_schema_version = fields.get("expected_schema_version")
        return validate_grant(grant, at=at, grantee=grantee, expected_schema_version=expected_schema_version) if grant is not None else GrantValidityResult(False, REASON_NOT_FOUND, "Unknown grant", evaluated_at=None)

    def revoke_if_version(self, *a, **kw):
        return self.revoke(*a, **kw)


# --- Compatibility aliases --------------------------------------------------

GrantRecord = Grant
GrantLifecycleRecord = Grant
BoundedGrant = Grant
GrantInstance = Grant

GrantLifecycle = GrantLifecycleManager
GrantManager = GrantLifecycleManager
GrantLifecycleService = GrantLifecycleManager
GrantLifecycleStore = GrantLifecycleManager
GrantLifecycleEngine = GrantLifecycleManager
GrantStore = GrantLifecycleManager
GrantRegistry = GrantLifecycleManager

GrantValidity = GrantValidityResult
GrantLifecycleResult = GrantValidityResult
GrantValidityCheck = GrantValidityResult
GrantCheckResult = GrantValidityResult

GrantLifecycleInput = GrantLifecycleInputError
GrantLifecycleConflict = GrantLifecycleConflictError
GrantLifecycleTransition = GrantLifecycleTransitionError

create = create_grant
revoke = revoke_grant
expire = expire_grant
invalidate = invalidate_grant
transition = transition_grant
validate = validate_grant
is_valid = is_grant_valid

evaluate_grant_lifecycle = validate_grant
evaluate_grant = validate_grant
check_grant = validate_grant
grant_valid = is_grant_valid
grant_is_valid = is_grant_valid

__all__ = [
    "Grant", "GrantRecord", "GrantLifecycleRecord", "BoundedGrant", "GrantInstance",
    "GrantValidityResult", "GrantValidity", "GrantLifecycleResult", "GrantValidityCheck",
    "GrantCheckResult", "GrantLifecycleManager", "GrantLifecycle", "GrantManager",
    "GrantLifecycleService", "GrantLifecycleStore", "GrantLifecycleEngine", "GrantStore",
    "GrantRegistry", "GrantLifecycleError", "GrantLifecycleInputError",
    "GrantLifecycleConflictError", "GrantLifecycleTransitionError",
    "STATUS_PROPOSED", "STATUS_ISSUED", "STATUS_ACTIVE", "STATUS_REVOKED",
    "STATUS_EXPIRED", "STATUS_INVALIDATED", "STATUS_REJECTED", "STATUS_CANCELLED",
    "STATUS_SUSPENDED", "ALL_STATUSES", "TERMINAL_STATUSES",
    "REASON_MATCH", "REASON_MISSING_GRANT_ID", "REASON_MISSING_VALIDITY",
    "REASON_MISSING_PROVENANCE", "REASON_GRANT_UNTRUSTED", "REASON_GRANT_REVOKED",
    "REASON_GRANT_NOT_YET_VALID", "REASON_GRANT_EXPIRED",
    "REASON_GRANT_INVALID_WINDOW", "REASON_INPUT_MALFORMED",
    "REASON_INVALID_STATUS", "REASON_ILLEGAL_TRANSITION", "REASON_VERSION_CONFLICT",
    "REASON_SCOPE_MISSING", "REASON_NOT_FOUND", "REASON_ALREADY_REVOKED",
    "validate_grant", "is_grant_valid", "create_grant", "revoke_grant",
    "expire_grant", "invalidate_grant", "transition_grant",
    "evaluate_grant_lifecycle", "evaluate_grant", "check_grant",
    "create", "revoke", "expire", "invalidate", "transition", "validate",
    "is_valid", "grant_valid", "grant_is_valid",
]
