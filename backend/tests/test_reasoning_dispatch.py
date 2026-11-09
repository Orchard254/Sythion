"""Focused contract tests; no provider, network, profile or database needed."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from backend.services.reasoning_dispatch import (
    ReasoningJob,
    ReasoningPurpose,
    dispatch_reasoning,
)


@pytest.mark.asyncio
async def test_conversation_result_is_returned_without_side_effects():
    calls = []

    async def model():
        calls.append("called")
        return '{"assistant_text":"Hello","commands":[]}'

    job = ReasoningJob(ReasoningPurpose.CONVERSATION, timeout_seconds=2)
    assert await dispatch_reasoning(job, model) == '{"assistant_text":"Hello","commands":[]}'
    assert calls == ["called"]
    assert job.work_id is None


@pytest.mark.asyncio
async def test_task_decision_retains_work_reference_and_raw_result():
    raw = {"message": {"tool_calls": [{"function": {"name": "complete_task"}}]}}
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="task-123")

    async def model():
        return raw

    assert await dispatch_reasoning(job, model) is raw
    assert job.work_id == "task-123"


@pytest.mark.asyncio
async def test_dispatch_does_not_retry_provider_errors():
    calls = 0

    async def fail():
        nonlocal calls
        calls += 1
        raise ConnectionError("offline")

    with pytest.raises(ConnectionError, match="offline"):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, 2), fail)
    assert calls == 1


@pytest.mark.asyncio
async def test_deadline_cancels_underlying_provider_call():
    cancelled = asyncio.Event()

    async def hanging():
        try:
            await asyncio.sleep(10)
        finally:
            cancelled.set()

    with pytest.raises(TimeoutError):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.CONVERSATION, 0.01), hanging)
    assert cancelled.is_set()


@pytest.mark.asyncio
async def test_parent_cancellation_propagates_to_provider():
    entered = asyncio.Event()
    cancelled = asyncio.Event()

    async def hanging():
        entered.set()
        try:
            await asyncio.Future()
        finally:
            cancelled.set()

    parent = asyncio.create_task(dispatch_reasoning(ReasoningJob(ReasoningPurpose.CONVERSATION, 2), hanging))
    await asyncio.wait_for(entered.wait(), 1)
    parent.cancel()
    with pytest.raises(asyncio.CancelledError):
        await parent
    assert cancelled.is_set()


@pytest.mark.parametrize("bad", [0, -1, float("inf"), float("nan"), True, "60"])
def test_invalid_timeouts_rejected(bad):
    with pytest.raises(ValueError, match="timeout_seconds"):
        ReasoningJob(ReasoningPurpose.CONVERSATION, bad)


@pytest.mark.parametrize("bad", ["", "  ", " trailing ", 1, False])
def test_invalid_work_ids_rejected(bad):
    with pytest.raises(ValueError, match="work_id"):
        ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id=bad)


def test_invalid_purpose_rejected():
    with pytest.raises(ValueError, match="purpose"):
        ReasoningJob("do_everything", 1)


def test_job_cannot_mutate_authority_metadata():
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="t1")
    with pytest.raises(FrozenInstanceError):
        job.work_id = "other"
    assert job.job_id != ReasoningJob(ReasoningPurpose.TASK_DECISION, 2).job_id


@pytest.mark.asyncio
async def test_invalid_provider_callable_is_not_silently_retried():
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 1)
    with pytest.raises(TypeError, match="awaitable"):
        await dispatch_reasoning(job, lambda: "not awaitable")
    with pytest.raises(TypeError, match="ReasoningJob"):
        await dispatch_reasoning("invalid", lambda: None)


@pytest.mark.asyncio
async def test_null_result_remains_untrusted_for_caller_validation():
    async def model():
        return None

    assert await dispatch_reasoning(ReasoningJob(ReasoningPurpose.CONVERSATION, 1), model) is None
