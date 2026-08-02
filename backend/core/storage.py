"""
User-local data storage for JARVIS.

All persistent data lives under the current user's LocalAppData directory
with user-only Windows ACLs, per the approved data-lifecycle contract
(JARVIS_FINAL_HANDOFF_BRIEF.md:65). Paths are centralized here so the
database, logs, handshake, and profile lock always agree.
"""

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Legacy ~/.jarvis fallback (kept for migration; production uses LocalAppData).
LEGACY_DATA_DIR = os.path.join(os.path.expanduser("~"), ".jarvis")


def _local_app_data_dir() -> Optional[str]:
    """Return the current user's Windows LocalAppData directory, if any."""
    if sys.platform != "win32":
        return None
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )


def data_dir() -> str:
    """Return (and create) the user-local JARVIS data directory.

    Windows: ``%LOCALAPPDATA%\\JARVIS``. Non-Windows: a temp-scoped fallback so
    tests and non-production runs still work. The directory is created with
    user-only ACLs on Windows.
    """
    env_dir = os.environ.get("JARVIS_DATA_DIR", "")
    if env_dir:
        base = env_dir
    elif _local_app_data_dir():
        base = os.path.join(_local_app_data_dir(), "JARVIS")
    else:
        base = os.path.join(tempfile.gettempdir(), "jarvis-data")

    _ensure_private_dir(base)
    return base


def _ensure_private_dir(path: str) -> None:
    """Create ``path`` if needed and lock it down to the current user on Windows."""
    if os.path.exists(path) and not os.path.isdir(path):
        raise NotADirectoryError(f"JARVIS data path exists and is not a directory: {path}")

    os.makedirs(path, exist_ok=True)

    if sys.platform == "win32":
        try:
            _restrict_acl_windows(path)
        except Exception as e:  # pragma: no cover - depends on OS state
            logger.warning("Could not apply user-only ACL to %s: %s", path, e)


def _restrict_acl_windows(path: str) -> None:
    """Replace the ACL of ``path`` with one granting only the current user.

    Removes inherited permissions so other local users and the well-known
    Users group cannot read or modify JARVIS data. Uses pywin32 directly
    (no subprocess) to keep the sidecar single-process.
    """
    import win32api
    import win32security
    import win32con

    user_sid, _, _ = win32security.LookupAccountName("", win32api.GetUserName())
    dacl = win32security.ACL()

    # Current user: full control, propagated to children.
    dacl.AddAccessAllowedAce(
        win32security.ACL_REVISION,
        win32con.GENERIC_ALL,
        user_sid,
    )

    owner = win32security.GetFileSecurity(
        path, win32security.OWNER_SECURITY_INFORMATION
    )
    owner.SetSecurityDescriptorOwner(user_sid, False)
    win32security.SetFileSecurity(
        path, win32security.OWNER_SECURITY_INFORMATION, owner
    )

    dacl_sec = win32security.GetFileSecurity(
        path, win32security.DACL_SECURITY_INFORMATION
    )
    dacl_sec.SetSecurityDescriptorDacl(1, dacl, 0)
    win32security.SetFileSecurity(
        path, win32security.DACL_SECURITY_INFORMATION, dacl_sec
    )


def database_path() -> str:
    """Return the SQLite database file path."""
    return os.path.join(data_dir(), "jarvis.db")


def log_dir() -> str:
    """Return the directory for log files."""
    return os.path.join(data_dir(), "logs")


def log_path() -> str:
    """Return the main log file path."""
    return os.path.join(log_dir(), "jarvis.log")


def handshake_path() -> str:
    """Return the sidecar startup handshake file path."""
    return os.path.join(data_dir(), "sidecar.json")


def lock_path() -> str:
    """Return the profile single-instance lock file path."""
    return os.path.join(data_dir(), "profile.lock")


def migrate_legacy_data_dir() -> bool:
    """Copy legacy ~/.jarvis data into LocalAppData if LocalAppData is empty.

    Returns True when a migration occurred. Non-destructive: the legacy
    directory is left in place.
    """
    legacy = Path(LEGACY_DATA_DIR)
    target = Path(data_dir())
    if not legacy.is_dir() or target == legacy:
        return False
    if not legacy.is_dir():
        return False
    if any(target.iterdir()):
        return False

    import shutil

    try:
        for item in legacy.iterdir():
            dest = target / item.name
            if item.is_dir():
                shutil.copytree(item, dest)
            else:
                shutil.copy2(item, dest)
        logger.info("Migrated legacy data from %s to %s", legacy, target)
        return True
    except Exception as e:  # pragma: no cover - filesystem dependent
        logger.warning("Legacy data migration failed: %s", e)
        return False
