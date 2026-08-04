"""
Sythion (Synth) sidecar launcher (Phase A handshake reference).

This is the launcher-side half of the startup handshake contract that the
Tauri shell will implement in Rust. For development it starts the Python
sidecar exactly the way the packaged app will:

1. Write a fresh per-launch token to the private token file.
2. Start the sidecar (uvicorn) with Sythion (Synth)_HANDSHAKE_TOKEN_FILE set.
3. Wait for the sidecar handshake file to appear with the bound port.
4. Print the resolved port + token.

Usage:
    python scripts/launch_sidecar.py [--port PORT] [--no-open]
"""

import argparse
import json
import os
import subprocess
import sys
import time

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, REPO_ROOT)

from backend.core.handshake import (
    write_launch_token,
    read_handshake,
    token_file_path,
    handshake_path,
)


def pick_port(preferred: int) -> int:
    """Return a free loopback port, preferring the requested one."""
    import socket

    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    try:
        s.bind(("127.0.0.1", preferred))
        return preferred
    except OSError:
        s.close()
        s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
        s.bind(("127.0.0.1", 0))
        port = s.getsockname()[1]
        s.close()
        return port
    finally:
        s.close()


def main() -> int:
    parser = argparse.ArgumentParser(description="Start the Sythion (Synth) sidecar via the handshake contract")
    parser.add_argument("--port", type=int, default=8000, help="Preferred loopback port (default 8000)")
    parser.add_argument("--no-open", action="store_true", help="Do not open the dashboard")
    parser.add_argument("--dev", action="store_true", help="Run in development mode (Vite origin allowed)")
    args = parser.parse_args()

    token = write_launch_token()
    port = pick_port(args.port)
    print(f"[launcher] wrote per-launch token to {token_file_path()}")
    print(f"[launcher] starting sidecar on 127.0.0.1:{port}")

    env = dict(os.environ)
    env["Sythion (Synth)_HANDSHAKE_TOKEN_FILE"] = token_file_path()
    env["Sythion (Synth)_DEVELOPMENT_MODE"] = "true" if args.dev else "false"

    # Remove the handshake file so we can distinguish this launch from a stale one.
    try:
        os.remove(handshake_path())
    except OSError:
        pass

    log_dir = os.path.join(os.environ.get("LOCALAPPDATA", os.path.expanduser("~")), "Sythion (Synth)", "logs")
    os.makedirs(log_dir, exist_ok=True)
    with open(os.path.join(log_dir, "sidecar.out.log"), "ab") as out, \
         open(os.path.join(log_dir, "sidecar.err.log"), "ab") as err:
        sidecar = subprocess.Popen(
            [sys.executable, "-m", "uvicorn", "backend.main:app", "--host", "127.0.0.1", "--port", str(port)],
            cwd=REPO_ROOT,
            env=env,
            stdout=out,
            stderr=err,
        )

    try:
        deadline = time.time() + 60
        while time.time() < deadline:
            if sidecar.poll() is not None:
                print(f"[launcher] sidecar exited early with code {sidecar.returncode}", file=sys.stderr)
                return 1
            handshake = read_handshake()
            if handshake:
                print(f"[launcher] sidecar ready: port={handshake['port']} token={handshake['token'][:8]}...")
                print(f"[launcher] handshake file: {handshake_path()}")
                if not args.no_open:
                    import webbrowser
                    webbrowser.open(f"http://127.0.0.1:{port}")
                return 0
            time.sleep(0.5)
        print("[launcher] timed out waiting for sidecar handshake", file=sys.stderr)
        sidecar.terminate()
        return 1
    finally:
        # Leave the sidecar running; the caller owns its lifecycle. On Windows a
        # child spawned via Popen continues after this launcher exits.
        pass


if __name__ == "__main__":
    raise SystemExit(main())
