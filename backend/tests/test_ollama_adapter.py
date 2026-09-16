"""Tests for the fail-closed Ollama Cloud capability contract."""

import json

import httpx
import pytest

from backend.adapters.ollama_adapter import (
    NUM_CTX,
    OllamaCloudAdapter,
    ProviderCapabilityError,
    ProviderCapabilityProfile,
    format_tools_for_ollama,
)
from backend.api.schemas import GUIHotkeyInput


def capability_transport(selected_model: str, tool_name: str = "sythion_capability_probe"):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": selected_model}]})
        if request.url.path == "/api/chat":
            assert json.loads(request.content)["num_ctx"] == NUM_CTX
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
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    profile = await adapter.ensure_capabilities()

    assert profile.model_id == "available-model"
    assert profile.native_tool_calls is True
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_rejects_stale_model_id():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "retired-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(ProviderCapabilityError, match="unavailable"):
        await adapter.ensure_capabilities()
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_accepts_tagged_model_matching_base_name():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model:cloud"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model"),
    )
    adapter._client_loop = asyncio.get_running_loop()

    profile = await adapter.ensure_capabilities()

    assert profile.model_id == "available-model:cloud"
    assert profile.native_tool_calls is True
    await adapter.close()


@pytest.mark.asyncio
async def test_capability_probe_rejects_plain_text_or_wrong_tool_call():
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=capability_transport("available-model", tool_name="wrong_tool"),
    )
    adapter._client_loop = asyncio.get_running_loop()

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
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=rate_limited_transport(),
    )
    adapter._client_loop = asyncio.get_running_loop()

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
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=transport,
    )
    adapter._client_loop = asyncio.get_running_loop()

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


def test_format_tools_emits_array_items_and_descriptions():
    formatted = format_tools_for_ollama([
        {"name": "gui_hotkey", "description": "Press a hotkey", "input_schema": GUIHotkeyInput}
    ])
    params = formatted[0]["function"]["parameters"]
    assert params["required"] == ["window_title", "keys"]
    keys = params["properties"]["keys"]
    assert keys["type"] == "array"
    assert keys["items"] == {"type": "string"}
    assert "JSON array" in keys["description"]
    # The model must see the full allowed-key whitelist, not an empty schema.
    assert "volumeup" in keys["description"]


def _payload_transport(seen: dict):
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/chat":
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "ok"}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_execute_tool_call_payload_sets_num_ctx():
    seen = {}
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    adapter._capability_profile = adapter._capability_profiles["available-model"]
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_payload_transport(seen),
    )
    adapter._client_loop = asyncio.get_running_loop()

    result = await adapter.execute_tool_call(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t", "description": "d", "input_schema": None}],
    )

    assert result["message"]["content"] == "ok"
    assert seen["payload"]["num_ctx"] == NUM_CTX
    # The rest of the tool-call request shape is unchanged.
    assert seen["payload"]["tools"] is not None
    assert seen["payload"]["max_tokens"] == 4000
    assert seen["payload"]["tool_choice"] == "auto"
    await adapter.close()


@pytest.mark.asyncio
async def test_chat_payload_sets_num_ctx():
    seen = {}
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_payload_transport(seen),
    )
    adapter._client_loop = asyncio.get_running_loop()

    result = await adapter.chat(messages=[{"role": "user", "content": "hi"}])

    assert result["message"]["content"] == "ok"
    assert seen["payload"]["num_ctx"] == NUM_CTX
    assert seen["payload"]["max_tokens"] == 4000
    await adapter.close()
