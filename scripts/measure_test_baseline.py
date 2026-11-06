#!/usr/bin/env python3
"""
Measure the Sythion (Synth) backend test baseline, and check the documents
that quote it.

WHY THIS SCRIPT EXISTS
----------------------
Every documented test baseline in this repository used to be a number that
somebody typed into prose. Those numbers moved every time a test was added or
removed, and a reader had no way to tell a stale document from a true one.
This script replaces the typed numbers with a MEASUREMENT, and it makes the
documents checkable against that measurement:

  * ``python scripts/measure_test_baseline.py`` measures and prints JSON;
  * ``python scripts/measure_test_baseline.py --check`` re-measures and
    compares the documents against the result (rules C1-C7);
  * ``python scripts/measure_test_baseline.py --check-collected`` is the cheap
    drift detector: it re-measures the collected count with ``--collect-only``
    instead of running the whole suite again.

The commands are READ OUT OF THE ``Makefile`` (``test`` and
``test-acceptance``), so this script can never measure something the
Makefile does not run. ``make`` itself is not required and is not used: the
recipe lines are executed directly with this interpreter.

THE THREE RULES THAT MAKE THIS SCRIPT TRUSTWORTHY
-------------------------------------------------
1. A PUBLISHED NUMBER COMES FROM A COMPLETED RUN. A suite run counts only
   when the child process exited 0 AND printed a parseable pytest summary AND
   printed no native-fault marker. Anything else - a crash, a native fault, a
   test failure, a missing summary - publishes NO counts at all.
2. NO COUNT IS HARD-CODED. There is not one expected number in this file. The
   numbers come from the suite, and ``--check`` compares the documents
   against the measurement structurally, so the check keeps working after the
   numbers move.
3. NO WARNING COUNT IS PUBLISHED, EVER. The warning count moves between runs
   of an unchanged tree, so it is not a fact about the code. It is not
   printed, not logged, not stored in the JSON.

EXIT CODES
----------
0  the measurement succeeded / the documents agree with it
1  ``--check`` mode: the documents DISAGREE with the measurement (stale)
2  no complete measurement could be made (crash, native fault, failure,
   missing summary) - INCONCLUSIVE; the documents are not implicated
3  usage error (no Makefile target, unreadable root, invalid --measured file)
4  ``--check`` mode: the running interpreter/platform differs from the one
   the documents name - INCONCLUSIVE; the documents were measured elsewhere
"""

import argparse
import json
import os
import platform
import re
import shlex
import subprocess
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import unquote

SCHEMA_VERSION = 1

EXIT_OK = 0
EXIT_STALE = 1
EXIT_INCONCLUSIVE = 2
EXIT_USAGE = 3
EXIT_ENVIRONMENT = 4

# The documents that carry the baseline block, and the one that may only
# quote a pair (rule C1) without being a baseline document itself.
# The baseline-doc set was reduced to README.md only; all docs/*.md and
# README.md were removed. Only README.md still carries the baseline
# block; the other documents can no longer be compared.
BASELINE_DOCS = (
    "README.md",
)
PAIR_ONLY_DOCS = ()

# A native fault is disqualifying even when the process survives it: this
# machine has a fault mode that throws hundreds of COM exceptions through the
# UI Automation worker and then finishes with exit code 0.
_FAULT_PATTERNS = (
    re.compile(r"Windows fatal exception"),
    re.compile(r"Fatal Python error:"),
    re.compile(r"Thread 0x[0-9a-fA-F]+ \["),
    re.compile(r"Current thread 0x[0-9a-fA-F]+"),
)

# The outcome counters of a pytest summary line. The warning counter is
# deliberately absent: it is never published (rule 3).
_COUNTER_RE = re.compile(
    r"(\d+)\s+(passed|failed|errors?|skipped|xfailed|xpassed|deselected)"
)
_COUNTER_NAMES = {
    "passed": "passed",
    "failed": "failed",
    "error": "error",
    "errors": "error",
    "skipped": "skipped",
    "xfailed": "xfailed",
    "xpassed": "xpassed",
    "deselected": "deselected",
}
_DURATION_RE = re.compile(r"\bin\s+([0-9]+(?:\.[0-9]+)?)s")

# Rule patterns for the document check (see check_documents()).
_PAIR_RE = re.compile(r"(\d+)\s+passed,\s*(\d+)\s+skipped")
_COLLECTED_RE = re.compile(r"(\d+)\s+collected")
_DURATION_CLAIM_RE = re.compile(
    r"(?<![0-9A-Za-z_])~?[0-9]+(?:\.[0-9]+)?\s*(?:seconds|secs|sec|minutes|mins|min|s|m)\b"
)
_LIVE_FRACTION_RE = re.compile(r"[0-9]+\s*/\s*[0-9]+\s*live", re.IGNORECASE)
_MEASURED_RE = re.compile(
    r"Measured on CPython\s+(?P<py>[0-9]+(?:\.[0-9]+)*)\s*/\s*(?P<platform>[A-Za-z][A-Za-z0-9_-]*)"
)
_CI_PIN_RE = re.compile(r"CI pins Python\s+(?P<ci>[0-9]+(?:\.[0-9]+)*)")
_BADGE_RE = re.compile(r"img\.shields\.io/badge/([^\s)]+)")
_BADGE_PAIR_RE = re.compile(r"(\d+)\s+passed\D+?(\d+)\s+skipped")

MACHINE_QUALIFIERS = ("machine-specific", "reference machine")


class UsageError(Exception):
    """A problem with the invocation or with the files it points at."""


# --------------------------------------------------------------------------
# repository inspection
# --------------------------------------------------------------------------
def default_root():
    """The repository root: the parent of the directory holding this file."""
    return Path(__file__).resolve().parent.parent


def parse_makefile(path):
    """Return {target: [recipe lines]} for a Makefile.

    Only what this script needs: the recipe lines of each target, in order.
    """
    targets = {}
    current = None
    for raw in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not raw.strip() or raw.lstrip().startswith("#"):
            continue
        if raw.startswith("\t") or raw.startswith("    "):
            if current is not None:
                targets[current].append(raw.strip())
            continue
        match = re.match(r"^([A-Za-z0-9_.\-]+)\s*:(?!=)(.*)$", raw)
        if match:
            current = match.group(1)
            targets.setdefault(current, [])
            continue
        current = None
    return targets


def derive_argv(recipe_lines):
    """The argv of a target's command, with the interpreter pinned.

    The pytest command is preferred; a target whose recipe is something else
    (the failure demonstrations use one) falls back to its first real
    command. ``python`` is replaced by THIS interpreter, so the measurement
    is the measurement of the interpreter that is running.
    """
    if not recipe_lines:
        return None
    chosen = None
    for line in recipe_lines:
        if "pytest" in line:
            chosen = line
            break
    if chosen is None:
        for line in recipe_lines:
            if line.startswith("@echo"):
                continue
            chosen = line
            break
    if chosen is None:
        return None
    try:
        argv = shlex.split(chosen, posix=True)
    except ValueError:
        return None
    if not argv:
        return None
    head = os.path.basename(argv[0]).lower()
    if head in ("python", "python3", "py") or head.startswith("python3."):
        argv[0] = sys.executable
    return argv


def read_ci_python_version(root):
    """The Python version the CI workflow pins, read out of the workflow."""
    path = root / ".github" / "workflows" / "ci.yml"
    if not path.is_file():
        return None
    text = path.read_text(encoding="utf-8", errors="replace")
    match = re.search(
        r"python-version:\s*[\"']?([0-9]+(?:\.[0-9]+)*)[\"']?", text
    )
    return match.group(1) if match else None


def git_info(root):
    """commit / branch / dirty for the tree that was measured."""
    info = {"commit": None, "branch": None, "dirty": None}

    def run(args):
        try:
            proc = subprocess.run(
                ["git", "-C", str(root)] + args,
                capture_output=True,
                text=True,
                timeout=30,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if proc.returncode != 0:
            return None
        return proc.stdout.strip()

    commit = run(["rev-parse", "HEAD"])
    if commit:
        info["commit"] = commit
    branch = run(["rev-parse", "--abbrev-ref", "HEAD"])
    if branch:
        info["branch"] = branch
    status = run(["status", "--porcelain"])
    if status is not None:
        info["dirty"] = bool(status.strip())
    return info


def pytest_version():
    """The pytest version in use, or None."""
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", "--version"],
            capture_output=True,
            text=True,
            timeout=120,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    match = re.search(r"([0-9]+(?:\.[0-9]+)*)", proc.stdout or "")
    return match.group(1) if match else None


# --------------------------------------------------------------------------
# running and classifying a suite
# --------------------------------------------------------------------------
def parse_summary(text):
    """The pytest summary of an output, or (None, None).

    The LAST summary-looking line wins: pytest prints progress before it, and
    only the last line is the outcome of the run.
    """
    for line in reversed(text.splitlines()):
        stripped = line.strip()
        if " in " not in stripped:
            continue
        counts = {}
        for number, name in _COUNTER_RE.findall(stripped):
            counts[_COUNTER_NAMES[name]] = int(number)
        if not counts:
            continue
        duration = _DURATION_RE.search(stripped)
        if not duration:
            continue
        return counts, float(duration.group(1))
    return None, None


def fault_markers(text):
    """The native-fault markers present in an output."""
    found = []
    for pattern in _FAULT_PATTERNS:
        match = pattern.search(text)
        if match:
            found.append(match.group(0))
    return found


def classify(exit_code, output, summary):
    """Classify one attempt.

    COMPLETE requires all three of: exit code 0, a parseable summary, and no
    native-fault marker. The third condition is separate on purpose - a native
    fault that does not kill the process still produces a summary and an exit
    code of 0, and those numbers must not be published.
    """
    faults = fault_markers(output)
    if exit_code == 0 and summary and not faults:
        return "ok"
    if faults and exit_code != 0:
        return "crashed"
    if faults:
        return "faulted"
    if exit_code != 0:
        return "failed"
    return "incomplete"


def run_command(argv, cwd):
    """Run one command; return (exit_code, combined output, seconds)."""
    started = time.monotonic()
    try:
        proc = subprocess.run(
            argv,
            cwd=str(cwd),
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            errors="replace",
        )
        output = proc.stdout or ""
        code = proc.returncode
    except OSError as exc:
        return 127, "could not execute {}: {}".format(" ".join(argv), exc), 0.0
    return code, output, time.monotonic() - started


def tail(text, limit=2000):
    """The end of an output, for the attempt record."""
    if len(text) <= limit:
        return text
    return "...[truncated]...\n" + text[-limit:]


def outcome_collected(counts):
    """The collected count implied by a run's outcome counters."""
    total = 0
    for name in ("passed", "failed", "error", "skipped", "xfailed", "xpassed"):
        total += counts.get(name, 0)
    return total


def has_marker_expression(argv):
    """True when the pytest command carries a -m expression.

    Only the arguments AFTER the "pytest" token count: the interpreter's own
    "-m pytest" is not a marker expression.
    """
    try:
        start = argv.index("pytest") + 1
    except ValueError:
        start = 0
    for arg in argv[start:]:
        if arg == "-m" or arg.startswith("-m="):
            return True
    return False


def collect_only_argv(argv):
    """The same command, with --collect-only added."""
    out = []
    for arg in argv:
        if arg == "--collect-only":
            return argv
        out.append(arg)
    out.append("--collect-only")
    return out


def measure_collected(argv, cwd):
    """The collected count, measured independently with --collect-only."""
    code, output, _ = run_command(collect_only_argv(argv), cwd)
    if code != 0:
        return None, "collect-only run exited {}".format(code)
    match = re.search(r"([0-9]+)\s+tests?\s+collected", output)
    if not match:
        return None, "collect-only run printed no collected count"
    return int(match.group(1)), None


def measure_suite(name, argv, cwd, attempts_allowed):
    """Measure one suite, retrying in a FRESH process after a bad attempt.

    A fresh process per attempt is mandatory: the native faults this guards
    against do not reproduce in isolation, so re-running inside one process
    would prove nothing.
    """
    attempts = []
    result = None
    for index in range(1, attempts_allowed + 1):
        code, output, seconds = run_command(argv, cwd)
        counts, duration = parse_summary(output)
        status = classify(code, output, counts)
        attempt = {
            "suite": name,
            "attempt": index,
            "exit_code": code,
            "status": status,
            "seconds": round(seconds, 3),
            "fault_markers": fault_markers(output),
            "tail": tail(output),
        }
        attempts.append(attempt)
        result = {
            "suite": name,
            "command": argv,
            "exit_code": code,
            "status": status,
            "complete": status == "ok",
            "duration_seconds": round(seconds, 3),
            "fault_markers": attempt["fault_markers"],
        }
        if status == "ok":
            result["passed"] = counts.get("passed", 0)
            result["skipped"] = counts.get("skipped", 0)
            result["collected"] = outcome_collected(counts)
            result["collected_source"] = "outcome_sum"
            result["duration_seconds"] = round(duration, 3) if duration else result["duration_seconds"]
            if has_marker_expression(argv) or counts.get("deselected", 0):
                # The outcome counters are not the collected count when
                # something was deselected, so measure it instead - and
                # publish nothing if the two ways of measuring disagree.
                measured, error = measure_collected(argv, cwd)
                if measured is None:
                    result["complete"] = False
                    result["status"] = "inconsistent"
                    result["collected_error"] = error
                    result.pop("collected", None)
                elif measured != outcome_collected(counts):
                    result["complete"] = False
                    result["status"] = "inconsistent"
                    result["collected_error"] = (
                        "the outcome counters sum to {} but --collect-only "
                        "collected {}".format(outcome_collected(counts), measured)
                    )
                    result.pop("collected", None)
                else:
                    result["collected"] = measured
                    result["collected_source"] = "collect_only"
            return result, attempts
    return result, attempts


# --------------------------------------------------------------------------
# the measurement
# --------------------------------------------------------------------------
def measure(root, makefile, attempts_allowed, only=None):
    """Measure the suites named by the Makefile. Returns the JSON payload."""
    targets = parse_makefile(makefile)
    for required in ("test", "test-acceptance"):
        if required not in targets:
            raise UsageError(
                "the Makefile has no '{}' target; refusing to invent a command "
                "the Makefile does not run".format(required)
            )

    payload = {
        "schema_version": SCHEMA_VERSION,
        "status": "ok",
        "complete": True,
        "measured_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "git": git_info(root),
        "python_version": platform.python_version(),
        "platform": platform.system(),
        "platform_release": platform.release(),
        "pytest_version": pytest_version(),
        "ci_python_version": read_ci_python_version(root),
        "suites": {},
        "attempts": [],
    }

    plan = [("backend", "test"), ("acceptance", "test-acceptance")]
    if only == "acceptance":
        plan = [("acceptance", "test-acceptance")]

    for suite_name, target in plan:
        argv = derive_argv(targets[target])
        if not argv:
            raise UsageError(
                "the Makefile target '{}' has no runnable command".format(target)
            )
        result, attempts = measure_suite(suite_name, argv, root, attempts_allowed)
        payload["suites"][suite_name] = result
        payload["attempts"].extend(attempts)
        if not result.get("complete"):
            payload["status"] = result.get("status", "incomplete")
            payload["complete"] = False
            break

    if only == "acceptance":
        payload["scope"] = "acceptance"
        payload["backend_measured"] = False
        payload["note"] = (
            "only the acceptance suite was measured; this run cannot verify "
            "the backend baseline"
        )
    else:
        payload["scope"] = "backend+acceptance"
        payload["backend_measured"] = True

    if payload["complete"] and "backend" in payload["suites"]:
        backend = payload["suites"]["backend"]
        payload["passed"] = backend["passed"]
        payload["skipped"] = backend["skipped"]
        payload["collected"] = backend["collected"]
        payload["collected_source"] = backend["collected_source"]
        payload["duration_seconds"] = backend["duration_seconds"]
    return payload


def load_measurement(path):
    """Load a previously written measurement JSON."""
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        raise UsageError("cannot read the measurement file {}: {}".format(path, exc))
    if not isinstance(data, dict) or "suites" not in data:
        raise UsageError("{} is not a measurement produced by this script".format(path))
    return data


# --------------------------------------------------------------------------
# the document check (rules C1-C7)
# --------------------------------------------------------------------------
def resolve_docs(root, given):
    """Resolve the document list; relative paths may be relative to the root."""
    if not given:
        names = list(BASELINE_DOCS) + list(PAIR_ONLY_DOCS)
    else:
        names = list(given)
    resolved = []
    for name in names:
        candidate = Path(name)
        if not candidate.is_absolute():
            from_cwd = Path.cwd() / candidate
            if from_cwd.exists():
                candidate = from_cwd
            else:
                candidate = root / candidate
        resolved.append(candidate)
    return resolved


def read_docs(paths):
    """{path: text} for the documents that exist; missing ones are an error."""
    texts = {}
    for path in paths:
        if not path.is_file():
            raise UsageError("document not found: {}".format(path))
        texts[path] = path.read_text(encoding="utf-8", errors="replace")
    return texts


def line_of(text, index):
    """The 1-based line number of a character offset."""
    return text.count("\n", 0, index) + 1


def measured_pairs(measurement):
    """The measured (passed, skipped) pairs, by suite."""
    pairs = {}
    for name, suite in measurement.get("suites", {}).items():
        if suite.get("complete"):
            pairs[name] = (suite.get("passed"), suite.get("skipped"))
    return pairs


def suite_of_line(line):
    """Which suite a quoted pair belongs to, from the line's own wording."""
    lowered = line.lower()
    if "acceptance" in lowered:
        return "acceptance"
    if "backend" in lowered or "full" in lowered:
        return "backend"
    return None


def stated_interpreter(texts, baseline_paths):
    """The (path, python version, platform) the documents say they name."""
    stated = []
    for path in baseline_paths:
        match = _MEASURED_RE.search(texts[path])
        if match is not None:
            stated.append((path, match.group("py"), match.group("platform").strip()))
    return stated


def check_documents(texts, measurement, ci_version, paths, root):
    """Apply rules C1-C7. Returns (problems, interpreter_mismatch)."""
    problems = []
    baseline_paths = []
    for path in paths:
        try:
            relative = path.resolve().relative_to(root).as_posix()
        except ValueError:
            relative = path.name
        if relative in BASELINE_DOCS:
            baseline_paths.append(path)
    pairs = measured_pairs(measurement)
    backend_pair = pairs.get("backend")
    acceptance_pair = pairs.get("acceptance")
    all_pairs = set(pairs.values())

    # C1 - every quoted pair is a measured pair.
    for path, text in texts.items():
        for match in _PAIR_RE.finditer(text):
            pair = (int(match.group(1)), int(match.group(2)))
            line = line_of(text, match.start())
            source = text.splitlines()[line - 1]
            if suite_of_line(source) == "acceptance":
                allowed = acceptance_pair
            elif suite_of_line(source) == "backend":
                allowed = backend_pair
            else:
                allowed = None
            if allowed is None:
                if pair not in all_pairs:
                    problems.append(
                        "{}:{}: quoted pair '{}' is not a measured pair".format(
                            path, line, "{} passed, {} skipped".format(*pair)
                        )
                    )
            elif pair != allowed:
                problems.append(
                    "{}:{}: quoted pair '{}' does not match the measured "
                    "{} pair '{}'".format(
                        path,
                        line,
                        "{} passed, {} skipped".format(*pair),
                        "acceptance" if allowed is acceptance_pair else "backend",
                        "{} passed, {} skipped".format(*allowed),
                    )
                )

    # C2 - each baseline document states both measured pairs.
    for path in baseline_paths:
        text = texts[path]
        for name, pair in (("backend", backend_pair), ("acceptance", acceptance_pair)):
            if pair is None:
                continue
            quoted = "{} passed, {} skipped".format(*pair)
            if quoted not in text:
                problems.append(
                    "{}: does not state the measured {} pair '{}'".format(
                        path, name, quoted
                    )
                )

    # C3 - the README badge agrees with the README prose.
    readme = None
    for path, text in texts.items():
        if path.name == "README.md":
            readme = text
    if readme is not None:
        badge = _BADGE_RE.search(readme)
        prose = None
        for line in readme.splitlines():
            if "Full backend suite" in line:
                prose = _PAIR_RE.search(line)
                break
        if badge is None:
            problems.append("README.md: no shields.io badge found")
        elif prose is None:
            problems.append(
                "README.md: no 'Full backend suite' line to compare the badge with"
            )
        else:
            decoded = unquote(badge.group(1))
            badge_pair = _BADGE_PAIR_RE.search(decoded)
            if badge_pair is None:
                problems.append(
                    "README.md: the badge carries no 'N passed / M skipped' pair"
                )
            else:
                badge_value = (int(badge_pair.group(1)), int(badge_pair.group(2)))
                prose_value = (int(prose.group(1)), int(prose.group(2)))
                if badge_value != prose_value:
                    problems.append(
                        "README.md: the badge says '{}' but the prose says '{}'".format(
                            "{} passed / {} skipped".format(*badge_value),
                            "{} passed, {} skipped".format(*prose_value),
                        )
                    )

    # C4 - the interpreter, the platform and the CI pin are stated.
    stated_versions = []
    for path in baseline_paths:
        text = texts[path]
        measured = _MEASURED_RE.search(text)
        if measured is None:
            problems.append(
                "{}: does not state the interpreter and platform the baseline "
                "was measured on".format(path)
            )
        else:
            stated_versions.append((path, measured.group("py"), measured.group("platform").strip()))
        pin = _CI_PIN_RE.search(text)
        if ci_version is None:
            problems.append(
                "{}: the CI Python version could not be read out of "
                ".github/workflows/ci.yml".format(path)
            )
        elif pin is None:
            problems.append(
                "{}: does not state which Python version CI pins".format(path)
            )
        elif pin.group("ci") != ci_version:
            problems.append(
                "{}: states that CI pins Python {} but the workflow pins "
                "{}".format(path, pin.group("ci"), ci_version)
            )

    # C5 - every duration claim carries a machine qualifier.
    for path in baseline_paths:
        text = texts[path]
        for line_number, line in enumerate(text.splitlines(), start=1):
            if not _DURATION_CLAIM_RE.search(line):
                continue
            lowered = line.lower()
            if not any(qualifier in lowered for qualifier in MACHINE_QUALIFIERS):
                problems.append(
                    "{}:{}: duration claim '{}' is not marked as "
                    "machine-specific".format(path, line_number, line.strip())
                )

    # C6 - any collected count equals the measured collected count.
    measured_collected = measurement.get("collected")
    for path in baseline_paths:
        text = texts[path]
        for match in _COLLECTED_RE.finditer(text):
            if measured_collected is None:
                continue
            if int(match.group(1)) != measured_collected:
                problems.append(
                    "{}:{}: states '{}' collected but the measurement collected "
                    "{}".format(
                        path,
                        line_of(text, match.start()),
                        match.group(1),
                        measured_collected,
                    )
                )

    # C7 - no line claims an N/N live result.
    for path in baseline_paths:
        text = texts[path]
        for line_number, line in enumerate(text.splitlines(), start=1):
            if _LIVE_FRACTION_RE.search(line):
                problems.append(
                    "{}:{}: claims a live result ('{}') that no executed run in "
                    "this repository produced".format(
                        path, line_number, line.strip()
                    )
                )

    mismatch = None
    for path, py, plat in stated_versions:
        if py != measurement.get("python_version") or plat.lower() != str(
            measurement.get("platform", "")
        ).lower():
            mismatch = (path, py, plat)
            break
    return problems, mismatch


# --------------------------------------------------------------------------
# CLI
# --------------------------------------------------------------------------
def parse_args(argv):
    parser = argparse.ArgumentParser(
        description=(
            "Measure the Sythion backend test baseline and check the documents "
            "that quote it."
        ),
        epilog=(
            "exit codes: 0 = measured / the documents agree; 1 = --check mode, "
            "the documents disagree (stale); 2 = no complete measurement "
            "(inconclusive); 3 = usage error; 4 = --check mode, the running "
            "interpreter/platform differs from the one the documents name "
            "(inconclusive)."
        ),
    )
    parser.add_argument(
        "--root",
        default=None,
        help="repository root (default: the parent of this script's directory)",
    )
    parser.add_argument(
        "--makefile",
        default=None,
        help="the Makefile to read the 'test'/'test-acceptance' recipes from",
    )
    parser.add_argument(
        "--attempts",
        type=int,
        default=3,
        help="attempts per suite before giving up (default: 3)",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="measure only the acceptance suite (cannot verify the backend baseline)",
    )
    parser.add_argument(
        "--check",
        action="store_true",
        help="compare the documents against the measurement (rules C1-C7)",
    )
    parser.add_argument(
        "--check-collected",
        action="store_true",
        help="cheap drift check: --collect-only versus the documents' collected count",
    )
    parser.add_argument(
        "--measured",
        default=None,
        help="use a previously written measurement JSON instead of measuring again",
    )
    parser.add_argument(
        "--docs",
        nargs="+",
        default=None,
        help="override the list of documents to check",
    )
    parser.add_argument(
        "--out",
        default=None,
        help="also write the measurement JSON to this file",
    )
    return parser.parse_args(argv)


def main(argv=None):
    args = parse_args(argv)
    try:
        root = Path(args.root).resolve() if args.root else default_root()
        if not root.is_dir():
            raise UsageError("repository root not found: {}".format(root))
        makefile = Path(args.makefile) if args.makefile else root / "Makefile"
        if not makefile.is_file():
            raise UsageError("Makefile not found: {}".format(makefile))
        if args.attempts < 1:
            raise UsageError("--attempts must be at least 1")

        if args.measured:
            measurement = load_measurement(args.measured)
        else:
            only = "acceptance" if args.fast else None
            measurement = measure(root, makefile, args.attempts, only=only)

        if args.out:
            Path(args.out).write_text(
                json.dumps(measurement, indent=2) + "\n", encoding="utf-8"
            )

        if not (args.check or args.check_collected):
            print(json.dumps(measurement, indent=2))
            return EXIT_OK if measurement.get("complete") else EXIT_INCONCLUSIVE

        # ---- check modes -------------------------------------------------
        if not measurement.get("complete"):
            print(
                "the measurement did not complete (status '{}'): the documents "
                "are not implicated, this check is INCONCLUSIVE".format(
                    measurement.get("status")
                ),
                file=sys.stderr,
            )
            print(json.dumps(measurement, indent=2))
            return EXIT_INCONCLUSIVE

        paths = resolve_docs(root, args.docs)
        texts = read_docs(paths)
        ci_version = measurement.get("ci_python_version") or read_ci_python_version(root)

        if args.check_collected:
            backend = measurement.get("suites", {}).get("backend")
            if backend is None:
                print(
                    "the measurement has no backend suite; run a full measurement "
                    "first",
                    file=sys.stderr,
                )
                return EXIT_INCONCLUSIVE
            baseline_paths = []
            for path in paths:
                try:
                    relative = path.resolve().relative_to(root).as_posix()
                except ValueError:
                    relative = path.name
                if relative in BASELINE_DOCS:
                    baseline_paths.append(path)
            mismatch = None
            for path, py, plat in stated_interpreter(texts, baseline_paths):
                if py != measurement.get("python_version") or plat.lower() != str(
                    measurement.get("platform", "")
                ).lower():
                    mismatch = (path, py, plat)
                    break
            if mismatch is not None:
                path, py, plat = mismatch
                print(
                    "{} states the baseline was measured on CPython {} / {}, but "
                    "this run is on CPython {} / {}. The documents were measured "
                    "elsewhere; this check is INCONCLUSIVE.".format(
                        path,
                        py,
                        plat,
                        measurement.get("python_version"),
                        measurement.get("platform"),
                    ),
                    file=sys.stderr,
                )
                return EXIT_ENVIRONMENT
            measured_collected, error = measure_collected(backend["command"], root)
            if measured_collected is None:
                print("could not measure the collected count: {}".format(error), file=sys.stderr)
                return EXIT_INCONCLUSIVE
            problems = []
            stated = measurement.get("collected")
            if stated is not None and stated != measured_collected:
                problems.append(
                    "the measurement collected {} but the independent "
                    "--collect-only run collected {}".format(stated, measured_collected)
                )
            for path in baseline_paths:
                text = texts[path]
                for match in _COLLECTED_RE.finditer(text):
                    if int(match.group(1)) != measured_collected:
                        problems.append(
                            "{}:{}: states '{}' collected but the measurement "
                            "collected {}".format(
                                path,
                                line_of(text, match.start()),
                                match.group(1),
                                measured_collected,
                            )
                        )
            if problems:
                print("the documents are STALE:", file=sys.stderr)
                for problem in problems:
                    print("  " + problem, file=sys.stderr)
                return EXIT_STALE
            print(
                "documents agree with the measurement: collected {}".format(
                    measured_collected
                )
            )
            return EXIT_OK

        problems, mismatch = check_documents(
            texts, measurement, ci_version, paths, root
        )
        if mismatch is not None:
            path, py, plat = mismatch
            print(
                "{} states the baseline was measured on CPython {} / {}, but this "
                "run is on CPython {} / {}. The documents were measured "
                "elsewhere; this check is INCONCLUSIVE.".format(
                    path,
                    py,
                    plat,
                    measurement.get("python_version"),
                    measurement.get("platform"),
                ),
                file=sys.stderr,
            )
            return EXIT_ENVIRONMENT
        if problems:
            print("the documents are STALE:", file=sys.stderr)
            for problem in problems:
                print("  " + problem, file=sys.stderr)
            return EXIT_STALE
        print(
            "the documents agree with the measurement ({} documents checked)".format(
                len(paths)
            )
        )
        return EXIT_OK
    except UsageError as exc:
        print("usage error: {}".format(exc), file=sys.stderr)
        return EXIT_USAGE


if __name__ == "__main__":
    sys.exit(main())
