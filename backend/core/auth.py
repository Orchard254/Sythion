"""
Authentication middleware for Sythion (Synth) API.
Validates the per-launch control token on all requests.
"""

import hmac
from collections.abc import Callable
from urllib.parse import parse_qs

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
    # Secondary loopback dev port so a second dev instance does not depend on
    # port 3000 being free (auth only accepts these exact origins, never arbitrary ports).
    "http://localhost:3001",
    "http://127.0.0.1:3001",
})

PROTECTED_PATHS = frozenset({"/health", "/tasks", "/tools"})

LOOPBACK_HOSTS = frozenset({"127.0.0.1", "::1", "localhost"})

BROWSER_BRIDGE_PREFIX = "/api/v1/browser/"

# Browser extensions always send a scheme-based Origin on WebSocket and fetch
# requests (e.g. chrome-extension://<id>). These schemes are accepted ONLY on
# browser-bridge paths and ONLY together with the per-launch control token.
EXTENSION_ORIGIN_SCHEMES = (
    "chrome-extension://",
    "moz-extension://",
    "safari-web-extension://",
)


def control_origins(development_mode: bool) -> frozenset[str]:
    """Return the exact trusted origins for the active runtime mode."""
    if development_mode:
        return PACKAGED_APP_ORIGINS | DEVELOPMENT_APP_ORIGINS
    return PACKAGED_APP_ORIGINS


def extract_websocket_token(scope: Scope) -> str:
    """Extract the control token from a WebSocket handshake query string.

    Browsers cannot set custom headers on WebSocket connections, so the
    browser extension authenticates with ``?token=<per-launch control token>``.
    """
    query_string = scope.get("query_string", b"")
    try:
        params = parse_qs(query_string.decode("latin-1"), keep_blank_values=True)
    except Exception:
        return ""
    values = params.get("token", [])
    return values[0] if values else ""





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
        if not token and scope["type"] == "websocket":
            # WebSocket handshakes cannot carry custom headers; the browser
            # extension authenticates via the query string instead.
            token = extract_websocket_token(scope)

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
        # anti-CSRF boundary for browser requests: a trusted Origin must be
        # one of the packaged/dev dashboard origins — or, exclusively on the
        # browser-bridge paths, a browser-extension origin. Non-browser local
        # clients (CLI, curl, smoke tests) send no Origin header and are
        # authenticated by the token alone.
        origin = headers.get("origin")
        if origin and not self._origin_allowed(scope, origin):
            await self._reject(scope, send, 403, "Untrusted request origin")
            return

        await self.app(scope, receive, send)

    def _origin_allowed(self, scope: Scope, origin: str) -> bool:
        if origin in self.allowed_origins:
            return True
        path = scope.get("path", "")
        if not path.startswith(BROWSER_BRIDGE_PREFIX):
            return False

        # Exact extension-origin parsing. Only a small, explicitly allowlisted
        # set of schemes is accepted, in canonical lowercase form. The origin
        # must be exactly `scheme://<non-empty id>` with no userinfo, port,
        # path, query, or fragment components.
        from urllib.parse import urlsplit

        parsed = urlsplit(origin)
        allowed_schemes = frozenset(
            scheme.split("://", 1)[0] for scheme in EXTENSION_ORIGIN_SCHEMES
        )
        if parsed.scheme not in allowed_schemes:
            return False

        netloc = parsed.netloc
        if not netloc:
            return False
        if any(ch.isspace() for ch in netloc):
            return False
        if "@" in netloc or ":" in netloc:
            return False
        if parsed.username is not None or parsed.password is not None or parsed.port is not None:
            return False
        if parsed.path or parsed.query or parsed.fragment:
            return False
        return True

    @staticmethod
    def _is_protected(scope: Scope) -> bool:
        path = scope.get("path", "")
        # Browser-bridge routes are protected like every other API route.
        # WebSocket handshakes authenticate via query-string token (see
        # extract_websocket_token); HTTP bridge routes use the standard
        # X-Control-Token header. There is deliberately no unauthenticated
        # path into browser-control capabilities.
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
