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
  * the legacy ``~/.sythion`` migration cannot copy a real user's data into
    the scratch tree: the profile directory the migration REALLY looks at
    carries a placeholder before anything runs, and a fake legacy profile
    inside the scratch tree is proven not to be copied - with the app's
    lifespan running and with it not running;
  * no sentinel value is ever RENDERED: every comparison is boolean and every
    failure message names the property that broke, and this module asserts
    that neither sentinel occurs in any captured output or JUnit XML;
  * no network I/O: the child environment redirects the profile directory and
    ``%LOCALAPPDATA%`` into the scratch tree, and the probe blocks outbound
    connections and records every attempt;
  * the child environment is built EXPLICITLY (every ``SYTHION_*`` variable
    removed, ``PYTHONPATH`` scrubbed) so the developer's shell contributes
    nothing and the scratch tree cannot be shadowed;
  * EVERY child environment - the three nested runs' and the migration
    probe's - removes BOTH profile-dir overrides, by their production
    constants: the canonical ``SYTHION_DATA_DIR`` and the LEGACY
    ``Sythion (Synth)_DATA_DIR``.  The legacy name does not start with
    ``SYTHION_`` - it upper-cases to ``SYTHION (SYNTH)_DATA_DIR``, a SPACE
    and not an underscore - so the ``SYTHION_*`` sweep cannot see it, and
    ``backend/core/storage.py`` honours it whenever the canonical variable
    is absent: a child would follow it out of the scratch tree, or - with
    the canonical override set as well - raise ``ValueError: conflicting
    profile directories`` at import time;
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

try:
    from backend.core.storage import (
        DATA_DIR_ENV,
        DEFAULT_DATA_DIR_NAME,
        LEGACY_DATA_DIR_ENV,
    )
except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
    from core.storage import (
        DATA_DIR_ENV,
        DEFAULT_DATA_DIR_NAME,
        LEGACY_DATA_DIR_ENV,
    )


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


def _fallback_profile_dir(scratch):
    """The profile directory the isolation-present shape resolves to.

    ``backend/core/storage.py`` resolves the profile directory from
    ``SYTHION_DATA_DIR`` first and falls back to
    ``%LOCALAPPDATA%\\<DEFAULT_DATA_DIR_NAME>`` when no override is set.  The
    isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
    test, so the profile directory the nested runs - and the app lifespan -
    really use is the ``%LOCALAPPDATA%`` one, which inside the scratch tree
    is ``<scratch>/localappdata/Sythion (Synth)``.

    The name is taken from the production constant, not re-spelled here, so a
    rename in ``backend/core/storage.py`` moves this fence with it.
    """
    return scratch / "localappdata" / DEFAULT_DATA_DIR_NAME


def _write_the_placeholder(directory):
    """Put the isolation placeholder into a directory, creating it if needed."""
    directory.mkdir(parents=True, exist_ok=True)
    (directory / ".isolation-placeholder").write_text("", encoding="utf-8")


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
    # The legacy profile migration (``backend/core/storage.py``::
    # ``migrate_legacy_data_dir``) copies ``~/.sythion`` into the PROFILE
    # DIRECTORY, but only when that directory is EMPTY.  There are TWO
    # directories the profile can resolve to, and BOTH have to carry the
    # placeholder:
    #
    #   * ``<scratch>/profile`` - the target while ``SYTHION_DATA_DIR`` is
    #     set (the child environment sets it, and the isolation removes it
    #     again for the duration of every test);
    #   * ``<scratch>/localappdata/Sythion (Synth)`` - the target in the
    #     isolation-PRESENT shape, where ``SYTHION_DATA_DIR`` is gone and
    #     ``data_dir()`` falls back to ``%LOCALAPPDATA%``.  This is the target
    #     the nested runs and the app lifespan really use, so a placeholder in
    #     the first directory alone fences nothing there.
    #
    # The placeholder is written BEFORE anything runs, so the fence is a
    # property of the tree and not of the order in which the app happens to
    # start up.
    _write_the_placeholder(scratch / "profile")
    _write_the_placeholder(_fallback_profile_dir(scratch))


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


def _inherited_environment():
    """The parent environment, EXPLICITLY.

    Nothing is inherited wholesale: every ``SYTHION_*`` variable is removed
    (case-insensitively) and ``PYTHONPATH``/``PYTEST_ADDOPTS`` are scrubbed,
    so the developer's shell contributes nothing and the scratch tree cannot
    be shadowed.  This is the base EVERY child environment is built from -
    the three nested runs' and the migration probe's - so the two cannot
    drift apart.
    """
    env = {}
    for key, value in os.environ.items():
        upper = key.upper()
        if upper.startswith("SYTHION_"):
            continue
        if upper in ("PYTHONPATH", "PYTEST_ADDOPTS"):
            continue
        env[key] = value
    return env


def _scrub_the_profile_dir_overrides(env):
    """Remove BOTH profile-dir overrides from ``env``, in place.

    ``backend/core/storage.py`` resolves the profile directory from TWO
    environment variables: the canonical ``SYTHION_DATA_DIR``
    (``storage.DATA_DIR_ENV``) and the legacy ``Sythion (Synth)_DATA_DIR``
    (``storage.LEGACY_DATA_DIR_ENV``).  The legacy one is honoured whenever
    the canonical one is absent - and the isolation DELETES the canonical
    one for the duration of every test - so the legacy one is exactly what
    ``data_dir()`` would follow.

    The legacy name starts with ``Sythion ``, not ``SYTHION_``, so the
    ``SYTHION_*`` sweep cannot see it.  On a machine whose shell carries it,
    a child would resolve a profile directory OUTSIDE the scratch tree - or,
    when the canonical override is set as well, raise ``ValueError:
    conflicting profile directories`` at import time.

    ONE helper serves EVERY child environment - the three nested runs' and
    the migration probe's - so the two cannot drift apart again.  Both names
    are taken from the PRODUCTION constants, so a rename in
    ``backend/core/storage.py`` moves the scrub with it.  The comparison is
    case-insensitive because that is how the Windows environment block
    behaves: ``os.environ`` itself upper-cases the key.
    """
    for override in (DATA_DIR_ENV, LEGACY_DATA_DIR_ENV):
        for key in list(env):
            if key.upper() == override.upper():
                del env[key]
    return env


def _child_environment(scratch, signal_path):
    """Build the child environment EXPLICITLY.

    Nothing is inherited wholesale (see ``_inherited_environment``), and
    NEITHER profile-dir override survives the scrub: the canonical one is
    ``SYTHION_*``-prefixed, the legacy one is removed by its production name
    (see ``_scrub_the_profile_dir_overrides``).  Without that, a machine
    whose shell carries the legacy override would hand the child TWO
    conflicting profile directories, and ``backend/core/storage.py`` would
    raise ``ValueError: conflicting profile directories`` at import time.

    The canonical override is then set ON PURPOSE, to the scratch tree's own
    profile directory, so the child's data and its database never touch a
    real profile.
    """
    env = _inherited_environment()
    _scrub_the_profile_dir_overrides(env)
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


def _redact_sentinels(text):
    """Replace a sentinel value with a placeholder, should one occur at all.

    A second layer under the leak assertion: the raw child output may be
    embedded in a failure message, and this makes it impossible for a
    sentinel VALUE to reach a terminal or a JUnit XML even if the leak
    assertion is ever reordered or removed.
    """
    for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
        text = text.replace(sentinel, "[REDACTED-SENTINEL]")
    return text


def _assert_no_sentinel_leaked(run, label):
    """Neither sentinel may occur in any captured output of a run.

    This assertion is deliberately made BEFORE any assertion that renders the
    run's raw output: the raw output is the one place a sentinel value could
    escape, so it may only be rendered once this check has passed.

    The comparison is a BOOLEAN on purpose.  ``assert sentinel not in text``
    would make pytest render both operands - the sentinel value and the text
    that contains it - so the check that exists to keep the value out of the
    output would be the very thing that prints it.  Only the property that
    broke is ever named.
    """
    for text, where in ((run.output, "stdout/stderr"), (run.report_text, "the JUnit XML")):
        for sentinel in (FILE_SENTINEL, ENV_SENTINEL):
            leaked = sentinel in text
            assert leaked is False, (
                "%s: a sentinel value reached the %s" % (label, where)
            )


def _read_signal(run):
    """The probe's signal line, read from the file it wrote."""
    with open(str(run.signal_path), "r", encoding="utf-8") as handle:
        return handle.read().strip()


def _assert_the_signal(run, expected, label):
    """The probe's signal line must be exactly the expected string."""
    assert run.signal_path.exists(), (
        "%s: the probe did not write its signal file" % label
    )
    signal = _read_signal(run)
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
        # The leak check comes FIRST: the raw child output is rendered in the
        # failure message below, and the whole point of the sentinel is that
        # it never appears in any captured stdout/stderr.  Once this check has
        # passed, the output is provably sentinel-free, so rendering it is
        # safe (and it is redacted a second time, see ``_redact_sentinels``).
        _assert_no_sentinel_leaked(run1, "run 1")
        assert run1.returncode == 0, (
            "run 1 (no .env, isolation present) exited %d; the output was:\n%s"
            % (run1.returncode, _redact_sentinels(run1.output))
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
        _assert_no_network_attempt(run1, "run 1")

        # -- run 2: the GENERATED sentinel .env, the isolation is present --
        _write_the_generated_env_file(scratch)
        run2 = _run_nested(scratch, 2)
        _assert_no_sentinel_leaked(run2, "run 2")
        assert run2.returncode == 0, (
            "run 2 (sentinel .env, isolation present) exited %d; the output was:\n%s"
            % (run2.returncode, _redact_sentinels(run2.output))
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
        _assert_no_network_attempt(run2, "run 2")

        # -- run 3: the NEGATIVE CONTROL, the isolation is removed ---------
        _disable_the_isolation_in_the_scratch_copy(scratch)
        run3 = _run_nested(scratch, 3)
        _assert_no_sentinel_leaked(run3, "run 3")
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
        _assert_no_network_attempt(run3, "run 3")

        print(
            "020 isolation measurement: run 1 %.1fs [%s] | run 2 %.1fs [%s] | "
            "run 3 %.1fs [%s]"
            % (
                run1.elapsed,
                _read_signal(run1),
                run2.elapsed,
                _read_signal(run2),
                run3.elapsed,
                _read_signal(run3),
            )
        )
    finally:
        removal_error = _remove_scratch_tree(scratch)
        if removal_error is not None:
            pytest.fail(
                "the scratch tree %s could not be removed (%s); debris must not "
                "accumulate silently" % (scratch, removal_error)
            )


# ---------------------------------------------------------------------------
# F1 - the legacy ``~/.sythion`` migration fence
# ---------------------------------------------------------------------------
#
# ``backend/core/storage.py::migrate_legacy_data_dir()`` copies the legacy
# ``~/.sythion`` profile into the PROFILE DIRECTORY, and only when that
# directory is EMPTY.  Two things follow, and both are measured below:
#
#   1. the directory the migration looks at is NOT always
#      ``<scratch>/profile``.  In the isolation-PRESENT shape the isolation
#      has deleted ``SYTHION_DATA_DIR``, so ``data_dir()`` resolves to
#      ``<scratch>/localappdata/Sythion (Synth)``.  A placeholder in
#      ``<scratch>/profile`` alone fences nothing there - the fence has to be
#      in the directory the migration really looks at, and it has to be there
#      BEFORE anything runs;
#   2. "nothing was copied" must not be an accident of the app never
#      starting up.  The proof therefore runs the app's OWN lifespan with a
#      fake legacy profile present, and it runs the same migration once with
#      the placeholder REMOVED, to show that the fence - and not luck - is
#      what stops the copy.
#
# The fake legacy profile lives INSIDE the scratch tree, and the child's
# ``HOME``/``USERPROFILE`` point at it, so the developer's real ``~/.sythion``
# is never even a candidate source: ``LEGACY_DATA_DIR`` is computed from
# ``expanduser("~")`` at import time.

# The fake legacy profile carries a file whose CONTENT is the marker.  If the
# migration ever copies the legacy profile into the scratch tree, the marker
# shows up there - a name and a content, never a real user's data.
LEGACY_MARKER_NAME = "legacy-marker.txt"
LEGACY_MARKER_CONTENT = "LEGACY-MIGRATION-MARKER-020\n"

# The probe that observes the migration in the scratch tree.  It is written
# into the scratch tree at run time (the file set of this item stays at three
# files under ``backend/tests/``).  It prints one ``PROOF <name>=<value>`` line
# per fact and never prints a file's content.
_MIGRATION_PROBE_SOURCE = '''\
"""Observe the legacy profile migration in one chosen shape.

The probe runs inside the scratch tree, prints one ``PROOF <name>=<value>``
line per observed fact, and never prints a file's content.
"""

import os
import sys

MODE = os.environ["PROOF_MODE"]
SCRATCH_ROOT = os.environ["PROOF_SCRATCH_ROOT"]
sys.path.insert(0, SCRATCH_ROOT)

from backend.core import storage

print("PROOF target=%s" % storage.data_dir())
print("PROOF legacy=%s" % storage.LEGACY_DATA_DIR)
# The entries the profile directory holds RIGHT NOW - before the app is
# imported and before the migration is invoked.  The parent test uses them to
# prove that the profile directory was PRISTINE (nothing but the
# placeholder) when the migration looked at it, so that "nothing was copied"
# is the fence's doing and not the tree's state.
print("PROOF target_entries=%s" % ",".join(sorted(os.listdir(storage.data_dir()))))

if MODE == "migration":
    # The migration invoked in the isolation-present shape, with nothing else
    # running - the shape in which the legacy profile would be copied.
    print("PROOF migrated=%s" % storage.migrate_legacy_data_dir())
elif MODE == "current-shape":
    # The CURRENT shape: the app is imported and its TestClient is built, but
    # it is never entered, so the app's lifespan never runs.
    import backend.tests.conftest  # noqa: F401 - installs the httpx shim
    from fastapi.testclient import TestClient
    from backend.main import app
    TestClient(app)
    print("PROOF lifespan=not-run")
elif MODE == "lifespan":
    # The app's OWN startup path, entered.
    import backend.tests.conftest  # noqa: F401 - installs the httpx shim
    from fastapi.testclient import TestClient
    from backend.main import app
    with TestClient(app):
        pass
    print("PROOF lifespan=ran")
else:
    raise SystemExit("unknown proof mode: %s" % MODE)
'''


class _MigrationProbeRun(object):
    """One migration probe subprocess and the facts it reported."""

    def __init__(self, mode, completed):
        self.mode = mode
        self.completed = completed

    @property
    def output(self):
        return (self.completed.stdout or "") + (self.completed.stderr or "")

    @property
    def facts(self):
        """The ``PROOF <name>=<value>`` lines the probe printed."""
        facts = {}
        for line in (self.completed.stdout or "").splitlines():
            if not line.startswith("PROOF "):
                continue
            name, _, value = line[len("PROOF "):].partition("=")
            facts[name] = value
        return facts

    @property
    def target(self):
        """The profile directory the migration looked at."""
        return self.facts.get("target", "")

    @property
    def legacy(self):
        """The legacy directory the migration would have copied."""
        return self.facts.get("legacy", "")

    @property
    def target_entries(self):
        """The names the profile directory held when the probe looked at it."""
        entries = self.facts.get("target_entries", "")
        return [name for name in entries.split(",") if name]

    @property
    def migrated(self):
        """Whether the migration reported that it copied the legacy profile."""
        return self.facts.get("migrated", "")


def _run_the_migration_probe(scratch, mode, localappdata, fake_home):
    """Run the migration probe in the isolation-present shape.

    The environment is the CHECK's own child environment (see
    ``_inherited_environment``) with the isolation's effect applied: the
    isolation deletes EVERY ``SYTHION_*`` variable for the duration of every
    test, so the probe observes none of them - not even ``SYTHION_DATA_DIR``.
    The base environment removes them all, so the probe's own view is the
    isolation-present one.
    The LEGACY profile-dir override is removed as well, by the SAME helper
    the nested runs use (see ``_scrub_the_profile_dir_overrides``): it is
    the one the ``SYTHION_*`` scrub cannot see, and it is exactly what
    ``data_dir()`` would follow once the canonical variable is gone.  With
    both overrides absent, ``data_dir()`` falls back to ``%LOCALAPPDATA%``.

    Two variables are redirected into the scratch tree, and only into the
    scratch tree: ``%LOCALAPPDATA%`` (where the profile directory resolves)
    and ``HOME``/``USERPROFILE`` (where the legacy profile the migration
    reads resolves - it is computed from ``expanduser("~")`` at import time).
    """
    script = scratch / "migration-probe.py"
    script.write_text(_MIGRATION_PROBE_SOURCE, encoding="utf-8")

    env = _inherited_environment()
    # The profile directory has a SECOND override, and the prefix scrub
    # above cannot see it: ``backend/core/storage.py`` honours the legacy
    # ``Sythion (Synth)_DATA_DIR`` whenever the canonical
    # ``SYTHION_DATA_DIR`` is absent - and the isolation DELETES the
    # canonical one, so the legacy one is exactly what ``data_dir()`` would
    # follow.  The legacy name starts with ``Sythion ``, not ``SYTHION_``,
    # so it survives the sweep above and would resolve the probe's profile
    # directory OUTSIDE the scratch tree (and the migration would copy the
    # fake legacy marker there).  Both overrides are therefore removed HERE,
    # by the SHARED helper - the very same one the nested runs' child
    # environment uses - so the two places cannot drift apart again.  The
    # names come from the production constants, so a rename in
    # ``backend/core/storage.py`` moves the scrub with it, and the
    # comparison is case-insensitive because that is how the Windows
    # environment block behaves - ``os.environ`` itself upper-cases the key.
    _scrub_the_profile_dir_overrides(env)
    env["LOCALAPPDATA"] = str(localappdata)
    env["HOME"] = str(fake_home)
    env["USERPROFILE"] = str(fake_home)
    env["PROOF_MODE"] = mode
    env["PROOF_SCRATCH_ROOT"] = str(scratch)

    try:
        completed = subprocess.run(
            [sys.executable, str(script)],
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
            "the migration probe (%s) exceeded the %d s timeout"
            % (mode, RUN_TIMEOUT_SECONDS)
        )
    return _MigrationProbeRun(mode, completed)


def _legacy_marker_copies(scratch, legacy_dir):
    """Every file under the scratch tree that carries the legacy marker.

    The fake legacy profile itself is excluded: it is the SOURCE, not a copy.
    """
    copies = []
    for path in sorted(scratch.rglob(LEGACY_MARKER_NAME)):
        if not path.is_file():
            continue
        if legacy_dir in path.parents:
            continue
        try:
            content = path.read_text(encoding="utf-8", errors="replace")
        except OSError:
            continue
        if LEGACY_MARKER_CONTENT in content:
            copies.append(str(path.relative_to(scratch)))
    return copies


def test_the_legacy_migration_cannot_copy_user_data_into_the_scratch_tree():
    """The legacy ``~/.sythion`` migration must not be able to copy into the scratch tree.

    The fence is a property of the tree, not of the order in which the app
    happens to start up:

      * in the isolation-present shape the profile directory resolves to
        ``<scratch>/localappdata/Sythion (Synth)`` - the directory that
        carries the placeholder - and NOT to ``<scratch>/profile``;
      * with the app's OWN lifespan running, and with a fake legacy profile
        inside the scratch tree, nothing is copied;
      * with the app's lifespan NOT running (the current shape), nothing is
        copied either;
      * and the same migration, invoked with the placeholder REMOVED, DOES
        copy - so the fence is what stops the copy, and the proof above is
        not vacuous.
    """
    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-migration-"))
    try:
        _build_scratch_tree(scratch)

        # The fake legacy profile, INSIDE the scratch tree.
        fake_home = scratch / "fake-home"
        legacy_dir = fake_home / ".sythion"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / LEGACY_MARKER_NAME).write_text(
            LEGACY_MARKER_CONTENT, encoding="utf-8"
        )

        # The scratch tree's own ``%LOCALAPPDATA%`` root - the one the app's
        # profile directory really resolves to - and a second root that
        # carries NO placeholder, which is the control.
        fenced_root = scratch / "localappdata"
        unfenced_root = scratch / "localappdata-unfenced"

        def _run(mode, root):
            run = _run_the_migration_probe(scratch, mode, root, fake_home)
            assert run.completed.returncode == 0, (
                "the migration probe (%s) exited %d; the output was:\n%s"
                % (mode, run.completed.returncode, _redact_sentinels(run.output))
            )
            return run

        # -- the migration invoked in the isolation-present shape -----------
        # This run comes FIRST, and it has to: the profile directory must be
        # PRISTINE when the migration looks at it, so that the only thing that
        # can stop the copy is the placeholder.  The probe reports what the
        # directory held, and the assertion below checks it - so a tree that
        # was not pristine fails LOUDLY instead of proving nothing.
        migration = _run("migration", fenced_root)
        assert migration.target == str(_fallback_profile_dir(scratch)), (
            "in the isolation-present shape the profile directory resolved to "
            "%s and not to the placeholder-bearing %s"
            % (migration.target, _fallback_profile_dir(scratch))
        )
        assert migration.legacy == str(legacy_dir), (
            "the migration would have read %s and not the fake legacy profile "
            "inside the scratch tree" % migration.legacy
        )
        assert migration.target_entries == [".isolation-placeholder"], (
            "the profile directory held %s and not only the placeholder, so "
            "this run would prove nothing: the fence has to be the ONLY thing "
            "in the directory when the migration looks at it"
            % migration.target_entries
        )
        assert migration.migrated == "False", (
            "the migration reported that it copied the legacy profile into "
            "the scratch tree (target %s)" % migration.target
        )
        assert _legacy_marker_copies(scratch, legacy_dir) == [], (
            "the fake legacy profile was copied into the scratch tree: %s"
            % _legacy_marker_copies(scratch, legacy_dir)
        )

        # -- the control: the same run WITHOUT the placeholder --------------
        # The only difference to the run above is the placeholder: a second
        # ``%LOCALAPPDATA%`` root inside the scratch tree that carries none.
        # Without the fence the migration has to copy, otherwise the
        # assertions above would prove nothing.
        control = _run("migration", unfenced_root)
        assert control.target_entries == [], (
            "the control's profile directory was not empty (%s), so the "
            "control would prove nothing" % control.target_entries
        )
        assert control.migrated == "True", (
            "the control did not copy the fake legacy profile: without the "
            "placeholder the migration has to copy it, otherwise this proof "
            "proves nothing (target %s)" % control.target
        )
        expected_copy = str(
            Path("localappdata-unfenced")
            / DEFAULT_DATA_DIR_NAME
            / LEGACY_MARKER_NAME
        )
        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
            "the control did not copy the fake legacy profile into the "
            "unfenced profile directory: %s"
            % _legacy_marker_copies(scratch, legacy_dir)
        )

        # -- the CURRENT shape: the app's lifespan never runs --------------
        current = _run("current-shape", fenced_root)
        assert current.target == str(_fallback_profile_dir(scratch)), (
            "in the isolation-present shape the profile directory resolved to "
            "%s and not to the placeholder-bearing %s"
            % (current.target, _fallback_profile_dir(scratch))
        )
        assert current.target_entries == [".isolation-placeholder"], (
            "the profile directory held %s and not only the placeholder"
            % current.target_entries
        )
        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
            "the fake legacy profile was copied into the scratch tree while "
            "the app's lifespan was not running (only the control's copy is "
            "expected): %s" % _legacy_marker_copies(scratch, legacy_dir)
        )

        # -- the app's OWN lifespan running --------------------------------
        lifespan_run = _run("lifespan", fenced_root)
        assert lifespan_run.facts.get("lifespan") == "ran", (
            "the app's lifespan did not run: %s" % lifespan_run.facts
        )
        assert _legacy_marker_copies(scratch, legacy_dir) == [expected_copy], (
            "the fake legacy profile was copied into the scratch tree while "
            "the app's lifespan was running (only the control's copy is "
            "expected): %s" % _legacy_marker_copies(scratch, legacy_dir)
        )
    finally:
        removal_error = _remove_scratch_tree(scratch)
        if removal_error is not None:
            pytest.fail(
                "the scratch tree %s could not be removed (%s); debris must not "
                "accumulate silently" % (scratch, removal_error)
            )


# ---------------------------------------------------------------------------
# The legacy profile-dir override must not reach the migration probe
# ---------------------------------------------------------------------------
#
# ``backend/core/storage.py`` resolves the profile directory from TWO
# environment variables: the canonical ``SYTHION_DATA_DIR`` and the legacy
# ``Sythion (Synth)_DATA_DIR`` (the packaging and launch scripts set the
# legacy one).  The legacy one is honoured whenever the canonical one is
# absent - and the isolation DELETES the canonical one, so the legacy one is
# precisely what ``data_dir()`` follows.
#
# The legacy name starts with ``Sythion ``, not ``SYTHION_``, so a scrub that
# only removes ``SYTHION_*`` variables does not see it.  On a machine where
# the legacy override happens to be set, the migration probe's profile
# directory would therefore resolve OUTSIDE the scratch tree and the fake
# legacy marker would be copied there.
#
# The test below measures exactly that.  The override is set in the PARENT
# environment and points at a directory INSIDE the scratch tree, so the test
# itself never writes outside it, and the test asserts both halves of the
# containment: the probe's profile directory is the expected scratch-internal
# one, and the override's directory is left untouched.


def test_the_migration_probe_stays_in_the_scratch_tree_when_the_legacy_override_is_set(
    monkeypatch,
):
    """A legacy profile-dir override in the shell must not move the probe out.

    ``backend/core/storage.py`` honours ``Sythion (Synth)_DATA_DIR`` when the
    canonical ``SYTHION_DATA_DIR`` is absent, and the isolation deletes the
    canonical one.  The legacy name does not start with ``SYTHION_``, so it
    is not removed by a ``SYTHION_*`` scrub: the probe would follow it out
    of the scratch tree and copy the fake legacy marker there.

    The override points at a directory INSIDE the scratch tree, so the test
    never writes outside it, and the assertions cover the whole property:
    the probe's resolved profile directory is the expected scratch-internal
    one, the migration reports that it copied nothing, and the override's
    directory is left empty.
    """
    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-legacy-override-"))
    try:
        _build_scratch_tree(scratch)

        # The fake legacy profile, INSIDE the scratch tree: the source the
        # migration would copy.  The child's ``HOME``/``USERPROFILE`` point at
        # it, so the developer's real ``~/.sythion`` is never a source.
        fake_home = scratch / "fake-home"
        legacy_dir = fake_home / ".sythion"
        legacy_dir.mkdir(parents=True)
        (legacy_dir / LEGACY_MARKER_NAME).write_text(
            LEGACY_MARKER_CONTENT, encoding="utf-8"
        )

        # The override the developer's shell may carry, pointing INSIDE the
        # scratch tree.  ``monkeypatch`` restores whatever was there before,
        # so the real override (if any) is untouched by this test.
        override_dir = scratch / "legacy-override-target"
        override_dir.mkdir()
        monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(override_dir))

        run = _run_the_migration_probe(
            scratch, "migration", scratch / "localappdata", fake_home
        )
        assert run.completed.returncode == 0, (
            "the migration probe (migration) exited %d; the output was:\n%s"
            % (run.completed.returncode, _redact_sentinels(run.output))
        )

        # (1) the containment: the probe's profile directory is the
        # scratch-internal one, NOT the directory the override named.
        assert run.target == str(_fallback_profile_dir(scratch)), (
            "the probe's profile directory resolved to %s and not to the "
            "scratch-internal %s: the legacy override %s reached the probe's "
            "child environment"
            % (run.target, _fallback_profile_dir(scratch), LEGACY_DATA_DIR_ENV)
        )

        # (2) the probe's own report: the migration ran and copied nothing.
        assert run.migrated == "False", (
            "the migration reported that it copied the legacy profile into %s"
            % run.target
        )

        # (3) the write property: the override's directory is untouched, and
        # the fake legacy profile was not copied ANYWHERE in the scratch tree.
        written = sorted(path.name for path in override_dir.iterdir())
        assert written == [], (
            "the probe wrote %s into the directory the legacy override named"
            % written
        )
        assert _legacy_marker_copies(scratch, legacy_dir) == [], (
            "the fake legacy profile was copied into the scratch tree: %s"
            % _legacy_marker_copies(scratch, legacy_dir)
        )
    finally:
        removal_error = _remove_scratch_tree(scratch)
        if removal_error is not None:
            pytest.fail(
                "the scratch tree %s could not be removed (%s); debris must not "
                "accumulate silently" % (scratch, removal_error)
            )


# ---------------------------------------------------------------------------
# The legacy profile-dir override must not reach the NESTED RUNS either
# ---------------------------------------------------------------------------
#
# The same root cause, a second manifestation.  ``_child_environment`` sets
# the canonical ``SYTHION_DATA_DIR`` (to the scratch tree's own profile
# directory) and INHERITED the legacy override - it is not ``SYTHION_``-
# prefixed, so the ``SYTHION_*`` sweep could not see it.  Inside the nested
# run ``backend/core/storage.py::resolve_data_dir()`` then raised
# ``ValueError: conflicting profile directories`` at IMPORT time (both
# overrides present, naming different directories), so the measurement went
# RED on a machine that merely has the legacy override configured.
#
# Nothing was written outside the scratch tree in that shape - the failure
# happened before anything was created - but a RED measurement on a
# differently configured machine is a false alarm, and the ambient process
# environment is exactly what this item is about.
#
# The test below re-runs the measurement VERBATIM - the same three nested
# runs, the same assertions, the same negative control - with the legacy
# override present in the PARENT environment.  The override names a
# directory INSIDE this test's own scratch tree, so the test never writes
# outside it, and the test asserts that directory is left untouched.


def test_the_nested_runs_stay_green_with_the_legacy_override_is_set(monkeypatch):
    """The three nested runs must stay GREEN with the legacy override configured.

    ``backend/core/storage.py`` honours ``Sythion (Synth)_DATA_DIR`` whenever
    the canonical ``SYTHION_DATA_DIR`` is absent, and the child environment
    sets the canonical one.  The legacy name does not start with
    ``SYTHION_``, so it is not removed by a ``SYTHION_*`` scrub: the nested
    run would carry TWO conflicting profile directories and die at import
    time with ``ValueError: conflicting profile directories``.

    The measurement is re-run here exactly as it is - the same three nested
    runs, the same signal line, the same negative control - with the legacy
    override set in the PARENT environment.  It has to stay GREEN, and the
    directory the override names has to stay empty.
    """
    scratch = Path(tempfile.mkdtemp(prefix="sythion-020-nested-legacy-"))
    try:
        # The directory the legacy override names.  It is INSIDE this test's
        # own scratch tree, so nothing can be written outside it.
        override_dir = scratch / "legacy-override-target"
        override_dir.mkdir()
        # ``monkeypatch`` restores whatever was there before, so the real
        # override (if any) is untouched by this test.
        monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(override_dir))

        # The setup has to be real, or the test would prove nothing: the
        # PARENT environment really carries the legacy override.  The
        # comparison is case-insensitive because ``os.environ`` upper-cases
        # the key on Windows - the legacy name is seen there as
        # ``SYTHION (SYNTH)_DATA_DIR``.
        present = any(
            key.upper() == LEGACY_DATA_DIR_ENV.upper() for key in os.environ
        )
        assert present is True, (
            "the legacy override %s is not in the parent environment, so this "
            "test would prove nothing" % LEGACY_DATA_DIR_ENV
        )

        # The measurement itself, verbatim.
        try:
            test_the_local_env_isolation_is_measured_not_assumed()
        except AssertionError as exc:
            pytest.fail(
                "the isolation measurement went RED with the legacy override "
                "%s set in the parent environment: %s"
                % (LEGACY_DATA_DIR_ENV, exc)
            )

        # The write property: the directory the legacy override named is
        # untouched, so no child resolved a profile directory there.
        written = sorted(path.name for path in override_dir.iterdir())
        assert written == [], (
            "the nested runs wrote %s into the directory the legacy override "
            "named" % written
        )
    finally:
        removal_error = _remove_scratch_tree(scratch)
        if removal_error is not None:
            pytest.fail(
                "the scratch tree %s could not be removed (%s); debris must not "
                "accumulate silently" % (scratch, removal_error)
            )
