"""Trusted-host attestation boundary: auditable, non-authorizing backend half.

These tests prove key provenance is injection-only and that a verified host
message produces a receipt with every authority flag fixed false. They do NOT
claim Windows Hello or authenticated human presence.
"""

import hashlib
import hmac

import pytest

from backend.services.recovery_native_challenge import (
    NativeAttestationReceipt,
    NativePresenceChallengeBroker,
    TrustedHostAttestationBoundary,
)

REVISION = "sha256:" + "a" * 64
KEY = b"k" * 48


def sign(challenge, key=KEY):
    message = NativePresenceChallengeBroker._canonical_message(
        challenge.challenge_id, challenge.task_id, challenge.evidence_revision)
    return hmac.new(key, message, hashlib.sha256).hexdigest()


@pytest.fixture
def boundary():
    calls = []

    def provider():
        calls.append(True)
        return KEY

    service = TrustedHostAttestationBoundary(provider)
    return service, calls


def test_key_is_injected_once_and_cannot_be_set_by_a_caller(boundary):
    service, calls = boundary
    assert service.configured is True
    assert len(calls) == 1
    assert not hasattr(service, "set_key")


@pytest.mark.parametrize("bad_key", [None, b"", b"short", "text", 123])
def test_invalid_key_provider_output_fails_closed(bad_key):
    with pytest.raises(ValueError):
        TrustedHostAttestationBoundary(lambda: bad_key)


def test_verified_host_message_yields_a_non_authorizing_receipt(boundary):
    service, _ = boundary
    challenge = service.issue("task-1", REVISION)
    receipt = service.verify(
        challenge_id=challenge.challenge_id,
        task_id="task-1",
        evidence_revision=REVISION,
        signature_hex=sign(challenge),
        current_evidence_revision=REVISION,
    )
    assert isinstance(receipt, NativeAttestationReceipt)
    assert receipt.task_id == "task-1"
    assert receipt.host_message_authenticated is True
    assert receipt.authenticated_human is False
    assert receipt.independent_effect_verified is False
    assert receipt.recovery_cleared is False
    assert receipt.authorization_issued is False
    assert receipt.automatic_replay_allowed is False
    assert len(service.receipts()) == 1


def test_replay_and_stale_revision_write_no_second_receipt(boundary):
    service, _ = boundary
    challenge = service.issue("task-2", REVISION)
    args = dict(
        challenge_id=challenge.challenge_id, task_id="task-2",
        evidence_revision=REVISION, signature_hex=sign(challenge),
        current_evidence_revision=REVISION,
    )
    service.verify(**args)
    with pytest.raises(PermissionError):
        service.verify(**args)  # one-shot challenge consumed
    with pytest.raises(PermissionError):
        service.verify(**{**args, "current_evidence_revision": "sha256:" + "b" * 64})
    assert len(service.receipts()) == 1


def test_wrong_task_scope_is_rejected_without_a_receipt(boundary):
    service, _ = boundary
    challenge = service.issue("task-3", REVISION)
    with pytest.raises(PermissionError):
        service.verify(
            challenge_id=challenge.challenge_id, task_id="other-task",
            evidence_revision=REVISION, signature_hex=sign(challenge),
            current_evidence_revision=REVISION,
        )
    assert service.receipts() == ()


@pytest.mark.parametrize("field_name", [
    "authenticated_human", "independent_effect_verified", "recovery_cleared",
    "authorization_issued", "automatic_replay_allowed",
])
def test_receipt_cannot_assert_authority(field_name):
    values = dict(
        receipt_id="r", challenge_id="c", task_id="t",
        evidence_revision=REVISION, observed_at=0.0,
    )
    values[field_name] = True
    with pytest.raises(ValueError):
        NativeAttestationReceipt(**values)
