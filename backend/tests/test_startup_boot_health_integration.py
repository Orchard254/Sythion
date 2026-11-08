"""Offline regression: startup aggregate status is consumed by health surfaces."""

import logging
import os
from pathlib import Path

import pytest


def _failed_boot():
    return {
        "order": ["task_recovery", "goal_migration", "agent_init", "wake_recovery"],
        "stage_statuses": {"task_recovery": "failed"},
        "stages": [{"stage": "task_recovery", "status": "failed", "error": "injected"}],
        "startup_sequence_status": "failed",
        "status": "failed",
        "overall_status": "failed",
        "aggregate_status": "failed",
        "recovery": [],
    }


def _ok_boot():
    return {
        "order": ["task_recovery", "goal_migration", "agent_init", "wake_recovery"],
        "stage_statuses": {
            "task_recovery": "completed",
            "goal_migration": "completed",
            "agent_init": "completed",
            "wake_recovery": "completed",
        },
        "stages": [],
        "startup_sequence_status": "completed",
        "status": "completed",
        "overall_status": "completed",
        "aggregate_status": "completed",
        "recovery": [],
    }


@pytest.fixture(scope="module", autouse=True)
def _isolated_runtime(tmp_path_factory):
    """Establish a disposable runtime profile before any backend import.

    Fixture-local limitation: settings singleton rebinding and cross-module
    collection order are not provable from one file and require clean-collector
    or child-process checks.
    """
    root = tmp_path_factory.mktemp("isolated-startup")
    profile = root / "profile"
    logs = root / "logs"
    profile.mkdir()
    logs.mkdir()

    previous = {}
    roots_before = list(logging.getLogger().handlers)
    for key, value in {
        "SYTHION_DATA_DIR": str(profile),
        "SYTHION_LOG_DIR": str(logs),
        "SYTHION_SECRET_BACKEND": "memory",
        "SYTHION_PROTOTYPE_BUILD": "1",
    }.items():
        previous[key] = os.environ.get(key)
        os.environ[key] = value
    for key in ("Sythion (Synth)_DATA_DIR", "SYTHION_HANDSHAKE_TOKEN_FILE"):
        previous[key] = os.environ.pop(key, None)

    try:
        yield root, profile, logs
    finally:
        for key, value in previous.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value
        for handler in list(logging.getLogger().handlers):
            if handler not in roots_before and getattr(handler, "_sythion_handler", False):
                try:
                    handler.close()
                except Exception:
                    pass
                try:
                    logging.getLogger().removeHandler(handler)
                except Exception:
                    pass


def test_api_health_exposes_startup_boot_aggregate(monkeypatch):
    import backend.main as backend_main
    from backend.api.routes import get_health_db
    from backend.main import app
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db

    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    def override_get_db():
        yield session

    app.dependency_overrides[get_health_db] = override_get_db
    backend_main.control_token = "startup-integration-token"
    client = TestClient(
        app,
        headers={"Origin": "tauri://localhost",
                 "X-Control-Token": "startup-integration-token"},
    )
    try:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["startup_sequence_status"] == "failed"
        assert body["startup_status"] == "failed"
        assert body["startup_aggregate_status"] == "failed"
        assert body["startup_boot"]["startup_sequence_status"] == "failed"
    finally:
        app.dependency_overrides.clear()
        session.close()
        backend_main.startup_boot = previous
        backend_main.control_token = None


def test_top_level_health_exposes_startup_boot_aggregate(monkeypatch):
    import backend.main as backend_main
    from backend.main import app
    from fastapi.testclient import TestClient

    previous = backend_main.startup_boot
    backend_main.startup_boot = _ok_boot()
    try:
        import backend.main as _m

        prev_token = _m.control_token
        _m.control_token = "startup-integration-token-2"
        client = TestClient(
            app,
            headers={"Origin": "tauri://localhost",
                     "X-Control-Token": "startup-integration-token-2"},
        )
        try:
            resp = client.get("/health")
            assert resp.status_code == 200
            body = resp.json()
            assert body["startup_sequence_status"] == "completed"
            assert body["startup_aggregate_status"] == "completed"
        finally:
            _m.control_token = prev_token
    finally:
        backend_main.startup_boot = previous


def test_run_health_checks_reports_startup_failure(monkeypatch):
    import backend.main as backend_main
    from backend.services.system_state import run_health_checks

    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()
    try:
        report = run_health_checks(db_session=None)
        check = next(c for c in report["checks"] if c["name"] == "startup_sequence")
        assert check["status"] == "DEGRADED"
        assert check["evidence"]["startup_sequence_status"] == "failed"
    finally:
        backend_main.startup_boot = previous


def test_run_health_checks_reports_startup_completed(monkeypatch):
    import backend.main as backend_main
    from backend.services.system_state import run_health_checks

    previous = backend_main.startup_boot
    backend_main.startup_boot = _ok_boot()
    try:
        report = run_health_checks(db_session=None)
        check = next(c for c in report["checks"] if c["name"] == "startup_sequence")
        assert check["status"] == "HEALTHY"
    finally:
        backend_main.startup_boot = previous


_MISSING = object()


def test_lifespan_captures_startup_boot_result(monkeypatch, tmp_path):
    import logging
    from pathlib import Path

    import backend.core.config as core_config
    import backend.core.storage as storage

    isolated_profile = tmp_path / "profile"
    isolated_logs = tmp_path / "logs"
    isolated_profile.mkdir()
    isolated_logs.mkdir()

    monkeypatch.setenv("SYTHION_DATA_DIR", str(isolated_profile))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    monkeypatch.setenv("SYTHION_LOG_DIR", str(isolated_logs))
    monkeypatch.delenv("SYTHION_HANDSHAKE_TOKEN_FILE", raising=False)
    monkeypatch.setenv("SYTHION_SECRET_BACKEND", "memory")
    monkeypatch.setattr(core_config.settings, "database_path",
                       str(tmp_path / "lifespan.db"))

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", lambda: False)

    assert Path(storage.data_dir()).resolve() == isolated_profile.resolve()
    assert Path(storage.lock_path()).resolve().parent == isolated_profile.resolve()
    assert Path(storage.handshake_path()).resolve().parent == isolated_profile.resolve()

    # Snapshot identities before lifespan mutation.
    import backend.adapters.ollama_adapter as ollama_adapter_mod
    import backend.main as backend_main
    import backend.models.database as dbmod
    import backend.runtime.model_provider as model_provider_mod
    import backend.services.wake_worker as wake_worker_mod

    prev_db_manager = backend_main.db_manager
    prev_tool_executor = backend_main.tool_executor
    prev_control_token = backend_main.control_token
    prev_profile_lock = backend_main.profile_lock
    prev_startup_boot = backend_main.startup_boot
    prev_startup_sequence_status = backend_main.startup_sequence_status
    prev_model_provider = getattr(model_provider_mod, "_default_provider", None)
    prev_adapter_provider = getattr(ollama_adapter_mod, "_provider", None)
    prev_adapter = getattr(ollama_adapter_mod, "_adapter", None)
    prev_wake_worker = wake_worker_mod.get_wake_worker()
    app = backend_main.app
    app_state = app.state
    prev_app_startup_boot = getattr(app_state, "startup_boot", _MISSING)
    prev_app_startup_ss = getattr(app_state, "startup_sequence_status", _MISSING)
    import backend.api.dependencies as api_dependencies
    prev_provider_attr = getattr(api_dependencies, "_provider", None)

    monkeypatch.setattr("backend.main.generate_control_token",
                       lambda: "lifespan-integration-token")

    import backend.core.handshake as hs
    monkeypatch.setattr(hs, "read_launch_token", lambda: None)

    import backend.services.bootstrap as bs

    def fake_run(db_manager):
        return _failed_boot()

    monkeypatch.setattr(bs, "run_startup_sequence", fake_run)

    prev_settings_port = core_config.settings.port
    prev_settings_database_path = core_config.settings.database_path

    monkeypatch.setattr(backend_main, "migrate_legacy_data_dir", lambda: False, raising=False)

    from fastapi.testclient import TestClient

    backend_main.control_token = "lifespan-integration-token"
    try:
        with TestClient(backend_main.app) as client:
            assert isinstance(backend_main.startup_boot, dict)
            assert backend_main.startup_boot["startup_sequence_status"] == "failed"
            assert backend_main.startup_sequence_status == "failed"
            resp = client.get(
                "/api/v1/health",
                headers={"Origin": "tauri://localhost",
                         "X-Control-Token": "lifespan-integration-token"},
            )
            assert resp.status_code == 200
            assert resp.json()["startup_sequence_status"] == "failed"
    finally:
        # Release only a fixture-owned newly acquired lock.
        current_lock = backend_main.profile_lock
        if current_lock is not prev_profile_lock and getattr(current_lock, "_fd", None) is not None:
            try:
                current_lock.release()
            except Exception:
                pass
        backend_main.db_manager = prev_db_manager
        dbmod.set_db_manager(prev_db_manager)
        backend_main.tool_executor = prev_tool_executor
        backend_main.control_token = prev_control_token
        backend_main.startup_boot = prev_startup_boot
        backend_main.startup_sequence_status = prev_startup_sequence_status
        backend_main.profile_lock = prev_profile_lock
        model_provider_mod._default_provider = prev_model_provider
        ollama_adapter_mod._provider = prev_adapter_provider
        ollama_adapter_mod._adapter = prev_adapter
        wake_worker_mod.set_wake_worker(prev_wake_worker)
        api_dependencies._provider = prev_provider_attr
        core_config.settings.port = prev_settings_port
        core_config.settings.database_path = prev_settings_database_path
        if prev_app_startup_boot is _MISSING:
            try:
                delattr(app_state, "startup_boot")
            except Exception:
                pass
        else:
            app_state.startup_boot = prev_app_startup_boot
        if prev_app_startup_ss is _MISSING:
            try:
                delattr(app_state, "startup_sequence_status")
            except Exception:
                pass
        else:
            app_state.startup_sequence_status = prev_app_startup_ss
        # Ensure temporary root handlers are cleaned by module fixture.


def test_api_health_status_is_degraded_for_failed_startup(monkeypatch):
    import backend.main as backend_main
    from backend.api.routes import get_health_db
    from backend.main import app
    from fastapi.testclient import TestClient
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db

    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    def override_get_db():
        yield session

    app.dependency_overrides[get_health_db] = override_get_db
    backend_main.control_token = "startup-status-token"
    client = TestClient(
        app,
        headers={"Origin": "tauri://localhost",
                 "X-Control-Token": "startup-status-token"},
    )
    try:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["startup_sequence_status"] == "failed"
    finally:
        app.dependency_overrides.clear()
        session.close()
        backend_main.startup_boot = previous
        backend_main.control_token = None