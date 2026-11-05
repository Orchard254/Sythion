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

import httpx as _httpx_for_timeout_tests
from backend.api.schemas import GUIHotkeyInput


def capability_transport(selected_model: str, tool_name: str = "sythion_capability_probe"):
    async def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers["authorization"] == "Bearer test-key"
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={"models": [{"name": selected_model}]})
        if request.url.path == "/api/chat":
            payload = json.loads(request.content)
            assert payload["options"]["num_ctx"] == NUM_CTX
            assert "num_ctx" not in payload
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
@pytest.mark.parametrize("max_tokens", [None, 73])
async def test_execute_tool_call_payload_sets_num_ctx(max_tokens):
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
        **({} if max_tokens is None else {"max_tokens": max_tokens}),
    )

    assert result["message"]["content"] == "ok"
    assert seen["payload"]["options"] == {
        "num_ctx": NUM_CTX, "num_predict": 4000 if max_tokens is None else max_tokens,
    }
    assert "num_ctx" not in seen["payload"]
    assert "max_tokens" not in seen["payload"]
    # The rest of the tool-call request shape is unchanged.
    assert seen["payload"]["tools"] is not None
    assert seen["payload"]["tool_choice"] == "auto"
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("max_tokens", [None, 73])
@pytest.mark.parametrize("temperature", [None, 0.0, 0.7])
async def test_chat_payload_sets_num_ctx(max_tokens, temperature):
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

    result = await adapter.chat(
        messages=[{"role": "user", "content": "hi"}],
        temperature=temperature,
        **({} if max_tokens is None else {"max_tokens": max_tokens}),
    )

    assert result["message"]["content"] == "ok"
    expected_options = {
        "num_ctx": NUM_CTX, "num_predict": 4000 if max_tokens is None else max_tokens,
    }
    if temperature is not None:
        expected_options["temperature"] = temperature
    assert seen["payload"]["options"] == expected_options
    assert "num_ctx" not in seen["payload"]
    assert "max_tokens" not in seen["payload"]
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat", "execute_tool_call"])
@pytest.mark.parametrize("cancel", [False, True])
async def test_native_request_failure_preserves_error_without_replay(method, cancel):
    import asyncio

    calls = []

    async def handler(request):
        calls.append(request)
        if cancel:
            raise asyncio.CancelledError()
        return httpx.Response(400, json={"error": "invalid model schema"})

    adapter = OllamaCloudAdapter()
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com", transport=httpx.MockTransport(handler),
    )
    adapter._client_loop = asyncio.get_running_loop()
    kwargs = {"messages": [{"role": "user", "content": "hi"}]}
    if method == "execute_tool_call":
        kwargs["tools"] = []
    try:
        if cancel:
            with pytest.raises(asyncio.CancelledError):
                await getattr(adapter, method)(**kwargs)
        else:
            with pytest.raises(RuntimeError, match="HTTP error 400"):
                await getattr(adapter, method)(**kwargs)
        assert len(calls) == 1
    finally:
        await adapter.close()


from backend.adapters.ollama_adapter import OllamaRequestPayloadTooLargeError


def _byte_count_transport(calls):
    async def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"message": {"content": "ok"}})
    return httpx.MockTransport(handler)


@pytest.mark.asyncio
async def test_default_byte_envelope_guard_is_inert():
    seen = {}
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    calls = {"n": 0}
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_byte_count_transport(calls),
    )
    adapter._client_loop = asyncio.get_running_loop()

    result = await adapter.chat(messages=[{"role": "user", "content": "x" * 1000}])
    assert result["message"]["content"] == "ok"
    assert calls["n"] == 1
    await adapter.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("method", ["chat", "execute_tool_call"])
async def test_byte_envelope_guard_rejects_before_transport(method):
    calls = {"n": 0}
    adapter = OllamaCloudAdapter(max_request_payload_bytes=1)
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_byte_count_transport(calls),
    )
    adapter._client_loop = asyncio.get_running_loop()

    kwargs = {"messages": [{"role": "user", "content": "hello"}]}
    if method == "execute_tool_call":
        kwargs["tools"] = [{"name": "t", "description": "d", "input_schema": None}]

    with pytest.raises(OllamaRequestPayloadTooLargeError) as exc_info:
        await getattr(adapter, method)(**kwargs)
    exc = exc_info.value
    assert exc.payload_bytes > exc.max_payload_bytes
    assert exc.reason == "payload_byte_envelope_exceeded"
    assert "not a token or context-window fit claim" in str(exc)
    assert calls["n"] == 0
    await adapter.close()


@pytest.mark.asyncio
async def test_byte_envelope_uses_actual_httpx_serialization():
    payload = {
        "model": "available-model",
        "messages": [{"role": "user", "content": "hi"}],
        "options": {"num_predict": 4000, "num_ctx": NUM_CTX},
        "stream": False,
    }
    actual = len(httpx.Request("POST", "/api/chat", json=payload).content)

    calls = {"n": 0}
    adapter = OllamaCloudAdapter()
    adapter.max_request_payload_bytes = actual
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_byte_count_transport(calls),
    )
    adapter._client_loop = asyncio.get_running_loop()

    result = await adapter.chat(messages=[{"role": "user", "content": "hi"}])
    assert result["message"]["content"] == "ok"
    assert calls["n"] == 1
    await adapter.close()

    adapter2 = OllamaCloudAdapter()
    adapter2.max_request_payload_bytes = actual - 1
    adapter2.api_key = "test-key"
    adapter2.selected_model = "available-model"
    adapter2._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    import asyncio
    adapter2._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_byte_count_transport(calls),
    )
    adapter2._client_loop = asyncio.get_running_loop()
    with pytest.raises(OllamaRequestPayloadTooLargeError):
        await adapter2.chat(messages=[{"role": "user", "content": "hi"}])
    await adapter2.close()


@pytest.mark.asyncio
async def test_capability_probe_byte_envelope_guard_rejects_before_transport():
    calls = {"tags": 0, "chat": 0}

    async def handler(request):
        if request.url.path == "/api/tags":
            calls["tags"] += 1
            return httpx.Response(200, json={"models": [{"name": "available-model"}]})
        if request.url.path == "/api/chat":
            calls["chat"] += 1
            return httpx.Response(200, json={
                "message": {"tool_calls": [{"function": {"name": "x", "arguments": {}}}]},
            })
        return httpx.Response(404)

    adapter = OllamaCloudAdapter(max_request_payload_bytes=1)
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(OllamaRequestPayloadTooLargeError):
        await adapter.ensure_capabilities()
    assert calls["chat"] == 0
    await adapter.close()


@pytest.mark.asyncio
async def test_none_max_tokens_uses_default_in_chat_and_tool_call():
    seen = {}
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    adapter._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_payload_transport(seen),
    )
    adapter._client_loop = asyncio.get_running_loop()

    await adapter.chat(messages=[{"role": "user", "content": "hi"}], max_tokens=None)
    assert seen["payload"]["options"]["num_predict"] == 4000

    seen2 = {}
    adapter2 = OllamaCloudAdapter()
    adapter2.api_key = "test-key"
    adapter2.selected_model = "available-model"
    adapter2._capability_profiles["available-model"] = ProviderCapabilityProfile(
        model_id="available-model", native_tool_calls=True
    )
    adapter2._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_payload_transport(seen2),
    )
    adapter2._client_loop = asyncio.get_running_loop()
    await adapter2.execute_tool_call(
        messages=[{"role": "user", "content": "hi"}],
        tools=[{"name": "t", "description": "d", "input_schema": None}],
        max_tokens=None,
    )
    assert seen2["payload"]["options"]["num_predict"] == 4000
    await adapter.close()
    await adapter2.close()


@pytest.mark.asyncio
async def test_execute_tool_call_capability_probe_payload_too_large_preserves_structured_error():
    calls = {"tags": 0, "chat": 0}

    async def handler(request):
        if request.url.path == "/api/tags":
            calls["tags"] += 1
            return httpx.Response(200, json={"models": [{"name": "available-model"}]})
        if request.url.path == "/api/chat":
            calls["chat"] += 1
            return httpx.Response(200, json={"message": {"content": "ok"}})
        return httpx.Response(404)

    adapter = OllamaCloudAdapter(max_request_payload_bytes=200)
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"
    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=httpx.MockTransport(handler),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(OllamaRequestPayloadTooLargeError) as exc_info:
        await adapter.execute_tool_call(messages=[], tools=[])

    exc = exc_info.value
    assert type(exc) is OllamaRequestPayloadTooLargeError
    assert exc.reason == "payload_byte_envelope_exceeded"
    assert exc.code == "payload_byte_envelope_exceeded"
    assert exc.error_code == "payload_byte_envelope_exceeded"
    assert exc.payload_bytes == 358
    assert exc.actual_bytes == 358
    assert exc.observed_bytes == 358
    assert exc.max_payload_bytes == 200
    assert exc.configured_limit == 200
    assert exc.limit == 200
    assert "not a token or context-window fit claim" in str(exc)
    assert calls == {"tags": 1, "chat": 0}
    await adapter.close()


@pytest.mark.asyncio
async def test_chat_read_timeout_redacts_secret_and_preserves_type():
    secret = "SECRET_READ_TIMEOUT_URL"
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"

    async def handler(request):
        raise httpx.ReadTimeout(secret)

    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com", transport=httpx.MockTransport(handler),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(RuntimeError) as exc_info:
        await adapter.chat(messages=[{"role": "user", "content": "hi"}])
    exc = exc_info.value
    assert "SECRET" not in str(exc)
    assert secret not in str(exc)
    assert type(exc.__cause__) is httpx.ReadTimeout
    assert secret not in str(exc.__cause__)
    await adapter.close()


@pytest.mark.asyncio
async def test_chat_connect_timeout_redacts_secret_and_preserves_type():
    secret = "SECRET_CONNECT_TIMEOUT_URL"
    adapter = OllamaCloudAdapter()
    adapter.api_key = "test-key"
    adapter.selected_model = "available-model"

    async def handler(request):
        raise httpx.ConnectTimeout(secret)

    import asyncio
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com", transport=httpx.MockTransport(handler),
    )
    adapter._client_loop = asyncio.get_running_loop()

    with pytest.raises(RuntimeError) as exc_info:
        await adapter.chat(messages=[{"role": "user", "content": "hi"}])
    exc = exc_info.value
    assert secret not in str(exc)
    assert type(exc.__cause__) is httpx.ConnectTimeout
    assert secret not in str(exc.__cause__)
    await adapter.close()
