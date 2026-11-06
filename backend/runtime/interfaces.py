"""Narrow runtime interfaces (Protocols) for the protected kernel.

These Protocols define what the intelligence layer may *request* from the
runtime. Implementations live inside the protected runtime and remain the
final authority over execution, state, audit, and policy.

Design rules:
- Narrow interfaces, no service locator.
- No raw DB sessions exposed for runtime writes.
- Implementations must preserve the invariants in
  README.md.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class EventLedger(Protocol):
    """Append-only, immutable task-event ledger interface.

    The ledger is authoritative: events are ordered by ``sequence_num`` per
    task and are never rewritten or deleted by higher layers.
    """

    def append_event(
        self,
        task_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> int:
        """Append one event; returns its sequence number. Does not commit —
        the owning state-transition transaction commits."""
        ...

    def event_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Full ordered history for a task as decoded dicts."""
        ...

    def events_after(self, task_id: str, after_sequence: int) -> List[Dict[str, Any]]:
        """Ordered events with sequence_num > after_sequence."""
        ...

    def latest_sequence(self, task_id: str) -> int:
        """Current maximum sequence number for a task (0 if none)."""
        ...


@runtime_checkable
class TaskStateStore(Protocol):
    """Authoritative reader/writer of task lifecycle state.

    Only this boundary may mutate ``tasks.state`` (and related mutable task
    columns). Intelligence components receive an instance; they never UPDATE
    task rows themselves.
    """

    async def get_task(self, task_id: str) -> Optional[Any]:
        """Read a task snapshot (or None)."""
        ...

    async def create_task(
        self,
        original_request: str,
        idempotency_key: Optional[str] = None,
    ) -> Any:
        ...

    async def transition_state(
        self,
        task_id: str,
        new_state: Any,
        event_type: str,
        event_data: Dict[str, Any],
        **updates: Any,
    ) -> Any:
        """Validate and apply a state transition, appending the audit event."""
        ...

    async def record_event(
        self,
        task_id: str,
        event_type: str,
        event_data: Dict[str, Any],
    ) -> None:
        """Append a durable event without changing state."""
        ...


@runtime_checkable
class LeaseManager(Protocol):
    """Exclusive single-runner lease over the profile's execution."""

    async def acquire(self, task_id: Optional[str] = None) -> bool: ...
    async def renew(self, task_id: Optional[str] = None) -> bool: ...
    async def release(self) -> None: ...
    @property
    def nonce(self) -> Optional[str]:
        """Opaque ownership token of the current holder (None if unheld)."""
        ...


@runtime_checkable
class ModelClient(Protocol):
    """What the reasoning layer actually needs from a model provider.

    Implementations wrap concrete providers (currently Ollama Cloud only).
    The reasoning layer must not import provider modules directly.
    """

    async def decide(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        """One decision request; returns the raw provider response dict."""
        ...

    def provider_name(self) -> str:
        """Stable identifier of the underlying provider (diagnostics only)."""
        ...
