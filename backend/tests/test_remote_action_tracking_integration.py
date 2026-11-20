"""Remote turn/goal/WS send paths are durably tracked by the lifecycle."""

import json
from datetime import datetime

from backend.models.database import RemoteAction
from backend.runtime.remote_action_lifecycle import COMPLETED, UNCERTAIN
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import grant_capability, issue_device_credential

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db, trust, capability):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    set_trust_level(test_db, device_id, trust)
    token = issue_device_credential(test_db, device_id, now=NOW)
    grant_capability(test_db, device_id, capability)
    test_db.commit()
    return device_id, token


def _headers(device_id, token):
    return {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": token,
    }


def test_remote_turn_is_tracked_to_verified_completion(
    test_client, test_db, monkeypatch,
):
    from backend.tests.test_assistant_turn_api import FakeEngine

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )
    device_id, token = _device(test_db, "trusted", "conversation.write")

    response = test_client.post(
        "/api/v1/remote/turns",
        json={"text": "hello", "client_request_id": "turn-1"},
        headers=_headers(device_id, token),
    )
    assert response.status_code == 200
    body = response.json()
    action = test_db.query(RemoteAction).filter_by(
        action_id=body["action_id"]).one()
    assert action.state == COMPLETED
    assert action.evidence_ref and action.evidence_ref.startswith("event:")
    assert action.idempotency_key == "turn-1"


def test_remote_turn_failure_is_recorded_uncertain(
    test_client, test_db, monkeypatch,
):
    class FailingEngine:
        def __init__(self, *a, **k):
            pass

        async def run_turn(self, *a, **k):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FailingEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )
    device_id, token = _device(test_db, "trusted", "conversation.write")

    response = test_client.post(
        "/api/v1/remote/turns",
        json={"text": "hello", "client_request_id": "turn-fail"},
        headers=_headers(device_id, token),
    )
    assert response.status_code == 502
    assert "uncertain" in response.json()["detail"]

    action = test_db.query(RemoteAction).filter_by(
        idempotency_key="turn-fail").one()
    assert action.state == UNCERTAIN
    assert "remote_turn_failed" in (action.reason or "")


def test_remote_goal_is_tracked_to_verified_completion(test_client, test_db):
    device_id, token = _device(test_db, "privileged", "goals.submit")

    response = test_client.post(
        "/api/v1/remote/goals",
        json={"title": "A tracked goal", "client_request_id": "goal-1"},
        headers=_headers(device_id, token),
    )
    assert response.status_code == 200
    body = response.json()
    action = test_db.query(RemoteAction).filter_by(
        action_id=body["action_id"]).one()
    assert action.state == COMPLETED
    assert action.evidence_ref == body["goal_id"]
    assert action.idempotency_key == "goal-1"


def test_ws_send_is_tracked_to_verified_completion(test_client, test_db):
    device_id, token = _device(test_db, "trusted", "conversation.write")
    headers = _headers(device_id, token)
    url = (
        f"/device/relay/ws?device_id={device_id}&token={token}"
        "&idempotency_key=ws-send-1"
    )
    with test_client.websocket_connect(url) as ws:
        session = json.loads(ws.receive_text())
        assert session["type"] == "session"
        ws.receive_text()  # morning brief
        ws.send_text(json.dumps({
            "type": "send", "text": "hello", "idempotency_key": "ws-send-1",
        }))
        accepted = json.loads(ws.receive_text())
        assert accepted["type"] == "accepted"
        assert accepted["action_state"] == COMPLETED
        action_id = accepted["action_id"]

    action = test_db.query(RemoteAction).filter_by(action_id=action_id).one()
    assert action.state == COMPLETED
    assert action.evidence_ref.startswith("relay-message:")
    assert action.idempotency_key == "ws-send-1"


def test_uncertain_action_is_never_auto_replayed(test_client, test_db, monkeypatch):
    class FailingEngine:
        def __init__(self, *a, **k):
            pass

        async def run_turn(self, *a, **k):
            raise RuntimeError("provider failed")

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FailingEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )
    device_id, token = _device(test_db, "trusted", "conversation.write")
    body = {"text": "hello", "client_request_id": "turn-replay"}

    first = test_client.post(
        "/api/v1/remote/turns", json=body,
        headers=_headers(device_id, token),
    )
    replay = test_client.post(
        "/api/v1/remote/turns", json=body,
        headers=_headers(device_id, token),
    )
    assert first.status_code == 502
    assert replay.status_code == 409
    assert "not replayed" in replay.json()["detail"]

    matches = test_db.query(RemoteAction).filter_by(
        idempotency_key="turn-replay").all()
    assert len(matches) == 1
    assert matches[0].state == UNCERTAIN
