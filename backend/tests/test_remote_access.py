"""Stage 3a remote trust boundary: hashed credentials, explicit grants, trust floors."""

from datetime import datetime

from backend.models.database import DeviceCredential, DeviceGrant, RegisteredDevice, TaskEvent
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import (
    CAPABILITIES,
    check_access,
    grant_capability,
    issue_device_credential,
    list_grants,
    revoke_capability,
    revoke_device_credential,
    verify_device_credential,
)

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db, trust="observed"):
    device = register_local_device(test_db, now=NOW)
    if trust != "observed":
        set_trust_level(test_db, device["device_id"], trust)
    test_db.commit()
    return device["device_id"]


def test_capability_floors_are_declared():
    assert "conversation.write" in CAPABILITIES
    assert "goals.submit" in CAPABILITIES
    assert "messages.read" in CAPABILITIES


def test_credential_is_hashed_and_verifiable(test_db):
    device_id = _device(test_db)
    token = issue_device_credential(test_db, device_id, now=NOW)
    test_db.commit()

    row = test_db.query(DeviceCredential).filter_by(device_id=device_id).one()
    assert row.token_hash != token
    assert token not in row.token_hash
    assert verify_device_credential(test_db, device_id, token) is True
    assert verify_device_credential(test_db, device_id, "wrong-token") is False
    assert verify_device_credential(test_db, "dev_missing", token) is False


def test_revoked_credential_cannot_verify(test_db):
    device_id = _device(test_db)
    token = issue_device_credential(test_db, device_id, now=NOW)
    test_db.commit()
    assert revoke_device_credential(test_db, device_id, now=NOW) is True
    test_db.commit()
    assert verify_device_credential(test_db, device_id, token) is False
    assert revoke_device_credential(test_db, device_id, now=NOW) is False


def test_issue_requires_known_device(test_db):
    try:
        issue_device_credential(test_db, "dev_missing", now=NOW)
        raise AssertionError("credential issued to unknown device")
    except LookupError:
        pass


def test_grant_and_revoke_are_explicit(test_db):
    device_id = _device(test_db)
    assert list_grants(test_db, device_id) == []
    grant_capability(test_db, device_id, "briefing.read")
    grant_capability(test_db, device_id, "briefing.read")  # idempotent
    test_db.commit()
    assert list_grants(test_db, device_id) == ["briefing.read"]
    assert test_db.query(DeviceGrant).count() == 1

    assert revoke_capability(test_db, device_id, "briefing.read") is True
    test_db.commit()
    assert list_grants(test_db, device_id) == []


def test_grant_rejects_unknown_capability_and_device(test_db):
    device_id = _device(test_db)
    try:
        grant_capability(test_db, device_id, "omnipotent")
        raise AssertionError("unknown capability granted")
    except ValueError:
        pass
    try:
        grant_capability(test_db, "dev_missing", "briefing.read")
        raise AssertionError("grant to unknown device accepted")
    except LookupError:
        pass


def test_access_requires_grant_and_trust(test_db):
    device_id = _device(test_db, trust="observed")
    # A capability with a higher floor is refused even if granted.
    grant_capability(test_db, device_id, "conversation.write")
    test_db.commit()
    assert check_access(test_db, device_id, "conversation.write") == (
        False, "insufficient_trust")

    # An ungranted low-floor capability is refused.
    assert check_access(test_db, device_id, "briefing.read") == (False, "no_grant")

    grant_capability(test_db, device_id, "briefing.read")
    set_trust_level(test_db, device_id, "trusted")
    test_db.commit()
    assert check_access(test_db, device_id, "briefing.read") == (True, "ok")
    assert check_access(test_db, device_id, "conversation.write") == (True, "ok")

    # goals.submit needs privileged trust: even with a grant, a trusted device
    # cannot use it.
    grant_capability(test_db, device_id, "goals.submit")
    test_db.commit()
    assert check_access(test_db, device_id, "goals.submit") == (
        False, "insufficient_trust")


def test_access_rejects_unknown_inputs(test_db):
    device_id = _device(test_db)
    assert check_access(test_db, "dev_missing", "briefing.read") == (
        False, "unknown_device")
    assert check_access(test_db, device_id, "omnipotent") == (
        False, "unknown_capability")


def test_remote_turn_requires_valid_device_credential(test_client, test_db):
    response = test_client.post("/api/v1/remote/turns", json={"text": "hello"})
    assert response.status_code == 401

    device_id = _device(test_db)
    token = issue_device_credential(test_db, device_id, now=NOW)
    test_db.commit()
    headers = {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": "wrong",
    }
    response = test_client.post(
        "/api/v1/remote/turns", json={"text": "hello"}, headers=headers)
    assert response.status_code == 401


def test_remote_turn_requires_explicit_capability_grant(test_client, test_db):
    device_id = _device(test_db)
    token = issue_device_credential(test_db, device_id, now=NOW)
    test_db.commit()
    headers = {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": token,
    }
    # Trusted device but no grant -> denied before the reasoning boundary.
    set_trust_level(test_db, device_id, "trusted")
    test_db.commit()
    response = test_client.post(
        "/api/v1/remote/turns", json={"text": "hello"}, headers=headers)
    assert response.status_code == 403
    assert "no_grant" in response.json()["detail"]


def test_remote_turn_works_for_granted_device(test_client, test_db, monkeypatch):
    from backend.tests.test_assistant_turn_api import FakeEngine

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )

    device_id = _device(test_db, trust="trusted")
    token = issue_device_credential(test_db, device_id, now=NOW)
    grant_capability(test_db, device_id, "conversation.write")
    test_db.commit()
    headers = {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": token,
    }
    response = test_client.post(
        "/api/v1/remote/turns", json={"text": "hello"}, headers=headers)
    assert response.status_code == 200
    body = response.json()
    assert body["assistant_text"]
    assert body["device_id"] == device_id
    assert body["authorized"] is False
    assert body["approval_issued"] is False
    assert body["briefing"]["authorized"] is False

    events = [
        e.event_type for e in test_db.query(TaskEvent)
        .filter(TaskEvent.event_type == "device.remote_turn").all()
    ]
    assert events == ["device.remote_turn"]


def test_remote_endpoints_are_owner_actions_and_audited(test_client, test_db):
    device_id = _device(test_db)

    response = test_client.post(f"/api/v1/devices/{device_id}/credential")
    assert response.status_code == 200
    token = response.json()["token"]
    assert token and test_db.query(DeviceCredential).one().token_hash != token

    response = test_client.get(f"/api/v1/devices/{device_id}/grants")
    assert response.json()["grants"] == []

    response = test_client.put(
        f"/api/v1/devices/{device_id}/grants/briefing.read")
    assert response.status_code == 200
    assert response.json()["granted"] is True
    assert check_access(test_db, device_id, "briefing.read") == (True, "ok")

    response = test_client.delete(
        f"/api/v1/devices/{device_id}/grants/briefing.read")
    assert response.json()["revoked"] is True

    response = test_client.delete(f"/api/v1/devices/{device_id}/credential")
    assert response.json()["revoked"] is True

    events = [
        e.event_type for e in test_db.query(TaskEvent)
        .filter(TaskEvent.event_type.like("device.%")).all()
    ]
    for expected in (
        "device.credential_issued", "device.grant_added",
        "device.grant_removed", "device.credential_revoked",
    ):
        assert expected in events
