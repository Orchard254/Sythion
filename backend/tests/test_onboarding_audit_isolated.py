import asyncio

from fastapi import HTTPException

from backend.core import secrets
from backend.core.secrets import set_secret_store_for_testing, MemoryStore


def _make_client(monkeypatch, adapter, provider=None):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db
    from backend.main import app
    import backend.adapters.ollama_adapter as oa
    import backend.runtime.model_provider as mp
    import backend.api.routes as routes
    import backend.main as backend_main

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    monkeypatch.setattr(oa, "get_adapter", lambda: adapter)
    def _candidate(*a, **k):
        return AsyncProbeAdapter(models=adapter.models, fail=adapter.fail, cancel=adapter.cancel)
    monkeypatch.setattr(oa, "OllamaCloudAdapter", _candidate)
    if provider is not None:
        monkeypatch.setattr(mp, "get_default_provider", lambda: provider)
    async def _ok(candidate, base_url):
        return None
    monkeypatch.setattr(routes, "_validate_ollama_cloud_key", _ok)

    app.dependency_overrides[get_db] = lambda: session
    previous_token = backend_main.control_token
    backend_main.control_token = "isolated-test-token"
    from fastapi.testclient import TestClient
    tc = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "isolated-test-token"})
    return tc, previous_token, backend_main


class AsyncProbeAdapter:
    def __init__(self, models=None, fail=None, cancel=False):
        self.api_key = "old-key"
        self.models = models if models is not None else [{"name": "m1"}]
        self.fail = fail
        self.cancel = cancel
        self.inflight_keys = []

    async def list_models(self):
        self.inflight_keys.append(self.api_key)
        await asyncio.sleep(0.01)
        if self.cancel:
            raise asyncio.CancelledError()
        if self.fail:
            raise self.fail
        return self.models


def test_isolated_validation_failure_does_not_mutate_shared_adapter(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    adapter = AsyncProbeAdapter(fail=RuntimeError("network timeout"))
    tc, previous_token, backend_main = _make_client(monkeypatch, adapter)
    import backend.main as bm
    from backend.core.secrets import OLLAMA_API_KEY
    try:
        res = tc.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code == 503
        assert adapter.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        bm.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_isolated_validation_cancellation_preserves_secret_and_shared_key(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    adapter = AsyncProbeAdapter(cancel=True)
    tc, previous_token, backend_main = _make_client(monkeypatch, adapter)
    import backend.main as bm
    from backend.main import app
    from fastapi.testclient import TestClient
    # TestClient raises server exceptions by default; use raise_server_exceptions=False
    tc2 = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "isolated-test-token"}, raise_server_exceptions=False)
    try:
        res = tc2.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        # Cancellation during discovery should not produce a successful validation.
        assert res.status_code >= 400
        assert adapter.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        bm.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)
        _ = tc


def test_isolated_empty_discovery_reports_503_without_mutation(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    adapter = AsyncProbeAdapter(models=[])
    tc, previous_token, backend_main = _make_client(monkeypatch, adapter)
    import backend.main as bm
    from backend.main import app
    try:
        res = tc.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code == 503
        assert "Authenticated model discovery returned no models" in res.json()["detail"]
        assert adapter.api_key == "old-key"
    finally:
        bm.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)