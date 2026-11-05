import json

import httpx
import pytest

try:
    from backend.adapters.ollama_adapter import (
        NUM_CTX,
        OllamaCloudAdapter,
        ProviderCapabilityProfile,
    )
    from backend.services.conversation_runtime import ModelClient as RuntimeModelClient
    from backend.services.conversation_turn_engine import ConversationTurnEngine
except ImportError:
    from adapters.ollama_adapter import (
        NUM_CTX,
        OllamaCloudAdapter,
        ProviderCapabilityProfile,
    )
    from services.conversation_runtime import ModelClient as RuntimeModelClient
    from services.conversation_turn_engine import ConversationTurnEngine


def _transport(seen):
    async def handler(request):
        if request.url.path == "/api/chat":
            seen["payload"] = json.loads(request.content)
            return httpx.Response(200, json={"message": {"content": "ok"}})
        return httpx.Response(404)

    return httpx.MockTransport(handler)


def _adapter(seen):
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
        transport=_transport(seen),
    )
    adapter._client_loop = asyncio.get_event_loop()
    return adapter


@pytest.mark.asyncio
async def test_real_adapter_payload_carries_temperature_in_options():
    seen = {}
    adapter = _adapter(seen)
    try:
        result = await adapter.chat(
            messages=[{"role": "user", "content": "hi"}],
            temperature=0.2,
        )
        assert result["message"]["content"] == "ok"
        assert seen["payload"].get("options", {}).get("temperature") == 0.2
        assert "temperature" not in seen["payload"]
        assert "format" not in seen["payload"]
        assert seen["payload"]["options"]["num_ctx"] == NUM_CTX
        assert seen["payload"]["options"]["num_predict"] == 4000
        assert "num_ctx" not in seen["payload"]
        assert "max_tokens" not in seen["payload"]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_real_adapter_default_payload_omits_temperature():
    seen = {}
    adapter = _adapter(seen)
    try:
        await adapter.chat(messages=[{"role": "user", "content": "hi"}])
        assert "temperature" not in seen["payload"]
        assert seen["payload"]["options"] == {"num_ctx": NUM_CTX, "num_predict": 4000}
        assert "format" not in seen["payload"]
    finally:
        await adapter.close()


@pytest.mark.asyncio
async def test_assistant_call_forwards_low_temperature_only():
    import json

    class FakeModel:
        def __init__(self):
            self.kwargs = {}

        async def chat(self, messages, **kwargs):
            self.kwargs = dict(kwargs)
            return json.dumps({"assistant_text": "hello", "commands": []})

    fake = FakeModel()
    engine = ConversationTurnEngine(fake)
    result = await engine.run("hi", [], [])
    assert result["assistant_text"] == "hello"
    assert fake.kwargs.get("temperature") == 0.2
    assert "format" not in fake.kwargs
    assert "json_mode" not in fake.kwargs


@pytest.mark.asyncio
async def test_unsupported_provider_does_not_forward_temperature():
    class UnsupportedProvider:
        def __init__(self):
            self.calls = []

        async def chat(self, messages, max_tokens=4000, timeout=60.0):
            self.calls.append(
                {"messages": messages, "max_tokens": max_tokens, "timeout": timeout}
            )
            return {"message": {"content": "ok"}}

    provider = UnsupportedProvider()
    client = RuntimeModelClient(provider=provider)
    output = await client.chat(
        messages=[{"role": "user", "content": "hi"}],
        temperature=0.2,
        max_tokens=12,
        timeout=5.0,
    )
    assert output == "ok"
    assert len(provider.calls) == 1
    assert "temperature" not in provider.calls[0]
    assert provider.calls[0]["max_tokens"] == 12
    assert provider.calls[0]["timeout"] == 5.0


@pytest.mark.asyncio
async def test_engine_malformed_json_retried_once_then_rejected():
    import json

    class FakeModel:
        def __init__(self):
            self.calls = 0

        async def chat(self, messages, **kwargs):
            self.calls += 1
            return "not json at all"

    fake = FakeModel()
    engine = ConversationTurnEngine(fake)
    with pytest.raises(Exception):
        await engine.run("hi", [], [])
    assert fake.calls == 2
