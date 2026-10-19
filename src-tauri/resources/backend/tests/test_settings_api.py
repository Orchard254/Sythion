"""
Settings API tests for the live file-root grant path.

GET /api/v1/settings must report the same approved roots the file tools
enforce: environment defaults merged with roots granted at runtime via
PUT /api/v1/settings (no restart required).
"""

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, get_db


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "settings-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "settings-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


@pytest.fixture
def env_roots(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "file_roots", ["C:\\EnvRoot"])


def test_get_settings_reports_env_roots(client, db_session, env_roots):
    response = client.get("/api/v1/settings")
    assert response.status_code == 200
    assert "C:\\EnvRoot" in response.json()["file_roots"]


def test_granted_root_appears_immediately(client, db_session, env_roots):
    grant = client.put("/api/v1/settings", json={"file_roots": ["C:\\GrantedRoot"]})
    assert grant.status_code == 200

    response = client.get("/api/v1/settings")
    roots = response.json()["file_roots"]
    assert "C:\\EnvRoot" in roots
    assert "C:\\GrantedRoot" in roots


def test_revoked_root_disappears_immediately(client, db_session, env_roots):
    client.put("/api/v1/settings", json={"file_roots": ["C:\\GrantedRoot"]})
    client.put("/api/v1/settings", json={"file_roots": []})

    response = client.get("/api/v1/settings")
    assert "C:\\GrantedRoot" not in response.json()["file_roots"]


def test_invalid_root_spec_rejected(client, db_session, env_roots):
    response = client.put("/api/v1/settings", json={"file_roots": 42})
    assert response.status_code == 400
