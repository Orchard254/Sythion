#!/usr/bin/env python3
"""Generate an alternate Tauri configuration for a clean, isolated installer.

The default ``src-tauri/tauri.conf.json`` bundles ``src-tauri/resources``,
the protected mirror. That mirror must never be read from, written to or
overwritten by a packaging run that is trying to produce a clean payload.

This script writes a *new* configuration under a build output directory whose
``bundle.resources`` entries point at a *fresh* runtime/backend payload. It
does not download anything, invoke Cargo or npm, stage a runtime, or touch
``src-tauri/resources``. The payload must already exist and must already be
verified clean by ``scripts/verify_release_payload.py``; otherwise generation
fails closed and no configuration file is written.

Relative paths in the generated file follow Tauri's merge semantics: when the
configuration is supplied through ``tauri build --config``, Tauri parses and
resolves paths against the default ``src-tauri/tauri.conf.json``, not against
this file's own location. They are therefore written relative to
``src-tauri/``.

Usage
-----
    python scripts/generate_clean_installer_config.py \\
        --output-dir build/clean-installer \\
        --resources-dir build/clean-installer/resources

Exit code 0 on success, non-zero on any validation failure. The script never
claims that an installer was built or verified end-to-end.
"""

from __future__ import annotations

import argparse
import copy
import importlib.util
import json
import os
import stat
import tempfile
import sys
from pathlib import Path
from typing import Any

SCRIPT_DIR = Path(__file__).resolve().parent
REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]


class CleanInstallerError(RuntimeError):
    """Raised when a clean-installer configuration cannot be generated safely."""


def fail(msg: str) -> None:
    raise CleanInstallerError(msg)


def _load_verify_module() -> Any:
    path = SCRIPT_DIR / "verify_release_payload.py"
    spec = importlib.util.spec_from_file_location("verify_release_payload", path)
    if spec is None or spec.loader is None:
        fail(f"unable to load verifier module: {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


def _resolve(repo_root: Path, value: Path | str) -> Path:
    path = Path(value)
    if path.is_absolute():
        return path.resolve()
    return (repo_root / path).resolve()


def _within(child: Path, parent: Path) -> bool:
    try:
        child.relative_to(parent)
    except ValueError:
        return False
    return True


def _rel(base_dir: Path, target: Path) -> str:
    # ``os.path.relpath`` raises ``ValueError`` when the two paths live on
    # different Windows drives/mounts (for example an E: staging tree paired
    # with the C: repository). In that case an absolute, drive-qualified path
    # is the only safe representation: Tauri merges the additional configuration
    # against ``src-tauri`` and an absolute source path is left untouched
    # instead of being mis-resolved onto the wrong drive.
    try:
        return Path(os.path.relpath(target, base_dir)).as_posix()
    except ValueError:
        fail(
            "path is not on the same Windows drive as src-tauri and cannot be merged safely: "
            f"{target}"
        )


def _path_has_reparse_ancestor(path: Path | str) -> bool:
    """Report whether any existing ancestor of path is a reparse point."""
    try:
        current = Path(path)
    except (TypeError, ValueError):
        return True
    seen = set()
    while True:
        key = str(current)
        if key in seen:
            break
        seen.add(key)
        try:
            if _is_reparse_point(current):
                return True
        except (OSError, ValueError):
            return True
        try:
            parent = current.parent
        except (OSError, ValueError):
            return True
        if parent == current:
            break
        current = parent
    return False


def _is_reparse_point(path: Path | str) -> bool:
    """Report whether ``path`` is a symlink, junction or other reparse point
    WITHOUT following it."""
    try:
        st = Path(path).lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _contains_reparse_point(root: Path) -> bool:
    """Report whether any descendant of ``root`` is a reparse point, without
    following foreign links."""
    if not root.is_dir():
        return False
    try:
        for dirpath, dirs, files in os.walk(root, followlinks=False):
            for name in list(dirs) + list(files):
                if _is_reparse_point(Path(dirpath) / name):
                    return True
    except OSError:
        return True
    return False


def _verify_isolated_payload(repo_root: Path, resources_dir: Path) -> None:
    try:
        verify = _load_verify_module()
    except Exception as exc:
        fail(f"unable to load release-payload verifier: {exc}")
    backend_main = resources_dir / "backend" / "main.py"
    runtime_python = resources_dir / "python-runtime" / "python.exe"
    if _path_has_reparse_ancestor(backend_main) or _is_reparse_point(backend_main):
        fail(f"refusing reparse point in isolated backend entrypoint: {backend_main}")
    if _path_has_reparse_ancestor(runtime_python) or _is_reparse_point(runtime_python):
        fail(f"refusing reparse point in isolated Python runtime: {runtime_python}")
    if not backend_main.is_file():
        fail(f"isolated backend entrypoint missing: {backend_main}")
    if not runtime_python.is_file():
        fail(f"isolated Python runtime missing: {runtime_python}")
    findings = list(verify.scan_tree(resources_dir))
    findings.extend(
        verify.backend_mirror_findings(repo_root / "backend", resources_dir / "backend")
    )
    if findings:
        details = "\n".join(f"  - {finding.describe()}" for finding in findings)
        fail(
            "isolated payload failed verification "
            f"({len(findings)} finding(s)):\n{details}"
        )


def _validate_paths(repo_root: Path, output_dir: Any, resources_dir: Any) -> None:
    output_path = Path(output_dir)
    resources_path = Path(resources_dir)
    protected_raw = (repo_root / "src-tauri" / "resources")
    base_config_raw = (repo_root / "src-tauri" / "tauri.conf.json")

    # Refuse reparse points/symlinks anywhere in the paths we are about to
    # follow. A link in the middle of the path is just as dangerous as a
    # link at its end.
    if _path_has_reparse_ancestor(output_path) or _is_reparse_point(output_path):
        fail(f"refusing reparse point / symlink output directory: {output_path}")
    if _path_has_reparse_ancestor(resources_path) or _is_reparse_point(resources_path):
        fail(f"refusing reparse point / symlink resources directory: {resources_path}")
    if _path_has_reparse_ancestor(protected_raw) or _is_reparse_point(protected_raw):
        fail(f"refusing reparse point / symlink protected mirror: {protected_raw}")
    if _path_has_reparse_ancestor(base_config_raw) or _is_reparse_point(base_config_raw):
        fail(f"refusing reparse point / symlink base configuration: {base_config_raw}")

    output_resolved = output_path.resolve()
    resources_resolved = resources_path.resolve()
    protected = (repo_root / "src-tauri" / "resources").resolve()
    src_tauri_dir = (repo_root / "src-tauri").resolve()
    if src_tauri_dir.drive and output_resolved.drive and str(output_resolved.drive).lower() != str(src_tauri_dir.drive).lower():
        fail(
            "output directory must be on the same Windows drive as src-tauri; "
            "cross-drive generated/config paths are mangled by Tauri configuration merges: "
            f"output={output_resolved}, src-tauri={src_tauri_dir}"
        )
    if src_tauri_dir.drive and resources_resolved.drive and str(resources_resolved.drive).lower() != str(src_tauri_dir.drive).lower():
        fail(
            "isolated resources (payload) must be on the same Windows drive as src-tauri; "
            "cross-drive resource paths are mangled by Tauri configuration merges: "
            f"resources={resources_resolved}, src-tauri={src_tauri_dir}"
        )
    if output_resolved == src_tauri_dir:
        fail(
            "output directory must not be src-tauri itself: "
            "that would overwrite the base tauri.conf.json: "
            f"{output_resolved}"
        )

    if _within(output_resolved, protected) or output_resolved == protected:
        fail(
            "output directory must not be inside the protected mirror "
            f"src-tauri/resources: {output_resolved}"
        )
    if _within(resources_resolved, protected) or resources_resolved == protected:
        fail(
            "isolated resources must not be inside the protected mirror "
            f"src-tauri/resources: {resources_resolved}"
        )
    if _within(protected, resources_resolved):
        fail(
            "isolated resources directory must not contain the protected mirror: "
            f"{resources_resolved}"
        )
    # Never target a filesystem root and never let the output or the payload be
    # written under src-tauri or inside one another ambiguously.
    if str(output_resolved) == str(output_resolved.anchor):
        fail(f"output directory must not be a filesystem root: {output_resolved}")
    if str(resources_resolved) == str(resources_resolved.anchor):
        fail(f"resources directory must not be a filesystem root: {resources_resolved}")
    if _within(output_resolved, src_tauri_dir):
        fail(f"output directory must not be inside src-tauri: {output_resolved}")
    if _within(resources_resolved, src_tauri_dir) and resources_resolved != protected:
        fail(f"resources directory must not be inside src-tauri: {resources_resolved}")
    if output_resolved == resources_resolved or _within(output_resolved, resources_resolved):
        fail(
            "output directory must not be inside the isolated resources directory: "
            f"{output_resolved}"
        )
    if not resources_resolved.is_dir():
        fail(f"isolated resources directory does not exist: {resources_resolved}")
    if _contains_reparse_point(resources_resolved):
        fail(f"isolated resources directory contains a reparse point: {resources_resolved}")

    base_config = repo_root / "src-tauri" / "tauri.conf.json"
    if not base_config.is_file():
        fail(f"base Tauri configuration missing: {base_config}")


def generate_clean_installer_config(
    repo_root: Path | str,
    output_dir: Path | str,
    resources_dir: Path | str | None = None,
    *,
    prototype: bool = False,
    prototype_build: bool = False,
    isolated_profile: bool = False,
) -> Path:
    """Validate inputs and write an alternate Tauri configuration.

    Returns the path of the generated configuration file.
    """

    repo_root = Path(repo_root).resolve()
    output_raw = Path(output_dir)
    if resources_dir is None:
        resources_raw = output_raw / "resources"
    else:
        resources_raw = Path(resources_dir)

    _validate_paths(repo_root, output_raw, resources_raw)

    output_dir = _resolve(repo_root, output_raw)
    resources_dir = _resolve(repo_root, resources_raw)

    if _is_reparse_point(output_dir):
        fail(f"refusing reparse point / symlink output directory: {output_dir}")
    if _is_reparse_point(resources_dir):
        fail(f"refusing reparse point / symlink resources directory: {resources_dir}")
    if _contains_reparse_point(resources_dir):
        fail(f"isolated resources directory contains a reparse point: {resources_dir}")

    _verify_isolated_payload(repo_root, resources_dir)

    base_config_path = repo_root / "src-tauri" / "tauri.conf.json"
    try:
        base_config = json.loads(base_config_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        fail(f"unable to parse base Tauri configuration: {exc}")

    config = copy.deepcopy(base_config)
    # Prototype identity is explicit opt-in only; do not rebrand ordinary clean
    # installers. Only mark when requested by caller or environment.
    mark_prototype = bool(prototype or prototype_build or isolated_profile)
    if mark_prototype:
        config["productName"] = "Sythion (Synth) Prototype"
        config["identifier"] = "ai.sythion.desktop.prototype"
    # Paths in Tauri's merged additional config are resolved against the
    # default configuration's directory (src-tauri), not against this file.
    tauri_dir = (repo_root / "src-tauri").resolve()

    try:
        output_dir.mkdir(parents=True, exist_ok=True)
    except OSError as exc:
        fail(f"unable to create output directory: {exc}")
    if _is_reparse_point(output_dir):
        fail(f"refusing reparse point / symlink output directory: {output_dir}")
    if _is_reparse_point(output_dir.parent):
        fail(f"refusing reparse point / symlink output parent: {output_dir.parent}")

    rel_resources_raw = _rel(tauri_dir, resources_dir).rstrip("/")
    if rel_resources_raw in ("", "."):
        resource_prefix = ""
        verifier_root = "."
    else:
        resource_prefix = rel_resources_raw + "/"
        verifier_root = rel_resources_raw

    base_bundle = base_config.get("bundle") if isinstance(base_config, dict) else None
    base_resources = None
    if isinstance(base_bundle, dict) and isinstance(base_bundle.get("resources"), dict):
        base_resources = base_bundle["resources"]

    bundle = config.setdefault("bundle", {})
    if not isinstance(bundle, dict):
        bundle = {}
        config["bundle"] = bundle

    # JSON Merge Patch merges nested objects recursively. Setting only the two
    # new keys would leave every pre-existing source key (including the
    # protected mirror's "resources/python-runtime" and "resources/backend")
    # in the final merged configuration. Emit an explicit null for each old
    # key so RFC 7396 deletes it before the clean payload keys are added.
    new_resources: dict[str, Any] = {}
    if isinstance(base_resources, dict):
        for old_source in base_resources:
            new_resources[old_source] = None
    new_resources[f"{resource_prefix}python-runtime"] = "python-runtime"
    new_resources[f"{resource_prefix}backend"] = "backend"
    bundle["resources"] = new_resources

    build = config.setdefault("build", {})
    frontend_dir = (repo_root / "frontend").resolve()
    frontend_dist = (repo_root / "frontend" / "dist").resolve()
    frontend_rel = _rel(tauri_dir, frontend_dir)
    build["frontendDist"] = _rel(tauri_dir, frontend_dist)
    build["beforeDevCommand"] = f"npm --prefix {frontend_rel} run dev"
    verify_script_rel = _rel(tauri_dir, SCRIPT_DIR / "verify_release_payload.py")
    build["beforeBuildCommand"] = (
        f"npm --prefix {frontend_rel} run build && "
        f"python {verify_script_rel} {verifier_root} --check-backend-mirror"
    )

    # Icons remain relative to src-tauri exactly as in the base configuration.
    # Do not re-root or rewrite them; Tauri resolves them against src-tauri.

    app_section = config.get("app")
    if not isinstance(app_section, dict):
        config["app"] = {}

    config_path = output_dir / "tauri.conf.json"
    src_tauri_dir = (repo_root / "src-tauri").resolve()
    base_config_path = src_tauri_dir / "tauri.conf.json"

    # Guard the final output path without following a pre-existing link.
    if _path_has_reparse_ancestor(config_path) or _is_reparse_point(config_path):
        fail(f"refusing reparse point / symlink generated file: {config_path}")

    # Create-only: a pre-existing configuration (including a symlink, broken
    # or not) must never be overwritten or silently replaced.
    try:
        existing = config_path.lstat()
        fail(
            "refusing to overwrite pre-existing generated configuration: "
            f"{config_path}"
        )
    except FileNotFoundError:
        pass
    except OSError as exc:
        fail(f"unable to inspect generated configuration path: {exc}")

    if config_path.resolve() == base_config_path.resolve():
        fail("refusing to overwrite the base Tauri configuration")

    temp_path: Path | None = None
    try:
        fd, temp_name = tempfile.mkstemp(
            prefix=".tauri.conf.json.",
            suffix=".tmp",
            dir=str(output_dir),
        )
        temp_path = Path(temp_name)
        try:
            with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as handle:
                handle.write(json.dumps(config, indent=2) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
        except OSError as exc:
            fail(f"unable to write generated configuration: {exc}")
        try:
            os.chmod(temp_path, 0o644)
        except OSError:
            pass

        # Atomic, create-only publication: link(2)-style creation fails when
        # the destination already exists, including a link created by a racing
        # process after the check above.
        try:
            os.link(temp_path, config_path)
        except FileExistsError:
            fail(
                "refusing to overwrite pre-existing generated configuration: "
                f"{config_path}"
            )
        except OSError as exc:
            fail(f"unable to publish generated configuration: {exc}")

        # Remove our own temporary file name now that the content is reachable
        # through the new output path. This never touches a pre-existing temp.
        try:
            temp_path.unlink()
        except OSError:
            pass
        temp_path = None
    finally:
        if temp_path is not None:
            try:
                temp_path.unlink()
            except OSError:
                pass

    return config_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Generate an alternate Tauri config that bundles a verified "
        "isolated backend/runtime payload."
    )
    parser.add_argument(
        "--repo-root",
        type=Path,
        default=REPO_ROOT_DEFAULT,
        help="repository root (default: parent of this script's directory)",
    )
    parser.add_argument(
        "--output-dir",
        type=Path,
        default=Path("build") / "clean-installer",
        help="directory for the generated configuration (default: build/clean-installer)",
    )
    parser.add_argument(
        "--resources-dir",
        type=Path,
        default=None,
        help="verified isolated payload root "
        "(default: <output-dir>/resources)",
    )
    parser.add_argument(
        "--prototype",
        "--prototype-build",
        "--isolated-profile",
        action="store_true",
        help="mark the generated build as a prototype build so the shell resolves "
        "an isolated default profile; ordinary clean installers are production",
    )
    args = parser.parse_args(argv)

    repo_root = args.repo_root.resolve()
    try:
        config_path = generate_clean_installer_config(
            repo_root,
            args.output_dir,
            args.resources_dir,
            prototype=args.prototype,
        )
    except CleanInstallerError as exc:
        print(f"[clean-installer] FAIL: {exc}", file=sys.stderr)
        return 1

    print(f"[clean-installer] generated {config_path}")
    print(
        "[clean-installer] this only records an alternate configuration path; "
        "it is not an installer build or install verification."
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
