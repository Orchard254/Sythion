"""
Sidecar startup handshake (Phase A contract).

The packaged Tauri shell and the Python sidecar coordinate exactly once per
launch through a private, user-ACL-protected file in the Sythion (Synth) data dir:

1. Launcher creates the data dir and writes ``token.json`` (fresh random token,
   user-only ACLs) *before* spawning the sidecar.
2. Launcher starts the sidecar with ``SYTHION_HANDSHAKE_TOKEN_FILE`` pointing at
   that file.
3. Sidecar reads the token, binds 127.0.0.1 on an ephemeral port, then writes
   ``sidecar.json`` with ``{ "port": N, "token": "..." }``.
4. Launcher reads ``sidecar.json`` and talks to ``127.0.0.1:port`` with the token.

The token is never passed on a command line (visible in process listings).
"""

import json
import os
import secrets
import tempfile
from typing import Dict, Optional

from .storage import data_dir, handshake_path

TOKEN_FILE_NAME = "token.json"
DEVICE_CHANNEL_FILE_NAME = "device_channel.json"
DEVICE_CHANNEL_VERSION = 1


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
    data_dir()
    path = token_file_path()
    # Atomic private write; ACL is applied to the temp file before replace.
    _write_private(path, json.dumps({"token": token, "version": 1}))
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
    path = handshake_path()
    # Atomic private write; ACL is applied to the temp file before replace.
    _write_private(path, json.dumps(payload))
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


def device_channel_config_path() -> str:
    """Return the shell-written device-channel config path in the profile dir."""
    return os.path.join(data_dir(), DEVICE_CHANNEL_FILE_NAME)


def default_device_channel_config() -> Dict[str, object]:
    """Current behavior when the shell has not written a config yet."""
    return {
        "version": DEVICE_CHANNEL_VERSION,
        "enabled": True,
        "relay_enabled": True,
        "ws_enabled": True,
    }


def read_device_channel_config() -> Dict[str, object]:
    """Read and validate the shell's private device-channel config.

    Missing file -> current default (enabled) for backward compatibility.
    Corrupt or invalid file -> fail closed (all channels disabled). This config
    can only disable channels; it never widens the loopback boundary.
    """
    path = device_channel_config_path()
    if not os.path.exists(path):
        return default_device_channel_config()
    try:
        with open(path, "r", encoding="utf-8") as fh:
            payload = json.load(fh)
    except (OSError, ValueError):
        return _disabled_device_channel_config()
    if not isinstance(payload, dict):
        return _disabled_device_channel_config()
    expected = {"version", "enabled", "relay_enabled", "ws_enabled"}
    if set(payload) != expected or payload.get("version") != DEVICE_CHANNEL_VERSION:
        return _disabled_device_channel_config()
    if not all(isinstance(payload[key], bool) for key in
               ("enabled", "relay_enabled", "ws_enabled")):
        return _disabled_device_channel_config()
    return dict(payload)


def _disabled_device_channel_config() -> Dict[str, object]:
    return {
        "version": DEVICE_CHANNEL_VERSION,
        "enabled": False,
        "relay_enabled": False,
        "ws_enabled": False,
    }


def write_device_channel_config(
    enabled: bool, relay_enabled: bool, ws_enabled: bool,
) -> Dict[str, object]:
    """Persist a validated config. Owner/shell only; never called by a device."""
    for name, value in (
        ("enabled", enabled), ("relay_enabled", relay_enabled),
        ("ws_enabled", ws_enabled),
    ):
        if not isinstance(value, bool):
            raise ValueError(f"{name} must be a boolean")
    payload = {
        "version": DEVICE_CHANNEL_VERSION,
        "enabled": enabled,
        "relay_enabled": relay_enabled,
        "ws_enabled": ws_enabled,
    }
    data_dir()
    _write_private(device_channel_config_path(), json.dumps(payload))
    return payload


def _write_private(path: str, content: str) -> None:
    """Write ``content`` atomically via a unique, unpredictable temp file.

    Cleans up the temp file on failure and applies private permissions on
    non-Windows systems; leaves no stale predictable ``.tmp`` behind.
    """
    path = str(path)
    directory = os.path.dirname(os.path.abspath(path)) or os.getcwd()
    os.makedirs(directory, exist_ok=True)

    legacy_tmp = path + ".tmp"
    try:
        if os.path.lexists(legacy_tmp):
            os.remove(legacy_tmp)
    except OSError:
        pass

    fd, tmp = tempfile.mkstemp(prefix=".sythion-", suffix=".tmp", dir=directory)
    try:
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(content)
                fh.flush()
                try:
                    os.fsync(fh.fileno())
                except (OSError, AttributeError):
                    pass
            if os.name != "nt":
                try:
                    os.chmod(tmp, 0o600)
                except OSError:
                    pass
            else:
                # Apply user-only ACL to the temp file BEFORE atomic replace.
                from .storage import _restrict_acl_windows

                _restrict_acl_windows(tmp)
            os.replace(tmp, path)
        except Exception:
            if os.path.lexists(tmp):
                try:
                    os.remove(tmp)
                except OSError:
                    pass
            raise
    finally:
        if os.path.lexists(tmp):
            try:
                os.remove(tmp)
            except OSError:
                pass
