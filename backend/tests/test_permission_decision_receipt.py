"""Transactional permission decision receipt foundation tests."""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.runtime.permission_decision_receipt import (
    PermissionDecisionReceiptManager as ReceiptManager,
    PermissionDecisionReceiptRow,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'permission_receipt.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def seed_task(db, task_id="task-1", version=1, pending=None):
    db.add(
        Task(
            id=task_id,
            original_request="Create a file",
            state="waiting_for_user",
            version=version,
            pending_action=json.dumps(pending) if pending is not None else None,
        )
    )
    db.commit()


def test_decision_identity_required(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    with pytest.raises(ValueError, match="Exact permission decision identity is required"):
        mgr.approve(task_id="task-1", action_id="a1", tool_name="file_create", task_version=1, proposal_digest="digest-1")
    with pytest.raises(ValueError, match="Exact permission decision identity is required"):
        mgr.approve(decision_id="", task_id="task-1", action_id="a1", tool_name="file_create", task_version=1, proposal_digest="digest-1")
    with pytest.raises(ValueError, match="Exact permission decision identity is required"):
        mgr.approve(decision_id=123, task_id="task-1", action_id="a1", tool_name="file_create", task_version=1, proposal_digest="digest-1")


def test_duplicate_identical_request_is_idempotent(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    first = mgr.approve(
        decision_id="decision-1",
        task_id="task-1",
        action_id="action-1",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-1",
    )
    db.commit()
    assert first.status == STATUS_APPROVED

    second = mgr.approve(
        decision_id="decision-1",
        task_id="task-1",
        action_id="action-1",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-1",
    )
    db.commit()

    assert second.decision_id == first.decision_id
    assert second.status == STATUS_APPROVED
    assert db.query(PermissionDecisionReceiptRow).count() == 1


def test_conflicting_decisions_fail(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    approved = mgr.approve(
        decision_id="decision-2",
        task_id="task-1",
        action_id="action-2",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-2",
    )
    db.commit()
    assert approved.status == STATUS_APPROVED

    with pytest.raises(ValueError, match="Conflicting permission decision"):
        mgr.deny(
            decision_id="decision-2",
            task_id="task-1",
            action_id="action-2",
            tool_name="file_create",
            task_version=1,
            proposal_digest="digest-2",
        )
    db.rollback()

    row = db.query(PermissionDecisionReceiptRow).filter_by(decision_id="decision-2").one()
    assert row.status == STATUS_APPROVED
    assert db.query(PermissionDecisionReceiptRow).count() == 1


def test_task_version_binding_mismatch_fails(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    with pytest.raises(ValueError, match="Task version binding mismatch"):
        mgr.approve(
            decision_id="decision-3",
            task_id="task-1",
            action_id="action-3",
            tool_name="file_create",
            task_version=2,
            proposal_digest="digest-3",
        )
    db.rollback()
    assert db.query(PermissionDecisionReceiptRow).count() == 0


def test_missing_digest_fails(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    with pytest.raises(ValueError, match="Exact proposal digest is required"):
        mgr.approve(
            decision_id="decision-4",
            task_id="task-1",
            action_id="action-4",
            tool_name="file_create",
            task_version=1,
            proposal_digest="",
        )
    db.rollback()
    assert db.query(PermissionDecisionReceiptRow).count() == 0


def test_raw_params_and_content_are_not_stored_or_returned(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    receipt = mgr.approve(
        decision_id="decision-5",
        task_id="task-1",
        action_id="action-5",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-5",
        params={"path": "/tmp/secret", "content": "SECRET"},
        content="SECRET",
    )
    db.commit()

    assert "params" not in receipt.to_dict()
    assert "content" not in receipt.to_dict()
    row = db.query(PermissionDecisionReceiptRow).filter_by(decision_id="decision-5").one()
    for unsafe in ("params", "content", "arguments"):
        assert not hasattr(row, unsafe)
    assert "SECRET" not in str(receipt.to_dict())


def test_pending_status_binding_mismatch_fails(db):
    seed_task(
        db,
        pending={"kind": "permission", "status": "awaiting", "action_id": "action-6", "tool_name": "file_create"},
    )
    mgr = ReceiptManager(db)
    with pytest.raises(ValueError, match="Pending decision binding mismatch|Pending action binding mismatch"):
        mgr.approve(
            decision_id="decision-6",
            task_id="task-1",
            action_id="action-6",
            tool_name="file_create",
            task_version=1,
            proposal_digest="digest-6",
            expected_pending_status="approved",
        )
    db.rollback()
    assert db.query(PermissionDecisionReceiptRow).count() == 0


def test_deny_creates_terminal_receipt(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    receipt = mgr.deny(
        decision_id="decision-7",
        task_id="task-1",
        action_id="action-7",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-7",
    )
    db.commit()
    assert receipt.status == STATUS_DENIED
    assert receipt.denied is True
    assert receipt.approved is False


def test_resolve_pending_permission_alias_is_cas(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    first = mgr.resolve_pending_permission(
        decision_id="decision-8",
        task_id="task-1",
        action_id="action-8",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-8",
        decision="approve",
    )
    db.commit()
    assert first.status == STATUS_APPROVED

    # Same exact request is idempotent; different decision conflicts.
    same = mgr.resolve_pending_permission(
        decision_id="decision-8",
        task_id="task-1",
        action_id="action-8",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-8",
        decision="approve",
    )
    assert same.decision_id == first.decision_id

    with pytest.raises(ValueError):
        mgr.resolve_pending_permission(
            decision_id="decision-8",
            task_id="task-1",
            action_id="action-8",
            tool_name="file_create",
            task_version=1,
            proposal_digest="digest-8",
            decision="deny",
        )
    db.rollback()
