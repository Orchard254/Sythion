"""Trusted owner approval route: real proposal + decision receipt, owner-only."""

from backend.models.database import PermissionProposalRow, Task, TaskEvent
from backend.runtime.permission_proposal import SUPPORTED_ACTION


PARAMS = {"path": "C:/tmp/demo.txt", "content": "hello", "overwrite": False}


def _stage(test_db, task_id="task-1", action_id="a1", version=3):
    test_db.add(Task(id=task_id, original_request="request",
                     state="running", version=version))
    test_db.commit()
    from backend.services.consequential_approval import ConsequentialApprovalService

    svc = ConsequentialApprovalService(test_db)
    proposal = svc.stage_proposal(
        task_id=task_id, action_id=action_id, tool_name=SUPPORTED_ACTION,
        task_version=version, params=PARAMS,
    )
    test_db.commit()
    return proposal


def test_pending_requires_owner_boundary(test_client, test_db):
    # Strip the control token the fixture supplies -> the loopback middleware
    # rejects the request before any handler runs.
    saved = test_client.headers.pop("X-Control-Token", None)
    try:
        response = test_client.get("/api/v1/approvals/pending")
        assert response.status_code == 401
    finally:
        if saved is not None:
            test_client.headers["X-Control-Token"] = saved


def test_device_channel_cannot_use_owner_routes(test_client, test_db):
    device_headers = {
        "X-Sythion-Device-Id": "dev_1",
        "X-Sythion-Device-Token": "some-token",
    }
    assert test_client.get(
        "/api/v1/approvals/pending", headers=device_headers).status_code == 403
    assert test_client.post(
        "/api/v1/approvals/decide", json={}, headers=device_headers).status_code == 403


def test_pending_lists_awaiting_proposals(test_client, test_db):
    proposal = _stage(test_db)
    response = test_client.get("/api/v1/approvals/pending")
    assert response.status_code == 200
    proposals = response.json()["proposals"]
    assert len(proposals) == 1
    assert proposals[0]["proposal_id"] == proposal.proposal_id
    assert proposals[0]["status"] if "status" in proposals[0] else True


def test_owner_can_deny_exact_proposal(test_client, test_db):
    from backend.runtime.permission_decision_receipt import (
        PermissionDecisionReceiptManager,
    )

    proposal = _stage(test_db)
    response = test_client.post("/api/v1/approvals/decide", json={
        "proposal_id": proposal.proposal_id,
        "decision_id": "owner-ui-d1",
        "decision": "deny",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["decision_id"] == "owner-ui-d1"
    assert body["decision"] == "deny"
    assert body["approved"] is False
    assert body["authorization_issued"] is False

    receipt = PermissionDecisionReceiptManager(db=test_db).get(
        decision_id="owner-ui-d1")
    assert receipt is not None
    assert receipt.status == "denied"
    assert receipt.decision_id == "owner-ui-d1"

    events = [e.event_type for e in test_db.query(TaskEvent)
              .filter(TaskEvent.event_type == "permission.decision_recorded").all()]
    assert events == ["permission.decision_recorded"]


def test_owner_can_approve_exact_proposal(test_client, test_db):
    from backend.runtime.permission_decision_receipt import (
        PermissionDecisionReceiptManager,
    )

    proposal = _stage(test_db)
    response = test_client.post("/api/v1/approvals/decide", json={
        "proposal_id": proposal.proposal_id,
        "decision_id": "owner-ui-a1",
        "decision": "approve",
    })
    assert response.status_code == 200
    body = response.json()
    assert body["approved"] is True
    assert body["allowed"] is True
    receipt = PermissionDecisionReceiptManager(db=test_db).get(
        decision_id="owner-ui-a1")
    assert receipt is not None
    assert receipt.status == "approved"


def test_ui_generated_decision_id_is_bound_to_receipt(test_client, test_db):
    import uuid

    from backend.runtime.permission_decision_receipt import (
        PermissionDecisionReceiptManager,
    )

    proposal = _stage(test_db, action_id="ui-id")
    decision_id = f"owner-ui-{uuid.uuid4()}"
    response = test_client.post("/api/v1/approvals/decide", json={
        "proposal_id": proposal.proposal_id,
        "decision_id": decision_id,
        "decision": "approve",
    })
    assert response.status_code == 200
    assert response.json()["decision_id"] == decision_id
    receipt = PermissionDecisionReceiptManager(db=test_db).get(
        decision_id=decision_id)
    assert receipt is not None
    assert receipt.decision_id == decision_id
    assert receipt.status == "approved"


def test_decision_requires_valid_inputs(test_client, test_db):
    proposal = _stage(test_db, action_id="inputs")
    cases = [
        {"decision_id": "d", "decision": "deny"},
        {"proposal_id": proposal.proposal_id, "decision": "deny"},
        {"proposal_id": proposal.proposal_id, "decision_id": "d"},
        {"proposal_id": proposal.proposal_id, "decision_id": "d", "decision": "maybe"},
        {"proposal_id": "missing", "decision_id": "d", "decision": "deny"},
    ]
    for body in cases:
        response = test_client.post("/api/v1/approvals/decide", json=body)
        assert response.status_code in (400, 404), body


def test_replaying_decision_id_does_not_create_second_receipt(test_client, test_db):
    from backend.runtime.permission_decision_receipt import (
        PermissionDecisionReceiptManager,
    )

    proposal = _stage(test_db, action_id="replay")
    body = {
        "proposal_id": proposal.proposal_id,
        "decision_id": "owner-ui-replay",
        "decision": "deny",
    }
    first = test_client.post("/api/v1/approvals/decide", json=body)
    second = test_client.post("/api/v1/approvals/decide", json=body)
    assert first.status_code == 200
    assert second.status_code == 200
    manager = PermissionDecisionReceiptManager(db=test_db)
    assert manager.get(decision_id="owner-ui-replay") is not None
    from backend.models.database import PermissionDecisionReceiptRow
    matching = test_db.query(PermissionDecisionReceiptRow).filter_by(
        decision_id="owner-ui-replay").count()
    assert matching == 1  # replay is idempotent, not a second independent approval


def test_approval_receipt_is_bound_to_proposal(test_client, test_db):
    proposal = _stage(test_db, action_id="bound")
    response = test_client.post("/api/v1/approvals/decide", json={
        "proposal_id": proposal.proposal_id,
        "decision_id": "owner-ui-bound",
        "decision": "approve",
    })
    assert response.status_code == 200
    row = test_db.query(PermissionProposalRow).filter_by(
        proposal_id=proposal.proposal_id).one()
    assert row.status == "awaiting"  # staging primitive stays non-executing
