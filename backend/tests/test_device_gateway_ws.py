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


def test_ws_rejects_when_shell_disables_channel(test_client, test_db, monkeypatch):
    import backend.core.handshake as handshake

    import backend.api.device_gateway_routes as gateway

    device_id, token = _device(test_db)
    disabled = {"version": 1, "enabled": False,
                "relay_enabled": False, "ws_enabled": False}
    monkeypatch.setattr(handshake, "read_device_channel_config", lambda: disabled)
    monkeypatch.setattr(gateway, "read_device_channel_config", lambda: disabled)
    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect(_ws_url(device_id, token)):
            pass
    assert exc.value.code == 1008


def test_ws_ack_frame_is_recorded_durably(test_client, test_db):
    from backend.models.database import RelayAck, RelayMessage

    device_id, token = _device(test_db)
    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        session = json.loads(ws.receive_text())
        assert session["type"] == "session"
        # No blockers exist, so only the morning brief is seeded (a briefing is
        # seeded only when the user briefing has blockers).
        seeded = json.loads(ws.receive_text())
        assert seeded["type"] == "message"
        # Acknowledge the seeded message's durable sequence.
        ws.send_text(json.dumps({"type": "ack", "sequence": seeded["sequence"]}))
        # A malformed ack must not tear down the socket or be persisted.
        ws.send_text(json.dumps({"type": "ack", "sequence": 999999}))
        ws.send_text(json.dumps({"type": "poll"}))

    records = test_db.query(RelayAck).all()
    assert len(records) == 1
    assert records[0].device_id == device_id
    assert records[0].session_id == session["session_id"]
    assert records[0].sequence == seeded["sequence"]
    # The acked sequence is a real durable RelayMessage in this session.
    message = test_db.query(RelayMessage).filter_by(id=seeded["sequence"]).one()
    assert message.session_id == session["session_id"]


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


def _stub_engine(monkeypatch):
    from backend.tests.test_assistant_turn_api import FakeEngine

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )


def test_ws_open_poll_and_send(test_client, test_db, monkeypatch):
    _stub_engine(monkeypatch)
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
        assert accepted["reply_sequence"] > accepted["sequence"]
        assert accepted["action_state"] == "completed"

        # The send path pushes the owner message and the durable Synth reply.
        owner_message = json.loads(ws.receive_text())
        assert owner_message["role"] == "owner"
        assert owner_message["text"] == "hello from phone"
        assert owner_message["sequence"] == accepted["sequence"]

        reply = json.loads(ws.receive_text())
        assert reply["role"] == "synth"
        assert reply["kind"] == "text"
        assert reply["text"] == "Hello from Synth."
        assert reply["sequence"] == accepted["reply_sequence"]

        # A poll cannot create new durable messages; verify by DB rather than
        # blocking on a socket frame that is intentionally never sent.
        # The gateway shares the fixture Session across threads, so wait for a
        # handler reply before each read: the error frame is sent only after all
        # of the send path's commits, which removes a pre-existing race where
        # the main thread queried while the handler was mid-commit.
        from backend.models.database import RelayMessage

        ws.send_text("not json")
        assert json.loads(ws.receive_text())["reason"] == "invalid_json"
        before = test_db.query(RelayMessage).filter_by(
            session_id=session["session_id"]).count()
        ws.send_text(json.dumps({"type": "poll"}))
        ws.send_text(json.dumps({"type": "ack", "sequence": reply["sequence"]}))
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["reason"] == "invalid_json"
        test_db.expire_all()
        after = test_db.query(RelayMessage).filter_by(
            session_id=session["session_id"]).count()
        assert after == before


def test_ws_send_records_reply_evidence_and_event(test_client, test_db, monkeypatch):
    from backend.models.database import RemoteAction, RelayMessage, TaskEvent

    _stub_engine(monkeypatch)
    device_id, token = _device(test_db)
    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        ws.receive_text()  # session
        ws.receive_text()  # morning brief
        ws.send_text(json.dumps({"type": "send", "text": "ping"}))
        accepted = json.loads(ws.receive_text())
        ws.receive_text()  # owner message
        ws.receive_text()  # synth reply

    action = test_db.query(RemoteAction).filter_by(
        action_id=accepted["action_id"]).one()
    assert action.state == "completed"
    assert action.evidence_ref.startswith("relay-message:")
    assert "event:" in action.evidence_ref

    reply = test_db.query(RelayMessage).filter_by(
        id=accepted["reply_sequence"]).one()
    assert reply.role == "synth"
    assert reply.text == "Hello from Synth."

    events = [e.event_type for e in test_db.query(TaskEvent)
              .filter(TaskEvent.event_type == "device.relay_message").all()]
    assert events == ["device.relay_message"]


def test_ws_send_failure_is_recorded_uncertain(test_client, test_db, monkeypatch):
    from backend.models.database import RemoteAction

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
    device_id, token = _device(test_db)
    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        ws.receive_text()  # session
        ws.receive_text()  # morning brief
        ws.send_text(json.dumps({"type": "send", "text": "ping"}))
        error = json.loads(ws.receive_text())
        assert error["type"] == "error"
        assert error["reason"] == "send_recorded_uncertain"

    action = test_db.query(RemoteAction).filter_by(
        action_id=error["action_id"]).one()
    assert action.state == "uncertain"


def test_ws_rejects_bad_frames_without_closing(test_client, test_db):
    device_id, token = _device(test_db)
    with test_client.websocket_connect(_ws_url(device_id, token)) as ws:
        ws.receive_text()  # session
        ws.receive_text()  # morning brief (no blockers in this test)
        ws.send_text("not json")
        assert json.loads(ws.receive_text())["reason"] == "invalid_json"
        ws.send_text(json.dumps({"type": "unknown"}))
        assert json.loads(ws.receive_text())["reason"] == "unknown_frame_type"
