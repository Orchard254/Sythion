"""Safe boundary between one goal worker and its durable outcome handoff.

A worker returning or raising is not proof of task success/failure. The
orchestrator must inspect committed task/step evidence after this returns;
failures here halt the bounded cycle without authorizing retries or steps.
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from typing import Any, Awaitable, Callable


_LOG = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class GoalWorkerHandoff:
    settled_without_error: bool
    stop_reason: str | None = None


async def run_goal_worker_handoff(
    *,
    worker: Callable[..., Awaitable[Any]],
    task_id: str,
    tool_executor: Any,
    apply_outcome: Callable[[str], Awaitable[Any]],
) -> GoalWorkerHandoff:
    """Run once and apply persisted outcome, refusing to infer success.

    Ordinary Exception failures are contained and surfaced only as a fixed
    stop category; an asyncio cancellation (BaseException) propagates. A
    crash while applying evidence must not cause another tool attempt.
    """
    if not callable(worker) or not callable(apply_outcome):
        raise TypeError("worker and outcome applier must be callable")
    if not isinstance(task_id, str) or not task_id.strip():
        raise ValueError("task ID required")
    try:
        await worker(None, task_id, tool_executor)
        await apply_outcome(task_id)
    except Exception as exc:
        # The exception TYPE is useful for diagnostics; logging its text or
        # traceback could reveal file paths, tool results or credentials.
        _LOG.warning("Goal worker handoff failed: %s", type(exc).__name__)
        # No exception message, paths, secrets, or tool payloads in the
        # result. The caller may log a coarse category and roll back its
        # own unfinished SQLAlchemy transaction before returning.
        return GoalWorkerHandoff(False, "worker_or_outcome_error")
    return GoalWorkerHandoff(True)
