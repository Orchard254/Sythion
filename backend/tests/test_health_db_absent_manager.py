"""Focused regression: /api/v1/health survives a missing DB manager."""

import backend.main as backend_main
from backend.main import app
from fastapi.testclient import TestClient


def test_api_health_is_degraded_when_db_manager_absent(monkeypatch):
    previous_boot = backend_main.startup_boot
    previous_manager = backend_main.db_manager
    previous_token = backend_main.control_token

    backend_main.startup_boot = {"startup_sequence_status": "unknown"}
    backend_main.db_manager = None
    backend_main.control_token = "absent-db-manager-token"

    import backend.models.database as dbmod

    previous_db_manager = dbmod._db_manager
    dbmod._db_manager = None

    client = TestClient(
        app,
        headers={"Origin": "tauri://localhost",
                "X-Control-Token": "absent-db-manager-token"},
    )
    try:
        resp = client.get("/api/v1/health")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] == "degraded"
        assert body["database_connected"] is False
    finally:
        backend_main.startup_boot = previous_boot
        backend_main.db_manager = previous_manager
        backend_main.control_token = previous_token
        dbmod._db_manager = previous_db_manager
