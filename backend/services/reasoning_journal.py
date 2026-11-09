"""Privacy-minimal task reasoning receipts over the existing task event ledger.

The journal sees only invocation metadata. It is not a scheduler, an execution
receipt, a grant of tool authority, or proof that model output was valid.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from typing import Any, Awaitable, Callable

from backend.services.reasoning_dispatch import ReasoningJob, ReasoningPurpose

REASONING_TERMINAL_PHASES = frozenset({
    "response_received", "timed_out", "failed", "cancelled",
})
REASONING_PHASES = REASONING_TERMINAL_PHASES | {"started"}


class ReasoningJournal:
    """Bind reasoner lifecycle to an ownership-checked task-event recorder.

    The recorder must persist/commit an immutable event before returning and
    refuse calls when task ownership has been lost. This observer never reads
    a prompt, tool parameters, model output, or private reasoning text.
    """

    def __init__(self, record_event: Callable[[str, str, dict], Awaitable[None]]):
        if not callable(record_event):
            raise TypeError("record_event must be callable")
        self._record_event = record_event

    async def __call__(self, job: ReasoningJob, phase: str) -> None:
        if not isinstance(job, ReasoningJob):
            raise TypeError("reasoning job is required")
        if job.purpose is not ReasoningPurpose.TASK_DECISION or not job.work_id:
            raise ValueError("durable reasoning receipt requires a task decision")
        if phase not in REASONING_PHASES:
            raise ValueError("unsupported reasoning phase")
        await self._record_event(
            job.work_id,
            f"reasoning.{phase}",
            {"job_id": job.job_id, "purpose": job.purpose.value},
        )


def unresolved_reasoning_jobs(events: Iterable[Mapping[str, Any]]) -> tuple[str, ...]:
    """Identify invocation starts with no matching terminal receipt.

    The caller provides ledger events in authoritative sequence order. These
    IDs are *uncertain past calls*, never authority to replay tools or claim a
    model result. Does not mutate events, ask a model, or infer success.
    """
    pending: dict[str, None] = {}
    for event in events:
        if not isinstance(event, Mapping):
            continue
        kind = event.get("event_type")
        if kind not in ({"reasoning.started"} | {
            f"reasoning.{phase}" for phase in REASONING_TERMINAL_PHASES
        }):
            continue
        data = event.get("event_data")
        if not isinstance(data, Mapping):
            continue
        job_id = data.get("job_id")
        if not isinstance(job_id, str) or not job_id.strip():
            continue
        if kind == "reasoning.started":
            pending[job_id] = None
        else:
            pending.pop(job_id, None)
    return tuple(pending)
