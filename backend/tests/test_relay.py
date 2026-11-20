"""Stage 3/4 first-party relay: durable, device-bound message sessions."""

from datetime import datetime, timedelta

from backend.models.database import RelayMessage, RelaySession
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.relay import (
    close_session,
    enqueue_reply,
    open_session,
    poll_messages,
    post_message,
    session_belongs_to,
)
from backend.services.remote_access import (
    grant_capability,
    issue_device_credential,
)

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    set_trust_level(test_db, device_id, "trusted")
    issue_device_credential(test_db, device_id, now=NOW)
    grant_capability(test_db, device_id, "conversation.write")
    test_db.commit()
    return device_id


def _headers(test_db, device_id):
    from backend.services.remote_access import issue_device_credential

    # Reuse the credential issued in _device by reading its plaintext token is
    # impossible (hash-only); issue a fresh one for the request.
    token = issue_device_credential(test_db, device_id, now=NOW)
    test_db.commit()
    return {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": token,
    }


def test_open_session_seeds_briefings(test_db):
    from backend.models.database import Task

    device_id = _device(test_db)
    test_db.add(Task(
        id="brief-task", original_request="request",
        state="waiting_for_user", pending_question="Which file?",
    ))
    test_db.commit()
    session = open_session(test_db, device_id)
    test_db.commit()

    assert session["device_id"] == device_id
    assert session["status"] == "open"
    row = test_db.query(RelaySession).filter_by(id=session["session_id"]).one()
    assert row.expires_at > row.created_at

    messages = poll_messages(test_db, session["session_id"])
    assert [m["kind"] for m in messages] == ["briefing", "morning_brief"]
    assert all(m["role"] == "synth" for m in messages)


def test_post_poll_and_reply_flow(test_db):
    device_id = _device(test_db)
    session = open_session(test_db, device_id)
    test_db.commit()
    sid = session["session_id"]

    posted = post_message(test_db, sid, "hello")
    reply = enqueue_reply(test_db, sid, "hi there")
    test_db.commit()

    messages = poll_messages(test_db, sid, after_id=posted["id"] - 1)
    roles = [m["role"] for m in messages]
    assert "owner" in roles
    assert "synth" in roles
    assert any(m["text"] == "hi there" for m in messages)


def test_session_is_device_bound_and_expires(test_db):
    device_id = _device(test_db)
    session = open_session(test_db, device_id)
    test_db.commit()
    sid = session["session_id"]

    assert session_belongs_to(test_db, sid, device_id) is True
    assert session_belongs_to(test_db, sid, "dev_other") is False

    row = test_db.query(RelaySession).filter_by(id=sid).one()
    row.expires_at = datetime.utcnow() - timedelta(seconds=1)
    test_db.commit()
    assert session_belongs_to(test_db, sid, device_id) is False


def test_close_session(test_db):
    device_id = _device(test_db)
    session = open_session(test_db, device_id)
    test_db.commit()
    sid = session["session_id"]
    assert session_belongs_to(test_db, sid, device_id) is True
    assert close_session(test_db, sid) is True
    test_db.commit()
    assert session_belongs_to(test_db, sid, device_id) is False


def test_text_is_bounded(test_db):
    device_id = _device(test_db)
    session = open_session(test_db, device_id)
    posted = post_message(test_db, session["session_id"], "x" * 20000)
    assert len(posted["text"]) <= 8000


def test_relay_endpoints_require_device_auth(test_client, test_db):
    response = test_client.post("/api/v1/remote/sessions")
    assert response.status_code == 401


def test_relay_endpoints_full_flow(test_client, test_db, monkeypatch):
    from backend.models.database import Task
    from backend.tests.test_assistant_turn_api import FakeEngine

    test_db.add(Task(
        id="brief-task", original_request="request",
        state="waiting_for_user", pending_question="Which file?",
    ))
    test_db.commit()
    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )

    device_id = _device(test_db)
    headers = _headers(test_db, device_id)

    response = test_client.post("/api/v1/remote/sessions", headers=headers)
    assert response.status_code == 200
    sid = response.json()["session_id"]

    response = test_client.get(
        f"/api/v1/remote/sessions/{sid}/messages", headers=headers)
    assert response.status_code == 200
    assert len(response.json()["messages"]) == 2

    response = test_client.post(
        f"/api/v1/remote/sessions/{sid}/messages",
        json={"text": "hello"}, headers=headers,
    )
    assert response.status_code == 200
    assert response.json()["authorized"] is False

    response = test_client.get(
        f"/api/v1/remote/sessions/{sid}/messages", headers=headers)
    kinds = [m["kind"] for m in response.json()["messages"]]
    assert kinds == ["briefing", "morning_brief", "text", "text"]

    response = test_client.delete(
        f"/api/v1/remote/sessions/{sid}", headers=headers)
    assert response.json()["closed"] is True

    response = test_client.get(
        f"/api/v1/remote/sessions/{sid}/messages", headers=headers)
    assert response.status_code == 404


def test_other_device_cannot_access_session(test_client, test_db):
    from backend.models.database import RegisteredDevice

    device_id = _device(test_db)
    headers = _headers(test_db, device_id)
    sid = test_client.post(
        "/api/v1/remote/sessions", headers=headers).json()["session_id"]

    # A second trusted device with a valid credential still cannot see the
    # first device's session. Insert a distinct device row directly (the local
    # registry always returns this machine's single id).
    other_id = "dev_other"
    test_db.add(RegisteredDevice(
        device_id=other_id, name="phone", kind="phone",
        trust_level="observed", first_seen=NOW, last_seen=NOW,
        capabilities="[]",
    ))
    set_trust_level(test_db, other_id, "trusted")
    issue_device_credential(test_db, other_id, now=NOW)
    grant_capability(test_db, other_id, "conversation.write")
    test_db.commit()
    other_headers = _headers(test_db, other_id)

    response = test_client.get(
        f"/api/v1/remote/sessions/{sid}/messages", headers=other_headers)
    assert response.status_code == 404


def test_http_send_deduplicates_by_idempotency_key(test_client, test_db, monkeypatch):
    from backend.models.database import RemoteAction
    from backend.tests.test_assistant_turn_api import FakeEngine

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )
    device_id = _device(test_db)
    headers = _headers(test_db, device_id)
    sid = test_client.post("/api/v1/remote/sessions", headers=headers).json()["session_id"]
    body = {"text": "hello", "idempotency_key": "phone-http-1"}

    first = test_client.post(
        f"/api/v1/remote/sessions/{sid}/messages", json=body, headers=headers)
    assert first.status_code == 200
    assert first.json()["action_state"] == "completed"
    action_id = first.json()["action_id"]

    # Reusing the same key on a resolved action is refused, not replayed.
    replay = test_client.post(
        f"/api/v1/remote/sessions/{sid}/messages", json=body, headers=headers)
    assert replay.status_code == 409
    assert "not replayed" in replay.json()["detail"]

    matches = test_db.query(RemoteAction).filter_by(
        idempotency_key="phone-http-1").all()
    assert len(matches) == 1
    assert matches[0].action_id == action_id


def test_messages_are_durable(test_db):
    device_id = _device(test_db)
    sid = open_session(test_db, device_id)["session_id"]
    post_message(test_db, sid, "persist me")
    test_db.commit()
    test_db.expire_all()
    messages = poll_messages(test_db, sid)
    assert any(m["text"] == "persist me" for m in messages)
