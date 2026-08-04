"""Tests for the fail-closed Ollama Cloud capability contract."""

import httpx
import pytest

from backend.adapters.ollama_adapter import OllamaCloudAdapter, ProviderCapabilityError


def capability_transport(selected_model: str, tool_name: str = "sythion_capability_probe"):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": selected_model}]})
        if request.url.path == "/api/chat":
            return httpx.Response(200, json={
                "message": {"tool_calls": [{"function": {"name": tool_name, "arguments": {}}}]}
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_capability_probe_requires_selected_model_and_native_tool_call():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )

    profile = await adapter.ensure_capabilities()

    assert profile.model_id == "available-model"
    assert profile.native_tool_calls is True
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_rejects_stale_model_id():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "retired-model"
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )

    with pytest.raises(ProviderCapabilityError, match="unavailable"):
        await adapter.ensure_capabilities()
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_accepts_tagged_model_matching_base_name():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model:cloud"
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )

    profile = await adapter.ensure_capabilities()

    assert profile.model_id == "available-model:cloud"
    assert profile.native_tool_calls is True
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_rejects_plain_text_or_wrong_tool_call():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model", tool_name="wrong_tool"),
    )

    with pytest.raises(ProviderCapabilityError, match="valid native tool call"):
        await adapter.ensure_capabilities()
    await adapter.close()


def rate_limited_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        return httpx.Response(429, json={"error": "rate limited"})

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_rate_limited_probe_reports_clean_message():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=rate_limited_transport(),
    )

    with pytest.raises(ProviderCapabilityError, match="Rate limit exceeded"):
        await adapter.ensure_capabilities()
    assert adapter._capability_failed_at is not None
    await adapter.close()


@pytest.mark.asyncio
async def test_failed_capability_is_cached_for_a_cooldown(monkeypatch):
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    calls = {"n": 0}
    transport = rate_limited_transport()
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )

    async def _count_send(request, *args, **kwargs):
        calls["n"] += 1
        return httpx.Response(429, json={"error": "rate limited"})

    monkeypatch.setattr(transport, "handle_async_request", _count_send)

    with pytest.raises(ProviderCapabilityError, match="Rate limit exceeded"):
        await adapter.ensure_capabilities()
    first_calls = calls["n"]

    # A second attempt inside the cooldown window must fail locally without
    # contacting the provider again.
    with pytest.raises(ProviderCapabilityError, match="retry in"):
        await adapter.ensure_capabilities()
    assert calls["n"] == first_calls
    await adapter.close()
