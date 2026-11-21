"""Owner reconciliation of uncertain remote actions: audited, no replay."""

import pytest

from backend.models.database import (
    RemoteAction,
    RemoteActionEvent,
    RemoteActionReconciliation,
    TaskEvent,
)
from backend.runtime.remote_action_lifecycle import (
    RECEIVED,
    UNCERTAIN,
    RemoteActionManager,
)
from backend.runtime.remote_action_reconciliation import (
    ReconciliationError,
    reconcile_action,
)


def _uncertain(test_db, key="unc-key", device="dev_1"):
    action = RemoteActionManager(test_db).create(
        idempotency_key=key, device_id=device, task_id="task-1")
    RemoteActionManager(test_db).transition(action["action_id"], UNCERTAIN, reason="lost")
    test_db.commit()
    return action["action_id"]


def test_reconciliation_records_decision_without_reviving_action(test_db):
    action_id = _uncertain(test_db)
    result = reconcile_action(
        test_db, action_id,
        decision_id="owner-ui-rec-1",
        outcome="effect_not_performed",
        evidence_ref="evidence:task-system:checked",
        note="verified in target system",
    )
    test_db.commit()

    assert result["original_state"] == UNCERTAIN
    assert result["new_action_id"] is None
    assert result["authorized"] is False
    action = test_db.query(RemoteAction).filter_by(action_id=action_id).one()
    assert action.state == UNCERTAIN  # terminal, untouched

    records = test_db.query(RemoteActionReconciliation).all()
    assert len(records) == 1
    assert records[0].decision_id == "owner-ui-rec-1"
    assert records[0].outcome == "effect_not_performed"

    markers = [e for e in test_db.query(RemoteActionEvent).all()
               if e.to_state.startswith("marker:reconciled")]
    assert len(markers) == 1
    assert markers[0].decision_id == "owner-ui-rec-1"


def test_reconciliation_can_start_fresh_action_with_new_key(test_db):
    action_id = _uncertain(test_db)
    result = reconcile_action(
        test_db, action_id,
        decision_id="owner-ui-rec-2",
        outcome="abandoned",
        new_idempotency_key="fresh-key-2",
    )
    test_db.commit()

    assert result["new_action_id"] is not None
    fresh = test_db.query(RemoteAction).filter_by(
        action_id=result["new_action_id"]).one()
    assert fresh.state == RECEIVED
    assert fresh.idempotency_key == "fresh-key-2"
    assert fresh.device_id == "dev_1"


def test_reconciliation_rejects_replayed_key(test_db):
    action_id = _uncertain(test_db, key="same-key")
    try:
        reconcile_action(
            test_db, action_id, decision_id="d", outcome="abandoned",
            new_idempotency_key="same-key",
        )
        raise AssertionError("replayed key accepted")
    except ReconciliationError as exc:
        assert exc.code == "REPLAY_REFUSED"


def test_reconciliation_rejects_non_uncertain_action(test_db):
    action = RemoteActionManager(test_db).create(
        idempotency_key="open-key", device_id="dev_1")
    test_db.commit()
    try:
        reconcile_action(test_db, action["action_id"], decision_id="d",
                         outcome="abandoned")
        raise AssertionError("open action reconciled")
    except ReconciliationError as exc:
        assert exc.code == "NOT_UNCERTAIN"


def test_reconciliation_requires_decision_and_valid_outcome(test_db):
    action_id = _uncertain(test_db, key="k1")
    try:
        reconcile_action(test_db, action_id, decision_id="", outcome="abandoned")
        raise AssertionError("empty decision accepted")
    except ReconciliationError as exc:
        assert exc.code == "DECISION_REQUIRED"

    action_id = _uncertain(test_db, key="k2")
    try:
        reconcile_action(test_db, action_id, decision_id="d", outcome="magic")
        raise AssertionError("unknown outcome accepted")
    except ReconciliationError as exc:
        assert exc.code == "INVALID_OUTCOME"


def test_owner_api_reconciles_and_audits(test_client, test_db):
    action_id = _uncertain(test_db, key="api-key")
    response = test_client.post(
        f"/api/v1/remote-actions/{action_id}/reconcile",
        json={
            "decision_id": "owner-ui-api",
            "outcome": "effect_not_performed",
            "evidence_ref": "evidence:independent:1",
            "new_idempotency_key": "api-fresh",
        },
    )
    assert response.status_code == 200
    body = response.json()
    assert body["decision_id"] == "owner-ui-api"
    assert body["new_action_id"] is not None
    assert body["original_state"] == UNCERTAIN

    events = [e.event_type for e in test_db.query(TaskEvent)
              .filter(TaskEvent.event_type == "remote_action.reconciled").all()]
    assert events == ["remote_action.reconciled"]

    listed = test_client.get(
        f"/api/v1/remote-actions/{action_id}/reconciliations")
    assert listed.status_code == 200
    assert len(listed.json()["reconciliations"]) == 1


def test_device_channel_cannot_reconcile(test_client, test_db):
    action_id = _uncertain(test_db, key="dev-api-key")
    headers = {
        "X-Sythion-Device-Id": "dev_1",
        "X-Sythion-Device-Token": "token",
    }
    response = test_client.post(
        f"/api/v1/remote-actions/{action_id}/reconcile",
        json={"decision_id": "d", "outcome": "abandoned"},
        headers=headers,
    )
    assert response.status_code == 403


def test_unknown_action_returns_404(test_client, test_db):
    response = test_client.post(
        "/api/v1/remote-actions/ra_missing/reconcile",
        json={"decision_id": "d", "outcome": "abandoned"},
    )
    assert response.status_code == 404


@pytest.mark.parametrize(
    "outcome", ["effect_confirmed", "effect_not_performed", "abandoned"],
)
def test_reconciled_action_auto_dismissed_from_uncertain_listing(test_db, outcome):
    """A decided action leaves the owner queue; unrelated ones stay; state is intact."""
    manager = RemoteActionManager(test_db)
    resolved_id = _uncertain(test_db, key=f"resolved-{outcome}", device="dev_1")
    unrelated_id = _uncertain(test_db, key=f"unrelated-{outcome}", device="dev_2")

    before = {a["action_id"] for a in manager.list_uncertain()}
    assert resolved_id in before
    assert unrelated_id in before

    result = reconcile_action(
        test_db, resolved_id,
        decision_id=f"owner-{outcome}",
        outcome=outcome,
    )
    test_db.commit()
    assert result["outcome"] == outcome

    after = {a["action_id"] for a in manager.list_uncertain()}
    assert resolved_id not in after            # auto-dismissed
    assert unrelated_id in after               # genuinely unresolved stays visible

    # Projection-only: the action is still the same terminal uncertain record.
    action = test_db.query(RemoteAction).filter_by(action_id=resolved_id).one()
    assert action.state == UNCERTAIN
    assert result["original_state"] == UNCERTAIN
    assert test_db.query(RemoteActionReconciliation).filter_by(
        action_id=resolved_id).count() == 1


def test_reconciled_action_absent_from_uncertain_api(test_client, test_db):
    """The owner-facing uncertain endpoint reflects the same dismissal."""
    resolved_id = _uncertain(test_db, key="api-resolved")
    unrelated_id = _uncertain(test_db, key="api-unrelated")

    before = test_client.get("/api/v1/remote-actions/uncertain").json()["actions"]
    assert {a["action_id"] for a in before} == {resolved_id, unrelated_id}

    reconcile_action(
        test_db, resolved_id,
        decision_id="owner-api-dismiss",
        outcome="abandoned",
    )
    test_db.commit()

    after = test_client.get("/api/v1/remote-actions/uncertain").json()["actions"]
    assert {a["action_id"] for a in after} == {unrelated_id}
