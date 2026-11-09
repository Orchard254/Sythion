"""No subprocess or external repository required for acceptance parse checks."""
import importlib.util
from pathlib import Path
import pytest

RUNNER = Path(__file__).resolve().parents[2] / "scripts/run_goal_acceptance.py"
spec = importlib.util.spec_from_file_location("goal_acceptance_runner", RUNNER)
runner = importlib.util.module_from_spec(spec)
spec.loader.exec_module(runner)


def test_pass_requires_positive_completed_tests_and_zero_exit():
    good = runner.pytest_verdict("................ 12 passed in 0.13s", exit_code=0, strict_skips=True)
    assert good["status"] == "passed"
    assert good["counts"]["passed"] == 12


@pytest.mark.parametrize("output,exit_code", [
    ("5 skipped in 0.10s", 0),
    ("12 passed, 1 skipped in 0.13s", 0),
    ("12 passed, 1 failed in 0.13s", 1),
    ("12 passed in 0.13s", 1),
    ("no tests ran", 5),
    ("Fatal Python error: crash\n12 passed in 0.13s", 0),
    ("Windows fatal exception\n12 passed in 0.13s", 0),
])
def test_skip_errors_or_native_fault_are_never_accepted(output, exit_code):
    assert runner.pytest_verdict(output, exit_code=exit_code, strict_skips=True)["status"] == "not_verified"


def test_full_suite_can_report_known_skips_without_forgetting_counts():
    result = runner.pytest_verdict("21 passed, 2 skipped in 1.0s", exit_code=0,
                                   strict_skips=False)
    assert result["status"] == "passed"
    assert result["counts"]["skipped"] == 2


def test_native_fault_outside_summary_tail_still_blocks_acceptance():
    result = runner.pytest_verdict("12 passed in 0.13s", exit_code=0,
                                   strict_skips=True, native_fault_seen=True)
    assert result["status"] == "not_verified"


@pytest.mark.parametrize("returncode,status", [
    (0, "passed"), (1, "not_verified"), (2, "not_verified"),
])
def test_staged_backend_preflight_is_fail_closed(monkeypatch, returncode, status):
    from types import SimpleNamespace
    called = []

    def fake_run(command, **kwargs):
        called.append((command, kwargs))
        return SimpleNamespace(returncode=returncode)

    monkeypatch.setattr(runner.subprocess, "run", fake_run)
    response = runner.backend_mirror_preflight()
    assert response["status"] == status
    assert called[0][0][-1] == "--check"
    assert "refresh_staged_backend.py" in called[0][0][1]
    assert "--apply" not in called[0][0]
