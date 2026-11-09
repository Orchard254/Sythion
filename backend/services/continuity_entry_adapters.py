"""Voice/assistant entry adapters for the continuity kernel.

Smallest coherent wiring step (checklist S014/S088): each user-facing entry
path can build the same scoped memory-access context from its own
task/session identity, and the continuity-kernel context for its turn,
without any route or runtime rewrite.

Every function is gated by :data:`CONTINUITY_KERNEL_ENABLED` (default
``False``). With the flag off the adapters return ``None`` and callers fall
back to the historical task/session-only selection path unchanged. The flag
is *not* activated by importing this module; callers opt in explicitly via
the ``enabled=`` argument (used by tests and future integration). No model,
tool, voice, or database work happens here: this module only builds the
typed context objects.
"""

from __future__ import annotations

from typing import Optional

from backend.services.continuity_kernel_contract import (
    CONTINUITY_KERNEL_ENABLED,
    ContinuityKernelContext,
)
from backend.services.memory_service import MemoryAccessContext

__all__ = [
    "entry_enabled",
    "entry_access_context",
    "assistant_entry_context",
    "voice_entry_context",
    "kernel_context_for",
]


def entry_enabled(enabled: Optional[bool] = None) -> bool:
    """Resolve the adapter gate; defaults to the import-time disabled flag."""
    return bool(CONTINUITY_KERNEL_ENABLED if enabled is None else enabled)


def entry_access_context(
    *,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[MemoryAccessContext]:
    """Scoped memory-access context for a continuity entry turn.

    Returns ``None`` while the kernel flag is disabled so callers keep the
    legacy behavior. When enabled, the returned context carries exactly the
    entry's task/session identity (plus goal linkage) for scoped selection.
    """
    if not entry_enabled(enabled):
        return None
    return MemoryAccessContext(
        session_id=session_id, task_id=task_id, goal_id=goal_id
    )


def assistant_entry_context(
    *,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[MemoryAccessContext]:
    """Assistant-turn (``/assistant/turns``) entry adapter."""
    return entry_access_context(
        session_id=session_id,
        task_id=task_id,
        goal_id=goal_id,
        enabled=enabled,
    )


def voice_entry_context(
    *,
    voice_session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[MemoryAccessContext]:
    """Voice-session entry adapter.

    Maps the voice capture session id onto the memory SESSION identity so a
    spoken turn gets the same scoped selection as a typed turn once the
    kernel is enabled. Returns ``None`` while disabled.
    """
    return entry_access_context(
        session_id=voice_session_id,
        task_id=task_id,
        goal_id=goal_id,
        enabled=enabled,
    )


def kernel_context_for(
    *,
    session_id: Optional[str] = None,
    task_id: Optional[str] = None,
    goal_id: Optional[str] = None,
    conversation_id: Optional[str] = None,
    request: Optional[str] = None,
    enabled: Optional[bool] = None,
) -> Optional[ContinuityKernelContext]:
    """Kernel context contract instance for one entry turn, or ``None``."""
    if not entry_enabled(enabled):
        return None
    return ContinuityKernelContext(
        session_id=session_id,
        task_id=task_id,
        goal_id=goal_id,
        conversation_id=conversation_id,
        request=request,
        enabled=True,
    )
