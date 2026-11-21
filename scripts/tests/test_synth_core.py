"""Hermetic tests for the headless Core launcher (scripts/synth_core.py).

uvicorn.run is mocked, so no server starts and no network is used. All state
lives under a per-test tmp_path profile and the relevant env vars are restored
after every test.
"""

import os
import stat

import pytest

from scripts import synth_core
from backend.core.handshake import read_launch_token, token_file_path
from backend.core.profile_lock import ProfileLock
from backend.core.storage import DATA_DIR_ENV


@pytest.fixture(autouse=True)
def _isolate_launch_env(monkeypatch):
    """Snapshot/restore every env var the launcher may write."""
    for name in synth_core.MANAGED_ENV_VARS:
        if name in os.environ:
            monkeypatch.setenv(name, os.environ[name])  # register original
        else:
            monkeypatch.delenv(name, raising=False)  # register absence
    yield


def _fake_uvicorn(monkeypatch):
    """Install a no-op uvicorn.run and return the dict it records into."""
    calls = {}

    def fake_run(app, **kwargs):
        calls["app"] = app
        calls["kwargs"] = kwargs

    monkeypatch.setattr(synth_core.uvicorn, "run", fake_run)
    return calls


def _assert_user_only_file(path):
    """Assert the token file has user-only permissions, per the helper's ACL."""
    assert os.path.isfile(path)
    if os.name == "nt":
        win32api = pytest.importorskip("win32api")
        win32security = pytest.importorskip("win32security")
        sec = win32security.GetFileSecurity(
            path, win32security.DACL_SECURITY_INFORMATION
        )
        dacl = sec.GetSecurityDescriptorDacl()
        assert dacl is not None
        user_sid, _, _ = win32security.LookupAccountName("", win32api.GetUserName())
        sids = [dacl.GetAce(i)[2] for i in range(dacl.GetAceCount())]
        assert user_sid in sids
    else:  # pragma: no cover - POSIX parity with handshake._write_private
        assert stat.S_IMODE(os.stat(path).st_mode) & 0o777 == 0o600


def test_main_sets_env_binds_loopback_and_writes_private_token(
    tmp_path, monkeypatch
):
    calls = _fake_uvicorn(monkeypatch)

    rc = synth_core.main(
        ["--data-dir", str(tmp_path), "--port", "8123", "--no-browser"]
    )

    assert rc == 0
    assert os.environ[DATA_DIR_ENV] == os.path.normpath(
        os.path.abspath(str(tmp_path))
    )
    assert calls["app"] == "backend.main:app"
    assert calls["kwargs"]["host"] == "127.0.0.1"
    assert calls["kwargs"]["port"] == 8123
    assert "0.0.0.0" not in repr(calls["kwargs"])

    token = read_launch_token()
    assert token and len(token) >= 32
    _assert_user_only_file(token_file_path())


def test_stdout_never_contains_token_value(tmp_path, monkeypatch, capsys):
    _fake_uvicorn(monkeypatch)

    rc = synth_core.main(
        ["--data-dir", str(tmp_path), "--port", "8001", "--no-browser"]
    )

    assert rc == 0
    token = read_launch_token()
    assert token
    out = capsys.readouterr().out
    assert token not in out
    # The token is discoverable by FILE path only, never by value.
    assert token_file_path() in out


def test_second_concurrent_launch_refused_via_profile_lock(
    tmp_path, monkeypatch, capsys
):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path))
    holder = ProfileLock()
    assert holder.acquire() is True
    try:
        calls = _fake_uvicorn(monkeypatch)
        rc = synth_core.main(
            ["--data-dir", str(tmp_path), "--port", "8002", "--no-browser"]
        )
    finally:
        holder.release()

    assert rc != 0
    captured = capsys.readouterr()
    assert "lock" in captured.err.lower()
    assert "second" in captured.err.lower() or "concurrent" in captured.err.lower()
    # uvicorn must never be started for the refused second instance.
    assert calls == {}


def test_no_browser_suppresses_browser_launch(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(synth_core.webbrowser, "open", opened.append)
    _fake_uvicorn(monkeypatch)

    rc = synth_core.main(
        ["--data-dir", str(tmp_path), "--port", "8003", "--no-browser"]
    )

    assert rc == 0
    assert opened == []


def test_browser_opens_by_default_on_loopback_url(tmp_path, monkeypatch):
    opened = []
    monkeypatch.setattr(synth_core.webbrowser, "open", opened.append)
    _fake_uvicorn(monkeypatch)

    rc = synth_core.main(["--data-dir", str(tmp_path), "--port", "8004"])

    assert rc == 0
    assert opened == ["http://127.0.0.1:8004"]
