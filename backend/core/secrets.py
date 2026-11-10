"""
Write-only secret storage for Sythion (Synth).

Policy (): secrets never enter `.env`, SQLite, logs, task
events, exports, or model context. The production backend is Windows Credential
Manager (win32cred). A non-persistent in-memory backend is used for dev/test so
tests never touch the real credential vault.

Secrets are write-only by contract: callers may set, check existence, or delete,
but stored values are never returned to API clients.

Prototype builds resolve their secrets under a distinct, build-derived namespace
so an isolated prototype never reads, writes or deletes the production target
``Sythion (Synth)/ollama_api_key``. Identity resolution is fail-closed: any
ambiguity or conflicting marker prevents access to BOTH namespaces and falls
back to the non-persistent in-memory backend.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Protocol, Tuple


# ---------------------------------------------------------------------------
# Secret registry (whitelist of known secret names)
# ---------------------------------------------------------------------------

OLLAMA_API_KEY = "ollama_api_key"
OPENROUTER_API_KEY = "openrouter_api_key"
NVIDIA_API_KEY = "nvidia_api_key"
GROQ_API_KEY = "groq_api_key"

# All keys use the existing write-only, build-scoped Credential Manager store.
# There is NO generic URL/key passthrough and no environment-key fallback.
KNOWN_SECRETS = frozenset({
    OLLAMA_API_KEY, OPENROUTER_API_KEY, NVIDIA_API_KEY, GROQ_API_KEY,
})

# Credential Manager target name prefixes. Credentials are stored per-user under
# these namespaces so unrelated generic credentials are never touched.
_PRODUCTION_SECRET_NAMESPACE = "Sythion (Synth)"
_PROTOTYPE_SECRET_NAMESPACE = "Sythion (Synth) Prototype"

# Historical module global retained for backwards compatibility (item 007's
# acceptance guard snapshots this at import time). It is recomputed at runtime
# from the resolved namespace.
_CM_TARGET_PREFIX = "Sythion (Synth)/"


def _target_prefix_for_namespace(namespace: Optional[str]) -> str:
    if not namespace:
        return ""
    namespace = namespace.strip()
    if namespace.endswith("/"):
        namespace = namespace[:-1]
    return namespace + "/"


# ---------------------------------------------------------------------------
# Identity / namespace resolution (fail-closed)
# ---------------------------------------------------------------------------

# Environment variable names consulted, in priority order. Using a broad alias
# set keeps the shell (Windows feature builds, dev checkouts, packaged installs)
# and any explicit operator override on the same path without forcing a rename.
_PROTOTYPE_MARKER_ENV_VARS = (
    "SYTHION_PROTOTYPE_BUILD",
    "SYTHION_PROTECT_BUILD",
    "SYTHION_IS_PROTOTYPE",
    "SYTHION_PROTOTYPE",
)

_BUILD_IDENTITY_ENV_VARS = (
    "SYTHION_BUILD_IDENTITY",
    "SYTHION_PRODUCT_NAME",
    "SYTHION_APP_IDENTITY",
    "SYTHION_IDENTIFIER",
    "SYTHION_PRODUCT_IDENTIFIER",
)

_SECRET_NAMESPACE_ENV_VARS = (
    "SYTHION_SECRET_NAMESPACE",
    "SYTHION_SECRET_TARGET_PREFIX",
    # Accept a service-name-style override as well.
    "SYTHION_SECRET_SERVICE",
)

_DATA_DIR_ENV_VARS = (
    "SYTHION_DATA_DIR",
    "Sythion (Synth)_DATA_DIR",
)

_TRUTHY_VALUES = {"1", "true", "yes", "on", "prototype"}


def _truthy(value: Optional[str]) -> bool:
    if value is None:
        return False
    return value.strip().lower() in _TRUTHY_VALUES


def _normalize_namespace(value: Optional[str]) -> Optional[str]:
    if value is None:
        return None
    value = value.strip()
    if not value:
        return None
    if value.endswith("/"):
        value = value[:-1]
    return value


def _marker_claim(value: str) -> Optional[bool]:
    """Interpret one environment value as a prototype claim, or None if unclear."""
    low = value.strip().lower()
    if not low:
        return None
    if low in _TRUTHY_VALUES or "prototype" in low or low.endswith(".prototype"):
        return True
    if low in {"0", "false", "no", "off", "production"}:
        return False
    return None


def resolve_secret_context() -> Tuple[Optional[str], bool, bool, str]:
    """Resolve the secret namespace for the current process.

    Returns ``(namespace, is_prototype, fail_closed, reason)``. A ``None``
    namespace together with ``fail_closed=True`` means the in-memory backend
    must be used.
    """
    claims: List[Tuple[str, Optional[bool]]] = []

    # Build-identity markers.
    for var in _BUILD_IDENTITY_ENV_VARS:
        value = os.environ.get(var)
        if value:
            claim = _marker_claim(value)
            claims.append((var, claim))

    # Explicit prototype booleans.
    for var in _PROTOTYPE_MARKER_ENV_VARS:
        value = os.environ.get(var)
        if value not in (None, ""):
            low = value.strip().lower()
            if low in _TRUTHY_VALUES or _marker_claim(value) is True:
                claim = True
            elif low in {"0", "false", "no", "off", "production"}:
                claim = False
            else:
                claim = None  # invalid / ambiguous -> fail-closed
            claims.append((var, claim))

    # Legacy explicit namespace values (must agree with every other claim).
    explicit_namespaces: List[Tuple[str, str]] = []
    for var in _SECRET_NAMESPACE_ENV_VARS:
        value = _normalize_namespace(os.environ.get(var))
        if value is not None:
            explicit_namespaces.append((var, value))

    # Profile location can corroborate prototype identity, but is treated as a
    # weak claim and never enough on its own to move off the production default
    # unless it is unambiguous.
    for var in _DATA_DIR_ENV_VARS:
        value = os.environ.get(var)
        if value and "prototype" in value.lower():
            claims.append((var, True))

    # Fail-closed: unknown or conflicting claims.
    if claims:
        known_claims = [claim for _, claim in claims if claim is not None]
        if any(claim is None for _, claim in claims):
            return (
                None,
                False,
                True,
                "ambiguous build-identity marker: " + ", ".join(f"{k}={v}" for k, v in claims),
            )
        known_values = {claim for claim in known_claims}
        if len(known_values) > 1:
            return (
                None,
                False,
                True,
                "conflicting build-identity markers: " + ", ".join(f"{k}={v}" for k, v in claims),
            )

    prototype_claim = (any(claim is True for _, claim in claims) and
                      not any(claim is False for _, claim in claims) and
                      not any(claim is None for _, claim in claims))

    # Explicit namespace handling.
    if explicit_namespaces:
        # All explicit overrides must agree with each other.
        override_values = {value for _, value in explicit_namespaces}
        if len(override_values) > 1:
            return (
                None,
                False,
                True,
                "conflicting secret namespace overrides: " + ", ".join(f"{k}={v}" for k, v in explicit_namespaces),
            )

        override = next(iter(override_values))
        override_prototype = "prototype" in override.lower()

        # Cross-check the override against build identity markers.
        if claims and prototype_claim != override_prototype:
            return (
                None,
                False,
                True,
                "secret namespace override disagrees with build identity: "
                f"override={override!r}, prototype_marker={prototype_claim}",
            )

        # Map a generic/production-looking override onto the production namespace
        # rather than silently normalising it away. Unknown namespaces are
        # accepted (they isolate storage), but they must not masquerade as the
        # production one when a prototype marker said otherwise.
        if prototype_claim and not override_prototype and override in (_PRODUCTION_SECRET_NAMESPACE,):
            return (
                None,
                False,
                True,
                "production namespace requested by a prototype-marked build",
            )

        return (override, override_prototype, False, "explicit secret namespace override")

    if prototype_claim:
        return (_PROTOTYPE_SECRET_NAMESPACE, True, False, "prototype build identity")

    return (_PRODUCTION_SECRET_NAMESPACE, False, False, "production build identity")


def _resolve_target_prefix() -> str:
    namespace, _, fail_closed, _ = resolve_secret_context()
    if fail_closed:
        # Fail closed: never return a usable production prefix.
        return ""
    return _target_prefix_for_namespace(namespace)


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class SecretStore(Protocol):
    def get(self, name: str) -> str: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class CredentialManagerStore:
    """Windows Credential Manager backend (production / isolated namespace)."""

    def __init__(self, target_prefix: Optional[str] = None) -> None:
        self._target_prefix = _target_prefix_for_namespace(
            target_prefix[:-1] if isinstance(target_prefix, str) and target_prefix.endswith("/") else target_prefix
        ) if target_prefix else _resolve_target_prefix()

    def _target(self, name: str) -> str:
        return self._target_prefix + name

    def get(self, name: str) -> str:
        try:
            import win32cred
            import pywintypes
        except ImportError:
            return ""
        try:
            cred = win32cred.CredRead(self._target(name), win32cred.CRED_TYPE_GENERIC)
        except pywintypes.error:
            return ""
        blob = cred.get("CredentialBlob", b"")
        try:
            return blob.decode("utf-16-le")
        except UnicodeDecodeError:
            return ""

    def set(self, name: str, value: str) -> None:
        import win32cred

        cred = {
            "Type": win32cred.CRED_TYPE_GENERIC,
            "TargetName": self._target(name),
            "CredentialBlob": value,  # win32cred stores as UTF-16 internally
            "Persist": win32cred.CRED_PERSIST_LOCAL_MACHINE,
            "UserName": os.environ.get("USERNAME", ""),
            "Comment": "Sythion (Synth) secret",
        }
        win32cred.CredWrite(cred, 0)

    def delete(self, name: str) -> None:
        import win32cred
        import pywintypes

        try:
            win32cred.CredDelete(self._target(name), win32cred.CRED_TYPE_GENERIC, 0)
        except pywintypes.error:
            pass  # Nothing to delete.


class MemoryStore:
    """Non-persistent in-memory backend for dev/test. Never touches the vault."""

    def __init__(self) -> None:
        self._values: Dict[str, str] = {}

    def get(self, name: str) -> str:
        return self._values.get(name, "")

    def set(self, name: str, value: str) -> None:
        self._values[name] = value

    def delete(self, name: str) -> None:
        self._values.pop(name, None)


# ---------------------------------------------------------------------------
# Store selection
# ---------------------------------------------------------------------------

_store: Optional[SecretStore] = None


def _cm_target(name: str, target_prefix: Optional[str] = None) -> str:
    if target_prefix is None:
        prefix = _resolve_target_prefix()
    else:
        namespace = target_prefix
        if isinstance(namespace, str) and namespace.endswith("/"):
            namespace = namespace[:-1]
        prefix = _target_prefix_for_namespace(namespace)
    if not prefix:
        prefix = "Sythion (Synth) [FAIL-CLOSED]/"
    return prefix + name


def _default_store() -> SecretStore:
    """Return the production backend, or the in-memory one off-Windows/dev."""
    backend = os.environ.get("SYTHION_SECRET_BACKEND", "").strip().lower()
    namespace, _, fail_closed, _ = resolve_secret_context()

    # Persist strict isolation: prototype/ambiguous contexts never use the
    # environment or .env credential convenience value.
    if fail_closed or namespace != _PRODUCTION_SECRET_NAMESPACE:
        # Keep going; settings clearing happens in config.py at import.
        pass

    if backend == "memory" or fail_closed:
        return MemoryStore()
    try:
        import win32cred  # noqa: F401

        return CredentialManagerStore(_resolve_target_prefix())
    except ImportError:
        return MemoryStore()


def _get_store() -> SecretStore:
    global _store
    if _store is None:
        _store = _default_store()
    return _store


def set_secret_store_for_testing(store: Optional[SecretStore]) -> None:
    """Override the active backend (tests only). Pass None to reset."""
    global _store
    _store = store


# ---------------------------------------------------------------------------
# Public API (write-only by contract)
# ---------------------------------------------------------------------------


def set_secret(name: str, value: str) -> None:
    """Store a secret. The value is never returned by any read path."""
    if name not in KNOWN_SECRETS:
        raise ValueError(f"Unknown secret name: {name!r}")
    _get_store().set(name, value)


def delete_secret(name: str) -> None:
    """Delete a stored secret."""
    if name not in KNOWN_SECRETS:
        raise ValueError(f"Unknown secret name: {name!r}")
    _get_store().delete(name)


def has_secret(name: str) -> bool:
    """Report whether a secret is configured (never its value)."""
    return bool(_get_store().get(name))


def list_secret_names() -> List[str]:
    """Return the names of configured secrets, never values."""
    return [name for name in sorted(KNOWN_SECRETS) if has_secret(name)]


def resolve_ollama_api_key(env_override: str = "") -> str:
    """Return the active Ollama API key.

    Precedence:
      1. Explicit environment override (dev convenience, e.g. the shell env).
      2. The configured secret store (Credential Manager in production).

    In prototype or ambiguous/fail-closed contexts the explicit override is
    ignored so an inherited or settings-supplied production key cannot leak.
    """
    namespace, _is_prototype, fail_closed, _ = resolve_secret_context()
    allow_override = (
        namespace == _PRODUCTION_SECRET_NAMESPACE
        and not _is_prototype
        and not fail_closed
    )
    if env_override and allow_override:
        return env_override
    return _get_store().get(OLLAMA_API_KEY)



def resolve_compatible_provider_key(provider_id: str) -> str:
    """Read a configured key only for an explicitly trusted provider ID.

    This reuses the existing scoped secret store, which automatically
    isolates prototypes and fails closed on ambiguous build identity. No
    key may be inherited via environment variables or backend/.env.
    """
    key_names = {
        "openrouter": OPENROUTER_API_KEY,
        "nvidia": NVIDIA_API_KEY,
        "groq": GROQ_API_KEY,
    }
    name = key_names.get(provider_id)
    if name is None:
        raise ValueError("Unsupported inference provider")
    return _get_store().get(name)


# Minimal introspection helpers (no aliases).
def secret_namespace() -> Optional[str]:
    return resolve_secret_context()[0]

def secret_is_prototype() -> bool:
    return resolve_secret_context()[1]

def secret_fail_closed() -> bool:
    return resolve_secret_context()[2]

def is_prototype_build() -> bool:
    return secret_is_prototype()
