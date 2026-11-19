"""S183 acceptance: measure usefulness, not model activity.

Also covers the event-driven dispatch decision (direct procedure, model,
waiting, idle) and its observable event/measurement record.
"""

import pytest

from backend.services.reasoning_dispatch import (
    AttentionDecision,
    AttentionSignals,
    DispatchEvent,
    DispatchVerdict,
    ReasoningAdmissionError,
    ReasoningEvent,
    ReasoningEventBus,
    ReasoningMetrics,
    ReasoningPurpose,
    admit_reasoning_job,
    decide_attention,
    dispatch_reasoning,
    get_reasoning_metrics,
)


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics = get_reasoning_metrics()
    metrics.reset()
    yield
    metrics.reset()


def _admit(signals=None):
    return admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=30.0,
        provider_id="nvidia", work_id="task-metrics", signals=signals)


@pytest.mark.asyncio
async def test_idle_and_direct_procedure_never_call_a_model():
    idle = decide_attention(AttentionSignals())
    assert idle.verdict is DispatchVerdict.DO_NOTHING
    with pytest.raises(ReasoningAdmissionError, match="do_nothing"):
        _admit(AttentionSignals())

    procedure = decide_attention(AttentionSignals(
        work_active=True, direct_procedure_available=True))
    assert procedure.verdict is DispatchVerdict.DIRECT_PROCEDURE
    with pytest.raises(ReasoningAdmissionError, match="direct_procedure"):
        _admit(AttentionSignals(
            work_active=True, direct_procedure_available=True))

    report = get_reasoning_metrics().report()
    assert report.idle == 1
    assert report.direct_procedures == 1
    assert report.model_calls == 0


@pytest.mark.asyncio
async def test_waiting_for_actor_defers_without_a_model_call():
    signals = AttentionSignals(work_active=True, waiting_for_actor=True)
    decision = decide_attention(signals)
    assert decision.verdict is DispatchVerdict.DEFER
    with pytest.raises(ReasoningAdmissionError, match="defer"):
        _admit(signals)
    report = get_reasoning_metrics().report()
    assert report.deferred == 1
    assert report.model_calls == 0


@pytest.mark.asyncio
async def test_activity_only_is_rejected_as_success():
    job = _admit(AttentionSignals(has_new_input=True)).job

    async def model():
        return {"choices": [], "usage": {"total_tokens": 11}}

    await dispatch_reasoning(job, model)
    report = get_reasoning_metrics().report()
    assert report.model_calls == 1
    assert report.model_responses == 1
    assert report.total_tokens == 11
    assert report.activity_only is True
    assert report.effective is False

    get_reasoning_metrics().record_verified_work(job, "receipt:task-metrics")
    report = get_reasoning_metrics().report()
    assert report.verified_useful == 1
    assert report.effective is True
    assert report.activity_only is False


@pytest.mark.asyncio
async def test_verified_work_requires_an_evidence_reference():
    job = _admit(AttentionSignals(has_new_input=True)).job
    with pytest.raises(ValueError):
        get_reasoning_metrics().record_verified_work(job, "")
    assert get_reasoning_metrics().report().verified_useful == 0


@pytest.mark.asyncio
async def test_failed_call_is_measured_as_failure_not_success():
    job = _admit(AttentionSignals(has_new_input=True)).job

    async def model():
        raise RuntimeError("provider unavailable")

    with pytest.raises(RuntimeError):
        await dispatch_reasoning(job, model)
    report = get_reasoning_metrics().report()
    assert report.failed_calls == 1
    assert report.model_responses == 0
    assert report.effective is False


@pytest.mark.asyncio
async def test_event_bus_records_dispatch_and_result_without_payloads():
    bus = ReasoningEventBus()
    seen = []
    bus.subscribe(seen.append)
    metrics = ReasoningMetrics(bus)
    job = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=30.0,
        provider_id="openrouter")

    metrics.record_attention(
        job.job.job_id,
        AttentionDecision(DispatchVerdict.DISPATCH, "admitted"),
        job.job.purpose, job.job.provider_id)
    metrics.record_model_started(job.job)
    metrics.record_model_outcome(job.job, success=True, tokens=3)
    events = bus.history()
    assert [e.event for e in events] == [
        ReasoningEvent.ADMITTED,
        ReasoningEvent.MODEL_STARTED,
        ReasoningEvent.MODEL_RESPONSE,
    ]
    assert all(isinstance(e, DispatchEvent) for e in events)
    assert seen == list(events)
    assert all("usage" not in str(e) and "content" not in str(e) for e in events)
