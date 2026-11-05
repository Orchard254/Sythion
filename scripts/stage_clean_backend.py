#!/usr/bin/env python3
"""Stage an isolated, clean backend payload WITHOUT touching the staged mirror.

This is the safe counterpart to ``stage_python_runtime.py`` for the *backend*
application tree only:

* It reads the SOURCE backend tree (default: ``<repo>/backend``) and copies it
  into a NEW output directory using :func:`payload_copy_ignore`, so prohibited
  secret material, databases, caches and the test suite are never copied.
* It never writes to ``src-tauri/resources`` (including the pre-existing staged
  mirror ``src-tauri/resources/backend``) and never touches the development
  source tree.
* It stages the BACKEND APPLICATION TREE ONLY. It does NOT download or stage
  the pinned Python runtime; the full installer must still use
  ``stage_python_runtime.py`` for that. Safe integration is a two-stage flow:
  (1) build and verify this isolated payload outside ``src-tauri/resources``;
  (2) only after it scans clean, separately refresh the staged backend with
  an approved, non-overwrite path swap. It must never target the live mirror.
* It performs no network access and no build.
* It promotes the staged tree to the final output directory ONLY after
  :func:`scan_tree` and :func:`backend_mirror_findings` report zero findings.
* It never opens, hashes or copies ``.env`` / key material. Secret
  classification is filename-based, so no secret value is ever read.

Usage
-----
    python scripts/stage_clean_backend.py
    python scripts/stage_clean_backend.py --output C:\\some\\isolated\\root
    python scripts/stage_clean_backend.py --source backend --output build\\staging\\clean

Exit code is 0 when the isolated tree is clean and non-zero otherwise.
"""
from __future__ import annotations

import argparse
import os
import shutil
import stat
import sys
import tempfile
from pathlib import Path
from typing import List, Optional, Sequence, Tuple

REPO_ROOT = Path(__file__).resolve().parents[1]
SCRIPTS_DIR = Path(__file__).resolve().parent
SRC_TAURI_RESOURCES = REPO_ROOT / "src-tauri" / "resources"
DEFAULT_OUTPUT = REPO_ROOT / "build" / "staging" / "clean-release-payload"
DEFAULT_SOURCE = REPO_ROOT / "backend"

sys.path.insert(0, str(SCRIPTS_DIR))
from verify_release_payload import (  # noqa: E402
    backend_mirror_findings,
    payload_copy_ignore,
    prune_tree,
    scan_tree,
)


def _is_reparse_point(path: Path) -> bool:
    """Report whether ``path`` is a symlink, junction (Windows reparse point)
    or other reparse point, WITHOUT following it.

    Junctions are the dangerous case: Python reports ``is_symlink() == False``
    for them, yet ``shutil.rmtree`` / copy operations follow them. We detect
    them through the ``FILE_ATTRIBUTE_REPARSE_POINT`` (0x400) flag.
    """
    try:
        st = path.lstat()
    except FileNotFoundError:
        # A genuinely missing (future) output is not a reparse point.
        return False
    except (OSError, ValueError):
        # Uncertain metadata fails closed.
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    attrs = getattr(st, "st_file_attributes", 0)
    return bool(attrs & 0x400)


def _safe_rmtree(path: Path) -> None:
    """Remove ``path`` without ever following a symlink or reparse point.

    Reparse points / symlinks are unlinked (the link itself is removed) but
    their targets are never touched. Normal directories are emptied and
    removed recursively one level at a time. This is the only removal helper
    used by stage() so a malicious output/temp entry cannot redirect cleanup
    at a protected or personal-data directory.
    """
    path = Path(path)
    try:
        st = path.lstat()
    except FileNotFoundError:
        return
    # Reparse point or symlink: unlink the link, never its target.
    if stat.S_ISLNK(st.st_mode) or (getattr(st, "st_file_attributes", 0) & 0x400):
        try:
            path.unlink()
        except OSError as exc:
            raise SystemExit(f"[stage-clean] refusing to remove reparse point {path}: {exc}")
        return
    if not stat.S_ISDIR(st.st_mode):
        try:
            path.unlink()
        except OSError as exc:
            raise SystemExit(f"[stage-clean] cannot remove file {path}: {exc}")
        return
    # Real directory: recurse into children first, then remove the (now empty)
    # directory itself. Child symlinks/reparse points are unlinked, not
    # followed.
    for child in list(path.iterdir()):
        _safe_rmtree(child)
    try:
        path.rmdir()
    except OSError as exc:
        raise SystemExit(f"[stage-clean] cannot remove directory {path}: {exc}")


def _contains_reparse_point(root: Path) -> bool:
    """Report whether ``root`` (or any descendant reached without following
    external symlinks we refuse to follow) contains a symlink/reparse point."""
    try:
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs) + list(files):
                if _is_reparse_point(Path(dirpath) / name):
                    return True
    except OSError:
        return True
    return False


def _resolve(path: Path) -> Path:
    return path.resolve()


def _is_within(path: Path, root: Path) -> bool:
    try:
        path.relative_to(root)
        return True
    except ValueError:
        return False


def _validate_paths(source: Path, output: Path) -> None:
    # Check reparse/symlink metadata first, before existence checks can
    # collapse an uncertain error into "not found".
    if _is_reparse_point(source):
        raise SystemExit(f"[stage-clean] refusing reparse point / symlink source: {source}")
    if _is_reparse_point(output):
        raise SystemExit(f"[stage-clean] refusing reparse point / symlink output: {output}")

    try:
        if not source.is_dir():
            raise SystemExit(f"[stage-clean] source backend directory not found: {source}")
    except (OSError, ValueError):
        raise SystemExit(f"[stage-clean] cannot inspect source backend directory: {source}")

    try:
        s, o = source.resolve(), output.resolve()
    except (OSError, RuntimeError, ValueError):
        raise SystemExit(f"[stage-clean] cannot resolve source/output paths: {source} {output}")

    # Never target the pre-existing staged mirror or its parent resources tree.
    protected_candidates = [SRC_TAURI_RESOURCES, SRC_TAURI_RESOURCES / "backend"]
    for protected in protected_candidates:
        try:
            pr = protected.resolve()
        except (OSError, RuntimeError, ValueError):
            raise SystemExit(f"[stage-clean] cannot resolve protected path: {protected}")
        if o == pr or _is_within(o, pr):
            raise SystemExit(
                f"[stage-clean] refusing to target protected path {o} "
                f"(would touch the existing staged mirror at {pr})"
            )
    # Never stage into the source tree (or vice versa).
    if o == s or _is_within(o, s) or _is_within(s, o):
        raise SystemExit(
            f"[stage-clean] refusing overlapping source/output: source={s} output={o}"
        )
    # Never operate on a filesystem/drive root: --overwrite / cleanup must never
    # be able to wipe an entire volume.
    if str(o) == str(o.anchor) or str(o) in ("", "/", "\\\\"):
        raise SystemExit(f"[stage-clean] refusing filesystem root output: {o}")
    # Prohibit any symlink / reparse point inside the source tree: copytree
    # would otherwise follow it and pull in (or later clean) files outside
    # the source tree.
    if _contains_reparse_point(s):
        raise SystemExit(
            f"[stage-clean] refusing source tree containing symlink/reparse point: {s}"
        )


def stage(
    source: Path,
    output: Path,
    *,
    overwrite: bool = False,
    keep_failed: bool = False,
    verify: bool = True,
    mirror_check: bool = True,
) -> Tuple[int, List, List, Optional[Path]]:
    """Return ``(exit_code, findings, removed, output_path_or_None)``.

    ``overwrite`` is retained only to reject legacy callers explicitly; a truthy
    value raises ``SystemExit`` immediately because pre-existing outputs are
    never deleted or replaced.
    """
    if overwrite:
        raise SystemExit(
            "[stage-clean] --overwrite is disabled: existing outputs are never removed "
            "or replaced; remove or choose a new output path manually if needed"
        )
    # Validate BEFORE resolving, so a symlinked/junction source or output is
    # detected (resolve() would otherwise follow it and hide the reparse point).
    _validate_paths(Path(source), Path(output))
    source = _resolve(source)
    output = _resolve(output)

    _output_exists = False
    try:
        output.lstat()
        _output_exists = True
    except FileNotFoundError:
        _output_exists = False
    except OSError as exc:
        raise SystemExit(f"[stage-clean] cannot inspect output path metadata: {output}: {exc}")
    if _output_exists and not overwrite:
        raise SystemExit(
            f"[stage-clean] output already exists: {output}\n"
            f"            overwrite is disabled; choose a new output path or remove "
            f"the existing output manually (src-tauri/resources/backend is never eligible)"
        )

    # Resolve output against the current filesystem state again before any
    # directory creation: prevents acting on a path that became a reparse
    # point between validation and use.
    if _is_reparse_point(output):
        raise SystemExit(f"[stage-clean] refusing reparse point output: {output}")
    parent = output.parent
    parent.mkdir(parents=True, exist_ok=True)
    if _is_reparse_point(parent):
        raise SystemExit(f"[stage-clean] refusing reparse point output parent: {parent}")

    # Fresh, uniquely-named temp dir. tempfile.mkdtemp creates a new
    # directory atomically enough for our purposes; it fails closed if a
    # colliding/link path already exists instead of replacing anything.
    temp_root = Path(
        tempfile.mkdtemp(prefix=f".{output.name}.staging.", dir=str(parent))
    )
    if _is_reparse_point(temp_root):
        _safe_rmtree(temp_root)
        raise SystemExit(f"[stage-clean] refusing reparse point temp staging root: {temp_root}")

    try:
        destination_backend = temp_root / "backend"
        shutil.copytree(
            source,
            destination_backend,
            dirs_exist_ok=True,
            symlinks=True,  # never dereference a link that slipped past validation
            ignore=payload_copy_ignore(),
        )
        removed = prune_tree(temp_root)

        findings = []
        if verify:
            findings.extend(scan_tree(temp_root))
            if mirror_check:
                findings.extend(backend_mirror_findings(source, destination_backend))

        if findings:
            if keep_failed:
                print(f"[stage-clean] verification FAILED; keeping temp tree: {temp_root}")
            else:
                _safe_rmtree(temp_root)
            return 1, findings, removed, None

        _final_output_lstat = None
        try:
            _final_output_lstat = output.lstat()
        except FileNotFoundError:
            _final_output_lstat = None
        except OSError as exc:
            raise SystemExit(f"[stage-clean] cannot inspect final output metadata: {output}: {exc}")
        if _final_output_lstat is not None:
            if not overwrite:
                raise SystemExit(f"[stage-clean] output appeared during staging: {output}")
            if _is_reparse_point(output):
                # Unlink the (unsafe) reparse point without touching its target.
                _safe_rmtree(output)
                raise SystemExit(
                    f"[stage-clean] refusing to replace reparse point output: {output}"
                )
            _safe_rmtree(output)
        # os.rename fails if the target suddenly exists, so a race cannot
        # silently overwrite an output we have just checked is absent.
        temp_root.rename(output)
        return 0, [], removed, output
    except SystemExit:
        _safe_rmtree(temp_root)
        raise
    except Exception:
        _safe_rmtree(temp_root)
        raise


def _report(findings, removed, output: Optional[Path]) -> None:
    if removed:
        print(f"[stage-clean] removed {len(removed)} prohibited artifact(s):")
        for item in removed:
            print(f"    - {item}")
    if findings:
        print(f"[stage-clean] FAIL: {len(findings)} packaging violation(s):")
        for finding in findings:
            print(f"    - {finding.describe()}")
        print("[stage-clean] resolve these violations before using the staged tree.")
    elif output is not None:
        print(f"[stage-clean] OK: isolated clean payload staged at {output}")
        print(f"[stage-clean] backend staged at {output / 'backend'}")


def main(argv: Optional[Sequence[str]] = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--source",
        type=Path,
        default=DEFAULT_SOURCE,
        help=f"source backend directory (default: {DEFAULT_SOURCE})",
    )
    parser.add_argument(
        "--output",
        type=Path,
        default=DEFAULT_OUTPUT,
        help=f"NEW isolated output payload root (default: {DEFAULT_OUTPUT}); "
        "must not target src-tauri/resources",
    )
    parser.add_argument(
        "--keep-failed",
        action="store_true",
        help="keep the temporary staging tree when verification fails",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip verification (not recommended)",
    )
    parser.add_argument(
        "--no-mirror-check",
        action="store_true",
        help="skip the source/staged backend comparison",
    )
    args = parser.parse_args(argv)

    try:
        rc, findings, removed, output = stage(
            args.source,
            args.output,
            overwrite=False,
            keep_failed=args.keep_failed,
            verify=not args.no_verify,
            mirror_check=not args.no_mirror_check,
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    _report(findings, removed, output)
    if rc != 0:
        return 1
    if output is not None:
        print(
            "\nRelease verification command:\n"
            f"    python scripts/verify_release_payload.py \"{output}\" "
            f"--check-backend-mirror --json"
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
