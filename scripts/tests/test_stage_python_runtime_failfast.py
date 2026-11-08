"""Offline fail-fast tests for legacy stage_python_runtime backend staging.

These tests never touch the network, never build, and never touch the staged
mirror at src-tauri/resources/backend. Synthetic tmp trees and spies only.
"""
from __future__ import annotations

import importlib.util
import pathlib
import shutil
import sys

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "stage_python_runtime.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


S = _load(_MODULE_PATH, "stage_python_runtime_failfast_under_test")


def test_stage_backend_deprecated_refuses_before_any_path_mutation(tmp_path, monkeypatch):
    backend = tmp_path / "legacy-backend"
    backend.mkdir()
    sentinel = backend / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")

    monkeypatch.setattr(S, "BACKEND_DIR", backend)

    rmtrees = []
    copytrees = []
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: rmtrees.append((a, k)))
    monkeypatch.setattr(shutil, "copytree", lambda *a, **k: copytrees.append((a, k)))

    import pytest

    with pytest.raises(SystemExit) as exc:
        S.stage_backend()

    msg = str(exc.value).lower()
    assert "stage_clean_backend" in msg
    assert "verify_release_payload" in msg
    assert rmtrees == [], "legacy stage_backend must not rmtree before refusing"
    assert copytrees == [], "legacy stage_backend must not copytree before refusing"
    assert sentinel.is_file()
    assert sentinel.read_text(encoding="utf-8") == "preserve"


def test_main_legacy_full_staging_refuses_before_effects(tmp_path, monkeypatch):
    calls = {"stage_embedded_python": 0, "install_requirements": 0,
            "stage_backend": 0, "verify_payload": 0}

    def counter(name):
        def _fn(*a, **k):
            calls[name] += 1
            return "embedded.exe"

        return _fn

    # Real synthetic targets so any accidental write/stage is observable; these
    # remain untouched because main() must fail before any expensive effect.
    backend = tmp_path / "legacy-backend"
    runtime = tmp_path / "runtime"
    resources = tmp_path / "resources"
    downloads = tmp_path / "downloads"
    for path in (backend, runtime, resources, downloads):
        path.mkdir()
    sentinel = backend / "sentinel.txt"
    sentinel.write_text("preserve", encoding="utf-8")
    before = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))

    monkeypatch.setattr(S, "BACKEND_DIR", backend)
    monkeypatch.setattr(S, "RUNTIME_DIR", runtime)
    monkeypatch.setattr(S, "RESOURCES", resources)
    monkeypatch.setattr(S, "SRC_TAURI", resources.parent)
    monkeypatch.setattr(S, "DOWNLOAD_DIR", downloads)
    monkeypatch.setattr(S, "stage_embedded_python", counter("stage_embedded_python"))
    monkeypatch.setattr(S, "install_requirements", counter("install_requirements"))
    monkeypatch.setattr(S, "stage_backend", counter("stage_backend"))
    monkeypatch.setattr(S, "verify_payload", counter("verify_payload"))

    rmtrees = []
    copytrees = []
    monkeypatch.setattr(shutil, "rmtree", lambda *a, **k: rmtrees.append((a, k)))
    monkeypatch.setattr(shutil, "copytree", lambda *a, **k: copytrees.append((a, k)))

    import subprocess

    def _no_subprocess(*a, **k):
        raise AssertionError("subprocess must not run during legacy refusal")

    monkeypatch.setattr(subprocess, "run", _no_subprocess)

    import pytest

    with pytest.raises(SystemExit) as exc:
        S.main([])

    msg = str(exc.value).lower()
    # Actionable pointers to the supported isolated staging/verification flow.
    assert "stage_clean_backend" in msg
    assert "verify_release_payload" in msg
    assert "clean-release-payload" in msg
    assert "check-backend-mirror" in msg
    assert calls == {
        "stage_embedded_python": 0,
        "install_requirements": 0,
        "stage_backend": 0,
        "verify_payload": 0,
    }
    assert rmtrees == [], "legacy main refused but staged rmtree occurred"
    assert copytrees == [], "legacy main refused but staged copytree occurred"
    assert sentinel.is_file(), "legacy main touched the backend sentinel"
    assert sentinel.read_text(encoding="utf-8") == "preserve"
    after = sorted(str(p.relative_to(tmp_path)) for p in tmp_path.rglob("*"))
    assert before == after, "legacy main changed the filesystem during refusal"


def test_main_retains_legacy_argument_parsing_before_refusal(monkeypatch, tmp_path):
    for name in ("stage_embedded_python", "install_requirements", "stage_backend", "verify_payload"):
        monkeypatch.setattr(S, name, lambda *a, **k: None)

    import pytest

    with pytest.raises(SystemExit):
        S.main(["--lock", str(tmp_path / "lock.txt"),
               "--regenerate-lock",
               "--python-version", S.PYTHON_VERSION])


def test_stage_embedded_python_helper_remains_available():
    assert callable(S.stage_embedded_python)
    assert callable(S.fail)
