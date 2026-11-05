"""Explicit dependency wiring for the API layer (Phase 1 composition root).

Replaces the previous function-attribute injection
(``get_tool_executor._executor = ...``) with an explicit provider set by the
application lifespan. This is deliberately NOT a service locator: exactly one
dependency (the shared ToolExecutor) flows through here, everything else is
constructor-injected.

Fallback behavior: when no provider has been installed (unit tests, dev runs
without the lifespan), a fully-registered ToolExecutor is built on demand so
the API surface keeps working identically. Since Phase 0, DB-approved tools
are never loaded into any executor, so all executors have identical content.
"""

from __future__ import annotations

import threading
from typing import Any, Callable, Optional

from backend.tools.executor import ToolExecutor

_provider: Optional[Callable[[], ToolExecutor]] = None
_fallback_lock = threading.Lock()
_fallback_executor: Optional[ToolExecutor] = None


def set_executor_provider(provider: Callable[[], ToolExecutor]) -> None:
    """Install the application's single shared executor provider."""
    global _provider
    _provider = provider


def reset_executor_provider() -> None:
    """Test helper: drop the installed provider."""
    global _provider
    _provider = None


def _build_fallback() -> ToolExecutor:
    global _fallback_executor
    with _fallback_lock:
        if _fallback_executor is None:
            from backend.tools.registry import register_all_tools

            executor = ToolExecutor()
            register_all_tools(executor)
            _fallback_executor = executor
    return _fallback_executor


def get_tool_executor() -> ToolExecutor:
    """Return the runtime's shared ToolExecutor."""
    if _provider is not None:
        return _provider()
    return _build_fallback()


# Optional, explicitly injected grounded discussion linkage service.  Default is
# None, so production behavior is unchanged unless a caller installs a
# provider.
_grounded_discussion_provider = None


def set_grounded_discussion_provider(provider: Any = None) -> None:
    global _grounded_discussion_provider
    _grounded_discussion_provider = provider


def set_discussion_linkage_provider(provider: Any = None) -> None:
    set_grounded_discussion_provider(provider)


def reset_grounded_discussion_provider() -> None:
    global _grounded_discussion_provider
    _grounded_discussion_provider = None


def reset_discussion_linkage_provider() -> None:
    reset_grounded_discussion_provider()


def get_grounded_discussion_provider() -> Any:
    return _grounded_discussion_provider


def get_discussion_linkage_provider() -> Any:
    return _grounded_discussion_provider
