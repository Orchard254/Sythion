"""Behavior tests for sythion/src-tauri/sythion-installer-template.nsi guard functions.

These tests execute the actual NSIS instruction flow of the extracted guard
functions through nsis_guard_interpreter.NSISVM. They do not patch or invoke the
template, do not touch the registry, profile directories, shortcuts, or network,
and use only synthetic E: paths. Windows path normalization uses the real
kernel32 GetFullPathNameW implementation through the interpreter.
"""

from __future__ import annotations

import os
import unittest

import nsis_guard_interpreter as nsi

BASE = "E:\\cxtest-temp\\nsis-guard"
LOCALAPPDATA = BASE + "\\localappdata"
DEFAULT_ROOT = LOCALAPPDATA + "\\Sythion"
CANONICAL_ROOT = BASE + "\\canonical"
LEGACY_ROOT = BASE + "\\legacy"
SAFE_INSTALL = BASE + "\\safe-install"


def make_vm(install_dir: str) -> nsi.NSISVM:
    vm = nsi.NSISVM()
    vm.vars.update(
        {
            "$INSTDIR": install_dir,
            "$LOCALAPPDATA": LOCALAPPDATA,
            "$PRODUCTNAME": "Sythion",
            "${PRODUCTNAME}": "Sythion",
        }
    )
    return vm


class TestNSISGuardBehavior(unittest.TestCase):
    def setUp(self) -> None:
        self._saved_env = {
            "SYTHION_DATA_DIR": os.environ.get("SYTHION_DATA_DIR"),
            "Sythion (Synth)_DATA_DIR": os.environ.get("Sythion (Synth)_DATA_DIR"),
        }
        os.environ.pop("SYTHION_DATA_DIR", None)
        os.environ.pop("Sythion (Synth)_DATA_DIR", None)

    def tearDown(self) -> None:
        for key, value in self._saved_env.items():
            if value is None:
                os.environ.pop(key, None)
            else:
                os.environ[key] = value

    def _set_canonical(self, path: str | None) -> None:
        if path is None:
            os.environ.pop("SYTHION_DATA_DIR", None)
        else:
            os.environ["SYTHION_DATA_DIR"] = path

    def _set_legacy(self, path: str | None) -> None:
        if path is None:
            os.environ.pop("Sythion (Synth)_DATA_DIR", None)
        else:
            os.environ["Sythion (Synth)_DATA_DIR"] = path

    # ------------------------------------------------------------------ #
    # Path normalization (real Win32 GetFullPathNameW via interpreter)
    # ------------------------------------------------------------------ #
    def test_normalize_system_path_returns_lexical_absolute_path(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$R8"] = "E:\\cxtest-temp\\a\\..\\b\\."
        vm.execute_function("NormalizeSystemPath")
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R9", ""), "E:\\cxtest-temp\\b")

    def test_normalize_system_path_fails_closed_when_required_length_exceeds_buffer(
        self,
    ) -> None:
        vm = make_vm(SAFE_INSTALL)
        too_long = "E:\\" + ("a" * 9000)
        vm.vars["$R8"] = too_long
        vm.execute_function("NormalizeSystemPath")
        self.assertEqual(vm.vars.get("$R9", ""), "")

    def test_finalize_resolved_system_path_keeps_lexical_path_when_handle_cannot_open(
        self,
    ) -> None:
        vm = make_vm(SAFE_INSTALL)
        missing = "E:\\cxtest-temp\\does-not-exist-nsis-guard"
        vm.vars["$R8"] = missing
        vm.execute_function("FinalizeResolvedSystemPath")
        self.assertEqual(vm.vars.get("$R9", ""), missing)

    # ------------------------------------------------------------------ #
    # Relationship checks
    # ------------------------------------------------------------------ #
    def test_install_relationship_aborts_for_equal_path(self) -> None:
        inst = DEFAULT_ROOT
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertTrue(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R9", ""), "")

    def test_install_relationship_aborts_when_install_is_under_profile_root(self) -> None:
        inst = DEFAULT_ROOT + "\\nested\\app"
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertTrue(vm.last_aborted)

    def test_install_relationship_aborts_when_install_is_ancestor_of_profile_root(
        self,
    ) -> None:
        inst = "E:\\cxtest-temp\\nsis-guard"
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertTrue(vm.last_aborted)

    def test_install_relationship_returns_for_lexically_unrelated_paths(self) -> None:
        inst = SAFE_INSTALL
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertFalse(vm.last_aborted)

    def test_install_relationship_aborts_for_string_prefix_without_separator(self) -> None:
        inst = DEFAULT_ROOT + "X"
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertTrue(vm.last_aborted)

    def test_install_relationship_preserves_r3_register(self) -> None:
        inst = SAFE_INSTALL
        root = DEFAULT_ROOT
        stored_second_root = CANONICAL_ROOT
        vm = make_vm(inst)
        vm.vars.update(
            {"$R7": inst, "$R5": root, "$R3": stored_second_root, "$R9": ""}
        )
        vm.execute_function("IsInstallPathUnderOrContainingProfileRoot")
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R3", ""), stored_second_root)

    def test_uninstall_relationship_aborts_for_equal_path(self) -> None:
        inst = DEFAULT_ROOT
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("un.IsInstallPathUnderOrContainingProfileRoot")
        self.assertTrue(vm.last_aborted)

    def test_uninstall_relationship_returns_for_unrelated_path(self) -> None:
        inst = SAFE_INSTALL
        root = DEFAULT_ROOT
        vm = make_vm(inst)
        vm.vars.update({"$R7": inst, "$R5": root, "$R9": ""})
        vm.execute_function("un.IsInstallPathUnderOrContainingProfileRoot")
        self.assertFalse(vm.last_aborted)

    # ------------------------------------------------------------------ #
    # Multi-root Verify behavior
    # ------------------------------------------------------------------ #
    def _run_verify(self, install_dir: str) -> nsi.NSISVM:
        vm = make_vm(install_dir)
        vm.execute_function("VerifyInstallDirNotProfileDataDir")
        return vm

    def test_verify_no_override_allows_safe_location(self) -> None:
        vm = self._run_verify(SAFE_INSTALL)
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R7", ""), SAFE_INSTALL)

    def test_verify_no_override_aborts_default_root(self) -> None:
        vm = self._run_verify(DEFAULT_ROOT)
        self.assertTrue(vm.last_aborted)

    def test_verify_canonical_only_allows_safe_location(self) -> None:
        self._set_canonical(CANONICAL_ROOT)
        vm = self._run_verify(SAFE_INSTALL)
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R7", ""), SAFE_INSTALL)

    def test_verify_canonical_only_aborts_each_root(self) -> None:
        self._set_canonical(CANONICAL_ROOT)
        for bad in (CANONICAL_ROOT, DEFAULT_ROOT):
            vm = self._run_verify(bad)
            self.assertTrue(vm.last_aborted, msg=bad)

    def test_verify_legacy_only_allows_safe_location(self) -> None:
        self._set_legacy(LEGACY_ROOT)
        vm = self._run_verify(SAFE_INSTALL)
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R7", ""), SAFE_INSTALL)

    def test_verify_legacy_only_aborts_each_root(self) -> None:
        self._set_legacy(LEGACY_ROOT)
        for bad in (LEGACY_ROOT, DEFAULT_ROOT):
            vm = self._run_verify(bad)
            self.assertTrue(vm.last_aborted, msg=bad)

    def test_verify_both_distinct_roots_overflow_fails_closed(self) -> None:
        # Canonical + legacy + default local root = three distinct roots.
        self._set_canonical(CANONICAL_ROOT)
        self._set_legacy(LEGACY_ROOT)
        vm = self._run_verify(SAFE_INSTALL)
        self.assertTrue(vm.last_aborted)

    def test_verify_invalid_canonical_override_fails_closed(self) -> None:
        # A malformed canonical root must remain sticky and prevent the valid
        # default root from masking it.
        self._set_canonical("E:\\" + ("a" * 9000))
        vm = self._run_verify(SAFE_INSTALL)
        self.assertTrue(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R2", ""), "")
        self.assertEqual(vm.vars.get("$R3", ""), "")

    def test_verify_invalid_legacy_override_fails_closed(self) -> None:
        self._set_legacy("E:\\" + ("a" * 9000))
        vm = self._run_verify(SAFE_INSTALL)
        self.assertTrue(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R2", ""), "")
        self.assertEqual(vm.vars.get("$R3", ""), "")

    def test_verify_overflow_root_registers_stay_cleared(self) -> None:
        self._set_canonical(CANONICAL_ROOT)
        self._set_legacy(LEGACY_ROOT)
        vm = self._run_verify(SAFE_INSTALL)
        self.assertTrue(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R6", ""), "rejected")

    def test_verify_canonical_and_default_counts_as_two_valid_roots(self) -> None:
        self._set_canonical(CANONICAL_ROOT)
        # Install into a third, unrelated location remains allowed; overflow only
        # occurs if a third DISTINCT root is added.
        vm = self._run_verify(SAFE_INSTALL)
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R7", ""), SAFE_INSTALL)

    # ------------------------------------------------------------------ #
    # Uninstall verification
    # ------------------------------------------------------------------ #
    def test_uninstall_verify_aborts_default_root(self) -> None:
        vm = make_vm(DEFAULT_ROOT)
        vm.execute_function("un.VerifyInstallDirNotProfileDataDir")
        self.assertTrue(vm.last_aborted)

    def test_uninstall_verify_allows_unrelated_location(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.execute_function("un.VerifyInstallDirNotProfileDataDir")
        self.assertFalse(vm.last_aborted)

    # ------------------------------------------------------------------ #
    # Registered-location upgrade behavior
    # ------------------------------------------------------------------ #
    def test_restore_previous_install_location_reuses_safe_registered_root(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$4"] = BASE + "\\safe-registered-root"
        vm.execute_function("RestorePreviousInstallLocation")
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$INSTDIR", ""), BASE + "\\safe-registered-root")

    def test_restore_previous_install_location_skips_profile_root_registered(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$4"] = DEFAULT_ROOT
        vm.execute_function("RestorePreviousInstallLocation")
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$INSTDIR", ""), SAFE_INSTALL)

    def test_restore_previous_install_location_skips_empty_registry_value(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$4"] = ""
        vm.execute_function("RestorePreviousInstallLocation")
        self.assertEqual(vm.vars.get("$INSTDIR", ""), SAFE_INSTALL)

    def test_restore_previous_install_location_skips_ambiguous_registered_roots(self) -> None:
        self._set_canonical(CANONICAL_ROOT)
        self._set_legacy(LEGACY_ROOT)
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$4"] = SAFE_INSTALL
        vm.execute_function("RestorePreviousInstallLocation")
        self.assertTrue(vm.last_aborted is False)
        self.assertEqual(vm.vars.get("$INSTDIR", ""), SAFE_INSTALL)

    # ------------------------------------------------------------------ #
    # Silent and explicit /D profile-root handling
    # ------------------------------------------------------------------ #
    def test_explicit_d_profile_root_is_rejected(self) -> None:
        vm = make_vm(DEFAULT_ROOT)
        vm.execute_function("VerifyInstallDirNotProfileDataDir")
        self.assertTrue(vm.last_aborted)

    def test_explicit_d_safe_location_is_allowed(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.execute_function("VerifyInstallDirNotProfileDataDir")
        self.assertFalse(vm.last_aborted)

    def test_registered_helper_reports_profile_root_variants(self) -> None:
        vm = make_vm(SAFE_INSTALL)
        for unsafe in (DEFAULT_ROOT, DEFAULT_ROOT + "\\nested", LOCALAPPDATA):
            vm.vars.update({"$R7": unsafe, "$R5": DEFAULT_ROOT})
            vm.execute_function("RegisteredInstallPathIsProfileRootUnsafe")
            self.assertEqual(
                vm.vars.get("$R0", ""), "1", msg=unsafe
            )
        vm.vars.update({"$R7": SAFE_INSTALL, "$R5": DEFAULT_ROOT})
        vm.execute_function("RegisteredInstallPathIsProfileRootUnsafe")
        self.assertEqual(vm.vars.get("$R0", ""), "0")

    def test_registered_helper_preserves_r1_uninstall_command(self) -> None:
        command = r'"C:\old\uninstall.exe" /S'
        vm = make_vm(SAFE_INSTALL)
        vm.vars.update(
            {
                "$R7": SAFE_INSTALL,
                "$R5": DEFAULT_ROOT,
                "$R1": command,
            }
        )
        vm.execute_function("RegisteredInstallPathIsProfileRootUnsafe")
        self.assertEqual(vm.vars.get("$R1", ""), command)


    # ------------------------------------------------------------------ #
    # Final resolved-path prefix handling (regression)
    # ------------------------------------------------------------------ #
    def test_finalize_resolved_system_path_returns_ordinary_path_for_existing_folder(
        self,
    ) -> None:
        vm = make_vm(SAFE_INSTALL)
        vm.vars["$R8"] = SAFE_INSTALL
        vm.execute_function("FinalizeResolvedSystemPath")
        self.assertFalse(vm.last_aborted)
        self.assertEqual(vm.vars.get("$R9", ""), SAFE_INSTALL)
        self.assertFalse(vm.vars.get("$R9", "").startswith("\\\\"))

    def test_silent_mode_suppresses_guard_error_message_but_aborts(self) -> None:
        vm = make_vm(DEFAULT_ROOT)
        vm.silent_mode = True
        vm.execute_function("VerifyInstallDirNotProfileDataDir")
        self.assertTrue(vm.last_aborted)
        self.assertFalse(any("MessageBox" in message for message in vm.messages))

    def test_silent_attribute_modes_suppress_guard_error_message(self) -> None:
        for attribute in ("silent", "silent_mode", "is_silent"):
            vm = make_vm(DEFAULT_ROOT)
            setattr(vm, attribute, True)
            vm.execute_function("VerifyInstallDirNotProfileDataDir")
            self.assertTrue(vm.last_aborted, msg=attribute)
            self.assertFalse(
                any("MessageBox" in message for message in vm.messages), msg=attribute
            )
        vm = make_vm(DEFAULT_ROOT)
        vm.vars["$Silent"] = "1"
        vm.execute_function("VerifyInstallDirNotProfileDataDir")
        self.assertTrue(vm.last_aborted)
        self.assertFalse(any("MessageBox" in message for message in vm.messages))


if __name__ == "__main__":
    unittest.main()