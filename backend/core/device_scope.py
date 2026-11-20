"""Authentication scope for device endpoints (not the owner control plane).

This is a separate scope from ``LocalControlBoundaryMiddleware``: it does NOT
weaken the control-token/loopback boundary for ``/api/``. A device presents its
own credential (never the owner control token) and the peer must still be on
loopback — remote reach is provided by a tunnel that terminates on loopback.
Credential validity and capability grants are checked by the endpoint before a
socket is accepted; this middleware only enforces the scope's shape.
"""

from __future__ import annotations

from urllib.parse import parse_qs

from starlette.types import ASGIApp, Receive, Scope, Send

from backend.core.auth import LOOPBACK_HOSTS

DEVICE_SCOPE_PREFIX = "/device/"


def device_query_credentials(scope: Scope) -> tuple[str, str]:
    """Device id/token from a WebSocket handshake query string."""
    try:
        params = parse_qs(
            scope.get("query_string", b"").decode("latin-1"),
            keep_blank_values=True,
        )
    except Exception:
        return "", ""
    device_id = params.get("device_id", [""])[0]
    token = params.get("token", [""])[0]
    return device_id, token


class DeviceScopeMiddleware:
    """Guard only ``/device/*`` routes; all other paths pass through unchanged."""

    def __init__(self, app: ASGIApp) -> None:
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        path = scope.get("path", "")
        if scope["type"] not in {"http", "websocket"} or not path.startswith(DEVICE_SCOPE_PREFIX):
            await self.app(scope, receive, send)
            return

        client = scope.get("client")
        if client and client[0] not in LOOPBACK_HOSTS:
            await self._reject(scope, send, 403, "Requests must originate from the loopback interface")
            return

        # The shell's private config can disable the channel. It can never
        # widen the loopback rule above or grant a capability.
        from backend.core.handshake import read_device_channel_config

        config = read_device_channel_config()
        if not config["enabled"]:
            await self._reject(scope, send, 503, "device channel disabled")
            return
        if scope["type"] == "websocket" and not (
            config["ws_enabled"] and config["relay_enabled"]
        ):
            await self._reject(scope, send, 503, "device websocket scope disabled")
            return

        if scope["type"] == "websocket":
            device_id, token = device_query_credentials(scope)
        else:
            headers = {
                key.decode("latin-1").lower(): value.decode("latin-1")
                for key, value in scope.get("headers", [])
            }
            device_id = headers.get("x-sythion-device-id", "")
            token = headers.get("x-sythion-device-token", "")

        if not device_id or not token:
            await self._reject(scope, send, 401, "Device credential is required")
            return

        scope["device_auth"] = {"device_id": device_id, "token": token}
        await self.app(scope, receive, send)

    @staticmethod
    async def _reject(scope: Scope, send: Send, status_code: int, detail: str) -> None:
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008, "reason": detail})
            return
        body = ('{"detail":"' + detail + '"}').encode("utf-8")
        await send({
            "type": "http.response.start",
            "status": status_code,
            "headers": [(b"content-type", b"application/json")],
        })
        await send({"type": "http.response.body", "body": body})
