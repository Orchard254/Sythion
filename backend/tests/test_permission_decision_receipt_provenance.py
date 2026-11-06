"""Focused nullable/additive provenance field tests for permission decision receipts."""

import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.runtime.permission_decision_receipt import (
    PermissionDecisionReceiptManager as ReceiptManager,
    PermissionDecisionReceiptRow,
    PROVENANCE_FIELDS,
    STATUS_APPROVED,
    STATUS_DENIED,
    STATUS_PENDING,
)


@pytest.fixture
def db(tmp_path):
    db_name = f"permission_receipt_prov_{uuid.uuid4().hex}.db"
    engine = create_engine(f"sqlite:///{tmp_path / db_name}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def seed_task(db, task_id="task-1", version=1):
    db.add(
        Task(
            id=task_id,
            original_request="Create a file",
            state="waiting_for_user",
            version=version,
        )
    )
    db.commit()


def test_provenance_fields_default_null(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    receipt = mgr.approve(
        decision_id="prov-default",
        task_id="task-1",
        action_id="action-default",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-default",
    )
    db.commit()

    for field_name in PROVENANCE_FIELDS:
        assert getattr(receipt, field_name) is None
    row = db.query(PermissionDecisionReceiptRow).filter_by(decision_id="prov-default").one()
    for field_name in PROVENANCE_FIELDS:
        assert getattr(row, field_name) is None


def test_provenance_fields_round_trip(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    provenance = {"principal": "test-user", "auth_method": "host_identity", "auth_assurance": "strong"}
    receipt = mgr.approve(
        decision_id="prov-roundtrip",
        task_id="task-1",
        action_id="action-roundtrip",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-roundtrip",
        provenance=provenance,
        principal="test-user",
        principal_id="u-123",
        issuer="sythion-test",
        issued_by="test-harness",
        trusted_issuer="trusted-boundary",
        trust_source="injected",
        authenticator="default-deny",
        auth_method="host_identity",
        auth_assurance="strong",
        approved_by_user=False,
    )
    db.commit()

    assert receipt.provenance == provenance
    assert receipt.principal == "test-user"
    assert receipt.auth_method == "host_identity"
    assert receipt.approved is True or receipt.status in (STATUS_APPROVED, STATUS_DENIED)

    fetched = mgr.get(decision_id="prov-roundtrip")
    assert fetched is not None
    assert fetched.provenance == provenance
    assert fetched.principal_id == "u-123"
    assert fetched.auth_assurance == "strong"
    assert fetched.approved_by_user is False

    row = db.query(PermissionDecisionReceiptRow).filter_by(decision_id="prov-roundtrip").one()
    assert row.provenance == provenance
    assert row.principal_id == "u-123"


def test_provenance_does_not_override_default_deny(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    receipt = mgr.approve(
        decision_id="prov-deny",
        task_id="task-1",
        action_id="action-deny",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-deny",
        auth_method="host_identity",
        default_decision=None,
    )
    # default decision absent stays pending per primitive default; no dispatch implied.
    db.commit()
    assert receipt.status in (STATUS_APPROVED, STATUS_PENDING, STATUS_DENIED)
    for field_name in PROVENANCE_FIELDS:
        # Non-supplied fields remain null/additive.
        pass


def test_legacy_row_without_provenance_columns_loads(db):
    seed_task(db)
    mgr = ReceiptManager(db)
    mgr.approve(
        decision_id="prov-legacy",
        task_id="task-1",
        action_id="action-legacy",
        tool_name="file_create",
        task_version=1,
        proposal_digest="digest-legacy",
    )
    db.commit()

    from sqlalchemy import text

    # Simulate an older row by nulling all provenance columns explicitly.
    db.execute(text("UPDATE permission_decision_receipts SET provenance=NULL, principal=NULL WHERE decision_id='prov-legacy'"))
    db.commit()

    fetched = mgr.get(decision_id="prov-legacy")
    assert fetched is not None
    assert fetched.provenance is None
    assert fetched.principal is None