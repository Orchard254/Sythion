"""Unwired, read-only, fail-closed uncertainty inspection foundation.

This module intentionally does **not** inspect or disclose durable task, ownership,
or action history.  The available schema does not contain a provably authenticated
profile/task/session boundary, so treating any caller-provided identifier as
authority would create a cross-profile/session disclosure path.

The module therefore returns an explicit NO DATA / fail-closed result.  It performs
no query against Task, ownership, action, event, session, profile, vault, device,
or network state and never clears, resolves, approves, schedules, or authorizes
uncertainty.
"""

from __future__ import annotations

import types
from typing import Any, Mapping


class DeepFrozenSnapshot(Mapping[str, Any]):
    """A deeply immutable mapping view.

    Nested dicts/lists are recursively converted to frozen objects, so callers
    cannot mutate any level of the returned snapshot.
    """

    def __init__(self, data: Mapping[str, Any]):
        self._data: Mapping[str, Any] = types.MappingProxyType(_deep_freeze(data))

    def __getitem__(self, key: str) -> Any:
        return self._data[key]

    def get(self, key: str, default: Any = None) -> Any:
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self) -> int:
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __contains__(self, key: object) -> bool:
        return key in self._data

    def __getattr__(self, name: str) -> Any:
        try:
            return self._data[name]
        except KeyError as exc:
            raise AttributeError(name) from exc

    def __setattr__(self, name: str, value: Any) -> None:
        if name == "_data":
            super().__setattr__(name, value)
            return
        raise AttributeError("DeepFrozenSnapshot is immutable")

    def to_dict(self) -> dict[str, Any]:
        # A defensive unfrozen copy prevents callers from mutating snapshot data,
        # including nested frozen proxy objects.
        return _thaw(self._data)

    def __eq__(self, other: object) -> bool:
        if isinstance(other, DeepFrozenSnapshot):
            other = other._data
        if isinstance(other, Mapping):
            return tuple(sorted(self._data.items())) == tuple(sorted(dict(other).items()))
        return NotImplemented

    def __repr__(self) -> str:
        return f"DeepFrozenSnapshot({dict(self._data)!r})"


def _deep_freeze(value: Any) -> Any:
    if isinstance(value, Mapping):
        return DeepFrozenMappingProxy({str(key): _deep_freeze(item) for key, item in value.items()})
    if isinstance(value, (list, tuple)):
        return tuple(_deep_freeze(item) for item in value)
    if isinstance(value, set):
        return frozenset(_deep_freeze(item) for item in value)
    if isinstance(value, bytearray):
        return bytes(value)
    return value


class DeepFrozenMappingProxy(Mapping[str, Any]):
    """Internal frozen mapping with dict-compatible behavior."""

    def __init__(self, data: Mapping[str, Any]):
        self._data = types.MappingProxyType(dict(data))

    def __getitem__(self, key):
        return self._data[key]

    def get(self, key, default=None):
        return self._data.get(key, default)

    def keys(self):
        return self._data.keys()

    def values(self):
        return self._data.values()

    def items(self):
        return self._data.items()

    def __len__(self):
        return len(self._data)

    def __iter__(self):
        return iter(self._data)

    def __contains__(self, key):
        return key in self._data

    def __eq__(self, other):
        return dict(self._data) == other

    def __repr__(self):
        return f"DeepFrozenMappingProxy({dict(self._data)!r})"


def _thaw(value: Any) -> Any:
    if isinstance(value, (DeepFrozenSnapshot, DeepFrozenMappingProxy)):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, Mapping):
        return {str(key): _thaw(item) for key, item in value.items()}
    if isinstance(value, tuple):
        return [_thaw(item) for item in value]
    if isinstance(value, (frozenset, set)):
        return [_thaw(item) for item in value]
    return value


def _require_no_autoflush_contract(db: Any) -> None:
    # The caller-provided session must explicitly expose no_autoflush.  Silently
    # substituting a no-op context would allow unsafe reads.
    if db is None:
        raise ValueError("db is required")
    no_autoflush = getattr(db, "no_autoflush", None)
    if no_autoflush is None:
        raise ValueError("db must expose no_autoflush")


def _blocked_result(
    task_id: Any = None,
    session_id: Any = None,
    profile: Any = None,
) -> DeepFrozenSnapshot:
    # Intentionally echo only a coarse "requested" marker, never durable IDs or
    # history.  Even the caller-supplied identifiers are treated as untrusted.
    data = {
        "inspection": "fail_closed_no_data",
        "read_only": True,
        "data_returned": False,
        "inspection_allowed": False,
        "uncertainty_changed": False,
        "uncertainty": "unknown",
        "uncertain_action_ids": (),
        "active_action_ids": (),
        "task": {"present": False, "data_returned": False},
        "ownership": {"present": False, "data_returned": False},
        "task_events": {"available": False, "data_returned": False, "returned": 0, "total": 0},
        "task_decision_window": {"available": False, "present": False, "data_returned": False},
        "conversation_task_associations": {"available": False, "count": 0, "data_returned": False},
        "session_lineage": {"requested": session_id is not None, "data_returned": False, "rows": ()},
        "scope_requested": {
            "task_id": task_id is not None,
            "session_id": session_id is not None,
            "profile": profile is not None,
        },
        "profile_scope": None,
        "profile_trust": "not_claimed",
        "profile_authenticated_assumed": False,
        "profile_authentication_responsibility": "caller",
        "profile_boundary_enforced": False,
        "profile_boundary_status": "not_enforceable_absent_profile_schema",
        "caller_responsible_for_profile_authentication": True,
        "boundary_status": "profile_task_boundary_unprovable",
        "blocker": "profile_task_boundary_unenforceable",
        "no_data_reason": (
            "Durable schema has no authenticated profile/task/session ownership "
            "binding; returning data would require trusting caller-provided scope."
        ),
        "malformed_evidence": "unknown",
        "missing_evidence": "unknown",
        "evidence_status": "unknown_unverified",
        "fail_closed": True,
        "mutations_performed": (),
        "resolution_performed": False,
        "approval_performed": False,
        "scheduling_performed": False,
        "authorization_performed": False,
        "cleared_uncertainty": False,
        "authorization": "none",
        "privacy": {
            "raw_event_data_exposed": False,
            "raw_request_text_exposed": False,
            "raw_params_exposed": False,
            "internal_ids_exposed": False,
            "free_text_reasons_exposed": False,
            "secrets_exposed": False,
        },
        "limitations": (
            "Fail-closed: no durable data is returned because the profile/task/session boundary cannot be proven.",
            "Expired lease, PID, heartbeat, and unlocked local guard state are observations only and never prove producer quiescence.",
            "Current state, timestamps, and digests cannot establish historical causation.",
            "No scope, task, ownership, action, event, session, profile, device, vault, or network lookup is performed.",
        ),
    }
    return DeepFrozenSnapshot(data)


def _inspect(
    db: Any,
    *,
    task_id: Any = None,
    session_id: Any = None,
    profile: Any = None,
    profile_id: Any = None,
    event_limit: Any = None,
    include_events: Any = None,
) -> DeepFrozenSnapshot:
    # Validate the required Db contract before returning NO DATA.
    _require_no_autoflush_contract(db)

    # Entering the contract confirms the session object's context manager works
    # and fails closed if it does not. No SQL is issued.
    with db.no_autoflush:
        return _blocked_result(
            task_id=task_id,
            session_id=session_id,
            profile=profile if profile is not None else profile_id,
        )


def inspect_task_uncertainty(db: Any = None, *args: Any, **kwargs: Any) -> DeepFrozenSnapshot:
    db = db if db is not None else kwargs.get("db")
    for key in ("database", "db_session", "session", "session_db"):
        if db is None and kwargs.get(key) is not None:
            db = kwargs.get(key)
    if args:
        kwargs = dict(kwargs)
        if len(args) > 0 and kwargs.get("task_id") is None:
            kwargs["task_id"] = args[0]
        if len(args) > 1 and kwargs.get("session_id") is None:
            kwargs["session_id"] = args[1]
        if len(args) > 2 and kwargs.get("profile") is None and kwargs.get("profile_id") is None:
            kwargs["profile"] = args[2]
    return _inspect(
        db,
        task_id=kwargs.get("task_id"),
        session_id=kwargs.get("session_id"),
        profile=kwargs.get("profile", kwargs.get("profile_id")),
        profile_id=kwargs.get("profile_id"),
        event_limit=kwargs.get("event_limit", kwargs.get("limit")),
        include_events=kwargs.get("include_events", kwargs.get("events")),
    )


# Stable aliases preserved.
inspect_uncertainty = inspect_task_uncertainty
inspect_task_uncertainty_history = inspect_task_uncertainty
get_uncertainty_inspection = inspect_task_uncertainty
get_uncertainty_snapshot = inspect_task_uncertainty
inspect_uncertain_actions = inspect_task_uncertainty
inspect_task_ownership_uncertainty = inspect_task_uncertainty


class TaskUncertaintyInspectionService:
    def __init__(self, db: Any = None, /, *args: Any, **kwargs: Any):
        self._db = db
        for key in ("database", "db_session", "session", "session_db"):
            if self._db is None and kwargs.get(key) is not None:
                self._db = kwargs.get(key)
        self._task_id = kwargs.get("task_id")
        self._session_id = kwargs.get("session_id")
        self._profile = kwargs.get("profile", kwargs.get("profile_id"))
        self._profile_id = kwargs.get("profile_id")
        if args and self._task_id is None:
            self._task_id = args[0] if len(args) > 0 else None
        if len(args) > 1 and self._session_id is None:
            self._session_id = args[1]
        if len(args) > 2 and self._profile is None:
            self._profile = args[2]

    def inspect(self, task_id: Any = None, session_id: Any = None, profile: Any = None, *,
                db: Any = None, profile_id: Any = None,
                event_limit: Any = None, include_events: Any = None) -> DeepFrozenSnapshot:
        effective_db = self._db if db is None else db
        return _inspect(
            effective_db,
            task_id=task_id if task_id is not None else self._task_id,
            session_id=session_id if session_id is not None else self._session_id,
            profile=profile if profile is not None else self._profile,
            profile_id=profile_id if profile_id is not None else self._profile_id,
            event_limit=event_limit,
            include_events=include_events,
        )

    inspect_uncertainty = inspect
    inspect_task_uncertainty = inspect
    inspect_task_uncertainty_history = inspect
    get_uncertainty_snapshot = inspect
    snapshot = inspect


UncertaintyInspectionService = TaskUncertaintyInspectionService
UncertaintyInspector = TaskUncertaintyInspectionService
TaskUncertaintyInspector = TaskUncertaintyInspectionService
ReadOnlyUncertaintyInspection = TaskUncertaintyInspectionService
TaskUncertaintyInspection = TaskUncertaintyInspectionService
UncertaintyInspectionFoundation = TaskUncertaintyInspectionService


__all__ = [
    "DeepFrozenSnapshot",
    "TaskUncertaintyInspectionService",
    "UncertaintyInspectionService",
    "UncertaintyInspector",
    "TaskUncertaintyInspector",
    "ReadOnlyUncertaintyInspection",
    "TaskUncertaintyInspection",
    "UncertaintyInspectionFoundation",
    "inspect_task_uncertainty",
    "inspect_uncertainty",
    "inspect_task_uncertainty_history",
    "get_uncertainty_inspection",
    "get_uncertainty_snapshot",
    "inspect_uncertain_actions",
    "inspect_task_ownership_uncertainty",
]
