"""Headless Core launcher for Sythion (Synth).

Runs the FastAPI backend (``backend.main:app``) with uvicorn bound strictly to
127.0.0.1, with no Tauri window. It reuses the existing Phase A handshake,
profile-lock and private-file conventions rather than reimplementing them:

  * Launch token: backend.core.handshake.write_launch_token / token_file_path.
    The user-only ACL is applied by handshake._write_private (chmod 0600 on
    POSIX, _restrict_acl_windows on Windows).
  * Single instance: backend.core.profile_lock.ProfileLock -- the very lock the
    backend lifespan acquires. We probe it before starting uvicorn so a second
    concurrent launch fails early with a clear message.
  * Data dir / log / handshake paths: backend.core.storage. SYTHION_DATA_DIR is
    the canonical override. Build-namespace isolation stays entirely inside
    backend.core.secrets: this launcher never sets or overrides any secret or
    prototype-identity env var, so it cannot widen or redirect that boundary.

stdout carries only operational metadata (port, pid, log path, token *file*
path). The token VALUE is never printed, logged, or passed on a command line.

Usage:
    python scripts/synth_core.py [--data-dir DIR] [--port 8000] [--no-browser]
"""

from __future__ import annotations

import argparse
import os
import sys
import webbrowser

# Make the repository root importable so `backend.*` resolves regardless of cwd.
REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
if REPO_ROOT not in sys.path:
    sys.path.insert(0, REPO_ROOT)

import uvicorn  # noqa: E402

from backend.core.handshake import (  # noqa: E402
    token_file_path,
    write_launch_token,
)
from backend.core.profile_lock import ProfileLock  # noqa: E402
from backend.core.storage import (  # noqa: E402
    DATA_DIR_ENV,
    LEGACY_DATA_DIR_ENV,
    log_path,
    normalize_data_dir,
)

APP_IMPORT = "backend.main:app"
LOOPBACK_HOST = "127.0.0.1"
DEFAULT_PORT = 8000

EXIT_ERROR = 1
EXIT_LOCK_HELD = 2

# Env vars this launcher writes. Listed so tests can isolate/restore them.
MANAGED_ENV_VARS = (
    DATA_DIR_ENV,
    LEGACY_DATA_DIR_ENV,
    "SYTHION_HANDSHAKE_TOKEN_FILE",
    "SYTHION_PORT",
)


def build_parser() -> argparse.ArgumentParser:
    """Return the CLI parser with a clear, self-documenting --help."""
    parser = argparse.ArgumentParser(
        prog="synth_core",
        description=(
            "Run the Sythion (Synth) Core backend headlessly (no Tauri window). "
            "Binds uvicorn to 127.0.0.1 only and enforces the single-instance "
            "profile lock before the server starts."
        ),
    )
    parser.add_argument(
        "--data-dir",
        default="",
        metavar="DIR",
        help=(
            f"Profile directory; sets the canonical {DATA_DIR_ENV} env var "
            "(default: the backend's resolved data dir)."
        ),
    )
    parser.add_argument(
        "--port",
        type=int,
        default=DEFAULT_PORT,
        metavar="PORT",
        help=f"Fixed loopback port on 127.0.0.1 (default: {DEFAULT_PORT}).",
    )
    parser.add_argument(
        "--no-browser",
        action="store_true",
        help="Do not open the dashboard in a browser (fully headless).",
    )
    return parser


def _configure_data_dir(data_dir: str) -> str:
    """Make the chosen profile dir authoritative and return its absolute path.

    The legacy override is cleared so a stale value cannot conflict with (or
    split) the canonical one -- storage.resolve_data_dir raises when the two
    disagree, and a headless launcher must not silently pick one.
    """
    os.environ.pop(LEGACY_DATA_DIR_ENV, None)
    if data_dir:
        resolved = normalize_data_dir(data_dir)
    else:
        from backend.core.storage import data_dir as _data_dir

        resolved = normalize_data_dir(_data_dir())
    os.environ[DATA_DIR_ENV] = resolved
    return resolved


def _single_instance_available() -> bool:
    """Probe the existing profile lock without holding it across uvicorn.run.

    The backend's ASGI lifespan acquires (and on shutdown releases) the same
    ProfileLock. Because uvicorn.run executes the app in THIS process, keeping a
    second ProfileLock held here would make the app's own lifespan fail to
    acquire it. We therefore acquire+release a probe: a live incumbent makes the
    probe fail closed (clean early refusal), and the app then owns the lock for
    the real process lifetime. This never kills or bypasses the first instance.
    """
    probe = ProfileLock()
    if not probe.acquire():
        return False
    probe.release()
    return True


def main(argv=None) -> int:
    """Launch the Core headlessly. Returns a process exit code."""
    parser = build_parser()
    args = parser.parse_args(argv)

    if not 1 <= args.port <= 65535:
        print("ERROR: --port must be between 1 and 65535", file=sys.stderr)
        return EXIT_ERROR

    try:
        resolved_dir = _configure_data_dir(args.data_dir)
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not set profile directory: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # Enforce single-instance before any token/DB/handshake state is touched.
    if not _single_instance_available():
        print(
            "ERROR: another Sythion Core instance holds the profile lock for "
            f"'{resolved_dir}'; refusing to start a second concurrent instance.",
            file=sys.stderr,
        )
        return EXIT_LOCK_HELD

    # Write the per-launch control token with the helper's user-only ACL, then
    # point the in-process app at the exact same file (existing Phase A contract).
    try:
        write_launch_token()
        token_path = token_file_path()
        os.environ["SYTHION_HANDSHAKE_TOKEN_FILE"] = token_path
        # The backend lifespan publishes the bound port in sidecar.json from
        # SYTHION_PORT; keep it in sync with the port uvicorn actually binds.
        os.environ["SYTHION_PORT"] = str(args.port)
        # Discard a stale handshake so readiness readers see only this launch.
        from backend.core.storage import handshake_path

        try:
            os.remove(handshake_path())
        except OSError:
            pass
        log_file = log_path()
    except (OSError, ValueError) as exc:
        print(f"ERROR: could not prepare launch handshake: {exc}", file=sys.stderr)
        return EXIT_ERROR

    # stdout: operational metadata only. The token value is NEVER emitted.
    print(f"port={args.port}")
    print(f"pid={os.getpid()}")
    print(f"log={log_file}")
    print(f"token_file={token_path}")

    if not args.no_browser:
        try:
            webbrowser.open(f"http://{LOOPBACK_HOST}:{args.port}")
        except Exception:
            # Browser launch is best-effort; Core ownership never depends on it.
            pass

    try:
        uvicorn.run(APP_IMPORT, host=LOOPBACK_HOST, port=args.port, log_level="info")
    except KeyboardInterrupt:
        # uvicorn shuts down gracefully; the app lifespan releases the lock.
        print("Core stopped.", file=sys.stderr)
        return 0
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
