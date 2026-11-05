"""
Acceptance gate: Cloud capability (Phase B).

Onboarding lists authenticated model IDs, and the provider is only usable
after proving one fake read-only native tool call. Unauthenticated or stale
providers fail closed.
"""

import json

import httpx
import pytest

from backend.adapters.ollama_adapter import NUM_CTX, OllamaCloudAdapter, ProviderCapabilityError


def _transport(selected_model: str):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": selected_model}]})
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            assert payload["options"]["num_ctx"] == NUM_CTX
            assert "num_ctx" not in payload
            return httpx.Response(200, json={
                "message": {"tool_calls": [{"function": {"name": "sythion_capability_probe", "arguments": {}}}]}
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_onboarding_lists_authenticated_model_ids():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_transport("minimax-m3"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    models = await adapter.list_models()

    assert [m["name"] for m in models] == ["minimax-m3"]
    await adapter.close()


@pytest.mark.asyncio
async def test_provider_unusable_without_proven_native_tool():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "minimax-m3:cloud"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_transport("minimax-m3"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    profile = await adapter.ensure_capabilities()

    assert profile.model_id == "minimax-m3:cloud"
    assert profile.native_tool_calls is True
    await adapter.close()


@pytest.mark.asyncio
async def test_no_api_key_fails_closed():
    adapter = OllamaCloudAdapter()
    adapter.api_key = ""
    adapter.selected_model = "minimax-m3:cloud"

    with pytest.raises(ProviderCapabilityError, match="API key"):
        await adapter.ensure_capabilities()


@pytest.mark.asyncio
async def test_stale_model_fails_closed():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "retired-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_transport("minimax-m3"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(ProviderCapabilityError, match="unavailable"):
        await adapter.ensure_capabilities()
    await adapter.close()
