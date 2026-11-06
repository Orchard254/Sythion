"""No-key model discovery must fail closed with a clear provider error."""

import pytest
import httpx
import asyncio

from backend.adapters.ollama_adapter import (
    OllamaCloudAdapter,
    OllamaCloudProvider,
    ProviderCapabilityError,
)
from backend.core import secrets


def test_no_key_list_models_raises_clear_error_without_http(monkeypatch):
    monkeypatch.setattr(secrets, "_store", secrets.MemoryStore())

    class FactoryCalledError(AssertionError):
        pass

    def factory(**kwargs):
        raise FactoryCalledError("HTTP client was constructed despite missing key")

    monkeypatch.setattr(httpx, "AsyncClient", factory)

    adapter = OllamaCloudAdapter()
    adapter.api_key = ""

    with pytest.raises(ProviderCapabilityError) as raised:
        asyncio.run(adapter.list_models())

    assert "Cloud API key is not configured" in str(raised.value)


def test_no_key_get_models_endpoint_returns_clear_error(monkeypatch, test_client):
    monkeypatch.setattr(secrets, "_store", secrets.MemoryStore())

    adapter = OllamaCloudAdapter()
    adapter.api_key = ''
    monkeypatch.setattr("backend.adapters.ollama_adapter.get_adapter", lambda: adapter)

    import backend.runtime.model_provider as model_provider
    monkeypatch.setattr(model_provider, "get_default_provider", lambda: OllamaCloudProvider())

    response = test_client.get("/api/v1/models")
    assert response.status_code == 503
    assert response.json()["detail"] == "Failed to fetch models: Cloud API key is not configured"
