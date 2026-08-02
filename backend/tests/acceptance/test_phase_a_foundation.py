"""
Acceptance gate: Phase A foundation (packaging spike, Rust-free slice).

Covers the sidecar-side contracts the Tauri shell will rely on:
- Per-launch token handshake file (written by launcher, read by sidecar).
- Sidecar handshake exposing ephemeral port + token.
- Profile single-instance lock with stale recovery.
- User-only ACLs on the private data directory.
- Sidecar version endpoint.
"""

import os
import sys
import tempfile

import pytest

from backend.core.handshake import (
    write_launch_token,
    read_launch_token,
    write_handshake,
    read_handshake,
    token_file_path,
)
from backend.core.profile_lock import ProfileLock
from backend.core.storage import data_dir, _restrict_acl_windows


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    """Redirect JARVIS_DATA_DIR to a temp dir for the test."""
    monkeypatch.setenv("JARVIS_DATA_DIR", str(tmp_path))
    return str(tmp_path)


def test_launch_token_roundtrip(isolated_data_dir):
    token = write_launch_token("per-launch-secret")
    assert read_launch_token() == "per-launch-secret"
    path = token_file_path()
    assert os.path.exists(path)
    # Token must be discoverable only via the private file, not env/cmdline.
    assert "per-launch-secret" not in " ".join(sys.argv)


def test_launch_token_generates_random_when_unspecified(isolated_data_dir):
    t1 = write_launch_token()
    t2 = write_launch_token()
    assert t1 and t2 and t1 != t2
    assert len(t1) >= 32


def test_launch_token_missing_returns_none(isolated_data_dir):
    assert read_launch_token() is None


def test_handshake_roundtrip(isolated_data_dir):
    write_handshake(port=54321, token="sidecar-token")
    payload = read_handshake()
    assert payload == {"port": 54321, "token": "sidecar-token", "version": 1}


def test_handshake_rejects_malformed(isolated_data_dir):
    path = os.path.join(data_dir(), "sidecar.json")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not json")
    assert read_handshake() is None
    with open(path, "w", encoding="utf-8") as fh:
        fh.write('{"port": "oops"}')
    assert read_handshake() is None


def test_profile_lock_excludes_second_instance(isolated_data_dir):
    lock1 = ProfileLock()
    assert lock1.acquire() is True
    lock2 = ProfileLock()
    assert lock2.acquire() is False
    lock2.release()
    lock1.release()
    # After release, a new lock is obtainable.
    lock3 = ProfileLock()
    assert lock3.acquire() is True
    lock3.release()


def test_profile_lock_stale_recovery(isolated_data_dir):
    """A lock whose owner PID is gone is reclaimed."""
    path = os.path.join(data_dir(), "profile.lock")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("999999999 1234567890")  # dead pid, old timestamp
    lock = ProfileLock()
    assert lock.acquire() is True
    lock.release()


@pytest.mark.skipif(sys.platform != "win32", reason="Windows ACL test")
def test_data_dir_applies_user_only_acl(isolated_data_dir):
    import win32api
    import win32security

    directory = data_dir()
    assert os.path.isdir(directory)

    dacl_sec = win32security.GetFileSecurity(
        directory, win32security.DACL_SECURITY_INFORMATION
    )
    dacl = dacl_sec.GetSecurityDescriptorDacl()
    user_sid, _, _ = win32security.LookupAccountName("", win32api.GetUserName())

    assert dacl is not None
    assert dacl.GetAceCount() >= 1
    aces = [dacl.GetAce(i) for i in range(dacl.GetAceCount())]
    # GetAce returns ((ace_type, flags), access_mask, sid).
    sid_list = [ace[2] for ace in aces]
    assert user_sid in sid_list


def test_version_endpoint_live(client):
    response = client.get(
        "/api/v1/version",
        headers={"X-Control-Token": "acceptance-control-token"},
    )
    assert response.status_code == 200
    body = response.json()
    assert body["version"]
    assert body["api_version"] == "v1"
