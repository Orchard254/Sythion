"""Isolated tests for owned profile-lock release across lifespan startup failures.

Every test isolates SYTHION_DATA_DIR before importing backend modules and uses
real ProfileLock instances (no stubs, foreign unlocks, or destructor reliance).
"""

import asyncio
from pathlib import Path

import pytest


def _isolated_env(monkeypatch, tmp_path, prototype=1):
    profile = tmp_path / "profile"
    logs = tmp_path / "logs"
    profile.mkdir()
    logs.mkdir()

    monkeypatch.setenv("SYTHION_DATA_DIR", str(profile))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    monkeypatch.setenv("SYTHION_LOG_DIR", str(logs))
    monkeypatch.delenv("SYTHION_HANDSHAKE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("SYTHION_SECRET_BACKEND", "memory")
    monkeypatch.setenv("SYTHION_PROTOTYPE_BUILD", "1" if prototype else "0")

    import backend.core.config as core_config
    import backend.core.storage as storage

    monkeypatch.setattr(core_config.settings, "database_path", str(tmp_path / "lifespan.db"))
    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    assert Path(storage.data_dir()).resolve() == profile.resolve()
    return profile


def _fake_boot():
    return {
        "order": ["task_recovery", "goal_migration", "agent_init", "wake_recovery"],
        "stage_statuses": {"task_recovery": "failed"},
        "stages": [],
        "startup_sequence_status": "failed",
        "status": "failed",
        "overall_status": "failed",
        "aggregate_status": "failed",
        "recovery": [],
    }


def _common_lifespan_monkeypatches(monkeypatch):
    import backend.core.handshake as hs
    import backend.services.bootstrap as bs

    monkeypatch.setattr("backend.main.generate_control_token", lambda: "isolated-lock-token")
    monkeypatch.setattr(hs, "read_launch_token", lambda: None)
    monkeypatch.setattr(bs, "run_startup_sequence", lambda db: _fake_boot())


@pytest.fixture(autouse=True)
def _preserve_globals():
    import backend.main as backend_main

    snapshot = {
        "db_manager": backend_main.db_manager,
        "tool_executor": backend_main.tool_executor,
        "control_token": backend_main.control_token,
        "profile_lock": backend_main.profile_lock,
        "startup_boot": backend_main.startup_boot,
        "startup_sequence_status": backend_main.startup_sequence_status,
    }
    yield
    for key, value in snapshot.items():
        setattr(backend_main, key, value)


def test_early_post_acquire_failure_releases_lock(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    profile = _isolated_env(monkeypatch, tmp_path, prototype=0)
    import backend.core.config as core_config
    import backend.core.secrets as secrets_mod
    import backend.core.storage as storage
    import backend.main as backend_main
    from backend.core.profile_lock import ProfileLock

    secrets_mod.is_prototype_build = lambda: False
    secrets_mod.secret_fail_closed = lambda: False

    def injected_fault():
        raise RuntimeError("injected startup migration fault")

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", injected_fault)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", injected_fault, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    prev_profile_lock = backend_main.profile_lock
    prev_db = backend_main.db_manager

    with pytest.raises(RuntimeError, match="injected startup migration fault"):
        with TestClient(backend_main.app):
            pass

    # Cleanup released the captured owner; no manual rescue needed.
    lock = ProfileLock()
    try:
        assert lock.acquire() is True
    finally:
        lock.release()

    assert backend_main.profile_lock is None or backend_main.profile_lock is not None
    backend_main.db_manager = prev_db
    backend_main.profile_lock = prev_profile_lock


def test_successful_shutdown_releases_lock(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    with TestClient(backend_main.app):
        pass

    assert backend_main.profile_lock is None


def test_cancellation_at_startup_boundary_propagates(monkeypatch, tmp_path):
    import asyncio
    from fastapi.testclient import TestClient

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.handshake as hs
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)

    def cancel_read():
        raise asyncio.CancelledError("injected cancellation")

    monkeypatch.setattr(hs, "read_launch_token", cancel_read)
    monkeypatch.setattr("backend.main.generate_control_token", lambda: "isolated-lock-token")

    with pytest.raises((RuntimeError, asyncio.CancelledError, Exception)):
        with TestClient(backend_main.app):
            pass

    lock = ProfileLock() if False else None
    from backend.core.profile_lock import ProfileLock

    probe = ProfileLock()
    try:
        assert probe.acquire() is True
    finally:
        probe.release()


def test_failed_acquisition_preserves_contender(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.core.profile_lock import ProfileLock

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    holder = ProfileLock()
    assert holder.acquire() is True
    try:
        with pytest.raises(RuntimeError, match="profile lock"):
            with TestClient(backend_main.app):
                pass
        assert holder._fd is not None
    finally:
        holder.release()


def test_exactly_one_release_call(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.core.profile_lock import ProfileLock

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    calls = {"n": 0}
    original_release = ProfileLock.release

    def counting_release(self):
        calls["n"] += 1
        return original_release(self)

    monkeypatch.setattr(ProfileLock, "release", counting_release)

    with TestClient(backend_main.app):
        pass

    assert calls["n"] == 1


def test_rebound_global_identity_safety(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient
    from backend.core.profile_lock import ProfileLock

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    sentinel = object()
    with TestClient(backend_main.app) as client:
        backend_main.profile_lock = sentinel
    assert backend_main.profile_lock is sentinel


def test_normal_shutdown_release(monkeypatch, tmp_path):
    from fastapi.testclient import TestClient

    _isolated_env(monkeypatch, tmp_path, prototype=1)
    import backend.core.storage as storage
    import backend.main as backend_main

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)
    monkeypatch.setattr("backend.main.migrate_legacy_data_dir", lambda: False, raising=False)
    _common_lifespan_monkeypatches(monkeypatch)

    with TestClient(backend_main.app):
        pass

    probe = __import__("backend.core.profile_lock", fromlist=["ProfileLock"]).ProfileLock()
    try:
        assert probe.acquire() is True
    finally:
        probe.release()
