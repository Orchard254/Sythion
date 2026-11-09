#!/usr/bin/env python3
"""Explicit, verified refresh of Sythion's generated Tauri backend mirror.

--check is read-only. --apply stages a clean backend outside resources,
verifies it, and promotes it while keeping the old mirror as a backup.
Does not touch the source tree, Python runtime, or network.
"""
from __future__ import annotations

import argparse
from pathlib import Path
import sys
import tempfile
import uuid

REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE = REPO_ROOT / "backend"
RESOURCES = REPO_ROOT / "src-tauri" / "resources"
MIRROR = RESOURCES / "backend"
BACKUP_PARENT = REPO_ROOT / "build" / "staging"

# Tests import this as scripts.refresh_staged_backend; the CLI runs it as a
# standalone script. Both need the sibling staging modules on sys.path.
SCRIPTS_DIR = str(Path(__file__).resolve().parent)
if SCRIPTS_DIR not in sys.path:
    sys.path.insert(0, SCRIPTS_DIR)

from stage_clean_backend import (  # noqa: E402
    _contains_reparse_point, _is_reparse_point, stage,
)
from verify_release_payload import (  # noqa: E402
    backend_mirror_findings, scan_tree,
)


def _safe_directory(path: Path, label: str, *, optional: bool = False,
                    check_children: bool = True) -> None:
    if _is_reparse_point(path):
        raise ValueError(f"{label} is a reparse point or symlink")
    if not path.exists():
        if optional:
            return
        raise ValueError(f"{label} does not exist")
    if not path.is_dir() or (check_children and _contains_reparse_point(path)):
        raise ValueError(f"{label} is not a safe ordinary directory")


def check() -> tuple[bool, int]:
    """Read-only drift check; only counts, never prints file contents."""
    _safe_directory(SOURCE, "source backend")
    _safe_directory(RESOURCES.parent, "Tauri directory", check_children=False)
    _safe_directory(RESOURCES, "Tauri resources", optional=True, check_children=False)
    _safe_directory(MIRROR, "existing backend mirror", optional=True)
    findings = backend_mirror_findings(SOURCE, MIRROR)
    return not findings, len(findings)


def apply() -> Path | None:
    """Explicitly promote a verified stage; retain old mirror, rollback on error."""
    _safe_directory(SOURCE, "source backend")
    _safe_directory(RESOURCES.parent, "Tauri directory", check_children=False)
    _safe_directory(RESOURCES, "Tauri resources", optional=True, check_children=False)
    _safe_directory(MIRROR, "existing backend mirror", optional=True)
    _safe_directory(BACKUP_PARENT.parent, "build directory",
                    optional=True, check_children=False)
    _safe_directory(BACKUP_PARENT, "backup parent",
                    optional=True, check_children=False)
    if MIRROR.exists() and scan_tree(MIRROR):
        # Never relocate a locally modified mirror that contains state or
        # prohibited installer artifacts. Require owner review first.
        raise ValueError("existing mirror contains prohibited release artifacts")

    BACKUP_PARENT.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(
        prefix="clean-backend-refresh-", dir=str(BACKUP_PARENT)
    ) as temp:
        output = Path(temp) / "verified-payload"
        rc, findings, _, out = stage(SOURCE, output)
        if rc != 0 or findings or out is None:
            raise RuntimeError("isolated backend staging failed verification")
        staged = out / "backend"
        if backend_mirror_findings(SOURCE, staged) or scan_tree(staged):
            raise RuntimeError("isolated backend failed source equality or safety scan")

        _safe_directory(RESOURCES, "Tauri resources",
                        optional=True, check_children=False)
        _safe_directory(MIRROR, "existing backend mirror", optional=True)
        backup = None
        if MIRROR.exists():
            backup = BACKUP_PARENT / f"clean-backend-backup-{uuid.uuid4().hex}"
            MIRROR.rename(backup)
        promoted = False
        try:
            RESOURCES.mkdir(parents=True, exist_ok=True)
            staged.rename(MIRROR)
            promoted = True
            if backend_mirror_findings(SOURCE, MIRROR) or scan_tree(MIRROR):
                raise RuntimeError("promoted mirror failed release verification")
        except Exception:
            if promoted and MIRROR.exists():
                MIRROR.rename(out / "unverified-backend")
            if backup is not None and backup.exists() and not MIRROR.exists():
                backup.rename(MIRROR)
            raise
        return backup


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    actions = parser.add_mutually_exclusive_group()
    actions.add_argument("--check", action="store_true",
                         help="read-only drift check (default)")
    actions.add_argument("--apply", action="store_true",
                         help="opt-in verified replacement of generated mirror")
    args = parser.parse_args(argv)
    try:
        if args.apply:
            backup = apply()
            print("[backend-mirror] verified refreshed mirror (source bytes match)")
            if backup is not None:
                print(f"[backend-mirror] previous mirror retained at {backup}")
            return 0
        matches, count = check()
        if matches:
            print("[backend-mirror] clean: staged backend matches source")
            return 0
        print(f"[backend-mirror] stale: {count} mismatched backend path(s)")
        print("[backend-mirror] stop Tauri builds, then explicitly run --apply")
        return 1
    except (OSError, ValueError, RuntimeError, SystemExit) as exc:
        print(f"[backend-mirror] NOT VERIFIED: {type(exc).__name__}: {exc}",
              file=sys.stderr)
        return 2


if __name__ == "__main__":
    raise SystemExit(main())
