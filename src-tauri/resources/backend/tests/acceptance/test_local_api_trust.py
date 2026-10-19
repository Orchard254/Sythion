"""
Acceptance gate: Local API trust (Phase A).

Wrong token, wrong origin, non-loopback clients, and unauthenticated
WebSockets cannot invoke or observe sidecar operations.
"""

import pytest

from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.tests.acceptance.conftest import ACCEPTANCE_CONTROL_TOKEN

from fastapi import FastAPI
from starlette.websockets import WebSocketDisconnect


def test_wrong_token_rejected(client):
    response = client.get("/api/v1/tasks", headers={"X-Control-Token": "wrong-token"})
    assert response.status_code == 401


def test_missing_token_rejected(client):
    response = client.get("/api/v1/tasks")
    assert response.status_code == 401


def test_wrong_origin_rejected_even_with_valid_token(client):
    response = client.get(
        "/api/v1/tasks",
        headers={
            "X-Control-Token": ACCEPTANCE_CONTROL_TOKEN,
            "Origin": "http://evil.example.com",
        },
    )
    assert response.status_code == 403


def test_dev_origin_rejected_when_not_in_dev_mode(client):
    response = client.get(
        "/api/v1/tasks",
        headers={
            "X-Control-Token": ACCEPTANCE_CONTROL_TOKEN,
            "Origin": "http://localhost:3000",
        },
    )
    assert response.status_code == 403


def test_valid_token_without_origin_accepted(client):
    response = client.get("/api/v1/tasks", headers={"X-Control-Token": ACCEPTANCE_CONTROL_TOKEN})
    assert response.status_code == 200


def test_non_loopback_client_rejected():
    """The middleware must reject a peer that is not on the loopback interface."""
    app = FastAPI()

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    seen = {}

    class Capture:
        def __init__(self, inner):
            self.inner = inner

        async def __call__(self, scope, receive, send):
            seen["status"] = None
            await self.inner(scope, receive, send)

    capture = Capture(app)
    wrapped = LocalControlBoundaryMiddleware(
        capture,
        token_provider=lambda: ACCEPTANCE_CONTROL_TOKEN,
        allowed_origins=control_origins(False),
    )

    from starlette.testclient import TestClient

    client = TestClient(wrapped)
    client.get("/api/v1/ping")  # warm up
    # The default TestClient transport reports no client host; run the
    # middleware directly with a crafted non-loopback peer address.
    import anyio

    received = []

    async def send(message):
        received.append(message)

    async def receive():
        return {"type": "http.request", "body": b"", "more_body": False}

    async def main():
        scope = {
            "type": "http",
            "asgi": {"version": "3.0"},
            "http_version": "1.1",
            "method": "GET",
            "scheme": "http",
            "path": "/api/v1/ping",
            "raw_path": b"/api/v1/ping",
            "query_string": b"",
            "headers": [
                (b"host", b"127.0.0.1:8000"),
                (b"x-control-token", ACCEPTANCE_CONTROL_TOKEN.encode()),
            ],
            "client": ("192.168.1.50", 54321),
            "server": ("127.0.0.1", 8000),
        }
        await wrapped(scope, receive, send)

    anyio.run(main)
    assert received[0]["status"] == 403


def test_unauthenticated_websocket_closed():
    app = FastAPI()

    @app.websocket("/api/v1/ws")
    async def ws(websocket):
        await websocket.accept()
        await websocket.send_text("ok")

    wrapped = LocalControlBoundaryMiddleware(
        app,
        token_provider=lambda: ACCEPTANCE_CONTROL_TOKEN,
        allowed_origins=control_origins(False),
    )

    from starlette.testclient import TestClient

    with TestClient(wrapped) as client:
        with pytest.raises(WebSocketDisconnect) as exc_info:
            with client.websocket_connect("/api/v1/ws"):
                pass
        assert exc_info.value.code == 1008
