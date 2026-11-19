"""Provider-neutral context working-set contract (S179).

The runtime decides what a model may see. This module carries no credentials,
performs no I/O and invokes no model. A working set is scoped to exact durable
work identity, records provenance and epistemic uncertainty, expires, and can
be cancelled so no further model messages are produced.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Dict, Tuple


class ContextSource(str, Enum):
    TASK_STATE = "task_state"
    GOAL_STATE = "goal_state"
    MEMORY = "memory"
    EVENT = "event"
    TOOL_RESULT = "tool_result"


class EpistemicStatus(str, Enum):
    KNOWN = "KNOWN"          # current authoritative runtime state
    REMEMBERED = "REMEMBERED"  # persisted memory, possibly stale
    OBSERVED = "OBSERVED"    # tool observation this session
    INFERRED = "INFERRED"    # derived by reasoning
    ASSUMED = "ASSUMED"      # unverified
    UNKNOWN = "UNKNOWN"


@dataclass(frozen=True, slots=True)
class ContextScope:
    """Exact durable work identity; a model never supplies one."""

    task_id: str | None = None
    goal_id: str | None = None
    session_id: str | None = None

    def __post_init__(self) -> None:
        for name in ("task_id", "goal_id", "session_id"):
            value = getattr(self, name)
            if value is not None and (
                not isinstance(value, str) or not value.strip()
                or value != value.strip()
            ):
                raise ValueError(f"{name} must be a non-empty trimmed string")
        if not any((self.task_id, self.goal_id, self.session_id)):
            raise ValueError("a context scope requires at least one identity")

    def contains(self, other: "ContextScope") -> bool:
        return all(
            getattr(other, name) in (None, getattr(self, name))
            for name in ("task_id", "goal_id", "session_id")
        )


@dataclass(frozen=True, slots=True)
class ContextItem:
    """One bounded, attributed item eligible for model-visible context."""

    source: ContextSource
    ref: str
    content: str
    owner_scope: ContextScope
    provenance: str
    status: EpistemicStatus = EpistemicStatus.UNKNOWN
    relevant: bool = False
    token_estimate: int = 0

    def __post_init__(self) -> None:
        if not isinstance(self.source, ContextSource):
            raise ValueError("unsupported context source")
        if not isinstance(self.owner_scope, ContextScope):
            raise ValueError("an item requires an exact owner scope")
        if not isinstance(self.status, EpistemicStatus):
            raise ValueError("unsupported epistemic status")
        # Provenance constrains what an item may claim. A tool result, event
        # or memory recall can never be presented as current runtime state.
        allowed = {
            ContextSource.TASK_STATE: {EpistemicStatus.KNOWN},
            ContextSource.GOAL_STATE: {EpistemicStatus.KNOWN},
            ContextSource.TOOL_RESULT: {EpistemicStatus.OBSERVED, EpistemicStatus.INFERRED},
            ContextSource.EVENT: {EpistemicStatus.OBSERVED, EpistemicStatus.INFERRED},
            ContextSource.MEMORY: {
                EpistemicStatus.REMEMBERED, EpistemicStatus.INFERRED,
                EpistemicStatus.ASSUMED, EpistemicStatus.UNKNOWN,
            },
        }
        if self.status not in allowed[self.source]:
            raise ValueError("epistemic status is not valid for this provenance")
        if not isinstance(self.ref, str) or not self.ref.strip():
            raise ValueError("item reference is required")
        if not isinstance(self.content, str) or len(self.content) > 8000:
            raise ValueError("item content must be bounded text")
        if not isinstance(self.provenance, str) or not self.provenance.strip():
            raise ValueError("item provenance is required")
        if not isinstance(self.relevant, bool):
            raise ValueError("relevance must be a boolean")
        if (isinstance(self.token_estimate, bool)
                or not isinstance(self.token_estimate, int)
                or not 0 <= self.token_estimate <= 100_000):
            raise ValueError("token estimate is out of bounds")


@dataclass(frozen=True, slots=True)
class ContextWorkingSet:
    """Immutable, scoped set that can be rendered into model messages."""

    scope: ContextScope
    items: Tuple[ContextItem, ...]
    assembled_at: float
    max_age_seconds: float
    cancelled: bool = False

    def __post_init__(self) -> None:
        if not isinstance(self.scope, ContextScope):
            raise ValueError("working set requires an exact scope")
        if (isinstance(self.max_age_seconds, bool)
                or not isinstance(self.max_age_seconds, (int, float))
                or not 0 < self.max_age_seconds <= 86_400):
            raise ValueError("max_age_seconds is out of bounds")
        for item in self.items:
            if not isinstance(item, ContextItem) or not self.scope.contains(item.owner_scope):
                raise ValueError("working set contains a foreign context item")

    def is_current(self, now: float | None = None) -> bool:
        current = time.monotonic() if now is None else now
        return 0 <= current - self.assembled_at <= self.max_age_seconds

    def cancel(self) -> "ContextWorkingSet":
        return ContextWorkingSet(
            self.scope, self.items, self.assembled_at,
            self.max_age_seconds, cancelled=True,
        )

    def relevance(self) -> float:
        if not self.items:
            return 0.0
        return sum(1 for item in self.items if item.relevant) / len(self.items)

    def token_estimate(self) -> int:
        return sum(item.token_estimate for item in self.items)

    def for_messages(self, now: float | None = None) -> list:
        """Render model messages; empty when cancelled, stale or unscoped."""
        if self.cancelled or not self.is_current(now):
            return []
        rendered = []
        for item in self.items:
            label = (
                f"[{item.source.value} ref={item.ref} "
                f"provenance={item.provenance} status={item.status.value}]"
            )
            rendered.append({"role": "system", "content": f"{label}\n{item.content}"})
        if not rendered:
            return []
        rendered.append({
            "role": "user",
            "content": "Use only the attributed runtime context above; "
                       "uncertain labels are not facts.",
        })
        return rendered


def assemble_working_set(
    scope: ContextScope,
    items: list,
    *,
    max_age_seconds: float = 300.0,
    now: float | None = None,
) -> ContextWorkingSet:
    """Build a fail-closed working set, dropping foreign or malformed items."""
    if not isinstance(scope, ContextScope):
        raise TypeError("scope must be a ContextScope")
    if items is None or isinstance(items, (str, bytes)):
        raise TypeError("items must be a sequence of ContextItem")
    accepted = []
    for item in items:
        if not isinstance(item, ContextItem) or not scope.contains(item.owner_scope):
            continue
        accepted.append(item)
    return ContextWorkingSet(
        scope=scope, items=tuple(accepted),
        assembled_at=time.monotonic() if now is None else now,
        max_age_seconds=max_age_seconds,
    )
