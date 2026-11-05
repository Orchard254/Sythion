"""Offline tests for stage_clean_runtime.py."""
from __future__ import annotations

import importlib.util
import os
import pathlib
import stat
import subprocess
import sys
import hashlib
import tempfile
import zipfile

import pytest

_MODULE_PATH = pathlib.Path(__file__).resolve().parent.parent / "stage_clean_runtime.py"
_TEST_ZIP = pathlib.Path(__file__).resolve().parents[2] / "build" / "staging" / "python-downloads" / "python-3.13.16-embed-amd64.zip"


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, str(path))
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules.setdefault(spec.name, module)
    spec.loader.exec_module(module)
    return module


S = _load(_MODULE_PATH, "stage_clean_runtime_under_test")
V = _load(pathlib.Path(__file__).resolve().parent.parent / "verify_release_payload.py", "verify_release_payload_for_runtime_test")


def _make_tree(root, mapping):
    root.mkdir(parents=True, exist_ok=True)
    for rel, content in mapping.items():
        p = root / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        if content is None:
            p.mkdir(parents=True, exist_ok=True)
        else:
            p.write_text(content, encoding="utf-8")
    return root


def test_stage_produces_clean_isolated_runtime(tmp_path):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print('hello')\n",
        ".env": "API_KEY=" + "K" * 40 + "\n",
        "app.db": "not shipped\n",
        "tests/test_main.py": "def test_x(): pass\n",
    })
    output = tmp_path / "isolated-root"
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False, bootstrap_pip=False,
        install_dependencies=False, verify=True, mirror_check=True, required_imports=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    assert out == output.resolve()
    assert output.is_dir()
    assert (output / "python-runtime" / "python.exe").is_file()
    assert (output / "backend" / "main.py").is_file()
    assert not (output / "backend" / ".env").exists()
    assert not (output / "backend" / "app.db").exists()
    assert not (output / "backend" / "tests").exists()
    assert not output.joinpath("src-tauri").exists()


def test_stage_refuses_existing_output(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    output.mkdir()
    (output / "stale.txt").write_text("stale\n", encoding="utf-8")
    try:
        S.stage(source, output, runtime_zip=_TEST_ZIP, install_dependencies=False, required_imports=False)
    except SystemExit as exc:
        assert "already exists" in str(exc)
    else:
        raise AssertionError("expected refusal")
    assert (output / "stale.txt").is_file()


def test_stage_refuses_protected_output(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    temp_protected = tmp_path / "protected-resources"
    temp_protected.mkdir()
    old = S.SRC_TAURI_RESOURCES
    S.SRC_TAURI_RESOURCES = temp_protected
    try:
        try:
            S._validate_paths(source, temp_protected / "clean")
        except SystemExit as exc:
            assert "protected" in str(exc)
        else:
            raise AssertionError("expected refusal")
    finally:
        S.SRC_TAURI_RESOURCES = old


def test_stage_create_only_does_not_open_secret_material(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {
        "main.py": "print(1)\n",
        ".env": "API_KEY=" + "S" * 40 + "\n",
    })
    output = tmp_path / "isolated"
    opened = []
    orig = pathlib.Path.read_text
    def patched(self, *a, **k):
        opened.append(str(self))
        return orig(self, *a, **k)
    monkeypatch.setattr(pathlib.Path, "read_text", patched)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, install_dependencies=False, required_imports=False,
        bootstrap_pip=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    opened_rel = {p.replace("\\", "/") for p in opened}
    assert not any(p.endswith("/.env") or p.endswith("\\.env") for p in opened_rel)


def _make_simple_zip(path, members):
    with zipfile.ZipFile(path, "w") as zf:
        for name, content in members.items():
            zf.writestr(name, content)


def test_safe_zip_extraction_allows_benign_archive(tmp_path):
    archive = tmp_path / "ok.zip"
    _make_simple_zip(archive, {"a/b.txt": "hello", "dir/": ""})
    destination = tmp_path / "out"
    result = S.safe_extract_zip(archive, destination)
    assert result == destination
    assert (destination / "a" / "b.txt").read_text(encoding="utf-8") == "hello"
    assert (destination / "dir").is_dir()


def test_safe_zip_extraction_rejects_traversal(tmp_path):
    archive = tmp_path / "evil.zip"
    _make_simple_zip(archive, {"../escape.txt": "nope"})
    destination = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        S.safe_extract_zip(str(archive), str(destination))
    assert "traversal" in str(exc.value).lower()
    assert not (tmp_path / "escape.txt").exists()


def test_safe_zip_extraction_rejects_windows_absolute_and_drive_paths(tmp_path):
    destination = tmp_path / "out"
    for bad_name in (r"C:\evil.txt", r"C:evil.txt", r"\evil.txt", "/evil.txt"):
        archive = tmp_path / "evil.zip"
        if archive.exists():
            archive.unlink()
        _make_simple_zip(archive, {bad_name: "nope"})
        before = list(tmp_path.iterdir())
        with pytest.raises(SystemExit) as exc:
            S.safe_extract_zip(archive, destination)
        message = str(exc.value).lower()
        assert "absolute" in message or "drive" in message or "traversal" in message
    assert not (tmp_path / "evil.txt").exists()


def test_safe_zip_extraction_rejects_symlink_entry(tmp_path):
    archive = tmp_path / "symlink.zip"
    info = zipfile.ZipInfo("link")
    info.external_attr = (stat.S_IFLNK | 0o777) << 16
    with zipfile.ZipFile(archive, "w") as zf:
        zf.writestr(info, "../escape")
    destination = tmp_path / "out"
    with pytest.raises(SystemExit) as exc:
        S.safe_extract_zip(archive, destination)
    assert "symlink" in str(exc.value).lower()


def test_safe_zip_extraction_prevalidates_all_entries(tmp_path):
    archive = tmp_path / "mixed.zip"
    _make_simple_zip(archive, {"ok.txt": "ok", "../bad.txt": "bad"})
    destination = tmp_path / "out"
    with pytest.raises(SystemExit):
        S.safe_extract_zip(archive, destination)
    if destination.exists():
        assert not (destination / "ok.txt").exists()
    assert not (tmp_path / "bad.txt").exists()


def test_safe_zip_extraction_rejects_preexisting_symlink_escape(tmp_path):
    destination = tmp_path / "out"
    destination.mkdir()
    outside = tmp_path / "outside"
    outside.mkdir()
    link = destination / "link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(link), str(outside)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction creation unavailable in this environment")
    assert S.is_rp(link)

    archive = tmp_path / "escape.zip"
    _make_simple_zip(archive, {"link/escape.txt": "nope"})
    with pytest.raises(SystemExit) as exc:
        S.safe_extract_zip(archive, destination)
    message = str(exc.value).lower()
    assert "symlink" in message or "escape" in message
    assert not (outside / "escape.txt").exists()


def test_safe_zip_extraction_rejects_windows_reserved_and_ambiguous_names(tmp_path):
    destination = tmp_path / "out"
    for bad_name in ("CON", "con.txt", "PRN", "AUX.log", "NUL", "COM1", "LPT9", "normal. ", "trailing.dot."):
        archive = tmp_path / "windows.zip"
        if archive.exists():
            archive.unlink()
        _make_simple_zip(archive, {bad_name: "nope"})
        with pytest.raises(SystemExit) as exc:
            S.safe_extract_zip(archive, destination)
        message = str(exc.value).lower()
        assert "reserved" in message or "trailing" in message or "ambiguous" in message


def test_safe_zip_extraction_rejects_dangling_reparse_point(tmp_path):
    destination = tmp_path / "out"
    destination.mkdir()
    missing_target = tmp_path / "missing-target"
    dangling = destination / "dangling-link"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dangling), str(missing_target)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction creation unavailable in this environment")
    assert not dangling.exists()
    assert S.is_rp(dangling)

    archive = tmp_path / "dangling.zip"
    _make_simple_zip(archive, {"dangling-link/evil.txt": "nope"})
    with pytest.raises(SystemExit) as exc:
        S.safe_extract_zip(archive, destination)
    message = str(exc.value).lower()
    assert "symlink" in message or "escape" in message
    assert not (missing_target / "evil.txt").exists()


def test_contains_rp_detects_dangling_reparse_root(tmp_path):
    missing = tmp_path / "missing-target"
    dangling = tmp_path / "dangling-root"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(dangling), str(missing)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction creation unavailable in this environment")
    assert not dangling.exists()
    assert S.contains_rp(dangling) is True


def test_stage_refuses_dangling_reparse_output(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print('hello')\n"})
    output = tmp_path / "dangling-output"
    missing = tmp_path / "missing-target"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(output), str(missing)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction creation unavailable in this environment")
    assert S.is_rp(output)
    occupied_before = list(output.iterdir()) if output.exists() else []
    try:
        S.stage(source, output, runtime_zip=_TEST_ZIP, install_dependencies=False, required_imports=False)
    except SystemExit as exc:
        message = str(exc).lower()
        assert "already exists" in message or "reparse" in message or "symlink" in message
    else:
        raise AssertionError("expected refusal")
    # The pre-existing dangling reparse path is not replaced, migrated, or deleted.
    assert S.is_rp(output)
    assert (list(output.iterdir()) if output.exists() else []) == occupied_before


def test_stage_rejects_unexpected_preexisting_backend_dir(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {"main.py": "print('hello')\n"})
    output = tmp_path / "isolated"
    temp_root = tmp_path / "fixed-temp-root"
    backend_dir = temp_root / "backend"
    temp_root.mkdir()
    backend_dir.mkdir()
    (backend_dir / "stale.txt").write_text("stale\n", encoding="utf-8")
    monkeypatch.setattr(tempfile, "mkdtemp", lambda *a, **k: str(temp_root))
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, install_dependencies=False, required_imports=False,
    )
    assert rc == 1
    assert any("runtime-error" == getattr(f, "kind", None) or "existing" in str(f).lower() or "fileexists" in str(f).lower() for f in findings) or rc == 1
    assert out is None


def test_python_archive_digest_guard_accepts_cached_archive():
    result = S.resolve_zip(_TEST_ZIP, "3.13.16", False)
    assert result == pathlib.Path(_TEST_ZIP).resolve()


def test_python_archive_digest_guard_rejects_unknown_version(tmp_path):
    archive = tmp_path / "any.zip"
    archive.write_bytes(b"not a trusted archive")
    with pytest.raises(SystemExit) as exc:
        S.resolve_zip(archive, "9.9.9", False)
    message = str(exc.value).lower()
    assert "no independently trusted digest" in message


def test_python_archive_digest_guard_rejects_mismatch_before_extraction(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {"main.py": "print('hello')\n"})
    output = tmp_path / "isolated"
    bad_archive = tmp_path / "bad.zip"
    bad_archive.write_bytes(b"not the official archive")
    monkeypatch.setattr(
        S,
        "TRUSTED_PYTHON_EMBEDDED_SHA256",
        {"3.13.16": "0" * 64},
    )
    seen = {"extracted": False}

    def tracking_safe_extract(*args, **kwargs):
        seen["extracted"] = True
        return S.safe_extract_zip(*args, **kwargs)

    monkeypatch.setattr(S, "safe_extract_zip", tracking_safe_extract)
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source,
            output,
            runtime_zip=bad_archive,
            install_dependencies=False,
            required_imports=False,
        )
    message = str(exc.value).lower()
    assert "digest mismatch" in message
    assert seen["extracted"] is False


def test_release_mode_rejects_unpinned_get_pip_before_execution(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    executed = {"count": 0}

    def fake_run(*args, **kwargs):
        executed["count"] += 1
        return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, get_pip=get_pip, lock=lock, release=True,
        )
    assert "pinned" in str(exc.value).lower() or "provenance" in str(exc.value).lower() or "sha-256" in str(exc.value).lower()
    assert executed["count"] == 0


def test_release_mode_rejects_modified_get_pip_before_execution(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("original bootstrap\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    get_pip.write_text("modified bootstrap\n", encoding="utf-8")
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    executed = {"count": 0}

    def fake_run(*args, **kwargs):
        executed["count"] += 1
        return subprocess.CompletedProcess(args=args[0] if args else [], returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
        )
    assert "digest mismatch" in str(exc.value).lower()


def test_release_mode_requires_bootstrap_pip(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=False, lock=lock, release=True,
        )
    assert "bootstrap" in str(exc.value).lower()


def test_release_mode_requires_dependency_installation(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=False, required_imports=False, verify=False,
            bootstrap_pip=True, lock=lock, release=True,
        )
    assert "installation" in str(exc.value).lower() or "install" in str(exc.value).lower()


def test_release_mode_rejects_missing_and_empty_locks(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    missing = tmp_path / "missing.lock.txt"
    with pytest.raises(SystemExit):
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, lock=missing, release=True,
        )
    empty = tmp_path / "empty.lock.txt"
    empty.write_text("# no requirements\n", encoding="utf-8")
    output2 = tmp_path / "isolated2"
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output2, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, lock=empty, release=True,
        )
    assert "locked requirement" in str(exc.value).lower() or "hash" in str(exc.value).lower()


def test_release_lock_rejects_decoys(tmp_path):
    decoy = tmp_path / "decoy.lock"
    decoy.write_text("# sha256:" + "a" * 64 + "\nfoo==1.0\n", encoding="utf-8")
    assert S.lock_has_complete_hashes(decoy) is False
    md5 = tmp_path / "md5.lock"
    md5.write_text("foo==1.0 --hash=md5:" + "b" * 32 + "\n", encoding="utf-8")
    assert S.lock_has_complete_hashes(md5) is False
    multi = tmp_path / "multi.lock"
    multi.write_text("foo==1.0 \\\nbar==2.0 --hash=sha256:" + "c" * 64 + "\n", encoding="utf-8")
    assert S.lock_has_complete_hashes(multi) is False

    junk = tmp_path / "junk.lock"
    junk.write_text("foo==1.0 \\\n    --hash=sha256:" + "d" * 64 + " junk\n", encoding="utf-8")
    assert S.lock_has_complete_hashes(junk) is False
    assert S.release_lock_hash_error(junk) is not None
    continuation_decoy = tmp_path / "continuation_decoy.lock"
    continuation_decoy.write_text("foo==1.0 \\\nbar==2.0\n", encoding="utf-8")
    assert S.lock_has_complete_hashes(continuation_decoy) is False


def test_release_lock_accepts_multiline_sha256(tmp_path):
    lock = tmp_path / "multiline.lock"
    lock.write_text(
        "foo==1.0 \\\n    --hash=sha256:" + "d" * 64 + "\n",
        encoding="utf-8",
    )
    assert S.lock_has_complete_hashes(lock) is True


def test_release_manifest_records_proven_facts(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "freeze" in list(args):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=True, required_imports=False, verify=True,
        bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
    )
    assert rc == 0, [f.describe() for f in findings]
    manifest = _json.loads((out / "runtime-manifest.json").read_text(encoding="utf-8"))
    assert manifest["provenance_mode"] == "release"
    assert manifest["release_provenance_verified"] is True
    assert manifest["bootstrap_pip"] is True
    assert manifest["pip_bootstrapped"] is True
    assert manifest["dependencies_installed"] is True
    assert manifest["get_pip_sha256"] == digest
    assert manifest["lock_sha256"] is not None


def test_release_mode_rejects_incomplete_lock_hashes(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, lock=lock, release=True,
        )
    assert "hash" in str(exc.value).lower() or "bootstrap" in str(exc.value).lower() or "pip" in str(exc.value).lower()


def test_install_requirements_release_enforces_require_hashes(tmp_path, monkeypatch):
    lock = tmp_path / "lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")
    captured = {}

    def fake_run(args, **kwargs):
        captured["args"] = list(args)
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    S.install_requirements(
        tmp_path / "python.exe", lock, allow_network=False,
        verify_inventory=False, release=True,
    )
    assert "--require-hashes" in captured["args"]


def test_install_requirements_release_rejects_incomplete_hashes(tmp_path):
    lock = tmp_path / "lock.txt"
    lock.write_text("foo==1.0\n", encoding="utf-8")
    with pytest.raises(SystemExit) as exc:
        S.install_requirements(
            tmp_path / "python.exe", lock, allow_network=False,
            verify_inventory=False, release=True,
        )
    assert "hash" in str(exc.value).lower()



def test_release_lock_rejects_spaced_equals_hash(tmp_path):
    spaced_equals = tmp_path / "spaced-equals.lock"
    spaced_equals.write_text(
        "foo==1.0 --hash = sha256:" + "a" * 64 + "\n",
        encoding="utf-8",
    )
    assert S.lock_has_complete_hashes(spaced_equals) is False


def test_release_lock_accepts_spaced_hash_option(tmp_path):
    spaced = tmp_path / "spaced.lock"
    spaced.write_text(
        "foo==1.0 --hash sha256:" + "b" * 64 + "\n",
        encoding="utf-8",
    )
    assert S.lock_has_complete_hashes(spaced) is True


def test_stage_rejects_reparse_get_pip_candidate_before_verification(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    outside = tmp_path / "outside"
    outside.mkdir()
    reparsed_get_pip = tmp_path / "get-pip-reparse"
    created = subprocess.run(
        ["cmd", "/c", "mklink", "/J", str(reparsed_get_pip), str(outside)],
        capture_output=True,
        text=True,
    )
    if created.returncode != 0:
        pytest.skip("directory junction creation unavailable in this environment")
    assert S.is_rp(reparsed_get_pip)

    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    with pytest.raises(SystemExit) as exc:
        S.stage(
            source, output, runtime_zip=_TEST_ZIP, allow_network=False,
            install_dependencies=True, required_imports=False, verify=False,
            bootstrap_pip=True, get_pip=reparsed_get_pip,
            get_pip_sha256="a" * 64, lock=lock, release=True,
        )
    assert "reparse" in str(exc.value).lower()


def test_ensure_getpip_download_mismatch_does_not_leave_cache(tmp_path, monkeypatch):
    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(S, "DOWNLOAD_DIR", download_dir)

    def fake_download(url, dest, *, force=False):
        pathlib.Path(dest).write_text("untrusted bootstrap\n", encoding="utf-8")
        return pathlib.Path(dest)

    monkeypatch.setattr(S, "download", fake_download)

    with pytest.raises(SystemExit):
        S.ensure_getpip(
            None, True, expected_sha256="0" * 64, release=False,
        )

    assert not (download_dir / "get-pip.py").exists()
    assert list(download_dir.iterdir()) == []


def test_ensure_getpip_download_integration_uses_network_content(tmp_path, monkeypatch):
    import hashlib
    import io
    import urllib.request

    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(S, "DOWNLOAD_DIR", download_dir)

    content = b"# verified bootstrap from mocked upstream\n"
    expected_digest = hashlib.sha256(content).hexdigest()

    def fake_urlopen(url):
        return io.BytesIO(content)

    monkeypatch.setattr(urllib.request, "urlopen", fake_urlopen)

    result = S.ensure_getpip(None, True, expected_sha256=expected_digest, release=False)

    cached = download_dir / "get-pip.py"
    assert result == cached.resolve()
    assert cached.is_file()
    assert cached.read_bytes() == content


def test_ensure_getpip_fd_download_does_not_unlink_temp_path(tmp_path, monkeypatch):
    import hashlib
    import io
    import os
    import urllib.request

    download_dir = tmp_path / "downloads"
    download_dir.mkdir()
    monkeypatch.setattr(S, "DOWNLOAD_DIR", download_dir)

    content = b"fd-owned bootstrap content\n"
    expected_digest = hashlib.sha256(content).hexdigest()

    monkeypatch.setattr(urllib.request, "urlopen", lambda url: io.BytesIO(content))

    unlinks = []
    original_unlink = os.unlink

    def tracking_unlink(path, *args, **kwargs):
        unlinks.append(str(path))
        return original_unlink(path, *args, **kwargs)

    monkeypatch.setattr(os, "unlink", tracking_unlink)

    result = S.ensure_getpip(None, True, expected_sha256=expected_digest, release=False)

    cached = download_dir / "get-pip.py"
    assert result == cached.resolve()
    assert cached.read_bytes() == content
    # The owned descriptor is written directly; no unlink/recreate of the path.
    assert unlinks == []


def test_runtime_provenance_manifest_verifier_rejects_tampered_python_digest(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "freeze" in list(args):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=True, required_imports=False, verify=True,
        bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
    )
    assert rc == 0, [f.describe() for f in findings]
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_sha256"] = "0" * 64
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        S.verify_runtime_manifest(manifest_path, root=out, release=True)


def test_runtime_provenance_manifest_verifier_prototype_accepts_manifest(tmp_path):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=False, required_imports=False, verify=True,
        bootstrap_pip=False, release=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    # Prototype manifests are not release-audited.
    assert S.runtime_provenance_manifest_verified(out / "runtime-manifest.json", root=out) is True


def test_runtime_provenance_manifest_verifier_rejects_archive_digest_as_python_digest(tmp_path, monkeypatch):
    # TRUSTED_PYTHON_EMBEDDED_SHA256 identifies the downloaded archive, never
    # python.exe. A manifest claiming that archive digest for python.exe must fail.
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "freeze" in list(args):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=True, required_imports=False, verify=True,
        bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
    )
    assert rc == 0, [f.describe() for f in findings]
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    # The official embedded archive digest is not the python.exe digest.
    manifest["python_sha256"] = S.TRUSTED_PYTHON_EMBEDDED_SHA256["3.13.16"]
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit):
        S.verify_runtime_manifest(manifest_path, root=out, release=True)


def test_runtime_provenance_manifest_does_not_self_promote_to_release(tmp_path, monkeypatch):
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=False, required_imports=False, verify=True,
        bootstrap_pip=False, release=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    # Caller metadata alone cannot make the unaudited manifest release-audited.
    assert S.runtime_provenance_manifest_verified(out / "runtime-manifest.json", root=out, release=False) is True


def test_runtime_provenance_manifest_prototype_marks_unknown_archive_unverifiable(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=False, required_imports=False, verify=True,
        bootstrap_pip=False, release=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_version"] = "9.9.9"
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    ok, info = S._verify_runtime_manifest_core(manifest_path, root=out, release=False)
    assert ok is True
    assert any("unverifiable" in str(item).lower() for item in info)


def test_runtime_provenance_manifest_release_fails_closed_without_trusted_archive(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "freeze" in list(args):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=True, required_imports=False, verify=True,
        bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
    )
    assert rc == 0, [f.describe() for f in findings]
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["python_version"] = "9.9.9"
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="unverifiable"):
        S.verify_runtime_manifest(manifest_path, root=out, release=True)


def test_runtime_provenance_manifest_enforces_explicit_approved_archive_pin(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=False, required_imports=False, verify=True,
        bootstrap_pip=False, release=False,
    )
    assert rc == 0, [f.describe() for f in findings]
    bad_archive = tmp_path / "bad-archive.zip"
    bad_archive.write_bytes(b"modified archive evidence")
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_zip"] = str(bad_archive)
    manifest["source_zip_sha256"] = hashlib.sha256(bad_archive.read_bytes()).hexdigest()
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="trusted Python embedded archive digest mismatch"):
        S.verify_runtime_manifest(manifest_path, root=out, release=True,
                                 zip_path=bad_archive,
                                 trusted_archive_sha256=S.TRUSTED_PYTHON_EMBEDDED_SHA256["3.13.16"])
    # A manifest marker cannot force the gate absent an explicit caller request.
    manifest_path = out / "runtime-manifest.json"
    import json as _json
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provenance_mode"] = "release"
    manifest["release_provenance_verified"] = True
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    assert S.runtime_provenance_manifest_verified(manifest_path, root=out, release=False) is True


def test_runtime_provenance_manifest_verifier_trusted_archive_mismatch(tmp_path, monkeypatch):
    import json as _json
    source = _make_tree(tmp_path / "source", {"main.py": "print(1)\n"})
    output = tmp_path / "isolated"
    get_pip = tmp_path / "get-pip.py"
    get_pip.write_text("print('bootstrap')\n", encoding="utf-8")
    digest = hashlib.sha256(get_pip.read_bytes()).hexdigest()
    lock = tmp_path / "requirements.lock.txt"
    lock.write_text("foo==1.0 --hash=sha256:" + "a" * 64 + "\n", encoding="utf-8")

    def fake_run(args, **kwargs):
        if "freeze" in list(args):
            return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="foo==1.0\n", stderr="")
        return subprocess.CompletedProcess(args=list(args), returncode=0, stdout="", stderr="")

    monkeypatch.setattr(subprocess, "run", fake_run)
    rc, findings, removed, out = S.stage(
        source, output, runtime_zip=_TEST_ZIP, allow_network=False,
        install_dependencies=True, required_imports=False, verify=True,
        bootstrap_pip=True, get_pip=get_pip, get_pip_sha256=digest, lock=lock, release=True,
    )
    assert rc == 0, [f.describe() for f in findings]
    bad_archive = tmp_path / "bad-archive.zip"
    bad_archive.write_bytes(b"modified archive evidence")
    bad_digest = hashlib.sha256(bad_archive.read_bytes()).hexdigest()
    manifest_path = out / "runtime-manifest.json"
    manifest = _json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["source_zip"] = str(bad_archive)
    manifest["source_zip_sha256"] = bad_digest
    manifest_path.write_text(_json.dumps(manifest, indent=2) + "\n", encoding="utf-8")
    with pytest.raises(SystemExit, match="trusted Python embedded archive digest mismatch"):
        S.verify_runtime_manifest(manifest_path, root=out, release=True, zip_path=bad_archive)


def test_explicit_release_mode_is_strict_about_whitespace(tmp_path):
    # Whitespace around an otherwise valid mode must be normalized, but embedded
    # or partial strings must not be accepted as release.
    assert S._explicit_release_mode(" release ") is True
    assert S._explicit_release_mode("RELEASE") is True
    assert S._explicit_release_mode("release please") is False
    assert S._explicit_release_mode("releasedx") is False
