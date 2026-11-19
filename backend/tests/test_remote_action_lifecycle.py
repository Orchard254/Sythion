"""Remote-action lifecycle: bounded transitions, approval binding, uncertain replay."""

from datetime import datetime, timedelta

import pytest

from backend.models.database import RemoteAction, RemoteActionEvent
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.runtime.remote_action_lifecycle import (
    AWAITING_APPROVAL,
    CANCELLED,
    COMPLETED,
    EXECUTING,
    FAILED,
    PLANNED,
    RECEIVED,
    UNCERTAIN,
    UNDERSTANDING,
    VERIFYING,
    RemoteActionError,
    RemoteActionManager,
)

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _manager(test_db):
    return RemoteActionManager(test_db, clock=lambda: NOW)


def _create(test_db, key="key-1", task_id=None):
    return _manager(test_db).create(
        idempotency_key=key, device_id="dev_1", task_id=task_id)


def test_create_is_received_and_deduplicated(test_db):
    manager = _manager(test_db)
    first = _create(test_db)
    assert first["state"] == RECEIVED
    assert first["deduplicated"] is False
    assert first["terminal"] is False

    second = _create(test_db)
    assert second["action_id"] == first["action_id"]
    assert second["deduplicated"] is True
    assert test_db.query(RemoteAction).count() == 1

    events = manager.events(first["action_id"])
    assert [e["to_state"] for e in events] == [RECEIVED]
    assert [e["sequence"] for e in events] == [1]


def test_create_requires_key_and_device(test_db):
    manager = _manager(test_db)
    with pytest.raises(RemoteActionError) as exc:
        manager.create(idempotency_key="", device_id="dev_1")
    assert exc.value.code == "IDEMPOTENCY_KEY_REQUIRED"
    with pytest.raises(RemoteActionError) as exc:
        manager.create(idempotency_key="k", device_id="")
    assert exc.value.code == "DEVICE_REQUIRED"


def test_full_happy_path_with_approval_and_evidence(test_db):
    manager = _manager(test_db)
    action = _create(test_db)
    aid = action["action_id"]

    assert manager.transition(aid, UNDERSTANDING)["state"] == UNDERSTANDING
    assert manager.transition(aid, PLANNED)["state"] == PLANNED
    assert manager.transition(aid, AWAITING_APPROVAL)["state"] == AWAITING_APPROVAL
    executing = manager.transition(aid, EXECUTING, decision_id="owner-ui-decision-1")
    assert executing["state"] == EXECUTING
    assert executing["approval_decision_id"] == "owner-ui-decision-1"
    assert manager.transition(aid, VERIFYING)["state"] == VERIFYING
    completed = manager.transition(
        aid, COMPLETED, evidence_ref="evidence:ledger:42")
    assert completed["state"] == COMPLETED
    assert completed["evidence_ref"] == "evidence:ledger:42"
    assert completed["terminal"] is True
    assert completed["resolved_at"] is not None

    events = manager.events(aid)
    assert [e["to_state"] for e in events] == [
        RECEIVED, UNDERSTANDING, PLANNED, AWAITING_APPROVAL,
        EXECUTING, VERIFYING, COMPLETED,
    ]
    assert [e["sequence"] for e in events] == list(range(1, 8))
    executing_event = next(e for e in events if e["to_state"] == EXECUTING)
    assert executing_event["decision_id"] == "owner-ui-decision-1"


def test_invalid_and_terminal_transitions_refused(test_db):
    manager = _manager(test_db)
    aid = _create(test_db)["action_id"]
    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, EXECUTING)
    assert exc.value.code == "INVALID_TRANSITION"  # received cannot jump to executing

    manager.transition(aid, FAILED, reason="provider refused")
    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, UNDERSTANDING)
    assert exc.value.code == "INVALID_TRANSITION"  # FAILED is terminal


def test_approval_requires_trusted_decision_id(test_db):
    manager = _manager(test_db)

    # Without a decision id, leaving awaiting_approval is refused.
    aid = _create(test_db)["action_id"]
    manager.transition(aid, UNDERSTANDING)
    manager.transition(aid, PLANNED)
    manager.transition(aid, AWAITING_APPROVAL)
    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, EXECUTING)
    assert exc.value.code == "TRUSTED_DECISION_REQUIRED"
    assert manager.get(aid)["state"] == AWAITING_APPROVAL

    # With a decision id (supplied by the trusted UI, recorded for audit) it
    # proceeds. The lifecycle cannot verify the source; the caller owns that.
    other = _create(test_db, key="key-other")["action_id"]
    manager.transition(other, UNDERSTANDING)
    manager.transition(other, PLANNED)
    manager.transition(other, AWAITING_APPROVAL)
    started = manager.transition(other, EXECUTING, decision_id="owner-ui-decision-2")
    assert started["state"] == EXECUTING
    assert started["approval_decision_id"] == "owner-ui-decision-2"


def test_verified_completion_requires_evidence(test_db):
    manager = _manager(test_db)
    aid = _create(test_db)["action_id"]
    for state in (UNDERSTANDING, PLANNED, EXECUTING, VERIFYING):
        manager.transition(aid, state)
    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, COMPLETED)
    assert exc.value.code == "EVIDENCE_REQUIRED"
    assert manager.get(aid)["state"] == VERIFYING


def test_uncertain_is_terminal_and_forbids_replay(test_db):
    manager = _manager(test_db)
    action = _create(test_db)
    aid = action["action_id"]
    manager.transition(aid, UNDERSTANDING)
    manager.transition(aid, UNCERTAIN, reason="connection lost after send")

    uncertain = manager.get(aid)
    assert uncertain["uncertain"] is True
    assert uncertain["terminal"] is True

    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, EXECUTING)
    assert exc.value.code == "UNCERTAIN_REPLAY_REFUSED"

    # Reusing the same idempotency key returns the same uncertain record —
    # never a fresh action to replay the effect.
    retried = manager.create(
        idempotency_key=action["idempotency_key"], device_id="dev_1")
    assert retried["action_id"] == aid
    assert retried["state"] == UNCERTAIN
    assert retried["deduplicated"] is True
    assert len(manager.list_uncertain()) == 1

    # A genuinely new retry uses a NEW key and starts from received.
    fresh = manager.create(idempotency_key="key-2", device_id="dev_1")
    assert fresh["state"] == RECEIVED
    assert fresh["action_id"] != aid


def test_version_guard_refuses_stale_transition(test_db):
    manager = _manager(test_db)
    aid = _create(test_db)["action_id"]
    with pytest.raises(RemoteActionError) as exc:
        manager.transition(aid, UNDERSTANDING, expected_version=99)
    assert exc.value.code == "VERSION_CONFLICT"


def test_uncertain_ownership_refuses_execution(test_db):
    manager = _manager(test_db)
    aid = _create(test_db, task_id="task-1")["action_id"]
    test_db.add(TaskOwnershipRow(
        task_id="task-1", owner_nonce="n1", worker_id="w1",
        status="ACTIVE", acquired_at=NOW, heartbeat_at=NOW,
        expires_at=NOW + timedelta(seconds=30), fencing_token=1, version=1,
        uncertain_action_ids='["old-action"]',
    ))
    test_db.commit()

    for state in (UNDERSTANDING, PLANNED, EXECUTING):
        try:
            manager.transition(aid, state)
        except RemoteActionError as exc:
            assert state == EXECUTING
            assert exc.code == "UNCERTAIN_OWNERSHIP_REFUSED"
    assert manager.get(aid)["state"] == PLANNED


def test_cancel_path(test_db):
    manager = _manager(test_db)
    aid = _create(test_db)["action_id"]
    assert manager.transition(aid, CANCELLED, reason="owner cancelled")["state"] == CANCELLED
    assert test_db.query(RemoteActionEvent).filter_by(action_id=aid).count() == 2
