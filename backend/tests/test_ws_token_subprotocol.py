"""WebSocket credential transport via a browser-settable subprotocol.

The control token and the device credential must never travel in the URL:
query strings leak into devtools, proxy logs and access logs. The supported
path is ``sythion-token.<base64url(token)>``; ``?token=`` is a deprecated
fallback kept only for older clients.
"""

import base64
import json
import logging
from datetime import datetime

import pytest
from fastapi import FastAPI, WebSocket
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from backend.core.auth import (
    LocalControlBoundaryMiddleware,
    extract_websocket_token,
    make_token_subprotocol,
    negotiate_token_subprotocol,
    token_from_subprotocols,
)
from backend.core.device_scope import device_query_credentials
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import grant_capability, issue_device_credential

TOKEN = "unit-control-token"
DEVICE_TOKEN = "unit-device-token"
DEVICE_ID = "dev_1"
NOW = datetime(2026, 11, 20, 8, 0, 0)


def _ws_scope(*, subprotocols=(), query=b""):
    return {
        "type": "websocket",
        "path": "/api/v1/browser/ws",
        "query_string": query,
        "subprotocols": list(subprotocols),
        "headers": [],
        "client": ("127.0.0.1", 1234),
    }


def _device(test_db):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    set_trust_level(test_db, device_id, "trusted")
    token = issue_device_credential(test_db, device_id, now=NOW)
    grant_capability(test_db, device_id, "conversation.write")
    test_db.commit()
    return device_id, token


def test_subprotocol_format_is_stable_and_does_not_contain_the_raw_token():
    expected = "sythion-token." + base64.urlsafe_b64encode(
        TOKEN.encode("utf-8")).decode("ascii").rstrip("=")
    assert make_token_subprotocol(TOKEN) == expected
    assert TOKEN not in expected
    assert expected.startswith("sythion-token.")
    assert token_from_subprotocols(["chat", expected]) == TOKEN


def test_extract_websocket_token_prefers_subprotocol_over_legacy_query():
    scope = _ws_scope(
        subprotocols=[make_token_subprotocol(TOKEN)],
        query=b"token=legacy-token",
    )
    assert extract_websocket_token(scope) == TOKEN


def test_extract_websocket_token_keeps_legacy_query_fallback():
    scope = _ws_scope(query=b"token=legacy-token")
    assert extract_websocket_token(scope) == "legacy-token"


def test_extract_websocket_token_rejects_missing_and_malformed_values():
    # Extraction is deliberately dumb: it returns the presented value and the
    # caller performs the same constant-time comparison as before. Wrong tokens
    # are rejected at that boundary (see the WS integration tests below).
    assert extract_websocket_token(_ws_scope()) == ""
    assert extract_websocket_token(_ws_scope(subprotocols=["chat"])) == ""
    assert extract_websocket_token(
        _ws_scope(subprotocols=[make_token_subprotocol("wrong-token")])) == "wrong-token"
    assert extract_websocket_token(
        _ws_scope(subprotocols=["sythion-token.!!!!"])) == ""
    assert extract_websocket_token(
        _ws_scope(subprotocols=["sythion-token."])) == ""
    assert extract_websocket_token(_ws_scope(query=b"token=")) == ""


def test_negotiate_token_subprotocol_only_echoes_what_the_client_offered():
    subprotocol = make_token_subprotocol(TOKEN)
    assert negotiate_token_subprotocol(_ws_scope(subprotocols=[subprotocol]), TOKEN) == subprotocol
    assert negotiate_token_subprotocol(_ws_scope(), TOKEN) is None
    assert negotiate_token_subprotocol(
        _ws_scope(subprotocols=["chat"]), TOKEN) is None
    assert negotiate_token_subprotocol(_ws_scope(subprotocols=[subprotocol]), "") is None


def test_device_query_credentials_reads_token_from_subprotocol():
    scope = _ws_scope(
        subprotocols=[make_token_subprotocol(DEVICE_TOKEN)],
        query=f"device_id={DEVICE_ID}".encode("ascii"),
    )
    assert device_query_credentials(scope) == (DEVICE_ID, DEVICE_TOKEN)


def test_device_query_credentials_keeps_legacy_query_fallback():
    scope = _ws_scope(
        query=f"device_id={DEVICE_ID}&token={DEVICE_TOKEN}".encode("ascii"),
    )
    assert device_query_credentials(scope) == (DEVICE_ID, DEVICE_TOKEN)


def test_device_query_credentials_rejects_missing_and_malformed_token():
    # A wrong-but-well-formed token is passed through to the unchanged
    # verify_device_credential / check_access checks, which reject it (see the
    # gateway integration tests below).
    scope = _ws_scope(query=f"device_id={DEVICE_ID}".encode("ascii"))
    assert device_query_credentials(scope) == (DEVICE_ID, "")
    scope = _ws_scope(
        subprotocols=[make_token_subprotocol("wrong-token")],
        query=f"device_id={DEVICE_ID}".encode("ascii"),
    )
    assert device_query_credentials(scope) == (DEVICE_ID, "wrong-token")
    scope = _ws_scope(
        subprotocols=["sythion-token.!!!!"],
        query=f"device_id={DEVICE_ID}".encode("ascii"),
    )
    assert device_query_credentials(scope) == (DEVICE_ID, "")


def _control_app():
    app = FastAPI()
    app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: TOKEN,
    )

    @app.websocket("/api/v1/ws")
    async def ws_echo(websocket: WebSocket):
        await websocket.accept()
        await websocket.send_text("ok")
        await websocket.close()

    return app


def test_control_ws_accepts_subprotocol_token_without_logging_it(caplog):
    client = TestClient(_control_app())
    with caplog.at_level(logging.DEBUG):
        with client.websocket_connect(
            "/api/v1/ws", subprotocols=[make_token_subprotocol(TOKEN)],
        ) as ws:
            assert ws.receive_text() == "ok"
    assert TOKEN not in caplog.text


def test_control_ws_rejects_wrong_or_missing_subprotocol_token():
    client = TestClient(_control_app())
    for subprotocols in (["chat"], [make_token_subprotocol("wrong-token")], []):
        with pytest.raises(WebSocketDisconnect) as exc:
            with client.websocket_connect("/api/v1/ws", subprotocols=subprotocols):
                pass
        assert exc.value.code == 1008


def test_control_ws_legacy_query_fallback_still_works():
    client = TestClient(_control_app())
    with client.websocket_connect(f"/api/v1/ws?token={TOKEN}") as ws:
        assert ws.receive_text() == "ok"


def test_device_gateway_ws_negotiates_subprotocol_and_keeps_token_out_of_logs(
    test_client, test_db, caplog,
):
    device_id, token = _device(test_db)
    subprotocol = make_token_subprotocol(token)
    with caplog.at_level(logging.DEBUG):
        with test_client.websocket_connect(
            f"/device/relay/ws?device_id={device_id}",
            subprotocols=[subprotocol],
        ) as ws:
            assert ws.accepted_subprotocol == subprotocol
            session = json.loads(ws.receive_text())
            assert session["type"] == "session"
            assert session["device_id"] == device_id
    assert token not in caplog.text


def test_device_gateway_ws_rejects_wrong_subprotocol_token(test_client, test_db):
    device_id, _ = _device(test_db)
    with pytest.raises(WebSocketDisconnect) as exc:
        with test_client.websocket_connect(
            f"/device/relay/ws?device_id={device_id}",
            subprotocols=[make_token_subprotocol("wrong-token")],
        ):
            pass
    assert exc.value.code == 1008


def test_device_gateway_ws_legacy_query_fallback_still_works(test_client, test_db):
    device_id, token = _device(test_db)
    with test_client.websocket_connect(
        f"/device/relay/ws?device_id={device_id}&token={token}",
    ) as ws:
        assert ws.accepted_subprotocol is None
        session = json.loads(ws.receive_text())
        assert session["device_id"] == device_id
