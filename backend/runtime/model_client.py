"""ModelClient implementation for the Ollama Cloud adapter.

The reasoning layer depends on ``backend.runtime.interfaces.ModelClient``;
this wrapper is the only place that knows about the concrete adapter. No
routing, no fallback, no behavior change versus calling the adapter directly.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional

from backend.adapters.ollama_adapter import get_adapter
from backend.runtime.interfaces import ModelClient


class OllamaCloudModelClient:
    """Adapter-facing ModelClient backed by the existing Ollama Cloud singleton."""

    def __init__(self):
        self._adapter = get_adapter()

    async def decide(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        return await self._adapter.execute_tool_call(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    def provider_name(self) -> str:
        return "ollama-cloud"


def get_default_model_client() -> ModelClient:
    """Resolve the active reasoning provider; unchanged Ollama behavior by default.

    Provider selection is authenticated, credential-bound backend state. An
    invalid active cloud binding raises: NEVER silently fall back to Ollama.
    """
    from backend.runtime.active_inference import get_active_decision_client
    return get_active_decision_client()



class CompatibleCloudModelClient:
    """Explicitly selected OpenAI-compatible cloud decision client.

    Not a default, not an automatic fallback and not a credential switch.
    The adapter verifies actual tool-calling ability before a decision.
    Tests may inject a synthetic adapter; normal construction reads the
    appropriate build-scoped provider secret from the existing vault.
    """

    def __init__(
        self, provider_id: str, model_id: str, *, adapter: Optional[Any] = None,
    ):
        # Delayed import preserves Ollama's existing default startup path.
        from backend.adapters.openai_compatible_adapter import (
            OpenAICompatibleCloudAdapter, provider_spec,
        )
        self._provider_id = provider_spec(provider_id).provider_id
        self._adapter = (
            adapter if adapter is not None else
            OpenAICompatibleCloudAdapter(self._provider_id, model_id)
        )

    async def decide(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        return await self._adapter.decide(
            messages=messages,
            tools=tools,
            max_tokens=max_tokens,
        )

    def provider_name(self) -> str:
        return self._provider_id

    async def close(self) -> None:
        await self._adapter.close()
