"""
Non-secret JARVIS profile metadata.

Only non-secret configuration (currently the selected Cloud model ID) is
persisted here, under the user-only LocalAppData directory. Secrets live in
Windows Credential Manager (backend/core/secrets.py) and never enter this
file, the database, logs, or model context.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Dict, Any

from .storage import data_dir

logger = logging.getLogger(__name__)


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
    """Merge ``patch`` into the profile and persist it atomically."""
    profile = load_profile()
    profile.update(patch)
    path = profile_path()
    tmp = path + ".tmp"
    with open(tmp, "w", encoding="utf-8") as f:
        json.dump(profile, f, indent=2, sort_keys=True)
    os.replace(tmp, path)
    return profile


def get_selected_model() -> str:
    """Return the persisted selected Cloud model ID, if any."""
    return load_profile().get("selected_model", "") or ""


def set_selected_model(model_id: str) -> None:
    """Persist the selected Cloud model ID (non-secret metadata only)."""
    save_profile({"selected_model": model_id})
