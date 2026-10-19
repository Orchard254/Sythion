"""Regression tests for the authenticated local control boundary."""

from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.core.auth import (
    DEVELOPMENT_APP_ORIGINS,
    LocalControlBoundaryMiddleware,
    PACKAGED_APP_ORIGINS,
    control_origins,
)


def create_test_client() -> TestClient:
    app = FastAPI()
    app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: "test-control-token",
    )

    @app.get("/api/v1/ping")
    async def ping():
        return {"ok": True}

    return TestClient(app)


def test_control_route_rejects_missing_token():
    client = create_test_client()

    response = client.get("/api/v1/ping", headers={"Origin": "tauri://localhost"})

    assert response.status_code == 401


def test_control_route_rejects_untrusted_origin():
    client = create_test_client()

    response = client.get(
        "/api/v1/ping",
        headers={
            "Origin": "http://localhost:3000",
            "X-Control-Token": "test-control-token",
        },
    )

    assert response.status_code == 403


def test_control_route_accepts_token_without_origin():
    client = create_test_client()

    response = client.get("/api/v1/ping", headers={"X-Control-Token": "test-control-token"})

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_control_route_accepts_packaged_origin_and_valid_token():
    client = create_test_client()

    response = client.get(
        "/api/v1/ping",
        headers={
            "Origin": "tauri://localhost",
            "X-Control-Token": "test-control-token",
        },
    )

    assert response.status_code == 200
    assert response.json() == {"ok": True}


def test_development_origins_are_opt_in_only():
    assert "http://localhost:3000" not in control_origins(False)
    assert control_origins(False) == PACKAGED_APP_ORIGINS
    assert DEVELOPMENT_APP_ORIGINS <= control_origins(True)
