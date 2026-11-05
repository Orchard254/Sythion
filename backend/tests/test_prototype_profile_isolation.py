"""Focused tests for explicit prototype build profile isolation."""

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(REPO_ROOT / "scripts"))

import importlib.util

spec = importlib.util.spec_from_file_location(
    "generate_clean_installer_config_test", REPO_ROOT / "scripts" / "generate_clean_installer_config.py"
)
assert spec and spec.loader
gen = importlib.util.module_from_spec(spec)
sys.modules[spec.name] = gen
spec.loader.exec_module(gen)

VERIFY_SPEC = importlib.util.spec_from_file_location(
    "verify_release_payload_test", REPO_ROOT / "scripts" / "verify_release_payload.py"
)
assert VERIFY_SPEC and VERIFY_SPEC.loader
VERIFY = importlib.util.module_from_spec(VERIFY_SPEC)
sys.modules[VERIFY_SPEC.name] = VERIFY
VERIFY_SPEC.loader.exec_module(VERIFY)


def _make_payload(tmp_path: Path) -> Path:
    payload = tmp_path / "payload"
    if payload.exists():
        shutil.rmtree(payload)
    dst_backend = payload / "backend"
    dst_backend.parent.mkdir(parents=True, exist_ok=True)
    shutil.copytree(
        REPO_ROOT / "backend",
        dst_backend,
        ignore=VERIFY.payload_copy_ignore(),
    )
    (payload / "python-runtime").mkdir(parents=True, exist_ok=True)
    (payload / "python-runtime" / "python.exe").write_bytes(b"")
    return payload


def test_default_clean_installer_keeps_production_identity(tmp_path):
    payload = _make_payload(tmp_path)
    out = tmp_path / "production-clean"
    config_path = gen.generate_clean_installer_config(REPO_ROOT, out, payload)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["productName"] != "Sythion (Synth) Prototype"
    assert config["identifier"] != "ai.sythion.desktop.prototype"
    assert "Sythion" in config["productName"]


def test_prototype_flag_rebrands_generated_config(tmp_path):
    payload = _make_payload(tmp_path)
    out = tmp_path / "prototype-clean"
    config_path = gen.generate_clean_installer_config(
        REPO_ROOT, out, payload, prototype=True
    )
    config = json.loads(config_path.read_text(encoding="utf-8"))
    assert config["productName"] == "Sythion (Synth) Prototype"
    assert config["identifier"] == "ai.sythion.desktop.prototype"


def test_cli_prototype_flag_propagates_identity(tmp_path, capsys):
    payload = _make_payload(tmp_path)
    output_dir = tmp_path / "cli-prototype"
    rc = gen.main(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(output_dir),
            "--resources-dir",
            str(payload),
            "--prototype",
        ]
    )
    assert rc == 0
    config = json.loads((output_dir / "tauri.conf.json").read_text(encoding="utf-8"))
    assert config["productName"] == "Sythion (Synth) Prototype"
    assert config["identifier"] == "ai.sythion.desktop.prototype"
