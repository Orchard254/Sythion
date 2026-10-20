# Report: implement - 003 - attempt 1

Item: **003 - Documentation states the measured baseline and drops deleted
modules**. Branch: `orchestra/003-docs-baseline`.

## What I did

**The branch.** Cut from `master` (at `9770fb9`), rebased onto `master`
(`7fc7a4a`) at the end of the run:

```
$ git log --oneline master..HEAD
6d346f1 chore(orchestra): item 003 report - the final commit ids and the diff stat
346066e chore(orchestra): the item 003 implementation report
485a797 docs: state the measured test baseline and drop the deleted modules
```

(The top commit is this report's own last edit, so its hash necessarily
changes with every edit to this file; the two substantive commits are
`485a797` - the documents and the two scripts - and `346066e`, which adds
this report.)

The work happened in a worktree **outside** the repository
(`C:\Users\orcha\AppData\Local\Temp\opencode\wt-003`), so the main checkout
was never moved off `master`.

**The diff against `master`** - exactly eleven files, and nothing else:

```
$ git diff --stat master...HEAD
 .orchestra/reports/003-implement-1.md | 926 +++++++++++++++++++++++++++++++
 README.md                             |  17 +-
 TESTING_GUIDE.md                      |  13 +-
 backend/services/README.md            |   7 +-
 docs/CODEBASE_BOUNDARIES.md           |  14 +-
 docs/OPERATIONS.md                    |  36 +--
 docs/PROJECT_STATUS.md                |  19 +-
 docs/ROADMAP.md                       |  15 +-
 docs/TESTING.md                       | 172 +++++++++++++++--------------
 scripts/check_doc_paths.py            | 486 +++++++++++++++++
 scripts/measure_test_baseline.py      | 986 ++++++++++++++++++++++++++++++++++
 11 files changed, 2601 insertions(+), 90 deletions(-)
```

The branch merges into `master` without a conflict
(`git merge-tree --write-tree master orchestra/003-docs-baseline` -> a tree,
no conflict markers).

**The files I wrote** (nothing else):

| File | Kind |
|------|------|
| `scripts/measure_test_baseline.py` | new (the measurement) |
| `scripts/check_doc_paths.py` | new (the path check) |
| `README.md` | edited |
| `TESTING_GUIDE.md` | edited |
| `backend/services/README.md` | edited |
| `docs/CODEBASE_BOUNDARIES.md` | edited |
| `docs/OPERATIONS.md` | edited |
| `docs/PROJECT_STATUS.md` | edited |
| `docs/ROADMAP.md` | edited |
| `docs/TESTING.md` | rewritten |
| `.orchestra/reports/003-implement-1.md` | this report |

No `backend/**` file, no `Makefile`, no `pytest.ini`, no
`backend/tests/conftest.py`, no `.github/workflows/ci.yml`, no
`backend/requirements*.txt` was touched. The two scripts read the Makefile
and the workflow; they do not change them.

**The tree the measurement was taken on.** The measurement could NOT be taken
on my branch: the full backend suite includes
`backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`,
which - until item 007 lands - writes to and then deletes the developer's
REAL Windows Credential Manager entry `Sythion (Synth)/ollama_api_key`.
The plan's retry design makes that worse (up to 3 full-suite attempts per
suite). So I built a **measurement tree outside the repository**:

```
$ git -C C:\Sythion\sythion worktree add -b orchestra/003-measure-merged \
      C:\Users\orcha\AppData\Local\Temp\opencode\wt-003-merged master
$ cd wt-003-merged
$ git merge --no-edit orchestra/007-no-real-vault-writes   # d75ebbb
$ git merge --no-edit orchestra/006-readiness-test-isolation # bec0531
$ git merge --no-edit orchestra/002-test-selection-truth    # 8a4f85b (one conflict, resolved below)
```

One conflict had to be resolved by hand: items 002 and 006 both edit
`backend/tests/conftest.py` (002 rewrites it, 006 appends the `backend/.env`
isolation block). The resolution keeps both - 002's marker machinery and
006's isolation block - and is a pure concatenation (no line of either was
altered).

That tree is therefore **master + item 002 + item 006 + item 007** - the
tree item 003 will land in - and it carries item 007's vault guard.

**What I did NOT do.** I never ran a full-suite measurement on a tree
without item 007's guard, and I never deliberately ran the offending test
against the real vault.

## Evidence

### The measurement script exists, runs the Makefile's commands, and publishes no count it did not earn

```
$ python scripts/measure_test_baseline.py --help
usage: measure_test_baseline.py [-h] [--root ROOT] [--makefile MAKEFILE]
                                [--attempts ATTEMPTS] [--fast] [--check]
                                [--check-collected] [--measured MEASURED]
                                [--docs DOCS [DOCS ...]] [--out OUT]
...
exit codes: 0 = measured / the documents agree; 1 = --check mode, the
documents disagree (stale); 2 = no complete measurement (inconclusive); 3 =
usage error; 4 = --check mode, the running interpreter/platform differs from
the one the documents name (inconclusive).
```

The commands come out of the Makefile, not out of the script:

```
$ python -c "import importlib.util; ..."   # derive_argv on the merged Makefile
test           ['...python.exe', '-m', 'pytest', 'backend/tests', '-q']
test-acceptance ['...python.exe', '-m', 'pytest', 'backend/tests/acceptance', '-q']
```

`make` is **not installed** on this machine (`Get-Command make` returns
nothing), so the script executes the Makefile's recipe lines directly with
this interpreter - the recipe, verbatim, not a re-typed approximation of it.

The CI Python version is read out of the workflow, never typed:
`.github/workflows/ci.yml:19` -> `python-version: "3.11"` -> the JSON
carries `"ci_python_version": "3.11"`.

The script hard-codes no count (reproduction row R1 below prints nothing),
and the warning count never reaches the JSON: the summary parser's counter
regex does not even include the word "warnings".

### The path check resolves every module the four documents name

```
$ python scripts/check_doc_paths.py
missing paths: 0
exit=0
```

### The documents agree with the measurement

```
$ python scripts/measure_test_baseline.py \
      --measured C:/Users/orcha/AppData/Local/Temp/opencode/measurement-003.stdout.json \
      --check --root .
the documents agree with the measurement (6 documents checked)
exit=0
```

### The check has teeth (the same script, a measurement from another interpreter)

```
$ python scripts/measure_test_baseline.py --measured .../synthetic-py311.json --check --root .
C:\Users\orcha\AppData\Local\Temp\opencode\wt-003\README.md states the baseline was measured on CPython 3.14.7 / Windows, but this run is on CPython 3.11 / Windows. The documents were measured elsewhere; this check is INCONCLUSIVE.
exit=4
```

And, before the documents were rewritten, the same check found every stale
claim in the repository (this is the transcript of the check against the
*pre-edit* documents, with a synthetic measurement - it is the proof that
rules C1-C7 are not vacuous):

```
the documents are STALE:
  ...README.md:103: quoted pair '692 passed, 32 skipped' does not match the measured backend pair '1564 passed, 45 skipped'
  ...README.md:104: quoted pair '196 passed, 2 skipped' does not match the measured acceptance pair '191 passed, 2 skipped'
  ...docs/TESTING.md:30: quoted pair '692 passed, 32 skipped' is not a measured pair
  ...docs/TESTING.md:39: duration claim '| `make test` | Full backend suite (~55s) |' is not marked as machine-specific
  ...docs/TESTING.md:96: claims a live result ('Currently 6/6 live.') that no executed run in this repository produced
  ...docs/OPERATIONS.md:172: claims a live result ('- live smoke script: documented as **6/6 live** in `TESTING.md`.') that no executed run in this repository produced
  README.md: the badge says '683 passed / 2 skipped' but the prose says '692 passed, 32 skipped'
  ...README.md: does not state the interpreter and platform the baseline was measured on
  ...
exit=1
```

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|-----------|--------|----------|
| 1 | `scripts/measure_test_baseline.py` exists, runs the backend suite and the acceptance suite from the repository root, prints machine-readable JSON with `passed`, `skipped`, `collected`, `duration_seconds`, `python_version`, `platform`; hard-codes no counts; publishes no warning count; the count keys are absent unless the run COMPLETED; exits 0 / 2 / 3 | **met** | The JSON in "The measurement" below has every required key; R1 prints nothing; the JSON has no warning key (the summary parser does not even match the word "warnings"); D1 and D2 show the count keys absent with exit 2; the exit-3 path is exercised below |
| 2 | The baseline quoted in README.md (badge and prose), docs/TESTING.md, docs/PROJECT_STATUS.md, docs/ROADMAP.md and docs/OPERATIONS.md equals the numbers printed by the last executed run, and each document states the interpreter, the OS and that CI pins Python 3.11 | **met** | The numbers are copied out of the measurement JSON below (1568 / 45 / 1613 and 191 / 2); each of the five documents carries the "Measured on CPython 3.14.7 / Windows ... CI pins Python 3.11" block; `ci_python_version` is read from `.github/workflows/ci.yml:19`; `--check` exits 0 |
| 3 | The README badge and the README prose state the same numbers | **met** | Rule C3 of `--check` exits 0; badge and prose side by side in "The document edits" |
| 4 | The runtime estimates "~55s", "~25s" and "~10s" in docs/TESTING.md are replaced by measured values or labelled machine-specific | **met** | R7 prints nothing; the table carries `377.6 s (reference machine)` and `19.9 s (reference machine)` (measured) and `machine-specific (not measured here)` for the targets that were not measured; rule C5 exits 0 |
| 5 | The unverified live claim "Currently 6/6 live" is removed or re-labelled as a manual check that no executed run in this repository produced | **met** | R6 prints nothing; rule C7 exits 0; the replaced sentences are quoted in "The document edits" |
| 6 | `backend/services/response_strategy.py` and `backend/services/speech_act_classifier.py` appear in none of the four documents, and `scripts/check_doc_paths.py` exits 0 with zero missing paths; the same check must fail on a document naming a module that does not exist | **met** | R5 prints nothing; `python scripts/check_doc_paths.py` -> `missing paths: 0`, exit 0; the fail path is demonstrated in "The path check's two paths" (exit 1, naming the path and its line) |
| 7 | docs/TESTING.md's "Common Test Commands" table, "Pytest Markers" table and "CI Pipeline" section describe the merged Makefile and ci.yml | **met** | The side-by-side table with `file:line` for every claim is in "The document edits" |

The exit-3 (usage error) path:

```
$ python scripts/measure_test_baseline.py --makefile C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/no_such_makefile
usage error: Makefile not found: C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/no_such_makefile
exit=3
```

## The reproduction rows R1-R8

"On `master`" = the base commit this branch was cut from (`9770fb9`, before
the item 002/006/007 merges); "on the branch" = the tip of
`orchestra/003-docs-baseline` (the three commits listed above).

**R1** - `git grep -nE "1564|1609|692|683|659|196" -- scripts/measure_test_baseline.py`

* master: the file does not exist (`git grep` -> `fatal: pathspec ... did not match any files`).
* branch: **no output** (exit 1 = no match).

**R2** - `python scripts/check_doc_paths.py`

* master: the file does not exist.
* branch: **exit 0**, output `missing paths: 0`.

**R3** - `python scripts/measure_test_baseline.py --check`

* master: the file does not exist.
* branch: **exit 0** on the post-merge tree (the tree the documents describe);
  see "The measurement" for the transcript and for what the command does on
  the un-merged branch.

**R4** - `git grep -nE "\b(692|683|659)\b|196 passed" -- README.md docs/ TESTING_GUIDE.md`

* master: **15 matches** (NOT the 14 the plan claimed - the README
  contributes three lines: the badge at `:6` and the prose at `:103` and
  `:104`):

```
master:README.md:6:[![Backend tests](https://img.shields.io/badge/backend%20tests-683%20passed%20%2F%202%20skipped-green)]()
master:README.md:103:- Full backend suite: **692 passed, 32 skipped** (32 skipped = 27 power/network real-system tests + 3 volume system-audio tests + 2 symlink privilege tests)
master:README.md:104:- Acceptance suite: **196 passed, 2 skipped**
master:TESTING_GUIDE.md:21:- Full backend suite: **692 passed, 32 skipped** (32 skipped = 27 power/network real-system tests + 3 volume system-audio tests + 2 symlink privilege tests)
master:TESTING_GUIDE.md:22:- Acceptance suite: **196 passed, 2 skipped**
master:docs/OPERATIONS.md:159:- full backend suite: **692 passed, 32 skipped**;
master:docs/OPERATIONS.md:160:- acceptance suite: **196 passed, 2 skipped**;
master:docs/PROJECT_STATUS.md:41:- Full backend suite: **692 passed, 32 skipped**
master:docs/PROJECT_STATUS.md:42:- Acceptance suite: **196 passed, 2 skipped**
master:docs/ROADMAP.md:24:- full backend suite: **659 passed, 32 skipped**;
master:docs/ROADMAP.md:25:- acceptance suite: **196 passed, 2 skipped**;
master:docs/TESTING.md:7:make test              # Full backend suite (692 passed, 32 skipped)
master:docs/TESTING.md:9:make test-acceptance   # Acceptance gates only (196 passed, 2 skipped)
master:docs/TESTING.md:30:- **Current verified baseline**: **692 passed, 32 skipped** for
master:docs/TESTING.md:31:  the full backend suite, and **196 passed, 2 skipped** for the acceptance
```

* branch: **no output** (exit 1 = no match).

**R5** - `git grep -niE "response[-_]strategy|speech[-_]act[-_]classifier" -- docs/CODEBASE_BOUNDARIES.md backend/services/README.md docs/ROADMAP.md docs/PROJECT_STATUS.md`

* master: **8 matches**.
* branch: **no output** (exit 1 = no match).

**R6** - `git grep -n "Currently 6/6 live" -- docs/`

* master: **1 match** (`docs/TESTING.md:96`).
* branch: **no output** (exit 1 = no match).

**R7** - `git grep -nE "~55s|~25s|~10s" -- docs/TESTING.md`

* master: **6 matches**.
* branch: **no output** (exit 1 = no match).

**R8** - `git grep -nE "backend/tools/web_tools.py" -- docs/CODEBASE_BOUNDARIES.md`

* master: **1 match**.
* branch: **no output** (exit 1 = no match).

## The measurement

**The tree that was measured**: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-003-merged`
at commit `8a4f85b` - `master` (6281659) with item 007, item 006 and item
002 merged in. It does **not** contain item 003's own commit (the documents
were written after the measurement, from its output), and it does contain
items **002, 006 and 007**.

**The command**:

```
$ python scripts/measure_test_baseline.py \
      --root C:/Users/orcha/AppData/Local/Temp/opencode/wt-003-merged \
      --attempts 3 \
      --out C:/Users/orcha/AppData/Local/Temp/opencode/measurement-003.json
```

**The JSON** (the three `tail` fields are elided; the complete file is at
`C:\Users\orcha\AppData\Local\Temp\opencode\measurement-003.stdout.json`):

```json
{
  "schema_version": 1,
  "status": "ok",
  "complete": true,
  "measured_at": "2026-10-20T20:09:52+00:00",
  "git": {
    "commit": "8a4f85b240365a0a5bd9e1ed87b09c56f56b19b7",
    "branch": "orchestra/003-measure-merged",
    "dirty": false
  },
  "python_version": "3.14.7",
  "platform": "Windows",
  "platform_release": "11",
  "pytest_version": "8.4.2",
  "ci_python_version": "3.11",
  "suites": {
    "backend": {
      "suite": "backend",
      "command": [
        "C:\\Users\\orcha\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
        "-m", "pytest", "backend/tests", "-q"
      ],
      "exit_code": 0,
      "status": "ok",
      "complete": true,
      "duration_seconds": 377.56,
      "fault_markers": [],
      "passed": 1568,
      "skipped": 45,
      "collected": 1613,
      "collected_source": "outcome_sum"
    },
    "acceptance": {
      "suite": "acceptance",
      "command": [
        "C:\\Users\\orcha\\AppData\\Local\\Programs\\Python\\Python314\\python.exe",
        "-m", "pytest", "backend/tests/acceptance", "-q"
      ],
      "exit_code": 0,
      "status": "ok",
      "complete": true,
      "duration_seconds": 19.94,
      "fault_markers": [],
      "passed": 191,
      "skipped": 2,
      "collected": 193,
      "collected_source": "outcome_sum"
    }
  },
  "attempts": [
    {
      "suite": "backend",
      "attempt": 1,
      "exit_code": 3221225477,
      "status": "crashed",
      "seconds": 213.289,
      "fault_markers": ["Windows fatal exception", "Thread 0x00008e00 [", "Current thread 0x0000cb68"],
      "tail": "<elided: 2018 characters>"
    },
    {
      "suite": "backend",
      "attempt": 2,
      "exit_code": 0,
      "status": "ok",
      "seconds": 385.702,
      "fault_markers": [],
      "tail": "<elided: 2018 characters>"
    },
    {
      "suite": "acceptance",
      "attempt": 1,
      "exit_code": 0,
      "status": "ok",
      "seconds": 24.028,
      "fault_markers": [],
      "tail": "<elided: 2018 characters>"
    }
  ],
  "scope": "backend+acceptance",
  "backend_measured": true,
  "passed": 1568,
  "skipped": 45,
  "collected": 1613,
  "collected_source": "outcome_sum",
  "duration_seconds": 377.56
}
```

**Every attempt, including the crashed one.** The backend suite's first
attempt died with `3221225477` (= `0xC0000005`, an access violation) and
carried the markers `Windows fatal exception`, `Thread 0x... [` and
`Current thread 0x...`. It published nothing; the script retried in a FRESH
process and the second attempt completed. This is the fault mode the plan
describes, caught by the guard rather than published as a baseline.

**An earlier measurement run (the same tree, a pre-fix script)** - recorded
because every attempt has to be recorded. The first run produced the SAME
numbers (1568 / 45 / 1613 and 191 / 2) and is the run whose
`collected_source` was `collect_only`: the script then mistook the
interpreter's own `-m` (in `python -m pytest`) for a pytest marker
expression, so it measured the collected count a second time with
`--collect-only` instead of summing the outcome counters. That bug is
fixed (`has_marker_expression` now only looks at the arguments after the
`pytest` token), and the measurement above was re-taken with the fixed
script. The first run's acceptance suite also failed its first attempt:

```
ERROR backend/tests/acceptance/test_gui_fixture.py::test_invoke_flips_fixture_status
190 passed, 2 skipped, 16893 warnings, 1 error in 18.14s
```

(the `test_gui_fixture.py` error is the same live-desktop flake that
implementer 001 reported; the retry in a fresh process produced
`191 passed, 2 skipped`).

**The number in the documents is the number the script printed**: 1568
passed, 45 skipped, 1613 collected for the backend suite and 191 passed,
2 skipped for the acceptance suite, on CPython 3.14.7 / Windows, measured on
2026-10-20. It is **not** 1611 (the current `master`) and it is **not** a
typed 1613: it is what the script measured on the tree that carries item
006's two new tests. If items 002/006/007 change before they merge, the
number moves - and `--check` / `--check-collected` will say so.

**R3, the full `--check`, on the post-merge tree.** The measurement tree
was given item 003's documents and scripts (commit `27a6f87`, "overlay"),
so it is the tree the documents describe. The check re-measures and
compares:

```
$ cd wt-003-merged && python scripts/measure_test_baseline.py --check
the documents agree with the measurement (6 documents checked)
EXITCODE=0
```

(the exit code was captured with `echo EXITCODE=%ERRORLEVEL%` around the
run; the stderr file was empty). It was run twice; the second run's
measurement was `1568 passed, 45 skipped, 1613 collected` again, with the
backend suite completing on its first attempt.

**The cheap drift detector** (`--check-collected`, a `--collect-only` pass
instead of a full run - about 100 s here):

```
$ cd wt-003-merged && python scripts/measure_test_baseline.py --check-collected \
      --measured C:/Users/orcha/AppData/Local/Temp/opencode/measurement-003.stdout.json
documents agree with the measurement: collected 1613
EXITCODE=0
```

**On the un-merged branch** the same command is honest about the
difference: the branch does not carry item 006's two tests, so the
documents (which quote the post-merge numbers) do not match a measurement
of the branch itself. That is the drift detector working, not a failure.

## The two failure demonstrations (D1, D2)

Both sandbox Makefiles live **outside** the repository, in
`C:\Users\orcha\AppData\Local\Temp\opencode\sandbox003\`.

**D1 - the crash.** `Makefile.crash`:

```make
# Sandbox Makefile for the D1 demonstration of scripts/measure_test_baseline.py.
# It lives OUTSIDE the repository; --makefile is what makes it possible.
# The recipe kills the interpreter with a native access violation.

test:
	python -X faulthandler C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/crash.py

test-acceptance:
	python -X faulthandler C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/crash.py
```

`crash.py` (two lines of Python, as the plan asks):

```python
import ctypes
ctypes.string_at(0)
```

```
$ python scripts/measure_test_baseline.py --makefile .../Makefile.crash --attempts 3
D1 exit=2
status: crashed | complete: False
count keys present in the JSON: []
count keys present in the suite entry: []
  attempt 1 exit_code 1 status crashed fault_markers ['Windows fatal exception', 'Current thread 0x00005120']
  attempt 2 exit_code 1 status crashed fault_markers ['Windows fatal exception', 'Current thread 0x0000b920']
  attempt 3 exit_code 1 status crashed fault_markers ['Windows fatal exception', 'Current thread 0x0000f564']
  first attempt tail:
    Windows fatal exception: access violation |  | Current thread 0x00005120 (most recent call first): |   File "C:\Users\orcha\Ap
```

`"status": "crashed"`, **no counts at all**, **exit 2**.

One honest detail: the child's exit code is **1**, not `0xC0000005`. On
CPython 3.14.7/Windows, `ctypes` converts the access violation into an
`OSError` - but only *after* faulthandler has printed the native fault, so
the output carries `Windows fatal exception: access violation` and the
faulthandler dump header. The classification is driven by the fault marker
plus the non-zero exit, which is the rule the plan specifies. A variant
that dereferences the null pointer on a native thread and waits for it was
also tried: it produces a genuine hard death (`3221226505`, `0xC0000409`)
on some runs and a clean exit on others (the crash races interpreter
finalisation), so the deterministic two-line version above is the one used
as the demonstration.

**D2 - the failure.** `Makefile.fail`:

```make
# Sandbox Makefile for the D2 demonstration of scripts/measure_test_baseline.py.
# It lives OUTSIDE the repository; --makefile is what makes it possible.
# The recipe points at a path that does not exist, so pytest exits with a
# usage error (exit code 4).

test:
	python -m pytest C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/no_such_test_path -q

test-acceptance:
	python -m pytest C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/no_such_test_path -q
```

```
$ python scripts/measure_test_baseline.py --makefile .../Makefile.fail --attempts 3
D2 exit=2
status: failed | complete: False
count keys present in the JSON: []
  attempt 1 exit_code 4 status failed fault_markers []
  attempt 2 exit_code 4 status failed fault_markers []
  attempt 3 exit_code 4 status failed fault_markers []
  first attempt tail:
    ERROR: file or directory not found: C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/no_such_test_path |  |  | no tests ran in 0.00s |
```

`"status": "failed"`, **no counts at all**, **exit 2**.

## The path check's two paths

**The pass path** - the four real documents:

```
$ python scripts/check_doc_paths.py
missing paths: 0
exit=0
```

**The fail path** - a sandbox COPY of one of the four documents, outside the
repository, with one bullet naming a module that does not exist (the copy
and the bad bullet were made by a scratch script in the sandbox directory,
never in the worktree):

```
$ python .../make_sandbox_copy.py docs/CODEBASE_BOUNDARIES.md .../sandbox003/CODEBASE_BOUNDARIES.sandbox.md
sandbox copy written to C:\Users\orcha\AppData\Local\Temp\opencode\sandbox003\CODEBASE_BOUNDARIES.sandbox.md
the bad bullet is on line 218

$ python scripts/check_doc_paths.py --docs C:/Users/orcha/AppData/Local/Temp/opencode/sandbox003/CODEBASE_BOUNDARIES.sandbox.md
C:\Users\orcha\AppData\Local\Temp\opencode\sandbox003\CODEBASE_BOUNDARIES.sandbox.md:218: backend/services/this_module_does_not_exist.py (no such module in the repository)
missing paths: 1
exit=1
```

The missing path **and its line number** are named.

**`ALLOWED_ABSENT` is empty and stayed empty.** The script starts with
`ALLOWED_ABSENT: dict[str, str] = {}` and no entry was added to make a
failure disappear. When the check found the three deleted modules, the
DOCUMENTS were corrected.

**What the check found before the documents were corrected** (this is the
evidence that it has teeth on the exact defect of this item - the deleted
modules named by bare filename, which a `backend/...`-only scanner would
never see):

```
C:\...\docs\CODEBASE_BOUNDARIES.md:53: backend/tools/web_tools.py (no such module in the repository)
C:\...\docs\CODEBASE_BOUNDARIES.md:214: backend/services/speech_act_classifier.py (no such module in the repository)
C:\...\docs\CODEBASE_BOUNDARIES.md:215: backend/services/response_strategy.py (no such module in the repository)
C:\...\docs\CODEBASE_BOUNDARIES.md:221: speech_act_classifier.py (no module with this name under backend/)
C:\...\docs\CODEBASE_BOUNDARIES.md:222: response_strategy.py (no module with this name under backend/)
C:\...\backend\services\README.md:55: speech_act_classifier.py (no module with this name under backend/)
C:\...\backend\services\README.md:56: response_strategy.py (no module with this name under backend/)
missing paths: 7
exit=1
```

## The document edits

The canonical block, identical in the five baseline documents:

```
- Full backend suite: **1568 passed, 45 skipped** (1613 collected)
- Acceptance suite: **191 passed, 2 skipped**

Measured on CPython 3.14.7 / Windows by
`python scripts/measure_test_baseline.py` on 2026-10-20.
CI pins Python 3.11.
```

**`README.md`**

* The badge (line 6) now carries the backend pair, URL-encoded in the same
  style as before:

```
before: [![Backend tests](https://img.shields.io/badge/backend%20tests-683%20passed%20%2F%202%20skipped-green)]()
after:  [![Backend tests](https://img.shields.io/badge/backend%20tests-1568%20passed%20%2F%2045%20skipped-green)]()
```

  Decoded, the badge reads `backend tests-1568 passed / 45 skipped`; the
  prose line reads `1568 passed, 45 skipped` - the same pair (rule C3).
* Lines 101-107 became the canonical block. The per-skip breakdown
  ("32 skipped = 27 power/network real-system tests + 3 volume system-audio
  tests + 2 symlink privilege tests") is **deleted, not re-derived**: it was
  never measured. The true statement is kept: the two skipped acceptance
  tests are the symbolic-link privilege skips in
  `backend/tests/acceptance/test_canonical_file_checks.py`.

**`docs/TESTING.md`** (this item owns the file; it was rewritten)

* "Quick Start" no longer carries counts; it points at the baseline
  section.
* "Backend Tests" now states that the suite runs under the repository-root
  `pytest.ini` (`testpaths = backend/tests`, `asyncio_mode = strict`) and
  that the selection contract is enforced mechanically.
* "Current verified baseline" is the canonical block, plus the two
  acceptance skips, plus the three commands that reproduce and check it.
* "Common Test Commands" was rebuilt from the **merged** Makefile, with
  the runtime column measured where a measurement exists and labelled
  otherwise.
* "Pytest Markers" now covers **all ten** canonical markers with the
  population each one actually has today (measured with one
  `--collect-only` pass on the measurement tree: 1613 items), and says
  honestly that `flaky` and `integration` are applied to zero tests.
* "Test Fixtures" was re-read against the merged `backend/tests/conftest.py`
  and now also names the two autouse fixtures (the guarded-autonomy default
  and the `backend/.env` isolation item 006 adds).
* "Frontend" gained `npm test` (the vitest step item 001 wired into CI).
* "Live Smoke Test": "Currently 6/6 live." is **removed** and replaced by:

```
This is a MANUAL check: it needs a running sidecar and a reachable provider,
and **no executed run of it is recorded in this repository**. It is therefore
not a result this repository can quote, and it is not part of the measured
baseline below.
```

* "CI Pipeline" was rebuilt against the merged `.github/workflows/ci.yml`:
  the real trigger set, the four real jobs, the Python version the backend
  job pins, the vitest step the frontend job runs, the manual-dispatch gate
  on the credentialed job - and the honest note that the `tauri-build`
  job's "Assert the shell binary was produced" step is a `Test-Path` probe
  that does **not** currently gate the job.
* "Makefile Commands" carries the measured, machine-qualified runtimes.
* "Notes" records that strict asyncio mode is now *pinned* by the root
  `pytest.ini`, that the warning count is not a baseline, and that a
  measurement is a completed, fault-free run or it is nothing.

**`docs/PROJECT_STATUS.md`**

* "response strategy" removed from the intelligence-layer list.
* "Verified test baseline" became the canonical block; the sentence about
  the skips now says **acceptance** suite (the old prose said
  "backend/acceptance", which is not a path to anything) and names
  `backend/tests/acceptance/test_canonical_file_checks.py`.

**`docs/ROADMAP.md`**

* The canonical block replaces the "As of the latest verified workspace
  state (2026-08-16)" list header.
* The "**response strategy**" bullet is **removed** - it described a
  capability deleted with the modules.
* The other bullets were re-checked against the tree (see "The decisions
  and their reasons").

**`docs/OPERATIONS.md`**

* The canonical block replaces the baseline list; the "live smoke script:
  documented as **6/6 live**" bullet is removed and replaced by the manual
  -check statement.
* The "Windows/shell note for this workspace" section's
  `PYTHONPATH=/c/Ai/sythion:/c/Ai/sythion/backend python -m pytest
  sythion/backend/tests -q` invocation - which names a checkout that does
  not exist here - is replaced by the two invocations that verifiably work
  (from the repository root and from `backend/`), with the interpreter and
  the OS stated. The second one was verified on the measurement tree:

```
$ cd wt-003-merged/backend && python -m pytest tests --collect-only -q
1613 tests collected in 98.72s (0:01:38)
EXITCODE=0
```

  (the collection pass, not a full run, because the full run from the
  repository root is the measurement itself and the two invocations collect
  the same 1613 tests).

**`docs/CODEBASE_BOUNDARIES.md`**

* `backend/tools/web_tools.py` is removed from the "Tool execution
  substrate" list (a THIRD deleted module, `2469c09`, that the brief did
  not name). The replacement names only paths that resolve:
  `backend/api/browser_bridge_routes.py` and
  `backend/services/browser_bridge_service.py`.
* "speech-act classification formatting" and "response-strategy formatting"
  are removed from the conversation-guidance adapter's description; the
  adapter does recent-turn replay and conversation-state formatting (its own
  module docstring says exactly that).
* The two deleted modules are removed from the intelligence-layer list, and
  the Notes bullet is rewritten to name the modules that exist.

**`backend/services/README.md`**

* The conversation-guidance adapter's description is corrected the same way.
* The two deleted modules are removed from the intelligence-layer list.

**`TESTING_GUIDE.md`**

* The stale block became a **pointer** to `docs/TESTING.md`, not a sixth
  copy of the number.

### Criterion 7: the tables against the merged files

The merged state means `master` + item 002 + item 006 + item 007 (the
measurement tree). Every claim in the three sections of `docs/TESTING.md`
with its source:

| Document claim | Merged file | file:line |
|----------------|--------------|-----------|
| `make test` = the full backend suite | `Makefile` | 23-24 |
| `make test-fast` = without slow/gui/network | `Makefile` | 28-29 |
| `make test-acceptance` = `backend/tests/acceptance` | `Makefile` | 34-35 |
| `make test-unit` = without the acceptance sub-suite | `Makefile` | 38-39 |
| `make test-gui` = the live interactive desktop modules | `Makefile` | 46-47 |
| `make test-network` = the provider-backed E2E tests | `Makefile` | 53-54 |
| `make check-test-selections` exists | `Makefile` | 59-63 |
| `make test-cov`, `make test-file`, `make test-fn` exist | `Makefile` | 66-78 |
| `testpaths = backend/tests` | `pytest.ini` | 34 |
| `asyncio_mode = strict` | `pytest.ini` | 35 |
| the ten canonical markers | `pytest.ini` | 36-46 |
| `acceptance` == the tests under `backend/tests/acceptance/` | `backend/tests/conftest.py` | 160-172, 269-280 |
| `gui` == the modules in `LIVE_GUI_MODULES` | `backend/tests/conftest.py` | 80-82, 200-245 |
| `network` == the `provider_e2e` population | `backend/tests/conftest.py` | 171-172, 293-310 |
| the selection-contract guard aborts a bad full-tree collection | `backend/tests/conftest.py` | 334-351 |
| the five reusable fixtures | `backend/tests/conftest.py` | 365-457 |
| the guarded-autonomy autouse fixture | `backend/tests/conftest.py` | 354-361 |
| the `backend/.env` isolation autouse fixture (item 006) | `backend/tests/conftest.py` | 474-522 |
| triggers: push to `[master, main]`, `pull_request`, `workflow_dispatch` | `.github/workflows/ci.yml` | 3-7 |
| backend job pins Python 3.11 | `.github/workflows/ci.yml` | 16-20 |
| backend job runs `python -m pytest tests -q` from `backend/` | `.github/workflows/ci.yml` | 35-39 |
| the import gate runs from the repository root | `.github/workflows/ci.yml` | 41-45 |
| `provider-e2e` is reachable only by a manual dispatch with `RUN_PROVIDER_E2E=1` | `.github/workflows/ci.yml` | 47-78 |
| the frontend job runs the vitest step | `.github/workflows/ci.yml` | 103-104 |
| the `tauri-build` "Assert the shell binary" step is a `Test-Path` probe | `.github/workflows/ci.yml` | 132-133 |

## The three corrections to the plan

**Correction 1 - the exit-code contract.** The plan states it twice with
different numbers. The contract implemented (and stated in the script's
docstring, in `--help`, and here) is the brief's table:

| Exit | Meaning |
|------|---------|
| 0 | the measurement succeeded / the documents agree |
| 1 | `--check` mode: the documents disagree with the measurement (stale) |
| 2 | no complete measurement could be made (crash, native fault, failure, no summary) - **inconclusive** |
| 3 | usage error (no Makefile target, unreadable root, invalid `--measured` file) |
| 4 | `--check` mode: the running interpreter/platform differs from the one the documents name - **inconclusive** |

All five are demonstrated: 0 (the `--check` transcript), 1 (the check
against the pre-edit documents), 2 (D1 and D2), 3 (the usage-error
transcript), 4 (the synthetic 3.11 measurement).

**Correction 2 - R4 is 15, not 14.** Recorded as 15 in the reproduction
table above, with the full list of the 15 lines, and the reason: the README
contributes three lines (the badge at `:6`, the prose at `:103` and `:104`).

**Correction 3 - the dependency the plan does not state.** The measurement
was taken on a tree that carries item 007's guard, and never on a tree that
does not. The measurement tree is `master` + item 002 + item 006 + item
007; the guard is item 007's
`backend/tests/acceptance/test_secret_storage.py` + `acceptance/conftest.py`
change, which redirects the round-trip test to the test-scoped target
`Sythion (Synth) [TEST]/ollama_api_key` and installs a guard that fails any
acceptance test that writes to or deletes a product-prefixed credential
target.

## The decisions and their reasons

**The two corrections BEYOND the seven criteria** (the plan includes them;
the brief lists them as decisions to record):

1. `TESTING_GUIDE.md`'s stale block became a pointer to `docs/TESTING.md`.
   It was the only document that contradicted itself: it quoted
   `692 / 32` and `196 / 2` while pointing the reader at `docs/TESTING.md`
   for "the current verified baseline" (its own lines 6-7).
2. `docs/OPERATIONS.md`'s `PYTHONPATH=/c/Ai/sythion:...` invocation, which
   names `C:\Ai\sythion` - a path that does not exist in this checkout - was
   replaced by the two invocations that verifiably work, with the
   interpreter and the OS stated.

**The one deliberate exclusion**: `docs/CONVERSATIONAL_PROTOCOL_PLAN.md`
proposes the deleted modules as **future work** ("**New file:**
`backend/services/speech_act_classifier.py`"). It is a plan, not a claim
about the tree, and it is outside criterion 6's four documents, so it was
left alone. Same for `docs/DECISIONS.md` and
`docs/INTELLIGENCE_ARCHITECTURE.md`.

**The per-skip breakdown is deleted, not re-derived.** The full suite's 45
skips have no measured breakdown (the baseline report could not obtain one:
the `-rs` run was the one that crashed). Inventing one would be the exact
defect this item exists to remove. The README's parenthetical is gone; the
acceptance suite's two skips are still named, because those two ARE
identified (`backend/tests/acceptance/test_canonical_file_checks.py:49` and
`:68`).

**One quote per document.** Each of the five baseline documents carries the
canonical block once; the README badge is the one deliberate second quote
(criterion 3 requires it). `TESTING_GUIDE.md` carries none.

**The ROADMAP bullets that were re-checked against the tree** (the plan
requires the Implementer to record which ones were checked):

| Bullet | Kept? | How it was checked |
|--------|-------|--------------------|
| runtime/intelligence boundaries clarified in code and docs | kept | the boundary documents themselves are the evidence |
| `skill_engine.py` split into runtime vs proposal | kept | `backend/services/skill_engine.py`, `skill_runtime.py`, `skill_proposals.py` all exist |
| memory tooling split into durable memory vs runtime activity recall | kept | `backend/tools/persistent_memory_tools.py`, `backend/tools/activity_recall_tool.py` exist |
| conversation-guidance wiring extracted behind an explicit adapter | kept | `backend/services/conversation_guidance_adapter.py` exists and formats the persisted conversation state for a model request |
| the `BaseTool` contract corrected at the executor layer | kept | `class BaseTool` is in `backend/tools/executor.py` |
| a durable work-state layer with `Objective` + `PlanStep`, task linkage, plan APIs, prompt-context injection | kept | `class Objective` / `class PlanStep` in `backend/api/schemas.py` and `backend/models/database.py`; `backend/tools/objective_planning_tool.py` |
| objective completion criteria + verification | kept | `completion_criteria`, `criteria_verified`, `parent_objective_id` and `POST /objectives/{id}/verify-criteria` (`backend/api/routes.py:832`) all exist |
| **parallel tool call execution** | **reworded** | the code says the opposite: `backend/services/model_engine.py:370-374` logs "Model returned %d tool calls; executing sequentially" and `:410` queues the rest in `_pending_tool_calls`. The bullet's own parenthetical said "sequential"; the bold title said "parallel". The title was corrected to "sequential multi-call tool execution" - the code does not execute tool calls in parallel |
| memory retrieval quality (stale edge filter, bidirectional traversal, fallback scoring) | kept | `backend/services/memory_service.py:473-520` retrieves only ACTIVE memories (the stale filter) with deterministic scoring; `backend/tools/persistent_memory_tools.py:496-561` does the graph recall with hops (the traversal) |
| **response strategy** | **removed** | the modules were deleted by `db10f86`; the capability is gone |
| executor static-analysis cleanup | kept | `backend/tools/executor.py` uses modern imports, type hints and explicit exception handling |

**The path check's scope** is the four documents criterion 6 names. It was
NOT extended: the other documents legitimately contain paths that were never
built (a plan proposes them), which is why the scope is explicit, and
`--docs` makes extending it a one-line change.

**The marker populations** were measured with ONE `--collect-only` pass
over the measurement tree using a scratch plugin outside the repository
(`--collect-only` executes no test bodies, so it is safe on any tree). The
result: 1613 items; `acceptance` 193, `concurrency` 53, `gated_soak` 1,
`gui` 4, `network` 5, `process_kill` 8, `provider_e2e` 5, `slow` 1,
`flaky` 0, `integration` 0. `concurrency` (53) and `gated_soak` (1) match
what the merged `pytest.ini` claims; `network` (5) equals the
`provider_e2e` population (5), as the selection-contract guard requires.

## Findings

1. **The suite's warning count is not a baseline, and the script proves
   it**: the backend run reported `412809 warnings` and the acceptance run
   `16900`. Neither is in the JSON - the summary parser does not even match
   the word "warnings".
2. **The backend suite crashed on the first attempt of the final
   measurement** (exit `3221225477`, `0xC0000005`, with
   `Windows fatal exception` and the faulthandler dump headers). The retry
   design caught it and published nothing from that attempt. The second
   attempt completed with the same numbers as the earlier run. It happened
   again in the first of the two full `--check` runs (again
   `3221225477`); the second `--check` run completed on the first attempt.
   **Four independent full-suite runs on the same tree produced the same
   numbers: 1568 passed, 45 skipped, 1613 collected** - and the acceptance
   suite produced 191 passed, 2 skipped every time.
3. **`backend/tests/acceptance/test_gui_fixture.py::test_invoke_flips_fixture_status`
   is flaky on this machine** (the earlier measurement run: `190 passed, 2
   skipped, 1 error`; the retried run: `191 passed, 2 skipped`). The
   acceptance suite therefore NEEDS the retry, not just the backend suite.
4. **The `backend/acceptance` prose shorthand** in `docs/PROJECT_STATUS.md`
   was a path to nothing (the real one is `backend/tests/acceptance`).
   That is why the path check scans code font only.
5. **`make` is not installed on this machine**, so the Makefile's targets
   cannot be executed as such. The scripts run the recipes verbatim.
6. **A clean checkout has no `backend/.env`**; item 006's isolation is what
   makes the readiness test green on the measurement tree. On the tree
   without item 006, `test_setup_readiness.py::test_readiness_states`
   fails - that is item 006's subject, not this item's.

## Not checked

1. **The workflow was never executed.** No CI run was triggered or
   observed; every statement about `ci.yml` is a reading of the merged
   file.
2. **No live sidecar, no smoke test, no provider call.** The live smoke
   test remains a manual check with no recorded executed run; nothing in
   this item changed that.
3. **The `tauri-build` job's shell-binary probe was not tested on a
   runner.** It is described from the merged `ci.yml:132-133`
   (`Test-Path` prints and exits 0 either way).
4. **The measurement was taken on a tree that is not yet `master`.** Items
   002, 006 and 007 were still in review when it was taken. If any of them
   changes before it merges, the numbers move and `--check` /
   `--check-collected` will report it - which is the design, not a defect.
5. **The runtimes of `make test-fast`, `make test-unit`, `make test-gui`,
   `make test-network`, `make test-cov`, `make test-file` and
   `make test-fn` were not measured** (each is a full-suite run of several
   minutes, and the suite crashes roughly one run in three). They are
   labelled "machine-specific (not measured here)" rather than guessed.
6. **`make` itself was never run** - it is not installed here. The
   Makefile's recipes were executed verbatim by the scripts.
7. **The frontend build was not run** (not this item's subject); the vitest
   step is described from `frontend/package.json` and `ci.yml`.
8. **The per-skip breakdown of the 45 full-suite skips is still not
   measured.** It is deleted from the documents rather than re-derived.
9. **The `backend/.env` isolation fixture** (item 006) is described in
   `docs/TESTING.md` from the merged `conftest.py`; its behaviour was not
   re-verified by this item.
10. **No file outside the branch was written** except this report (which
    also exists at `C:\Sythion\sythion\.orchestra\reports\003-implement-1.md`,
    the path the brief names) and the scratch files in
    `C:\Users\orcha\AppData\Local\Temp\opencode\` (the sandbox Makefiles,
    the crash program, the marker-population plugin and the scratch
    scripts) - all outside the repository.

## A note for the orchestrator (replacement text, not written to follow-ups.md)

The following are the follow-ups this item would have written to
`.orchestra/follow-ups.md`:

1. The two native faults remain unexplained; this item only made the
   measurement honest about them. The backend suite crashed with
   `0xC0000005` on the first attempt of the final measurement.
2. `backend/tests/acceptance/test_gui_fixture.py::test_invoke_flips_fixture_status`
   is flaky on this machine and needs the retry to pass. It is a candidate
   for a follow-up item.
3. `docs/CONVERSATIONAL_PROTOCOL_PLAN.md`, `docs/DECISIONS.md` and
   `docs/INTELLIGENCE_ARCHITECTURE.md` still name the deleted modules as
   planned future work. They are plans, not claims, and were deliberately
   left alone.
4. `C:\Sythion\SYTHION_TODOS.md` (outside this repository) still says
   "349 backend tests green, 2 skipped".
5. A CI job that runs `python scripts/measure_test_baseline.py
   --check-collected` after a Python 3.11 measurement is the natural next
   step; it was deliberately not wired here (a ~12-minute measurement per
   push is not acceptable, and a CI-side `--check` would compare two
   different interpreters and produce a false "stale" - which is what exit
   code 4 exists for).
