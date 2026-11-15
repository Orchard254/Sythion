"""Production reasoning-provider selection; distinct from cloud previews.

Sythion's identity, task state, approvals and evidence remain in its runtime.
An active selection controls only WHERE bounded reasoning requests are sent.
It does not authorize actions. A staged preview or a successful model-name
lookup is never an active selection.

The selected cloud provider is bound to its exact build-scoped credential via
a domain-separated HMAC. A key rotation, malformed profile, or model switch
fails closed rather than silently switching to an unrelated provider.
"""
from __future__ import annotations

import hashlib
import hmac
import json
import re
import secrets
from dataclasses import dataclass
from typing import Any, Dict

from backend.core import profile

_VERSION = 1
_CLOUD_MODEL_PATTERN = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,179}\Z")
_MISSING = object()


class ActiveInferenceUnavailable(RuntimeError):
    """Active model selection is missing required evidence or has changed."""


@dataclass(frozen=True)
class ActiveInferenceSelection:
    provider_id: str
    model_id: str


def _active_mac(provider_id: str, model_id: str, nonce: str, credential: str) -> str:
    payload = json.dumps(
        ["sythion-active-reasoning-v1", provider_id, model_id, nonce],
        separators=(",", ":"), ensure_ascii=True,
    ).encode("utf-8")
    return hmac.new(credential.encode("utf-8"), payload, hashlib.sha256).hexdigest()


def _validate_cloud(provider_id: str, model_id: str) -> None:
    from backend.adapters.openai_compatible_adapter import PROVIDERS
    if (type(provider_id) is not str or provider_id not in PROVIDERS or
        type(model_id) is not str or _CLOUD_MODEL_PATTERN.fullmatch(model_id) is None):
        raise ActiveInferenceUnavailable("Invalid active provider selection")


def get_active_inference_selection() -> ActiveInferenceSelection:
    """Resolve active provider without accessing a staged *preview* candidate.

    An absent record preserves the existing production Ollama default. A
    present but damaged record, absent/rotated credential or invalid MAC
    raises. No credentials or MACs are returned to API consumers.
    """
    try:
        state = profile.load_profile_strict()
        raw = state.get("active_inference_selection", _MISSING)
        if raw is _MISSING:
            return ActiveInferenceSelection("ollama-cloud", "")
        if raw == {"version": _VERSION, "provider_id": "ollama-cloud"}:
            return ActiveInferenceSelection("ollama-cloud", "")
        if not isinstance(raw, dict) or set(raw) != {
            "version", "provider_id", "model_id", "nonce", "mac",
        } or type(raw.get("version")) is not int or raw["version"] != _VERSION:
            raise ActiveInferenceUnavailable("Invalid active provider selection")
        provider_id, model_id = raw["provider_id"], raw["model_id"]
        _validate_cloud(provider_id, model_id)
        nonce, supplied_mac = raw["nonce"], raw["mac"]
        if (type(nonce) is not str or re.fullmatch(r"[0-9a-f]{32}", nonce) is None or
            type(supplied_mac) is not str or re.fullmatch(r"[0-9a-f]{64}", supplied_mac) is None):
            raise ActiveInferenceUnavailable("Invalid active provider binding")
        from backend.core.secrets import resolve_compatible_provider_key
        credential = resolve_compatible_provider_key(provider_id)
        if not credential:
            raise ActiveInferenceUnavailable("Active provider credential unavailable")
        expected = _active_mac(provider_id, model_id, nonce, credential)
        if not hmac.compare_digest(expected, supplied_mac):
            raise ActiveInferenceUnavailable("Active provider credential changed")
        return ActiveInferenceSelection(provider_id, model_id)
    except ActiveInferenceUnavailable:
        raise
    except Exception:
        # Corrupt/unavailable profile or ambiguous secret context is not an
        # invitation to use a different provider without owner consent.
        raise ActiveInferenceUnavailable("Active provider selection unavailable") from None


def activate_verified_cloud_selection(db: Any, provider_id: str, model_id: str) -> ActiveInferenceSelection:
    """Activate after real text AND synthetic function-call evidence is valid.

    This is a local backend-state change; it sends NO inference request,
    touches no task state and grants no execution permission. Existing previews
    are deliberately inert until this explicit authenticated operation.
    """
    _validate_cloud(provider_id, model_id)
    from backend.core.cloud_text_evidence import is_text_evidence_current
    from backend.core.cloud_probe_evidence import is_probe_evidence_current
    from backend.core.cloud_assistant_evidence import is_assistant_contract_evidence_current
    from backend.core.secrets import resolve_compatible_provider_key

    snapshot = profile.get_staged_cloud_snapshot()
    if not snapshot or any(snapshot.get(k) != v for k, v in (
        ("provider_id", provider_id), ("model_id", model_id),
    )):
        raise ActiveInferenceUnavailable("Choose and verify this exact model first")
    if not is_text_evidence_current(db) or not is_probe_evidence_current(db):
        raise ActiveInferenceUnavailable("Text and function-call qualification required")
    if not is_assistant_contract_evidence_current(db):
        raise ActiveInferenceUnavailable("Structured Assistant qualification required")
    credential = resolve_compatible_provider_key(provider_id)
    if not credential:
        raise ActiveInferenceUnavailable("Provider credential unavailable")

    nonce = secrets.token_hex(16)
    binding = {
        "version": _VERSION,
        "provider_id": provider_id,
        "model_id": model_id,
        "nonce": nonce,
        "mac": _active_mac(provider_id, model_id, nonce, credential),
    }
    # Atomic merge: do not overwrite existing profile fields, Ollama model,
    # separate staged preview or other persisted Sythion configuration.
    try:
        profile.save_profile({"active_inference_selection": binding})
    except Exception:
        raise ActiveInferenceUnavailable("Could not save active provider") from None
    return get_active_inference_selection()


def require_active_selection_unchanged(expected: ActiveInferenceSelection) -> None:
    if get_active_inference_selection() != expected:
        raise ActiveInferenceUnavailable("Provider selection changed during inference")


class ActiveCompatibleChatProvider:
    """One explicitly selected provider per call; close sockets even on error."""
    def __init__(self, selection: ActiveInferenceSelection):
        self.selection = selection

    def provider_id(self) -> str:
        return self.selection.provider_id

    def selected_model_id(self) -> str:
        return self.selection.model_id

    async def chat(self, messages, max_tokens=4000, timeout=60.0, temperature=None):
        from backend.runtime.compatible_chat_provider import CompatibleCloudChatProvider
        require_active_selection_unchanged(self.selection)
        client = CompatibleCloudChatProvider(self.selection.provider_id, self.selection.model_id)
        try:
            result = await client.chat(
                messages=messages, max_tokens=max_tokens,
                timeout=timeout, temperature=temperature,
            )
            require_active_selection_unchanged(self.selection)
            return result
        finally:
            await client.close()


class ActiveCompatibleDecisionClient:
    """Same selected identity for each structured decision; never execute tools."""
    def __init__(self, selection: ActiveInferenceSelection):
        self.selection = selection

    def provider_name(self) -> str:
        return self.selection.provider_id

    async def decide(self, messages, tools, max_tokens=4000):
        from backend.runtime.model_client import CompatibleCloudModelClient
        require_active_selection_unchanged(self.selection)
        # Activation was allowed only after text + tool proofs, and the
        # persisted active binding is revalidated against the current secret
        # before and after every decision. Avoid a second hidden provider
        # request to repeat the same synthetic capability probe on each turn.
        # Actual decision structure and task permissions remain validated.
        client = CompatibleCloudModelClient(
            self.selection.provider_id, self.selection.model_id,
            preverified_tool_capability=True,
        )
        try:
            result = await client.decide(messages, tools, max_tokens=max_tokens)
            require_active_selection_unchanged(self.selection)
            return result
        finally:
            await client.close()


def get_active_chat_provider() -> Any:
    selected = get_active_inference_selection()
    if selected.provider_id == "ollama-cloud":
        from backend.runtime.model_provider import get_default_provider
        return get_default_provider()
    return ActiveCompatibleChatProvider(selected)


def get_active_decision_client() -> Any:
    selected = get_active_inference_selection()
    if selected.provider_id == "ollama-cloud":
        from backend.runtime.model_client import OllamaCloudModelClient
        return OllamaCloudModelClient()
    return ActiveCompatibleDecisionClient(selected)
