"""Strict, pure selection validation for discussion-resolution envelopes.

This module validates a closed selection envelope only.  It does not perform
memory retrieval, persistence, task creation, command execution, authorization,
or any provider/database access.  It is a prerequisite contract, not a complete
memory pipeline.

Closed envelope::

    {
        "outcome": "selected" | "clarification",
        "selected_discussion_ids": [...],
        "clarification": string | null,
    }

``outcome == "selected"`` requires a nonempty list/tuple of unique, exact,
un-padded discussion IDs that are all present in the caller-supplied
``candidate_discussion_ids`` sequence, plus ``clarification is None``.  It is
additionally forbidden when ``discovery_complete`` is not strictly ``True``.

``outcome == "clarification"`` requires ``selected_discussion_ids == []`` and a
non-blank clarification string.

The caller owns session authority: candidate IDs must be gathered from the same
session before calling this validator.  This module never infers a session from a
string prefix, label, or discussion ID.  It performs no SQL, network, or model
calls and does not mutate its inputs.

Candidate identifiers are also fail-closed validated as exact, nonempty, unique
strings.  No artificial count cap is imposed.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Mapping, Sequence

__all__ = [
    "DiscussionResolutionSelectionError",
    "DiscussionResolutionSelectionResult",
    "validate_discussion_resolution_selection",
]

_UNSET = object()

_CANONICAL_KEYS = frozenset(
    {"outcome", "selected_discussion_ids", "clarification"}
)

_RESPONSE_KEYS = (
    "response",
    "envelope",
    "raw_response",
    "payload",
    "result",
    "model_response",
    "output",
    "value",
    "data",
)

_CANDIDATE_KEYS = (
    "candidate_discussion_ids",
    "candidate_ids",
    "candidate_ids_list",
    "allowed_discussion_ids",
    "allowed_candidate_ids",
    "allowed_ids",
    "same_session_candidate_ids",
    "same_session_candidate_discussion_ids",
    "same_session_ids",
    "available_candidate_ids",
    "available_discussion_ids",
    "candidates",
    "candidate_set",
    "valid_candidate_ids",
    "exact_candidate_ids",
    "candidate_discussions",
    "discovered_candidate_ids",
    "discovered_discussion_ids",
    "same_session_candidates",
    "session_candidate_ids",
    "discussion_candidate_ids",
)

_DISCOVERY_KEYS = (
    "discovery_complete",
    "discovery_completed",
    "discovery_is_complete",
    "discovery_complete_flag",
)


class DiscussionResolutionSelectionError(ValueError):
    """Raised when a discussion-resolution selection envelope is invalid."""

    def __init__(self, message: str = "", *, reason_code: str | None = None):
        super().__init__(message)
        self._reason_code = (
            reason_code if isinstance(reason_code, str) and reason_code
            else "invalid_discussion_resolution_selection"
        )

    @property
    def reason_code(self) -> str:
        return self._reason_code

    @reason_code.setter
    def reason_code(self, value: object) -> None:
        if isinstance(value, str) and value:
            self._reason_code = value


@dataclass(frozen=True, slots=True)
class DiscussionResolutionSelectionResult:
    """Typed, immutable result of validating a selection envelope."""

    outcome: str
    selected_discussion_ids: tuple[str, ...]
    clarification: str | None

    def to_dict(self) -> dict[str, Any]:
        return {
            "outcome": self.outcome,
            "selected_discussion_ids": list(self.selected_discussion_ids),
            "clarification": self.clarification,
        }

    @property
    def selected(self) -> bool:
        return self.outcome == "selected"


def _fail(message: str) -> None:
    raise DiscussionResolutionSelectionError(message)


def _is_candidate_id(value: Any) -> bool:
    return (
        type(value) is str
        and value != ""
        and value == value.strip()
    )


def _validate_candidate_ids(value: Any) -> tuple[str, ...]:
    """Fail-closed validation of the caller-supplied candidate ID domain."""
    if value is None:
        return ()
    if isinstance(value, (str, bytes, bytearray, dict)):
        _fail("Candidate discussion IDs must be a sequence of strings")
    try:
        items = list(value)
    except TypeError:
        _fail("Candidate discussion IDs must be a sequence of strings")
    result: list[str] = []
    seen: set[str] = set()
    for item in items:
        if not _is_candidate_id(item):
            _fail("Candidate discussion IDs must be exact, nonempty strings")
        if item in seen:
            _fail("Candidate discussion IDs must be unique")
        seen.add(item)
        result.append(item)
    return tuple(result)


def _extract_response(args: tuple[Any, ...], kwargs: dict[str, Any]) -> tuple[Any, Any, Any, bool]:
    extra_positional = list(args)
    if extra_positional:
        response = extra_positional.pop(0)
    else:
        response = _UNSET

    candidate_value: Any = _UNSET
    discovery_value: Any = _UNSET
    discovery_provided = False

    if extra_positional:
        if len(extra_positional) > 2:
            _fail("Too many positional arguments")
        candidate_value = extra_positional.pop(0)
        if extra_positional:
            discovery_value = extra_positional.pop(0)
            discovery_provided = True

    if response is _UNSET:
        found = [kwargs[key] for key in _RESPONSE_KEYS if key in kwargs]
        if len(found) > 1:
            _fail("Multiple envelope arguments supplied")
        if found:
            response = found[0]

    if candidate_value is _UNSET:
        found = [kwargs[key] for key in _CANDIDATE_KEYS if key in kwargs]
        if len(found) > 1:
            _fail("Multiple candidate-ID arguments supplied")
        if found:
            candidate_value = found[0]

    if discovery_value is _UNSET:
        found = [kwargs[key] for key in _DISCOVERY_KEYS if key in kwargs]
        if len(found) > 1:
            _fail("Multiple discovery arguments supplied")
        if found:
            discovery_value = found[0]
            discovery_provided = True

    if not discovery_provided and discovery_value is _UNSET:
        discovery_value = False

    return response, candidate_value, discovery_value, discovery_provided


def validate_discussion_resolution_selection(
    *args: Any,
    **kwargs: Any,
) -> DiscussionResolutionSelectionResult:
    """Validate one closed discussion-resolution selection envelope.

    Positional or keyword aliases are accepted for the envelope, candidate
    IDs, and discovery flag.  See module docstring for the contract.
    """

    raw_response, candidate_value, discovery_value, discovery_provided = _extract_response(
        args, kwargs
    )

    if raw_response is _UNSET:
        _fail("A selection envelope is required")

    if candidate_value is _UNSET:
        candidate_value = None

    if discovery_value is _UNSET:
        discovery_value = False
        discovery_provided = False

    if isinstance(raw_response, (str, bytes, bytearray)) or not isinstance(raw_response, Mapping):
        _fail("Selection envelope must be a JSON object")

    if set(raw_response.keys()) != _CANONICAL_KEYS:
        _fail("Selection envelope keys must be exactly outcome, selected_discussion_ids, and clarification")

    outcome = raw_response["outcome"]
    if type(outcome) is not str or outcome not in ("selected", "clarification"):
        _fail("Selection outcome must be 'selected' or 'clarification'")

    raw_ids = raw_response["selected_discussion_ids"]
    if isinstance(raw_ids, (str, bytes, bytearray)) or not isinstance(raw_ids, Sequence):
        _fail("selected_discussion_ids must be an array, not a string or other scalar")
    try:
        ids_list = list(raw_ids)
    except TypeError:
        _fail("selected_discussion_ids must be an array")

    clarification = raw_response["clarification"]
    if clarification is not None and type(clarification) is not str:
        _fail("clarification must be a string or null")
    if isinstance(clarification, str) and clarification.strip() == "":
        _fail("clarification must be a nonblank string")

    candidate_ids = _validate_candidate_ids(candidate_value)
    candidate_set = set(candidate_ids)

    selected_ids: list[str] = []
    seen_selected: set[str] = set()
    for item in ids_list:
        if type(item) is not str:
            _fail("Selected discussion IDs must be strings, not booleans or other types")
        if item == "" or item != item.strip():
            _fail("Selected discussion IDs cannot be empty or padded")
        if item in seen_selected:
            _fail("Selected discussion IDs must be unique")
        seen_selected.add(item)
        selected_ids.append(item)

    for item in selected_ids:
        if item not in candidate_set:
            _fail("Selected discussion IDs must be exact same-session candidate IDs")

    if discovery_provided:
        if type(discovery_value) is not bool:
            _fail("discovery_complete must be a strict boolean")
        discovery_bool = discovery_value
    else:
        discovery_bool = False

    if outcome == "selected":
        if not selected_ids:
            _fail("A selected outcome requires nonempty selected discussion IDs")
        if clarification is not None:
            _fail("A selected outcome requires clarification to be null")
        if not discovery_bool:
            _fail("A selected outcome requires completed discovery")
    else:
        if selected_ids:
            _fail("A clarification outcome requires selected discussion IDs to be empty")
        if not isinstance(clarification, str) or clarification.strip() == "":
            _fail("A clarification outcome requires a nonblank clarification string")

    return DiscussionResolutionSelectionResult(
        outcome=outcome,
        selected_discussion_ids=tuple(selected_ids),
        clarification=clarification,
    )
