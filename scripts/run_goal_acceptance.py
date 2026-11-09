#!/usr/bin/env python3
"""Run evidence-producing goal integration checks in a clean local checkout.

This runner never triggers GitHub Actions, changes settings, enables autonomy,
or claims the installed Windows application has been accepted. A skipped
process-kill suite is not a pass. Results contain only bounded metadata and
an output digest; raw test logs remain on the executing terminal.
"""

from __future__ import annotations

import argparse
from collections import deque
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import platform
import re
import shutil
import subprocess
import sys
import time

ROOT = Path(__file__).resolve().parent.parent
FOCUSED = (
    "backend/tests/test_goal_execution_vertical_slice.py",
    "backend/tests/test_goal_readonly_tool_adapter.py",
    "backend/tests/test_work_orchestration.py",
    "backend/tests/test_goal_planning.py",
    "backend/tests/test_goal_restart_policy.py",
    "backend/tests/test_goal_retry_safety.py",
    "backend/tests/test_goal_execution_binding.py",
    "backend/tests/test_goal_completion_policy.py",
)
_FATAL = ("Fatal Python error:", "Windows fatal exception", "Current thread 0x")
_COUNT = re.compile(r"\b(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed)\b")


def pytest_verdict(tail: str, *, exit_code: int, strict_skips: bool,
                   native_fault_seen: bool = False) -> dict:
    """Do not declare success from exit code alone or a skipped suite."""
    counters = {}
    for count, name in _COUNT.findall(tail):
        name = "errors" if name in {"error", "errors"} else name
        counters[name] = int(count)
    complete = (
        exit_code == 0 and counters.get("passed", 0) > 0
        and not counters.get("failed", 0) and not counters.get("errors", 0)
        and (not strict_skips or not counters.get("skipped", 0))
        and not native_fault_seen
        and not any(marker in tail for marker in _FATAL)
    )
    return {"status": "passed" if complete else "not_verified", "counts": counters}


def _source_revision() -> dict:
    """A reproducible acceptance observation requires a clean Git commit."""
    def git(*args: str) -> subprocess.CompletedProcess:
        return subprocess.run(["git", *args], cwd=ROOT, capture_output=True,
                              text=True, check=False)
    head = git("rev-parse", "HEAD")
    state = git("status", "--porcelain", "--untracked-files=normal")
    if head.returncode or state.returncode:
        return {"commit": None, "clean": False, "reason": "git_unavailable"}
    return {"commit": head.stdout.strip(), "clean": not bool(state.stdout.strip()),
            "reason": "clean" if not state.stdout.strip() else "uncommitted_changes"}


def run_suite(label: str, argv: list[str], *, strict_skips: bool,
              env: dict[str, str] | None = None) -> dict:
    started = time.monotonic()
    digest = hashlib.sha256()
    tail = deque(maxlen=80)
    native_fault_seen = False
    try:
        with subprocess.Popen(argv, cwd=ROOT, env=env, stdout=subprocess.PIPE,
                              stderr=subprocess.STDOUT, text=True,
                              errors="replace", bufsize=1) as process:
            assert process.stdout is not None
            for line in process.stdout:
                print(line, end="", flush=True)
                digest.update(line.encode("utf-8", errors="replace"))
                tail.append(line)
                native_fault_seen = native_fault_seen or any(
                    marker in line for marker in _FATAL
                )
            exit_code = process.wait()
    except OSError as exc:
        return {"suite": label, "status": "not_verified",
                "reason": type(exc).__name__, "exit_code": None,
                "elapsed_seconds": round(time.monotonic() - started, 2)}
    result = pytest_verdict("".join(tail), exit_code=exit_code,
                            strict_skips=strict_skips,
                            native_fault_seen=native_fault_seen)
    return {"suite": label, **result, "exit_code": exit_code,
            "elapsed_seconds": round(time.monotonic() - started, 2),
            "output_sha256": digest.hexdigest()}


def run_command(label: str, argv: list[str]) -> dict:
    started = time.monotonic()
    try:
        status = subprocess.run(argv, cwd=ROOT, check=False).returncode
    except OSError as exc:
        return {"suite": label, "status": "not_verified",
                "reason": type(exc).__name__, "exit_code": None}
    return {"suite": label, "status": "passed" if status == 0 else "not_verified",
            "exit_code": status, "elapsed_seconds": round(time.monotonic()-started, 2)}


def backend_mirror_preflight() -> dict:
    """Read-only release parity check; do not spend a long test run on drift."""
    try:
        result = subprocess.run(
            [sys.executable, str(ROOT / "scripts/refresh_staged_backend.py"), "--check"],
            cwd=ROOT, capture_output=True, text=True, timeout=120, check=False,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"suite": "staged_backend_preflight", "status": "not_verified",
                "reason": type(exc).__name__}
    if result.returncode == 0:
        return {"suite": "staged_backend_preflight", "status": "passed"}
    return {"suite": "staged_backend_preflight", "status": "not_verified",
            "reason": "backend_mirror_missing_stale_or_unsafe"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--process-kill", action="store_true",
                        help="Run actual Windows hard-kill restart tests")
    parser.add_argument("--full-backend", action="store_true")
    parser.add_argument("--frontend", action="store_true")
    parser.add_argument("--report", type=Path, help="Write metadata-only JSON report")
    args = parser.parse_args(argv)
    revision = _source_revision()
    result = {"schema_version": 1,
              "recorded_at": datetime.now(timezone.utc).isoformat(),
              "platform": platform.platform(), "python": sys.version.split()[0],
              "source": revision, "suites": [],
              "installed_windows_acceptance_performed": False,
              "github_actions_triggered": False}
    if not revision["clean"]:
        result["status"] = "inconclusive_dirty_or_unavailable_checkout"
    elif not (ROOT / "backend/tests/test_goal_execution_vertical_slice.py").is_file():
        result["status"] = "inconclusive_incomplete_checkout"
    else:
        result["suites"].append(run_suite(
            "goal_orm_and_policy",
            [sys.executable, "-m", "pytest", "-q", *FOCUSED],
            strict_skips=True))
        if args.process_kill:
            if os.name != "nt":
                result["suites"].append({"suite": "windows_process_kill",
                                         "status": "not_verified", "reason": "windows_required"})
            else:
                env = os.environ.copy()
                env["SYTHION_RUN_PROCESS_KILL"] = "1"
                result["suites"].append(run_suite(
                    "windows_process_kill",
                    [sys.executable, "-m", "pytest", "-q", "-m", "process_kill",
                     "backend/tests/test_process_crash_recovery.py"],
                    strict_skips=True, env=env))
        if args.full_backend:
            mirror = backend_mirror_preflight()
            result["suites"].append(mirror)
            if mirror["status"] == "passed":
                result["suites"].append(run_suite(
                    "full_backend", [sys.executable, "-m", "pytest", "-q", "backend/tests"],
                    strict_skips=False))
            else:
                print("[acceptance] The generated Tauri backend mirror needs review.")
                print("[acceptance] To refresh explicitly, run:")
                print("  python scripts/refresh_staged_backend.py --apply")
                result["suites"].append({
                    "suite": "full_backend", "status": "not_verified",
                    "reason": "staged_backend_preflight_failed",
                })
        if args.frontend:
            npm = shutil.which("npm")
            result["suites"].append(
                run_command("frontend_build", [npm, "run", "build", "--prefix", "frontend"])
                if npm else {"suite": "frontend_build", "status": "not_verified",
                             "reason": "npm_not_installed"})
            if npm:
                result["suites"].append(run_command(
                    "frontend_vitest", [npm, "run", "test", "--prefix", "frontend"]))
        result["status"] = "checks_passed_windows_acceptance_still_required" if all(
            suite["status"] == "passed" for suite in result["suites"]
        ) else "not_verified"
    if args.report:
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(json.dumps(result, indent=2) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2))
    return 0 if result["status"] == "checks_passed_windows_acceptance_still_required" else 2


if __name__ == "__main__":
    raise SystemExit(main())
