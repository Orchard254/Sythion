"""Prevent the Windows desktop launcher from starting a duplicate backend.

Static contract tests also run on Linux: no Windows processes, account
credentials, model requests, sockets, or profile writes are needed.
"""
from pathlib import Path
import re

ROOT = Path(__file__).resolve().parents[2]
LAUNCHER = ROOT / "start_sythion.cmd"


def test_launcher_has_only_tauri_runtime_as_backend_owner():
    script = LAUNCHER.read_text(encoding="utf-8")
    normalized = script.lower()
    assert len(re.findall(r"(?im)^\s*call npx --prefix frontend tauri dev\s*$", script)) == 1
    actionable_lines = "\n".join(
        line for line in normalized.splitlines()
        if line.strip() and not line.lstrip().startswith("rem ")
    )
    assert "uvicorn" not in actionable_lines
    assert "python -m" not in actionable_lines
    assert "start \"sythion backend\"" not in actionable_lines
    assert "set pythonpath" not in actionable_lines
    assert "--port 8000" not in actionable_lines
    assert "setlocal" in normalized
    assert "exit /b %sythion_launch_exit_code%" in normalized


def test_tauri_uses_dynamic_port_and_per_launch_handshake():
    source = (ROOT / "src-tauri" / "src" / "sidecar.rs").read_text(encoding="utf-8")
    assert "spawn_sidecar_for_build(" in source
    assert "pick_free_port()" in source
    assert '.arg("uvicorn")' in source
    assert '.env("SYTHION_HANDSHAKE_TOKEN_FILE", &token_file)' in source
    assert '.env("SYTHION_PORT", port.to_string())' in source


def test_rust_custom_prototype_cfg_is_registered():
    build_script = (ROOT / "src-tauri" / "build.rs").read_text(encoding="utf-8")
    assert 'println!("cargo:rustc-check-cfg=cfg(prototype_build)");' in build_script
    assert 'println!("cargo:rustc-cfg=prototype_build");' in build_script


def test_handshake_watcher_uses_selected_profile_instead_of_default():
    watcher = (ROOT / "src-tauri" / "src" / "sidecar.rs").read_text(encoding="utf-8")
    shell = (ROOT / "src-tauri" / "src" / "main.rs").read_text(encoding="utf-8")
    assert "fn read_handshake(dir: &Path)" in watcher
    assert "let path = dir.join(HANDSHAKE_FILE_NAME);" in watcher
    assert "read_handshake(&dir)" in watcher
    assert "let handshake_path = dir.join(HANDSHAKE_FILE_NAME);" in watcher
    assert "sidecar::spawn_watcher(app_handle, profile_dir.clone());" in shell
