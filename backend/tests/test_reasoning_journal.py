"""Tests for provider-call receipts and safe restart reconciliation."""

import asyncio

import pytest

from backend.services.reasoning_dispatch import (
    ReasoningJob, ReasoningPurpose, ReasoningJournalError, dispatch_reasoning,
)
from backend.services.reasoning_journal import (
    ReasoningJournal, unresolved_reasoning_jobs,
)


class EventRecorder:
    def __init__(self):
        self.records = []

    async def __call__(self, task_id, event_type, data):
        self.records.append({
            "task_id": task_id,
            "event_type": event_type,
            "event_data": dict(data),
        })


@pytest.mark.asyncio
async def test_completed_call_produces_start_and_raw_response_receipts():
    recorder = EventRecorder()
    journal = ReasoningJournal(recorder)
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="t-1")
    async def provider():
        return {"untrusted": True}
    assert await dispatch_reasoning(job, provider, on_event=journal) == {"untrusted": True}
    assert [e["event_type"] for e in recorder.records] == ["reasoning.started", "reasoning.response_received"]
    assert all(e["event_data"] == {"job_id": job.job_id, "purpose": "task_decision"} for e in recorder.records)
    assert all(e["task_id"] == "t-1" for e in recorder.records)
    assert unresolved_reasoning_jobs(recorder.records) == ()


@pytest.mark.asyncio
async def test_rejected_start_receipt_prevents_provider_call():
    calls = 0
    async def recorder(*_args):
        raise RuntimeError("ownership lost")
    async def provider():
        nonlocal calls
        calls += 1
        return "should not happen"
    with pytest.raises(ReasoningJournalError, match="event persistence failed"):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"),
                                 provider, on_event=ReasoningJournal(recorder))
    assert calls == 0


@pytest.mark.asyncio
async def test_failed_completion_receipt_disallows_admitting_response():
    async def recorder(_task, event_type, _data):
        if event_type == "reasoning.response_received":
            raise RuntimeError("ledger unavailable")
    async def provider():
        return "model result"
    with pytest.raises(ReasoningJournalError, match="event persistence failed"):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"),
                                 provider, on_event=ReasoningJournal(recorder))


@pytest.mark.asyncio
async def test_provider_failure_is_recorded_without_retry():
    recorder = EventRecorder()
    calls = 0
    async def provider():
        nonlocal calls
        calls += 1
        raise ConnectionError("offline")
    with pytest.raises(ConnectionError):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"),
                                 provider, on_event=ReasoningJournal(recorder))
    assert calls == 1
    assert [e["event_type"] for e in recorder.records] == ["reasoning.started", "reasoning.failed"]
    assert unresolved_reasoning_jobs(recorder.records) == ()


@pytest.mark.asyncio
async def test_timeout_is_recorded_and_cancels_provider():
    recorder = EventRecorder()
    cancelled = asyncio.Event()
    async def provider():
        try:
            await asyncio.sleep(5)
        finally:
            cancelled.set()
    with pytest.raises(TimeoutError):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, .01, work_id="t"),
                                 provider, on_event=ReasoningJournal(recorder))
    assert cancelled.is_set()
    assert [e["event_type"] for e in recorder.records] == ["reasoning.started", "reasoning.timed_out"]
    assert unresolved_reasoning_jobs(recorder.records) == ()


@pytest.mark.asyncio
async def test_cancellation_is_recorded_and_propagated():
    recorder = EventRecorder()
    entered = asyncio.Event()
    async def provider():
        entered.set()
        await asyncio.Future()
    task = asyncio.create_task(dispatch_reasoning(
        ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="t"),
        provider, on_event=ReasoningJournal(recorder)))
    await asyncio.wait_for(entered.wait(), 1)
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    assert [e["event_type"] for e in recorder.records] == ["reasoning.started", "reasoning.cancelled"]


def test_recovery_finds_crash_after_start_without_claiming_success():
    recorder = EventRecorder()
    old = {"event_type": "reasoning.started", "event_data": {"job_id": "old", "purpose": "task_decision"}}
    complete = [
        {"event_type": "reasoning.started", "event_data": {"job_id": "done"}},
        {"event_type": "reasoning.response_received", "event_data": {"job_id": "done"}},
    ]
    assert unresolved_reasoning_jobs([old] + complete) == ("old",)
    assert unresolved_reasoning_jobs([{"event_type": "reasoning.failed", "event_data": {"job_id": "old"}}, old]) == ("old",)


def test_recovery_ignores_malformed_or_unrelated_events():
    events = [None, {}, {"event_type": "task.completed", "event_data": {"job_id": "x"}},
              {"event_type": "reasoning.started", "event_data": None},
              {"event_type": "reasoning.started", "event_data": {"job_id": ""}},
              {"event_type": "reasoning.started", "event_data": {"job_id": "x"}},
              {"event_type": "reasoning.failed", "event_data": {"job_id": "other"}}]
    assert unresolved_reasoning_jobs(events) == ("x",)


@pytest.mark.asyncio
async def test_journal_rejects_non_task_call_without_touching_ledger():
    rec = EventRecorder()
    with pytest.raises(ValueError, match="task decision"):
        await ReasoningJournal(rec)(ReasoningJob(ReasoningPurpose.CONVERSATION, 1), "started")
    assert rec.records == []


@pytest.mark.asyncio
async def test_journal_rejects_unknown_phase():
    rec = EventRecorder()
    with pytest.raises(ValueError, match="phase"):
        await ReasoningJournal(rec)(ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"), "approved")
    assert rec.records == []


@pytest.mark.asyncio
async def test_cancel_receipt_failure_does_not_swallow_cancel():
    entered = asyncio.Event()
    async def recorder(_task, event_type, _data):
        if event_type == "reasoning.cancelled":
            raise OSError("disk unavailable")
    async def provider():
        entered.set()
        await asyncio.Future()
    task = asyncio.create_task(dispatch_reasoning(
        ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="t"), provider,
        on_event=ReasoningJournal(recorder)))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task


@pytest.mark.asyncio
async def test_journal_failure_on_timeout_prevents_reuse_of_model_result():
    async def recorder(_task, event_type, _data):
        if event_type == "reasoning.timed_out":
            raise OSError("ledger unavailable")
    async def provider():
        await asyncio.sleep(3)
    with pytest.raises(ReasoningJournalError, match="event persistence failed"):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, .01, work_id="t"),
                                 provider, on_event=ReasoningJournal(recorder))
