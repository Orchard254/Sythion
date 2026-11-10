"""Strict cloud conversation bridge: offline, no API credentials or accounts."""
import asyncio
import json

import httpx
import pytest

from backend.adapters.openai_compatible_adapter import (
    CompatibleProviderError, OpenAICompatibleCloudAdapter,
)
from backend.runtime.compatible_chat_provider import (
    CompatibleCloudChatProvider, require_text_completion,
)
from backend.services.conversation_runtime import ModelClient as ConversationModelClient
from backend.services.conversation_turn_engine import ConversationTurnEngine


def completion(content='{"assistant_text":"Hello","commands":[]}', *, finish="stop"):
    return {
        "choices": [{
            "finish_reason": finish,
            "message": {"role": "assistant", "content": content},
        }]
    }


class StubAdapter:
    def __init__(self, result=None):
        self.calls = []
        self.result = completion() if result is None else result

    async def chat(self, *, messages, max_tokens, temperature):
        self.calls.append((messages, max_tokens, temperature))
        return self.result

    async def close(self):
        raise AssertionError("Injected adapter lifecycle belongs to owner")


@pytest.mark.asyncio
async def test_explicit_cloud_provider_matches_existing_conversation_text_contract():
    adapter = StubAdapter()
    provider = CompatibleCloudChatProvider("openrouter", "unit/model:free", adapter=adapter)
    client = ConversationModelClient(provider=provider)
    messages = [{"role": "user", "content": "Say hello with no commands"}]
    result = await client.chat(messages, temperature=0.2, max_tokens=270, timeout=10.0)
    assert json.loads(result) == {"assistant_text": "Hello", "commands": []}
    assert adapter.calls == [(messages, 270, 0.2)]
    assert provider.provider_id() == "openrouter"
    assert provider.selected_model_id() == "unit/model:free"
    await provider.close()


@pytest.mark.asyncio
async def test_conversation_turn_engine_accepts_explicit_provider_without_default_ollama(monkeypatch):
    from backend.runtime import model_provider
    monkeypatch.setattr(
        model_provider, "get_default_provider",
        lambda: (_ for _ in ()).throw(AssertionError("Unexpected Ollama provider")),
    )
    provider = CompatibleCloudChatProvider("groq", "unit-model", adapter=StubAdapter())
    engine = ConversationTurnEngine(ConversationModelClient(provider=provider))
    text = await engine._call_model([{"role": "user", "content": "Hello"}])
    assert json.loads(text)["commands"] == []
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_provider_supplies_native_shape_for_memory_without_running_operations():
    stub = StubAdapter(completion('{"ops": []}'))
    provider = CompatibleCloudChatProvider("nvidia", "unit", adapter=stub)
    answer = await provider.chat([{"role": "user", "content": "do not write memory"}])
    assert answer == {"message": {"role": "assistant", "content": '{"ops": []}'}}
    assert len(stub.calls) == 1


@pytest.mark.parametrize("response,reason", [
    ({}, "Invalid cloud text response"),
    ({"choices": []}, "Invalid cloud text response"),
    ({"choices": [completion()["choices"][0]] * 2}, "Invalid cloud text response"),
    (completion(None), "missing content"),
    (completion("   "), "missing content"),
    (completion("unfinished", finish="length"), "truncated"),
    (completion("text", finish="tool_calls"), "Unexpected cloud tool"),
    ({"choices": [{"message": {"role": "user", "content": "hello"}}]}, "Invalid cloud text role"),
    ({"choices": [{"message": {"role": "assistant", "content": "hi",
                              "tool_calls": [{"function": {"name": "delete"}}]}}]}, "Unexpected cloud tool"),
])
def test_untrusted_or_action_bearing_response_fails_closed(response, reason):
    with pytest.raises(CompatibleProviderError, match=reason):
        require_text_completion(response)


def test_untrusted_provider_is_rejected_before_adapter_injection():
    with pytest.raises(CompatibleProviderError, match="Unsupported inference provider"):
        CompatibleCloudChatProvider("https://untrusted.example", "model", adapter=StubAdapter())


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", [
    {"timeout": 0}, {"timeout": float("inf")}, {"max_tokens": -1},
    {"max_tokens": True}, {"temperature": float("nan")},
])
async def test_invalid_parameters_fail_without_provider_request(invalid):
    stub = StubAdapter()
    provider = CompatibleCloudChatProvider("groq", "unit", adapter=stub)
    with pytest.raises(CompatibleProviderError):
        await provider.chat([{"role": "user", "content": "hello"}], **invalid)
    assert stub.calls == []


@pytest.mark.asyncio
async def test_httpx_429_never_retries_and_never_leaks_secret_provider_body():
    calls = []
    async def handler(request):
        calls.append(request)
        assert request.url.host == "openrouter.ai"
        assert request.url.path == "/api/v1/chat/completions"
        body = json.loads(request.content)
        assert body["model"] == "unit/model:free"
        assert body["temperature"] == 0.2
        return httpx.Response(429, json={"error": {"message": "account-internal-sensitive-info"}})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler),
        base_url="https://openrouter.ai/api/v1/",
        follow_redirects=False,
    ) as client:
        adapter = OpenAICompatibleCloudAdapter(
            "openrouter", "unit/model:free", api_key="synthetic-secret", client=client,
        )
        provider = CompatibleCloudChatProvider(
            "openrouter", "unit/model:free", adapter=adapter,
        )
        with pytest.raises(CompatibleProviderError, match="Provider HTTP 429") as err:
            await provider.chat([{"role": "user", "content": "hi"}], temperature=0.2)
        assert "account-internal-sensitive-info" not in str(err.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_chat_timeout_has_unknown_outcome_no_fallback_or_replay():
    class Slow:
        calls = 0
        async def chat(self, **kwargs):
            self.calls += 1
            await asyncio.Event().wait()
    stub = Slow()
    provider = CompatibleCloudChatProvider("nvidia", "unit", adapter=stub)
    with pytest.raises(CompatibleProviderError, match="outcome unknown"):
        await provider.chat([{"role": "user", "content": "hello"}], timeout=0.01)
    assert stub.calls == 1
