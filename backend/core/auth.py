"""
Authentication middleware for Sythion (Synth) API.
Validates the per-launch control token on all requests.
"""

import hmac
from collections.abc import Callable

from fastapi import Request, HTTPException, status

from starlette.types import ASGIApp, Message, Receive, Scope, Send
from typing import Optional


PACKAGED_APP_ORIGINS = frozenset({
    "tauri://localhost",
    "http://tauri.localhost",
    "https://tauri.localhost",
})

DEVELOPMENT_APP_ORIGINS = frozenset({
    "http://localhost:3000",
    "http://127.0.0.1:3000",
})

PROTECTED_PATHS = frozenset({"/health", "/tasks", "/tools"})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})


def control_origins(development_mode: bool) -> frozenset[str]:
    """Return the exact trusted origins for the active runtime mode."""
    if development_mode:
        return PACKAGED_APP_ORIGINS | DEVELOPMENT_APP_ORIGINS
    return PACKAGED_APP_ORIGINS





class LocalControlBoundaryMiddleware:
    """Require the packaged dashboard identity for all local control routes."""

    def __init__(
        self,
        app: ASGIApp,
        token_provider: Callable[[], Optional[str]],
        allowed_origins: frozenset[str] = PACKAGED_APP_ORIGINS,
    ) -> None:
        self.app = app
        self.token_provider = token_provider
        self.allowed_origins = allowed_origins

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"} or not self._is_protected(scope):
            await self.app(scope, receive, send)
            return

        headers = {
            key.decode("latin-1").lower(): value.decode("latin-1")
            for key, value in scope.get("headers", [])
        }
        token = headers.get("x-control-token")
        if not token:
            authorization = headers.get("authorization", "")
            if authorization.lower().startswith("bearer "):
                token = authorization[7:]

        expected_token = self.token_provider()
        if not expected_token or not token or not hmac.compare_digest(token, expected_token):
            await self._reject(scope, send, 401, "Invalid or missing control token")
            return

        # The sidecar is loopback-only. Reject non-loopback peers outright; the
        # token/origin rules are the second line of defense for browser CSRF.
        client = scope.get("client")
        if client and client[0] not in LOOPBACK_HOSTS:
            await self._reject(scope, send, 403, "Requests must originate from the loopback interface")
            return

        # The token already authenticates the caller. The origin check is an
        # anti-CSRF boundary for browser requests: a trusted Origin must be one
        # of the packaged/dev dashboard origins. Non-browser local clients
        # (CLI, curl, smoke tests) send no Origin header and are authenticated
        # by the token alone.
        origin = headers.get("origin")
        if origin and origin not in self.allowed_origins:
            await self._reject(scope, send, 403, "Untrusted request origin")
            return

        await self.app(scope, receive, send)

    @staticmethod
    def _is_protected(scope: Scope) -> bool:
        path = scope.get("path", "")
        # The browser bridge WebSocket and status endpoints connect from local browser extensions
        if path.startswith("/api/v1/browser/"):
            return False
        return path.startswith("/api/") or path in PROTECTED_PATHS


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
