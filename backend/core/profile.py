"""
Non-secret Sythion (Synth) profile metadata.

Only non-secret configuration (currently the selected Cloud model ID) is
persisted here, under the user-only LocalAppData directory. Secrets live in
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
    """Read the profile metadata file, tolerating an absent/corrupt file."""
    path = profile_path()
    try:
        with open(path, encoding="utf-8") as f:
            data = json.load(f)
            return data if isinstance(data, dict) else {}
    except FileNotFoundError:
        return {}
    except (json.JSONDecodeError, OSError) as e:  # pragma: no cover - FS errors
        logger.warning("Could not read profile at %s: %s", path, e)
        return {}


def save_profile(patch: Dict[str, Any]) -> Dict[str, Any]:
    """Merge ``patch`` into the profile and persist it atomically and durably.

    The merged profile is written to a unique temporary file in the same
    directory, flushed and fsynced, and only then atomically renamed over the
    profile path. A crash during serialization therefore leaves the previous
    profile.json (if any) intact, and a successful return guarantees the new
    bytes have been committed to stable storage on platforms that support it.
    """
    with _SAVE_LOCK:
        profile = load_profile()
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


def get_selected_model() -> str:
    """Return the persisted selected Cloud model ID, if any."""
    return load_profile().get("selected_model", "") or ""


def set_selected_model(model_id: str) -> None:
    """Persist the selected Cloud model ID (non-secret metadata only)."""
    save_profile({"selected_model": model_id})
