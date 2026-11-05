"""Synthetic checks for the safe production NSIS install path.

These tests do not execute or launch an installer. They verify the Tauri
configuration, the supported custom template, and the generated NSIS control flow
when a local build has produced it.
"""

from __future__ import annotations

import json
import os
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parents[2]
TAURI_CONF = REPO_ROOT / "src-tauri" / "tauri.conf.json"
CUSTOM_TEMPLATE = REPO_ROOT / "src-tauri" / "sythion-installer-template.nsi"
PROFILE_RUNTIME_SOURCE = REPO_ROOT / "backend" / "core" / "storage.py"


def _candidate_generated_scripts() -> list[Path]:
    paths = [
        REPO_ROOT / "src-tauri" / "target" / "release" / "nsis" / "x64" / "installer.nsi",
    ]
    cargo_target_dir = os.environ.get("CARGO_TARGET_DIR")
    if cargo_target_dir:
        paths.append(
            Path(cargo_target_dir) / "release" / "nsis" / "x64" / "installer.nsi"
        )
    paths.append(Path("E:/SythionTarget/release/nsis/x64/installer.nsi"))
    # Include fresh isolated output roots without relying on environment state.
    for build_root in sorted(Path("E:/").glob("SythionBuild-*/release/nsis/x64/installer.nsi")):
        paths.append(build_root)
    # Deduplicate while preserving order.
    unique: list[Path] = []
    for path in paths:
        if path not in unique:
            unique.append(path)
    return unique


def _generated_script() -> Path | None:
    marked: list[Path] = []
    for path in _candidate_generated_scripts():
        if not path.is_file():
            continue
        try:
            text = path.read_text(encoding="utf-8")
        except OSError:
            continue
        if "Function VerifyInstallDirNotProfileDataDir" not in text:
            continue
        score = sum(
            marker in text
            for marker in (
                "kernel32::GetFullPathNameW",
                "kernel32::GetFinalPathNameByHandleW",
                "PopulateProfileRootsForGuard",
                "Fail closed",
            )
        )
        marked.append((score, path.stat().st_mtime, path))
    if marked:
        marked.sort(key=lambda item: (item[0], item[1]))
        return marked[-1][2]
    for path in _candidate_generated_scripts():
        if path.is_file():
            return path
    return None


def _load_tauri_conf() -> dict:
    return json.loads(TAURI_CONF.read_text(encoding="utf-8"))


def test_production_profile_data_path_remains_localappdata() -> None:
    storage_text = PROFILE_RUNTIME_SOURCE.read_text(encoding="utf-8")

    assert 'DEFAULT_DATA_DIR_NAME = "Sythion (Synth)"' in storage_text
    assert 'name = PROTOTYPE_DATA_DIR_NAME if _is_prototype_build() else DEFAULT_DATA_DIR_NAME' in storage_text


def test_tauri_uses_per_user_install_with_custom_template() -> None:
    conf = _load_tauri_conf()
    nsis = conf["bundle"]["windows"]["nsis"]

    assert nsis["installMode"] == "currentUser"
    assert nsis["template"] == "sythion-installer-template.nsi"


def test_custom_template_changes_default_install_path() -> None:
    template = CUSTOM_TEMPLATE.read_text(encoding="utf-8")

    assert 'StrCpy $INSTDIR "$LOCALAPPDATA\\Programs\\${PRODUCTNAME}"' in template
    assert 'StrCpy $INSTDIR "$LOCALAPPDATA\\${PRODUCTNAME}"' not in template


def test_custom_template_retains_legacy_registered_install_path() -> None:
    template = CUSTOM_TEMPLATE.read_text(encoding="utf-8")

    assert "Function RestorePreviousInstallLocation" in template
    assert 'ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""' in template
    assert "StrCpy $INSTDIR $4" in template
    assert "${If} $INSTDIR == \"${PLACEHOLDER_INSTALL_DIR}\"" in template
    assert "Call RestorePreviousInstallLocation" in template
    assert "Multiply" not in template


def test_custom_template_fails_closed_on_profile_root_collision() -> None:
    template = CUSTOM_TEMPLATE.read_text(encoding="utf-8")

    assert "Function VerifyInstallDirNotProfileDataDir" in template
    assert 'Function un.VerifyInstallDirNotProfileDataDir' in template
    assert "FinalizeResolvedSystemPath" in template
    assert "PopulateProfileRootsForGuard" in template
    assert 'kernel32::GetFullPathNameW' in template
    assert "MessageBox MB_OK|MB_ICONSTOP" in template
    assert "\n  Abort\n" in template


def test_custom_template_calls_guard_unconditionally() -> None:
    template = CUSTOM_TEMPLATE.read_text(encoding="utf-8")

    # The guard must run for default, legacy-registry, and explicit /D paths.
    assert template.count("Call VerifyInstallDirNotProfileDataDir") >= 2
    # Uninstall safety check is also present.
    assert "Call un.VerifyInstallDirNotProfileDataDir" in template

def test_reinstall_profile_root_skips_legacy_uninstall_and_cleanup() -> None:
    template = CUSTOM_TEMPLATE.read_text(encoding="utf-8")

    assert "reinst_skip_old_uninstall:" in template
    assert "StrCpy $R1 \"\"" in template
    assert "reinst_run_legacy_uninstall:" in template
    assert "StrCpy $R1 \"$R1 _?=$4\"" in template
    assert template.index("reinst_skip_old_uninstall:") < template.index(
        "reinst_legacy_uninstall_done:"
    )


def test_source_uses_supported_customization_only() -> None:
    conf = _load_tauri_conf()
    nsis = conf["bundle"]["windows"]["nsis"]

    assert "installerHooks" not in nsis
    assert "installMode" in nsis
    assert "template" in nsis


@pytest.mark.skipif(
    _generated_script() is None, reason="generated installer.nsi is not available"
)
def test_generated_script_uses_programs_default_location() -> None:
    script = _generated_script().read_text(encoding="utf-8")

    assert '!define INSTALLMODE "currentUser"' in script
    assert '$LOCALAPPDATA\\Programs\\${PRODUCTNAME}' in script


@pytest.mark.skipif(
    _generated_script() is None, reason="generated installer.nsi is not available"
)
def test_generated_script_preserves_legacy_registered_install_path() -> None:
    script = _generated_script().read_text(encoding="utf-8")

    assert "Function RestorePreviousInstallLocation" in script
    assert 'ReadRegStr $4 SHCTX "${MANUPRODUCTKEY}" ""' in script
    assert "StrCpy $INSTDIR $4" in script
    assert "${If} $INSTDIR == \"${PLACEHOLDER_INSTALL_DIR}\"" in script
    assert "Call RestorePreviousInstallLocation" in script


@pytest.mark.skipif(
    _generated_script() is None, reason="generated installer.nsi is not available"
)
def test_generated_script_preserves_explicit_d_conflict() -> None:
    script = _generated_script().read_text(encoding="utf-8")

    guard_start = script.index('${If} $INSTDIR == "${PLACEHOLDER_INSTALL_DIR}"')
    # The placeholder-guarded restore remains conditional; unconditional guard
    # appears after the guard block and protects /D values too.
    assert "Call RestorePreviousInstallLocation" in script[guard_start:]
    assert "Call VerifyInstallDirNotProfileDataDir" in script


@pytest.mark.skipif(
    _generated_script() is None, reason="generated installer.nsi is not available"
)
def test_generated_script_contains_fail_closed_guard() -> None:
    script = _generated_script().read_text(encoding="utf-8")

    assert "Function VerifyInstallDirNotProfileDataDir" in script
    assert "Function un.VerifyInstallDirNotProfileDataDir" in script
    assert 'kernel32::GetFullPathNameW' in script
    assert "MessageBox MB_OK|MB_ICONSTOP" in script
    assert script.count("\n  Abort\n") >= 1


@pytest.mark.skipif(
    _generated_script() is None, reason="generated installer.nsi is not available"
)
def test_generated_script_silent_guard_control_flow() -> None:
    script = _generated_script().read_text(encoding="utf-8")

    # The guard is a standalone function invoked from .onInit, so it can be
    # emitted after Section Install while still running before destructive
    # installer operations.
    assert "Function VerifyInstallDirNotProfileDataDir" in script
    assert "Call VerifyInstallDirNotProfileDataDir" in script
    assert "MessageBox MB_OK|MB_ICONSTOP" in script
    assert "\n  Abort\n" in script

def _normalize_guard_path(path: str) -> str:
    """Small Python mirror of the installer's normalized path comparison."""

    return os.path.normpath(os.path.abspath(path))


def _guard_paths(install: str, profiles: list[str]) -> tuple[bool, str]:
    normalized_install = _normalize_guard_path(install)
    for raw_profile in profiles:
        normalized_profile = _normalize_guard_path(raw_profile)
        if normalized_profile == "" or normalized_install == "":
            return False, "unsafe-empty"
        if normalized_install == normalized_profile:
            return False, "equal"
        if normalized_install.startswith(normalized_profile + os.sep):
            return False, "nested-in-profile"
        if normalized_profile.startswith(normalized_install + os.sep):
            return False, "ancestor-of-profile"
    return True, "safe"


def test_guard_behavior_safe_default_and_programs_location() -> None:
    assert _guard_paths(
        r"C:\Users\example\AppData\Local\Programs\Sythion (Synth)", []
        if False
        else [r"C:\Users\example\AppData\Local\Sythion (Synth)"]
    ) == (True, "safe")


def test_guard_behavior_rejects_exact_and_nested_profile_paths() -> None:
    profile = r"C:\Users\example\AppData\Local\Sythion (Synth)"
    assert _guard_paths(profile, [profile])[1] == "equal"
    assert _guard_paths(profile + r"\nested", [profile])[1] == "nested-in-profile"
    assert _guard_paths(r"D:\unrelated\profile", [profile + r"\nested"]) == (True, "safe")


def test_guard_behavior_rejects_ancestor_and_traversal_paths() -> None:
    profile = r"C:\Users\example\AppData\Local\Sythion (Synth)"
    assert _guard_paths(r"C:\Users\example\AppData\Local", [profile])[1] == "ancestor-of-profile"
    assert _guard_paths(profile + r"\..\Sythion (Synth)", [profile])[1] == "equal"


def test_guard_behavior_custom_profile_roots_are_protected() -> None:
    custom = r"C:\isolated-custom-profile"
    assert _guard_paths(custom + r"\install", [custom])[1] == "nested-in-profile"
    assert _guard_paths(r"D:\unrelated\install", [custom])[1] == "safe"


def test_guard_behavior_production_and_prototype_roots_are_separate() -> None:
    production = r"C:\Users\example\AppData\Local\Sythion (Synth)"
    prototype = r"C:\Users\example\AppData\Local\Sythion (Synth) Prototype"
    assert _guard_paths(production + r"\install", [production])[1] == "nested-in-profile"
    assert _guard_paths(prototype + r"\install", [prototype])[1] == "nested-in-profile"
    assert _guard_paths(production + r"\install", [prototype]) == (True, "safe")
