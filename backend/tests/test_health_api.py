"""Health endpoint tests."""

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, get_db


def _client_with_db(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "health-test-token"
    client = TestClient(
        app,
        headers={
            "Origin": "tauri://localhost",
            "X-Control-Token": "health-test-token",
        },
    )
    return client, previous_token


def test_health_reports_selected_model(monkeypatch):
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    from backend.adapters import ollama_adapter
    monkeypatch.setattr(ollama_adapter, "get_adapter", lambda: None)

    import core.config
    monkeypatch.setattr(core.config, "resolve_selected_model", lambda: "minimax-m3:cloud")

    import backend.api.routes as routes
    monkeypatch.setattr(routes, "get_adapter", lambda: None)

    client, previous_token = _client_with_db(session)
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["selected_model"] == "minimax-m3:cloud"
        assert body["cloud_configured"] is False
    finally:
        import backend.main as backend_main
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        session.close()
