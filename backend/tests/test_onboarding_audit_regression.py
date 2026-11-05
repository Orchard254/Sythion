import asyncio

import httpx
import pytest

from backend.api.routes import _validate_ollama_cloud_key


def test_validate_key_accepts_uppercase_configured_host(monkeypatch):
    original_client = httpx.AsyncClient

    def handler(request):
        assert request.url.path == "/api/usage"
        assert request.headers["Authorization"] == "Bearer test-key"
        return httpx.Response(200)

    def client(**kwargs):
        assert kwargs["follow_redirects"] is False
        return original_client(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)
    asyncio.run(_validate_ollama_cloud_key("test-key", "HTTPS://Ollama.COM/"))


def test_credential_validation_reports_network_failure_detail(monkeypatch):
    import backend.core.secrets as secrets
    from backend.core import secrets as secret_mod
    from fastapi import HTTPException
    from backend.core.secrets import set_secret_store_for_testing, MemoryStore, OLLAMA_API_KEY
    from backend.main import app
    from backend.models.database import Base, get_db
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.runtime import model_provider
    import backend.adapters.ollama_adapter as oa

    set_secret_store_for_testing(MemoryStore())

    class Provider:
        def list_models(self):
            raise RuntimeError("network timeout")

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(model_provider, "get_default_provider", lambda: Provider())
    monkeypatch.setattr(oa, "get_adapter", lambda: None)
    monkeypatch.setattr("backend.api.routes._validate_ollama_cloud_key", __import__("asyncio").coroutine(lambda c, u: None) if False else (lambda c, u: None))
    # _validate is async; replace with async function
    async def _ok(candidate, base_url):
        return None

    import backend.api.routes as routes
    monkeypatch.setattr(routes, "_validate_ollama_cloud_key", _ok)

    app.dependency_overrides[get_db] = lambda: session
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "setup-test-token"
    client = __import__("fastapi").testclient.TestClient if False else None
    from fastapi.testclient import TestClient

    tc = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "setup-test-token"})

    try:
        res = tc.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code == 503
        detail = res.json()["detail"]
        assert detail.startswith("Network failure during credential validation:")
        assert "network timeout" in detail
        assert "candidate-key" not in res.text
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)