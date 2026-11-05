"""
Acceptance tests for the deterministic importance classifier.

Contract: every planned tool action is classified as ROUTINE, CONSEQUENTIAL,
or FORBIDDEN based on the tool's declared policy and the action's context.
The classifier is the single policy gate that decides whether an action
needs owner approval — not the model's in-context judgment.
"""

import pytest

from backend.core.importance_classifier import (
    Importance,
    classify_action,
    needs_approval,
)


# ---------------------------------------------------------------------------
# Policy fixtures (mirror real tool declarations)
# ---------------------------------------------------------------------------

_READONLY_POLICY = {"risk_level": "none", "requires_confirmation": False}
_LOW_RISK_POLICY = {"risk_level": "low", "mutating": True, "requires_confirmation": False}
_MEDIUM_RISK_POLICY = {"risk_level": "medium", "requires_confirmation": True}
_HIGH_RISK_POLICY = {"risk_level": "high", "requires_confirmation": True, "destructive": True}
_ADMIN_POLICY = {"risk_level": "high", "requires_admin": True, "requires_confirmation": True}
_CONFIRMATION_POLICY = {"requires_confirmation": True, "risk_level": "low"}


# ---------------------------------------------------------------------------
# FORBIDDEN classification
# ---------------------------------------------------------------------------


class TestForbiddenClassification:
    def test_package_install_is_forbidden(self):
        assert classify_action("package_install", _HIGH_RISK_POLICY) == Importance.FORBIDDEN

    def test_package_uninstall_is_forbidden(self):
        assert classify_action("package_uninstall", _HIGH_RISK_POLICY) == Importance.FORBIDDEN

    def test_admin_required_is_forbidden(self):
        assert classify_action("some_tool", _ADMIN_POLICY) == Importance.FORBIDDEN

    def test_forbidden_overrides_autonomous_mode(self):
        assert classify_action(
            "package_install", _HIGH_RISK_POLICY, autonomy_level="autonomous"
        ) == Importance.FORBIDDEN


# ---------------------------------------------------------------------------
# CONSEQUENTIAL classification
# ---------------------------------------------------------------------------


class TestConsequentialClassification:
    def test_high_risk_is_consequential(self):
        assert classify_action("file_delete", _HIGH_RISK_POLICY) == Importance.CONSEQUENTIAL

    def test_medium_risk_is_consequential(self):
        assert classify_action("file_write", _MEDIUM_RISK_POLICY) == Importance.CONSEQUENTIAL

    def test_destructive_is_consequential(self):
        policy = {"destructive": True, "risk_level": "low"}
        assert classify_action("app_close", policy) == Importance.CONSEQUENTIAL

    def test_confirmation_required_is_consequential(self):
        assert classify_action("gui_type", _CONFIRMATION_POLICY) == Importance.CONSEQUENTIAL

    def test_system_path_escalates_to_consequential(self):
        params = {"path": "C:\\Windows\\System32\\config\\SAM"}
        assert classify_action(
            "file_read", _LOW_RISK_POLICY, params
        ) == Importance.CONSEQUENTIAL

    def test_loopback_url_escalates_to_consequential(self):
        params = {"url": "http://127.0.0.1:8000/api/v1/admin"}
        assert classify_action(
            "file_read", _READONLY_POLICY, params
        ) == Importance.CONSEQUENTIAL

    def test_localhost_url_escalates_to_consequential(self):
        params = {"url": "http://localhost:8000/secret"}
        assert classify_action(
            "file_read", _READONLY_POLICY, params
        ) == Importance.CONSEQUENTIAL


# ---------------------------------------------------------------------------
# ROUTINE classification
# ---------------------------------------------------------------------------


class TestRoutineClassification:
    def test_readonly_is_routine(self):
        assert classify_action("system_time", _READONLY_POLICY) == Importance.ROUTINE

    def test_low_risk_non_mutating_is_routine(self):
        policy = {"risk_level": "low", "requires_confirmation": False}
        assert classify_action("memory_recall", policy) == Importance.ROUTINE

    def test_no_policy_defaults_to_routine(self):
        assert classify_action("unknown_tool", {}) == Importance.ROUTINE


# ---------------------------------------------------------------------------
# Autonomy mode interaction
# ---------------------------------------------------------------------------


class TestAutonomyMode:
    def test_consequential_in_guarded_needs_approval(self):
        assert needs_approval(
            "file_write", _MEDIUM_RISK_POLICY, autonomy_level="guarded"
        ) is True

    def test_consequential_in_autonomous_still_needs_approval(self):
        assert needs_approval(
            "file_write", _MEDIUM_RISK_POLICY, autonomy_level="autonomous"
        ) is True

    def test_routine_never_needs_approval(self):
        assert needs_approval("system_time", _READONLY_POLICY) is False

    def test_forbidden_never_needs_approval(self):
        assert needs_approval("package_install", _HIGH_RISK_POLICY) is False

    def test_forbidden_in_autonomous_still_forbidden(self):
        assert classify_action(
            "package_install", _HIGH_RISK_POLICY, autonomy_level="autonomous"
        ) == Importance.FORBIDDEN

    def test_consequential_approved_by_user_does_not_need_approval(self):
        assert needs_approval(
            "file_write",
            _MEDIUM_RISK_POLICY,
            autonomy_level="guarded",
            approved_by_user=True,
        ) is False


# ---------------------------------------------------------------------------
# Edge cases
# ---------------------------------------------------------------------------


class TestEdgeCases:
    def test_missing_risk_level_defaults_routine(self):
        policy = {"requires_confirmation": False}
        assert classify_action("tool", policy) == Importance.ROUTINE

    def test_empty_params_does_not_escalate(self):
        assert classify_action("file_read", _LOW_RISK_POLICY, {}) == Importance.ROUTINE

    def test_none_params_does_not_escalate(self):
        assert classify_action("file_read", _LOW_RISK_POLICY, None) == Importance.ROUTINE

    def test_windows_path_in_name_only_does_not_escalate(self):
        params = {"name": "windows_backup"}
        assert classify_action("file_read", _LOW_RISK_POLICY, params) == Importance.ROUTINE

    def test_relative_path_does_not_escalate(self):
        params = {"path": "./data/file.txt"}
        assert classify_action("file_read", _LOW_RISK_POLICY, params) == Importance.ROUTINE
