"""Inert cloud preview may persist only an explicit non-secret candidate.

No provider calls, no task execution, no readiness changes. Real application
endpoints are exercised using the shared authenticated TestClient.
"""
import json

import pytest

from backend.core import profile, secrets
from backend.runtime.model_client import OllamaCloudModelClient


@pytest.fixture
def preview_profile(tmp_path, monkeypatch):
    data_dir = tmp_path / "isolated-preview"
    data_dir.mkdir()
    monkeypatch.setattr(profile, "data_dir", lambda: str(data_dir))
    secrets.set_secret_store_for_testing(secrets.MemoryStore())
    yield data_dir
    secrets.set_secret_store_for_testing(None)


def test_staging_does_not_activate_provider_or_override_ollama_model(test_client, preview_profile, monkeypatch):
    from backend.runtime import model_client
    profile.set_selected_model("existing-ollama-model")
    monkeypatch.setattr(model_client, "get_adapter", lambda: object())
    response = test_client.post("/api/v1/inference-preview/stage", json={
        "provider_id": "openrouter", "model_id": "google/gemma-4-26b-a4b-it:free",
    })
    assert response.status_code == 200, response.text
    data = response.json()
    assert data == {
        "staged": True, "active": False,
        "provider_id": "openrouter", "model_id": "google/gemma-4-26b-a4b-it:free",
        "ready_for_execution": False, "model_verified": False,
    }
    assert profile.get_selected_model() == "existing-ollama-model"
    assert profile.get_staged_cloud_selection() == {
        "provider_id": "openrouter", "model_id": "google/gemma-4-26b-a4b-it:free"
    }
    assert isinstance(model_client.get_default_model_client(), OllamaCloudModelClient)
    stored = (preview_profile / "profile.json").read_text(encoding="utf-8")
    assert "existing-ollama-model" in stored
    assert "google/gemma-4-26b-a4b-it:free" in stored
    assert "api_key" not in stored


def test_provider_listing_uses_only_vault_presence_not_network(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    def no_network(*args, **kwargs):
        raise AssertionError("Catalog endpoint must not construct an adapter")
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", no_network)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-sensitive-token")
    response = test_client.get("/api/v1/inference-preview/providers")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["active_provider"] == "ollama-cloud"
    assert body["ready_for_execution"] is False
    assert body["staged"] == {}
    assert {p["provider_id"] for p in body["providers"]} == {"openrouter", "nvidia", "groq"}
    by_id = {p["provider_id"]: p for p in body["providers"]}
    assert by_id["openrouter"]["credential_configured"] is True
    assert by_id["groq"]["credential_configured"] is False
    assert "synthetic-sensitive-token" not in response.text


@pytest.mark.parametrize("provider,model", [
    ("untrusted", "legit/model:free"),
    ("openrouter", "https://wrong.host/file"),
    ("openrouter", ""),
    ("openrouter", "name with spaces"),
    ("openrouter", "../bad"),
    ("openrouter", "name\nheader"),
])
def test_invalid_preview_candidate_is_rejected_without_profile_mutation(test_client, preview_profile, provider, model):
    response = test_client.post("/api/v1/inference-preview/stage", json={
        "provider_id": provider, "model_id": model
    })
    assert response.status_code in {400, 422}
    assert profile.get_staged_cloud_selection() == {}
    assert not (preview_profile / "profile.json").exists()


def test_preview_rejects_secret_in_extra_payload(test_client, preview_profile):
    response = test_client.post("/api/v1/inference-preview/stage", json={
        "provider_id": "openrouter", "model_id": "model:free",
        "api_key": "do-not-persist-this",
    })
    assert response.status_code == 422
    assert not (preview_profile / "profile.json").exists()


def test_corrupt_or_untrusted_profile_never_trusted_as_selection(preview_profile):
    p = preview_profile / "profile.json"
    for payload in [
        {"staged_cloud_selection": {"provider_id": "https://evil.example", "model_id": "m"}},
        {"staged_cloud_selection": {"provider_id": "openrouter", "model_id": "http://evil.example"}},
        {"staged_cloud_selection": ["openrouter", "m"]},
        {"staged_cloud_selection": {"provider_id": "nvidia", "model_id": {"value": "m"}}},
    ]:
        p.write_text(json.dumps(payload), encoding="utf-8")
        assert profile.get_staged_cloud_selection() == {}


def test_preview_status_requires_authentication(test_client, preview_profile):
    token = test_client.headers.get("X-Control-Token")
    test_client.headers.pop("X-Control-Token", None)
    try:
        assert test_client.get("/api/v1/inference-preview/providers").status_code == 401
        assert test_client.post("/api/v1/inference-preview/stage", json={
            "provider_id": "openrouter", "model_id": "test/free:free"
        }).status_code == 401
        assert profile.get_staged_cloud_selection() == {}
    finally:
        if token:
            test_client.headers["X-Control-Token"] = token


def test_explicit_candidate_change_does_not_claim_capabilities(test_client, preview_profile):
    for provider in ("groq", "nvidia", "openrouter"):
        response = test_client.post("/api/v1/inference-preview/stage", json={
            "provider_id": provider, "model_id": "safe-model:free",
        })
        assert response.status_code == 200, response.text
        assert response.json()["model_verified"] is False
        assert response.json()["ready_for_execution"] is False
        assert profile.get_staged_cloud_selection()["provider_id"] == provider
