"""Offline contract tests using a simulated host, never actual Windows Hello."""
from dataclasses import FrozenInstanceError
import hashlib
import hmac

import pytest

from backend.services.recovery_native_challenge import (
    NativePresenceChallengeBroker, NativePresenceObservation,
)

KEY = b"K" * 32
REV = "sha256:" + "a" * 64
OTHER_REV = "sha256:" + "b" * 64


class FakeClock:
    now = 100.0
    def __call__(self): return self.now


def broker():
    clock = FakeClock()
    return NativePresenceChallengeBroker(KEY, monotonic=clock), clock


def signed(challenge, task=None, revision=None, key=KEY):
    task = task if task is not None else challenge.task_id
    revision = revision if revision is not None else challenge.evidence_revision
    msg = NativePresenceChallengeBroker._canonical_message(challenge.challenge_id, task, revision)
    return hmac.new(key, msg, hashlib.sha256).hexdigest()


def test_default_unwired_broker_denies_without_key():
    b = NativePresenceChallengeBroker()
    assert not b.configured
    with pytest.raises(PermissionError): b.issue("t", REV)
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id="0"*64, task_id="t", evidence_revision=REV, signature_hex="0"*64, current_evidence_revision=REV)


def test_signed_one_time_host_assertion_is_not_authorization():
    b, _ = broker()
    c = b.issue("task-a", REV)
    p = b.verify_host_assertion(challenge_id=c.challenge_id, task_id="task-a", evidence_revision=REV,
                                signature_hex=signed(c), current_evidence_revision=REV)
    assert p.host_message_authenticated is True
    assert not p.authenticated_human
    assert not p.independent_effect_verified
    assert not p.recovery_cleared
    assert not p.authorization_issued
    assert not p.automatic_replay_allowed
    assert p.purpose == "recovery_review_only"
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id, task_id="task-a", evidence_revision=REV,
                                signature_hex=signed(c), current_evidence_revision=REV)


def test_different_task_or_revision_cannot_spend_challenge():
    b, _ = broker(); c=b.issue("task-a", REV)
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-b",evidence_revision=REV,
                                signature_hex=signed(c, task="task-b"), current_evidence_revision=REV)
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",evidence_revision=OTHER_REV,
                                signature_hex=signed(c,revision=OTHER_REV), current_evidence_revision=REV)
    # Neither wrong-scope claim can transform a valid challenge.
    assert b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                   evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV).host_message_authenticated


def test_stale_challenge_refused_after_deadline():
    b, clock=broker(); c=b.issue("task-a", REV, ttl_seconds=2)
    clock.now+=2
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV)


def test_new_process_cannot_replay_old_challenge():
    b,_=broker(); c=b.issue("task-a", REV)
    new,_=broker()
    with pytest.raises(PermissionError):
        new.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                  evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV)


def test_false_key_or_bogus_signature_denied():
    b,_=broker(); c=b.issue("task-a", REV)
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                evidence_revision=REV,signature_hex=signed(c,key=b"Z"*32), current_evidence_revision=REV)
    assert b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                   evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV).host_message_authenticated


def test_three_failures_exhaust_challenge():
    b,_=broker(); c=b.issue("task-a", REV)
    for _ in range(3):
        with pytest.raises(PermissionError):
            b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                    evidence_revision=REV,signature_hex="0"*64, current_evidence_revision=REV)
    with pytest.raises(PermissionError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task-a",
                                evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV)


@pytest.mark.parametrize("bad",["", "  task", "t ", "x"*129, 5, None, True])
def test_invalid_task_scope_rejected(bad):
    b,_=broker()
    with pytest.raises(ValueError): b.issue(bad,REV)


@pytest.mark.parametrize("bad",["", "sha256:0", "SHA256:"+"a"*64, "sha256:"+"F"*64, 5, None, True])
def test_invalid_revision_rejected(bad):
    b,_=broker()
    with pytest.raises(ValueError): b.issue("task",bad)


@pytest.mark.parametrize("bad",[0, -1, 121, 1.5, True, None])
def test_invalid_ttl_rejected(bad):
    b,_=broker()
    with pytest.raises(ValueError): b.issue("task",REV,ttl_seconds=bad)


@pytest.mark.parametrize("bad",[b"", b"short", "not bytes", 123,bytearray(b"K"*32)])
def test_untrusted_key_shape_rejected(bad):
    with pytest.raises(ValueError): NativePresenceChallengeBroker(bad)


def test_challenge_capacity_is_bounded_and_expired_slot_reused():
    b,clock=broker()
    for _ in range(64): b.issue("task",REV,ttl_seconds=1)
    with pytest.raises(RuntimeError): b.issue("task",REV)
    clock.now+=1
    assert b.issue("task",REV).task_id=="task"


def test_untrusted_signature_shape_never_bypasses_check():
    b,_=broker(); c=b.issue("task",REV)
    for bad in (True,None,"", "a"*63,"A"*64, 123):
        with pytest.raises(PermissionError):
            b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task",
                                    evidence_revision=REV,signature_hex=bad, current_evidence_revision=REV)
    assert b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task",
                                   evidence_revision=REV,signature_hex=signed(c), current_evidence_revision=REV).host_message_authenticated


def test_snapshot_payload_contains_no_authorization_or_secret():
    b,_=broker(); c=b.issue("task",REV)
    assert c.purpose == "recovery_review_only"
    assert "K"*32 not in repr(c)
    assert not hasattr(c, "signature")
    assert not hasattr(c, "authorization")
    with pytest.raises(FrozenInstanceError): c.task_id="different"


def test_broker_does_not_accept_caller_approved_boolean():
    b,_=broker(); c=b.issue("task",REV)
    with pytest.raises(TypeError):
        b.verify_host_assertion(challenge_id=c.challenge_id,task_id="task",evidence_revision=REV,
                                signature_hex=signed(c), current_evidence_revision=REV, approved=True)


def test_confirmation_is_just_host_message_integrity_not_human_identity():
    observation=NativePresenceObservation()
    assert observation.host_message_authenticated is False
    assert observation.authenticated_human is False
    assert observation.recovery_cleared is False


def test_revision_changed_after_issuance_consumes_old_challenge():
    b, _ = broker(); c = b.issue("task-a", REV)
    with pytest.raises(PermissionError, match="revision changed"):
        b.verify_host_assertion(challenge_id=c.challenge_id, task_id="task-a",
                                evidence_revision=REV, current_evidence_revision=OTHER_REV,
                                signature_hex=signed(c))
    with pytest.raises(PermissionError, match="unavailable or consumed"):
        b.verify_host_assertion(challenge_id=c.challenge_id, task_id="task-a",
                                evidence_revision=REV, current_evidence_revision=REV,
                                signature_hex=signed(c))


def test_signature_for_different_challenge_is_not_reusable():
    b, _ = broker(); a = b.issue("task-a", REV); c = b.issue("task-a", REV)
    with pytest.raises(PermissionError, match="invalid"):
        b.verify_host_assertion(challenge_id=c.challenge_id, task_id="task-a",
                                evidence_revision=REV, current_evidence_revision=REV,
                                signature_hex=signed(a))
    assert b.verify_host_assertion(challenge_id=a.challenge_id, task_id="task-a",
                                   evidence_revision=REV, current_evidence_revision=REV,
                                   signature_hex=signed(a)).host_message_authenticated
