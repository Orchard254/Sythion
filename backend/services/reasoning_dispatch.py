"""Provider-agnostic, bounded reasoning invocation boundary.

The runtime owns work state, policy, execution and evidence. A reasoning job
only requests computation from a model; its result never authorizes effects.
This small boundary does not schedule autonomous work or persist tasks.
"""

from __future__ import annotations

import asyncio
import inspect
import math
import uuid
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import Enum
from typing import TypeVar


class ReasoningPurpose(str, Enum):
    CONVERSATION = "conversation"
    TASK_DECISION = "task_decision"


@dataclass(frozen=True, slots=True)
class ReasoningJob:
    """Metadata for one bounded provider call, not an execution grant.

    ``work_id`` associates a reasoning call with existing durable work when
    available. ``job_id`` uniquely identifies the invocation, not the goal.
    No prompt, credentials or model response are stored in this metadata.
    """

    purpose: ReasoningPurpose
    timeout_seconds: float
    work_id: str | None = None
    job_id: str = field(default_factory=lambda: uuid.uuid4().hex)

    def __post_init__(self) -> None:
        if not isinstance(self.purpose, ReasoningPurpose):
            raise ValueError("unsupported reasoning purpose")
        timeout = self.timeout_seconds
        if (
            isinstance(timeout, bool)
            or not isinstance(timeout, (int, float))
            or not math.isfinite(timeout)
            or timeout <= 0
        ):
            raise ValueError("timeout_seconds must be a positive finite number")
        if self.work_id is not None and (
            not isinstance(self.work_id, str)
            or not self.work_id.strip()
            or self.work_id != self.work_id.strip()
        ):
            raise ValueError("work_id must be a non-empty, trimmed string")
        if not isinstance(self.job_id, str) or not self.job_id.strip():
            raise ValueError("job_id must be a non-empty string")


T = TypeVar("T")


async def dispatch_reasoning(
    job: ReasoningJob, call: Callable[[], Awaitable[T]]
) -> T:
    """Invoke one model call under a deadline; return its *untrusted* output.

    No retries, tool execution, state writes, permission approvals, or success
    claims happen here. Cancellation and provider failures propagate to the
    existing caller's error handling. The caller validates the model output.
    """
    if not isinstance(job, ReasoningJob):
        raise TypeError("job must be a ReasoningJob")
    if not callable(call):
        raise TypeError("call must be callable")
    awaitable = call()
    if not inspect.isawaitable(awaitable):
        raise TypeError("call must return an awaitable")
    return await asyncio.wait_for(awaitable, timeout=job.timeout_seconds)
