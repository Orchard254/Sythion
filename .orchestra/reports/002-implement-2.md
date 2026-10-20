# Report: implement - 002 - attempt 2

Item: **002 - Makefile test selections and pytest configuration stop claiming what they do not do**
Role: implementer (implementer-3) - redo round 1

- **Base commit:** `662cbe38c5bf80b70794f914ff1b07ee82884043` (`master` at branching time)
- **Branch:** `orchestra/002-test-selection-truth`
- **Worktree:** `C:\Users\orcha\AppData\Local\Temp\opencode\wt-002`
- **Commits (this round):**
  - `cc03a7b` - `test: make the marker descriptions and the guard's messages true`
    (on top of round 1's `0f27d93`; history was **not** rewritten, `0f27d93` was not amended, nothing was rebased, nothing was pushed)
- **Files changed by `cc03a7b`:** `pytest.ini`, `Makefile`, `backend/tests/conftest.py`,
  `backend/tests/acceptance/test_gui_fixture.py` - the same four files as round 1, nothing else.
  `git diff --name-only 662cbe3 HEAD` -> the same four files.

> **Read this first - a second agent is working in the same worktree.**
> At 21:21 the orchestrator wrote `.orchestra/briefs/002-implement-3.md` - a *recovery
> dispatch* for this same redo round, on the premise that this session "died on
> `ECONNRESET`" after committing `cc03a7b` but before writing the report. That session is
> alive and is using **the same worktree** `wt-002`. At 21:44-21:47 it moved the root
> `pytest.ini` away, created `backend/tests/acceptance/test_gui_extra.py` and edited
> `backend/tests/conftest.py` - while one of my full-suite runs was in flight. That run
> (`r2-full-suite-run3`) is therefore **invalid** and is discarded; the details are in
> "Findings" F5. Everything else in this report was measured outside that window, and the
> remaining end-to-end demonstration was run in an isolated scratch tree so that it could
> not interfere with the other dispatch either way.

---

## What I did

### The three fixes

**Finding 1 - the `gated_soak` description was false.**
`pytest.ini` said "long-running soak tests, gated behind an explicit environment flag".
`backend/tests/test_phase115_soak.py` has **no gate at all** (no `environ`, no `getenv`, no
`skipif`, no `pytest.skip`) and its one test runs in every default suite. The description
now says what the code does, and the `concurrency` description (nit 1) now says how that
marker is actually applied. The header of `pytest.ini` was also narrowed: it used to claim
that "the marker populations are enforced by the selection-contract guard", which is only
true of three of the ten; it now lists exactly the five things the guard enforces and says
which descriptions are maintained by hand.

**Finding 2 - the fallback comment promised something the guard prevents.**
The comment said the in-file declarations are "only a fallback, so the suite still runs ...
when it is run without that file". It does not: a full-tree collection without the root
`pytest.ini` aborts with exit code 4 before any test runs. **I kept the behaviour and made
the comment true** (the reasoning is in "The three findings", row 2). The comment now states
exactly what the fallback is, that the root `pytest.ini` is REQUIRED, that a full-tree
collection without it aborts, and *why* `pytest --markers` shows none of the ten markers
without the ini. The fallback itself was narrowed: it now declares the marker **NAMES**
only, so the descriptions exist in exactly one place (`pytest.ini`) and cannot drift - the
failure mode that produced finding 1.

**Finding 3 - the `gui` expectation was hard-coded to one filename, and the message was false.**
The `gui` population is now driven by an explicit registry, `LIVE_GUI_MODULES`, in
`backend/tests/conftest.py`, and the guard checks **both directions** with messages that
state what is actually wrong. The old message - "marker 'gui' is applied to N test(s) it
does not describe" - was false for a legitimate second live-GUI module and is gone.

While rewriting the comparison I also hardened the path normalisation (`_norm` now uses
`os.path.realpath` on both sides, with a cache): the old code compared a `resolve()`d
tests-root against non-resolved item paths, which under a symlinked checkout could make one
file look like two and abort the suite with a false violation - the same class of defect as
finding 3.

### Commands run (all from the worktree; every output is in
`C:\Users\orcha\AppData\Local\Temp\opencode\002-evidence\`)

```
# BEFORE the fix (on 0f27d93, tree clean) - the reproductions
Move-Item pytest.ini pytest.ini.hidden
python -m pytest backend/tests --collect-only -q -p no:cacheprovider     # finding 2
python -m pytest --markers                                                # finding 2
python -m pytest backend/tests/test_work_state.py --collect-only -q       # finding 2
Move-Item pytest.ini.hidden pytest.ini
# a second module with `pytestmark = pytest.mark.gui` added temporarily
python -m pytest backend/tests --collect-only -q -p no:cacheprovider     # finding 3
# (probe file removed; git status clean afterwards)

# AFTER the fix (on cc03a7b) - the same probes, plus the criteria re-check
python -m pytest backend/tests --collect-only -q -p no:cacheprovider
python -m pytest backend/tests --collect-only            # header
python -m pytest backend/tests -m "<each of the ten markers>" --collect-only -q
python -m pytest backend/tests -m "not slow and not gui and not network" --collect-only -q
python -m pytest backend/tests -m "not acceptance" --collect-only -q
python -m pytest backend/tests/acceptance --collect-only -q
python -m pytest backend/tests/test_work_state.py --collect-only -q
python -m pytest --collect-only -q
python -m pytest --markers
python -m pytest backend/tests -q -p no:cacheprovider                    # full suite
<pinned venv> -m pytest backend/tests --collect-only -q                  # pytest 7.4.4
<pinned venv> -m pytest tests --collect-only -q     (cwd = backend)        # the CI shape
```

---

## Evidence

### E1 - Finding 1: the `gated_soak` description was false, and is now true

**The code has no gate.** Grep over the whole file (no output):

```
> Select-String -Path backend/tests/test_phase115_soak.py -Pattern "environ|getenv|skip|SYTHION"
(no output)
```

**It runs in the default suite:**

```
> python -m pytest backend/tests/test_phase115_soak.py -q -p no:cacheprovider --durations=3
============================= slowest 3 durations =============================
1.27s call     backend/tests/test_phase115_soak.py::test_learning_soak_mixed_workload
(2 durations < 0.005s hidden.  Use -vv to show these durations.)
1 passed, 482 warnings in 1.94s
```

**BEFORE** (`pytest.ini` at `0f27d93`):

```
    gated_soak: long-running soak tests, gated behind an explicit environment flag
```

**AFTER** (`pytest.ini` at `cc03a7b`) - and this is what `pytest --markers` prints now:

```
@gated_soak: the synthetic learning-soak workload in backend/tests/test_phase115_soak.py (one test); despite the name it is NOT gated by an environment flag and runs in the default suite
```

(The literal `pytest --markers` output is `@pytest.mark.gated_soak: ...`; the line above is
the ini entry.)

Two related truthfulness fixes in the same file:

```
BEFORE: concurrency: tests that exercise the shared runtime under concurrent load
AFTER:  concurrency: applied at module level to backend/tests/test_phase105_concurrency.py and backend/tests/test_phase1075_hardening.py (53 tests in total, not all of them concurrency tests)
```

and the header claim, which used to overstate the guard:

```
BEFORE: The marker populations are enforced by the selection-contract guard in
        backend/tests/conftest.py, which aborts a full-tree collection if a marker
        is applied to a set of tests it does not describe.
AFTER:  What the selection-contract guard in backend/tests/conftest.py enforces, on
        a full-tree collection (it aborts the run with exit code 4):
          * every marker carried by a collected test is a declared marker;
          * the `acceptance` population is exactly the tests under
            backend/tests/acceptance/;
          * the `gui` population is exactly the tests of the modules registered in
            LIVE_GUI_MODULES (backend/tests/conftest.py), in both directions;
          * the `network` population is exactly the `provider_e2e` population;
          * the configuration in use is this file, and it declares all ten
            canonical markers.
        The guard does NOT check the other markers' populations - the descriptions
        of `slow`, `flaky`, `integration`, `process_kill`, `provider_e2e`,
        `concurrency` and `gated_soak` are maintained by hand, so a change to the
        code behind them has to be reflected here.
```

### E2 - Finding 2: the fallback comment vs the behaviour (BEFORE and AFTER)

**BEFORE** (`0f27d93`), with the root `pytest.ini` renamed away:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
ERROR: selection-contract violation in the backend test tree:
  - no pytest configuration file was used; expected pytest.ini at the repository root
  - the root configuration does not declare the marker(s): acceptance, concurrency, flaky, gated_soak, gui, integration, network, process_kill, provider_e2e, slow
...
1609 tests collected in 76.75s (0:01:16)
EXITCODE=4
```

```
$ python -m pytest --markers            # with the ini hidden
  pytest.mark.slow -> 0
  pytest.mark.flaky -> 0
  pytest.mark.integration -> 0
  pytest.mark.acceptance -> 0
  pytest.mark.gui -> 0
  pytest.mark.network -> 0
  pytest.mark.process_kill -> 0
  pytest.mark.provider_e2e -> 0
  pytest.mark.concurrency -> 0
  pytest.mark.gated_soak -> 0
  (plugin/builtin markers shown: 10)
EXITCODE=0

$ python -m pytest backend/tests/test_work_state.py --collect-only -q -p no:cacheprovider
57 tests collected in 3.12s
EXITCODE=0
```

**AFTER** (`cc03a7b`), the same probes:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider     # ini hidden
...
ERROR: selection-contract violation in the backend test tree:
  - no pytest configuration file was used; the repository-root pytest.ini is required - it defines the marker vocabulary and the test tree
  - the configuration in use does not declare the marker(s): acceptance, concurrency, flaky, gated_soak, gui, integration, network, process_kill, provider_e2e, slow
...
1609 tests collected in 60.59s (0:01:00)
EXITCODE=4

$ python -m pytest --markers            # with the ini hidden
  pytest.mark.slow -> 0
  ... (all ten: 0) ...
  (plugin/builtin markers shown: 10)
EXITCODE=0

$ python -m pytest backend/tests/test_work_state.py --collect-only -q -p no:cacheprovider
EXITCODE=0

$ python -m pytest backend/tests/test_phase115_soak.py --collect-only -q -p no:cacheprovider
1 test collected in 0.62s
  PytestUnknownMarkWarning count: 0
EXITCODE=0
```

**Why `pytest --markers` shows none of the ten without the ini** (the question the brief
asks me to answer): `--markers` prints the registered markers and exits **without
collecting anything**. `backend/tests/conftest.py` is not the rootdir conftest - it is
registered as a plugin only when pytest *collects a path under `backend/tests/`*. Its
`pytest_configure` hook is therefore never called in the `--markers` state, so the fallback's
`addinivalue_line("markers", ...)` calls never happen and only the plugin/builtin markers
are listed. The same two measurements show the fallback is not dead code: in the no-ini
state `pytest --markers` shows **no** `gated_soak`, yet the partial run of
`test_phase115_soak.py` produces **0** `PytestUnknownMarkWarning` - the fallback declared
it, because that run does load the conftest.

**The comment now matches the behaviour** (`backend/tests/conftest.py`):

```
# The root pytest.ini is REQUIRED. A full-tree collection without it is
# aborted by the selection-contract guard below (rule 5) before any test
# runs, because the point of the guard is that the repository has exactly one
# place where the selection contract is written down.
#
# The declarations below are a declaration-only fallback, and they are
# exactly as much as they look:
#   * they carry the marker NAMES only - the descriptions are not duplicated
#     here, so there is no second copy to drift;
#   * they take effect only once this conftest is loaded, i.e. once pytest
#     collects something under backend/tests. That means:
#       - a PARTIAL run inside backend/tests (one file, one directory) gets
#         the marker vocabulary without pytest.ini, and therefore keeps its
#         marker selections and its unknown-mark warnings correct;
#       - `pytest --markers` does NOT show these markers when the test tree
#         has not been collected, because --markers never loads this
#         conftest;
#       - a FULL-TREE collection without pytest.ini aborts with exit code 4
#         (rule 5). The fallback does not, and cannot, make the suite
#         runnable without the root configuration.
```

### E3 - Finding 3: the `gui` rule (BEFORE and AFTER, both directions)

**BEFORE** (`0f27d93`), with a second module that legitimately declares
`pytestmark = pytest.mark.gui` added to the acceptance directory:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
ERROR: selection-contract violation in the backend test tree:
  - marker 'gui' is applied to 1 test(s) it does not describe, e.g. backend/tests/acceptance/test_gui_extra.py::test_extra_gui_probe
...
1610 tests collected in 80.23s (0:01:20)
EXITCODE=4
```

The message is **false**: the module declares the marker deliberately and does describe
live-GUI work.

**AFTER** (`cc03a7b`) - the same probe in the real tree:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
ERROR: selection-contract violation in the backend test tree:
  - module 'backend/tests/acceptance/test_gui_extra.py' has 1 test(s) carrying the `gui` marker but is not registered as a live-GUI module - add it to LIVE_GUI_MODULES in backend/tests/conftest.py
...
1610 tests collected in 63.39s (0:01:03)
EXITCODE=4
```

The same probe, with the module **registered** (the legitimate two-line change):

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
1610 tests collected in 67.26s (0:01:07)
EXITCODE=0
```

And a **stray per-test** `@pytest.mark.gui` in a module that did not opt in:

```
$ python -m pytest backend/tests --collect-only -q -p no:cacheprovider
...
ERROR: selection-contract violation in the backend test tree:
  - module 'backend/tests/test_work_state.py' has 1 test(s) carrying the `gui` marker but is not registered as a live-GUI module - add it to LIVE_GUI_MODULES in backend/tests/conftest.py
...
EXITCODE=4
```

**The other direction** - a *registered* live-GUI module that stops carrying the marker -
needs the GUI module edited, which would have interfered with the recovery dispatch running
in the same worktree, so it was run in an **isolated copy** of the committed tooling
(`scratch-gui/`, built from `git show HEAD:...`, with the real `backend` package on
`PYTHONPATH`):

```
$ python -m pytest "<scratch>/backend/tests" --collect-only -q
ERROR: selection-contract violation in the backend test tree:
  - module 'acceptance/test_gui_fixture.py' is registered as a live-GUI module but 2 of its 2 collected test(s) do not carry the `gui` marker, e.g. backend/tests/acceptance/test_gui_fixture.py::test_gui_one
3 tests collected in 0.03s
EXITCODE=4
```

The same scratch tree, clean and with the second module registered:

```
$ python -m pytest "<scratch>/backend/tests" --collect-only -q
3 tests collected in 0.03s
EXITCODE=0                      # the tree as committed: the guard is silent

$ python -m pytest "<scratch>/backend/tests" --collect-only -q     # second module registered
4 tests collected in 0.06s
EXITCODE=0                      # a legitimate second live-GUI module now passes
```

Both directions are now true, and neither is the old "it does not describe" claim.

### E4 - The criteria re-check on `cc03a7b`

```
r2-collect-all        1609 tests collected in 83.88s (0:01:23)          EXITCODE=0
r2-header             configfile: pytest.ini / asyncio: mode=Mode.STRICT / collected 1609 items
r2-m-testfast         1599/1609 tests collected (10 deselected)          EXITCODE=0
r2-m-notaccept        1416/1609 tests collected (193 deselected)         EXITCODE=0
r2-acceptance-path    193 tests collected in 3.34s                       EXITCODE=0
r2-partial            57 tests collected in 1.67s                        EXITCODE=0
r2-noarg              1609 tests collected in 71.09s (0:01:11)           EXITCODE=0
PytestUnknownMarkWarning in r2-collect-all: 0
```

```
============================= test session starts =============================
platform win32 -- Python 3.14.7, pytest-8.4.2, pluggy-1.6.0
rootdir: C:\Users\orcha\AppData\Local\Temp\opencode\wt-002
configfile: pytest.ini
plugins: anyio-4.15.1, asyncio-0.23.8
asyncio: mode=Mode.STRICT
collected 1609 items
```

**The CI shape under the PINNED toolchain** (pytest 7.4.4 / pytest-asyncio 0.23.3, the
versions in `backend/requirements.txt`):

```
r2-pinned-root      "<venv-pinned>/python.exe" -m pytest backend/tests --collect-only -q
                    1609 tests collected in 60.05s (0:01:00)            EXITCODE=0
r2-pinned-cishape   "<venv-pinned>/python.exe" -m pytest tests --collect-only -q   (cwd = backend)
                    1609 tests collected in 56.23s                       EXITCODE=0
```

**The full suite**, base vs. this branch, same worktree, same interpreter:

| Run | Tree | Result | Warnings | Wall | Exit |
|---|---|---|---|---|---|
| `before-full-suite-env.txt` | base `662cbe3` | **1564 passed, 45 skipped** | 406258 | 491.67 s | **0** |
| `after-full-suite.txt` | round 1 `0f27d93` | **1564 passed, 45 skipped** | 406269 | 1700.83 s | **0** |
| `r2-full-suite-run2.txt` | round 2 `cc03a7b` | **1564 passed, 45 skipped** | 406249 | 445.29 s | **0** |
| `r2-full-suite.txt` | round 2 `cc03a7b` | 2 failed, 1562 passed, 45 skipped | 406257 | 448.34 s | 1 |

The one non-green run is the pre-existing onboarding flake, characterised in F4 below; it
did not reproduce in any other run of the same commit. Four full-suite runs of `cc03a7b`
were made: this one (2 failures), `r2-full-suite-run2` (**1564 passed / 45 skipped /
exit 0**), one run with the HTTP diagnostic plugin loaded (**1564 passed / 45 skipped**;
its exit code was not captured because the output was piped through a filter), and
`r2-full-suite-run3`, which was invalidated by the interference described in F5.

**Warning volume**, base vs. this branch, per-file warning groups:

```
group count before/after: 926 926
total before/after: 385772 385772
identical per-group counts: True
```

**No filter-to-error filter** - the only hits for `filterwarnings|addopts|strict` in
`pytest.ini` are the comment documenting their absence and `asyncio_mode = strict`.

**The diff is confined to the test tooling:**

```
$ git diff --name-only 662cbe3 HEAD
Makefile
backend/tests/acceptance/test_gui_fixture.py
backend/tests/conftest.py
pytest.ini
```

---

## The three findings

| # | Finding | What I changed | Evidence that it is now true |
|---|---|---|---|
| 1 | `pytest.ini`: the `gated_soak` description claimed an environment gate that does not exist | Reworded the description to what the code does ("despite the name it is NOT gated by an environment flag and runs in the default suite"); reworded the `concurrency` description to say how the marker is applied and where; narrowed the header claim to the five things the guard actually enforces | E1 - the file has no `environ`/`getenv`/`skip` at all, the test passes in 1.27 s in the default suite, and `pytest --markers` now prints the true description |
| 2 | `backend/tests/conftest.py`: the comment promised the suite "still runs" without the root `pytest.ini`; it does not | **Kept the behaviour, made the comment true.** The root `pytest.ini` stays REQUIRED (rule 5 unchanged) - relaxing it would have given up the Reviewer's own pinned invariant (e) "the configuration in use is the repository-root `pytest.ini`" and the item's central property, that the repository has exactly ONE place where the selection contract lives. The comment now states the real scope of the fallback, that the ini is required, that a full-tree collection without it aborts, and why `pytest --markers` shows none of the ten. The fallback was narrowed to the marker NAMES so the descriptions live in exactly one place and cannot drift | E2 - BEFORE and AFTER full-tree collections without the ini (exit 4, zero tests run, the guard's message), `pytest --markers` with and without the ini, and the partial run that shows the fallback is not dead code (0 `PytestUnknownMarkWarning` for `gated_soak` with the ini hidden) |
| 3 | `backend/tests/conftest.py`: the `gui` expectation was one hard-coded filename and the failure message was false | Replaced the hard-coded `_NORM_GUI_MODULE` with an explicit `LIVE_GUI_MODULES` registry and rewrote the rule so it checks **both** directions: a module whose tests carry `gui` but that is not registered, and a registered module whose tests do not all carry the marker. Every message now names the module and states what is wrong. The `acceptance` and `network` messages were rewritten the same way ("outside backend/tests/acceptance/", "that are not provider_e2e tests"). The path normalisation was hardened (`realpath` on both sides) so a symlinked checkout cannot produce a false violation | E3 - the same probe before (false message, exit 4) and after (true message, exit 4), the registered case (exit 0), the stray per-test marker (exit 4) and the other direction (exit 4) |

---

## Acceptance criteria (re-checked after the fix)

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | A pytest configuration file exists at the repository root and pytest names it in its header | **PASS** | `pytest.ini` at the repo root; the header of `python -m pytest backend/tests --collect-only` contains `configfile: pytest.ini` (E4). On the base commit the same header has no `configfile:` line at all (round-1 report E2). |
| 2 | Every marker applied under `backend/tests/` is declared; a full-suite run emits zero `PytestUnknownMarkWarning` | **PASS** | `PytestUnknownMarkWarning` count in the collection output: **0** (E4). `pytest --markers` lists all ten canonical markers with the `pytest.ini` wording (E1/E4) - including the two that used to be undeclared, `concurrency` and `gated_soak`. |
| 3 | No Makefile selection can pass while collecting zero tests; every marker the Makefile names is applied to the tests it describes | **PASS** | All four expressions collect > 0 and exit 0: `not slow and not gui and not network` -> **1599** selected / **10** deselected, `not acceptance` -> **1416** / **193**, `gui` -> **4**, `network` -> **5** (E4). On the base, `gui` and `network` exit **5**. The populations are exactly the intended sets: `gui` == the one registered live-GUI module, `network` == `provider_e2e` (same 4 files, 5 tests), `acceptance` == the 24 acceptance modules / 193 tests, and the path selection `backend/tests/acceptance` and `-m acceptance` both give 193. `flaky` and `integration` are applied to no test, so their clauses were dropped from `test-fast` (the criterion's "or dropped" branch). |
| 4 | The suite's behaviour is unchanged apart from the intended selection fixes | **PASS** | `asyncio_mode = strict` is set explicitly and the header still reports `asyncio: mode=Mode.STRICT` (E4). Collected count on my base: **1609 before, 1609 after** (E4). Full suite on the same base, same interpreter: **1564 passed / 45 skipped / exit 0** on the base and **1564 passed / 45 skipped / exit 0** on this branch (E4 table). The one deliberate difference - the no-argument invocation now collects `backend/tests` (1609) instead of walking the repository root - is unchanged from round 1 and was **not** run on the base tree. |
| 5 | No warning-to-error filter is introduced and the warning volume is unchanged | **PASS** | `pytest.ini` contains **no** `filterwarnings` setting (E4). The per-file warning-group table of the base run and this branch's run is identical (926 groups, identical counts, identical totals). No warning became a failure. |
| 6 | The diff is confined to the test tooling | **PASS** | `git diff --name-only 662cbe3 HEAD` lists exactly `Makefile`, `backend/tests/acceptance/test_gui_fixture.py`, `backend/tests/conftest.py`, `pytest.ini` (E4). Nothing under `backend/` outside `backend/tests/`, no `docs/`, `scripts/`, `frontend/`, `src-tauri/`, `eval/`, `extension/`, `.github/`, no requirement pin, no workflow, no documentation file. |

---

## Marker populations and Makefile selections (measured on `cc03a7b`)

| Marker | Applied to (files) | Collected | Exit code of `-m <marker>` |
|---|---|---|---|
| `slow` | `backend/tests/test_phase105_concurrency.py` (1 test) | **1** | 0 |
| `flaky` | *(no file - declared for future use)* | **0** | **5** |
| `integration` | *(no file - declared for future use)* | **0** | **5** |
| `acceptance` | all 24 modules under `backend/tests/acceptance/` | **193** | 0 |
| `gui` | `backend/tests/acceptance/test_gui_fixture.py` (4 tests) | **4** | 0 |
| `network` | `test_phase10_provider_e2e.py`, `test_phase105_concurrency.py`, `test_phase1075_hardening.py`, `test_phase975_provider_e2e.py` | **5** | 0 |
| `process_kill` | 5 files | **8** | 0 |
| `provider_e2e` | the same 4 files as `network` | **5** | 0 |
| `concurrency` | `test_phase105_concurrency.py`, `test_phase1075_hardening.py` | **53** | 0 |
| `gated_soak` | `backend/tests/test_phase115_soak.py` | **1** | 0 |

```
slow           1/1609 tests collected (1608 deselected)          EXITCODE=0
flaky          no tests collected (1609 deselected)               EXITCODE=5
integration    no tests collected (1609 deselected)               EXITCODE=5
acceptance     193/1609 tests collected (1416 deselected)         EXITCODE=0
gui            4/1609 tests collected (1605 deselected)          EXITCODE=0
network        5/1609 tests collected (1604 deselected)          EXITCODE=0
process_kill   8/1609 tests collected (1601 deselected)          EXITCODE=0
provider_e2e   5/1609 tests collected (1604 deselected)          EXITCODE=0
concurrency    53/1609 tests collected (1556 deselected)         EXITCODE=0
gated_soak     1/1609 tests collected (1608 deselected)          EXITCODE=0
```

| Target | Marker expression | Collected | Deselected | Exit |
|---|---|---|---|---|
| `test-fast` | `not slow and not gui and not network` | **1599** | **10** | 0 |
| `test-unit` | `not acceptance` | **1416** | **193** | 0 |
| `test-gui` | `gui` | **4** | 1605 | 0 |
| `test-network` | `network` | **5** | 1604 | 0 |
| `test-acceptance` | (path) `backend/tests/acceptance` | **193** | - | 0 |

---

## The two nits

**Nit 1 - the `concurrency` description was imprecise.** Decided: the description now says
how the marker is applied and where, in the style the `gui` and `network` entries already
use - "applied at module level to `backend/tests/test_phase105_concurrency.py` and
`backend/tests/test_phase1075_hardening.py` (53 tests in total, not all of them concurrency
tests)". The brief's phrasing ("the marker is applied per test (53 times)") is a
misreading: the marker is applied at **module** level; 53 is the number of tests that
inherit it. The measurement confirms the population is exactly every test of those two
modules (53), including the `provider_e2e` and `process_kill` tests that live in them, so
the description now says so instead of claiming they are all concurrency tests.

**Nit 2 - `check-test-selections` is not wired into CI.** `ci.yml` is item 001's file and
item 001 is merged; I did **not** edit it. What the integration should be, for the
orchestrator to queue: CI's `backend-tests` job runs raw pytest (there is no `make` in the
runner), so the gate should be added as **four `python -m pytest ... --collect-only -q`
steps** - the same four expressions the Makefile target uses - rather than by installing
`make`. Cost: four extra full collections per pipeline run (~60-90 s each on this machine,
measured here), i.e. ~4-6 minutes added to a job that currently takes ~8 minutes. If the
cost is not wanted, the cheaper equivalent is a single step that collects the whole tree
once and asserts the four counts, which needs a small script; that is a separate item.

---

## Findings

**F1 - The `gated_soak` MARKER NAME is now the only remaining false statement about it.**
The marker is called `gated_soak`, but nothing is gated. I did not rename it: renaming a
marker changes the vocabulary in `pytest.ini`, in the conftest and in
`backend/tests/test_phase115_soak.py`, and the description now says explicitly that the
name is historical. A rename is a follow-up, not something to smuggle into this item.

**F2 - `backend/tests/test_phase115_soak.py:1` still says "Gated soak test - realistic
synthetic workload for learning substrate."** That docstring is as false as the description
was. The file is **not** one of the four files this item may write, so I did not touch it;
it is reported here instead. One line in that file fixes it.

**F3 - The `gui` property is now expressed as an explicit registry, not derived from the
code.** A guard cannot verify "this test needs a live desktop" - only the author knows.
The registry makes the claim explicit and reviewable ("this module needs a live desktop"),
and the guard then checks that the claim and the marker agree in both directions. The cost
is that adding a second live-GUI module is a two-line change (the marker in the module, the
entry in `LIVE_GUI_MODULES`) instead of one; the benefit is that the message is true and
that the "which modules need a live desktop" list lives in one reviewable place. The
alternative - deriving the expectation from where the marker is declared - would make the
rule vacuous and would lose the "a module needs a live desktop but does not carry the
marker" direction entirely, which the brief asked for.

**F4 - A pre-existing flake in `backend/tests/acceptance/test_onboarding.py` (not caused by
this change).** One full-suite run of `cc03a7b` failed two tests that pass in every other
run:

```
FAILED backend/tests/acceptance/test_onboarding.py::test_select_model_persists_exact_returned_id
FAILED backend/tests/acceptance/test_onboarding.py::test_select_unknown_model_rejected
2 failed, 1562 passed, 45 skipped, 406257 warnings in 448.34s (0:07:28)
EXITCODE=1
```

with `assert 503 == 200` / `assert 503 == 400` - the endpoint
`POST /api/v1/onboarding/models` returns 503, which `backend/api/routes.py:2031-2034`
raises when `provider.list_models()` throws. The test stubs the **process-global** adapter's
`_client` with a mock transport (`backend/tests/acceptance/test_onboarding.py:52-63`), but
`OllamaCloudAdapter._get_client()` (`backend/adapters/ollama_adapter.py:84-106`) discards
that stub unless the adapter's remembered `_client_loop` is the loop that is currently
running - state that other tests in the session also touch. Demonstrated directly (script
outside the repository):

```
fresh adapter, new loop:                       stub kept=False  transport=AsyncHTTPTransport
adapter that already saw this loop:            stub kept=True   transport=MockTransport
```

So the test passes when the mock survives and fails (503, after a real outbound call is
attempted) when it does not. It is **not** related to this diff: the failing file is not in
`git diff --name-only`, the same commit passed 1564/45 in three other runs, and the
acceptance suite on its own passes (`191 passed, 2 skipped`). It is a pre-existing
order/state-dependent test; the Reviewer's own baseline report records the same class of
non-determinism in this suite.

**F5 - A second agent is working in my worktree, and it invalidated one of my runs.** At
21:21 the orchestrator wrote `.orchestra/briefs/002-implement-3.md`, a recovery dispatch
for this same round, believing this session had died on `ECONNRESET` after committing
`cc03a7b`. That session is alive and uses the same worktree. Between 21:44 and 21:47 it
moved the root `pytest.ini` away, created `backend/tests/acceptance/test_gui_extra.py` and
edited `backend/tests/conftest.py` - while my full-suite run `r2-full-suite-run3` was
collecting. That run therefore aborted with

```
ERROR: selection-contract violation in the backend test tree:
  - no pytest configuration file was used; the repository-root pytest.ini is required - it defines the marker vocabulary and the test tree
  - the configuration in use does not declare the marker(s): acceptance, concurrency, ...
EXITCODE=4
```

and is **discarded** (it is not evidence of anything except the interference). The
remaining end-to-end demonstration (the "registered module without the marker" direction)
was therefore run in an isolated scratch tree so that it could not interfere with the other
dispatch either. **Final state of the worktree, checked after the other dispatch restored
its own probe edits:** `git status --short` is **empty** and `HEAD` is `cc03a7b` - the tree
is clean and the committed state is correct and complete.

**F6 - The full suite's wall time varies by an order of magnitude on this machine** (445 s
in a quiet run, 1700 s when three other Implementers' suites were running concurrently).
The counts do not move; only the clock does. No full-suite number in this report should be
read as a runtime claim.

---

## Not checked

1. **`make` was never executed** - it is not installed on this machine (no `make.exe` under
   `C:\Program Files\Git\usr\bin`, `...\Git\mingw64\bin`, `C:\msys64`,
   `C:\ProgramData\chocolatey\bin`, `C:\Users\orcha\scoop\shims` or `C:\Strawberry\c\bin`;
   no WSL). The four `check-test-selections` recipe commands were run verbatim instead
   (E4). **GNU make has still never parsed this Makefile**; a reviewer with `make` should
   run `make check-test-selections` once.
2. **pytest 7.4.4 was exercised only for collection** (the pinned venv the Reviewer left
   behind: `1609 collected, exit 0` from the repo root and in the CI shape). I did not run
   the full suite under 7.4.4, and nothing was run under Python 3.11 (CI's version).
3. **The `gui` tests and the `provider_e2e` tests were not run for real** - they need a
   live interactive desktop and `SYTHION_RUN_PROVIDER_E2E=1` plus credentials. I read their
   gates instead; no credentialed provider call was made.
4. **The no-argument `python -m pytest` collection was not run on the base tree** (it would
   import the vendored runtime's third-party test modules). On the branch it collects 1609
   (E4).
5. **`docs/TESTING.md` was not touched** - item 003 owns it. It still describes the old
   marker and target semantics and must be updated after this item lands.
6. **The pre-existing `backend/.env` failure was not addressed** (item 006): a git
   worktree does not contain the gitignored `backend/.env`, so
   `backend/tests/test_setup_readiness.py::test_readiness_states` fails there. For the
   comparable full-suite runs I used the non-secret stand-in
   `SYTHION_OLLAMA_API_KEY=sythion-local-dummy-key-not-a-real-credential`; the real
   credential was never copied anywhere.
7. **The other ~38 files under `docs/`** were not read.
8. **I did not read the other Implementers' branches** for conflicts beyond checking that
   item 001's and item 004's branches touch no file of mine (they do not).

---

## Files touched outside the four named ones

None in the repository. Outside it, in
`C:\Users\orcha\AppData\Local\Temp\opencode\002-evidence\`: the measurement and probe
scripts, the two diagnostic scripts (`insert_marker.py`, `cmp_warnings.py`,
`diag_readiness.py`, `diag_onboarding_flake.py`, `diag_http_plugin.py`), the commit message
file, byte-copy backups used to undo every probe, and the isolated `scratch-gui/` tree.
Nothing was written anywhere else; the orchestrator's tree `C:\Sythion\sythion` was only
read (plus one read-only single-file pytest run in round 1 to establish that
`test_readiness_states` passes there).
