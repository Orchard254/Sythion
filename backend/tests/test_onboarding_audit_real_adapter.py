import asyncio

import httpx
import pytest

from backend.adapters.ollama_adapter import OllamaCloudAdapter
from backend.core.secrets import set_secret_store_for_testing, MemoryStore


class PublishingAdapter(OllamaCloudAdapter):
    """Real shared adapter whose publication to candidate can be made to fail."""

    def __init__(self, *, initial_key="old-key", publish_fail=False, **kwargs):
        super().__init__(**kwargs)
        self._publish_fail = publish_fail
        self._publish_calls = []
        self._allow_setattr = False
        super().__setattr__("api_key", initial_key)
        self._allow_setattr = True

    def __setattr__(self, name, value):
        if name not in ("_publish_fail", "_publish_calls", "_allow_setattr") and getattr(self, "_allow_setattr", False):
            if name == "api_key" and self._publish_fail and value != "old-key":
                self._publish_calls.append(value)
                raise RuntimeError("shared publication failed")
            self._publish_calls.append(value) if name == "api_key" else None
        super().__setattr__(name, value)

    @property
    def publish_calls(self):
        return list(self._publish_calls)


def _patch_httpx(monkeypatch, handler):
    original = httpx.AsyncClient

    def client(**kwargs):
        return original(transport=httpx.MockTransport(handler), **kwargs)

    monkeypatch.setattr(httpx, "AsyncClient", client)


def _make_real_client(monkeypatch, shared_adapter, publish_fail=False):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    from backend.models.database import Base, get_db
    from backend.main import app
    import backend.adapters.ollama_adapter as oa
    import backend.api.routes as routes
    import backend.main as backend_main

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    async def _candidate_ok(candidate, base_url):
        return None

    monkeypatch.setattr(oa, "get_adapter", lambda: shared_adapter)
    monkeypatch.setattr(routes, "_validate_ollama_cloud_key", _candidate_ok)

    app.dependency_overrides[get_db] = lambda: session
    previous_token = backend_main.control_token
    backend_main.control_token = "real-adapter-token"
    from fastapi.testclient import TestClient

    tc = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"})
    return tc, previous_token, backend_main


def test_real_adapter_successful_validation_publishes_without_network(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    shared = PublishingAdapter(initial_key="old-key", publish_fail=False)

    def handler(request):
        assert request.url.path == "/api/tags"
        return httpx.Response(200, json={"models": [{"name": "m1"}]})

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    from fastapi.testclient import TestClient
    tc = TestClient(tc.app, headers=tc.headers, raise_server_exceptions=False) if False else tc

    try:
        # ASGITransport isn't needed with TestClient; ensure real adapter is live.
        from backend.main import app
        class Dummy:
            pass
        res = tc.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code == 200
        assert shared.api_key == "candidate-key"
        assert res.json()["model_count"] == 1
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("candidate-key",)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_real_adapter_publication_failure_does_not_claim_success(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    shared = PublishingAdapter(initial_key="old-key", publish_fail=True)

    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "m1"}]})

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    try:
        tc = tc
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"}, raise_server_exceptions=False)
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code != 200
        assert "publication" in res.text.lower() or res.status_code >= 400
        assert shared.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_real_adapter_transport_cancellation_surfaces_and_preserves_shared_adapter(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    shared = PublishingAdapter(initial_key="old-key", publish_fail=False)

    import asyncio

    def handler(request):
        raise asyncio.CancelledError()

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    try:
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"}, raise_server_exceptions=False)
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code >= 400
        assert shared.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


class PartialPublicationAdapter(PublishingAdapter):
    """Fails on a shared publication field after api_key has been changed."""

    def __init__(self, *, initial_key="old-key", fail_field="_client", **kwargs):
        self._fail_field = fail_field
        self._allow_setattr = False
        super().__init__(initial_key=initial_key, publish_fail=False, **kwargs)
        self._allow_setattr = True

    def __setattr__(self, name, value):
        if getattr(self, "_allow_setattr", False) and name == getattr(self, "_fail_field", None):
            raise RuntimeError(f"shared publication failed while setting {name}")
        super().__setattr__(name, value)


@pytest.mark.parametrize("fail_field", ["_client", "_client_loop"])
def test_real_adapter_partial_publication_failure_rolls_back(monkeypatch, fail_field):
    set_secret_store_for_testing(MemoryStore())
    shared = PartialPublicationAdapter(initial_key="old-key", fail_field=fail_field)

    def handler(request):
        return httpx.Response(200, json={"models": [{"name": "m1"}]})

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    try:
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"}, raise_server_exceptions=False)
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code >= 400
        assert shared.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_real_adapter_timeout_is_classified_as_network_failure(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    shared = PublishingAdapter(initial_key="old-key", publish_fail=False)

    import httpx

    def handler(request):
        raise httpx.ConnectTimeout("connect timed out")

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    try:
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"}, raise_server_exceptions=False)
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code == 503
        assert res.json()["detail"].lower().startswith("network failure during credential validation:")
        assert "connect" in res.json()["detail"].lower() or "timeout" in res.json()["detail"].lower()
        assert shared.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)
    set_secret_store_for_testing(MemoryStore())
    shared = PublishingAdapter(initial_key="old-key", publish_fail=False)

    import asyncio

    def handler(request):
        raise asyncio.CancelledError()

    _patch_httpx(monkeypatch, handler)
    tc, previous_token, backend_main = _make_real_client(monkeypatch, shared)

    try:
        from backend.main import app
        from fastapi.testclient import TestClient
        client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "real-adapter-token"}, raise_server_exceptions=False)
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "candidate-key"})
        assert res.status_code >= 400
        assert shared.api_key == "old-key"
        from backend.core.secrets import resolve_ollama_api_key
        assert resolve_ollama_api_key() in ("", None)
    finally:
        backend_main.control_token = previous_token
        from backend.main import app
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)