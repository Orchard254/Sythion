"""The ``backend/.env`` isolation must be MEASURED, not assumed (item 020).

``backend/tests/conftest.py`` holds an autouse fixture that stops the suite
inheriting a developer's gitignored ``backend/.env`` and the process
environment's ``SYTHION_*`` variables.  Until now nothing proved that the
fixture is still THERE: with the fixture deleted and a clean checkout (no
``backend/.env`` - which is what a CI runner has) the readiness file is
entirely green, so a deleted isolation was invisible.

This module closes that hole.  It builds a scratch copy of the repository's
own ``backend/`` package (including the repository's own conftest, i.e. the
isolation under test), gives the copy a GENERATED ``.env`` holding a fake
sentinel, and runs three nested pytest subprocesses against it:

  run 1  no ``.env``, isolation present  -> the effective settings are clean
  run 2  sentinel ``.env``, isolation present -> the effective settings are
         STILL clean, and the readiness file's outcome set is identical to
         run 1's, item by item
  run 3  sentinel ``.env``, isolation REMOVED in the scratch copy -> the
         nested run FAILS (the generated probe fails and so does the
         repository's own ``test_the_suite_does_not_inherit_the_local_env_file``)

Run 3 is the anti-vacuity mechanism: without it, "the sentinel is not in
effect" could be satisfied by a scratch tree that simply never read the
``.env`` at all.

Safety rules this module keeps (they are the point of the exercise, not
incidental):

  * the developer's real ``backend/.env`` is never copied, never read and
    never printed - the copy EXCLUDES it and the scratch tree gets a
    GENERATED ``.env`` with an invented sentinel;
  * no sentinel value is ever RENDERED: every comparison is boolean and every
    failure message names the property that broke, and this module asserts
    that neither sentinel occurs in any captured output or JUnit XML;
  * no network I/O: the child environment redirects the profile directory and
    ``%LOCALAPPDATA%`` into the scratch tree, and the probe blocks outbound
    connections and records every attempt;
  * the child environment is built EXPLICITLY (every ``SYTHION_*`` variable
    removed, ``PYTHONPATH`` scrubbed) so the developer's shell contributes
    nothing and the scratch tree cannot be shadowed;
  * the scratch tree is removed in a ``finally``, and a removal that still
    fails FAILS the test - debris must not accumulate silently.

The probe that runs in the child process is committed as a template
(``backend/tests/isolation_probe_template.py``) so a reviewer can read the
code that actually runs; it is copied into the scratch tree at run time.
"""

import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
import xml.etree.ElementTree as ET
from pathlib import Path

import pytest


_TESTS_DIR = Path(__file__).resolve().parent
_REPO_ROOT = _TESTS_DIR.parent.parent
_PROBE_TEMPLATE = _TESTS_DIR / "isolation_probe_template.py"

# The two invented sentinels.  Neither is a credential, neither is ever
# rendered, and neither ever reaches an output stream (asserted below).
FILE_SENTINEL = "SENTINEL-FILE-020-DO-NOT-PRINT"
ENV_SENTINEL = "SENTINEL-ENV-020-DO-NOT-PRINT"

# The EXACT signal line the probe has to produce, in both directions.
SIGNAL_ISOLATED = (
    "SENTINEL-PROBE file_neutralised=True env_neutralised=True env_cleared=True "
    "profile_isolated=True network_attempts=0"
)
SIGNAL_UNISOLATED = (
    "SENTINEL-PROBE file_neutralised=False env_neutralised=False env_cleared=False "
    "profile_isolated=True network_attempts=0"
)

# A nested run is bounded: on a timeout the child is killed and the test
# FAILS.  It never hangs.
RUN_TIMEOUT_SECONDS = 120

# The anchor of the negative control.  It carries the fixture's NAME, so
# exactly one of the conftest's autouse fixtures is affected
# (``_guarded_autonomy_default`` is untouched).
ISOLATION_ANCHOR = re.compile(
    r"@pytest\.fixture\(autouse=True\)\r?\ndef _isolate_local_env_settings\("
)

PROBE_MODULE = "test_env_isolation_probe"
PROBE_TEST = "test_the_probe_observes_that_the_local_env_isolation_holds"
READINESS_MODULE = "test_setup_readiness"
READINESS_ISOLATION_TEST = "test_the_suite_does_not_inherit_the_local_env_file"


class _NestedRun(object):
    """One nested pytest run and everything observed about it."""

    def __init__(self, index, completed, report_path, signal_path, network_path, elapsed):
        self.index = index
        self.completed = completed
        self.report_path = report_path
        self.signal_path = signal_path
        self.network_path = network_path
        self.elapsed = elapsed

    @property
    def returncode(self):
        return self.completed.returncode

    @property
    def output(self):
        return (self.completed.stdout or "") + (self.completed.stderr or "")

    @property
    def report_text(self):
        try:
            with open(str(self.report_path), "r", encoding="utf-8", errors="replace") as handle:
                return handle.read()
        except OSError:
            return ""


def _build_scratch_tree(scratch):
    """Copy the repository's ``backend`` package into a scratch tree.

    The copy is a byte-for-byte copytree of the repository's own package, so
    the isolation under test is the repository's conftest and not a
    re-implementation of it.
    """
    backend = scratch / "backend"
    shutil.copytree(
        str(_REPO_ROOT / "backend"),
        str(backend),
        ignore=shutil.ignore_patterns(
            "__pycache__",
            "*.pyc",
            ".pytest_cache",
            ".env",
            ".env.*",
            "*.db",
            "*.sqlite*",
        ),
    )
    # The root configuration travels with the tree: it carries the marker
    # vocabulary and ``asyncio_mode``.
    shutil.copy2(str(_REPO_ROOT / "pytest.ini"), str(scratch / "pytest.ini"))
    # The probe travels as the committed template and is collected ONLY here.
    shutil.copy2(
        str(_PROBE_TEMPLATE), str(backend / "tests" / "test_env_isolation_probe.py")
    )
    (scratch / "profile").mkdir(exist_ok=True)
    (scratch / "localappdata").mkdir(exist_ok=True)
    # The legacy profile migration copies ``~/.sythion`` into the profile
    # directory when that directory is EMPTY.  A placeholder keeps the
    # scratch tree free of anything but fakes.
    (scratch / "profile" / ".isolation-placeholder").write_text("", encoding="utf-8")


def _assert_no_env_file_was_copied(scratch):
    """The developer's real ``backend/.env`` must not be in the scratch tree."""
    found = sorted(
        str(path.relative_to(scratch)) for path in (scratch / "backend").rglob(".env*")
    )
    assert found == [], (
        "the scratch tree carries an env file the copy should have excluded: %s" % found
    )


def _write_the_generated_env_file(scratch):
    """Write the GENERATED ``.env`` - a fake sentinel, nothing else."""
    (scratch / "backend" / ".env").write_text(
        "SYTHION_OLLAMA_API_KEY=%s\n" % FILE_SENTINEL, encoding="utf-8"
    )


def _disable_the_isolation_in_the_scratch_copy(scratch):
    """Turn the autouse isolation OFF in the scratch copy - and only there.

    The anchor names the fixture, so only that one fixture is affected.
    If the anchor is not found the control cannot be built, and a control
    that cannot be built cannot detect anything: that is a failure, never a
    silent skip.
    """
    conftest = scratch / "backend" / "tests" / "conftest.py"
    with open(str(conftest), "r", encoding="utf-8", newline="") as handle:
        source = handle.read()

    def _replacement(match):
        newline = "\r\n" if "\r\n" in match.group(0) else "\n"
        return "@pytest.fixture" + newline + "def _isolate_local_env_settings("

    patched, count = ISOLATION_ANCHOR.subn(_replacement, source, count=1)
    if count != 1:
        pytest.fail(
            "the negative control could not be built: the anchor of the autouse "
            "isolation fixture was not found in the scratch copy of "
            "backend/tests/conftest.py, so the control would prove nothing"
        )
    with open(str(conftest), "w", encoding="utf-8", newline="") as handle:
        handle.write(patched)


def _child_environment(scratch, signal_path):
    """Build the child environment EXPLICITLY.

    Nothing is inherited wholesale: every ``SYTHION_*`` variable is removed
    (case-insensitively), ``PYTHONPATH`` is scrubbed so the scratch tree
    cannot be shadowed, and ``PYTEST_ADDOPTS`` is removed so the caller's
    pytest options cannot change the child's command line.
    """
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("SYTHION_"):
            continue
        if upper in ("PYTHONPATH", "PYTEST_ADDOPTS"):
            continue
        env[key] = value
    # The profile directory and %LOCALAPPDATA% resolve inside the scratch
    # tree, so the child's data and its database never touch a real profile.
    env["LOCALAPPDATA"] = str(scratch / "localappdata")
    env["SYTHION_DATA_DIR"] = str(scratch / "profile")
    # The process-environment sentinel: the isolation under test has to
    # neutralise this one too.
    env["SYTHION_SELECTED_MODEL"] = ENV_SENTINEL
    # The probe's own plumbing.  NOT ``SYTHION_*``-prefixed, because the
    # isolation deletes every ``SYTHION_*`` variable for the duration of a
    # test and the probe's plumbing has to survive that.
    env["PROBE_SCRATCH_ROOT"] = str(scratch)
    env["PROBE_SIGNAL_PATH"] = str(signal_path)
    env["PROBE_NETWORK_ATTEMPTS"] = str(scratch / "probe-network-attempts.txt")
    env["PROBE_FILE_SENTINEL"] = FILE_SENTINEL
    env["PROBE_ENV_SENTINEL"] = ENV_SENTINEL
    return env


def _run_nested(scratch, index):
    """Run one nested pytest subprocess against the scratch tree."""
    signal_path = scratch / ("probe-signal-%d.txt" % index)
    report_path = scratch / ("report-%d.xml" % index)
    command = [
        sys.executable,
        "-m",
        "pytest",
        "tests/test_env_isolation_probe.py",
        "tests/test_setup_readiness.py",
        "-q",
        "--disable-warnings",
        "--junitxml=%s" % report_path,
    ]
    env = _child_environment(scratch, signal_path)
    started = time.monotonic()
    try:
        completed = subprocess.run(
            command,
            cwd=str(scratch / "backend"),
            env=env,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            encoding="utf-8",
            errors="replace",
            timeout=RUN_TIMEOUT_SECONDS,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            "the nested isolation run %d exceeded the %d s timeout"
            % (index, RUN_TIMEOUT_SECONDS)
        )
    elapsed = time.monotonic() - started
    return _NestedRun(
        index, completed, report_path, signal_path, scratch / "probe-network-attempts.txt", elapsed
    )


def _module_name(case):
    """The module part of a JUnit testcase, without path or package prefix.

    pytest writes the classname either as the dotted module path
    (``backend.tests.test_setup_readiness``) or as the file path, depending
    on the import mode; both spellings reduce to the same module name here.
    """
    classname = (case.get("classname") or "").replace("\\", "/")
    module = classname.rsplit("/", 1)[-1]
    if module.endswith(".py"):
        return module[:-3]
    return module.rsplit(".", 1)[-1]


def _verdict(case):
    """The verdict of a JUnit testcase: passed / failed / error / skipped."""
    if case.find("failure") is not None:
        return "failed"
    if case.find("error") is not None:
        return "error"
    if case.find("skipped") is not None:
        return "skipped"
    return "passed"


def _outcomes(report_path):
    """Map ``(module, test name) -> verdict`` out of a JUnit XML."""
    outcomes = {}
    for case in ET.parse(str(report_path)).iter("testcase"):
        outcomes[(_module_name(case), case.get("name") or "")] = _verdict(case)
    return outcomes


def _readiness_outcomes(run):
    """The readiness file's outcome map: test name -> verdict."""
    outcomes = _outcomes(run.report_path)
    return {
        name: verdict
        for (module, name), verdict in outcomes.items()
        if module == READINESS_MODULE
    }


def _failure_of(run, module, name):
    """The failure text of one JUnit testcase, or None when it did not fail."""
    for case in ET.parse(str(run.report_path)).iter("testcase"):
        if _module_name(case) != module:
            continue
        if (case.get("name") or "") != name:
            continue
        for tag in ("failure", "error"):
            element = case.find(tag)
            if element is not None:
                return element.get("message") or element.text or ""
    return None


def _assert_no_sentinel_leaked(run, label):
    """Neither sentinel may occur in any captured output of a run."""
    for text, where in ((run.output, "stdout/stderr"), (run.report_text, "the JUnit XML")):
        for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
            assert sentinel not in text, (
                "%s: a sentinel value reached the %s" % (label, where)
            )


def _assert_the_signal(run, expected, label):
    """The probe's signal line must be exactly the expected string."""
    assert run.signal_path.exists(), (
        "%s: the probe did not write its signal file" % label
    )
    with open(str(run.signal_path), "r", encoding="utf-8") as handle:
        signal = handle.read().strip()
    assert signal == expected, (
        "%s: the probe's signal line is not the expected one (expected %r)"
        % (label, expected)
    )


def _assert_no_network_attempt(run, label):
    """No outbound connection may have been attempted."""
    assert not run.network_path.exists(), (
        "%s: the probe recorded a blocked outbound connection attempt" % label
    )


def _remove_scratch_tree(scratch):
    """Remove the scratch tree.

    Returns None when the tree is gone, an error string when it is not.  A
    tree that survives the removal is debris, and debris must be visible.
    """
    if not scratch.exists():
        return None

    def _unlock_and_retry(function, path, exc=None):
        try:
            os.chmod(str(path), stat.S_IWRITE)
        except OSError:
            pass
        try:
            function(str(path))
        except OSError:
            pass

    try:
        if sys.version_info >= (3, 12):
            shutil.rmtree(str(scratch), onexc=_unlock_and_retry)
        else:
            shutil.rmtree(str(scratch), onerror=_unlock_and_retry)
    except OSError as exc:
        return "%s: %s" % (type(exc).__name__, exc)
    if scratch.exists():
        return "the scratch tree still exists after the removal"
    return None


def test_the_local_env_isolation_is_measured_not_assumed():
    """A deleted isolation must turn this suite RED - on a machine with no ``.env``.

    Three nested runs against a scratch copy of the tree, with the exact
    signal line asserted in both directions and the negative control built
    by deleting the isolation from the SCRATCH copy.
    """
    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-"))
    try:
        _build_scratch_tree(scratch)
        _assert_no_env_file_was_copied(scratch)

        # -- run 1: no .env at all, the isolation is present ---------------
        run1 = _run_nested(scratch, 1)
        assert run1.returncode == 0, (
            "run 1 (no .env, isolation present) exited %d; the output was:\n%s"
            % (run1.returncode, run1.output)
        )
        _assert_the_signal(run1, SIGNAL_ISOLATED, "run 1")
        outcomes1 = _outcomes(run1.report_path)
        assert outcomes1.get((PROBE_MODULE, PROBE_TEST)) == "passed", (
            "run 1: the generated probe did not pass"
        )
        readiness1 = _readiness_outcomes(run1)
        assert readiness1, "run 1: the readiness file produced no testcase"
        for name, verdict in readiness1.items():
            assert verdict == "passed", (
                "run 1: the readiness test %s was %s" % (name, verdict)
            )
        _assert_no_sentinel_leaked(run1, "run 1")
        _assert_no_network_attempt(run1, "run 1")

        # -- run 2: the GENERATED sentinel .env, the isolation is present --
        _write_the_generated_env_file(scratch)
        run2 = _run_nested(scratch, 2)
        assert run2.returncode == 0, (
            "run 2 (sentinel .env, isolation present) exited %d; the output was:\n%s"
            % (run2.returncode, run2.output)
        )
        _assert_the_signal(run2, SIGNAL_ISOLATED, "run 2")
        outcomes2 = _outcomes(run2.report_path)
        assert outcomes2.get((PROBE_MODULE, PROBE_TEST)) == "passed", (
            "run 2: the generated probe did not pass"
        )
        readiness2 = _readiness_outcomes(run2)
        for name, verdict in readiness2.items():
            assert verdict == "passed", (
                "run 2: the readiness test %s was %s" % (name, verdict)
            )
        # The heart of criterion 1: with the sentinel .env present, the
        # readiness file stays green with the SAME pass/fail set as the
        # .env-absent tree - item by item.
        assert readiness2 == readiness1, (
            "the readiness file's outcome set changed when the sentinel .env "
            "was present: %s became %s" % (readiness1, readiness2)
        )
        _assert_no_sentinel_leaked(run2, "run 2")
        _assert_no_network_attempt(run2, "run 2")

        # -- run 3: the NEGATIVE CONTROL, the isolation is removed ---------
        _disable_the_isolation_in_the_scratch_copy(scratch)
        run3 = _run_nested(scratch, 3)
        assert run3.returncode != 0, (
            "run 3 (sentinel .env, isolation removed) exited 0: the negative "
            "control did not fail - the check is vacuous"
        )
        _assert_the_signal(run3, SIGNAL_UNISOLATED, "run 3")
        assert _failure_of(run3, PROBE_MODULE, PROBE_TEST) is not None, (
            "run 3: the generated probe did not fail"
        )
        assert _failure_of(run3, READINESS_MODULE, READINESS_ISOLATION_TEST) is not None, (
            "run 3: the repository's own %s did not fail" % READINESS_ISOLATION_TEST
        )
        _assert_no_sentinel_leaked(run3, "run 3")
        _assert_no_network_attempt(run3, "run 3")

        print(
            "020 isolation measurement: run 1 %.1fs, run 2 %.1fs, run 3 %.1fs"
            % (run1.elapsed, run2.elapsed, run3.elapsed)
        )
    finally:
        removal_error = _remove_scratch_tree(scratch)
        if removal_error is not None:
            pytest.fail(
                "the scratch tree %s could not be removed (%s); debris must not "
                "accumulate silently" % (scratch, removal_error)
            )
