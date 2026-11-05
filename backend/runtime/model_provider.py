"""Provider-neutral model layer (Phase 5).

The reasoning layer and all higher components depend on these Protocols;
Ollama-specific endpoints/fields/retry semantics live ONLY in the concrete
provider implementation. No routing, no fallback, no second provider — this
is the *boundary* where routing can later be added without touching the
Planner, Main Agent, or Goal system.
"""

from __future__ import annotations

from typing import Any, Dict, List, Optional, Protocol, runtime_checkable


@runtime_checkable
class ModelProvider(Protocol):
    """Everything higher layers may know about the configured provider."""

    def provider_id(self) -> str:
        """Stable provider identifier, e.g. 'ollama-cloud'."""
        ...

    def selected_model_id(self) -> Optional[str]:
        """Currently configured model id (None until onboarded)."""
        ...

    def list_models(self) -> List[Dict[str, Any]]:
        """Models the provider currently offers. Raises on outage."""
        ...

    def health(self) -> "ModelHealth":
        """Structured health; never raises."""
        ...

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        max_tokens: int = 4000,
        timeout: float = 300.0,
        temperature: Optional[float] = None,
        **kwargs: Any,
    ) -> Dict[str, Any]:
        """Free-form completion (used by background services like memory
        consolidation that need a judgment, not a tool-call decision)."""
        ...


class ModelHealth:
    """Deterministic provider/model health snapshot."""

    __slots__ = ("provider", "status", "detail")

    HEALTHY = "HEALTHY"
    UNAVAILABLE = "UNAVAILABLE"
    DEGRADED = "DEGRADED"
    UNKNOWN = "UNKNOWN"

    def __init__(self, provider: str, status: str, detail: str = ""):
        assert status in {self.HEALTHY, self.UNAVAILABLE, self.DEGRADED,
                          self.UNKNOWN}, f"invalid health status {status}"
        self.provider = provider
        self.status = status
        self.detail = detail

    def to_dict(self) -> Dict[str, str]:
        return {"provider": self.provider, "status": self.status,
                "detail": self.detail}


# Neutral access point: higher layers import THIS, never the adapter module.
_default_provider: Optional[Any] = None


def get_default_provider() -> Any:
    """Return the configured provider implementation (lazy adapter load)."""
    global _default_provider
    if _default_provider is None:
        from backend.adapters.ollama_adapter import (
            get_default_provider as _impl_get,
        )

        _default_provider = _impl_get()
    return _default_provider