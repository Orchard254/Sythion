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

    import backend.core.config as core_config
    monkeypatch.setattr(core_config, "resolve_selected_model", lambda: "minimax-m3:cloud")

    # Phase 5: /health reads provider health through the neutral facade
    # (function-local import resolves against this module at call time).
    import backend.runtime.model_provider as model_provider_mod
    from backend.runtime.model_provider import ModelHealth

    class _HealthyProvider:
        def provider_id(self):
            return "test"

        def selected_model_id(self):
            return "minimax-m3:cloud"

        def list_models(self):
            return []

        def health(self):
            return ModelHealth("test", ModelHealth.HEALTHY)

        async def chat(self, messages, max_tokens=4000, timeout=300.0):
            return {}

    import backend.api.routes as routes  # noqa: F401 (route under test)
    monkeypatch.setattr(model_provider_mod, "get_default_provider",
                        lambda: _HealthyProvider())

    client, previous_token = _client_with_db(session)
    try:
        response = client.get("/api/v1/health")
        assert response.status_code == 200
        body = response.json()
        assert body["status"] == "healthy"
        assert body["selected_model"] == "minimax-m3:cloud"
        # PHASE 5: cloud_configured now reflects provider HEALTH via the
        # neutral facade (the stubbed provider reports HEALTHY).
        assert body["cloud_configured"] is True
    finally:
        import backend.main as backend_main
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        session.close()
