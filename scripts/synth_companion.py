#!/usr/bin/env python3
"""Launch the Sythion (Synth) companion surface in a plain browser.

No Tauri process is required. The Tauri shell is only packaging; the real
runtime is the browser talking to the headless Core at 127.0.0.1:8000.

How the control token reaches the browser (runtime only, never persisted):
  * Built frontend (``frontend/dist``): this script starts a tiny stdlib
    loopback HTTP server. It injects a synchronous <script> into the *served*
    index.html that calls
        sessionStorage.setItem('sythion_control_token', <token>)
    The existing frontend (frontend/src/services/api.ts getControlToken)
    already reads exactly that key and forwards it as X-Control-Token. The
    token is never written to disk, baked into built assets, printed, or
    logged. The served HTML is produced in memory per request with
    Cache-Control: no-store.
  * No built frontend: fall back to the Vite dev server. The launcher starts
    Vite with ``VITE_SYTHION_CONTROL_TOKEN`` in the child process environment;
    the existing frontend (frontend/src/services/bridge.ts) reads that dev-only
    variable. Requires Node/npm and installed frontend dependencies.

The static server also reverse-proxies ``/api/*`` to the headless Core so the
companion's POST /api/v1/assistant/turns actually reaches the backend.

OVERLAY / "ANYTIME" SCOPE (honest limitations)
  The companion surface itself is the compact conversational capsule already
  in the shared bundle (`?surface=companion`); this launcher does not restyle
  it. A browser-only launcher CANNOT provide a global hotkey, always-on-top,
  skip-taskbar, or a true always-present desktop overlay -- those are OS
  integrations owned by the Tauri work unit and are deliberately not
  attempted here. What it CAN provide is a compact, frameless-feeling capsule
  in a *separate* browser window, a stable loopback URL that is cheap to
  bookmark/pin/reopen, and a one-command relaunch. Chromium "--app" kiosk
  flags would give a chromeless window but are browser-specific and are NOT
  used here so we do not promise a guarantee we cannot make cross-browser.
"""

from __future__ import annotations

import argparse
import functools
import http.server
import json
import mimetypes
import os
import socket
import subprocess
import sys
import threading
import urllib.error
import urllib.parse
import urllib.request
import webbrowser
from pathlib import Path
from typing import Optional, Sequence

# --- Constants -------------------------------------------------------------

LOOPBACK_HOST = "127.0.0.1"
SURFACE_QUERY = "?surface=companion"
SURFACE_HASH = "#/companion"
DEFAULT_STATIC_PORT = 8787  # stable default so a pinned/bookmarked tab keeps working
VITE_DEFAULT_PORT = 3000  # matches frontend/vite.config.ts
CORE_HOST = "127.0.0.1"
CORE_PORT = 8000  # matches the Vite proxy target in frontend/vite.config.ts
API_PREFIX = "/api"

TOKEN_FILE_ENV = "SYTHION_HANDSHAKE_TOKEN_FILE"
DATA_DIR_ENV = "SYTHION_DATA_DIR"
LEGACY_DATA_DIR_ENV = "Sythion (Synth)_DATA_DIR"
DEFAULT_DATA_DIR_NAME = "Sythion (Synth)"
TOKEN_FILE_NAME = "token.json"

# Must match frontend/src/services/api.ts: sessionStorage.getItem('sythion_control_token')
SESSION_TOKEN_KEY = "sythion_control_token"
# Must match frontend/src/services/bridge.ts: import.meta.env.VITE_SYTHION_CONTROL_TOKEN
VITE_TOKEN_ENV = "VITE_SYTHION_CONTROL_TOKEN"


class TokenError(RuntimeError):
    """Raised when the headless Core control token cannot be obtained."""


# --- Token discovery -------------------------------------------------------


def default_token_file(data_dir: Optional[str] = None) -> Path:
    """Resolve the token.json path using the Core's documented conventions.

    Mirrors backend/core/handshake.py:token_file_path and
    backend/core/storage.py data-dir resolution without importing the backend.
    An explicit ``data_dir`` (from --data-dir) targets a Core started with a
    custom profile and takes precedence over the environment conventions.
    """
    explicit = os.environ.get(TOKEN_FILE_ENV, "").strip()
    if explicit:
        return Path(explicit)

    resolved = (
        (data_dir or "").strip()
        or os.environ.get(DATA_DIR_ENV, "").strip()
        or os.environ.get(LEGACY_DATA_DIR_ENV, "").strip()
    )
    if not resolved:
        local = os.environ.get("LOCALAPPDATA") or os.path.join(
            os.path.expanduser("~"), "AppData", "Local"
        )
        resolved = os.path.join(local, DEFAULT_DATA_DIR_NAME)
    return Path(resolved) / TOKEN_FILE_NAME


def read_control_token(path: Path) -> str:
    """Read {"token": "..."} (the Core token.json shape). Never echoes contents."""
    if not path.is_file():
        raise TokenError(f"control token file not found: {path}")
    try:
        raw = path.read_text(encoding="utf-8")
    except OSError as exc:
        raise TokenError(f"cannot read control token file {path}: {exc.strerror}") from None
    try:
        payload = json.loads(raw)
    except ValueError:
        raise TokenError(f"invalid JSON in control token file {path}") from None

    token = ""
    if isinstance(payload, dict):
        token = payload.get("token", "")
    elif isinstance(payload, str):
        token = payload
    token = str(token).strip()
    if not token:
        raise TokenError(f"control token file {path} contains no token")
    return token


# --- URL / browser helpers -------------------------------------------------


def build_companion_url(port: int, host: str = LOOPBACK_HOST) -> str:
    """Build the loopback companion URL (surface marker lives in query + hash)."""
    return f"http://{host}:{port}/{SURFACE_QUERY}{SURFACE_HASH}"


def open_browser(url: str) -> None:
    """Open the compact companion in a new browser window (no flags/token)."""
    webbrowser.open_new(url)


def _find_free_port(preferred: int) -> int:
    """Return the preferred loopback port, or a free ephemeral one if busy."""
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        try:
            sock.bind((LOOPBACK_HOST, preferred))
            return preferred
        except OSError:
            pass
    with socket.socket(socket.AF_INET, socket.SOCK_STREAM) as sock:
        sock.bind((LOOPBACK_HOST, 0))
        return int(sock.getsockname()[1])


# --- HTML bootstrap injection ---------------------------------------------


def inject_bootstrap(html: str, token: str) -> str:
    """Inject a synchronous sessionStorage bootstrap before the app bundle.

    The script runs in <head> before any deferred module script, so the token
    is present when getControlToken() first reads it. '<' is escaped so even an
    unexpected token cannot break out of the <script> block.
    """
    key_literal = json.dumps(SESSION_TOKEN_KEY).replace("<", "\\u003c")
    token_literal = json.dumps(token).replace("<", "\\u003c")
    script = (
        "<script>(function(){try{"
        f"sessionStorage.setItem({key_literal},{token_literal});"
        "}catch(e){}})();</script>"
    )
    lowered = html.lower()
    head_close = lowered.find("</head>")
    if head_close != -1:
        return html[:head_close] + script + html[head_close:]
    body_open = lowered.find("<body")
    if body_open != -1:
        gt = html.find(">", body_open)
        if gt != -1:
            return html[: gt + 1] + script + html[gt + 1 :]
    return script + html


# --- Loopback static server (+ /api reverse proxy) ------------------------


class CompanionHandler(http.server.BaseHTTPRequestHandler):
    """Serves frontend/dist and proxies /api to the headless Core, loopback only."""

    server_version = "SynthCompanion/1.0"
    protocol_version = "HTTP/1.1"

    def __init__(self, *args, root: Path, token: str,
                 token_path: Optional[Path] = None,
                 core_port: int = CORE_PORT, **kwargs):
        self._root = root.resolve()
        self._token = token  # literal fallback (e.g. hermetic tests / Vite env)
        self._token_path = token_path
        self._core_port = core_port
        super().__init__(*args, **kwargs)

    def _current_token(self) -> Optional[str]:
        """Re-read the Core token for each index.html response.

        The Core rewrites token.json on every start. Capturing it once at
        process launch leaves a stale token in a long-lived browser session;
        reading the file here keeps a page reload in step with a restarted Core.
        A transient read failure returns None so we never overwrite the
        browser's sessionStorage with an empty token.
        """
        if self._token_path is None:
            return self._token
        try:
            return read_control_token(self._token_path)
        except TokenError:
            return None

    def log_message(self, fmt: str, *args: object) -> None:
        # Never log request lines: headers/URLs could carry the control token.
        return

    def guess_type(self, path: str) -> str:
        guessed, _ = mimetypes.guess_type(path)
        return guessed or "application/octet-stream"

    # -- path safety --------------------------------------------------------

    def _safe_path(self) -> Optional[Path]:
        """Map self.path to a file strictly inside root, or None if unsafe."""
        parsed = urllib.parse.urlparse(self.path)
        raw = urllib.parse.unquote(parsed.path)
        if "\\" in raw or "\x00" in raw:  # no backslash / NUL tricks on Windows
            return None
        parts = [p for p in raw.split("/") if p not in ("", ".")]
        if any(p == ".." for p in parts):  # reject parent traversal
            return None
        candidate = self._root.joinpath(*parts).resolve()
        try:
            if os.path.commonpath([str(self._root), str(candidate)]) != str(self._root):
                return None
        except ValueError:  # different drives on Windows
            return None
        return candidate

    # -- request dispatch ---------------------------------------------------

    def do_GET(self) -> None:
        self._dispatch(body_allowed=False)

    def do_HEAD(self) -> None:
        self._dispatch(body_allowed=False)

    def do_POST(self) -> None:
        self._dispatch(body_allowed=True)

    def do_PUT(self) -> None:
        self._dispatch(body_allowed=True)

    def do_PATCH(self) -> None:
        self._dispatch(body_allowed=True)

    def do_DELETE(self) -> None:
        self._dispatch(body_allowed=True)

    def do_OPTIONS(self) -> None:
        self._dispatch(body_allowed=True)

    def _dispatch(self, body_allowed: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        if parsed.path == API_PREFIX or parsed.path.startswith(API_PREFIX + "/"):
            self._proxy_api(body_allowed)
            return
        if self.command not in ("GET", "HEAD"):
            self.send_error(405, "Method Not Allowed")
            return
        self._serve_static(write_body=self.command == "GET")

    def _serve_static(self, write_body: bool) -> None:
        candidate = self._safe_path()
        if candidate is None:
            self.send_error(403, "Forbidden")
            return
        if candidate.is_dir():
            candidate = candidate / "index.html"
        if not candidate.is_file():
            self.send_error(404, "Not Found")
            return
        try:
            data = candidate.read_bytes()
        except OSError:
            self.send_error(404, "Not Found")
            return

        # Only the root index.html gets the runtime token bootstrap, and the
        # token is re-read on EVERY response so a Core restart is picked up by
        # the next reload instead of leaving a stale control token in place.
        if candidate.name == "index.html" and candidate.parent == self._root:
            token = self._current_token()
            if token:
                data = inject_bootstrap(data.decode("utf-8", errors="replace"), token).encode(
                    "utf-8"
                )

        self.send_response(200)
        self.send_header("Content-Type", self.guess_type(str(candidate)))
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        if write_body:
            self.wfile.write(data)

    def _proxy_api(self, body_allowed: bool) -> None:
        parsed = urllib.parse.urlparse(self.path)
        target = f"http://{CORE_HOST}:{self._core_port}{parsed.path}"
        if parsed.query:
            target += "?" + parsed.query

        body = None
        if body_allowed:
            try:
                length = int(self.headers.get("Content-Length", "0") or "0")
            except ValueError:
                length = 0
            if length > 0:
                body = self.rfile.read(length)

        hop_by_hop = {
            "host",
            "connection",
            "content-length",
            "transfer-encoding",
            "accept-encoding",
        }
        headers = {k: v for k, v in self.headers.items() if k.lower() not in hop_by_hop}
        req = urllib.request.Request(target, data=body, headers=headers, method=self.command)
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                self._forward(resp.status, resp.headers, resp.read())
        except urllib.error.HTTPError as exc:
            self._forward(exc.code, exc.headers, exc.read())
        except (urllib.error.URLError, TimeoutError, OSError):
            self.send_error(502, "Headless Core unavailable")

    def _forward(self, status: int, resp_headers: object, data: bytes) -> None:
        self.send_response(status)
        skip = {
            "transfer-encoding",
            "connection",
            "content-length",
            "content-encoding",
        }
        for key, value in resp_headers.items():  # type: ignore[union-attr]
            if key.lower() not in skip:
                self.send_header(key, value)
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        if self.command != "HEAD":
            self.wfile.write(data)


def build_server(root: Path, token: str, port: int,
                 token_path: Optional[Path] = None) -> http.server.ThreadingHTTPServer:
    """Create (do not start) a loopback-only threaded companion server.

    ``token`` is the startup-validated literal; when ``token_path`` is given the
    handler re-reads the Core's token from it on every index.html response so a
    Core restart cannot leave a stale token in the browser.
    """
    handler = functools.partial(
        CompanionHandler, root=root, token=token, token_path=token_path
    )
    return http.server.ThreadingHTTPServer((LOOPBACK_HOST, port), handler)


def launch_vite(port: int, token: str, frontend_dir: Path) -> subprocess.Popen:
    """Start the Vite dev server with the token in its child-process env only."""
    npm = "npm.cmd" if os.name == "nt" else "npm"
    cmd = [
        npm,
        "run",
        "dev",
        "--",
        "--host",
        LOOPBACK_HOST,
        "--port",
        str(port),
        "--strictPort",
    ]
    env = os.environ.copy()
    env[VITE_TOKEN_ENV] = token  # consumed by bridge.ts (dev-only path)
    kwargs: dict[str, object] = {}
    if os.name == "nt":
        kwargs["creationflags"] = (
            getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
            | 0x00000008  # DETACHED_PROCESS
        )
    else:
        kwargs["start_new_session"] = True
    return subprocess.Popen(
        cmd,
        cwd=str(frontend_dir),
        env=env,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        **kwargs,
    )


# --- CLI -------------------------------------------------------------------


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(
        prog="synth_companion.py",
        description="Open the Sythion companion capsule in a plain loopback browser (no Tauri).",
    )
    parser.add_argument(
        "--token-file",
        type=Path,
        default=None,
        help="Path to the headless Core token.json (default: resolved from the Sythion data dir).",
    )
    parser.add_argument(
        "--data-dir",
        default=None,
        help="Headless Core data dir holding token.json (for a Core started with a custom profile).",
    )
    parser.add_argument(
        "--port",
        type=int,
        default=None,
        help="Loopback port (default: 8787 for built frontend, 3000 for the Vite fallback).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Print only the URL and status; do not launch a browser or a server.",
    )
    args = parser.parse_args(argv)

    token_path = args.token_file or default_token_file(
        str(args.data_dir) if args.data_dir else None
    )
    try:
        token = read_control_token(token_path)
    except TokenError as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        print(
            "Start the headless Core first, or pass --token-file PATH / --data-dir DIR.",
            file=sys.stderr,
        )
        return 2

    frontend = Path(__file__).resolve().parent.parent / "frontend"
    dist = frontend / "dist"
    built = dist.is_dir() and (dist / "index.html").is_file()

    if built:
        port = args.port if args.port is not None else _find_free_port(DEFAULT_STATIC_PORT)
        mode_note = "serving built frontend (compact companion capsule)"
    else:
        port = args.port if args.port is not None else VITE_DEFAULT_PORT
        mode_note = (
            "Vite dev fallback (requires Node/npm and installed frontend deps); "
            "build with 'npm run build' from frontend/ for the standalone server"
        )

    url = build_companion_url(port)

    # --no-browser is diagnostics only: URL + status, nothing else, no token.
    if args.no_browser:
        print(f"Companion surface: {url}")
        print(f"Status: {mode_note}")
        print("Note: browser-only mode cannot provide global hotkey / always-on-top / "
              "skip-taskbar; those require the Tauri overlay work unit.")
        return 0

    if built:
        # Pass the path (not just the captured string) so each index.html
        # response re-reads the Core token; a Core restart then stops leaving a
        # stale token behind in a long-lived browser session.
        server = build_server(dist, token, port, token_path=token_path)
        thread = threading.Thread(target=server.serve_forever, daemon=True)
        thread.start()
        print(f"Companion surface: {url}")
        print(f"Status: {mode_note}")
        open_browser(url)
        try:
            thread.join()
        except KeyboardInterrupt:
            pass
        finally:
            server.shutdown()
            server.server_close()
        return 0

    # Vite fallback: token goes to the browser via VITE_SYTHION_CONTROL_TOKEN.
    try:
        launch_vite(port, token, frontend)
    except OSError as exc:
        print(f"ERROR: could not start Vite dev server: {exc.strerror}", file=sys.stderr)
        print("Build the frontend, or install Node/npm dependencies, and retry.", file=sys.stderr)
        return 3
    print(f"Companion surface: {url}")
    print(f"Status: {mode_note}")
    open_browser(url)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
