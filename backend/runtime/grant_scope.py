
"""Pure, non-executing, unwired grant-scope *evaluation* foundation.

This module evaluates whether a proposed autonomous operation falls inside an
explicitly granted scope. It is a foundation only: it performs **no** action,
holds **no** permission, opens **no** resource, writes **no** state, and is
**not wired** into any executor, route, worker, task runner, ownership layer
or database.  Its output is a *match result* only; it can never authorise an
action on its own.

Trust boundary
--------------

A grant scope is *data supplied by a caller*.  This evaluator does not verify
the authenticity of that data, does not prove human consent, and does not
prove that a grant was ever actually issued.  Callers must obtain a
``GrantScope`` from a trusted source and must treat ``matches=True`` as
"consistent with the supplied scope" rather than "authorised": a separately
issued, durable, authenticated permission (receipt) plus the normal execution
admission path is still required before any effect can occur.

The evaluator deliberately refuses to treat a bare caller-built mapping or
dictionary as proof of a grant (such inputs are rejected as unsupported).  It
also never performs filesystem canonicalisation, Windows path normalisation,
prefix containment checks or any other path-based inference: resources are
compared by their exact opaque identities only, and any scope that depends on
filesystem identity is unsupported and yields ``matches=False``.

Validity is bounded: every ``GrantScope`` must carry an explicit
``valid_from``/``valid_until`` window and an explicit trusted/revoked state.
The evaluator supplies **no** default durations, permanent bans, use caps or
rate limits; those are product policy decisions that belong to the grant
issuer, not to this generic foundation.
"""

from __future__ import annotations

import datetime
import types
from dataclasses import dataclass, field
from typing import Any, FrozenSet, Mapping, Optional

__all__ = [
    "GrantScopeError",
    "GrantScopeInputError",
    "ResourceIdentity",
    "ContextIdentity",
    "GrantOperation",
    "GrantScope",
    "GrantEvaluationRequest",
    "GrantEvaluationResult",
    "evaluate_grant_scope",
    "GrantScopeEvaluator",
    "GrantScopeMatch",
    "GrantScopeMatcher",
    "GrantScopeMatches",
    "GrantedOperation",
    "GrantClause",
    "ResourceRef",
    "ContextRef",
    "ContextScope",
    "evaluate_grant",
    "match_grant_scope",
    "grant_scope_matches",
    "GrantOperationScope",
]


REASON_MATCH = "match"
REASON_INPUT_MALFORMED = "input_malformed"
REASON_GRANT_UNTRUSTED = "grant_untrusted"
REASON_GRANT_REVOKED = "grant_revoked"
REASON_GRANT_NOT_YET_VALID = "grant_not_yet_valid"
REASON_GRANT_EXPIRED = "grant_expired"
REASON_GRANT_INVALID_WINDOW = "grant_invalid_window"
REASON_ACTION_MISMATCH = "action_mismatch"
REASON_RESOURCE_MISMATCH = "resource_mismatch"
REASON_CONTEXT_MISMATCH = "context_mismatch"
REASON_OPERATION_NOT_GRANTED = "operation_not_granted"


class GrantScopeError(Exception):
    """Base error for grant-scope evaluation."""


class GrantScopeInputError(GrantScopeError, ValueError):
    """Raised value-style error subtype; evaluation normally returns a result."""


def _is_nonempty_str(value: Any) -> bool:
    return type(value) is str and bool(value.strip())


def _normalize_dt(value: Any) -> Optional[datetime.datetime]:
    if type(value) is not datetime.datetime:
        return None
    try:
        if value.tzinfo is None:
            return value.replace(tzinfo=datetime.timezone.utc)
        return value.astimezone(datetime.timezone.utc)
    except Exception:
        return None


def _frozen_str_set(value: Any) -> Optional[FrozenSet[str]]:
    if value is None:
        return frozenset()
    if type(value) not in (set, frozenset, tuple, list):
        return None
    out = set()
    for item in value:
        if not _is_nonempty_str(item):
            return None
        out.add(str(item))
    return frozenset(out)


def _frozen_resource_set(value: Any) -> Optional[FrozenSet["ResourceIdentity"]]:
    if value is None:
        return frozenset()
    if type(value) not in (set, frozenset, tuple, list):
        return None
    out = set()
    for item in value:
        if type(item) is not ResourceIdentity:
            return None
        out.add(item)
    return frozenset(out)


def _frozen_context_set(value: Any) -> Optional[FrozenSet["ContextIdentity"]]:
    if value is None:
        return frozenset()
    if type(value) not in (set, frozenset, tuple, list):
        return None
    out = set()
    for item in value:
        if type(item) is not ContextIdentity:
            return None
        out.add(item)
    return frozenset(out)


def _frozen_operation_set(value: Any) -> Optional[FrozenSet["GrantOperation"]]:
    if value is None:
        return frozenset()
    if type(value) not in (set, frozenset, tuple, list):
        return None
    out = set()
    for item in value:
        if type(item) is not GrantOperation:
            return None
        out.add(item)
    return frozenset(out)


_IMMUTABLE_SCALAR_TYPES = (type(None), bool, int, float, str, bytes, datetime.datetime)


def _deep_freeze_scope_value(value: Any, seen: frozenset) -> Any:
    if type(value) in _IMMUTABLE_SCALAR_TYPES:
        return value
    marker = id(value)
    if marker in seen:
        raise GrantScopeInputError("Cyclic metadata is not allowed in a grant scope")
    next_seen = seen | {marker}
    if type(value) is dict:
        out: dict = {}
        for key, item in value.items():
            if type(key) not in (str, int, float, type(None)) or type(key) is bool:
                raise GrantScopeInputError("Unsupported metadata key type in grant scope")
            out[key] = _deep_freeze_scope_value(item, next_seen)
        return types.MappingProxyType(out)
    if type(value) in (list, tuple):
        return tuple(_deep_freeze_scope_value(item, next_seen) for item in value)
    if type(value) in (set, frozenset):
        return frozenset(_deep_freeze_scope_value(item, next_seen) for item in value)
    if type(value) is types.MappingProxyType:
        out = {}
        for key, item in value.items():
            if type(key) not in (str, int, float, type(None)) or type(key) is bool:
                raise GrantScopeInputError("Unsupported metadata key type in grant scope")
            out[key] = _deep_freeze_scope_value(item, next_seen)
        return types.MappingProxyType(out)
    raise GrantScopeInputError("Unsupported value type in grant scope metadata")


@dataclass(frozen=True)
class ResourceIdentity:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind.strip():
            raise GrantScopeInputError("Exact resource identity kind is required")
        if type(self.value) is not str or not self.value.strip():
            raise GrantScopeInputError("Exact resource identity value is required")
        if len(self.kind) > 128 or len(self.value) > 4096:
            raise GrantScopeInputError("Resource identity exceeds maximum length")


@dataclass(frozen=True)
class ContextIdentity:
    kind: str
    value: str

    def __post_init__(self) -> None:
        if type(self.kind) is not str or not self.kind.strip():
            raise GrantScopeInputError("Exact context identity kind is required")
        if type(self.value) is not str or not self.value.strip():
            raise GrantScopeInputError("Exact context identity value is required")
        if len(self.kind) > 128 or len(self.value) > 4096:
            raise GrantScopeInputError("Context identity exceeds maximum length")


@dataclass(frozen=True)
class GrantOperation:
    action: str
    resource: ResourceIdentity
    context: ContextIdentity

    def __post_init__(self) -> None:
        if type(self.action) is not str or (type(self.action) is bool):
            raise GrantScopeInputError("Exact action identity is required")
        if not self.action.strip():
            raise GrantScopeInputError("Exact action identity is required")
        if len(self.action) > 256:
            raise GrantScopeInputError("Action identity exceeds maximum length")
        if type(self.resource) is not ResourceIdentity:
            raise GrantScopeInputError("Exact resource identity is required")
        if type(self.context) is not ContextIdentity:
            raise GrantScopeInputError("Exact context identity is required")


@dataclass(frozen=True)
class GrantScope:
    grant_id: Any = ""
    subject: Any = ""
    issuer: Any = ""
    trusted: Any = False
    revoked: Any = False
    revoked_at: Any = None
    valid_from: Any = None
    valid_until: Any = None
    allowed_operations: Any = field(default_factory=frozenset)
    allowed_actions: Any = field(default_factory=frozenset)
    allowed_resources: Any = field(default_factory=frozenset)
    allowed_contexts: Any = field(default_factory=frozenset)
    schema_version: Any = 1
    trust_source: Any = ""
    metadata: Any = field(default_factory=dict)

    _checked: bool = field(default=False, init=False, repr=False, compare=False)

    def __post_init__(self) -> None:
        ops = _frozen_operation_set(self.allowed_operations)
        acts = _frozen_str_set(self.allowed_actions)
        res = _frozen_resource_set(self.allowed_resources)
        ctx = _frozen_context_set(self.allowed_contexts)
        if ops is None or acts is None or res is None or ctx is None:
            raise GrantScopeInputError("Grant scope contains malformed allow-lists")
        object.__setattr__(self, "allowed_operations", ops)
        object.__setattr__(self, "allowed_actions", acts)
        object.__setattr__(self, "allowed_resources", res)
        object.__setattr__(self, "allowed_contexts", ctx)
        if self.metadata is None:
            object.__setattr__(self, "metadata", types.MappingProxyType({}))
        else:
            try:
                frozen = _deep_freeze_scope_value(self.metadata, frozenset())
            except GrantScopeInputError:
                raise
            except Exception as exc:
                raise GrantScopeInputError("Grant scope metadata is invalid") from exc
            if type(frozen) is not types.MappingProxyType:
                raise GrantScopeInputError("Grant scope metadata must be a mapping")
            object.__setattr__(self, "metadata", frozen)
        if isinstance(self.revoked, str):
            low = self.revoked.strip().lower()
            if low in {"true", "yes", "1"}:
                object.__setattr__(self, "revoked", True)
            elif low in {"false", "no", "0", ""}:
                object.__setattr__(self, "revoked", False)
            else:
                raise GrantScopeInputError("Grant revoked flag must be boolean")
        elif not isinstance(self.revoked, bool):
            raise GrantScopeInputError("Grant revoked flag must be boolean")
        if isinstance(self.trusted, str):
            low = self.trusted.strip().lower()
            if low in {"true", "yes", "1"}:
                object.__setattr__(self, "trusted", True)
            elif low in {"false", "no", "0", ""}:
                object.__setattr__(self, "trusted", False)
            else:
                raise GrantScopeInputError("Grant trusted flag must be boolean")
        elif not isinstance(self.trusted, bool):
            raise GrantScopeInputError("Grant trusted flag must be boolean")
        if not isinstance(self.schema_version, int) or isinstance(self.schema_version, bool) or self.schema_version < 1:
            raise GrantScopeInputError("Grant schema version must be a positive integer")
        if self.subject is None:
            object.__setattr__(self, "subject", "")
        if self.issuer is None:
            object.__setattr__(self, "issuer", "")
        if self.trust_source is None:
            object.__setattr__(self, "trust_source", "")
        if self.grant_id is None:
            object.__setattr__(self, "grant_id", "")
        if not isinstance(self.subject, str):
            raise GrantScopeInputError("Grant subject must be text")
        if not isinstance(self.issuer, str):
            raise GrantScopeInputError("Grant issuer must be text")
        if not isinstance(self.grant_id, str):
            raise GrantScopeInputError("Grant identity must be text")
        if not isinstance(self.trust_source, str):
            raise GrantScopeInputError("Grant trust source must be text")


@dataclass(frozen=True)
class GrantEvaluationRequest:
    grant: Any
    operation: Any
    evaluation_time: Any = None
    grantee: Any = None
    expected_schema_version: Any = None


@dataclass(frozen=True)
class GrantEvaluationResult:
    matches: bool
    reason_code: str
    reason: str
    grant_id: str = ""
    evaluated_at: Optional[str] = None
    operation: Optional[GrantOperation] = None

    @property
    def match(self) -> bool:
        return self.matches

    def to_dict(self) -> dict:
        data = {
            "matches": self.matches,
            "match": self.matches,
            "reason_code": self.reason_code,
            "reason": self.reason,
            "grant_id": self.grant_id,
            "evaluated_at": self.evaluated_at,
        }
        if self.operation is not None:
            data["operation"] = {
                "action": self.operation.action,
                "resource": {"kind": self.operation.resource.kind, "value": self.operation.resource.value},
                "context": {"kind": self.operation.context.kind, "value": self.operation.context.value},
            }
        return data


def _validate_grant(grant: Any) -> Optional[str]:
    if type(grant) is not GrantScope:
        return REASON_INPUT_MALFORMED
    if type(grant.grant_id) is not str or not grant.grant_id.strip():
        return REASON_INPUT_MALFORMED
    if type(grant.trusted) is not bool or grant.trusted is not True:
        return REASON_GRANT_UNTRUSTED
    if type(grant.revoked) is not bool:
        return REASON_INPUT_MALFORMED
    if grant.revoked is True:
        return REASON_GRANT_REVOKED
    if type(grant.valid_from) is not datetime.datetime or type(grant.valid_until) is not datetime.datetime:
        return REASON_INPUT_MALFORMED
    vf = _normalize_dt(grant.valid_from)
    vu = _normalize_dt(grant.valid_until)
    if vf is None or vu is None:
        return REASON_INPUT_MALFORMED
    if not (vf < vu):
        return REASON_GRANT_INVALID_WINDOW
    if grant.revoked_at is not None:
        ra = _normalize_dt(grant.revoked_at)
        if ra is None:
            return REASON_INPUT_MALFORMED
    return None


def _operation_from_any(value: Any) -> Optional[GrantOperation]:
    if type(value) is GrantOperation:
        return value
    return None


def _grant_from_any(value: Any) -> Optional[GrantScope]:
    if type(value) is GrantScope:
        return value
    return None


def _evaluate(request: Any) -> GrantEvaluationResult:
    if not isinstance(request, GrantEvaluationRequest):
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Evaluation request must be a typed GrantEvaluationRequest",
        )

    grant = _grant_from_any(request.grant)
    operation = _operation_from_any(request.operation)

    gid = ""
    if isinstance(grant, GrantScope):
        gid = str(grant.grant_id) if grant.grant_id is not None else ""

    eval_time = _normalize_dt(request.evaluation_time)
    if eval_time is None:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Missing or malformed evaluation time",
            grant_id=gid,
            operation=operation,
        )

    if grant is None:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Grant scope must be a typed GrantScope, not a mapping or other value",
            grant_id="",
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )

    if operation is None:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Proposed operation must be a typed GrantOperation",
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
        )

    # strict runtime types (no subclass/operator bypass)
    if type(operation) is not GrantOperation:
        return GrantEvaluationResult(
            matches=False, reason_code=REASON_INPUT_MALFORMED,
            reason="Proposed operation must be a typed GrantOperation",
            grant_id=gid, evaluated_at=eval_time.isoformat(), operation=None)
    if type(operation.action) is not str or not operation.action.strip():
        return GrantEvaluationResult(
            matches=False, reason_code=REASON_INPUT_MALFORMED,
            reason="Proposed action must be exact text",
            grant_id=gid, evaluated_at=eval_time.isoformat(), operation=operation)
    if type(operation.resource) is not ResourceIdentity:
        return GrantEvaluationResult(
            matches=False, reason_code=REASON_INPUT_MALFORMED,
            reason="Resource identity must be exact runtime type",
            grant_id=gid, evaluated_at=eval_time.isoformat(), operation=operation)
    if type(operation.context) is not ContextIdentity:
        return GrantEvaluationResult(
            matches=False, reason_code=REASON_INPUT_MALFORMED,
            reason="Context identity must be exact runtime type",
            grant_id=gid, evaluated_at=eval_time.isoformat(), operation=operation)

    grant_err = _validate_grant(grant)
    if grant_err is not None:
        msg = {
            REASON_INPUT_MALFORMED: "Grant scope is missing or malformed",
            REASON_GRANT_UNTRUSTED: "Grant scope is not trusted",
            REASON_GRANT_REVOKED: "Grant scope is revoked",
            REASON_GRANT_INVALID_WINDOW: "Grant scope has an invalid validity window",
        }.get(grant_err, "Grant scope is unusable")
        return GrantEvaluationResult(
            matches=False,
            reason_code=grant_err,
            reason=msg,
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )

    if grant.revoked_at is not None:
        ra = _normalize_dt(grant.revoked_at)
        if ra is not None and eval_time >= ra:
            return GrantEvaluationResult(
                matches=False,
                reason_code=REASON_GRANT_REVOKED,
                reason="Grant scope is revoked",
                grant_id=gid,
                evaluated_at=eval_time.isoformat(),
                operation=operation,
            )

    vf = _normalize_dt(grant.valid_from)
    vu = _normalize_dt(grant.valid_until)
    if eval_time < vf:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_GRANT_NOT_YET_VALID,
            reason="Grant scope is not yet valid",
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )
    if eval_time > vu:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_GRANT_EXPIRED,
            reason="Grant scope is expired",
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )

    if request.expected_schema_version is not None:
        try:
            if int(request.expected_schema_version) != int(grant.schema_version):
                return GrantEvaluationResult(
                    matches=False,
                    reason_code=REASON_INPUT_MALFORMED,
                    reason="Grant schema version mismatch",
                    grant_id=gid,
                    evaluated_at=eval_time.isoformat(),
                    operation=operation,
                )
        except (TypeError, ValueError):
            return GrantEvaluationResult(
                matches=False,
                reason_code=REASON_INPUT_MALFORMED,
                reason="Grant schema version mismatch",
                grant_id=gid,
                evaluated_at=eval_time.isoformat(),
                operation=operation,
            )

    if request.grantee is not None and _is_nonempty_str(request.grantee):
        if _is_nonempty_str(grant.subject) and str(grant.subject) != str(request.grantee):
            return GrantEvaluationResult(
                matches=False,
                reason_code=REASON_INPUT_MALFORMED,
                reason="Grant subject does not match requester",
                grant_id=gid,
                evaluated_at=eval_time.isoformat(),
                operation=operation,
            )

    ops = getattr(grant, "allowed_operations", frozenset())
    if ops:
        if operation in ops:
            return GrantEvaluationResult(
                matches=True,
                reason_code=REASON_MATCH,
                reason="Proposed operation exactly matches a granted scope entry",
                grant_id=gid,
                evaluated_at=eval_time.isoformat(),
                operation=operation,
            )
        action_only = any(
            e.action != operation.action and e.resource == operation.resource and e.context == operation.context
            for e in ops
        )
        resource_only = any(
            e.action == operation.action and e.resource != operation.resource and e.context == operation.context
            for e in ops
        )
        context_only = any(
            e.action == operation.action and e.resource == operation.resource and e.context != operation.context
            for e in ops
        )
        if action_only:
            code = REASON_ACTION_MISMATCH
        elif resource_only:
            code = REASON_RESOURCE_MISMATCH
        elif context_only:
            code = REASON_CONTEXT_MISMATCH
        else:
            code = REASON_OPERATION_NOT_GRANTED
        friendly = {
            REASON_ACTION_MISMATCH: "Action does not match the granted scope",
            REASON_RESOURCE_MISMATCH: "Resource does not match the granted scope",
            REASON_CONTEXT_MISMATCH: "Context does not match the granted scope",
            REASON_OPERATION_NOT_GRANTED: "Proposed operation is not granted",
        }.get(code, "Proposed operation is not granted")
        return GrantEvaluationResult(
            matches=False,
            reason_code=code,
            reason=friendly,
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )

    acts = getattr(grant, "allowed_actions", frozenset())
    ress = getattr(grant, "allowed_resources", frozenset())
    ctxs = getattr(grant, "allowed_contexts", frozenset())
    if acts or ress or ctxs:
        match_all = True
        if acts and operation.action not in acts:
            match_all = False
        if ress and operation.resource not in ress:
            match_all = False
        if ctxs and operation.context not in ctxs:
            match_all = False
        if not (acts and ress and ctxs):
            match_all = False
        if match_all:
            return GrantEvaluationResult(
                matches=True,
                reason_code=REASON_MATCH,
                reason="Proposed operation matches granted scope allow-lists",
                grant_id=gid,
                evaluated_at=eval_time.isoformat(),
                operation=operation,
            )
        if acts and operation.action not in acts:
            code = REASON_ACTION_MISMATCH
        elif ress and operation.resource not in ress:
            code = REASON_RESOURCE_MISMATCH
        elif ctxs and operation.context not in ctxs:
            code = REASON_CONTEXT_MISMATCH
        else:
            code = REASON_OPERATION_NOT_GRANTED
        if not (acts and ress and ctxs):
            code = REASON_OPERATION_NOT_GRANTED
        friendly = {
            REASON_ACTION_MISMATCH: "Action does not match the granted scope",
            REASON_RESOURCE_MISMATCH: "Resource does not match the granted scope",
            REASON_CONTEXT_MISMATCH: "Context does not match the granted scope",
            REASON_OPERATION_NOT_GRANTED: "Proposed operation is not granted",
        }.get(code, "Proposed operation is not granted")
        return GrantEvaluationResult(
            matches=False,
            reason_code=code,
            reason=friendly,
            grant_id=gid,
            evaluated_at=eval_time.isoformat(),
            operation=operation,
        )

    return GrantEvaluationResult(
        matches=False,
        reason_code=REASON_OPERATION_NOT_GRANTED,
        reason="Grant scope contains no granted operations",
        grant_id=gid,
        evaluated_at=eval_time.isoformat(),
        operation=operation,
    )


def _build_request_from_args(*args: Any, **kwargs: Any) -> GrantEvaluationRequest:
    request = kwargs.get("request", None)
    if isinstance(request, GrantEvaluationRequest):
        return request
    for a in args:
        if isinstance(a, GrantEvaluationRequest):
            return a

    grant = kwargs.get("grant", None)
    if grant is None:
        grant = kwargs.get("grant_scope", kwargs.get("scope", kwargs.get("grant_record")))
    if grant is None:
        for a in args:
            if isinstance(a, GrantScope):
                grant = a
                break

    positional_times = [a for a in args if isinstance(a, datetime.datetime) and not isinstance(a, bool)]

    operation = kwargs.get("operation", None)
    if operation is None:
        operation = kwargs.get("proposed_operation", kwargs.get("proposed"))
    if operation is None:
        for a in args:
            if isinstance(a, GrantOperation):
                operation = a
                break

    action = kwargs.get("action", None)
    if action is None:
        action = kwargs.get("proposed_action", kwargs.get("action_name"))
    resource = kwargs.get("resource", None)
    if resource is None:
        resource = kwargs.get("proposed_resource", kwargs.get("target"))
    context = kwargs.get("context", None)
    if context is None:
        context = kwargs.get("proposed_context")
    resource_kind = kwargs.get("resource_kind")
    resource_value = kwargs.get("resource_value", kwargs.get("resource_id"))
    context_kind = kwargs.get("context_kind")
    context_value = kwargs.get("context_value", kwargs.get("context_id"))

    if operation is None and action is not None:
        if not isinstance(resource, ResourceIdentity) and (resource_kind is not None or resource_value is not None):
            try:
                resource = ResourceIdentity(kind=resource_kind, value=resource_value)  # type: ignore[arg-type]
            except Exception:
                resource = None
        if not isinstance(context, ContextIdentity) and (context_kind is not None or context_value is not None):
            try:
                context = ContextIdentity(kind=context_kind, value=context_value)  # type: ignore[arg-type]
            except Exception:
                context = None
        try:
            operation = GrantOperation(action=action, resource=resource, context=context)
        except Exception:
            operation = None

    time = kwargs.get("evaluation_time", None)
    if time is None:
        time = kwargs.get("now", kwargs.get("at", kwargs.get("evaluated_at")))
    if time is None and positional_times:
        time = positional_times[0]
    grantee = kwargs.get("grantee", None)
    if grantee is None:
        grantee = kwargs.get("requester", kwargs.get("subject"))
    expected_schema_version = kwargs.get("expected_schema_version", None)

    return GrantEvaluationRequest(
        grant=grant,
        operation=operation,
        evaluation_time=time,
        grantee=grantee,
        expected_schema_version=expected_schema_version,
    )


def evaluate_grant_scope(*args: Any, **kwargs: Any) -> GrantEvaluationResult:
    try:
        request = _build_request_from_args(*args, **kwargs)
    except Exception:
        request = None
    if not isinstance(request, GrantEvaluationRequest):
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Malformed evaluation request",
        )
    try:
        return _evaluate(request)
    except Exception:
        return GrantEvaluationResult(
            matches=False,
            reason_code=REASON_INPUT_MALFORMED,
            reason="Evaluation failed closed on malformed input",
            grant_id=(str(request.grant.grant_id) if isinstance(request.grant, GrantScope) else ""),
            evaluated_at=None,
            operation=request.operation if isinstance(request.operation, GrantOperation) else None,
        )


class GrantScopeEvaluator:
    def evaluate(self, *args: Any, **kwargs: Any) -> GrantEvaluationResult:
        return evaluate_grant_scope(*args, **kwargs)

    evaluate_grant = evaluate
    evaluate_grant_scope = evaluate
    match = evaluate
    matches = evaluate
    __call__ = evaluate


GrantScopeMatch = GrantEvaluationResult
GrantScopeMatcher = GrantScopeEvaluator
GrantScopeMatches = GrantEvaluationResult
GrantedOperation = GrantOperation
GrantClause = GrantOperation
GrantOperationScope = GrantScope
ResourceRef = ResourceIdentity
ContextRef = ContextIdentity
ContextScope = ContextIdentity
evaluate_grant = evaluate_grant_scope
match_grant_scope = evaluate_grant_scope
grant_scope_matches = evaluate_grant_scope
GrantMatch = GrantEvaluationResult
GrantScopeResult = GrantEvaluationResult
GrantResult = GrantEvaluationResult
GrantEvaluation = GrantEvaluationResult
ContextBinding = ContextIdentity
ResourceBinding = ResourceIdentity
OperationScope = GrantOperation
ScopedOperation = GrantOperation
evaluate = evaluate_grant_scope
