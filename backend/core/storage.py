"""
User-local data storage for Sythion (Synth).

All persistent data lives under the current user's LocalAppData directory
with user-only Windows ACLs, per the approved data-lifecycle contract
(Sythion (Synth)_FINAL_HANDOFF_BRIEF.md:65). Paths are centralized here so the
database, logs, handshake, and profile lock always agree.
"""

import logging
import os
import sys
import tempfile
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)

# Legacy ~/.sythion fallback (kept for migration; production uses LocalAppData).
LEGACY_DATA_DIR = os.path.join(os.path.expanduser("~"), ".sythion")

# Profile directory contract.
#
# The desktop shell and this sidecar must agree on ONE profile directory: the
# database, the profile lock, the handshake file, the token file and the logs
# all live there. A split profile is not cosmetic - the sidecar would write its
# handshake into one directory while the shell looked for it in another, the
# bridge would never resolve, and the application would sit on "Connecting to
# Sythion" until the handshake timeout expired.
#
# The rules:
#
#   1. ``SYTHION_DATA_DIR`` is the canonical explicit override.
#   2. ``Sythion (Synth)_DATA_DIR`` is still honoured, but ONLY when the
#      canonical variable is absent. It exists because the packaging and
#      launch scripts set it.
#   3. If BOTH are set and they name different directories, this is an error.
#      Silently picking one would hide a misconfiguration that splits the
#      profile.
#   4. With neither set, the established default is used:
#      ``%LOCALAPPDATA%\\Sythion (Synth)``.
#
# The desktop shell resolves the directory once and passes the resolved path in
# ``SYTHION_DATA_DIR``, so in the packaged application this sidecar never has
# to guess.
DATA_DIR_ENV = "SYTHION_DATA_DIR"
LEGACY_DATA_DIR_ENV = "Sythion (Synth)_DATA_DIR"
DEFAULT_DATA_DIR_NAME = "Sythion (Synth)"
PROTOTYPE_DATA_DIR_NAME = "Sythion (Synth) Prototype"


def _is_prototype_build() -> bool:
    """Whether this backend runs as an isolated prototype build.

    Mirrors ``backend.core.secrets.is_prototype_build`` without importing it
    at module load time (storage is imported by lower layers). Fail-closed:
    any resolution/import error is treated as a prototype (isolated) build so
    the legacy profile and ambient roots are never reached.
    """
    try:
        try:
            from backend.core.secrets import (
                is_prototype_build as _proto,
                secret_fail_closed as _fail_closed,
            )
        except ImportError:
            from core.secrets import (
                is_prototype_build as _proto,
                secret_fail_closed as _fail_closed,
            )
        # A fail-closed (ambiguous/conflicting) identity is isolated even when
        # it is not labelled "prototype"; never treat ambiguity as production.
        return bool(_proto()) or bool(_fail_closed())
    except Exception:
        # Fail closed: an unresolvable/ambiguous prototype identity must never
        # be treated as production. Treating uncertain builds as prototypes keeps
        # the legacy profile, ambient roots, and production data dir out of reach.
        return True


def _local_app_data_dir() -> Optional[str]:
    """Return the current user's Windows LocalAppData directory, if any."""
    if sys.platform != "win32":
        return None
    return os.environ.get("LOCALAPPDATA") or os.path.join(
        os.path.expanduser("~"), "AppData", "Local"
    )


def default_data_dir() -> str:
    """Return the default profile directory when no override is supplied.

    Prototype builds resolve to an isolated, build-distinct directory so a
    prototype can never merge with or overwrite the production profile, even
    when launched directly without a launcher-provided override.
    """
    name = PROTOTYPE_DATA_DIR_NAME if _is_prototype_build() else DEFAULT_DATA_DIR_NAME
    if _local_app_data_dir():
        return os.path.join(_local_app_data_dir(), name)
    return os.path.join(tempfile.gettempdir(), "sythion-data")


def normalize_data_dir(path: str) -> str:
    """Normalize a profile directory: absolute, with ``.``/``..`` resolved."""
    return os.path.normpath(os.path.abspath(path))


def data_dir_key(path: str) -> str:
    """Return a comparable key for a profile directory.

    Windows paths are case-insensitive, so the comparison folds case there.
    """
    return os.path.normcase(normalize_data_dir(path))


def resolve_data_dir(canonical: str = "", legacy: str = "", default: str = "") -> str:
    """Resolve the profile directory from an explicit set of overrides.

    ``canonical`` is ``SYTHION_DATA_DIR``, ``legacy`` is
    ``Sythion (Synth)_DATA_DIR``. Empty values count as absent.

    Raises:
        ValueError: when both overrides are present and name different
            directories. The caller must not silently pick one of them.
    """
    canonical = (canonical or "").strip()
    legacy = (legacy or "").strip()
    default = default or default_data_dir()

    if canonical and legacy:
        if data_dir_key(canonical) != data_dir_key(legacy):
            raise ValueError(
                f"conflicting profile directories: {DATA_DIR_ENV} points at "
                f"'{canonical}' but {LEGACY_DATA_DIR_ENV} points at '{legacy}'. "
                f"Set only {DATA_DIR_ENV}, or make both point at the same directory."
            )
        return normalize_data_dir(canonical)
    if canonical:
        return normalize_data_dir(canonical)
    if legacy:
        return normalize_data_dir(legacy)
    return normalize_data_dir(default)


def data_dir() -> str:
    """Return (and create) the user-local Sythion (Synth) data directory.

    Windows: ``%LOCALAPPDATA%\\Sythion (Synth)``. Non-Windows: a temp-scoped fallback so
    tests and non-production runs still work. The directory is created with
    user-only ACLs on Windows.

    Raises:
        ValueError: when the canonical and legacy overrides disagree.
    """
    base = resolve_data_dir(
        os.environ.get(DATA_DIR_ENV, ""),
        os.environ.get(LEGACY_DATA_DIR_ENV, ""),
    )

    _ensure_private_dir(base)
    return base


def _ensure_private_dir(path: str) -> None:
    """Create ``path`` if needed and lock it down to the current user on Windows."""
    if os.path.exists(path) and not os.path.isdir(path):
        raise NotADirectoryError(f"Sythion (Synth) data path exists and is not a directory: {path}")

    os.makedirs(path, exist_ok=True)

    if sys.platform == "win32":
        try:
            _restrict_acl_windows(path)
        except Exception as e:  # pragma: no cover - depends on OS state
            logger.warning("Could not apply user-only ACL to %s: %s", path, e)


def _restrict_acl_windows(path: str) -> None:
    """Replace the ACL of ``path`` with one granting only the current user.

    Removes inherited permissions so other local users and the well-known
    Users group cannot read or modify Sythion (Synth) data. Uses pywin32 directly
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
    return os.path.join(data_dir(), "sythion.db")


def log_dir() -> str:
    """Return the directory for log files."""
    return os.path.join(data_dir(), "logs")


def log_path() -> str:
    """Return the main log file path."""
    return os.path.join(log_dir(), "sythion.log")


def handshake_path() -> str:
    """Return the sidecar startup handshake file path."""
    return os.path.join(data_dir(), "sidecar.json")


def lock_path() -> str:
    """Return the profile single-instance lock file path."""
    return os.path.join(data_dir(), "profile.lock")


def migrate_legacy_data_dir() -> bool:
    """Copy legacy ~/.sythion data into LocalAppData if LocalAppData is empty.

    Returns True when a migration occurred. Non-destructive: the legacy
    directory is left in place.

    Prototype builds never migrate the real legacy profile: returning before
    any filesystem access keeps a prototype from importing user data from
    ``~/.sythion`` even when the launcher/target directory would otherwise
    be empty.
    """
    if _is_prototype_build():
        return False
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
