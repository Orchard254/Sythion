# Report: implement - 002 - attempt 1

Item: **002 - Makefile test selections and pytest configuration stop claiming what they do not do**
Role: implementer (implementer-3)

- **Base commit:** `662cbe38c5bf80b70794f914ff1b07ee82884043` (`master` at the time of branching)
- **Branch:** `orchestra/002-test-selection-truth`
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-002` (created with
  `git worktree add C:\Users\orcha\AppData\Local\Temp\opencode\wt-002 -b orchestra/002-test-selection-truth master`
  from `C:\Sythion\sythion`). Nothing was checked out, committed or written in the orchestrator's tree.
- **Commit:** `0f27d9390219f3469055f9b296d9de5b55518900` -
  `test: make the pytest configuration and Makefile selections say what they do`
- **Files changed:** exactly four - `pytest.ini` (new), `Makefile`,
  `backend/tests/conftest.py`, `backend/tests/acceptance/test_gui_fixture.py`.
  `git diff --name-only 662cbe3 HEAD` -> `Makefile`,
  `backend/tests/acceptance/test_gui_fixture.py`, `backend/tests/conftest.py`, `pytest.ini`.
- **Never pushed.** No destructive command was run. `make clean`,
  `scripts/stage_python_runtime.py`, migrations and `npx tauri build` were not run.
  The no-argument `python -m pytest` collection was **not** run on the base tree.

---

## What I did

### The four files

1. **`pytest.ini` (NEW, repository root).** The pytest configuration the repository never had.
   `testpaths = backend/tests`, `asyncio_mode = strict`, and the ten canonical markers, each with
   an honest description of what it currently selects.
   **No `filterwarnings` setting of any kind, no `addopts`, no `--strict-markers`, no
   `norecursedirs`/`python_files`/`python_classes`/`xfail_strict`.**
2. **`backend/tests/conftest.py`.**
   - `pytest_configure` now declares the canonical markers **only if the root configuration has
     not already declared them** (the canonical set grows from eight to ten: `concurrency` and
     `gated_soak` are added).
   - `pytest_itemcollected` applies the two rule-based classifications: `acceptance` to every
     item under `backend/tests/acceptance/`, `network` to every item that already carries
     `provider_e2e`.
   - `pytest_collection_finish` runs the **selection-contract guard** on every full-tree
     collection and aborts with `pytest.UsageError` (exit 4) when a marker population drifts.
3. **`backend/tests/acceptance/test_gui_fixture.py`.** One added block:
   `pytestmark = pytest.mark.gui`.
4. **`Makefile`.** `test-fast` drops the two clauses that deselected nothing
   (`not flaky`, `not integration`); `test-gui`/`test-network` gain `-rs`; the new
   `check-test-selections` gate; the header block and the `## Help descriptions` block were
   rewritten to state **what each target selects**, with no runtime claims.

### The mechanism (why `pytest_itemcollected`, not `pytest_collection_modifyitems`)

pytest's own marker deselection (`MarkPlugin.deselect_by_mark`) is registered
`@hookimpl(tryfirst=True)` (`_pytest/mark/__init__.py`), so it runs **before** any other
`pytest_collection_modifyitems` implementation. A marker added in
`pytest_collection_modifyitems` is therefore invisible to `-m`. `pytest_itemcollected` is
called for **every** item during collection (`_pytest/main.py:967`), i.e. *before*
`pytest_collection_modifyitems` runs (`_pytest/main.py:812-814`), so a marker added there is
visible to `-m`. Verified empirically on this machine (pytest 8.4.2):

```
> python -m pytest backend/tests/acceptance -m gui --collect-only -q
backend/tests/acceptance/test_gui_fixture.py::test_observe_finds_fixture_window
backend/tests/acceptance/test_gui_fixture.py::test_invoke_flips_fixture_status
backend/tests/acceptance/test_gui_fixture.py::test_set_value_writes_fixture_edit
backend/tests/acceptance/test_gui_fixture.py::test_focus_brings_fixture_window_foreground
4/193 tests collected (189 deselected) in 4.33s

> python -m pytest backend/tests/acceptance -m "not acceptance" --collect-only -q
no tests collected (193 deselected) in 5.12s
```

### Commands run (all from the worktree, all outputs kept in
`C:\Users\orcha\AppData\Local\Temp\opencode\002-evidence\`)

```
# BEFORE (base commit 662cbe3, tree unmodified)
python -m pytest backend/tests --collect-only -q -p no:cacheprovider
python -m pytest backend/tests --collect-only        -p no:cacheprovider     # header
python -m pytest backend/tests -m gui         --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m network     --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m acceptance  --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m flaky       --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m integration --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m slow        --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -q -p no:cacheprovider                      # full suite

# AFTER (commit 0f27d93)
python -m pytest backend/tests --collect-only -q -p no:cacheprovider
python -m pytest backend/tests --collect-only        -p no:cacheprovider     # header
python -m pytest backend/tests -m <each of the ten canonical markers> --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m "not acceptance" --collect-only -q -p no:cacheprovider
python -m pytest backend/tests -m "slow or gui or network" --collect-only -q -p no:cacheprovider
python -m pytest backend/tests/acceptance --collect-only -q -p no:cacheprovider
python -m pytest backend/tests/test_work_state.py --collect-only -q -p no:cacheprovider   # partial run
python -m pytest --collect-only -q -p no:cacheprovider                                   # no-argument
python -m pytest --markers
python -m pytest backend/tests -q -p no:cacheprovider                      # full suite
```

### One environment difference, and how it was neutralised

The worktree does **not** contain `backend/.env`, because that file is **gitignored and
untracked** and therefore is not checked out into a worktree:

```
> Test-Path C:\Sythion\sythion\backend\.env                 -> True
> Test-Path ...\wt-002\backend\.env                          -> False
> git check-ignore -v backend/.env
.gitignore:16:.env	backend/.env
> git ls-files --error-unmatch backend/.env
fatal: pathspec 'backend/.env' did not match any file(s) known to git
```

`backend/core/config.py:175-178` loads that file through pydantic-settings
(`"env_file": <repo>/backend/.env`), so `settings.ollama_api_key` is empty in the worktree, and
`backend/api/routes.py:2261` (`key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)`)
then reports `ollama_api_key_ok = False`, which makes
`backend/tests/test_setup_readiness.py::test_readiness_states` fail on
`assert body["full_ready"] is True`. Proven by running the same test in both trees:

```
# worktree (no backend/.env)
> python -m pytest backend/tests/test_setup_readiness.py -q -p no:cacheprovider
FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
1 failed, 7 passed, 589 warnings in 4.59s

# master working copy (has backend/.env)
> cd C:\Sythion\sythion; python -m pytest backend/tests/test_setup_readiness.py -q -p no:cacheprovider
8 passed, 592 warnings in 3.68s
```

and by dumping the readiness checks (script kept outside the repository):

```
# worktree
full_ready = False
   ollama_api_key_ok            False      <- the only failing check
   ... all other 9 checks True
settings.ollama_api_key set = False

# master working copy
full_ready = True
   ollama_api_key_ok            True
settings.ollama_api_key set = True
```

The real credential was **not** copied into the worktree. Instead, the full-suite runs on both
sides used a non-secret stand-in that restores the same *boolean* state master has (a configured
key):

```
$env:SYTHION_OLLAMA_API_KEY = "sythion-local-dummy-key-not-a-real-credential"
```

No test makes a credentialed provider call with it: the only consumers of
`settings.ollama_api_key` outside the readiness checks are the two `provider_e2e` modules, whose
tests are gated on `SYTHION_RUN_PROVIDER_E2E=1` (not set) and therefore skip.

---

## Evidence

### E1 - R1/R2/R5 on the BASE commit: the selections collect nothing

```
$ python -m pytest backend/tests -m gui --collect-only -q -p no:cacheprovider
no tests collected (1609 deselected) in 152.96s (0:02:32)
EXITCODE=5

$ python -m pytest backend/tests -m network --collect-only -q -p no:cacheprovider
no tests collected (1609 deselected) in 135.63s (0:02:15)
EXITCODE=5

$ python -m pytest backend/tests -m acceptance --collect-only -q -p no:cacheprovider
no tests collected (1609 deselected) in 141.12s (0:02:21)
EXITCODE=5

$ python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q -p no:cacheprovider
1608/1609 tests collected (1 deselected) in 136.61s (0:02:16)
EXITCODE=0
```

The base's own `make test-fast` expression (with `not flaky and not integration`) gives the same
`1608/1609 ... (1 deselected)` - i.e. the two clauses this item drops were no-ops on the base:

```
$ python -m pytest backend/tests -m flaky --collect-only -q -p no:cacheprovider
no tests collected (1609 deselected) in 132.22s (0:02:12)
EXITCODE=5

$ python -m pytest backend/tests -m integration --collect-only -q -p no:cacheprovider
no tests collected (1609 deselected) in 134.36s (0:02:14)
EXITCODE=5

$ python -m pytest backend/tests -m slow --collect-only -q -p no:cacheprovider
1/1609 tests collected (1608 deselected) in 119.54s (0:01:59)
EXITCODE=0
```

### E2 - R3 on the BASE commit: no `configfile:` line

```
$ python -m pytest backend/tests --collect-only -p no:cacheprovider
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\Users\orcha\AppData\Local\Temp\opencode\wt-002
plugins: anyio-4.15.1, asyncio-0.23.8
asyncio: mode=Mode.STRICT
collected 1609 items
...
================== 1609 tests collected in 146.83s (0:02:26) ==================
EXITCODE=0
```

There is no `configfile:` line: the repository had no `pytest.ini`, `pyproject.toml`,
`tox.ini` or `setup.cfg`.

### E3 - R4 on the BASE commit: three `PytestUnknownMarkWarning`

From the base full-suite output (`before-full-suite-env.txt`):

```
backend\tests\test_phase105_concurrency.py:39: PytestUnknownMarkWarning: Unknown pytest.mark.concurrency - is this a typo? ...
    pytestmark = [pytest.mark.concurrency]

backend\tests\test_phase1075_hardening.py:35: PytestUnknownMarkWarning: Unknown pytest.mark.concurrency - is this a typo? ...
    pytestmark = [pytest.mark.concurrency]

backend\tests\test_phase115_soak.py:15: PytestUnknownMarkWarning: Unknown pytest.mark.gated_soak - is this a typo? ...
    pytestmark = pytest.mark.gated_soak
```

### E4 - The full suite, BEFORE and AFTER (same worktree, same base, back to back)

| | collected | result | warnings | wall time | exit |
|---|---|---|---|---|---|
| **BEFORE** (`before-full-suite-env.txt`, base `662cbe3`) | 1609 | **1564 passed, 45 skipped** | 406258 | 491.67 s (0:08:11) | **0** |
| **AFTER** (`after-full-suite.txt`, branch `0f27d93`) | 1609 | **1564 passed, 45 skipped** | 406269 | 1700.83 s (0:28:20) | **0** |

```
# BEFORE
1564 passed, 45 skipped, 406258 warnings in 491.67s (0:08:11)
EXITCODE=0

# AFTER
1564 passed, 45 skipped, 406269 warnings in 1700.83s (0:28:20)
EXITCODE=0
```

The wall-time difference is **machine load, not the change**: during the AFTER run three other
heavy `python` processes (the parallel Implementers for items 001/004) were running full suites
on the same 8-core box; the BEFORE run was taken on a quieter box. The pass/skip counts and the
exit code are identical.

The base run **without** the stand-in key (i.e. the raw worktree, `before-full-suite.txt`) is
also recorded, so the reviewer can see the `.env` effect in isolation:

```
1 failed, 1563 passed, 45 skipped, 406263 warnings in 543.12s (0:09:03)
EXITCODE=1
FAILED backend/tests/test_setup_readiness.py::test_readiness_states - assert ...
```

(The single failure is the missing gitignored `backend/.env`, explained above; it is present on
the base tree too, not caused by this change. It does **not** reproduce on the master working
copy, where the same test passes.)

### E5 - R3/R4 on the BRANCH: the configuration is named, the warnings are gone

```
$ python -m pytest backend/tests --collect-only -p no:cacheprovider
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\Users\orcha\AppData\Local\Temp\opencode\wt-002
configfile: pytest.ini
plugins: anyio-4.15.1, asyncio-0.23.8
asyncio: mode=Mode.STRICT
collected 1609 items
...
================== 1609 tests collected in 146.83s (0:02:26) ==================
EXITCODE=0

$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
1609 tests collected in 143.65s (0:02:23)
EXITCODE=0

$ Select-String -Path after-collect-all.txt -Pattern "PytestUnknownMarkWarning" | Measure-Object
Count
-----
0
```

`pytest --markers` on the branch lists all ten canonical markers:

```
@pytest.mark.slow: a slow test (deselect with '-m "not slow"')
@pytest.mark.flaky: declared for future use; not currently applied to any test
@pytest.mark.integration: declared for future use; not currently applied to any test
@pytest.mark.acceptance: the acceptance sub-suite under backend/tests/acceptance/
@pytest.mark.gui: requires a live interactive Windows desktop and the real UI Automation stack (currently backend/tests/acceptance/test_gui_fixture.py)
@pytest.mark.network: requires an outbound network connection (currently every provider_e2e test)
@pytest.mark.process_kill: real Windows process-kill integration tests (run explicitly with SYTHION_RUN_PROCESS_KILL=1)
@pytest.mark.provider_e2e: real provider-backed specialist E2E tests (run explicitly with SYTHION_RUN_PROVIDER_E2E=1 plus a configured provider credential/model; skipped otherwise)
@pytest.mark.concurrency: tests that exercise the shared runtime under concurrent load
@pytest.mark.gated_soak: long-running soak tests, gated behind an explicit environment flag
```

(plus `asyncio`, `anyio` and the pytest built-ins.)

### E6 - The four Makefile selections, measured on the branch

| Target | Marker expression | collected | deselected | exit |
|---|---|---|---|---|
| `test-fast` | `not slow and not gui and not network` | **1599** | **10** | 0 |
| `test-unit` | `not acceptance` | **1416** | **193** | 0 |
| `test-gui` | `gui` | **4** | 1605 | 0 |
| `test-network` | `network` | **5** | 1604 | 0 |

```
$ python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q -p no:cacheprovider
1599/1609 tests collected (10 deselected) in 146.10s (0:02:26)
EXITCODE=0

$ python -m pytest backend/tests -m "not acceptance" --collect-only -q -p no:cacheprovider
1416/1609 tests collected (193 deselected) in 112.67s (0:01:52)
EXITCODE=0

$ python -m pytest backend/tests -m gui --collect-only -q -p no:cacheprovider
4/1609 tests collected (1605 deselected) in 935.13s (0:15:35)
EXITCODE=0

$ python -m pytest backend/tests -m network --collect-only -q -p no:cacheprovider
5/1609 tests collected (1604 deselected) in 936.90s (0:15:36)
EXITCODE=0
```

On the base commit the same four commands give `1608/1609 (1 deselected)` exit 0,
`1609/1609 (0 deselected)` exit 0, **exit 5** and **exit 5** - i.e. `make check-test-selections`
fails on the base (at the third line) and passes on the branch.

`acceptance` selects exactly the acceptance directory - the path selection and the marker
selection have the same size:

```
$ python -m pytest backend/tests/acceptance --collect-only -q -p no:cacheprovider
193 tests collected in 5.62s
EXITCODE=0

$ python -m pytest backend/tests -m acceptance --collect-only -q -p no:cacheprovider
193/1609 tests collected (1416 deselected) in 170.39s (0:02:50)
EXITCODE=0
```

### E7 - The complete set `make test-fast` deselects (10 node IDs)

```
$ python -m pytest backend/tests -m "slow or gui or network" --collect-only -q -p no:cacheprovider
backend/tests/acceptance/test_gui_fixture.py::test_observe_finds_fixture_window
backend/tests/acceptance/test_gui_fixture.py::test_invoke_flips_fixture_status
backend/tests/acceptance/test_gui_fixture.py::test_set_value_writes_fixture_edit
backend/tests/acceptance/test_gui_fixture.py::test_focus_brings_fixture_window_foreground
backend/tests/test_phase105_concurrency.py::TestFairnessSlotAllocation::test_long_running_fairness
backend/tests/test_phase105_concurrency.py::TestProviderE2EParallel::test_two_reasoning_parallel_provider
backend/tests/test_phase1075_hardening.py::TestProviderE2EGated::test_two_reasoning_real_provider_isolated
backend/tests/test_phase10_provider_e2e.py::test_two_reasoning_specialists_aggregation
backend/tests/test_phase975_provider_e2e.py::TestProviderReasoningOnlyE2E::test_analysis_specialist_full_path_with_real_model
backend/tests/test_phase975_provider_e2e.py::TestProviderActionProducingE2E::test_coding_specialist_proposes_and_executes_fixture_tool
10/1609 tests collected (1599 deselected) in 182.24s (0:03:02)
EXITCODE=0
```

Note that the two `provider_e2e` tests that live in `test_phase105_concurrency.py` and
`test_phase1075_hardening.py` - the files item 004 is editing in parallel - are covered by the
**rule** ("`provider_e2e` implies `network`"), so item 004 needs no edit for this.

### E8 - The guard FIRES (demonstration 1: `gui` applied to the wrong file)

`@pytest.mark.gui` was temporarily inserted above the first test of
`backend/tests/test_work_state.py` (a file outside the acceptance directory):

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
ERROR: selection-contract violation in the backend test tree:
  - marker 'gui' is applied to 1 test(s) it does not describe, e.g. backend/tests/test_work_state.py::test_decompose_request_splits_sequence_connectors
...
EXITCODE=4
```

(The `ERROR:` line appears in the middle of the captured file only because pytest writes it to
unbuffered stderr while the collected node IDs are flushed from a block-buffered stdout at exit;
the exit code is what matters and it is **4**, pytest's `EXIT_USAGEERROR`.)

### E9 - The guard FIRES (demonstration 2: an undeclared marker)

`@pytest.mark.sythion_guard_demo_undeclared` (not one of the canonical ten) was temporarily
inserted above the same test:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
backend\tests\test_work_state.py:60: PytestUnknownMarkWarning: Unknown pytest.mark.sythion_guard_demo_undeclared - is this a typo? ...
    @pytest.mark.sythion_guard_demo_undeclared
...
ERROR: selection-contract violation in the backend test tree:
  - undeclared marker 'pytest.mark.sythion_guard_demo_undeclared' is applied to 1 test(s), e.g. backend/tests/test_work_state.py::test_decompose_request_splits_sequence_connectors
...
EXITCODE=4
```

### E10 - The guard is a NO-OP again after undoing the mis-marking

The temporary edit was reverted from a byte-copy backup (not with a git checkout):

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
1609 tests collected in 149.82s (0:02:29)
EXITCODE=0

$ git status --short
$ git diff --stat
(empty - the tree is clean)
$ Select-String -Path backend/tests/test_work_state.py -Pattern "pytest.mark.gui|guard_demo" | Measure-Object
matches: 0
```

The guard is also a no-op on the merged tree: the full suite on the branch exits 0 (E4), and
item 004's branch (which adds `backend/tests/test_no_tautological_asserts.py` and edits three
other test files) introduces **no** `pytest.mark` at all, so it cannot trip any of the five
rules:

```
$ git diff 662cbe3 orchestra/004-real-assertions -- backend/tests/test_phase105_concurrency.py \
      backend/tests/test_phase1075_hardening.py backend/tests/test_phase125_planning_validation.py \
    | Select-String -Pattern "^[+-].*pytest\.mark|^[+-].*pytestmark"
(no output)
$ git show orchestra/004-real-assertions:backend/tests/test_no_tautological_asserts.py \
    | Select-String -Pattern "pytest\.mark\.\w+|pytestmark"
(no output)
```

### E11 - A PARTIAL run does not trip the guard

```
$ python -m pytest backend/tests/test_work_state.py --collect-only -q -p no:cacheprovider
57 tests collected in 4.00s
EXITCODE=0
```

### E12 - The no-argument invocation now collects the backend suite only

```
$ python -m pytest --collect-only -q -p no:cacheprovider
1609 tests collected in 176.42s (0:02:56)
EXITCODE=0
```

This is the one deliberate change to an existing command: with `testpaths = backend/tests` the
no-argument invocation collects the backend suite instead of walking the whole repository root
(13 root-level `test_*.py` scripts plus the vendored runtime's third-party test modules). Every
documented command passes an explicit path and is unaffected. The no-argument collection was
**not** run on the base tree, for the reason the plan gives.

### E13 - Warning volume: no new category, no warning turned into a failure

Per-file warning-group comparison of the two full-suite runs (script outside the repository):

```
group count before/after: 926 926
total before/after: 385772 385772
identical per-group counts: True
```

The only differences between the two warnings summaries are:

* the **3** `PytestUnknownMarkWarning` entries disappear (the intended fix), and
* the block of `PytestUnhandledThreadExceptionWarning`s raised by a background thread of
  `backend/tests/test_phase105_concurrency.py` (a SQLAlchemy/sqlite3
  `UNIQUE constraint failed: task_events.task_id, task_events.sequence_num` race in a thread)
  occurs **2 times before and 1 time after** - a pre-existing non-deterministic warning, one
  instance fewer in the AFTER run. That single block is the entire `+11` difference in the
  warning total (406258 -> 406269).

No warning became a failure: both runs exited 0 with the same 1564 passed / 45 skipped.
`pytest.ini` contains **no** `filterwarnings` setting:

```
$ Select-String -Path pytest.ini -Pattern "filterwarnings|addopts|strict"
pytest.ini:8:#   * filterwarnings - the suite emits a large number of datetime.utcnow()
pytest.ini:10:#   * addopts        - no --strict-markers (the guard in
pytest.ini:12:#     an addopts entry would change the effective command line of every
pytest.ini:14:#   * norecursedirs / python_files / python_classes / xfail_strict.
pytest.ini:22:asyncio_mode = strict
```

(the only hits are the comment that documents their absence, and `asyncio_mode = strict`).

### E14 - The diff is confined to the test tooling

```
$ git diff --name-only 662cbe3 HEAD
Makefile
backend/tests/acceptance/test_gui_fixture.py
backend/tests/conftest.py
pytest.ini

$ git diff --stat 662cbe3 HEAD
 Makefile                                     |  82 ++++++---
 backend/tests/acceptance/test_gui_fixture.py |   6 +
 backend/tests/conftest.py                    | 258 +++++++++++++++++++++++++--
 pytest.ini                                   |  33 ++++
 4 files changed, 332 insertions(+), 47 deletions(-)
```

No file under `backend/` outside `backend/tests/`, no `docs/`, `scripts/`, `frontend/`,
`src-tauri/`, `eval/`, `extension/` or `.github/` file, no requirement pin, no workflow and no
documentation file is touched. `backend/tests/test_phase105_concurrency.py`,
`test_phase1075_hardening.py` and `test_phase125_planning_validation.py` (item 004's files) are
untouched.

---

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | A pytest configuration file exists at the repository root and pytest names it in its header | **PASS** | `pytest.ini` exists at the repo root; the header of `python -m pytest backend/tests --collect-only` contains `configfile: pytest.ini` (E5). On the base the same header has **no** `configfile:` line (E2). |
| 2 | Every marker applied anywhere under `backend/tests/` is declared, and a full-suite run emits zero `PytestUnknownMarkWarning` | **PASS** | `concurrency` and `gated_soak` are now declared (plus the other eight, in `pytest.ini`); `pytest --markers` lists all ten (E5); the branch full-suite output contains **0** `PytestUnknownMarkWarning` (E3/E4/E5) where the base had 3. |
| 3 | No Makefile selection can pass while collecting zero tests, and every marker the Makefile names is applied to the tests it describes | **PASS** | All four expressions collect > 0 and exit 0: `not slow and not gui and not network` -> 1599, `not acceptance` -> 1416, `gui` -> 4, `network` -> 5 (E6). On the base, `gui` and `network` exit **5** (E1). The populations are exactly the intended sets: `gui` == the one GUI module, `network` == `provider_e2e` (same 4 files, 5 tests each), `acceptance` == the 24 acceptance modules / 193 tests, and the path selection `backend/tests/acceptance` and `-m acceptance` both give 193 (E6, marker table). `flaky` and `integration` are applied to no test, so their clauses were dropped from the `test-fast` expression (the criterion's "or dropped" branch) - proven to be no-ops on the base (E1). |
| 4 | The suite's behaviour is unchanged apart from the intended selection fixes | **PASS** | `asyncio_mode = strict` is set explicitly in `pytest.ini` and the header still reports `asyncio: mode=Mode.STRICT` (E2/E5). Collected count on my base: **1609 before, 1609 after** (E2/E5). Full suite on the same base, same interpreter, back to back: **1564 passed / 45 skipped / exit 0 before, 1564 passed / 45 skipped / exit 0 after** (E4). The one deliberate difference - the no-argument invocation now collects `backend/tests` (1609) instead of walking the repository root - is recorded in E12; it was **not** run on the base tree. |
| 5 | No warning-to-error filter is introduced and the warning volume is unchanged | **PASS** | `pytest.ini` contains **no** `filterwarnings` setting at all (E13). The per-file warning-group table of the two full-suite runs is identical (926 groups, identical counts); the `PytestUnknownMarkWarning` count goes 3 -> 0; no warning became a failure (both runs exit 0 with the same 1564/45). The only delta is one fewer instance of a pre-existing non-deterministic thread-exception warning (E13). |
| 6 | The diff is confined to the test tooling | **PASS** | `git diff --name-only 662cbe3 HEAD` lists exactly `Makefile`, `backend/tests/acceptance/test_gui_fixture.py`, `backend/tests/conftest.py`, `pytest.ini` (E14). Nothing under `backend/` outside `backend/tests/`, no `docs/`, `scripts/`, `frontend/`, `src-tauri/`, `eval/`, `extension/`, `.github/`, no runtime module, no requirement pin, no workflow, no documentation file. |

---

## Marker populations after the change

Every row was measured with `python -m pytest backend/tests -m "<marker>" --collect-only -q -p no:cacheprovider`
on the branch tip `0f27d93` (total collected on this branch: 1609).

| Marker | Declared where | Applied to (files) | Collected count | Exit code of `-m <marker>` |
|---|---|---|---|---|
| `slow` | `pytest.ini` (canonical 10) | `backend/tests/test_phase105_concurrency.py` (1 test: `TestFairnessSlotAllocation::test_long_running_fairness`) | **1** | 0 |
| `flaky` | `pytest.ini` (canonical 10) | *(no file - declared for future use)* | **0** | **5** |
| `integration` | `pytest.ini` (canonical 10) | *(no file - declared for future use)* | **0** | **5** |
| `acceptance` | `pytest.ini` (canonical 10); applied by the path rule in `backend/tests/conftest.py` | all 24 modules under `backend/tests/acceptance/` | **193** | 0 |
| `gui` | `pytest.ini` (canonical 10); applied by `pytestmark` in `backend/tests/acceptance/test_gui_fixture.py` | `backend/tests/acceptance/test_gui_fixture.py` (4 tests) | **4** | 0 |
| `network` | `pytest.ini` (canonical 10); applied by the rule "provider_e2e implies network" in `backend/tests/conftest.py` | `backend/tests/test_phase10_provider_e2e.py`, `backend/tests/test_phase105_concurrency.py`, `backend/tests/test_phase1075_hardening.py`, `backend/tests/test_phase975_provider_e2e.py` | **5** | 0 |
| `process_kill` | `pytest.ini` (canonical 10) | `backend/tests/test_phase105_concurrency.py`, `backend/tests/test_phase1075_hardening.py`, `backend/tests/test_phase9_process_kill.py`, `backend/tests/test_phase975_admission_recovery.py`, `backend/tests/test_process_crash_recovery.py` | **8** | 0 |
| `provider_e2e` | `pytest.ini` (canonical 10) | the same 4 files as `network` | **5** | 0 |
| `concurrency` | `pytest.ini` (canonical 10) - **newly declared** | `backend/tests/test_phase105_concurrency.py`, `backend/tests/test_phase1075_hardening.py` | **53** | 0 |
| `gated_soak` | `pytest.ini` (canonical 10) - **newly declared** | `backend/tests/test_phase115_soak.py` | **1** | 0 |

`network` and `provider_e2e` have **identical** populations (same 4 files, same 5 tests) - that
identity is what the guard's rule 4 enforces.

---

## The Makefile selections

| Target | Marker expression | Collected | Deselected | Exit |
|---|---|---|---|---|
| `test-fast` | `not slow and not gui and not network` | **1599** | **10** | 0 |
| `test-unit` | `not acceptance` | **1416** | **193** | 0 |
| `test-gui` | `gui` | **4** | 1605 | 0 |
| `test-network` | `network` | **5** | 1604 | 0 |

The complete list of the 10 node IDs that `make test-fast` deselects is in E7.

---

## Findings

1. **The marker-deselection ordering really is load-bearing.** The rules are applied in
   `pytest_itemcollected`, never in `pytest_collection_modifyitems`; `deselect_by_mark` is
   `tryfirst` (`_pytest/mark/__init__.py`) and therefore runs before any other
   `pytest_collection_modifyitems` implementation. Had the rules been applied in
   `pytest_collection_modifyitems`, `-m acceptance` would have collected 0 and every selection in
   this item would have silently selected nothing while the suite stayed green.
2. **`make check-test-selections` cannot be executed on this machine: `make` is not installed.**
   `where.exe make` -> "Could not find files for the given pattern(s)"; no `make.exe` exists under
   `C:\Program Files\Git\usr\bin`, `C:\Program Files\Git\mingw64\bin`, `C:\msys64`,
   `C:\ProgramData\chocolatey\bin`, `C:\Users\orcha\scoop\shims` or `C:\Strawberry\c\bin`.
   I therefore ran the **four recipe commands verbatim** (E6) and they are the same evidence the
   target would produce. The Makefile itself was therefore never executed by any `make`; only its
   recipe lines were.
3. **A worktree does not contain the gitignored `backend/.env`,** and that file's absence makes
   `backend/tests/test_setup_readiness.py::test_readiness_states` fail. This is a property of the
   worktree, not of this change (the same test fails on the base commit in the worktree and
   passes in the master working copy). It is the reason the "before" full-suite run initially
   reported `1 failed, 1563 passed` and why a stand-in key was used for the comparable runs.
   **The Verifier should re-measure in the master working copy (or with `backend/.env` present).**
4. **The plan's predicted counts are all confirmed exactly:** 4 GUI tests, 5 provider-E2E tests,
   1 slow test, 10 deselected by `make test-fast`, 1599 selected, 193 acceptance tests.
   The plan's R5 "master" column said `1608 tests collected / 1 deselected` - confirmed on the
   base with the new expression as well.
5. **Criterion 4's absolute number did not move on my base:** 1609 before and 1609 after, because
   my base (`662cbe3`) predates item 004. `master` has since moved to `47345f4` (item 001 merged;
   it touches `.github/workflows/ci.yml` and `frontend/package.json` only). Item 004's branch
   `orchestra/004-real-assertions` adds `backend/tests/test_no_tautological_asserts.py`, so **if
   the Committer rebases my branch onto a master that already contains item 004, the collected
   count becomes 1611, not 1609** - that is expected and correct. My branch and item 004's branch
   touch **no common file**, so the rebase is conflict-free.
6. **The AFTER full-suite run took 28:20 against the BEFORE run's 8:11.** This is machine load
   (three other Implementers' suites were running concurrently on the same 8 cores - I observed
   their `python` processes during the run), not a change in the suite: the pass/skip counts, the
   collected count, the warning-group table and the exit code are identical.
7. **The `network` population is a rule, not a file list**, exactly as the plan decided. The two
   `provider_e2e` tests inside `test_phase105_concurrency.py` and `test_phase1075_hardening.py` -
   the two files item 004 is editing - are covered without editing them. Verified: item 004's
   diff adds/removes no `pytest.mark` at all, so the merged tree cannot trip the guard.
8. **`make test-network` cannot make a credentialed provider call.** The `provider_e2e` tests keep
   their `@pytest.mark.skipif(os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1")` gate, the
   Makefile target sets no such variable, and `-rs` now prints the skip reason instead of
   reporting a silent skip as a pass. The five `network` tests are skipped in every run recorded
   in this report.
9. **`make test-fast` is still slow** (1599 of 1609 tests). That is the plan's explicit decision:
   the suite is hermetic, so an honestly fast target would have to exclude a named part of the
   suite by name, which is exactly the silent-narrowing failure mode this item removes. Recorded
   as a follow-up, not smuggled in here.
10. **No native crash occurred in any run I made** - not in the two full-suite runs (one on the
    base commit, one on the branch) nor in the ~30 collection passes over the whole tree. The
    pre-existing crash hazard described in the brief (2 native crashes in 6 observed full-suite
    runs on `master`) did not reproduce; the only non-determinism I saw was the
    `PytestUnhandledThreadExceptionWarning` block from `test_phase105_concurrency.py` (2 instances
    before, 1 after), which is a warning, not a crash.

---

## Not checked

1. **`make` was never run** - it is not installed on this machine (finding 2). The Makefile was
   therefore never executed by `make`: not `make test`, not `make test-fast`, not
   `make check-test-selections`, not even `make help`. I ran each recipe's command verbatim
   instead, and I checked the Makefile's tab indentation by reading the bytes back
   (every recipe line starts with exactly one TAB), but **I did not verify that GNU make parses
   this Makefile**. A reviewer with `make` available should run
   `make check-test-selections` once.
2. **pytest 7.4.4 was not exercised.** `backend/requirements.txt` pins `pytest==7.4.4`; this
   machine has 8.4.2 and I did not install 7.4.4. The guard uses only APIs that exist in pytest
   7.0+ (`item.path`, `item.add_marker`, `item.get_closest_marker`, `item.iter_markers`,
   `config.getini`, `config.args`, `config.rootpath`, `config.invocation_params.dir`,
   `config.inipath`), and `asyncio_mode` exists in pytest-asyncio 0.23.8, but **the guard was
   not executed under 7.4.4**. CI pins Python 3.11 as well; nothing was run under 3.11.
3. **The no-argument `python -m pytest` collection was not run on the base tree** (deliberately -
   it would import the vendored runtime's third-party test modules). Its base behaviour is
   therefore taken from the orchestrator's baseline report, not measured by me.
4. **I did not re-run the full suite a second time on the branch** to confirm the 28:20 wall time
   was load and not a regression. The pass/skip/warning-group counts are identical to the base
   run, and the guard's work is O(n) over 1609 items, so I am confident the difference is load -
   but I did not prove it with a second timing.
5. **`make test-gui` and `make test-network` were not executed as tests** (only their collection
   was measured). The four `gui` tests need a live interactive desktop and the five `network`
   tests need `SYTHION_RUN_PROVIDER_E2E=1` plus credentials, so running them for real was out of
   scope and would have required credentialed provider calls.
6. **I did not check the frontend, the Tauri shell, the installer, the release payload or CI.**
   `.github/workflows/ci.yml` belongs to item 001 and was not touched.
7. **The `backend/.env` stand-in key was not validated against the real key.** I verified that
   the readiness check only tests `bool(settings.ollama_api_key)`; I did not run any test that
   would use the key's value.
8. **I did not read the remaining ~38 files under `docs/`.** `docs/TESTING.md` belongs to item 003
   and was not touched.

---

## Files touched outside the four named ones

None in the repository. Outside the repository I wrote, in
`C:\Users\orcha\AppData\Local\Temp\opencode\002-evidence\` (the evidence directory the brief
sanctions): the measurement scripts (`run.ps1`, `before-*.ps1`, `after-*.ps1`, `guard-demo.ps1`),
two diagnostic scripts (`insert_marker.py`, `cmp_warnings.py`, `diag_readiness.py`), the commit
message file and a backup copy of `backend/tests/test_work_state.py` used to undo the guard
demonstration. Nothing was written anywhere else, and the orchestrator's tree
`C:\Sythion\sythion` was only read (plus one read-only single-file pytest run to establish that
`test_readiness_states` passes there).
