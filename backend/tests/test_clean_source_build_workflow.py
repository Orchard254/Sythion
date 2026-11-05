"""Isolated offline tests for the clean source-checkout build workflow.

These tests prove the workflow can run from a clean checkout where
``src-tauri/resources`` is absent: it stages an *isolated* payload and never
falls back to the (possibly dirty) protected staged mirror. Synthetic trees
only; no network, no real profile, credentials or mirror are touched.
"""
from __future__ import annotations

import importlib.util
import json
import os
import pathlib
import shutil
import sys

import pytest

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "clean_source_build.py"
TEST_ZIP = REPO_ROOT / "build" / "staging" / "python-downloads" / "python-3.13.16-embed-amd64.zip"


def _load():
    spec = importlib.util.spec_from_file_location("clean_source_build_workflow_under_test", MODULE_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


M = _load()


def _make_repo(root: pathlib.Path) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    backend = root / "backend"
    backend.mkdir(parents=True, exist_ok=True)
    (backend / "main.py").write_text("print('hello')\n", encoding="utf-8")

    src_tauri = root / "src-tauri"
    src_tauri.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(REPO_ROOT / "src-tauri" / "tauri.conf.json", src_tauri / "tauri.conf.json")
    resources = src_tauri / "resources"
    if resources.exists():
        shutil.rmtree(resources)
    assert not resources.exists()

    frontend = root / "frontend" / "dist"
    frontend.mkdir(parents=True, exist_ok=True)
    (frontend / "index.html").write_text("<!doctype html><html></html>\n", encoding="utf-8")
    return root


def _result_is_failure(result) -> bool:
    return getattr(result, "rc", 1) != 0


def test_clean_checkout_workflow_succeeds_with_resources_absent(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "clean-checkout")
    protected = repo / "src-tauri" / "resources"
    assert not protected.exists()

    payload_dir = tmp_path / "isolated-payload"
    installer_dir = tmp_path / "installer"
    result = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )

    assert not _result_is_failure(result), result.message
    assert result.payload_dir == payload_dir.resolve()
    assert result.config_path == (installer_dir / "tauri.conf.json").resolve()
    assert payload_dir.is_dir()
    assert (payload_dir / "python-runtime" / "python.exe").is_file()
    assert (payload_dir / "backend" / "main.py").is_file()
    assert not protected.exists()
    config = json.loads(result.config_path.read_text(encoding="utf-8"))
    clean_values = {value for value in config["bundle"]["resources"].values() if value is not None}
    assert clean_values == {"python-runtime", "backend"}, config["bundle"]["resources"]
    for source in config["bundle"]["resources"]:
        if config["bundle"]["resources"][source] is not None:
            assert "src-tauri/resources" not in source.replace("\\", "/")
    assert "verify_release_payload.py" in config["build"]["beforeBuildCommand"]
    assert "--check-backend-mirror" in config["build"]["beforeBuildCommand"]


def test_clean_checkout_workflow_does_not_require_preexisting_mirror(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "no-mirror")
    assert not (repo / "src-tauri" / "resources").exists()

    payload_dir = tmp_path / "payload"
    installer_dir = tmp_path / "installer"
    result = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=True,
        verify=False,
    )

    assert not _result_is_failure(result), result.message
    assert (installer_dir / "tauri.conf.json").is_file()


def test_clean_checkout_workflow_backend_only_stage_fails_closed_without_runtime(tmp_path):
    repo = _make_repo(tmp_path / "backend-only")
    payload_dir = tmp_path / "payload"
    installer_dir = tmp_path / "installer"

    result = M.run_workflow(
        repo_root=repo,
        stage=True,
        stage_backend_only=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        verify=False,
    )

    assert _result_is_failure(result), "backend-only staging cannot produce an installer config"
    assert result.config_path is None
    assert not (installer_dir / "tauri.conf.json").exists()


def test_clean_checkout_workflow_refuses_protected_mirror_as_payload_dir(tmp_path):
    repo = _make_repo(tmp_path / "protected-payload")
    protected = repo / "src-tauri" / "resources"

    try:
        result = M.run_workflow(
            repo_root=repo,
            stage=False,
            payload_dir=protected,
            installer_output=tmp_path / "installer",
        )
        failed = _result_is_failure(result)
        message = (result.message if result is not None else "").lower()
    except SystemExit as exc:
        failed = True
        message = str(exc).lower()

    assert failed
    assert "protected" in message or "mirror" in message
    assert not (tmp_path / "installer" / "tauri.conf.json").exists()


def test_clean_checkout_workflow_refuses_protected_mirror_as_resources_dir(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "protected-resources")
    protected = repo / "src-tauri" / "resources"
    payload_dir = tmp_path / "good-payload"
    installer_seed = tmp_path / "seed-installer"
    seed_result = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_seed,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert not _result_is_failure(seed_result), seed_result.message

    refused_installer = tmp_path / "refused-installer"
    try:
        result = M.run_workflow(
            repo_root=repo,
            stage=False,
            payload_dir=payload_dir,
            resources_dir=protected,
            installer_output=refused_installer,
        )
        failed = _result_is_failure(result)
        message = (result.message if result is not None else "").lower()
    except SystemExit as exc:
        failed = True
        message = str(exc).lower()

    assert failed
    assert "protected" in message or "mirror" in message
    assert not (refused_installer / "tauri.conf.json").exists()


def test_clean_checkout_workflow_refuses_overwrite_of_existing_output(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "no-clobber")
    payload_dir = tmp_path / "payload"
    installer_dir = tmp_path / "installer"

    first = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert not _result_is_failure(first), first.message

    second = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert _result_is_failure(second)
    assert (installer_dir / "tauri.conf.json").is_file()


def test_clean_checkout_workflow_resources_absent_after_run(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "absent-after")
    protected_before = sorted(str(p) for p in (repo / "src-tauri").rglob("*"))
    payload_dir = tmp_path / "payload"
    installer_dir = tmp_path / "installer"

    result = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=payload_dir,
        installer_output=installer_dir,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert not _result_is_failure(result), result.message

    protected_after = sorted(str(p) for p in (repo / "src-tauri").rglob("*"))
    assert protected_after == protected_before
    assert not (repo / "src-tauri" / "resources").exists()


@pytest.mark.skipif(not pathlib.Path("E:/").exists(), reason="synthetic E: drive unavailable")
def test_clean_checkout_workflow_cross_drive_resources_fail_closed(tmp_path):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "cross-drive-repo")
    staging_root = tmp_path / "payload"
    installer_seed = tmp_path / "seed-installer"
    seed = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=staging_root,
        installer_output=installer_seed,
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert not _result_is_failure(seed), seed.message

    e_payload = pathlib.Path("E:/oc-clean-workflow-cross-drive-test")
    try:
        if e_payload.exists():
            shutil.rmtree(e_payload)
        shutil.copytree(staging_root, e_payload)

        try:
            result = M.run_workflow(
                repo_root=repo,
                stage=False,
                payload_dir=e_payload,
                resources_dir=e_payload,
                installer_output=tmp_path / "cross-installer",
                verify=False,
            )
            failed = _result_is_failure(result)
            message = (result.message if result is not None else "").lower()
        except SystemExit as exc:
            failed = True
            message = str(exc).lower()

        assert failed, "cross-drive payload must fail closed"
        assert ("cross-drive" in message) or ("same windows drive" in message) or ("same drive" in message), message
        assert not (tmp_path / "cross-installer" / "tauri.conf.json").exists()
    finally:
        if e_payload.exists():
            shutil.rmtree(e_payload, ignore_errors=True)


def test_clean_checkout_workflow_no_mirror_file_reads(tmp_path, monkeypatch):
    if not TEST_ZIP.is_file():
        pytest.skip("embedded Python archive fixture is unavailable")

    repo = _make_repo(tmp_path / "side-effect")
    opened = []

    original_read_text = pathlib.Path.read_text

    def tracking_read_text(self, *args, **kwargs):
        opened.append(str(self))
        return original_read_text(self, *args, **kwargs)

    monkeypatch.setattr(pathlib.Path, "read_text", tracking_read_text)

    result = M.run_workflow(
        repo_root=repo,
        stage=True,
        payload_dir=tmp_path / "payload",
        installer_output=tmp_path / "installer",
        allow_network=False,
        install_dependencies=False,
        bootstrap_pip=False,
        runtime_zip=TEST_ZIP,
        mirror_check=False,
        verify=False,
    )
    assert not _result_is_failure(result), result.message

    opened_text = "\n".join(opened).replace("\\", "/").lower()
    assert "src-tauri/resources" not in opened_text
