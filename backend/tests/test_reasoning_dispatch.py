"""Focused contract tests; no provider, network, profile or database needed."""

import asyncio
from dataclasses import FrozenInstanceError

import pytest

from backend.services.reasoning_dispatch import (
    AdmissionDecision,
    AttentionSignals,
    DispatchVerdict,
    ReasoningAdmissionError,
    acquire_dispatch_slot,
    decide_attention,
    release_dispatch_slot,
    ReasoningJob,
    ReasoningPurpose,
    ReasoningResult,
    ReasoningResultError,
    admit_reasoning_job,
    as_mapping_result,
    as_text_result,
    dispatch_reasoning,
    get_reasoning_metrics,
    handoff_reasoning_result,
    ReasoningJournalError,
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


def test_text_result_carries_job_and_provider_identity_only():
    job = ReasoningJob(
        ReasoningPurpose.CONVERSATION, 2, provider_id="ollama-cloud")
    result = as_text_result(job, "Hello")
    assert isinstance(result, ReasoningResult)
    assert result.job_id == job.job_id
    assert result.purpose is ReasoningPurpose.CONVERSATION
    assert result.provider_id == "ollama-cloud"
    assert result.value == "Hello"
    fields = {f.name for f in __import__("dataclasses").fields(ReasoningResult)}
    assert fields == {
        "job_id", "purpose", "provider_id", "work_id", "value", "contract_version",
    }
    # No authorization, consent, tool-call or success fields may be added.
    assert not any(word in f for f in fields for word in (
        "approv", "consent", "permission", "tool", "grant", "success", "state"))


def test_text_result_rejects_untrusted_shapes():
    job = ReasoningJob(ReasoningPurpose.CONVERSATION, 2, provider_id="openrouter")
    with pytest.raises(ReasoningResultError):
        as_text_result(job, {"assistant_text": "Hello"})
    with pytest.raises(ReasoningResultError):
        as_text_result(job, True)
    with pytest.raises(ReasoningResultError):
        as_text_result(job, "x" * 11, max_chars=10)


def test_mapping_result_carries_job_and_provider_identity_only():
    job = ReasoningJob(
        ReasoningPurpose.TASK_DECISION, 2, work_id="task-1",
        provider_id="openrouter")
    payload = {"message": {"tool_calls": []}, "usage": {"total_tokens": 1}}
    result = as_mapping_result(job, payload)
    assert isinstance(result, ReasoningResult)
    assert result.job_id == job.job_id
    assert result.purpose is ReasoningPurpose.TASK_DECISION
    assert result.provider_id == "openrouter"
    assert result.value == payload
    assert result.value is not payload  # validated copy, never the raw object
    fields = {f.name for f in __import__("dataclasses").fields(ReasoningResult)}
    assert fields == {
        "job_id", "purpose", "provider_id", "work_id", "value", "contract_version",
    }


def test_mapping_result_rejects_untrusted_shapes():
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2)
    for raw in (None, "text", True, [1, 2]):
        with pytest.raises(ReasoningResultError):
            as_mapping_result(job, raw)
    with pytest.raises(ValueError):
        as_mapping_result(job, {"message": {}}, max_keys=0)
    with pytest.raises(ReasoningResultError):
        as_mapping_result(job, {1: "not a string key"})


def test_result_contract_version_is_enforced():
    job = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=60.0,
        provider_id="openrouter").job
    result = as_text_result(job, "hello")
    assert result.contract_version == 1
    with pytest.raises(ReasoningResultError, match="contract version"):
        ReasoningResult(
            result.job_id, result.purpose, result.provider_id,
            result.work_id, result.value, contract_version=999)


def test_job_provider_identity_must_be_trimmed_string():
    with pytest.raises(ValueError):
        ReasoningJob(ReasoningPurpose.CONVERSATION, 2, provider_id=" ollama-cloud ")


def test_unified_admission_creates_a_bounded_job_with_processor_identity():
    decision = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=120.0,
        provider_id="ollama-cloud", work_id="task-1")
    assert isinstance(decision, AdmissionDecision)
    assert decision.admitted is True
    assert decision.job.purpose is ReasoningPurpose.TASK_DECISION
    assert decision.job.provider_id == "ollama-cloud"
    assert decision.job.work_id == "task-1"
    assert decision.job.timeout_seconds == 120.0


@pytest.mark.parametrize("kwargs", [
    {"provider_id": ""},
    {"provider_id": " ollama-cloud "},
    {"timeout_seconds": 0},
    {"timeout_seconds": -1},
    {"timeout_seconds": 301},
    {"timeout_seconds": True},
    {"work_id": ""},
])
def test_admission_fails_closed(kwargs):
    params = dict(
        timeout_seconds=60.0, provider_id="openrouter", work_id="task-1")
    params.update(kwargs)
    with pytest.raises(ReasoningAdmissionError):
        admit_reasoning_job(ReasoningPurpose.TASK_DECISION, **params)


def test_admission_requires_work_identity_for_task_decisions():
    with pytest.raises(ReasoningAdmissionError):
        admit_reasoning_job(
            ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
            provider_id="openrouter")


def test_handoff_rejects_a_result_from_a_different_job():
    job = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=60.0,
        provider_id="openrouter").job
    other = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=60.0,
        provider_id="openrouter").job
    result = as_text_result(job, "Hello")
    with pytest.raises(ReasoningResultError):
        handoff_reasoning_result(result, other)


def test_handoff_rejects_wrong_processor_purpose_and_work():
    job = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
        provider_id="openrouter", work_id="task-1").job
    result = as_mapping_result(job, {"choices": []})
    for wrong in (
        ReasoningJob(ReasoningPurpose.CONVERSATION, 60.0, job.work_id,
                     job.job_id, "groq"),
        ReasoningJob(ReasoningPurpose.TASK_DECISION, 60.0, "other-task",
                     job.job_id, job.provider_id),
    ):
        with pytest.raises(ReasoningResultError):
            handoff_reasoning_result(result, wrong)


def test_handoff_accepts_the_admitted_job_result():
    job = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
        provider_id="openrouter", work_id="task-1").job
    result = as_mapping_result(job, {"choices": []})
    assert handoff_reasoning_result(result, job) is result


def test_attention_can_choose_do_nothing_without_any_model_call():
    decision = decide_attention(AttentionSignals())
    assert decision.verdict is DispatchVerdict.DO_NOTHING
    assert "nothing" in decision.reason
    with pytest.raises(ReasoningAdmissionError, match="do_nothing"):
        admit_reasoning_job(
            ReasoningPurpose.CONVERSATION, timeout_seconds=60.0,
            provider_id="openrouter", signals=AttentionSignals())


@pytest.mark.parametrize("signals,verdict", [
    (AttentionSignals(pending_mutating_action=True), DispatchVerdict.DEFER),
    (AttentionSignals(provider_available=False), DispatchVerdict.DEFER),
    (AttentionSignals(budget_available=False), DispatchVerdict.DEFER),
    (AttentionSignals(has_new_input=True), DispatchVerdict.DISPATCH),
    (AttentionSignals(work_active=True), DispatchVerdict.DISPATCH),
])
def test_attention_verdicts_are_deterministic_and_fail_closed(signals, verdict):
    assert decide_attention(signals).verdict is verdict


@pytest.mark.asyncio
async def test_dispatch_gate_blocks_overlapping_calls_for_the_same_work():
    first = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=5,
        provider_id="openrouter", work_id="shared-work").job
    second = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=5,
        provider_id="openrouter", work_id="shared-work").job
    started = asyncio.Event()
    finish = asyncio.Event()

    async def slow():
        started.set()
        await finish.wait()
        return {"first": True}

    async def quick():
        return {"second": True}

    task = asyncio.create_task(dispatch_reasoning(first, slow))
    await asyncio.wait_for(started.wait(), timeout=1)
    with pytest.raises(ReasoningAdmissionError):
        await dispatch_reasoning(second, quick)
    finish.set()
    assert await task == {"first": True}


@pytest.mark.asyncio
async def test_dispatch_slot_is_released_after_failure():
    def job():
        return admit_reasoning_job(
            ReasoningPurpose.TASK_DECISION, timeout_seconds=5,
            provider_id="openrouter", work_id="retryable-work").job

    async def fail():
        raise RuntimeError("provider failed")

    async def ok():
        return {"ok": True}

    with pytest.raises(RuntimeError):
        await dispatch_reasoning(job(), fail)
    assert await dispatch_reasoning(job(), ok) == {"ok": True}


def test_dispatch_slot_key_is_scoped_by_purpose_and_work():
    chat = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=5,
        provider_id="openrouter").job
    plan = admit_reasoning_job(
        ReasoningPurpose.GOAL_PLANNING, timeout_seconds=5,
        provider_id="openrouter", work_id="g1").job
    acquire_dispatch_slot(chat)
    acquire_dispatch_slot(plan)  # different purpose/work: allowed
    release_dispatch_slot(chat)
    release_dispatch_slot(plan)


@pytest.mark.asyncio
async def test_null_result_remains_untrusted_for_caller_validation():
    async def model():
        return None

    assert await dispatch_reasoning(ReasoningJob(ReasoningPurpose.CONVERSATION, 1), model) is None


@pytest.mark.asyncio
async def test_failed_start_receipt_leaves_no_phantom_model_metrics():
    """A start-receipt persistence failure must not count a model call or leak
    an _started entry — no provider was invoked, so the effectiveness report
    must stay honest.
    """
    from backend.services.reasoning_journal import ReasoningJournal

    metrics = get_reasoning_metrics()
    metrics.reset()

    async def recorder(*_args):
        raise RuntimeError("ownership lost")

    async def provider():
        raise AssertionError("provider must not run when the start receipt fails")

    with pytest.raises(ReasoningJournalError, match="event persistence failed"):
        await dispatch_reasoning(
            ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"),
            provider, on_event=ReasoningJournal(recorder),
        )

    report = metrics.report()
    assert report.model_calls == 0
    assert report.model_responses == 0
    assert report.failed_calls == 0
    assert metrics._started == {}
    metrics.reset()


@pytest.mark.asyncio
async def test_failed_completion_receipt_leaves_no_leaked_started_metrics():
    """Completion-phase counterpart: if the response_received receipt cannot
    be persisted, the provider already ran but the in-flight metrics must
    still be balanced — no leaked _started entry and a terminal outcome.
    """
    from backend.services.reasoning_journal import ReasoningJournal

    metrics = get_reasoning_metrics()
    metrics.reset()

    async def recorder(_task_id, event_type, _data):
        if event_type == "reasoning.response_received":
            raise RuntimeError("ledger unavailable")

    async def provider():
        return {"untrusted": True}

    with pytest.raises(ReasoningJournalError, match="event persistence failed"):
        await dispatch_reasoning(
            ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="t"),
            provider, on_event=ReasoningJournal(recorder),
        )

    report = metrics.report()
    assert metrics._started == {}
    assert report.model_calls == 1
    assert report.failed_calls == 1  # balanced terminal outcome recorded
    assert report.model_responses == 0
    metrics.reset()
