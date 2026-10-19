"""
Write-only secret storage for Sythion (Synth).

Policy (docs/ARCHITECTURE.md): secrets never enter `.env`, SQLite, logs, task
events, exports, or model context. The production backend is Windows Credential
Manager (win32cred). A non-persistent in-memory backend is used for dev/test so
tests never touch the real credential vault.

Secrets are write-only by contract: callers may set, check existence, or delete,
but stored values are never returned to API clients.
"""

from __future__ import annotations

import os
from typing import Dict, List, Optional, Protocol


# ---------------------------------------------------------------------------
# Secret registry (whitelist of known secret names)
# ---------------------------------------------------------------------------

OLLAMA_API_KEY = "ollama_api_key"

KNOWN_SECRETS = frozenset({OLLAMA_API_KEY})

# Credential Manager target name prefix. Credentials are stored per-user under
# this namespace so unrelated generic credentials are never touched.
_CM_TARGET_PREFIX = "Sythion (Synth)/"


def _cm_target(name: str) -> str:
    return _CM_TARGET_PREFIX + name


# ---------------------------------------------------------------------------
# Backends
# ---------------------------------------------------------------------------


class SecretStore(Protocol):
    def get(self, name: str) -> str: ...
    def set(self, name: str, value: str) -> None: ...
    def delete(self, name: str) -> None: ...


class CredentialManagerStore:
    """Windows Credential Manager backend (production)."""

    def get(self, name: str) -> str:
        try:
            import win32cred
            import pywintypes
        except ImportError:
            return ""
        try:
            cred = win32cred.CredRead(_cm_target(name), win32cred.CRED_TYPE_GENERIC)
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
            "TargetName": _cm_target(name),
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
            win32cred.CredDelete(_cm_target(name), win32cred.CRED_TYPE_GENERIC, 0)
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


def _default_store() -> SecretStore:
    """Return the production backend, or the in-memory one off-Windows/dev."""
    backend = os.environ.get("SYTHION_SECRET_BACKEND", "").lower()
    if backend == "memory":
        return MemoryStore()
    try:
        import win32cred  # noqa: F401

        return CredentialManagerStore()
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

    This is the single resolution point for the key; callers must not read
    Settings.ollama_api_key directly.
    """
    if env_override:
        return env_override
    return _get_store().get(OLLAMA_API_KEY)
