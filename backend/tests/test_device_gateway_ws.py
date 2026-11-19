"""Device-authenticated WS scope beside the relay; control boundary untouched."""

import json
from datetime import datetime

import pytest
from starlette.websockets import WebSocketDisconnect

from backend.models.database import Task
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import grant_capability, issue_device_credential

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db, *, trust="trusted", grant=True):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    set_trust_level(test_db, device_id, trust)
    token = issue_device_credential(test_db, device_id, now=NOW)
    if grant:
        grant_capability(test_db, device_id, "conversation.write")
    test_db.commit()
    return device_id, token


def _ws_url(device_id, token):
    return f"/device/relay/ws?device_id={device_id}&token={token}"


def test_device_scope_rejects_requests_without_credential(test_client, test_db):
    assert test_client.get("/device/anything").status_code == 401
    device_id, token = _device(test_db)
    # Credential present but no matching route -> the scope passes it through.
    response = test_client.get(
        "/device/anything",
        headers={"X-Sythion-Device-Id": device_id, "X-Sythion-Device-Token": token},
    )
    assert response.status_code == 404


def test_device_scope_rejects_non_loopback_peers():
    import asyncio

    from backend.core.device_scope import DeviceScopeMiddleware

    messages = []

    async def app(scope, receive, send):
        messages.append("called")

    async def receive():
        return {}

    async def send(message):
        messages.append(message)

    scope = {
        "type": "http",
        "path": "/device/relay/ws",
        "client": ("10.0.0.5", 1234),
        "headers": [
            (b"x-sythion-device-id", b"dev_1"),
            (b"x-sythion-device-token", b"token"),
        ],
    }
    asyncio.run(DeviceScopeMiddleware(app)(scope, receive, send))
    assert "called" not in messages
    assert any(m.get("status") == 403 for m in messages if isinstance(m, dict))


def test_control_boundary_is_unchanged(test_client, test_db):
    saved = test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.get("/api/v1/devices").status_code == 401
    finally:
        if saved is not None:
            test_client.headers["X-Control-Token"] = saved


def test_ws_rejects_invalid_credential(test_client, test_db):
    device_id, _ = _device(test_db)
    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect(_ws_url(device_id, "wrong-token")):
            pass
    assert exc.value.code == 1008


def test_ws_rejects_missing_grant(test_client, test_db):
    device_id, token = _device(test_db, grant=False)
    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect(_ws_url(device_id, token)):
            pass
    assert exc.value.code == 1008


def test_ws_open_poll_and_send(test_client, test_db):
    device_id, token = _device(test_db)
    test_db.add(Task(
        id="t-1", original_request="request",
        state="waiting_for_user", pending_question="Which file?",
    ))
    test_db.commit()

    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        session = json.loads(ws.receive_text())
        assert session["type"] == "session"
        assert session["device_id"] == device_id
        assert session["sequence"] == 0
        assert session["authorized"] is False

        briefing = json.loads(ws.receive_text())
        assert briefing["type"] == "message"
        assert briefing["kind"] == "briefing"
        assert briefing["authorized"] is False
        assert briefing["sequence"] > 0

        morning = json.loads(ws.receive_text())
        assert morning["kind"] == "morning_brief"

        ws.send_text(json.dumps({"type": "ack", "sequence": morning["sequence"]}))
        ws.send_text(json.dumps({"type": "send", "text": "hello from phone"}))
        accepted = json.loads(ws.receive_text())
        assert accepted["type"] == "accepted"
        assert accepted["authorized"] is False
        assert accepted["sequence"] > morning["sequence"]

        ws.send_text(json.dumps({"type": "poll"}))
        owner_message = json.loads(ws.receive_text())
        assert owner_message["role"] == "owner"
        assert owner_message["text"] == "hello from phone"
        assert owner_message["sequence"] == accepted["sequence"]


def test_ws_rejects_bad_frames_without_closing(test_client, test_db):
    device_id, token = _device(test_db)
    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        ws.receive_text()  # session
        ws.receive_text()  # morning brief (no blockers in this test)
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["reason"] == "invalid_json"
        ws.send_text(json.dumps({"type": "unknown"}))
        assert json.loads(ws.receive_text())["reason"] == "unknown_frame_type"
