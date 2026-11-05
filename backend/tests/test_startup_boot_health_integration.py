"""Offline regression: startup aggregate status is consumed by health surfaces."""

import backend.main as backend_main
from backend.services.system_state import run_health_checks
from backend.main import app
from backend.api.routes import get_health_db
from fastapi.testclient import TestClient


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


def test_api_health_exposes_startup_boot_aggregate(monkeypatch):
    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()
    # provide an initialized database like test_health_api does
    import backend.models.database as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db

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
    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()
    try:
        # no db session mirrors offline context; startup check should still run
        report = run_health_checks(db_session=None)
        check = next(c for c in report["checks"] if c["name"] == "startup_sequence")
        assert check["status"] == "DEGRADED"
        assert check["evidence"]["startup_sequence_status"] == "failed"
    finally:
        backend_main.startup_boot = previous


def test_run_health_checks_reports_startup_completed(monkeypatch):
    previous = backend_main.startup_boot
    backend_main.startup_boot = _ok_boot()
    try:
        report = run_health_checks(db_session=None)
        check = next(c for c in report["checks"] if c["name"] == "startup_sequence")
        assert check["status"] == "HEALTHY"
    finally:
        backend_main.startup_boot = previous


def test_lifespan_captures_startup_boot_result(monkeypatch, tmp_path):
    import backend.core.config as core_config
    monkeypatch.setattr(core_config.settings, "database_path", str(tmp_path / "lifespan.db"))

    # keep the control token predictable across startup
    monkeypatch.setattr("backend.main.generate_control_token", lambda: "lifespan-integration-token")
    import backend.core.handshake as hs
    monkeypatch.setattr(hs, "read_launch_token", lambda: None)

    import backend.services.bootstrap as bs

    def fake_run(db_manager):
        return _failed_boot()

    monkeypatch.setattr(bs, "run_startup_sequence", fake_run)

    previous = backend_main.startup_boot
    prev_token = backend_main.control_token
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
        backend_main.control_token = prev_token
        backend_main.startup_boot = previous


def test_api_health_status_is_degraded_for_failed_startup(monkeypatch):
    previous = backend_main.startup_boot
    backend_main.startup_boot = _failed_boot()

    import backend.models.database as dbmod
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db

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