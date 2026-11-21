"""Fail-closed audit of durable task reasoning receipt chronology.

A lifecycle receipt is evidence of a provider call, not authorization or proof
that a tool had no effect. This auditor deliberately accepts only the tiny
metadata schema emitted by the task reasoning journal. Unknown, duplicated,
or orphaned receipts cannot be used to declare an interrupted task safe.

Do not return job IDs, prompts, tool arguments or raw event payloads from the
audit. Event rows must be ordered by the *committed* task-event sequence.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from typing import Any

_TERMINAL = frozenset({
    "reasoning.response_received", "reasoning.timed_out",
    "reasoning.failed", "reasoning.cancelled",
})
_START = "reasoning.started"
_RECOGNIZED = _TERMINAL | {_START}


@dataclass(frozen=True, slots=True)
class ReasoningReceiptAudit:
    """Sanitized finding. Categories describe faults, not sensitive IDs."""

    starts: int
    terminals: int
    unresolved_count: int
    anomalies: tuple[str, ...]

    @property
    def valid(self) -> bool:
        return not self.anomalies


def audit_reasoning_receipts(events: Iterable[Mapping[str, Any]]) -> ReasoningReceiptAudit:
    """Validate job pairing, metadata and sequence integrity, without repairs.

    Other task-event kinds are ignored; unknown `reasoning.*` kinds are *not*.
    Sequence numbers can have gaps because unrelated task events interleave.
    They cannot repeat or run backward within the passed reasoning stream.
    """
    if events is None:
        raise TypeError("events must be an iterable")

    active: set[str] = set()
    seen: set[str] = set()
    problems: set[str] = set()
    last_sequence = 0
    starts = terminals = 0
    for row in events:
        if not isinstance(row, Mapping):
            problems.add("invalid_event")
            continue
        kind = row.get("event_type")
        if not isinstance(kind, str):
            problems.add("invalid_event")
            continue
        if not kind.startswith("reasoning."):
            continue
        if kind not in _RECOGNIZED:
            problems.add("unknown_reasoning_event")
            continue

        data = row.get("event_data")
        if (not isinstance(data, Mapping)
                or set(data) != {"job_id", "purpose"}
                or data.get("purpose") != "task_decision"
                or not isinstance(data.get("job_id"), str)
                or not data["job_id"]
                or len(data["job_id"]) > 128
                or data["job_id"] != data["job_id"].strip()):
            problems.add("invalid_receipt_metadata")
            continue

        # Only accepted receipts participate in the sequence chronology. A
        # malformed high-sequence event rejected above must not advance the
        # watermark and falsely flag every later legitimate receipt as
        # invalid_sequence — the real fault is its rejected metadata.
        sequence = row.get("sequence_num")
        if type(sequence) is not int or sequence <= last_sequence:
            problems.add("invalid_sequence")
        else:
            last_sequence = sequence

        job_id = data["job_id"]
        if kind == _START:
            starts += 1
            if job_id in seen:
                problems.add("duplicate_start")
            seen.add(job_id)
            active.add(job_id)
        else:
            terminals += 1
            if job_id not in active:
                problems.add("terminal_without_start")
            else:
                active.remove(job_id)
    return ReasoningReceiptAudit(
        starts=starts,
        terminals=terminals,
        unresolved_count=len(active),
        anomalies=tuple(sorted(problems)),
    )
