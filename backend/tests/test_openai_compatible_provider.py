"""OpenAI-compatible provider transport: deterministic offline security tests."""
import json
import pytest
import httpx

from backend.adapters.openai_compatible_adapter import (
    OpenAICompatibleCloudAdapter, CompatibleProviderError, provider_spec,
    PROVIDERS,
)
from backend.core import secrets


def _response(*, tool=False, name="sythion_capability_probe", args='{"answer":7}'):
    message = {"role": "assistant", "content": "hello"}
    if tool:
        message["tool_calls"] = [{
            "type": "function",
            "function": {"name": name, "arguments": args},
        }]
    return {"choices": [{"message": message}]}


@pytest.mark.parametrize("provider,host", [
    ("openrouter", "openrouter.ai"), ("nvidia", "integrate.api.nvidia.com"),
    ("groq", "api.groq.com"),
])
def test_endpoints_are_static_and_https(provider, host):
    from urllib.parse import urlsplit
    target = urlsplit(provider_spec(provider).base_url)
    assert target.scheme == "https"
    assert target.hostname == host
    with pytest.raises(CompatibleProviderError):
        provider_spec(provider + ".attacker.example")


def test_build_scoped_secret_names_are_separate():
    assert {"openrouter_api_key", "nvidia_api_key", "groq_api_key"} <= secrets.KNOWN_SECRETS
    with pytest.raises(ValueError):
        secrets.resolve_compatible_provider_key("attacker")


@pytest.mark.asyncio
async def test_chat_uses_the_fixed_url_and_does_not_leak_a_key():
    seen = []
    async def handler(req):
        seen.append(req)
        assert req.url.host == "openrouter.ai"
        assert req.url.path == "/api/v1/chat/completions"
        assert req.headers["authorization"] == "Bearer inert-test-key"
        assert req.headers.get("accept") != "text/event-stream"
        payload = json.loads(req.content)
        assert payload["model"] == "unit-model:free"
        assert payload["stream"] is False
        assert payload["messages"][0]["content"] == "hello"
        return httpx.Response(200, json=_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model:free", client=client, api_key="inert-test-key")
        assert (await a.chat([{"role": "user", "content": "hello"}]))["choices"][0]["message"]["content"] == "hello"
    assert len(seen) == 1


@pytest.mark.asyncio
async def test_status_error_redacts_upstream_body_and_does_not_retry():
    calls = []
    async def handler(req):
        calls.append(req)
        return httpx.Response(402, json={"error": {"message": "private-account-token"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="Provider HTTP 402") as exc:
            await a.chat([{"role": "user", "content": "hello"}])
    assert "private-account-token" not in str(exc.value)
    assert "secret" not in str(exc.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_provider_redirect_is_not_followed():
    calls = []
    async def handler(req):
        calls.append(req)
        return httpx.Response(302, headers={"Location": "https://attacker.example/key"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/", follow_redirects=False) as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="redirect rejected"):
            await a.chat([{"role": "user", "content": "hello"}])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_probe_is_required_before_any_decision_and_is_cached():
    calls = []
    async def handler(req):
        body = json.loads(req.content)
        calls.append(body)
        if body.get("tool_choice") == {"type": "function", "function": {"name": "sythion_capability_probe"}}:
            return httpx.Response(200, json=_response(tool=True))
        return httpx.Response(200, json=_response(tool=True, name="complete_task", args='{"result":"done"}'))
    tools = [{"type": "function", "function": {
        "name": "complete_task", "description": "Complete", "parameters": {"type": "object", "properties": {}}
    }}]
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        assert (await a.decide([{"role": "user", "content": "do work"}], tools))["choices"]
        assert (await a.decide([{"role": "user", "content": "do more"}], tools))["choices"]
    assert len(calls) == 3
    assert calls[0]["max_tokens"] == 90
    assert calls[1]["tool_choice"] == "required"


@pytest.mark.asyncio
@pytest.mark.parametrize("bad", [
    _response(),
    _response(tool=True, name="wrong"),
    _response(tool=True, args='{"answer":9}'),
    _response(tool=True, args='not json'),
])
async def test_bad_probe_fails_closed_without_decision(bad):
    calls = []
    async def handler(req):
        calls.append(req)
        return httpx.Response(200, json=bad)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="capability probe failed"):
            await a.decide([{"role": "user", "content": "task"}], [{"type": "function", "function": {"name": "complete_task"}}])
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_plaintext_decision_does_not_create_a_tool_call():
    calls = []
    async def handler(req):
        calls.append(req)
        if len(calls) == 1:
            return httpx.Response(200, json=_response(tool=True))
        return httpx.Response(200, json=_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="did not return a tool"):
            await a.decide([{"role": "user", "content": "task"}], [{"type": "function", "function": {"name": "complete_task"}}])
    assert len(calls) == 2


@pytest.mark.asyncio
async def test_discovery_is_not_capability_evidence():
    async def handler(req):
        assert req.url.path == "/api/v1/models"
        return httpx.Response(200, json={"data": [
            {"id": "free-model", "supported_parameters": ["tools"]},
            {"id": "incompatible", "supported_parameters": []},
        ]})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        a = OpenAICompatibleCloudAdapter("openrouter", "free-model", client=client, api_key="secret")
        models = await a.list_models()
        assert len(models) == 2
        assert models[0]["name"] == "free-model"
        assert not a._capability_ok


@pytest.mark.asyncio
async def test_openrouter_key_verification_is_authenticated_and_never_prints_details(capsys):
    calls = []
    async def handler(req):
        calls.append(req)
        assert req.url.host == "openrouter.ai"
        assert req.url.path == "/api/v1/key"
        assert req.headers["authorization"] == "Bearer inert-secret"
        return httpx.Response(200, json={"data": {"label": "secret-prefix-123", "is_free_tier": True}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        adapter = OpenAICompatibleCloudAdapter("openrouter", "discovery", client=client, api_key="inert-secret")
        assert await adapter.verify_key() is None
    assert len(calls) == 1
    assert "secret-prefix-123" not in capsys.readouterr().out


@pytest.mark.asyncio
async def test_key_verification_401_is_sanitized_and_separate_from_tool_probe():
    calls = []
    async def handler(req):
        calls.append(req)
        return httpx.Response(401, json={"error": {"message": "private-token-and-account"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        adapter = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="inert-secret")
        with pytest.raises(CompatibleProviderError, match="Provider HTTP 401") as error:
            await adapter.verify_key()
    assert "private-token-and-account" not in str(error.value)
    assert len(calls) == 1
    assert calls[0].url.path == "/api/v1/key"


@pytest.mark.asyncio
@pytest.mark.parametrize("status", [400, 401, 402, 403, 404, 429])
async def test_capability_probe_reports_only_sanitized_http_status(status):
    calls = []
    async def handler(req):
        calls.append(req)
        return httpx.Response(status, json={"error": {"message": "PRIVATE-ACCOUNT-DATA"}})
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        adapter = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="capability probe failed") as error:
            await adapter.prove_tool_capability()
    assert "provider HTTP " + str(status) in str(error.value)
    assert "PRIVATE-ACCOUNT-DATA" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_probe_reports_missing_tool_as_safe_category():
    async def handler(req):
        return httpx.Response(200, json=_response())
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), base_url="https://openrouter.ai/api/v1/") as client:
        adapter = OpenAICompatibleCloudAdapter("openrouter", "unit-model", client=client, api_key="secret")
        with pytest.raises(CompatibleProviderError, match="model returned no tool call"):
            await adapter.prove_tool_capability()
