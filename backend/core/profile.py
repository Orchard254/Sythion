"""
Non-secret Sythion (Synth) profile metadata.

Only non-secret configuration (the selected Ollama Cloud model ID and optional
staged provider/model preview) is persisted here under LocalAppData. Secrets live in
Windows Credential Manager (backend/core/secrets.py) and never enter this
file, the database, logs, or model context.
"""

from __future__ import annotations

import json
import logging
import os
import tempfile
import threading
from typing import Dict, Any

from .storage import data_dir

logger = logging.getLogger(__name__)

# Serializes writers in this process so concurrent save_profile calls cannot
# race on the load/merge/replace sequence. Cross-process callers are also
# protected by the directory-atomic os.replace below.
_SAVE_LOCK = threading.RLock()


def profile_path() -> str:
    """Return the profile metadata file path."""
    return os.path.join(data_dir(), "profile.json")


def load_profile() -> Dict[str, Any]:
    """Read the profile metadata file, tolerating an absent/corrupt file.

    Read-only callers (for example model resolution) use this tolerant form so a
    damaged profile does not crash unrelated startup. It does not validate the
    existing file for writes; see ``save_profile``.
    """
    path = profile_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError, UnicodeError) as e:  # pragma: no cover - FS errors
        logger.warning("Could not read profile at %s: %s", path, e)
        return {}
    return data if isinstance(data, dict) else {}


def _read_profile_for_save() -> Dict[str, Any]:
    """Read the existing profile strictly for an update.

    A missing profile is treated as empty so initial creation still works. Any
    other read, parse, or shape failure is deliberately not swallowed: raising
    keeps ``save_profile`` from overwriting the original bytes with a partial
    profile derived from an empty baseline.
    """
    path = profile_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
    except FileNotFoundError:
        return {}
    if not isinstance(data, dict):
        raise ValueError(
            f"profile at {path} must contain a JSON object, got "
            f"{type(data).__name__}"
        )
    return data


def save_profile(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``patch`` into the profile and persist it atomically and durably.

    The merged profile is written to a unique temporary file in the same
    directory, flushed and fsynced, and only then atomically renamed over the
    profile path. A crash during serialization therefore leaves the previous
    profile.json (if any) intact, and a successful return guarantees the new
    bytes have been committed to stable storage on platforms that support it.

    If an existing profile cannot be read as a JSON object, this raises instead
    of replacing it, so corrupt or unreadable profile bytes are preserved.
    """
    with _SAVE_LOCK:
        profile = _read_profile_for_save()
        profile.update(patch or {})
        path = profile_path()
        directory = os.path.dirname(os.path.abspath(path)) or "."
        os.makedirs(directory, exist_ok=True)

        # mkstemp creates a private (0600) temp file in the target directory so
        # the rename stays within one filesystem and no leftover partial file
        # collides with a concurrent writer.
        fd, tmp = tempfile.mkstemp(
            prefix=".profile.", suffix=".tmp", dir=directory
        )
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(profile, f, indent=2, sort_keys=True)
                f.flush()
                try:
                    os.fsync(f.fileno())
                except (OSError, ValueError):
                    # fsync is best-effort on some filesystems; the atomic
                    # rename below still preserves the no-partial-write
                    # guarantee.
                    pass
            os.replace(tmp, path)
            try:
                dir_fd = os.open(directory, os.O_DIRECTORY)
            except (OSError, AttributeError, ValueError):
                dir_fd = None
            if dir_fd is not None:
                try:
                    os.fsync(dir_fd)
                except (OSError, ValueError):
                    pass
                finally:
                    os.close(dir_fd)
        except BaseException:
            try:
                os.unlink(tmp)
            except FileNotFoundError:
                pass
            raise
        # Success path: mkstemp name no longer exists (renamed).
        return profile


def load_profile_strict() -> Dict[str, Any]:
    """Read profile for an authorization decision; corruption must NOT look like default."""
    return _read_profile_for_save()


def get_selected_model() -> str:
    """Return the persisted selected Cloud model ID, if any."""
    return load_profile().get("selected_model", "") or ""


def set_selected_model(model_id: str) -> None:
    """Persist the selected Cloud model ID (non-secret metadata only)."""
    save_profile({"selected_model": model_id})



def get_staged_cloud_selection() -> Dict[str, str]:
    """Return validated, inert preview metadata or {}.

    This is NOT an active provider selector. The default Ollama provider and
    existing setup readiness evidence are never inferred from these fields.
    Damaged or attacker-modified profile content fails closed.
    """
    from backend.adapters.openai_compatible_adapter import PROVIDERS
    value = load_profile().get("staged_cloud_selection")
    if not isinstance(value, dict):
        return {}
    provider = value.get("provider_id")
    model = value.get("model_id")
    if (not isinstance(provider, str) or provider not in PROVIDERS or
            not isinstance(model, str) or not 1 <= len(model) <= 180):
        return {}
    import re
    if "://" in model or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,179}", model) is None:
        return {}
    return {"provider_id": provider, "model_id": model}


def get_staged_cloud_snapshot() -> Dict[str, str]:
    """Snapshot of the candidate and per-stage generation.

    The opaque generation is *non-secret* and is only used internally to
    invalidate evidence when the owner stages another candidate, including
    when they later return to the same provider/model. Pre-generation staged
    profiles must be staged again before receiving durable verification.
    """
    value = load_profile().get("staged_cloud_selection")
    if not isinstance(value, dict):
        return {}
    from backend.adapters.openai_compatible_adapter import PROVIDERS
    import re
    provider = value.get("provider_id")
    model = value.get("model_id")
    generation = value.get("generation")
    if (not isinstance(provider, str) or provider not in PROVIDERS or
            not isinstance(model, str) or
            "://" in model or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,179}", model) is None or
            not isinstance(generation, str) or
            re.fullmatch(r"[0-9a-f]{32}", generation) is None):
        return {}
    return {"provider_id": provider, "model_id": model, "generation": generation}


def stage_cloud_selection(provider_id: str, model_id: str) -> Dict[str, str]:
    """Persist an explicit non-executable cloud candidate, never a credential.

    No capability or readiness claim is written. This operation never calls
    a model and never changes the legacy selected_model field.
    """
    from backend.adapters.openai_compatible_adapter import PROVIDERS
    import re
    if not isinstance(provider_id, str) or provider_id not in PROVIDERS:
        raise ValueError("Unsupported inference provider")
    if (not isinstance(model_id, str) or
            "://" in model_id or
            re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._:/+-]{0,179}", model_id) is None):
        raise ValueError("Invalid cloud model ID")
    import secrets as _python_secrets
    state = {"provider_id": provider_id, "model_id": model_id}
    save_profile({"staged_cloud_selection": {
        **state, "generation": _python_secrets.token_hex(16),
    }})
    return dict(state)
