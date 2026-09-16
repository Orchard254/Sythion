"""
Regression tests for the authenticated browser-bridge trust boundary.

Protected runtime contract ("Browser"): browser control cannot bypass
authentication or runtime policy. There is no unauthenticated path into
/api/v1/browser/*; WebSocket handshakes authenticate with the per-launch
control token (query string, since browsers cannot set WS headers) and a
browser-extension origin; every other bridge route uses the standard header.
All connections must come from loopback.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.auth import LocalControlBoundaryMiddleware, control_origins


TOKEN = "test-bridge-token"


def create_bridge_client(raise_exceptions=True) -> TestClient:
    from backend.api.browser_bridge_routes import router

    app = FastAPI()
    app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: TOKEN,
        allowed_origins=control_origins(False),
    )
    app.include_router(router)
    return TestClient(app)


# --- 1. Unauthenticated connection fails -------------------------------------


@pytest.mark.parametrize("path", ["/api/v1/browser/ws", "/api/v1/browser/status"])
def test_unauthenticated_browser_connection_fails(path):
    client = create_bridge_client()

    if path.endswith("/ws"):
        from starlette.websockets import WebSocketDisconnect

        with pytest.raises(WebSocketDisconnect):
            with client.websocket_connect(path):
                pass
    else:
        response = client.get(path)
        assert response.status_code == 401


# --- 2. Invalid credentials fail -----------------------------------------------


def test_websocket_with_wrong_token_fails():
    client = create_bridge_client()
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            "/api/v1/browser/ws?token=wrong-token",
            headers={"Origin": "chrome-extension://abcdefg"},
        ):
            pass


def test_status_with_wrong_token_fails():
    client = create_bridge_client()
    response = client.get(
        "/api/v1/browser/status",
        headers={"X-Control-Token": "wrong-token"},
    )
    assert response.status_code == 401


# --- 3. Valid credentials succeed ------------------------------------------------


def _handshake(client):
    with client.websocket_connect(
        f"/api/v1/browser/ws?token={TOKEN}",
        headers={"Origin": "chrome-extension://abcdefg"},
    ) as ws:
        ws.send_text(json.dumps({"browser": "Chrome Test"}))
        reply = json.loads(ws.receive_text())
        assert reply["type"] == "handshake_ok"
        return reply


def test_valid_token_extension_origin_connects():
    client = create_bridge_client()
    reply = _handshake(client)
    assert reply["status"] == "connected"


def test_valid_token_without_origin_connects():
    """Non-browser local clients (no Origin header) authenticate by token."""
    client = create_bridge_client()
    with client.websocket_connect(f"/api/v1/browser/ws?token={TOKEN}") as ws:
        ws.send_text(json.dumps({"browser": "LocalClient"}))
        reply = json.loads(ws.receive_text())
        assert reply["type"] == "handshake_ok"


def test_status_with_valid_token_succeeds():
    client = create_bridge_client()
    # Ensure no leftover connection state from other tests.
    from backend.services.browser_bridge_service import BrowserBridgeService
    BrowserBridgeService.get_instance()._active_socket = None

    response = client.get(
        "/api/v1/browser/status",
        headers={"X-Control-Token": TOKEN},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["connected"] is False


# --- 4. Unauthorized local clients cannot impersonate the extension --------------


def test_non_extension_http_origin_rejected_on_bridge_route():
    """A valid token with an arbitrary web origin must not open the bridge."""
    client = create_bridge_client()
    response = client.get(
        "/api/v1/browser/status",
        headers={
            "X-Control-Token": TOKEN,
            "Origin": "http://evil.example.com",
        },
    )
    assert response.status_code == 403


def test_websocket_web_origin_rejected():
    client = create_bridge_client()
    from starlette.websockets import WebSocketDisconnect

    with pytest.raises(WebSocketDisconnect):
        with client.websocket_connect(
            f"/api/v1/browser/ws?token={TOKEN}",
            headers={"Origin": "http://evil.example.com"},
        ):
            pass


# --- 5. The browser bridge remains loopback-only ---------------------------------


@pytest.mark.asyncio
async def test_non_loopback_peer_rejected_even_with_valid_token():
    """Direct middleware check: a non-loopback peer is refused outright."""
    sent = []

    async def receive():
        return {"type": "websocket.connect"}

    async def send(message):
        sent.append(message)

    scope = {
        "type": "websocket",
        "path": "/api/v1/browser/ws",
        "headers": [],
        "query_string": f"token={TOKEN}".encode(),
        "client": ("192.0.2.77", 51000),  # TEST-NET address: not loopback
    }

    middleware = LocalControlBoundaryMiddleware(
        app=lambda s, r, snd: None,
        token_provider=lambda: TOKEN,
    )
    await middleware(scope, receive, send)

    close_messages = [m for m in sent if m["type"] == "websocket.close"]
    assert close_messages, "expected the middleware to close the connection"


def test_sidecar_binds_loopback_only():
    from backend.core.config import settings

    assert settings.host in {"127.0.0.1", "::1", "localhost"}


# --- 6. Existing browser functionality still works -------------------------------


@pytest.mark.asyncio
async def test_bridge_service_roundtrip_still_works():
    """Behind authentication, the RPC bridge behaves exactly as before."""
    import asyncio
    from unittest.mock import AsyncMock

    from backend.services.browser_bridge_service import BrowserBridgeService

    service = BrowserBridgeService()
    mock_ws = AsyncMock()
    await service.connect(mock_ws, {"browser": "Chrome"})

    cmd_task = asyncio.create_task(service.get_tabs())
    await asyncio.sleep(0.01)

    sent_payload = json.loads(mock_ws.send_text.call_args[0][0])
    service.handle_incoming_message(json.dumps({
        "id": sent_payload["id"],
        "result": {"count": 2, "tabs": [{"id": 1}, {"id": 2}]},
    }))

    tabs = await cmd_task
    assert len(tabs) == 2
    await service.disconnect()