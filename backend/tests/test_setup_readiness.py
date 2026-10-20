import os

from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, get_db
from backend.core.secrets import set_secret_store_for_testing, MemoryStore, OLLAMA_API_KEY
from backend.runtime.model_provider import ModelHealth


class _Provider:
    def __init__(self, models=None, healthy=True, fail_models=False, inference_error=None):
        self.models = models or [{"name": "m1"}]
        self.healthy = healthy
        self.fail_models = fail_models
        self.inference_error = inference_error

    def provider_id(self):
        return "ollama-cloud"

    def selected_model_id(self):
        from backend.core.config import resolve_selected_model

        return resolve_selected_model()

    def list_models(self):
        if self.fail_models:
            raise RuntimeError("network timeout")
        return self.models

    def health(self):
        return ModelHealth("ollama-cloud", ModelHealth.HEALTHY if self.healthy else ModelHealth.UNAVAILABLE)

    async def chat(self, messages, max_tokens=4000, timeout=300.0):
        if self.inference_error:
            raise self.inference_error
        return {"choices": [{"message": {"content": "OK"}}]}


def _client(monkeypatch, provider=None, selected=None):
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()

    import backend.core.config as core_config
    monkeypatch.setattr(core_config, "resolve_selected_model", lambda: selected)
    if provider is not None:
        import backend.runtime.model_provider as m
        monkeypatch.setattr(m, "get_default_provider", lambda: provider)

    from backend.adapters import ollama_adapter
    monkeypatch.setattr(ollama_adapter, "get_adapter", lambda: None)

    app.dependency_overrides[get_db] = lambda: session
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "setup-test-token"
    client = TestClient(app, headers={"Origin": "tauri://localhost", "X-Control-Token": "setup-test-token"})
    set_secret_store_for_testing(MemoryStore())
    return client, previous_token, backend_main


def test_credential_validation_and_discovery(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    provider = _Provider(models=[{"name": "m1"}, {"name": "m2"}])
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "secret-key"})
        assert res.status_code == 200
        body = res.json()
        assert body["valid"] is True
        assert body["model_count"] == 2
        assert "secret-key" not in res.text
        assert "m1" in body["models"]
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_invalid_credentials_preserve_previous(monkeypatch):
    provider = _Provider(fail_models=True)
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    import backend.core.secrets as secrets
    secrets.set_secret(secrets.OLLAMA_API_KEY, "old-key")
    try:
        res = client.post("/api/v1/onboarding/credentials/test", json={"value": "bad-key"})
        assert res.status_code == 503
        assert "bad-key" not in res.text
        assert secrets.has_secret(secrets.OLLAMA_API_KEY)
        assert secrets.resolve_ollama_api_key() == "old-key"
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_model_id_rejection(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    provider = _Provider(models=[{"name": "m1"}])
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        res = client.post("/api/v1/onboarding/models", json={"model_id": "not-allowed"})
        assert res.status_code == 400
        assert "not-allowed" in res.json()["detail"]
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_inference_success_and_failure(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    provider = _Provider(inference_error=RuntimeError("boom"))
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected="m1")
    try:
        res = client.post("/api/v1/onboarding/inference", json={"model_id": "m1"})
        assert res.status_code == 502
        assert "boom" in res.json()["detail"]
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)

    # success
    set_secret_store_for_testing(MemoryStore())
    provider2 = _Provider()
    client, previous_token, backend_main = _client(monkeypatch, provider=provider2, selected="m1")
    try:
        monkeypatch.setattr(provider2, "list_models", lambda: [{"name": "m1"}])
        res = client.post("/api/v1/onboarding/inference", json={"model_id": "m1"})
        assert res.status_code == 200
        assert res.json()["success"] is True
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_readiness_states(monkeypatch):
    set_secret_store_for_testing(MemoryStore())
    # healthy
    provider = _Provider(healthy=True)
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected="m1")
    try:
        # run real inference to persist evidence
        client.post("/api/v1/onboarding/inference", json={"model_id": "m1"})
        client.post("/api/v1/setup/safe-settings")
        import backend.core.voice_assets as va
        monkeypatch.setattr(va, "stt_model_installed", lambda: True)
        monkeypatch.setattr(va, "_stt_checksum_verified", lambda: True)
        monkeypatch.setattr(va, "asset_status", lambda: {"tts_voice": "Voice", "stt_installed": True})
        res = client.get("/api/v1/setup/readiness")
        assert res.status_code == 200
        body = res.json()
        assert body["full_ready"] is True
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)

    # incomplete
    set_secret_store_for_testing(MemoryStore())
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        res = client.get("/api/v1/setup/readiness")
        body = res.json()
        assert body["full_ready"] is False
        assert body["checks"]["selected_model_ok"] is False
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_setup_gating_and_retry(monkeypatch):
    # Limited text-first does not change backend full_ready; still false when incomplete
    set_secret_store_for_testing(MemoryStore())
    provider = _Provider(healthy=False)
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        res = client.get("/api/v1/setup/readiness")
        assert res.json()["full_ready"] is False
        assert res.json()["text_first_available"] is True
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def _clear_local_env_key(monkeypatch):
    """Remove the developer's local ``.env`` key from the effective settings.

    ``backend/.env`` is a developer-local file and is never shipped in the
    installer. A test that asserts the credential-free behaviour must not be
    influenced by whatever key the developer happens to have configured.
    """
    import backend.core.config as core_config

    monkeypatch.setattr(core_config.settings, "ollama_api_key", "")


def test_a_clean_installation_without_credentials_is_not_ready(monkeypatch):
    """A fresh installation must not claim readiness before a key is stored.

    This is the credential-free first-run contract: the release installer ships
    no ``.env`` and no key, so the readiness report has to report the missing
    credential and refuse to be full ready.
    """
    set_secret_store_for_testing(MemoryStore())
    _clear_local_env_key(monkeypatch)
    provider = _Provider(healthy=True, models=[{"name": "m1"}])
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        res = client.get("/api/v1/setup/readiness")
        assert res.status_code == 200
        body = res.json()
        assert body["checks"]["ollama_api_key_ok"] is False
        assert body["full_ready"] is False
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)


def test_the_supported_credential_flow_makes_the_key_configured(monkeypatch):
    """Storing a key through the onboarding endpoint satisfies the readiness check.

    The key is written to the configured secret store (the in-memory one here),
    never to a file, and the value is never returned by the API.
    """
    set_secret_store_for_testing(MemoryStore())
    _clear_local_env_key(monkeypatch)
    provider = _Provider(healthy=True, models=[{"name": "m1"}])
    client, previous_token, backend_main = _client(monkeypatch, provider=provider, selected=None)
    try:
        before = client.get("/api/v1/setup/readiness").json()
        assert before["checks"]["ollama_api_key_ok"] is False

        stored = client.post("/api/v1/onboarding/credentials/test", json={"value": "test-key-value"})
        assert stored.status_code == 200
        assert "test-key-value" not in stored.text

        after = client.get("/api/v1/setup/readiness").json()
        assert after["checks"]["ollama_api_key_ok"] is True
        # A key alone is not readiness: the other checks still have to pass.
        assert after["full_ready"] is False
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()
        set_secret_store_for_testing(None)
