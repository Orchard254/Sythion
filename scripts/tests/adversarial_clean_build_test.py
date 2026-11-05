"""Adversarial tests."""
from __future__ import annotations
import importlib.util, json, pathlib, shutil, sys, tempfile

REPO_ROOT = pathlib.Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "clean_source_build.py"

spec = importlib.util.spec_from_file_location("csb", MODULE_PATH)
csb = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = csb
spec.loader.exec_module(csb)


def _minimal_payload(root):
    payload = root / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    (payload / "backend").mkdir(parents=True, exist_ok=True)
    (payload / "backend" / "main.py").write_text("x\n", encoding="utf-8")
    (payload / "python-runtime").mkdir(parents=True, exist_ok=True)
    (payload / "python-runtime" / "python.exe").write_bytes(b"")
    return payload


def test_backend_only_release_is_invalid():
    repo = pathlib.Path(tempfile.mkdtemp()) / "repo"
    repo.mkdir()
    result = csb.run_workflow(repo, release=True, stage_backend_only=True, stage=False)
    assert result.rc != 0


def _forged_manifest(payload):
    return {
        "python_version": "3.13.16",
        "runtime_dir": "python-runtime",
        "backend_dir": "backend",
        "python_sha256": "0" * 64,
        "provenance_mode": "release",
        "lock_path": str(payload / "missing.lock"),
        "lock_sha256": "f" * 64,
        "bootstrap_pip": True,
        "pip_bootstrapped": True,
        "dependencies_installed": True,
        "get_pip_sha256": "1" * 64,
        "get_pip_verified": True,
        "release_provenance_verified": True,
    }


def test_forged_manifest_with_true_booleans_is_rejected(tmp_path):
    payload = _minimal_payload(tmp_path)
    (payload / "runtime-manifest.json").write_text(json.dumps(_forged_manifest(payload)), encoding="utf-8")
    installer = tmp_path / "installer"
    result = csb.run_workflow(REPO_ROOT, payload_dir=payload, output_dir=installer, stage=False, release=True)
    assert result.rc != 0
    assert not (installer / "tauri.conf.json").exists()


def test_forged_false_release_fact_is_rejected(tmp_path):
    payload = _minimal_payload(tmp_path)
    forged = _forged_manifest(payload)
    forged["bootstrap_pip"] = False
    forged["pip_bootstrapped"] = False
    forged["dependencies_installed"] = False
    forged["get_pip_verified"] = False
    forged["release_provenance_verified"] = False
    (payload / "runtime-manifest.json").write_text(json.dumps(forged), encoding="utf-8")
    installer = tmp_path / "installer2"
    result = csb.run_workflow(REPO_ROOT, payload_dir=payload, output_dir=installer, stage=False, release=True)
    assert result.rc != 0


def test_prototype_payload_is_not_release(tmp_path):
    payload = _minimal_payload(tmp_path)
    (payload / "runtime-manifest.json").write_text(json.dumps({"provenance_mode": "prototype/unverified"}), encoding="utf-8")
    installer = tmp_path / "installer3"
    result = csb.run_workflow(REPO_ROOT, payload_dir=payload, output_dir=installer, stage=False, release=True)
    assert result.rc != 0
def test_cross_drive_payload_is_fail_closed():
    result = csb.run_workflow(REPO_ROOT, payload_dir=pathlib.Path("E:/nonexistent/clean-payload"), output_dir=REPO_ROOT / "build" / "clean-installer", stage=False)
    assert result.rc != 0
    message = str(result.message).lower()
    assert "same windows drive" in message or "cross-drive" in message
def test_cross_drive_resources_are_fail_closed(tmp_path):
    payload = _minimal_payload(tmp_path)
    installer = tmp_path / "installer-resources"
    result = csb.run_workflow(REPO_ROOT, payload_dir=payload, resources_dir=pathlib.Path("E:/cross-drive-resources"), output_dir=installer, stage=False)
    assert result.rc != 0
    assert "same windows drive" in str(result.message).lower() or "cross-drive" in str(result.message).lower()
