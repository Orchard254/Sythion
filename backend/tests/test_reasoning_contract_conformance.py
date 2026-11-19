"""Provider-neutral conformance of the shared reasoning contract.

The contract must behave identically for every registered reasoning provider
and for all three entry paths (conversation, task decision, goal planning).
This is offline conformance of the boundary, not live provider inference.
"""

from dataclasses import fields

import pytest

from backend.adapters.openai_compatible_adapter import PROVIDERS
from backend.services.reasoning_dispatch import (
    ReasoningJob,
    ReasoningPurpose,
    ReasoningResult,
    ReasoningResultError,
    admit_reasoning_job,
    as_mapping_result,
    as_text_result,
    handoff_reasoning_result,
)

PROVIDER_IDS = sorted(PROVIDERS)
assert "ollama-cloud" in PROVIDER_IDS  # no provider is excluded from conformance

PATHS = (
    (ReasoningPurpose.CONVERSATION, None, "text"),
    (ReasoningPurpose.TASK_DECISION, "task-conformance", "mapping"),
    (ReasoningPurpose.GOAL_PLANNING, "goal-conformance", "mapping"),
)


def _result(job, kind):
    return (as_text_result(job, "hello") if kind == "text"
            else as_mapping_result(job, {"choices": []}))


@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
@pytest.mark.parametrize("purpose,work_id,kind", PATHS)
def test_all_providers_and_paths_share_one_result_contract(
    provider_id, purpose, work_id, kind,
):
    admission = admit_reasoning_job(
        purpose, timeout_seconds=60.0, provider_id=provider_id, work_id=work_id)
    job = admission.job
    result = _result(job, kind)

    assert admission.admitted is True
    assert isinstance(result, ReasoningResult)
    assert result.job_id == job.job_id
    assert result.purpose is purpose
    assert result.provider_id == provider_id
    assert result.work_id == work_id
    assert handoff_reasoning_result(result, job) is result

    authority_fields = {
        "approve", "approval", "consent", "permission", "grant", "tool",
        "success", "state", "execute",
    }
    names = {f.name for f in fields(ReasoningResult)}
    assert not any(any(word in name for word in authority_fields) for name in names)


@pytest.mark.parametrize("provider_id", PROVIDER_IDS)
def test_results_cannot_cross_provider_boundaries(provider_id):
    job = admit_reasoning_job(
        ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
        provider_id=provider_id, work_id="same-work").job
    own = _result(job, "mapping")
    other_provider = next(p for p in PROVIDER_IDS if p != provider_id)
    other_job = ReasoningJob(
        ReasoningPurpose.TASK_DECISION, 60.0, "same-work", job.job_id,
        other_provider)
    with pytest.raises(ReasoningResultError):
        handoff_reasoning_result(own, other_job)


def test_pending_mutating_action_blocks_admission_for_every_provider():
    for provider_id in PROVIDER_IDS:
        with pytest.raises(Exception, match="pending mutating action"):
            admit_reasoning_job(
                ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
                provider_id=provider_id, work_id="blocked-work",
                pending_mutating_action=True)
