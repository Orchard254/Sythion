"""
Sidecar startup handshake (Phase A contract).

The packaged Tauri shell and the Python sidecar coordinate exactly once per
launch through a private, user-ACL-protected file in the Sythion (Synth) data dir:

1. Launcher creates the data dir and writes ``token.json`` (fresh random token,
   user-only ACLs) *before* spawning the sidecar.
2. Launcher starts the sidecar with ``Sythion (Synth)_HANDSHAKE_TOKEN_FILE`` pointing at
   that file.
3. Sidecar reads the token, binds 127.0.0.1 on an ephemeral port, then writes
   ``sidecar.json`` with ``{ "port": N, "token": "..." }``.
4. Launcher reads ``sidecar.json`` and talks to ``127.0.0.1:port`` with the token.

The token is never passed on a command line (visible in process listings).
"""

import json
import os
import secrets
from typing import Dict, Optional

from .storage import data_dir, handshake_path

TOKEN_FILE_NAME = "token.json"


def token_file_path() -> str:
    """Return the launcher->sidecar token file path."""
    env_path = os.environ.get("SYTHION_HANDSHAKE_TOKEN_FILE", "")
    if env_path:
        return env_path
    return os.path.join(data_dir(), TOKEN_FILE_NAME)


def write_launch_token(token: Optional[str] = None) -> str:
    """Write the per-launch token to the private token file.

    Args:
        token: Explicit token, or a fresh cryptographically random one.

    Returns:
        The token that was written.
    """
    token = token or secrets.token_urlsafe(32)
    from .storage import _restrict_acl_windows

    data_dir()
    path = token_file_path()
    _write_private(path, json.dumps({"token": token, "version": 1}))
    if os.name == "nt":
        try:
            _restrict_acl_windows(path)
        except Exception:  # pragma: no cover - OS dependent
            pass
    return token


def read_launch_token() -> Optional[str]:
    """Read the token written by the launcher. Returns None if unavailable."""
    path = token_file_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        return payload.get("token")
    except (OSError, ValueError):
        return None


def write_handshake(port: int, token: str) -> Dict[str, object]:
    """Write the sidecar's ephemeral port + token for the launcher to read."""
    payload = {"port": port, "token": token, "version": 1}
    _write_private(handshake_path(), json.dumps(payload))
    return payload


def read_handshake() -> Optional[Dict[str, object]]:
    """Read the sidecar handshake. Returns None if unavailable/invalid."""
    path = handshake_path()
    if not os.path.exists(path):
        return None
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
        if not isinstance(payload.get("port"), int) or not payload.get("token"):
            return None
        return payload
    except (OSError, ValueError):
        return None


def _write_private(path: str, content: str) -> None:
    """Write ``content`` atomically via a temp file + rename."""
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as fh:
        fh.write(content)
    os.replace(tmp, path)
