"""ModelClient implementation for the Ollama Cloud adapter.

The reasoning layer depends on ``backend.runtime.interfaces.ModelClient``;
this wrapper is the only place that knows about the concrete adapter. No
routing, no fallback, no behavior change versus calling the adapter directly.
"""

from __future__ import annotations

from typing import Any, Dict, List

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
    """Single construction point for the default provider-backed client."""
    return OllamaCloudModelClient()
