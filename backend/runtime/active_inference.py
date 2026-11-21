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
import logging
import re
import secrets
from dataclasses import dataclass
from typing import Any, Dict

from backend.core import profile

logger = logging.getLogger(__name__)

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


_PENDING_ACTIVATION_KEY = "pending_inference_selection"
# Ollama operations use a SEPARATE key so the credential-bound cloud pending
# record format stays untouched. Damaged/ambiguous records fail closed.
_PENDING_OLLAMA_KEY = "pending_ollama_selection"
_PENDING_OLLAMA_KINDS = ("activate", "restore", "select")


def clear_pending_selections() -> Dict[str, Any]:
    """Profile patch that clears every queued activation, of either kind.

    Direct activation/restore paths merge this into their own profile write so
    a superseded queued choice cannot fire later on top of the new selection.
    Only keys that actually exist are included: this keeps an unconfigured
    profile byte-for-byte unchanged, which downstream consumers rely on.
    """
    try:
        state = profile.load_profile()
    except Exception:
        # A profile we cannot read cannot be safely written either; include both
        # keys so a caller that ignores the read error still clears the records.
        return {_PENDING_ACTIVATION_KEY: None, _PENDING_OLLAMA_KEY: None}
    return {
        key: None
        for key in (_PENDING_ACTIVATION_KEY, _PENDING_OLLAMA_KEY)
        if key in state
    }


def _build_verified_binding(db: Any, provider_id: str, model_id: str) -> Dict[str, Any]:
    """Validate the owner's verified choice and build its credential-bound
    activation record. Pure with respect to active state: it writes nothing.
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
    return {
        "version": _VERSION,
        "provider_id": provider_id,
        "model_id": model_id,
        "nonce": nonce,
        "mac": _active_mac(provider_id, model_id, nonce, credential),
    }


def activate_verified_cloud_selection(db: Any, provider_id: str, model_id: str) -> ActiveInferenceSelection:
    """Activate after real text AND synthetic function-call evidence is valid.

    This is a local backend-state change; it sends NO inference request,
    touches no task state and grants no execution permission. Existing previews
    are deliberately inert until this explicit authenticated operation.
    """
    binding = _build_verified_binding(db, provider_id, model_id)
    # Atomic merge: do not overwrite existing profile fields, Ollama model,
    # separate staged preview or other persisted Sythion configuration.
    try:
        profile.save_profile({
            "active_inference_selection": binding,
            # Clear superseded queued choices of either kind: the owner just
            # made a direct, current cloud selection.
            **clear_pending_selections(),
        })
    except Exception:
        raise ActiveInferenceUnavailable("Could not save active provider") from None
    return get_active_inference_selection()


def has_in_flight_task(db: Any) -> bool:
    """Whether a task currently holds a model call bound to the active provider.

    Only CREATED (queued to start) and RUNNING states can have a reasoning call
    in flight. Waiting/paused/interrupted tasks have no active model call and
    must not block a legitimate model change.
    """
    try:
        from backend.models.database import Task
        from backend.api.schemas import TaskState
        return (db.query(Task.id)
                .filter(Task.state.in_((TaskState.CREATED.value, TaskState.RUNNING.value)))
                .first()) is not None
    except Exception:
        # Uncertain task state fails closed: treat as in flight so a switch is
        # deferred rather than applied on top of a possibly-live binding.
        return True


def queue_pending_activation(db: Any, provider_id: str, model_id: str) -> None:
    """Hold a verified model choice to apply automatically when idle.

    Lazy-user contract: once the owner confirms a verified model, they should
    not have to return later to retry. If a task has a model call in flight,
    store the credential-bound binding now (evidence is current at this moment)
    and let :func:`maybe_apply_pending_activation` promote it when work settles.
    """
    binding = _build_verified_binding(db, provider_id, model_id)
    try:
        profile.save_profile({
            _PENDING_ACTIVATION_KEY: binding,
            # The latest owner choice wins: drop any queued Ollama operation.
            _PENDING_OLLAMA_KEY: None,
        })
    except Exception:
        raise ActiveInferenceUnavailable("Could not schedule active provider") from None


def _valid_pending_ollama(raw: Any) -> bool:
    """Fail-closed validation of a queued Ollama operation record."""
    return (
        isinstance(raw, dict)
        and set(raw) == {"version", "model_id", "kind"}
        and type(raw.get("version")) is int
        and raw["version"] == _VERSION
        and raw.get("kind") in _PENDING_OLLAMA_KINDS
        and type(raw.get("model_id")) is str
        and _CLOUD_MODEL_PATTERN.fullmatch(raw["model_id"]) is not None
    )


def queue_pending_ollama_activation(
    db: Any, *, model_id: str, kind: str,
) -> None:
    """Hold an Ollama selection to apply automatically when idle.

    All verification (catalog membership and, for ``activate``, the
    authenticated chat proof) already happened in the request handler. This
    only persists the intended target; it never mutates active state.

    ``restore`` keeps the current selected model and only returns the active
    sentinel to Ollama; ``select`` records an unverified choice; ``activate``
    records a verified one.
    """
    if (type(model_id) is not str
            or _CLOUD_MODEL_PATTERN.fullmatch(model_id) is None
            or kind not in _PENDING_OLLAMA_KINDS):
        raise ActiveInferenceUnavailable("Invalid Ollama selection")
    record = {"version": _VERSION, "model_id": model_id, "kind": kind}
    try:
        profile.save_profile({
            _PENDING_OLLAMA_KEY: record,
            # The latest owner choice wins across providers as well.
            _PENDING_ACTIVATION_KEY: None,
        })
    except Exception:
        raise ActiveInferenceUnavailable("Could not schedule Ollama selection") from None


def _refresh_live_ollama_model(model_id: str) -> None:
    """Point the shared Ollama adapter at an applied model (best effort)."""
    try:
        from backend.adapters.ollama_adapter import get_adapter
        adapter = get_adapter()
        if adapter is not None:
            adapter.selected_model = model_id
            adapter._capability_profile = None
    except Exception:
        # The persisted profile is authoritative; a live-refresh failure must
        # never turn an applied selection into a failed one.
        logger.warning("Could not refresh the live Ollama model selection", exc_info=True)


def _apply_pending_ollama(db: Any, raw: Dict[str, Any]) -> bool:
    """Commit an already-verified Ollama choice; mirror the immediate path.

    Returns False (leaving active state untouched) on a damaged record or a
    failed/unstable commit. Keeps the pending record on failure so a later
    terminal transition can retry instead of silently losing the owner's choice.
    """
    from backend.core.config import resolve_selected_model
    from backend.core.settings_store import get_setting, set_setting

    kind, model_id = raw["kind"], raw["model_id"]
    if kind == "restore":
        try:
            profile.save_profile({
                "active_inference_selection": {
                    "version": 1, "provider_id": "ollama-cloud",
                },
                **clear_pending_selections(),
            })
        except Exception:
            return False
        _refresh_live_ollama_model(model_id)
        return True

    previous_profile = profile.load_profile_strict()
    previous_selection = previous_profile.get("active_inference_selection")
    previous_model = previous_profile.get("selected_model", "")
    previous_verified = get_setting(db, "ollama_inference_verified_model")
    try:
        patch: Dict[str, Any] = {"selected_model": model_id, **clear_pending_selections()}
        if kind == "activate":
            patch["active_inference_selection"] = {
                "version": 1, "provider_id": "ollama-cloud",
            }
            set_setting(db, "ollama_inference_verified_model", model_id)
        else:  # "select" — unverified; mirror the onboarding/models path.
            set_setting(db, "ollama_inference_verified_model", "")
        profile.save_profile(patch)
        db.commit()
        if resolve_selected_model() != model_id:
            raise ValueError("Selected model overridden by runtime configuration")
    except Exception:
        try:
            db.rollback()
            profile.save_profile({
                "selected_model": previous_model,
                "active_inference_selection": previous_selection
                    if previous_selection is not None else {
                        "version": 1, "provider_id": "ollama-cloud",
                    },
                # Keep the choice queued so a later terminal transition retries.
                _PENDING_OLLAMA_KEY: raw,
            })
            if previous_verified is not None:
                set_setting(db, "ollama_inference_verified_model", previous_verified)
                db.commit()
        except Exception:
            logger.error("Deferred Ollama selection restoration failed", exc_info=True)
        return False
    _refresh_live_ollama_model(model_id)
    return True


def maybe_apply_pending_activation(db: Any) -> bool:
    """Apply queued model choices once no task can hold the old binding.

    Called from the task terminal transition. Handles both a credential-bound
    cloud selection and the Ollama restore/select/activate operations, each
    under its own key. Returns True when a pending choice was promoted. Fails
    closed: damaged or ambiguous records are ignored and a still-in-flight
    task defers the switch rather than breaking its binding.
    """
    state = profile.load_profile()
    cloud_raw = state.get(_PENDING_ACTIVATION_KEY)
    ollama_raw = state.get(_PENDING_OLLAMA_KEY)
    cloud_ok = isinstance(cloud_raw, dict) and set(cloud_raw) == {
        "version", "provider_id", "model_id", "nonce", "mac",
    }
    ollama_ok = _valid_pending_ollama(ollama_raw)
    if not cloud_ok and not ollama_ok:
        return False
    # Two queued choices cannot both represent the owner's intent. Only our own
    # code writes these keys and it always clears the other kind, so this means
    # external damage — refuse to guess which one to apply.
    if cloud_ok and ollama_ok:
        return False
    try:
        from backend.models.database import Task
        from backend.api.schemas import TaskState
        blocking = (db.query(Task.id)
                    .filter(Task.state.in_((TaskState.CREATED.value, TaskState.RUNNING.value)))
                    .first())
        if blocking is not None:
            return False
        if cloud_ok:
            profile.save_profile({
                "active_inference_selection": cloud_raw,
                _PENDING_ACTIVATION_KEY: None,
            })
            return True
        return _apply_pending_ollama(db, ollama_raw)
    except Exception:
        return False


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


def _is_legacy_ollama_default(selected: ActiveInferenceSelection) -> bool:
    """The unconfigured sentinel keeps the native Ollama provider.

    A bound ollama-cloud selection has an explicit model ID and HMAC binding,
    so it follows the exact same compatible-provider path as every other cloud
    rather than falling back to the Ollama-specific transport.
    """
    return selected.provider_id == "ollama-cloud" and not selected.model_id


def get_active_chat_provider() -> Any:
    selected = get_active_inference_selection()
    if _is_legacy_ollama_default(selected):
        from backend.runtime.model_provider import get_default_provider
        return get_default_provider()
    return ActiveCompatibleChatProvider(selected)


def get_active_decision_client() -> Any:
    selected = get_active_inference_selection()
    if _is_legacy_ollama_default(selected):
        from backend.runtime.model_client import OllamaCloudModelClient
        return OllamaCloudModelClient()
    return ActiveCompatibleDecisionClient(selected)
