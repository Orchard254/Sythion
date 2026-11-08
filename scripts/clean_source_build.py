#!/usr/bin/env python3
"""Safe single-command clean source-checkout build workflow.

Load-order / side-effect map (import time): ``_load(filename, alias)``
resolves the sibling file, registers it in ``sys.modules`` via
``setdefault``, then runs ``spec.loader.exec_module`` so each
submodule's top-level code executes eagerly in strict source order:

1. ``STAGE_BACKEND``    <- ``stage_clean_backend.py``
2. ``STAGE_RUNTIME``    <- ``stage_clean_runtime.py``
3. ``GENERATE_CONFIG``  <- ``generate_clean_installer_config.py``
4. ``VERIFY``           <- ``verify_release_payload.py``

A missing sibling yields ``None``; callers must fail closed on ``None``.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Optional, Sequence

REPO_ROOT_DEFAULT = Path(__file__).resolve().parents[1]
SCRIPT_DIR = Path(__file__).resolve().parent


def _load(filename, alias):
    import importlib.util

    path = SCRIPT_DIR / filename
    if not path.is_file():
        return None
    spec = importlib.util.spec_from_file_location(alias, path)
    if spec is None or spec.loader is None:
        return None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(alias, module)
    spec.loader.exec_module(module)
    return module


STAGE_BACKEND = _load("stage_clean_backend.py", "stage_backend_clean_workflow")
STAGE_RUNTIME = _load("stage_clean_runtime.py", "stage_runtime_clean_workflow")
GENERATE_CONFIG = _load("generate_clean_installer_config.py", "generate_clean_cfg_workflow")
VERIFY = _load("verify_release_payload.py", "verify_rel_payload_workflow")


def _coalesce(values):
    for value in values:
        if value is not None:
            return value
    return None


def _is_reparse_point(path):
    import stat

    try:
        candidate = Path(path)
    except (TypeError, ValueError):
        return True
    try:
        st = candidate.lstat()
    except FileNotFoundError:
        return False
    except (OSError, ValueError):
        return True
    if stat.S_ISLNK(st.st_mode):
        return True
    return bool(getattr(st, "st_file_attributes", 0) & 0x400)


def _within(child, parent):
    try:
        Path(child).resolve().relative_to(Path(parent).resolve())
        return True
    except (ValueError, OSError, RuntimeError):
        return False


def _resolve(repo_root, value):
    path = Path(value)
    if path.is_absolute():
        return path
    return repo_root / path


def _protected(repo_root):
    return (repo_root / "src-tauri" / "resources").resolve()


def _refuse_protected(repo_root, path, label="path"):
    protected = _protected(repo_root)
    candidate = Path(path)
    resolved = candidate.resolve() if candidate.exists() else candidate
    if candidate == protected or _within(candidate, protected) or _within(protected, candidate):
        raise SystemExit(
            "clean-build: refusing to touch protected mirror %s (%s=%s)"
            % (protected, label, candidate)
        )
    if _is_reparse_point(candidate):
        raise SystemExit("clean-build: refusing reparse point %s: %s" % (label, candidate))


def _truthy(value):
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, str):
        lowered = value.strip().lower()
        if lowered in {"1", "true", "yes", "on", "release", "released", "prod", "production"}:
            return True
        if lowered in {"0", "false", "no", "off", "prototype", "unverified", "dev", "development"}:
            return False
        return None
    return bool(value)


class CleanBuildResult:
    def __init__(self, rc=0, payload_dir=None, config_path=None, manifest_path=None,
                 build_returncode=None, build_command=None, findings=None, removed=None,
                 mode="prototype/unverified", message=""):
        self.rc = rc
        self.payload_dir = payload_dir
        self.config_path = config_path
        self.manifest_path = manifest_path
        self.build_returncode = build_returncode
        self.build_command = build_command
        self.findings = list(findings) if findings is not None else []
        self.removed = list(removed) if removed is not None else []
        self.mode = mode
        self.message = message

    @property
    def success(self):
        return self.rc == 0

class CleanBuildWorkflow:
    def __init__(self, **kwargs):
        self.kwargs = dict(kwargs)

    def run(self, *args, **kwargs):
        merged = dict(self.kwargs)
        merged.update(kwargs)
        if args:
            merged.setdefault("repo_root", args[0])
        return run_workflow(*args, **merged)


Workflow = CleanBuildWorkflow
CleanBuild = CleanBuildWorkflow
CleanSourceBuild = CleanBuildWorkflow


def run_workflow(*args, **kwargs) -> CleanBuildResult:
    result = CleanBuildResult()

    repo_root_raw = _coalesce(
        [kwargs.get("repo_root"), kwargs.get("repo"), kwargs.get("root")] + list(args)
    )
    if isinstance(repo_root_raw, dict):
        kwargs.update(repo_root_raw)
        repo_root_raw = _coalesce(
            [kwargs.get("repo_root"), kwargs.get("repo"), kwargs.get("root"), REPO_ROOT_DEFAULT]
        )
    if repo_root_raw is None:
        repo_root_raw = REPO_ROOT_DEFAULT
    repo_root = Path(repo_root_raw).resolve()
    if not repo_root.is_dir():
        result.rc = 1
        result.message = "repository root not found: %s" % repo_root
        print(result.message, file=sys.stderr)
        return result

    # Backend-only release must fail before touching or requiring source/payload.
    _early_release = bool(_coalesce([_truthy(kwargs.get("release")), _truthy(kwargs.get("release_mode")), False]))
    _early_backend_only = bool(_coalesce([_truthy(kwargs.get("stage_backend")), _truthy(kwargs.get("backend_only")), _truthy(kwargs.get("stage_backend_only")), False]))
    if _early_release and _early_backend_only:
        result = CleanBuildResult()
        result.rc = 1
        result.message = "clean-build: release mode requires a full runtime payload; backend-only release is invalid"
        print(result.message, file=sys.stderr)
        return result

    source_raw = _coalesce(
        [
            kwargs.get("source"),
            kwargs.get("source_dir"),
            kwargs.get("source_path"),
            kwargs.get("backend"),
            kwargs.get("backend_source"),
            kwargs.get("backend_dir"),
        ]
    )
    source_dir = _resolve(repo_root, source_raw or (repo_root / "backend"))
    if not source_dir.is_dir():
        result.rc = 1
        result.message = "source backend directory not found: %s" % source_dir
        print(result.message, file=sys.stderr)
        return result
    _refuse_protected(repo_root, source_dir, "source")

    payload_raw = _coalesce(
        [
            kwargs.get("payload_dir"),
            kwargs.get("payload"),
            kwargs.get("isolated_output"),
            kwargs.get("staged_output"),
            kwargs.get("stage_output"),
            kwargs.get("payload_output"),
            kwargs.get("isolated_root"),
            kwargs.get("payload_root"),
        ]
    )
    installer_raw = _coalesce(
        [
            kwargs.get("installer_output"),
            kwargs.get("installer_dir"),
            kwargs.get("config_output"),
            kwargs.get("generated_dir"),
            kwargs.get("output_dir"),
            kwargs.get("gen_output"),
            kwargs.get("build_dir"),
        ]
    )
    generic_output = kwargs.get("output")
    if payload_raw is None and generic_output is not None and installer_raw is None:
        payload_raw = generic_output
    if installer_raw is None and generic_output is not None and payload_raw is not generic_output:
        installer_raw = generic_output

    if payload_raw is None:
        payload_raw = repo_root / "build" / "staging" / "clean-runtime-payload"
    if installer_raw is None:
        installer_raw = repo_root / "build" / "clean-installer"

    payload_dir = _resolve(repo_root, payload_raw)
    installer_output = _resolve(repo_root, installer_raw)
    _refuse_protected(repo_root, payload_dir, "payload_dir")
    _refuse_protected(repo_root, installer_output, "installer_output")
    # Tauri config merges preserve paths relative to src-tauri (same Windows
    # drive) only; cross-drive payload/config paths are fail-closed.
    src_tauri_dir = (repo_root / "src-tauri").resolve()
    def _same_windows_drive(path):
        return (not src_tauri_dir.drive or not Path(path).resolve().drive or
                str(Path(path).resolve().drive).lower() == str(src_tauri_dir.drive).lower())
    for label, check_path in (("payload_dir", payload_dir), ("installer_output", installer_output)):
        if not _same_windows_drive(check_path):
            result = CleanBuildResult()
            result.rc = 1
            result.message = (
                "clean-build: %s must be on the same Windows drive as src-tauri; "
                "cross-drive paths are mangled/omitted by Tauri configuration merges: "
                "%s vs %s" % (label, Path(check_path).resolve(), src_tauri_dir)
            )
            print(result.message, file=sys.stderr)
            return result

    resources_raw = _coalesce([kwargs.get("resources_dir"), kwargs.get("resources")])
    resources_dir = _resolve(repo_root, resources_raw) if resources_raw is not None else None
    # Ensure a separately resolved resources directory is on the same drive as src-tauri.
    src_tauri_dir = (repo_root / "src-tauri").resolve()
    _resolved_resources_dir = resources_dir if resources_dir is not None else payload_dir
    if (src_tauri_dir.drive and Path(_resolved_resources_dir).resolve().drive and
            str(Path(_resolved_resources_dir).resolve().drive).lower() != str(src_tauri_dir.drive).lower()):
        result = CleanBuildResult()
        result.rc = 1
        result.message = (
            "clean-build: resources_dir must be on the same Windows drive as src-tauri; "
            "cross-drive resource paths are mangled by Tauri configuration merges: "
            "%s vs %s"
            % (Path(_resolved_resources_dir).resolve(), src_tauri_dir)
        )
        print(result.message, file=sys.stderr)
        return result

    stage = kwargs.get("stage")
    if stage is None:
        stage = not bool(kwargs.get("no_stage", False))
    stage = bool(_coalesce([_truthy(stage), True]))

    stage_backend_only = _coalesce(
        [kwargs.get("stage_backend"), kwargs.get("backend_only"), kwargs.get("stage_backend_only")]
    )
    stage_backend_only = bool(_coalesce([_truthy(stage_backend_only), False]))

    allow_network = bool(_coalesce([_truthy(kwargs.get("allow_network")), _truthy(kwargs.get("network")), False]))
    release = bool(_coalesce([_truthy(kwargs.get("release")), _truthy(kwargs.get("release_mode")), False]))
    _early_stage_backend_only = bool(_coalesce([_truthy(kwargs.get("stage_backend")),
                                                _truthy(kwargs.get("backend_only")),
                                                _truthy(kwargs.get("stage_backend_only")), False]))
    if release and _early_stage_backend_only:
        result = CleanBuildResult()
        result.rc = 1
        result.message = "clean-build: release mode requires a full runtime payload; backend-only release is invalid"
        print(result.message, file=sys.stderr)
        return result
    if release and stage_backend_only:
        result = CleanBuildResult()
        result.rc = 1
        result.message = "clean-build: release mode requires a full runtime payload; backend-only release is invalid"
        print(result.message, file=sys.stderr)
        return result

    prototype = kwargs.get("prototype")
    if prototype is None:
        prototype = kwargs.get("prototype_build", kwargs.get("isolated_profile"))
    if prototype is None:
        prototype = not release
    else:
        prototype = bool(_truthy(prototype))
    if release and kwargs.get("prototype") is None:
        prototype = False

    keep_failed = bool(kwargs.get("keep_failed", False))
    verify = kwargs.get("verify")
    if verify is None:
        verify = not bool(kwargs.get("no_verify", False))
    verify = bool(verify)
    mirror_check = kwargs.get("mirror_check")
    if mirror_check is None:
        mirror_check = not bool(kwargs.get("no_mirror_check", False))
    mirror_check = bool(mirror_check)

    get_pip_sha256 = _coalesce(
        [
            kwargs.get("get_pip_sha256"),
            kwargs.get("get_pip_digest"),
            kwargs.get("get_pip_hash"),
            kwargs.get("pinned_get_pip_sha256"),
            kwargs.get("get_pip_sha"),
            kwargs.get("bootstrap_sha256"),
            os.environ.get("SYTHION_GET_PIP_SHA256"),
        ]
    )
    lock_path = _coalesce(
        [kwargs.get("lock_path"), kwargs.get("lock"), kwargs.get("requirements_lock"), kwargs.get("requirements_lock_path")]
    )
    runtime_zip = _coalesce(
        [kwargs.get("runtime_zip"), kwargs.get("python_zip"), kwargs.get("runtime_archive"), kwargs.get("zip_path")]
    )
    python_version = kwargs.get("python_version", "3.13.16")

    bootstrap_pip = kwargs.get("bootstrap_pip")
    if bootstrap_pip is None:
        bootstrap_pip = True
    bootstrap_pip = bool(bootstrap_pip)
    install_dependencies = kwargs.get("install_dependencies")
    if install_dependencies is None:
        install_dependencies = allow_network if not release else True
    if release:
        install_dependencies = True
        bootstrap_pip = True

    build_command = _coalesce(
        [
            kwargs.get("build_command"),
            kwargs.get("build_cmd"),
            kwargs.get("builder"),
            kwargs.get("build_tool"),
            os.environ.get("SYTHION_BUILD_COMMAND"),
        ]
    )
    run_build = kwargs.get("build")
    if run_build is None:
        run_build = kwargs.get("run_build")
    if run_build is None:
        run_build = build_command is not None
    run_build = bool(_coalesce([_truthy(run_build), build_command is not None]))

    # Stage.
    if stage:
        try:
            if (not stage_backend_only) and STAGE_RUNTIME is not None:
                rc, findings, removed, out = STAGE_RUNTIME.stage(
                    source=source_dir,
                    output=payload_dir,
                    overwrite=False,
                    keep_failed=keep_failed,
                    verify=verify,
                    mirror_check=mirror_check,
                    allow_network=allow_network,
                    bootstrap_pip=bootstrap_pip,
                    install_dependencies=install_dependencies,
                    required_imports=verify,
                    verify_inventory=True,
                    release=release,
                    get_pip_sha256=get_pip_sha256,
                    lock_path=lock_path,
                    python_version=python_version,
                    runtime_zip=runtime_zip,
                )
            else:
                if STAGE_BACKEND is None:
                    raise SystemExit("clean-build: stage_clean_backend module unavailable")
                rc, findings, removed, out = STAGE_BACKEND.stage(
                    source=source_dir,
                    output=payload_dir,
                    overwrite=False,
                    keep_failed=keep_failed,
                    verify=verify,
                    mirror_check=mirror_check,
                )
        except SystemExit as exc:
            result.rc = 1
            result.message = str(exc)
            print(result.message, file=sys.stderr)
            return result
        except Exception as exc:
            result.rc = 1
            result.message = "clean-build: staging failed: %s" % exc
            print(result.message, file=sys.stderr)
            return result

        result.findings.extend(findings or [])
        result.removed.extend(removed or [])
        if rc != 0 or out is None:
            result.rc = int(rc if rc is not None else 1)
            result.message = "clean-build: payload staging failed"
            print(result.message, file=sys.stderr)
            for finding in result.findings:
                try:
                    print("    - " + finding.describe(), file=sys.stderr)
                except Exception:
                    print("    - " + str(finding), file=sys.stderr)
            return result
        payload_dir = Path(out).resolve()
        result.payload_dir = payload_dir
    else:
        if not payload_dir.is_dir():
            result.rc = 1
            result.message = "payload directory does not exist: %s" % payload_dir
            print(result.message, file=sys.stderr)
            return result
        _refuse_protected(repo_root, payload_dir, "payload_dir")
        result.payload_dir = payload_dir

    # Release builds are fail-closed. Use the release manifest verifier's
    # independent artifact checks rather than trusting self-asserted JSON flags.
    if release:
        if STAGE_RUNTIME is None:
            result.rc = 1
            result.message = "clean-build: stage_clean_runtime verifier unavailable"
            print(result.message, file=sys.stderr)
            return result
        manifest_path = payload_dir / "runtime-manifest.json"
        try:
            STAGE_RUNTIME.verify_runtime_manifest(
                manifest=manifest_path,
                root=payload_dir,
                release=True,
            )
        except SystemExit as exc:
            result.rc = 1
            result.message = str(exc)
            print(result.message, file=sys.stderr)
            return result
        except Exception as exc:
            result.rc = 1
            result.message = "clean-build: runtime provenance verification failed: %s" % exc
            print(result.message, file=sys.stderr)
            return result

    # Prototype payloads (especially without dependency installation) are not
    # functional installers. Make that explicit instead of implying success.
    if not release:
        manifest_path = payload_dir / "runtime-manifest.json"
        dependencies_installed = None
        if manifest_path.is_file():
            try:
                manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
                dependencies_installed = manifest_data.get("dependencies_installed")
                backend_only = manifest_data.get("backend_dir") is not None and manifest_data.get("runtime_dir") is None
            except (OSError, ValueError, TypeError):
                manifest_data = {}
        if dependencies_installed is not True:
            print("[clean-build] WARNING: prototype/unverified payload has no installed runtime dependencies;"
                  " the resulting installer build is not functional. Use --allow-network for staging or sign/ship release payloads.")
    # A separately configured CARGO_TARGET_DIR on E: is intentionally preserved.
    if os.environ.get("CARGO_TARGET_DIR"):
        print("[clean-build] preserving separate CARGO_TARGET_DIR: %s" % os.environ["CARGO_TARGET_DIR"])

    if resources_dir is None:
        resources_dir = payload_dir
    else:
        resources_dir = _resolve(repo_root, resources_dir)
    _refuse_protected(repo_root, resources_dir, "resources_dir")

    if GENERATE_CONFIG is None:
        result.rc = 1
        result.message = "clean-build: generate_clean_installer_config module unavailable"
        print(result.message, file=sys.stderr)
        return result

    try:
        config_path = GENERATE_CONFIG.generate_clean_installer_config(
            repo_root=repo_root,
            output_dir=installer_output,
            resources_dir=resources_dir,
            prototype=prototype,
        )
    except Exception as exc:
        result.rc = 1
        result.message = str(exc)
        print(result.message, file=sys.stderr)
        return result

    result.config_path = Path(config_path).resolve()
    manifest_candidate = payload_dir / "runtime-manifest.json"
    if manifest_candidate.is_file():
        result.manifest_path = manifest_candidate.resolve()

    if run_build and build_command is not None:
        if isinstance(build_command, str):
            try:
                command_list = shlex.split(build_command, posix=(os.name != "nt"))
            except ValueError:
                command_list = build_command.split()
        elif isinstance(build_command, Sequence):
            command_list = [str(part) for part in build_command]
        else:
            command_list = [str(build_command)]

        for idx, part in enumerate(list(command_list)):
            part = part.replace("{config}", str(result.config_path))
            part = part.replace("{payload}", str(payload_dir))
            part = part.replace("{installer}", str(installer_output))
            part = part.replace("{repo}", str(repo_root))
            command_list[idx] = part

        env = dict(os.environ)
        env.setdefault("SYTHION_REPO_ROOT", str(repo_root))
        env.setdefault("SYTHION_PAYLOAD_DIR", str(payload_dir))
        env.setdefault("SYTHION_CONFIG_PATH", str(result.config_path))
        env.setdefault("SYTHION_PROTOTYPE", "1" if prototype else "0")
        env.setdefault("SYTHION_RELEASE", "1" if release else "0")
        env.setdefault("SYTHION_ALLOW_NETWORK", "1" if allow_network else "0")

        try:
            completed = subprocess.run(command_list, cwd=str(repo_root), env=env, check=False)
        except FileNotFoundError as exc:
            result.rc = 1
            result.message = "clean-build: build tool not found: %s" % exc
            print(result.message, file=sys.stderr)
            return result
        result.build_returncode = int(completed.returncode)
        result.build_command = command_list
        if completed.returncode != 0:
            result.rc = int(completed.returncode)
            result.message = "clean-build: build tool failed"
            print(result.message, file=sys.stderr)
            return result

    result.rc = 0
    result.mode = "release" if release else "prototype/unverified"
    result.message = "clean-build: OK payload=%s config=%s mode=%s" % (payload_dir, result.config_path, result.mode)
    print(result.message)
    if not run_build:
        print(
            "clean-build: next: npx --prefix frontend tauri build --no-bundle --config "
            + str(result.config_path)
        )
    return result


def run(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def build(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def build_clean(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def build_clean_checkout(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def clean_build(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def clean_source_build(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def clean_checkout_build(*args, **kwargs):
    return run_workflow(*args, **kwargs)


def execute(*args, **kwargs):
    return run_workflow(*args, **kwargs)


# Register methods after function defined.
for _name in ("run", "stage", "generate", "build"):
    setattr(CleanBuildWorkflow, _name, staticmethod(run_workflow))


def stage_payload(*args, **kwargs):
    merged = dict(kwargs)
    merged["stage"] = True
    merged["build"] = False
    return run_workflow(*args, **merged)


def generate_config(*args, **kwargs):
    merged = dict(kwargs)
    merged["stage"] = False
    merged["build"] = False
    return run_workflow(*args, **merged)


def invoke_build_tool(*args, **kwargs):
    merged = dict(kwargs)
    merged["build"] = True
    return run_workflow(*args, **merged)


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description="Safe clean source-checkout build workflow.")
    parser.add_argument("--repo-root", "--repo", dest="repo_root", type=Path, default=REPO_ROOT_DEFAULT)
    parser.add_argument("--source", "--source-dir", "--backend", dest="source", type=Path, default=None)
    parser.add_argument("--payload-dir", "--payload", "--isolated-output", dest="payload_dir", type=Path, default=None)
    parser.add_argument("--output", "--output-dir", "--installer-output", dest="generic_output", type=Path, default=None)
    parser.add_argument("--resources-dir", "--resources", dest="resources_dir", type=Path, default=None)
    parser.add_argument("--stage", dest="stage_flag", action="store_true", default=False)
    parser.add_argument("--no-stage", dest="no_stage", action="store_true", default=False)
    parser.add_argument("--stage-backend", dest="stage_backend", action="store_true", default=False)
    parser.add_argument("--stage-runtime", dest="stage_runtime", action="store_true", default=False)
    parser.add_argument("--allow-network", dest="allow_network", action="store_true", default=False)
    parser.add_argument("--release", dest="release", action="store_true", default=False)
    parser.add_argument("--prototype", dest="prototype_flag", action="store_true", default=False)
    parser.add_argument("--no-prototype", dest="no_prototype", action="store_true", default=False)
    parser.add_argument("--get-pip-sha256", "--get-pip-digest", dest="get_pip_sha256", default=None)
    parser.add_argument("--lock-path", "--lock", dest="lock_path", type=Path, default=None)
    parser.add_argument("--runtime-zip", "--python-zip", dest="runtime_zip", type=Path, default=None)
    parser.add_argument("--python-version", dest="python_version", default="3.13.16")
    parser.add_argument("--keep-failed", dest="keep_failed", action="store_true", default=False)
    parser.add_argument("--no-verify", dest="no_verify", action="store_true", default=False)
    parser.add_argument("--no-mirror-check", dest="no_mirror_check", action="store_true", default=False)
    parser.add_argument("--build-command", "--build-cmd", "--builder", dest="build_command", default=None)
    parser.add_argument("--build", dest="build", action="store_true", default=False)
    parser.add_argument("--no-build", dest="no_build", action="store_true", default=False)

    ns = parser.parse_args(argv)

    stage = True
    if ns.no_stage:
        stage = False
    if ns.stage_flag:
        stage = True
    prototype = None
    if ns.prototype_flag:
        prototype = True
    if ns.no_prototype:
        prototype = False
    if ns.release and prototype is None:
        prototype = False
    stage_backend_only = bool(ns.stage_backend and not ns.stage_runtime)

    try:
        result = run_workflow(
            repo_root=ns.repo_root,
            source=ns.source,
            payload_dir=ns.payload_dir,
            output=ns.generic_output,
            resources_dir=ns.resources_dir,
            stage=stage,
            stage_backend_only=stage_backend_only,
            allow_network=ns.allow_network,
            release=ns.release,
            prototype=prototype,
            get_pip_sha256=ns.get_pip_sha256,
            lock_path=ns.lock_path,
            runtime_zip=ns.runtime_zip,
            python_version=ns.python_version,
            keep_failed=ns.keep_failed,
            no_verify=ns.no_verify,
            no_mirror_check=ns.no_mirror_check,
            build_command=ns.build_command,
            build=(False if ns.no_build else (True if (ns.build or ns.build_command) else False)),
        )
    except SystemExit as exc:
        print(str(exc), file=sys.stderr)
        return 2
    except Exception as exc:
        print("clean-build: unexpected error: %s" % exc, file=sys.stderr)
        return 1

    print(result.message)
    return int(result.rc)


if __name__ == "__main__":
    raise SystemExit(main())
