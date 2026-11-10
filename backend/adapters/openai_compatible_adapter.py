"""Guarded OpenAI-compatible CLOUD transport for explicitly trusted providers.

V1 foundation: OpenRouter, NVIDIA NIM, Groq. No arbitrary URLs, no heuristic
model substitutions, no automatic retries/failover, no provider credential
exposure. Integration into default routing/onboarding is a separate gate.
These endpoints' OpenAI-style response shape is already supported by
ModelDecisionEngine._parse_decision; capability is *not* assumed by model name.

The only modeled side effect here is an HTTPS inference request. This module
never executes tool calls or mutates Sythion task state.
"""
from __future__ import annotations

import asyncio
import json
import time
from dataclasses import dataclass
from typing import Any, Dict, List, Optional

import httpx


@dataclass(frozen=True)
class CompatibleProviderSpec:
    provider_id: str
    base_url: str
    key_name: str


# A fixed egress allowlist is critical: the bearer token cannot be redirected
# to user-supplied hosts or cross-provider URLs. No configurable base_url.
PROVIDERS: Dict[str, CompatibleProviderSpec] = {
    "openrouter": CompatibleProviderSpec(
        "openrouter", "https://openrouter.ai/api/v1", "openrouter_api_key"
    ),
    "nvidia": CompatibleProviderSpec(
        "nvidia", "https://integrate.api.nvidia.com/v1", "nvidia_api_key"
    ),
    "groq": CompatibleProviderSpec(
        "groq", "https://api.groq.com/openai/v1", "groq_api_key"
    ),
}


class CompatibleProviderError(RuntimeError):
    """Sanitized status/error category, never raw response bodies or keys."""


def provider_spec(provider_id: str) -> CompatibleProviderSpec:
    try:
        return PROVIDERS[provider_id]
    except (KeyError, TypeError):
        raise CompatibleProviderError("Unsupported inference provider") from None


def _message(body: Any) -> Dict[str, Any]:
    if not isinstance(body, dict):
        raise CompatibleProviderError("Invalid provider response")
    choices = body.get("choices")
    if not isinstance(choices, list) or not choices:
        raise CompatibleProviderError("Invalid provider response")
    choice = choices[0]
    if not isinstance(choice, dict) or not isinstance(choice.get("message"), dict):
        raise CompatibleProviderError("Invalid provider message")
    return choice["message"]


def _tool_calls(message: Dict[str, Any]) -> List[Dict[str, Any]]:
    calls = message.get("tool_calls")
    if not isinstance(calls, list) or not calls:
        raise CompatibleProviderError("Model did not return a tool call")
    for call in calls:
        if not isinstance(call, dict):
            raise CompatibleProviderError("Malformed model tool call")
        fn = call.get("function")
        if not isinstance(fn, dict) or not isinstance(fn.get("name"), str) or not fn["name"]:
            raise CompatibleProviderError("Malformed model tool call")
        args = fn.get("arguments")
        if isinstance(args, str):
            try:
                args = json.loads(args)
            except (ValueError, TypeError):
                raise CompatibleProviderError("Invalid model tool arguments") from None
        if not isinstance(args, dict):
            raise CompatibleProviderError("Invalid model tool arguments")
    return calls


class OpenAICompatibleCloudAdapter:
    """One selected model on one allowlisted provider. No fallback or replay."""

    MAX_BYTES = 512_000

    def __init__(
        self,
        provider_id: str,
        model_id: str,
        *,
        client: Optional[httpx.AsyncClient] = None,
        api_key: Optional[str] = None,
    ):
        spec = provider_spec(provider_id)
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 180:
            raise CompatibleProviderError("An explicit model ID is required")
        # Injected key/client are for isolated tests. Production resolves only
        # the existing build-scoped write-only vault.
        if api_key is None:
            from backend.core.secrets import resolve_compatible_provider_key
            api_key = resolve_compatible_provider_key(spec.provider_id)
        if not isinstance(api_key, str) or not api_key.strip():
            raise CompatibleProviderError("Provider credential not configured")
        self.spec = spec
        self.model_id = model_id.strip()
        self._key = api_key.strip()
        self._client = client
        self._owns_client = client is None
        self._capability_ok = False
        self._probe_lock = asyncio.Lock()
        self._probe_failed_at: Optional[float] = None

    async def _get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            self._client = httpx.AsyncClient(
                base_url=self.spec.base_url + "/",
                headers={"Authorization": "Bearer " + self._key},
                timeout=httpx.Timeout(90.0, connect=10.0),
                follow_redirects=False,
            )
        return self._client

    async def _request(self, method: str, endpoint: str, *, data: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
        # A caller cannot supply an endpoint: only these internal paths exist.
        supported = {
            ("GET", "models"),
            ("POST", "chat/completions"),
        }
        # OpenRouter publishes an authenticated current-key endpoint. Never
        # send other providers' bearer tokens to an arbitrary verification URL.
        if self.spec.provider_id == "openrouter":
            supported.add(("GET", "key"))
        if (method, endpoint) not in supported:
            raise CompatibleProviderError("Unsupported inference operation")
        if data is not None:
            size = len(json.dumps(data, ensure_ascii=False).encode("utf-8"))
            if size > self.MAX_BYTES:
                raise CompatibleProviderError("Inference request exceeds payload limit")
        client = await self._get_client()
        try:
            kwargs = {"headers": {"Authorization": "Bearer " + self._key}}
            response = await (client.get(endpoint, **kwargs) if method == "GET"
                else client.post(endpoint, json=data, **kwargs))
            # No redirect, no second attempt on a potentially accepted call.
            if 300 <= response.status_code < 400:
                raise CompatibleProviderError("Provider redirect rejected")
            response.raise_for_status()
            body = response.json()
            if not isinstance(body, dict):
                raise CompatibleProviderError("Invalid provider response")
            return body
        except httpx.HTTPStatusError as exc:
            # OpenRouter has a documented free-model eligibility gate that
            # explicitly identifies disallowed non-agentic harness requests.
            # Recognize the *exact* routing-step code only. Never relay the
            # upstream error message, account metadata, or response body.
            if self.spec.provider_id == "openrouter" and exc.response.status_code == 403:
                if len(exc.response.content) <= 4096:
                    try:
                        payload = exc.response.json()
                    except (ValueError, TypeError):
                        payload = None
                    if isinstance(payload, dict):
                        error = payload.get("error")
                        metadata = error.get("metadata") if isinstance(error, dict) else None
                        if isinstance(metadata, dict) and (
                            metadata.get("failed_routing_step") ==
                            "Gate Free Endpoints by Agentic Harness"
                        ):
                            raise CompatibleProviderError(
                                "Provider agentic harness required"
                            ) from None
            raise CompatibleProviderError(
                "Provider HTTP " + str(exc.response.status_code)
            ) from None
        except (httpx.TimeoutException, httpx.TransportError):
            raise CompatibleProviderError("Provider transport outcome unknown") from None
        except (ValueError, TypeError):
            raise CompatibleProviderError("Invalid provider response") from None

    async def verify_key(self) -> None:
        """Verify an OpenRouter credential through its authenticated key API.

        Model listing may be publicly accessible, so successful catalog
        discovery is *not* evidence that the supplied key is valid. Do not
        display the returned account metadata, key label or usage details.
        This is a GET; no model inference or tool execution is performed.
        """
        if self.spec.provider_id != "openrouter":
            raise CompatibleProviderError("Key verification unavailable for provider")
        body = await self._request("GET", "key")
        if not isinstance(body.get("data"), dict):
            raise CompatibleProviderError("Invalid key verification response")

    async def list_models(self) -> List[Dict[str, Any]]:
        """Actual provider model discovery, not evidence of tool capability."""
        body = await self._request("GET", "models")
        rows = body.get("data")
        if not isinstance(rows, list):
            raise CompatibleProviderError("Invalid provider model listing")
        result = []
        for row in rows:
            if isinstance(row, dict) and isinstance(row.get("id"), str) and row["id"]:
                result.append({
                    "name": row["id"], "model": row["id"],
                    "supported_parameters": (
                        row["supported_parameters"] if isinstance(row.get("supported_parameters"), list) else []
                    ),
                })
        return result

    async def chat(
        self, messages: List[Dict[str, Any]], *, max_tokens: int = 4000,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not isinstance(messages, list) or not messages:
            raise CompatibleProviderError("Messages are required")
        payload: Dict[str, Any] = {
            "model": self.model_id,
            "messages": messages,
            "max_tokens": max_tokens,
            "stream": False,
        }
        if temperature is not None:
            payload["temperature"] = temperature
        body = await self._request("POST", "chat/completions", data=payload)
        _message(body)
        return body

    async def prove_tool_capability(self) -> None:
        """Provider must actually call a named harmless function once.

        No model is marked tool-capable from catalog metadata alone. A failed
        probe is temporarily cached to avoid wasting free-tier requests.
        """
        if self._capability_ok:
            return
        async with self._probe_lock:
            if self._capability_ok:
                return
            if (self._probe_failed_at is not None and
                    time.monotonic() - self._probe_failed_at < 60):
                raise CompatibleProviderError("Model capability probe recently failed")
            tool = {
                "type": "function",
                "function": {
                    "name": "sythion_capability_probe",
                    "description": "Return a known integer without doing any external work",
                    "parameters": {
                        "type": "object",
                        "properties": {"answer": {"type": "integer"}},
                        "required": ["answer"],
                        "additionalProperties": False,
                    },
                },
            }
            payload = {
                "model": self.model_id,
                "messages": [{"role": "user", "content": "Call sythion_capability_probe with answer 7."}],
                "tools": [tool],
                "tool_choice": {"type": "function", "function": {"name": "sythion_capability_probe"}},
                "max_tokens": 90,
                "stream": False,
            }
            try:
                body = await self._request("POST", "chat/completions", data=payload)
                calls = _tool_calls(_message(body))
                if len(calls) != 1 or calls[0]["function"]["name"] != "sythion_capability_probe":
                    raise CompatibleProviderError("Model capability probe returned the wrong tool")
                args = calls[0]["function"]["arguments"]
                if isinstance(args, str):
                    args = json.loads(args)
                if args != {"answer": 7}:
                    raise CompatibleProviderError("Model capability probe gave the wrong arguments")
                self._capability_ok = True
                self._probe_failed_at = None
            except (CompatibleProviderError, ValueError, TypeError) as exc:
                self._probe_failed_at = time.monotonic()
                # Report only locally defined error categories. Never expose
                # provider response bodies, request headers, or model outputs.
                reason = str(exc)
                if reason.startswith("Provider HTTP ") and reason[14:].isdigit():
                    category = "provider HTTP " + reason[14:]
                elif reason == "Provider transport outcome unknown":
                    category = "network outcome unknown"
                elif reason == "Provider redirect rejected":
                    category = "redirect rejected"
                elif reason == "Model did not return a tool call":
                    category = "model returned no tool call"
                elif reason == "Invalid model tool arguments":
                    category = "invalid tool-call arguments"
                elif reason == "Model capability probe returned the wrong tool":
                    category = "incorrect function name or call count"
                elif reason == "Model capability probe gave the wrong arguments":
                    category = "incorrect function arguments"
                elif reason == "Invalid provider message" or reason == "Invalid provider response":
                    category = "invalid provider message"
                else:
                    category = "invalid probe response"
                raise CompatibleProviderError(
                    "Model capability probe failed (" + category + ")"
                ) from None

    async def decide(
        self, messages: List[Dict[str, Any]], tools: List[Dict[str, Any]],
        *, max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        if not isinstance(tools, list) or not tools:
            raise CompatibleProviderError("Decision tools are required")
        if not isinstance(messages, list) or not messages:
            raise CompatibleProviderError("Messages are required")
        await self.prove_tool_capability()
        body = await self._request("POST", "chat/completions", data={
            "model": self.model_id,
            "messages": messages,
            "tools": tools,
            "tool_choice": "required",
            "max_tokens": max_tokens,
            "stream": False,
        })
        _tool_calls(_message(body))
        return body

    async def close(self) -> None:
        if self._owns_client and self._client is not None:
            await self._client.aclose()
        self._client = None
