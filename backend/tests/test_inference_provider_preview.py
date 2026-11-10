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



def test_probe_requires_exact_staging_and_explicit_consent(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    attempts = []
    def never_construct(*args, **kwargs):
        attempts.append(1)
        raise AssertionError("No model request should occur")
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", never_construct)
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key")
    request = {
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    }
    assert test_client.post("/api/v1/inference-preview/probe", json=request).status_code == 409
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    assert test_client.post("/api/v1/inference-preview/probe", json={
        **request, "confirm_network_request": False,
    }).status_code == 409
    assert test_client.post("/api/v1/inference-preview/probe", json={
        **request, "model_id": "different/tool:free",
    }).status_code == 409
    assert test_client.post("/api/v1/inference-preview/probe", json={
        **request, "extra_secret": "must-not-accept",
    }).status_code == 422
    assert attempts == []


def test_probe_fails_closed_without_build_scoped_key(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected network")))
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == 409
    assert "secure provider store" in response.json()["detail"]


def test_probe_success_persists_only_scoped_tool_evidence_without_execution(test_client, test_db, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    from backend.core.cloud_probe_evidence import is_probe_evidence_current
    attempts = []

    class SafeProbe:
        def __init__(self, provider, model, **kwargs):
            assert kwargs["api_key"] == "synthetic-key"
            attempts.append(("init", provider, model))
        async def prove_tool_capability(self):
            attempts.append(("probe",))
        async def close(self):
            attempts.append(("close",))

    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", SafeProbe)
    profile.set_selected_model("ollama-original")
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "tool_probe_verified": True, "ready_for_execution": False,
        "active": False, "evidence_persisted": True,
    }
    assert is_probe_evidence_current(test_db) is True
    assert test_client.get("/api/v1/inference-preview/providers").json()["tool_probe_verified"] is True
    assert attempts == [
        ("init", "openrouter", "test/tool:free"), ("probe",), ("close",)
    ]
    assert profile.get_selected_model() == "ollama-original"
    assert "tool_probe_verified" not in (preview_profile / "profile.json").read_text()
    assert "synthetic-key" not in (preview_profile / "profile.json").read_text()
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    assert is_probe_evidence_current(test_db) is False
    assert test_client.get("/api/v1/inference-preview/providers").json()["tool_probe_verified"] is False


@pytest.mark.parametrize("error,expected_status,detail", [
    ("Model capability probe failed (provider HTTP 429)", 429, "rate limit"),
    ("Model capability probe failed (provider HTTP 401)", 502, "stored credential"),
    ("Model capability probe failed (provider HTTP 400)", 502, "capability request"),
    ("Model capability probe failed (network outcome unknown)", 503, "outcome unknown"),
    ("Model capability probe failed (model returned no tool call)", 422, "tool calling"),
])
def test_probe_maps_errors_without_leaking_provider_details(
    test_client, preview_profile, monkeypatch, error, expected_status, detail
):
    import backend.adapters.openai_compatible_adapter as compatible
    attempts = []

    class FailingProbe:
        def __init__(self, *args, **kwargs):
            attempts.append("init")
        async def prove_tool_capability(self):
            attempts.append("probe")
            raise compatible.CompatibleProviderError(error)
        async def close(self):
            attempts.append("close")

    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", FailingProbe)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-sensitive-token")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == expected_status
    assert detail in response.json()["detail"]
    assert "synthetic-sensitive-token" not in response.text
    assert attempts == ["init", "probe", "close"]


def test_probe_rejects_missing_auth_before_constructing_provider(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter",
                        lambda *a, **k: (_ for _ in ()).throw(AssertionError("unexpected")))
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key")
    original = test_client.headers.pop("X-Control-Token", None)
    try:
        response = test_client.post("/api/v1/inference-preview/probe", json={
            "provider_id": "openrouter", "model_id": "test/tool:free",
            "confirm_network_request": True,
        })
        assert response.status_code == 401
    finally:
        if original:
            test_client.headers["X-Control-Token"] = original


def test_inflight_candidate_change_invalidates_probe_result(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    class ChangedProbe:
        def __init__(self, *args, **kwargs):
            pass
        async def prove_tool_capability(self):
            profile.stage_cloud_selection("groq", "different-model")
        async def close(self):
            pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", ChangedProbe)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-key")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == 409
    assert "changed during verification" in response.json()["detail"]



def test_probe_key_rotation_during_request_never_persists_success(test_client, test_db, preview_profile, monkeypatch):
    from backend.core.cloud_probe_evidence import is_probe_evidence_current
    import backend.adapters.openai_compatible_adapter as compatible

    class KeyChangingProbe:
        def __init__(self, *args, **kwargs):
            assert kwargs["api_key"] == "synthetic-v1"
        async def prove_tool_capability(self):
            secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-v2")
        async def close(self):
            pass

    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", KeyChangingProbe)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-v1")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == 409
    assert is_probe_evidence_current(test_db) is False


def test_probe_database_write_failure_fails_closed(test_client, test_db, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    import backend.core.cloud_probe_evidence as evidence

    class PassingProbe:
        def __init__(self, *args, **kwargs):
            pass
        async def prove_tool_capability(self):
            pass
        async def close(self):
            pass

    def fail(*args, **kwargs):
        raise RuntimeError("PRIVATE DB ERROR WITH NO PROOF")

    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", PassingProbe)
    monkeypatch.setattr(evidence, "set_setting", fail)
    profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic")
    response = test_client.post("/api/v1/inference-preview/probe", json={
        "provider_id": "openrouter", "model_id": "test/tool:free",
        "confirm_network_request": True,
    })
    assert response.status_code == 503
    assert "PRIVATE DB ERROR" not in response.text
    assert evidence.is_probe_evidence_current(test_db) is False


def test_preview_readiness_remains_ollama_only_after_scoped_evidence(
    test_client, test_db, preview_profile, monkeypatch
):
    from backend.core.cloud_probe_evidence import persist_probe_evidence
    from backend.runtime.model_provider import ModelHealth
    import backend.runtime.model_provider as mp

    class BadOllama:
        def health(self):
            return ModelHealth("ollama-cloud", ModelHealth.UNAVAILABLE)

    monkeypatch.setattr(mp, "get_default_provider", lambda: BadOllama())
    snapshot = profile.stage_cloud_selection("openrouter", "test/tool:free")
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "inert")
    persist_probe_evidence(test_db, profile.get_staged_cloud_snapshot(), "inert")
    status = test_client.get("/api/v1/inference-preview/providers").json()
    assert status["tool_probe_verified"] is True
    assert status["ready_for_execution"] is False
    readiness = test_client.get("/api/v1/setup/readiness").json()
    assert readiness["checks"]["ollama_health_ok"] is False
    assert readiness["checks"]["inference_verified_ok"] is False


def test_model_catalog_requires_stored_key_and_never_calls_provider(
    test_client, preview_profile, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    def fail(*a, **kw):
        raise AssertionError("No provider call without a stored key")
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", fail)
    response = test_client.get("/api/v1/inference-preview/model-catalog",
                               params={"provider_id": "openrouter"})
    assert response.status_code == 409
    assert profile.get_staged_cloud_selection() == {}


def test_model_catalog_returns_safe_metadata_and_does_not_stage(
    test_client, preview_profile, monkeypatch,
):
    import backend.adapters.openai_compatible_adapter as compatible
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-sensitive-token")
    calls = []
    class Catalog:
        def __init__(self, provider_id, model_id, *, api_key):
            calls.append(("init", provider_id, model_id, api_key))
        async def list_models(self):
            calls.append(("list",))
            return [
                {"model": "b/model:free", "supported_parameters": ["tools"]},
                {"model": "a/model", "supported_parameters": []},
                {"model": "https://evil.invalid", "supported_parameters": ["tools"]},
                {"model": "bad id"},
                {"model": "b/model:free", "supported_parameters": ["tools"]},
            ]
        async def close(self):
            calls.append(("close",))
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter", Catalog)
    response = test_client.get("/api/v1/inference-preview/model-catalog",
                               params={"provider_id": "openrouter"})
    assert response.status_code == 200, response.text
    assert response.json() == {
        "provider_id": "openrouter",
        "models": [
            {"id": "a/model", "free_variant": False, "tools_listed": False},
            {"id": "b/model:free", "free_variant": True, "tools_listed": True},
        ],
        "truncated": False,
        "ready_for_execution": False,
    }
    assert "synthetic-sensitive-token" not in response.text
    assert profile.get_staged_cloud_selection() == {}
    assert calls == [
        ("init", "openrouter", "__catalog_discovery__", "synthetic-sensitive-token"),
        ("list",), ("close",),
    ]


def test_model_catalog_rejects_untrusted_provider_and_missing_control_token(
    test_client, preview_profile,
):
    response = test_client.get("/api/v1/inference-preview/model-catalog",
                               params={"provider_id": "evil"})
    assert response.status_code == 400
    token = test_client.headers.pop("X-Control-Token", None)
    try:
        response = test_client.get("/api/v1/inference-preview/model-catalog",
                                   params={"provider_id": "openrouter"})
        assert response.status_code == 401
    finally:
        if token:
            test_client.headers["X-Control-Token"] = token


def test_model_catalog_sanitizes_rejected_key(test_client, preview_profile, monkeypatch):
    import backend.adapters.openai_compatible_adapter as compatible
    secrets.set_secret(secrets.OPENROUTER_API_KEY, "synthetic-sensitive-token")
    class FailingCatalog:
        async def list_models(self):
            raise compatible.CompatibleProviderError("Provider HTTP 401")
        async def close(self):
            pass
    monkeypatch.setattr(compatible, "OpenAICompatibleCloudAdapter",
                        lambda *a, **kw: FailingCatalog())
    response = test_client.get("/api/v1/inference-preview/model-catalog",
                               params={"provider_id": "openrouter"})
    assert response.status_code == 502
    assert "stored credential" in response.json()["detail"]
    assert "synthetic-sensitive-token" not in response.text
