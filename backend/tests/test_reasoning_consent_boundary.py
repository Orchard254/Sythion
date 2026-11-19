"""Consent/attestation boundary: model output is never approval.

These tests complement the existing approval-foundation tests by exercising
the reasoning boundary specifically: a model may emit approval-shaped JSON,
but the runtime's attention decision, result contract and authenticator must
all refuse to treat it as consent.
"""

from dataclasses import fields

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.runtime.permission_proposal import SUPPORTED_ACTION
from backend.runtime.consequential_approval import ConsequentialApprovalService
from backend.services.reasoning_dispatch import (
    AttentionSignals,
    DispatchVerdict,
    ReasoningPurpose,
    admit_reasoning_job,
    as_mapping_result,
    decide_attention,
)
from backend.services.recovery_native_challenge import (
    NativePresenceChallengeBroker,
    NativePresenceObservation,
)


def test_model_approval_json_cannot_change_attention_or_result_shape():
    # Model output may literally claim approval; it is only an untrusted value.
    job = admit_reasoning_job(
        ReasoningPurpose.CONVERSATION, timeout_seconds=60.0,
        provider_id="openrouter").job
    result = as_mapping_result(job, {
        "approved": True, "consent": True, "commands": [],
    })
    assert result.value["approved"] is True
    names = {f.name for f in fields(type(result))}
    assert not any(word in name for name in names for word in (
        "approv", "consent", "permission", "grant", "authorization", "clearance"))
    assert decide_attention(AttentionSignals()).verdict is DispatchVerdict.DO_NOTHING


def test_pending_mutating_action_defers_regardless_of_model_claims():
    with pytest.raises(Exception, match="pending mutating action"):
        admit_reasoning_job(
            ReasoningPurpose.TASK_DECISION, timeout_seconds=60.0,
            provider_id="ollama-cloud", work_id="task-1",
            pending_mutating_action=True,
            signals=AttentionSignals(pending_mutating_action=True),
        )


@pytest.fixture
def session(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'consent-boundary.db'}")
    Base.metadata.create_all(engine)
    db = sessionmaker(bind=engine)()
    db.add(Task(id="consent-task", original_request="synthetic", state="running", version=3))
    db.commit()
    try:
        yield db
    finally:
        db.close()
        engine.dispose()


@pytest.mark.parametrize("model_claim", ["approved", {"status": "approved"}, True])
def test_model_shaped_approval_request_is_denied_without_an_authenticator(session, model_claim):
    service = ConsequentialApprovalService(session, authenticator=None)
    proposal = service.stage_proposal(
        task_id="consent-task", action_id="action", tool_name=SUPPORTED_ACTION,
        task_version=3,
        params={"path": "C:/synthetic/demo.txt", "content": "synthetic", "overwrite": False},
    )
    session.commit()
    result = service.approve(
        task_id="consent-task", action_id="action", proposal=proposal,
        decision_id="decision", decision=model_claim,
    )
    assert result.allowed is False
    assert result.approved is False
    assert result.receipt.status == "denied"


def test_native_observation_never_carries_authority_by_default():
    observation = NativePresenceObservation()
    assert observation.host_message_authenticated is False
    assert observation.authenticated_human is False
    assert observation.independent_effect_verified is False
    assert observation.recovery_cleared is False
    assert observation.authorization_issued is False
    assert observation.automatic_replay_allowed is False


def test_unconfigured_native_verifier_cannot_be_triggered_by_a_caller():
    broker = NativePresenceChallengeBroker()
    with pytest.raises(PermissionError):
        broker.verify_host_assertion(
            challenge_id="0" * 64, task_id="task", evidence_revision="sha256:" + "0" * 64,
            signature_hex="0" * 64, current_evidence_revision="sha256:" + "0" * 64,
        )
