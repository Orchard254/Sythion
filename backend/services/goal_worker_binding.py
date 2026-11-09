"""Fail-closed binding of the real task worker to the registered ToolExecutor.

Goal orchestration may use an injected worker for deterministic tests. In
production, it must pass the already-registered application executor to the
real task loop; a missing provider must never create a RUNNING task that can
only fail once the worker tries to use tools. No tool is invoked here.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Callable


@dataclass(frozen=True)
class GoalWorkerBinding:
    run: Callable[..., Any]
    tool_executor: Any = field(repr=False)
    available: bool
    stop_reason: str | None = None


def bind_goal_worker(
    *,
    override: Callable[..., Any] | None,
    default_worker: Callable[..., Any],
    executor_provider: Callable[[], Any],
) -> GoalWorkerBinding:
    """Resolve tooling BEFORE creating any goal-backed task.

    An explicitly supplied worker owns its test-specific dependencies and
    keeps the previous `(None, task_id, None)` injection contract. The real
    worker must get the same executor provider used by ordinary API tasks.
    A provider error or missing executor fails closed and starts no task.
    """
    if override is not None:
        if not callable(override):
            raise TypeError("injected goal worker must be callable")
        return GoalWorkerBinding(override, None, True)
    if not callable(default_worker) or not callable(executor_provider):
        raise TypeError("default worker and executor provider must be callable")
    try:
        executor = executor_provider()
    except Exception:
        return GoalWorkerBinding(default_worker, None, False, "tool_executor_unavailable")
    if executor is None:
        return GoalWorkerBinding(default_worker, None, False, "tool_executor_unavailable")
    return GoalWorkerBinding(default_worker, executor, True)
