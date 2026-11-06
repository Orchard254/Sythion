"""
Ollama Cloud adapter for Sythion (Synth).
Handles authentication, model selection, and tool calls.
"""

import asyncio
import logging
import time
import httpx
from dataclasses import dataclass
from typing import List, Dict, Any, Optional
from ..core.config import settings, resolve_selected_model
from ..core.secrets import resolve_ollama_api_key

logger = logging.getLogger(__name__)


# Requested native context option, not an authoritative model limit or a
# guarantee that the provider honors it. Input token fit is not established here.
NUM_CTX = 131072

# Default-inert configured byte envelope for the final serialized HTTP
# request body.  Zero means "disabled" and preserves historical behavior.
# This is a conservative transport-size guard only; it is NOT a claim about
# token usage, model context windows, or provider-side acceptance.
DEFAULT_MAX_REQUEST_PAYLOAD_BYTES = 0
MAX_REQUEST_PAYLOAD_BYTES = DEFAULT_MAX_REQUEST_PAYLOAD_BYTES


class OllamaRequestPayloadTooLargeError(ValueError, RuntimeError):
    """Final Ollama request body exceeds the configured byte envelope."""

    def __init__(self, message, *, payload_bytes=None, max_payload_bytes=None,
                 reason=None, **extra):
        super().__init__(message)
        self.reason = reason or "payload_byte_envelope_exceeded"
        self.code = self.reason
        self.error_code = self.reason
        self.payload_bytes = payload_bytes
        self.actual_bytes = payload_bytes
        self.observed_bytes = payload_bytes
        self.max_payload_bytes = max_payload_bytes
        self.configured_limit = max_payload_bytes
        self.limit = max_payload_bytes
        for key, value in extra.items():
            setattr(self, key, value)



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
    return type(exc).__name__


def _safe_httpx_cause(exc: Exception) -> Exception:
    """Return a secret-free causal exception preserving only its httpx type.

    The original request/response objects, URLs, headers, bodies, and message
    text can contain credentials or sensitive transport details, so they are
    deliberately not chained.  Callers still get enough structural identity to
    classify the failure without exposing the provider exchange.
    """
    exc_type = type(exc)
    status_code = None
    response = getattr(exc, "response", None)
    if response is not None:
        status_code = getattr(response, "status_code", None)

    if exc_type is getattr(httpx, "HTTPStatusError", None) or issubclass(
        exc_type, getattr(httpx, "HTTPStatusError", Exception)
    ):
        safe_response = httpx.Response(status_code if isinstance(status_code, int) else 500)
        safe_request = httpx.Request("POST", "/")
        try:
            return exc_type("HTTP error", request=safe_request, response=safe_response)
        except Exception:
            return exc_type()

    try:
        return exc_type()
    except Exception:
        return exc_type(str(exc_type.__name__))


def _raise_sanitized(exc: BaseException, cause: Optional[Exception] = None) -> None:
    """Raise ``exc`` after clearing the implicit exception context.

    Attaching ``cause`` sets ``__cause__`` and suppresses context printing, but
    Python still records the in-flight exception in ``__context__`` where it can
    leak request/response details.  Scrub it before propagation.
    """
    if cause is not None:
        exc.__cause__ = cause
        exc.__suppress_context__ = True
    try:
        raise exc
    except BaseException:  # scrub regardless of exc concrete type
        exc.__context__ = None
        raise


@dataclass(frozen=True)
class ProviderCapabilityProfile:
    model_id: str
    native_tool_calls: bool


class OllamaCloudAdapter:
    """Adapter for Ollama Cloud API."""

    def __init__(self, max_request_payload_bytes=0):
        self.max_request_payload_bytes = max_request_payload_bytes
        self.base_url = settings.ollama_cloud_base_url
        self.api_key = resolve_ollama_api_key(settings.ollama_api_key)
        self.selected_model = resolve_selected_model()
        self._client: Optional[httpx.AsyncClient] = None
        self._client_loop: Optional[asyncio.AbstractEventLoop] = None
        # Per-model capability cache — immutable profile per model, no cross-poisoning.
        self._capability_profiles: Dict[str, ProviderCapabilityProfile] = {}
        self._capability_failed_at_map: Dict[str, float] = {}
        # Per-model async probes: only same-model concurrent cold starts serialize.
        import asyncio as _asyncio
        import threading as _thr
        self._cap_lock = _thr.Lock()
        self._capability_locks: Dict[str, _asyncio.Lock] = {}
        # Back-compat aliases: single-model view for legacy callers/tests reading the old attrs.
        self._capability_profile: Optional[ProviderCapabilityProfile] = None
        self._capability_failed_at: Optional[float] = None

    def _capability_lock_for(self, model_id: str):
        """Per-model lock — narrow synchronization, no global bottleneck."""
        import asyncio as _asyncio
        with self._cap_lock:
            lk = self._capability_locks.get(model_id)
            if lk is None:
                lk = _asyncio.Lock()
                self._capability_locks[model_id] = lk
            return lk

    async def _get_client(self) -> httpx.AsyncClient:
        """Get or create HTTP client with authentication.

        Recreates the client if the event loop has changed (e.g., when
        called from a background task with a different event loop).
        """
        current_loop = asyncio.get_running_loop()
        if (self._client is None or self._client.is_closed or
                self._client_loop is None or self._client_loop is not current_loop):
            if self._client and not self._client.is_closed:
                try:
                    await self._client.aclose()
                except Exception:
                    pass
            self._client = httpx.AsyncClient(
                base_url=self.base_url,
                headers={
                    "Authorization": f"Bearer {self.api_key}",
                    "Content-Type": "application/json",
                },
                timeout=httpx.Timeout(300.0, connect=30.0),
            )
            self._client_loop = current_loop
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
        if not self.api_key:
            raise ProviderCapabilityError("Cloud API key is not configured")
        try:
            client = await self._get_client()
            response = await client.get("/api/tags")
            response.raise_for_status()
            data = response.json()
            return data.get("models", [])
        except Exception as e:
            _raise_sanitized(
                RuntimeError(f"Failed to list models: {_friendly_http_error(e)}"),
                _safe_httpx_cause(e),
            )

    async def ensure_capabilities(self, model_id: Optional[str] = None) -> ProviderCapabilityProfile:
        """Fail closed until the Cloud model proves native tool support.

        Per-model keyed synchronization: concurrent cold starts for the SAME
        model serialize on a narrow per-model lock; different models never
        block each other and never poison each other's cache entry. The
        cached profile is immutable and replaced atomically.
        """
        target_model = model_id or self.selected_model
        if not self.api_key:
            raise ProviderCapabilityError("Cloud API key is not configured")
        if not target_model:
            raise ProviderCapabilityError("No Cloud model has been selected")

        # Fast path: already cached for this model
        cached = self._capability_profiles.get(target_model)
        if cached is not None:
            return cached

        # Negative cache per model
        failed_at = self._capability_failed_at_map.get(target_model)
        if failed_at is not None:
            remaining = _CAPABILITY_FAILURE_COOLDOWN_SECONDS - (
                time.monotonic() - failed_at
            )
            if remaining > 0:
                raise ProviderCapabilityError(
                    f"Cloud provider unavailable (rate limit or outage); "
                    f"retry in {int(remaining)}s"
                )

        # Narrow per-model serialization — only same-model probes contend.
        lock = self._capability_lock_for(target_model)
        async with lock:
            # Double-check after acquiring lock (another coroutine may have filled)
            cached2 = self._capability_profiles.get(target_model)
            if cached2 is not None:
                return cached2
            failed_at2 = self._capability_failed_at_map.get(target_model)
            if failed_at2 is not None:
                remaining2 = _CAPABILITY_FAILURE_COOLDOWN_SECONDS - (
                    time.monotonic() - failed_at2
                )
                if remaining2 > 0:
                    raise ProviderCapabilityError(
                        f"Cloud provider unavailable (rate limit or outage); "
                        f"retry in {int(remaining2)}s"
                    )

            try:
                models = await self.list_models()
            except RuntimeError as exc:
                self._capability_failed_at_map[target_model] = time.monotonic()
                if target_model == self.selected_model:
                    self._capability_failed_at = self._capability_failed_at_map[target_model]
                _raise_sanitized(
                    ProviderCapabilityError(f"Model listing failed: {exc}"),
                    exc,
                )

            model_ids = {
                mid
                for model in models
                for mid in (model.get("name"), model.get("model"))
                if isinstance(mid, str)
            }
            base_model = target_model.rsplit(":", 1)[0]
            if target_model not in model_ids and base_model not in model_ids:
                raise ProviderCapabilityError(
                    f"Selected model '{target_model}' is unavailable"
                )

            probe_tool_name = "sythion_capability_probe"
            payload = {
                "model": target_model,
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
                "options": {"num_ctx": NUM_CTX},
                "stream": False,
            }
            self._enforce_final_request_payload_byte_envelope(payload)
            try:
                client = await self._get_client()
                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
                tool_calls = response.json().get("message", {}).get("tool_calls", [])
            except httpx.HTTPError as exc:
                self._capability_failed_at_map[target_model] = time.monotonic()
                if target_model == self.selected_model:
                    self._capability_failed_at = self._capability_failed_at_map[target_model]
                _raise_sanitized(
                    ProviderCapabilityError(
                        f"Native tool probe failed: {_friendly_http_error(exc)}"
                    ),
                    _safe_httpx_cause(exc),
                )

            if len(tool_calls) != 1 or tool_calls[0].get("function", {}).get("name") != probe_tool_name:
                raise ProviderCapabilityError("Selected model did not return one valid native tool call")

            profile = ProviderCapabilityProfile(
                model_id=target_model,
                native_tool_calls=True,
            )
            # Immutable replacement — per-model entry, no cross-model poison.
            self._capability_profiles[target_model] = profile
            if target_model == self.selected_model:
                self._capability_profile = profile
            return profile

    def _request_payload_byte_limit(self, configured=None):
        raw = self.max_request_payload_bytes if configured is None else configured
        if raw is None:
            return 0
        try:
            limit = int(raw)
        except (TypeError, ValueError):
            return 0
        return limit if limit > 0 else 0

    def _enforce_final_request_payload_byte_envelope(self, payload, configured=None):
        limit = self._request_payload_byte_limit(configured)
        if limit <= 0:
            return None
        request = httpx.Request("POST", "/api/chat", json=payload)
        actual = len(request.content)
        if actual > limit:
            raise OllamaRequestPayloadTooLargeError(
                "Final HTTP request payload exceeds configured byte envelope: "
                f"{actual} bytes > {limit} bytes (conservative request-body bound; "
                "not a token or context-window fit claim)",
                payload_bytes=actual,
                max_payload_bytes=limit,
                reason="payload_byte_envelope_exceeded",
            )
        return actual

    async def execute_tool_call(
        self,
        messages: List[Dict[str, Any]],
        tools: List[Dict[str, Any]],
        max_tokens: int = 4000,
        model_id: Optional[str] = None,
    ) -> Dict[str, Any]:
        """Execute a tool call via Ollama Cloud.

        Args:
            messages: Conversation messages
            tools: Tool definitions in OpenAI format
            max_tokens: Maximum output tokens (None means the default 4000).

        Returns:
            Model response with tool calls

        Raises:
            RuntimeError: If the request fails
        """
        output_tokens = 4000 if max_tokens is None else max_tokens
        payload = {
            "model": model_id or self.selected_model,
            "messages": messages,
            "tools": tools,
            "options": {"num_predict": output_tokens, "num_ctx": NUM_CTX},
            "tool_choice": "auto",
            "stream": False,
        }
        self._enforce_final_request_payload_byte_envelope(payload)

        max_network_retries = 2
        for attempt in range(max_network_retries):
            try:
                await self.ensure_capabilities()
                client = await self._get_client()

                response = await client.post("/api/chat", json=payload)
                response.raise_for_status()
                return response.json()

            except OllamaRequestPayloadTooLargeError:
                raise
            except ProviderCapabilityError:
                raise
            except httpx.HTTPStatusError as e:
                status_code = getattr(getattr(e, "response", None), "status_code", None)
                msg = (
                    f"HTTP error {status_code}"
                    if status_code is not None
                    else "HTTP error"
                )
                if status_code == 429:
                    _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
                elif status_code == 401:
                    _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
                else:
                    _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
            except httpx.ReadTimeout as e:
                _raise_sanitized(RuntimeError(
                    "Request timed out after 300s - the model is taking too long to respond"
                ), _safe_httpx_cause(e))
            except httpx.ConnectTimeout as e:
                if attempt < max_network_retries - 1:
                    logger.warning(
                        "Transient network error (%s) on Cloud LLM call; retrying in 1.5s with fresh client...",
                        type(e).__name__,
                    )
                    if self._client and not self._client.is_closed:
                        await self._client.aclose()
                    self._client = None
                    import asyncio
                    await asyncio.sleep(1.5)
                    continue
                _raise_sanitized(RuntimeError("Connection timed out"), _safe_httpx_cause(e))
            except (httpx.ReadError, httpx.RemoteProtocolError, httpx.ConnectError) as e:
                if attempt < max_network_retries - 1:
                    logger.warning(
                        "Transient network error (%s) on Cloud LLM call; retrying in 1.5s with fresh client...",
                        type(e).__name__,
                    )
                    if self._client and not self._client.is_closed:
                        await self._client.aclose()
                    self._client = None
                    import asyncio
                    await asyncio.sleep(1.5)
                    continue
                _raise_sanitized(RuntimeError("Connection error after retry"), _safe_httpx_cause(e))
            except Exception as e:
                if isinstance(e, OllamaRequestPayloadTooLargeError):
                    raise
                _raise_sanitized(RuntimeError(f"Request failed: {type(e).__name__}"), _safe_httpx_cause(e))



    async def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        timeout: float = 300.0,
        model_id: Optional[str] = None,
        temperature: Optional[float] = None,
        think=None,
        format=None,
    ) -> Dict[str, Any]:
        """Plain chat completion with no tool calling.

        Used by background services (e.g. memory consolidation) that need a
        free-form LLM judgment rather than a decision loop tool call.
        ``model_id`` targets a specific registered model (evaluation);
        defaults to the configured selection. ``temperature``, when provided,
        is forwarded under ``options.temperature``. ``max_tokens=None`` means
        the default 4000.
        """
        try:
            client = await self._get_client()
            output_tokens = 4000 if max_tokens is None else max_tokens
            payload = {
                "model": model_id or self.selected_model,
                "messages": messages,
                "options": {"num_predict": output_tokens, "num_ctx": NUM_CTX},
                "stream": False,
            }
            if temperature is not None:
                payload["options"]["temperature"] = temperature
            if think is not None:
                payload["think"] = think
            if format is not None:
                payload["format"] = format
            self._enforce_final_request_payload_byte_envelope(payload)
            response = await client.post(
                "/api/chat",
                json=payload,
                timeout=httpx.Timeout(timeout, connect=30.0),
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            status_code = getattr(getattr(e, "response", None), "status_code", None)
            msg = (
                f"HTTP error {status_code}"
                if status_code is not None
                else "HTTP error"
            )
            if status_code == 429:
                _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
            elif status_code == 401:
                _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
            else:
                _raise_sanitized(RuntimeError(msg), _safe_httpx_cause(e))
        except httpx.ReadTimeout as e:
            _raise_sanitized(RuntimeError(
                f"Request timed out after {timeout}s - the model is taking too long to respond"
            ), _safe_httpx_cause(e))
        except httpx.ConnectTimeout as e:
            _raise_sanitized(RuntimeError("Connection timed out"), _safe_httpx_cause(e))
        except httpx.ConnectError as e:
            _raise_sanitized(RuntimeError("Connection error"), _safe_httpx_cause(e))
        except Exception as e:
            if isinstance(e, OllamaRequestPayloadTooLargeError):
                raise
            _raise_sanitized(RuntimeError(f"Request failed: {type(e).__name__}"), _safe_httpx_cause(e))


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


class OllamaCloudProvider:
    """Provider-neutral facade over the Ollama Cloud adapter.

    All Ollama-specific knowledge (endpoint names, request fields, retry
    semantics, capability probing) stays in the adapter; higher layers see
    only runtime.model_provider.ModelProvider.
    """

    @property
    def _adapter(self) -> "OllamaCloudAdapter":
        return get_adapter()

    def provider_id(self) -> str:
        return "ollama-cloud"

    def selected_model_id(self) -> Optional[str]:
        from ..core.config import resolve_selected_model

        return resolve_selected_model()

    def list_models(self) -> List[Dict[str, Any]]:
        """Sync facade over the async adapter (provider-neutral contract)."""
        import asyncio
        import concurrent.futures

        adapter = get_adapter()
        try:
            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        if loop is not None:
            with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                return pool.submit(lambda: asyncio.run(adapter.list_models())).result(timeout=60)
        return asyncio.run(adapter.list_models())

    def health(self) -> "ModelHealth":
        """Structured health via the existing capability probe (never raises).

        Uses the adapter's negative-cached probe so repeated calls do not
        hammer a rate-limited or offline provider.
        """
        from ..runtime.model_provider import ModelHealth

        if not resolve_ollama_api_key(settings.ollama_api_key):
            return ModelHealth(self.provider_id(), ModelHealth.UNAVAILABLE,
                               "api key not configured")
        try:
            import asyncio

            loop = asyncio.get_running_loop()
        except RuntimeError:
            loop = None
        try:
            if loop is not None:
                import concurrent.futures

                with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
                    ok = pool.submit(
                        lambda: asyncio.run(get_adapter().check_health())
                    ).result(timeout=30)
            else:
                ok = asyncio.run(get_adapter().check_health())
        except Exception as exc:  # pragma: no cover - defensive
            return ModelHealth(self.provider_id(), ModelHealth.UNKNOWN,
                               str(exc)[:200])
        status = ModelHealth.HEALTHY if ok else ModelHealth.UNAVAILABLE
        detail = "" if ok else "capability probe failed (rate limit/outage/stale model)"
        return ModelHealth(self.provider_id(), status, detail)

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        timeout: float = 300.0,
        temperature: Optional[float] = None,
        think=None,
        format=None,
    ) -> Dict[str, Any]:
        return await get_adapter().chat(
            messages,
            max_tokens=max_tokens,
            timeout=timeout,
            temperature=temperature,
            think=think,
            format=format,
        )


_provider: Optional[OllamaCloudProvider] = None


def get_default_provider() -> OllamaCloudProvider:
    global _provider
    if _provider is None:
        _provider = OllamaCloudProvider()
    return _provider


def format_tools_for_ollama(tools: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """Format tool definitions for Ollama Cloud API.

    Args:
        tools: List of tool definitions with name, description, and input_schema

    Returns:
        List of tools in OpenAI function calling format
    """
    formatted = []
    for tool in tools:
        # Convert Pydantic schema to JSON Schema, or use raw dict schema directly
        properties = {}
        required = []
        
        schema = tool.get('input_schema')
        if isinstance(schema, dict) and 'properties' in schema:
            # Raw JSON Schema dict (from DB-loaded tools)
            properties = schema.get('properties', {})
            required = schema.get('required', [])
        elif hasattr(schema, 'model_fields'):
            for field_name, field_info in schema.model_fields.items():
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
                        item_type = getattr(ann, '__args__', (None,))[0]
                        if item_type == str:
                            prop["items"] = {"type": "string"}
                        elif item_type == int:
                            prop["items"] = {"type": "integer"}
                        elif item_type == float:
                            prop["items"] = {"type": "number"}
                
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
