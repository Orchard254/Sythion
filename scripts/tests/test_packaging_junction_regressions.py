"""Disposable junction/reparse-point regressions for packaging safety.

These checks exercise the verifier against real Windows junctions. They must
never follow the junction into the external target; prohibited material there
must survive unchanged.

Junctions are created inside ordinary temporary directories so the checks do not
depend on a particular drive such as ``E:/``. When the local platform or test
environment cannot create a junction, the tests skip instead of reporting an
environmental failure.
"""
from __future__ import annotations

import importlib.util
import os
import pathlib
import subprocess
import sys
import tempfile

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "verify_release_payload.py"

spec = importlib.util.spec_from_file_location("verify_release_payload_junction_test", _MODULE_PATH)
assert spec is not None and spec.loader is not None
V = importlib.util.module_from_spec(spec)
sys.modules.setdefault(spec.name, V)
spec.loader.exec_module(V)


def _is_windows_junction_available() -> bool:
    """Return whether this environment can create and detect a real junction."""
    if os.name != "nt":
        return False
    try:
        with tempfile.TemporaryDirectory() as base_name:
            base = pathlib.Path(base_name)
            target = base / "target"
            target.mkdir()
            link = base / "link"
            result = subprocess.run(
                ["cmd", "/c", "mklink", "/J", str(link), str(target)],
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
            )
            if result.returncode != 0:
                return False
            is_reparse_point = V._is_reparse_point(link)
            V._safe_rmtree(link)
            return bool(is_reparse_point)
    except (OSError, RuntimeError, ValueError, AssertionError):
        return False


def _make_junction(target, link):
    link.parent.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(target)],
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    if result.returncode != 0:
        import pytest

        pytest.skip(f"junction creation unavailable: {result.stderr.strip()}")
    assert V._is_reparse_point(link) is True


def _write(root, rel, content):
    p = root / rel
    p.parent.mkdir(parents=True, exist_ok=True)
    p.write_text(content, encoding="utf-8")
    return p


def test_scan_tree_root_junction_fails_closed(tmp_path):
    if not _is_windows_junction_available():
        import pytest

        pytest.skip("junction creation unavailable")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    secret = "junction-secret-" + ("9" * 32)
    _write(target, ".env", "SYTHION_OLLAMA_API_KEY=" + secret + "\n")
    _write(target, "tests/test_external.py", "def test_x(): pass\n")
    _make_junction(target, link)

    findings = V.scan_tree(link)
    assert findings
    assert all(f.kind == "reparse-point" for f in findings)
    joined = "\n".join(f.describe() for f in findings)
    assert secret not in joined
    # Nothing external was classified as ordinary payload content.
    assert not any(f.path in {".env", "tests/test_external.py"} for f in findings)

    # The external target remains unscanned and untouched.
    assert (target / ".env").is_file()
    assert (target / "tests" / "test_external.py").is_file()
    V._safe_rmtree(link)


def test_prune_tree_root_junction_does_not_delete_external_target(tmp_path):
    if not _is_windows_junction_available():
        import pytest

        pytest.skip("junction creation unavailable")
    target = tmp_path / "external-target"
    target.mkdir()
    link = tmp_path / "junction"
    _write(target, ".env", "API_KEY=" + "J" * 40 + "\n")
    _write(target, "tests/test_external.py", "x")
    _write(target, "app.db", "binary")
    _make_junction(target, link)

    removed = V.prune_tree(link)
    assert removed == []
    assert (target / ".env").is_file()
    assert (target / "tests" / "test_external.py").is_file()
    assert (target / "app.db").is_file()
    V._safe_rmtree(link)


def test_prune_tree_does_not_descend_through_nested_junction(tmp_path):
    if not _is_windows_junction_available():
        import pytest

        pytest.skip("junction creation unavailable")
    root = tmp_path / "payload"
    root.mkdir()
    # Owned, removable prohibited file at the real root.
    _write(root, "app.db", "owned-binary")
    assert (root / "app.db").is_file()

    target = tmp_path / "external-target"
    target.mkdir()
    _write(target, ".env", "API_KEY=" + "K" * 40 + "\n")
    _write(target, "tests/test_external.py", "x")
    nested = root / "external"
    _make_junction(target, nested)

    removed = V.prune_tree(root)
    # Only the owned artifact inside the real root may be removed.
    assert any("app.db" in item for item in removed)
    assert all("external" not in item for item in removed)
    assert not (root / "app.db").exists()
    # The junction itself survives; probing its target must remain intact.
    V._safe_rmtree(nested)
    removed_again = V.prune_tree(root)
    assert removed_again == []
    assert (target / ".env").is_file()
    assert (target / "tests" / "test_external.py").is_file()


def test_verify_cli_root_junction_fails_closed(tmp_path, capsys):
    if not _is_windows_junction_available():
        import pytest

        pytest.skip("junction creation unavailable")
    target = tmp_path / "target"
    target.mkdir()
    link = tmp_path / "junction"
    _write(target, "main.py", "print(1)\n")
    _make_junction(target, link)

    rc = V.main([str(link), "--json"])
    captured = capsys.readouterr()
    assert rc == 1
    assert "reparse-point" in captured.out
    assert (target / "main.py").is_file()
    V._safe_rmtree(link)
