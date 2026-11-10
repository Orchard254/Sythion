"""Strict explicit cloud-chat bridge for existing Sythion conversation contracts.

The OpenAI-compatible transport emits choices[].message. Existing
ConversationRuntime and memory consolidation expect the Ollama-style
{"message": {"content": ...}} shape, while ConversationTurnEngine expects a
string from ConversationRuntime.ModelClient.chat(). This bridge normalizes a
verified *text* answer without changing either callsite.

It does NOT select a provider, enable onboarding, attest readiness, trigger
tool execution, or retry an uncertain request. Only trusted provider IDs are
accepted. Consumers must inject it explicitly; Ollama stays the default.

This is a response-shape foundation, NOT provider activation.
"""
from __future__ import annotations

import asyncio
import math
from typing import Any, Dict, List, Optional

from backend.adapters.openai_compatible_adapter import (
    CompatibleProviderError,
    OpenAICompatibleCloudAdapter,
    provider_spec,
    _message,
)


def require_text_completion(response: Any) -> str:
    """Return text only from exactly one non-truncated assistant choice.

    Malformed or action-bearing responses must never leak as printable dicts
    into a conversation or memory operation. Do not echo upstream content
    in raised errors; callers can report a safe category.
    """
    if not isinstance(response, dict):
        raise CompatibleProviderError("Invalid cloud text response")
    choices = response.get("choices")
    if not isinstance(choices, list) or len(choices) != 1:
        raise CompatibleProviderError("Invalid cloud text response")
    choice = choices[0]
    if not isinstance(choice, dict):
        raise CompatibleProviderError("Invalid cloud text response")
    if choice.get("finish_reason") == "length":
        raise CompatibleProviderError("Cloud text response truncated")
    if choice.get("finish_reason") in ("tool_calls", "function_call"):
        raise CompatibleProviderError("Unexpected cloud tool response")
    message = _message(response)
    if message.get("role", "assistant") != "assistant":
        raise CompatibleProviderError("Invalid cloud text role")
    if message.get("tool_calls") or message.get("function_call"):
        raise CompatibleProviderError("Unexpected cloud tool response")
    content = message.get("content")
    if not isinstance(content, str) or not content.strip():
        raise CompatibleProviderError("Cloud text response missing content")
    if len(content) > 200_000:
        raise CompatibleProviderError("Cloud text response exceeds limit")
    return content


class CompatibleCloudChatProvider:
    """Explicit OpenAI-compatible provider for chat/structured-text operations.

    This wrapper can be injected as ConversationRuntime.ModelClient(provider=...)
    or into best-effort memory consolidation, which consumes dict.message.content.
    It does NOT implement synchronous model discovery or health; those must
    be checked separately at provider admission.
    """

    def __init__(
        self, provider_id: str, model_id: str, *, adapter: Optional[Any] = None,
    ):
        spec = provider_spec(provider_id)
        if not isinstance(model_id, str) or not model_id.strip() or len(model_id) > 180:
            raise CompatibleProviderError("Explicit cloud model ID required")
        self._provider_id = spec.provider_id
        self._model_id = model_id.strip()
        self._owns_adapter = adapter is None
        self._adapter = adapter if adapter is not None else OpenAICompatibleCloudAdapter(
            self._provider_id, self._model_id,
        )

    def provider_id(self) -> str:
        return self._provider_id

    def selected_model_id(self) -> str:
        return self._model_id

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        timeout: float = 60.0,
        temperature: Optional[float] = None,
    ) -> Dict[str, Any]:
        if not isinstance(messages, list) or not messages:
            raise CompatibleProviderError("Cloud messages required")
        if type(max_tokens) is not int or not 1 <= max_tokens <= 32768:
            raise CompatibleProviderError("Invalid cloud output limit")
        if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
                or not math.isfinite(timeout) or timeout <= 0 or timeout > 300):
            raise CompatibleProviderError("Invalid cloud request timeout")
        if temperature is not None and (
            isinstance(temperature, bool)
            or not isinstance(temperature, (int, float))
            or not math.isfinite(temperature)
            or not 0 <= temperature <= 2
        ):
            raise CompatibleProviderError("Invalid cloud temperature")

        try:
            # Enforce the caller's timeout without retrying a call that may
            # have reached the provider. Cancellation propagates unchanged.
            raw = await asyncio.wait_for(
                self._adapter.chat(
                    messages=messages, max_tokens=max_tokens,
                    temperature=temperature,
                ),
                timeout=float(timeout),
            )
        except asyncio.TimeoutError:
            raise CompatibleProviderError("Provider transport outcome unknown") from None

        content = require_text_completion(raw)
        # Single canonical shape shared by ConversationRuntime.ModelClient
        # and existing memory consolidation. No synthetic readiness claims.
        return {"message": {"role": "assistant", "content": content}}

    async def close(self) -> None:
        if self._owns_adapter:
            await self._adapter.close()
