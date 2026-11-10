"""Explicit cloud client implementation, no account access or remote calls."""
from typing import Any, Dict, List

import pytest

from backend.runtime.interfaces import ModelClient
from backend.runtime.model_client import (
    CompatibleCloudModelClient, OllamaCloudModelClient, get_default_model_client,
)
from backend.adapters.openai_compatible_adapter import CompatibleProviderError


class InertCloudAdapter:
    def __init__(self):
        self.requests = []
        self.closed = False

    async def decide(self, messages, tools, *, max_tokens):
        self.requests.append((messages, tools, max_tokens))
        return {"choices": [{"message": {"tool_calls": [{
            "function": {"name": "complete_task", "arguments": '{"result":"OK"}'}
        }]}}]}

    async def close(self):
        self.closed = True


@pytest.mark.asyncio
async def test_explicit_compatible_client_respects_narrow_model_client_protocol():
    adapter = InertCloudAdapter()
    client = CompatibleCloudModelClient("openrouter", "unit-test-model", adapter=adapter)
    assert isinstance(client, ModelClient)
    assert client.provider_name() == "openrouter"
    messages = [{"role": "user", "content": "reply OK"}]
    tools = [{"type": "function", "function": {"name": "complete_task"}}]
    response = await client.decide(messages, tools, max_tokens=72)
    assert response["choices"][0]["message"]["tool_calls"][0]["function"]["name"] == "complete_task"
    assert adapter.requests == [(messages, tools, 72)]
    await client.close()
    assert adapter.closed


def test_unsupported_cloud_provider_cannot_be_constructed_even_with_injected_adapter():
    with pytest.raises(CompatibleProviderError, match="Unsupported"):
        CompatibleCloudModelClient("https://attacker.example", "unit", adapter=InertCloudAdapter())


def test_default_model_client_remains_ollama_only(monkeypatch):
    from backend.runtime import model_client as module
    sentinel = object()
    monkeypatch.setattr(module, "get_adapter", lambda: sentinel)
    default = get_default_model_client()
    assert isinstance(default, OllamaCloudModelClient)
    assert default._adapter is sentinel
    assert default.provider_name() == "ollama-cloud"


def test_injected_cloud_model_never_constructs_default_provider(monkeypatch):
    import backend.services.model_engine as model_engine

    def forbidden():
        raise AssertionError("unexpected Ollama provider initialization")

    monkeypatch.setattr(model_engine, "get_default_model_client", forbidden)
    client = CompatibleCloudModelClient("groq", "unit-model", adapter=InertCloudAdapter())
    engine = model_engine.ModelDecisionEngine(
        db_session=None,
        tool_executor=object(),
        model_client=client,
    )
    assert engine.model_client is client
    assert engine.adapter is client._adapter
