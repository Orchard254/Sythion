"""Shell device-channel config gates every /api/v1/remote/* endpoint."""

from datetime import datetime

import backend.core.handshake as handshake
from backend.core.handshake import default_device_channel_config
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import grant_capability, issue_device_credential

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db, trust="privileged"):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    set_trust_level(test_db, device_id, trust)
    token = issue_device_credential(test_db, device_id, now=NOW)
    for capability in ("conversation.write", "goals.submit", "briefing.read"):
        grant_capability(test_db, device_id, capability)
    test_db.commit()
    return device_id, token


def _headers(device_id, token):
    return {
        "X-Sythion-Device-Id": device_id,
        "X-Sythion-Device-Token": token,
    }


def _set_config(monkeypatch, **overrides):
    config = dict(default_device_channel_config())
    config.update(overrides)
    monkeypatch.setattr(handshake, "read_device_channel_config", lambda: config)
    return config


def _remote_calls(client, headers):
    """Exercise every remote endpoint family through the shared gate."""
    return [
        ("post", "/api/v1/remote/sessions", None),
        ("get", "/api/v1/remote/sessions/rs_missing/messages", None),
        ("post", "/api/v1/remote/sessions/rs_missing/messages", {"text": "hi"}),
        ("delete", "/api/v1/remote/sessions/rs_missing", None),
        ("post", "/api/v1/remote/turns", {"text": "hi"}),
        ("post", "/api/v1/remote/goals", {"title": "goal"}),
    ]


def test_disabled_channel_refuses_all_remote_endpoints(test_client, test_db, monkeypatch):
    _set_config(monkeypatch, enabled=False)
    device_id, token = _device(test_db)
    headers = _headers(device_id, token)
    for method, path, body in _remote_calls(test_client, headers):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(test_client, method)(path, **kwargs)
        assert response.status_code == 503, (method, path, response.text)
        assert "disabled" in response.json()["detail"]


def test_relay_disabled_refuses_all_remote_endpoints(test_client, test_db, monkeypatch):
    _set_config(monkeypatch, relay_enabled=False)
    device_id, token = _device(test_db)
    headers = _headers(device_id, token)
    for method, path, body in _remote_calls(test_client, headers):
        kwargs = {"headers": headers}
        if body is not None:
            kwargs["json"] = body
        response = getattr(test_client, method)(path, **kwargs)
        assert response.status_code == 503, (method, path, response.text)


def test_ws_flag_does_not_disable_http_relay(test_client, test_db, monkeypatch):
    from backend.tests.test_assistant_turn_api import FakeEngine

    monkeypatch.setattr(
        "backend.services.conversation_turn_engine.ConversationTurnEngine",
        FakeEngine,
    )
    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *a, **k: object(),
    )
    # ws_enabled only affects /device/*; HTTP relay must still work.
    _set_config(monkeypatch, ws_enabled=False)
    device_id, token = _device(test_db)
    response = test_client.post(
        "/api/v1/remote/turns", json={"text": "hi"}, headers=_headers(device_id, token))
    assert response.status_code == 200


def test_gate_runs_before_credential_check(test_client, test_db, monkeypatch):
    _set_config(monkeypatch, enabled=False)
    # No device credentials at all: the disabled channel wins with 503.
    response = test_client.post("/api/v1/remote/turns", json={"text": "hi"})
    assert response.status_code == 503


def test_corrupt_config_fails_closed_on_remote_endpoints(test_client, test_db, monkeypatch):
    from backend.core.handshake import _disabled_device_channel_config

    monkeypatch.setattr(
        handshake, "read_device_channel_config",
        lambda: _disabled_device_channel_config(),
    )
    device_id, token = _device(test_db)
    response = test_client.post(
        "/api/v1/remote/goals", json={"title": "goal"},
        headers=_headers(device_id, token))
    assert response.status_code == 503
