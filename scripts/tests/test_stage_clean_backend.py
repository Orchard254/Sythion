"""Offline tests for stage_clean_backend.py.

These tests never touch the network, never build, and never touch the existing
staged mirror at src-tauri/resources/backend. Synthetic tmp trees only.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import shutil
import sys

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "stage_clean_backend.py"
_VERIFIER_PATH = pathlib.Path(__file__).resolve().parent.parent / "verify_release_payload.py"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


S = _load(_MODULE_PATH, "stage_clean_backend_under_test")
V = _load(_VERIFIER_PATH, "verify_release_payload_for_stage_test")


def _make_tree(root: pathlib.Path, mapping: dict) -> pathlib.Path:
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in mapping.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.write_text(content, encoding="utf-8")
    return root


def test_stage_produces_clean_isolated_payload(tmp_path):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print('hello')\n",
        "cli.py": "print('cli')\n",
        ".env": "API_KEY=" + "K" * 40 + "\n",
        ".env.example": "API_KEY=<YOUR_KEY>\n",
        "secret.pem": "not shipped\n",
        "app.db": "not shipped\n",
        "scratchpad/probe.py": "print('drift?')\n",
        "sub/__pycache__/x.pyc": "nope\n",
        "tests/test_main.py": "def test_x(): pass\n",
    })
    output = tmp_path / "isolated-root"

    rc, findings, removed, out = S.stage(source, output)

    assert rc == 0, (rc, [f.describe() for f in findings])
    assert out == output.resolve()
    assert output.is_dir()
    backend = output / "backend"
    assert (backend / "main.py").is_file()
    assert (backend / "cli.py").is_file()
    assert not (backend / "scratchpad" / "probe.py").exists()
    assert (backend / ".env.example").is_file()
    # Prohibited artifacts never enter the new isolated tree.
    assert not (backend / ".env").exists()
    assert not (backend / "secret.pem").exists()
    assert not (backend / "app.db").exists()
    assert not (backend / "tests").exists()
    assert not (backend / "sub" / "__pycache__").exists()
    # Script did not touch the real staged mirror.
    assert not (output / "src-tauri").exists()


def test_stage_does_not_open_secret_material(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print(1)\n",
        ".env": "API_KEY=" + "S" * 40 + "\n",
    })
    opened = []

    orig = pathlib.Path.read_text
    def patched(self, *a, **k):
        opened.append(str(self))
        return orig(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", patched)

    output = tmp_path / "isolated"
    rc, findings, removed, out = S.stage(source, output)
    assert rc == 0, [f.describe() for f in findings]
    rel_opened = {os.path.relpath(p, str(output)).replace("\\", "/") for p in opened}
    # .env is never copied, therefore it cannot have been opened.
    assert not any(p == "backend/.env" or p.endswith("/.env") for p in rel_opened)


def test_stage_fails_closed_and_leaves_no_output_for_bad_source(tmp_path):
    # A hardcoded credential in source code should make verification fail.
    source = _make_tree(tmp_path / "source", {
        "main.py": 'API_KEY = "' + "Z" * 40 + '"\n',
    })
    output = tmp_path / "isolated-output"

    rc, findings, removed, out = S.stage(source, output)

    assert rc == 1
    assert out is None
    assert not output.exists(), "failed staging must not promote an output directory"
    assert any(f.kind == "secret-assignment" for f in findings)


def test_stage_refuses_protected_output_paths(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    # Point SRC_TAURI_RESOURCES at a fresh temp path for this unit check.
    temp_protected = tmp_path / "protected-resources"
    temp_protected.mkdir()
    old = S.SRC_TAURI_RESOURCES
    S.SRC_TAURI_RESOURCES = temp_protected
    try:
        try:
            S._validate_paths(source, temp_protected / "backend")
        except SystemExit as exc:
            assert "protected" in str(exc) or "staged mirror" in str(exc)
        else:
            raise AssertionError("expected refusal for protected output path")
    finally:
        S.SRC_TAURI_RESOURCES = old


def test_stage_refuses_overlapping_source_output(tmp_path):
    source = tmp_path / "source"
    source.mkdir()
    (source / "main.py").write_text("print(1)\n", encoding="utf-8")
    output = source / "nested"
    try:
        S._validate_paths(source, output)
    except SystemExit as exc:
        assert "overlap" in str(exc).lower()
    else:
        raise AssertionError("expected refusal for overlapping paths")


def test_stage_without_overwrite_refuses_existing_output(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    # Pre-create an unrelated file (simulating stale output).
    output.mkdir()
    (output / "stale.txt").write_text("stale\n", encoding="utf-8")

    try:
        S.stage(source, output)
    except SystemExit as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected refusal to overwrite without --overwrite")


def test_backend_mirror_clean_after_staging(tmp_path):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print(1)\n",
        "api/routes.py": "x = 1\n",
        ".env": "API_KEY=" + "R" * 40 + "\n",
    })
    output = tmp_path / "root"
    rc, findings, removed, out = S.stage(source, output)
    assert rc == 0, [f.describe() for f in findings]
    # Re-running the verifier's mirror check independently stays clean.
    more = V.backend_mirror_findings(source, output / "backend")
    assert more == [], [f.describe() for f in more]
    scan = V.scan_tree(output)
    assert scan == [], [f.describe() for f in scan]


def test_stage_rejects_overwrite_argument(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    output.mkdir()
    (output / "stale.txt").write_text("stale\n", encoding="utf-8")

    try:
        S.stage(source, output, overwrite=True)
    except SystemExit as exc:
        assert "overwrite" in str(exc).lower() or "disabled" in str(exc).lower()
    else:
        raise AssertionError("expected stage to reject overwrite=True")

    assert (output / "stale.txt").is_file(), "rejected overwrite must not delete existing output"


def test_cli_main_end_to_end_fresh_output(tmp_path, capsys):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print('hello')\n",
        "cli.py": "print('cli')\n",
        ".env": "API_KEY=" + "K" * 40 + "\\n",
        ".env.local": "SECRET=x\\n",
        "secret.pem": "not shipped\\n",
        "app.db": "not shipped\\n",
        "tests/test_main.py": "def test_x(): pass\\n",
        "scratchpad/probe.py": "print('probe')\\n",
    })
    output = tmp_path / "cli-clean-output"

    rc = S.main(["--source", str(source), "--output", str(output)])
    assert rc == 0, rc
    assert output.is_dir()
    assert (output / "backend" / "main.py").is_file()
    assert not (output / "backend" / "scratchpad").exists()
    assert not (output / "backend" / ".env").exists()
    assert not (output / "backend" / ".env.local").exists()
    assert not (output / "backend" / "secret.pem").exists()
    assert not (output / "backend" / "app.db").exists()
    assert not (output / "backend" / "tests").exists()
    out = capsys.readouterr().out
    assert "isolated clean payload" in out

    # Strict no-overwrite: a second CLI run against the now-existing output
    # must fail closed without deleting the prior output.
    rc2 = S.main(["--source", str(source), "--output", str(output)])
    assert rc2 == 2
    err = capsys.readouterr().err
    assert "already exists" in err
    assert (output / "backend" / "main.py").is_file()
