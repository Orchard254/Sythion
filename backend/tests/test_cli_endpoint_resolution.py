"""Endpoint resolution for operator tools must come from the handshake file."""

import importlib.util
import json
import os
import sys

import pytest

from backend.core import storage

REPO_ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), "..", ".."))


def _load_module(name, relative_path):
    path = os.path.join(REPO_ROOT, *relative_path.split("/"))
    spec = importlib.util.spec_from_file_location(name, path)
    module = importlib.util.module_from_spec(spec)
    if REPO_ROOT not in sys.path:
        sys.path.insert(0, REPO_ROOT)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def profile(tmp_path, monkeypatch):
    target = tmp_path / "sythion-profile"
    target.mkdir()
    (target / "sidecar.json").write_text(
        json.dumps({"port": 54321, "token": "test-token"}), encoding="utf-8"
    )
    monkeypatch.setenv("SYTHION_DATA_DIR", str(target))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    return target


def test_cli_endpoint_resolution(profile):
    expected_path = os.path.join(
        os.path.normpath(os.path.abspath(str(profile))), "sidecar.json"
    )
    expected_url = "http://127.0.0.1:54321"

    assert storage.handshake_path() == expected_path

    smoke = _load_module("smoke_test_under_test", "scripts/smoke_test.py")
    backend_cli = _load_module("backend_cli_under_test", "backend/cli.py")
    root_cli = _load_module("root_cli_under_test", "cli.py")

    assert smoke.resolve_handshake_path() == expected_path
    assert backend_cli.resolve_handshake_path() == expected_path
    assert root_cli.resolve_handshake_path() == expected_path
    assert smoke.resolve_sidecar_path() == backend_cli.handshake_path() == root_cli.resolve_handshake_path()

    assert smoke.resolve_base_url() == expected_url
    assert backend_cli.resolve_base_url() == expected_url
    assert root_cli.resolve_base_url() == expected_url

    assert smoke.get_base_url() == expected_url + "/api/v1"
    assert root_cli.get_base_url() == expected_url + "/api/v1"

    token, base_url = backend_cli._get_token_and_url()
    assert token == "test-token"
    assert base_url == expected_url

    source = os.path.join(REPO_ROOT, "backend", "cli.py")
    mirror = os.path.join(REPO_ROOT, "src-tauri", "resources", "backend", "cli.py")
    with open(source, encoding="utf-8") as f:
        source_text = f.read()
    with open(mirror, encoding="utf-8") as f:
        mirror_text = f.read()
    assert source_text == mirror_text