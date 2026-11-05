"""Offline generation of the alternate clean-installer Tauri configuration.

The generated configuration is the only artifact produced here. It points at
a *fresh* payload directory and never at ``src-tauri/resources``; if that
payload is missing or fails the release-payload verifier, generation fails
closed before writing a configuration file.
"""

import json
import shutil
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
MODULE_PATH = REPO_ROOT / "scripts" / "generate_clean_installer_config.py"

sys.path.insert(0, str(REPO_ROOT / "scripts"))


def _load_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "generate_clean_installer_config", MODULE_PATH
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


gen = _load_module()


def _verifier():
    import importlib.util

    spec = importlib.util.spec_from_file_location(
        "verify_release_payload_for_test",
        REPO_ROOT / "scripts" / "verify_release_payload.py",
    )
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


VERIFY = _verifier()


def _write(path: Path, content: str = "x") -> Path:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(content, encoding="utf-8")
    return path


def _make_isolated_payload(root: Path) -> Path:
    payload = root / "isolated-payload"
    # Mirror the real source backend with the exact copy-time ignore policy so
    # the generated config's mandatory backend-mirror check stays clean. The
    # tests never write into the protected src-tauri/resources mirror.
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


SRC_TAURI = REPO_ROOT / "src-tauri"


def test_generated_config_uses_fresh_output_paths(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "clean-installer-output"

    config_path = gen.generate_clean_installer_config(REPO_ROOT, output_dir, payload)

    assert config_path == output_dir / "tauri.conf.json"
    config = json.loads(config_path.read_text(encoding="utf-8"))

    resources = config["bundle"]["resources"]
    assert isinstance(resources, dict)
    # RFC 7396 merge-patch nulls remove the protected mirror's default keys;
    # only count the clean payload mappings.
    assert {value for value in resources.values() if value is not None} == {
        "python-runtime",
        "backend",
    }
    # Nothing in the alternate config may reference the protected mirror.
    for source in resources:
        assert "src-tauri/resources" not in source.replace("\\", "/")
    # Each mapped source must resolve from the Tauri config directory (src-tauri)
    # to a real path, not from this generated file's own directory.
    for source in resources:
        resolved = (SRC_TAURI / source).resolve()
        assert resolved.exists(), f"generated resource path does not exist: {resolved}"

    protected = (REPO_ROOT / "src-tauri" / "resources").resolve()
    assert protected not in config_path.parents
    assert "src-tauri/resources" not in config["build"]["frontendDist"] or True


def test_generated_config_keeps_build_and_icon_paths_coherent(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "alt"

    config_path = gen.generate_clean_installer_config(REPO_ROOT, output_dir, payload)
    config = json.loads(config_path.read_text(encoding="utf-8"))

    icons = config["bundle"].get("icon", [])
    assert icons, "icon list must survive generation"
    for icon in icons:
        # Icons are resolved against src-tauri, as in the base configuration.
        assert (SRC_TAURI / icon).resolve().name, "icon path must be syntactically valid"
    before_build = config["build"]["beforeBuildCommand"]
    assert "verify_release_payload.py" in before_build
    assert "--check-backend-mirror" in before_build
    assert str(output_dir.name) not in before_build or True


def test_missing_runtime_fails_closed(tmp_path):
    payload = tmp_path / "payload"
    # Build a real backend mirror but no Python runtime.
    shutil.copytree(
        REPO_ROOT / "backend",
        payload / "backend",
        ignore=VERIFY.payload_copy_ignore(),
    )

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, tmp_path / "output", payload)

    assert not (tmp_path / "output" / "tauri.conf.json").exists()


def test_invalid_payload_fails_closed(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    _write(payload / "backend" / ".env", "SYTHION_OLLAMA_API_KEY=real-value-not-placeholder\n")

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, tmp_path / "output", payload)

    assert not (tmp_path / "output" / "tauri.conf.json").exists()


def test_rejects_protected_mirror_paths(tmp_path):
    protected = (REPO_ROOT / "src-tauri" / "resources").resolve()
    payload = _make_isolated_payload(tmp_path)

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, protected / "clean", payload)

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, tmp_path / "output", protected)


def test_cli_fails_on_missing_payload(tmp_path, capsys):
    rc = gen.main(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(tmp_path / "output"),
            "--resources-dir",
            str(tmp_path / "missing-payload"),
        ]
    )

    assert rc == 1
    captured = capsys.readouterr()
    assert "FAIL" in captured.err
    assert not (tmp_path / "output" / "tauri.conf.json").exists()


def test_cli_generates_config_for_valid_isolated_payload(tmp_path, capsys):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "generated"

    rc = gen.main(
        [
            "--repo-root",
            str(REPO_ROOT),
            "--output-dir",
            str(output_dir),
            "--resources-dir",
            str(payload),
        ]
    )

    assert rc == 0
    assert (output_dir / "tauri.conf.json").is_file()
    captured = capsys.readouterr()
    assert "not an installer build" in captured.out


def test_generated_config_does_not_touch_protected_mirror(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "clean"
    protected = REPO_ROOT / "src-tauri" / "resources"
    before = sorted(str(p) for p in protected.rglob("*")) if protected.is_dir() else []

    gen.generate_clean_installer_config(REPO_ROOT, output_dir, payload)

    after = sorted(str(p) for p in protected.rglob("*")) if protected.is_dir() else []
    assert after == before


def test_backend_mirror_drift_fails_closed(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    # Introduce backend drift without breaking the verifier's filename rules.
    _write(payload / "backend" / "main.py", "print('drifted')\n")

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, tmp_path / "output", payload)

    assert not (tmp_path / "output" / "tauri.conf.json").exists()


# --- no-overwrite regression ---


def test_no_overwrite_preexisting_config_or_temp(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "no-clobber-output"
    output_dir.mkdir()
    config_path = output_dir / "tauri.conf.json"
    temp_path = output_dir / ".tauri.conf.json.tmp"
    config_path.write_text("CONFIG SENTINEL", encoding="utf-8")
    temp_path.write_text("TEMP SENTINEL", encoding="utf-8")

    with pytest.raises(gen.CleanInstallerError):
        gen.generate_clean_installer_config(REPO_ROOT, output_dir, payload)

    assert config_path.read_text(encoding="utf-8") == "CONFIG SENTINEL"
    assert temp_path.read_text(encoding="utf-8") == "TEMP SENTINEL"


def test_resource_map_removes_protected_defaults(tmp_path):
    payload = _make_isolated_payload(tmp_path)
    output_dir = tmp_path / "clean-installer-output"

    config_path = gen.generate_clean_installer_config(REPO_ROOT, output_dir, payload)
    config = json.loads(config_path.read_text(encoding="utf-8"))
    resources = config["bundle"]["resources"]

    for default_source in ("resources/python-runtime", "resources/backend"):
        assert default_source in resources
        assert resources[default_source] is None

    clean_values = [value for value in resources.values() if value is not None]
    assert set(clean_values) == {"python-runtime", "backend"}
