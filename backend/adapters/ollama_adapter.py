"""
Ollama Cloud adapter for Sythion (Synth).
Handles authentication, model selection, and tool calls.
"""

import time
import httpx
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from ..core.config import settings, resolve_selected_model
from ..core.secrets import resolve_ollama_api_key


class ProviderCapabilityError(RuntimeError):
    """A selected Cloud provider/model cannot safely execute Sythion (Synth) decisions."""


# After a capability probe fails (e.g. rate limit/outage), wait this long before
# probing again so a dead provider is not re-contacted on every task.
_CAPABILITY_FAILURE_COOLDOWN_SECONDS = 60


def _friendly_http_error(exc: Exception) -> str:
    """Map httpx failures to a short, user-safe message."""
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        if status == 429:
            return "Rate limit exceeded - try again later"
        if status == 401:
            return "Invalid API key"
        return f"HTTP error {status}"
    return str(exc)


@dataclass(frozen=True)
class ProviderCapabilityProfile:
    model_id: str
    native_tool_calls: bool


class OllamaCloudAdapter:
    """Adapter for Ollama Cloud API."""

    def __init__(self):
        self.base_url = settings.ollama_cloud_base_url
        self.api_key = resolve_ollama_api_key(settings.ollama_api_key)
        self.selected_model = resolve_selected_model()
        self._client: Optional[httpx.AsyncClient] = None
        self._capability_profile: Optional[ProviderCapabilityProfile] = None
        self._capability_failed_at: Optional[float] = None

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with authentication."""
        if self._client is None or self._client.is_closed:
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
        return self._client

    async def check_health(self) -> bool:
        """Check authentication, selected model availability, and native tools."""
        try:
            await self.ensure_capabilities()
            return True
        except ProviderCapabilityError:
            return False

    async def list_models(self) -> List[Dict[str, Any]]:
        """List available models from Ollama Cloud."""
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except Exception as e:
            raise RuntimeError(f"Failed to list models: {_friendly_http_error(e)}")

    async def ensure_capabilities(self) -> ProviderCapabilityProfile:
        """Fail closed until the selected Cloud model proves native tool support."""
        if self._capability_profile:
            return self._capability_profile
        if not self.api_key:
            raise ProviderCapabilityError("Cloud API key is not configured")
        if not self.selected_model:
            raise ProviderCapabilityError("No Cloud model has been selected")

        # Negative cache: a recent probe/list failure (rate limit, outage) is
        # not retried on every task, which would keep the provider hot.
        if self._capability_failed_at is not None:
            remaining = _CAPABILITY_FAILURE_COOLDOWN_SECONDS - (
                time.monotonic() - self._capability_failed_at
            )
            if remaining > 0:
                raise ProviderCapabilityError(
                    f"Cloud provider unavailable (rate limit or outage); "
                    f"retry in {int(remaining)}s"
                )

        try:
            models = await self.list_models()
        except RuntimeError as exc:
            self._capability_failed_at = time.monotonic()
            raise ProviderCapabilityError(f"Model listing failed: {exc}") from exc

        model_ids = {
            model_id
            for model in models
            for model_id in (model.get("name"), model.get("model"))
            if isinstance(model_id, str)
        }
        # Ollama Cloud tags aliases with a deployment suffix (e.g. the provider
        # serves /api/chat for "minimax-m3:cloud" while /api/tags only lists
        # the base "minimax-m3"). Accept the exact id or its base name, so a
        # stale/retired model is still rejected while a valid tag passes.
        base_model = self.selected_model.rsplit(":", 1)[0]
        if self.selected_model not in model_ids and base_model not in model_ids:
            raise ProviderCapabilityError(
                f"Selected model '{self.selected_model}' is unavailable"
            )

        probe_tool_name = "sythion_capability_probe"
        payload = {
            "model": self.selected_model,
            "messages": [{
                "role": "user",
                "content": "Call the supplied health-check tool exactly once.",
            }],
            "tools": [{
                "type": "function",
                "function": {
                    "name": probe_tool_name,
                    "description": "Non-mutating Sythion (Synth) provider capability check.",
                    "parameters": {"type": "object", "properties": {}},
                },
            }],
            "stream": False,
        }
        try:
            client = await self._get_client()
            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            tool_calls = response.json().get("message", {}).get("tool_calls", [])
        except httpx.HTTPError as exc:
            self._capability_failed_at = time.monotonic()
            raise ProviderCapabilityError(
                f"Native tool probe failed: {_friendly_http_error(exc)}"
            ) from exc

        if len(tool_calls) != 1 or tool_calls[0].get("function", {}).get("name") != probe_tool_name:
            raise ProviderCapabilityError("Selected model did not return one valid native tool call")

        self._capability_profile = ProviderCapabilityProfile(
            model_id=self.selected_model,
            native_tool_calls=True,
        )
        return self._capability_profile

    async def execute_tool_call(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4000,
    ) -> Dict[str, Any]:
        """Execute a tool call via Ollama Cloud.

        Args:
            messages: Conversation messages
            tools: Tool definitions in OpenAI format
            max_tokens: Maximum output tokens

        Returns:
            Model response with tool calls

        Raises:
            RuntimeError: If the request fails
        """
        try:
            await self.ensure_capabilities()
            client = await self._get_client()
            
            payload = {
                "model": self.selected_model,
                "messages": messages,
                "tools": tools,
                "max_tokens": max_tokens,
                "tool_choice": "auto",
                "stream": False,
            }

            response = await client.post("/api/chat", json=payload)
            response.raise_for_status()
            return response.json()

        except ProviderCapabilityError:
            raise
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 429:
                raise RuntimeError("Rate limit exceeded")
            elif e.response.status_code == 401:
                raise RuntimeError("Invalid API key")
            else:
                raise RuntimeError(f"HTTP error: {e.response.status_code}")
        except httpx.ReadTimeout:
            raise RuntimeError(
                "Request timed out after 300s - the model is taking too long to respond"
            )
        except httpx.ConnectError as e:
            raise RuntimeError(f"Connection error: {e}")
        except Exception as e:
            raise RuntimeError(f"Request failed: {type(e).__name__}: {str(e)}")

    async def close(self):
        """Close the HTTP client."""
        if self._client and not self._client.is_closed:
            await self._client.aclose()


# Singleton instance
_adapter: Optional[OllamaCloudAdapter] = None


def get_adapter() -> OllamaCloudAdapter:
    """Get the Ollama Cloud adapter instance."""
    global _adapter
    if _adapter is None:
        _adapter = OllamaCloudAdapter()
    return _adapter


def format_tools_for_ollama(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format tool definitions for Ollama Cloud API.

    Args:
        tools: List of tool definitions with name, description, and input_schema

    Returns:
        List of tools in OpenAI function calling format
    """
    formatted = []
    for tool in tools:
        # Convert Pydantic schema to JSON Schema
        properties = {}
        required = []
        
        if hasattr(tool.get('input_schema'), 'model_fields'):
            for field_name, field_info in tool['input_schema'].model_fields.items():
                prop = {
                    "type": "string",  # Default type
                    "description": field_info.description or "",
                }
                
                # Infer type from annotation
                if hasattr(field_info, 'annotation'):
                    ann = field_info.annotation
                    if ann == int:
                        prop["type"] = "integer"
                    elif ann == float:
                        prop["type"] = "number"
                    elif ann == bool:
                        prop["type"] = "boolean"
                    elif hasattr(ann, '__origin__') and ann.__origin__ is list:
                        prop["type"] = "array"
                
                properties[field_name] = prop
                
                # Check if required: only fields without a default are required
                if field_info.is_required():
                    required.append(field_name)

        formatted.append({
            "type": "function",
            "function": {
                "name": tool['name'],
                "description": tool['description'],
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        })

    return formatted
