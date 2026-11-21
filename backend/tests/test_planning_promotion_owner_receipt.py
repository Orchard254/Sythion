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

PROMOTE_PATH = "/api/v1/learning/planning/policies/{policy_id}/promote"


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
