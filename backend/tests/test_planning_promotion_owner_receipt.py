"""Governance: ACTIVE promotion requires a durable, verified owner-approval receipt.

A body-only ``owner_approved: true`` must never authorize promotion. The two
legitimate paths are (1) a verified durable receipt issued by a trusted owner
boundary, or (2) the explicit safe_for_auto_promotion + config opt-in (default
OFF).
"""

import json

from backend.core.config import settings
from backend.learning.planning_store import (
    PlanningOutcomeStore,
    PlanningPolicyApprovalReceipts,
    PlanningPolicyStore,
)
from backend.learning.planning_taxonomy import PlanningStrategy
from backend.runtime.permission_decision_receipt import (
    STATUS_APPROVED as RECEIPT_STATUS_APPROVED,
)

PROMOTE_PATH = "/api/v1/learning/planning/policies/{policy_id}/promote"
APPROVE_PATH = "/api/v1/learning/planning/policies/{policy_id}/approve"


def _seed_policy(test_db, tag, *, safe_auto=False, shadow_validated=True):
    """Create a policy staged at SHADOW_VALIDATED with qualifying real evidence."""
    ostore = PlanningOutcomeStore(test_db)
    for i in range(5):
        ostore.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="goal",
            goal_id=f"{tag}_real_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:{tag}_{i}"],
        )
    store = PlanningPolicyStore(test_db)
    evidence = {"sample_count": 10, "shadow_validated": shadow_validated}
    if safe_auto:
        evidence["safe_for_auto_promotion"] = True
    policy = store.create_observed(
        "goal", PlanningStrategy.DIRECT_PLAN.value, evidence_summary=evidence
    )
    assert store.promote(policy.id, target_status="SUPPORTED")["ok"] is True
    if shadow_validated:
        assert store.promote(policy.id, target_status="SHADOW_VALIDATED")["ok"] is True
    test_db.refresh(policy)
    return store, policy


def test_body_only_owner_approved_is_rejected_and_does_not_promote(test_client, test_db):
    _, policy = _seed_policy(test_db, "body_only")
    version_before = policy.policy_version

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE", "owner_approved": True},
    )

    assert response.status_code == 409
    detail = response.json()["detail"].lower()
    assert "receipt" in detail and "owner_approved" in detail
    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"
    assert policy.policy_version == version_before
    from backend.models.database import TaskEvent
    promoted = test_db.query(TaskEvent).filter(
        TaskEvent.event_type == "planning.policy.promoted"
    ).all()
    assert all(json.loads(e.event_data).get("to") != "ACTIVE" for e in promoted)


def test_verified_durable_receipt_promotes_to_active(test_client, test_db):
    _, policy = _seed_policy(test_db, "receipt_ok")
    receipts = PlanningPolicyApprovalReceipts(test_db)
    receipt_id = receipts.issue(policy, "ACTIVE", decision_id="owner-rcpt-endpoint-ok")
    test_db.commit()
    assert receipts.verify(policy, "ACTIVE", receipt_id) is True
    version_before = policy.policy_version

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE", "approval_receipt_id": receipt_id},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"
    assert response.json()["policy_version"] == version_before + 1
    test_db.refresh(policy)
    assert policy.status == "ACTIVE"

    # Audit event is preserved and records the verified basis, not a claim.
    from backend.models.database import TaskEvent
    event = test_db.query(TaskEvent).filter(
        TaskEvent.event_type == "planning.policy.promoted"
    ).order_by(TaskEvent.id.desc()).first()
    assert event is not None
    audit = json.loads(event.event_data)
    assert audit["to"] == "ACTIVE"
    assert audit["approval_basis"] == "owner_approval_receipt"
    assert audit["approval_receipt_id"] == receipt_id


def test_safe_auto_opt_in_promotes_without_receipt(test_client, test_db, monkeypatch):
    _, policy = _seed_policy(test_db, "safe_auto", safe_auto=True)
    monkeypatch.setattr(settings, "planning_safe_auto_promotion", True)

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE"},
    )

    assert response.status_code == 200
    assert response.json()["status"] == "ACTIVE"


def test_safe_auto_evidence_alone_is_not_enough_when_config_off(test_client, test_db, monkeypatch):
    _, policy = _seed_policy(test_db, "safe_auto_off", safe_auto=True)
    monkeypatch.setattr(settings, "planning_safe_auto_promotion", False)

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE", "owner_approved": True},
    )

    assert response.status_code == 409
    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"


def test_non_active_promotions_and_unrelated_fields_still_work(test_client, test_db):
    ostore = PlanningOutcomeStore(test_db)
    for i in range(5):
        ostore.record(
            planning_strategy=PlanningStrategy.DIRECT_PLAN.value,
            task_class="goal",
            goal_id=f"fields_real_{i}",
            outcome="SUCCESS_VERIFIED",
            verified_success=True,
            provenance="REAL_EXECUTION",
            evidence_refs=[f"goal:fields_{i}"],
        )
    store = PlanningPolicyStore(test_db)
    policy = store.create_observed(
        "goal", PlanningStrategy.DIRECT_PLAN.value,
        evidence_summary={"sample_count": 10, "shadow_validated": False},
    )

    # Non-ACTIVE promotion needs no receipt; even a stray body flag is harmless.
    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "SUPPORTED", "owner_approved": True},
    )
    assert response.status_code == 200
    assert response.json()["status"] == "SUPPORTED"
    assert response.json()["policy_version"] == policy.policy_version

    detail = test_client.get(f"/api/v1/learning/planning/policies/{policy.id}").json()
    assert detail["policy_key"] == "goal:DIRECT_PLAN"
    assert detail["task_class"] == "goal"
    assert detail["planning_strategy"] == "DIRECT_PLAN"
    assert detail["status"] == "SUPPORTED"


def test_receipt_is_bound_to_exact_policy_state(test_client, test_db):
    _, policy = _seed_policy(test_db, "bound")
    receipts = PlanningPolicyApprovalReceipts(test_db)
    receipt_id = receipts.issue(policy, "ACTIVE", decision_id="owner-rcpt-bound")
    test_db.commit()

    # A receipt cannot be replayed after the approved state changes.
    policy.evidence_summary_json = json.dumps({"sample_count": 99, "shadow_validated": True})
    test_db.commit()
    assert receipts.verify(policy, "ACTIVE", receipt_id) is False

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE", "approval_receipt_id": receipt_id},
    )
    assert response.status_code == 409
    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"


def test_store_ignores_legacy_owner_approved_boolean(test_db):
    store, policy = _seed_policy(test_db, "legacy_bool")
    result = store.promote(
        policy.id, target_status="ACTIVE", owner_approved=True, require_owner_approval=True
    )
    assert result["ok"] is False
    assert "owner" in result["reason"]


# ---------------------------------------------------------------------------
# End-to-end: a trusted owner can now REACH ACTIVE through the dedicated
# approval boundary, while every fail-closed property still holds.
# ---------------------------------------------------------------------------

def test_owner_approval_route_promotes_to_active_end_to_end(test_client, test_db):
    """Owner POSTs the approval path with the control token: receipt issued,
    policy ACTIVE with version incremented and a verified-basis audit event."""
    _, policy = _seed_policy(test_db, "approve_e2e")
    version_before = policy.policy_version

    response = test_client.post(
        APPROVE_PATH.format(policy_id=policy.id),
        json={"decision_id": "owner-approve-e2e-1"},
    )

    assert response.status_code == 200
    body = response.json()
    assert body["status"] == "ACTIVE"
    assert body["policy_version"] == version_before + 1
    receipt_id = body["approval_receipt_id"]
    assert receipt_id == "owner-approve-e2e-1"

    test_db.refresh(policy)
    assert policy.status == "ACTIVE"
    assert policy.policy_version == version_before + 1

    # The receipt is durable and bound to the pre-promotion state.
    from backend.learning.planning_store import PlanningPolicyApprovalReceipts
    from backend.models.database import PermissionDecisionReceiptRow
    row = test_db.query(PermissionDecisionReceiptRow).filter_by(
        decision_id=receipt_id
    ).first()
    assert row is not None
    assert row.status == RECEIPT_STATUS_APPROVED

    # Audit event records the verified basis, never a caller-supplied claim.
    from backend.models.database import TaskEvent
    event = test_db.query(TaskEvent).filter(
        TaskEvent.event_type == "planning.policy.promoted"
    ).order_by(TaskEvent.id.desc()).first()
    assert event is not None
    audit = json.loads(event.event_data)
    assert audit["to"] == "ACTIVE"
    assert audit["approval_basis"] == "owner_approval_receipt"
    assert audit["approval_receipt_id"] == receipt_id
    assert audit["owner_approved"] is True


def test_owner_approval_route_without_decision_id_mints_fresh_one(test_client, test_db):
    """A fresh decision_id is minted server-side when the owner omits it."""
    _, policy = _seed_policy(test_db, "approve_fresh")

    first = test_client.post(APPROVE_PATH.format(policy_id=policy.id), json={})
    assert first.status_code == 200
    rid1 = first.json()["approval_receipt_id"]
    assert isinstance(rid1, str) and rid1.strip()

    # The policy is now ACTIVE so a second approval cannot re-promote it.
    second = test_client.post(APPROVE_PATH.format(policy_id=policy.id), json={})
    assert second.status_code == 409


def test_body_only_owner_approved_still_409_after_new_route(test_client, test_db):
    """The legacy /promote endpoint still fails closed for a bare body boolean."""
    _, policy = _seed_policy(test_db, "body_only_after")
    version_before = policy.policy_version

    response = test_client.post(
        PROMOTE_PATH.format(policy_id=policy.id),
        json={"target_status": "ACTIVE", "owner_approved": True},
    )

    assert response.status_code == 409
    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"
    assert policy.policy_version == version_before


def test_invalid_and_stale_receipts_still_409(test_client, test_db):
    """A made-up receipt and a state-bound stale receipt are both rejected."""
    _, policy = _seed_policy(test_db, "stale_receipt")
    receipts = PlanningPolicyApprovalReceipts(test_db)
    stale_id = receipts.issue(policy, "ACTIVE", decision_id="owner-rcpt-stale-e2e")
    test_db.commit()
    # Any change to the approved state invalidates the SHA-256 binding.
    policy.evidence_summary_json = json.dumps({"sample_count": 99, "shadow_validated": True})
    test_db.commit()

    for bad_id in ("not-a-real-receipt", stale_id):
        response = test_client.post(
            PROMOTE_PATH.format(policy_id=policy.id),
            json={"target_status": "ACTIVE", "approval_receipt_id": bad_id},
        )
        assert response.status_code == 409
    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"


def test_unauthorized_caller_cannot_approve(test_client, test_db):
    """Callers without a valid control token cannot issue an approval."""
    _, policy = _seed_policy(test_db, "unauth_approve")

    # No control token.
    no_token = test_client.post(
        APPROVE_PATH.format(policy_id=policy.id),
        json={},
        headers={"X-Control-Token": ""},
    )
    assert no_token.status_code == 401

    # Wrong control token.
    bad_token = test_client.post(
        APPROVE_PATH.format(policy_id=policy.id),
        json={},
        headers={"X-Control-Token": "wrong-token"},
    )
    assert bad_token.status_code == 401

    # Device-channel credentials are refused even with a valid control token.
    device = test_client.post(
        APPROVE_PATH.format(policy_id=policy.id),
        json={},
        headers={"X-Sythion-Device-Id": "paired-device"},
    )
    assert device.status_code == 403

    test_db.refresh(policy)
    assert policy.status == "SHADOW_VALIDATED"
