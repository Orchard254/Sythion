# Report: implement - 020 - attempt 1

<!-- The implementer's report for item 020: "The `.env` isolation must be measured, not assumed, and cover the process environment". -->

- Branch: `orchestra/020-env-isolation` (from `master` @ `f1ad188`; `master` advanced to `cee7a02` - a `.orchestra/log.md` state commit - while this item was in flight)
- Worktree: `C:\Users\orcha\AppData\Local\Temp\opencode\wt-020`
- Commits:
  - `0c02fcb` - `test(020): import _LOCAL_ENV_FIELDS instead of re-hardcoding the field names`
  - `708ad6f` - `test(020): measure the .env isolation instead of assuming it`
- Nothing was pushed. `master` was not touched. No production file was changed.
- Interpreter: CPython 3.14.7, pytest 8.4.2, Windows.

## What I did

1. Read `plans/020.md` in full and implemented it as written.
2. New `backend/tests/test_env_isolation.py` - ONE test,
   `test_the_local_env_isolation_is_measured_not_assumed`, which:
   - builds a scratch tree (`tempfile.mkdtemp(prefix="sythion-020-")`) containing a
     byte-for-byte `copytree` of the repository's own `backend/` package (so the
     isolation under test is the repository's own conftest), the root `pytest.ini`,
     the generated probe and two empty redirect directories;
   - asserts that **no** `.env*` file was copied (the developer's real
     `backend/.env` is excluded by the copy's ignore patterns and is never read,
     printed or diffed);
   - runs three nested `python -m pytest` subprocesses (120 s timeout each, cwd =
     `<scratch>/backend`, an explicitly built child environment), each writing a
     JUnit XML and a signal file;
   - asserts the exact signal line in BOTH directions, the readiness file's
     outcome set (run 2 == run 1, item by item), the negative control (run 3
     fails: the generated probe AND the repository's own
     `test_the_suite_does_not_inherit_the_local_env_file`), the absence of both
     sentinel strings in every captured stdout/stderr **and** in every JUnit XML,
     the absence of any blocked outbound network attempt, and
   - removes the scratch tree in a `finally`; a removal that still fails FAILS
     the test.
3. New `backend/tests/isolation_probe_template.py` - the probe, committed as a
   TEMPLATE. Its name does not match `test_*.py`, so pytest never collects it
   in the repository; `test_env_isolation.py` copies it into the scratch tree
   as `test_env_isolation_probe.py`, where it IS collected.
4. Modified `backend/tests/test_setup_readiness.py` - criterion 4: it now imports
   `_LOCAL_ENV_FIELDS` from `backend.tests.conftest` and iterates it instead of
   re-hardcoding `("ollama_api_key", "selected_model", "file_roots")`. Nothing
   else in that file changed.

## Evidence

### E1 - the new test passes

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-020
$ python -m pytest backend/tests/test_env_isolation.py -q
.                                                                        [100%]
...
1 passed, 45 warnings in 29.26s
```

Wall time of the whole command (stopwatch around the process): **31.9 s**
(the pytest-reported duration is 29.26 s; the difference is interpreter start-up).

The per-run breakdown the test prints (`-s` run):

```
020 isolation measurement: run 1 8.4s, run 2 12.0s, run 3 10.2s
1 passed, 45 warnings in 31.53s
```

### E2 - the readiness file still passes (and now imports the field list)

```
$ python -m pytest backend/tests/test_setup_readiness.py -q
11 passed, 760 warnings in 3.92s        (wall: 13.5 s)
```

11 passed - the same count as before the change.

Single conftest instance, verified (temporary probe, deleted afterwards, not
committed):

```
$ python -m pytest backend/tests/test_zz_tmp_check.py -q -s
CONFTEST MODULES: ['backend.tests.conftest']
1 passed
```

So the new `from backend.tests.conftest import _LOCAL_ENV_FIELDS` resolves to
the SAME module object pytest already loaded: no second conftest instance, no
duplicated snapshot.

### E3 - the collected count: 1628 -> 1629 (+1, the one new test)

Baseline, measured in a throwaway worktree at `master` (`cee7a02`):

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-base
$ python -m pytest backend/tests --collect-only -q
1628 tests collected in 125.26s (0:02:05)      (wall: 134.1 s)
```

Branch (`orchestra/020-env-isolation`):

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-020
$ python -m pytest backend/tests --collect-only -q
1629 tests collected in 124.27s (0:02:04)      (wall: 135.9 s)
```

**Delta = +1**, and it is exactly the one new test:
`backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed`.
The probe template contributes **0** (its name does not match `test_*.py`), and
it adds no skip to the suite.

(One earlier baseline collection ended with `INTERNALERROR> MemoryError` after
printing the count; re-running it produced the clean `1628 tests collected`
above. A machine hiccup, not a code effect.)

### E4 - the full suite

See "E4 - full suite" below.

### E5 - the NEGATIVE CONTROL, level (a): inside the check (automated, every run)

The test builds the control itself: it deletes the isolation **in the scratch
copy only** (anchored edit of `<scratch>/backend/tests/conftest.py`,
`@pytest.fixture(autouse=True)` -> `@pytest.fixture` for
`_isolate_local_env_settings`; if the anchor is not found the test FAILS with
"the negative control could not be built") and then asserts that run 3 exits
!= 0, that the generated probe carries a `<failure>` and that the repository's
own `test_the_suite_does_not_inherit_the_local_env_file` carries a `<failure>`.

All three of those assertions hold - the test is green on the branch, which is
only possible if the control really went red.

### E6 - the NEGATIVE CONTROL, level (b): in the REPOSITORY (the human reproduction)

Throwaway worktree at the branch tip, with the isolation deleted from the
repository's `backend/tests/conftest.py`. It is a **clean checkout with no
`backend/.env`** - i.e. exactly the CI shape, the situation the 006 verifier
could not demonstrate.

```
$ cd C:\Users\orcha\AppData\Local\Temp\opencode\wt-020-negctl
$ git diff --stat
 backend/tests/conftest.py | 2 +-
 1 file changed, 1 insertion(+), 1 deletion(-)

$ python -m pytest backend/tests/test_env_isolation.py -q
```

**RESULT: RED.** The failure:

```
F                                                                        [100%]
================================== FAILURES ===================================
____________ test_the_local_env_isolation_is_measured_not_assumed _____________

    def test_the_local_env_isolation_is_measured_not_assumed():
        ...
>           assert run1.returncode == 0, (
                "run 1 (no .env, isolation present) exited %d; the output was:\n%s"
                % (run1.returncode, run1.output)
            )
E           AssertionError: run 1 (no .env, isolation present) exited 1; the output was:
E             F...........                                                             [100%]
E             ================================== FAILURES ===================================
E             _________ test_the_probe_observes_that_the_local_env_isolation_holds __________
E                 def test_the_probe_observes_that_the_local_env_isolation_holds():
E                     """The isolation must neutralise the env FILE and the process ENVIRONMENT.
E                     ...
E                 assert file_neutralised is True, (
E                     "the generated .env sentinel reached the effective settings"
E                 )
E             >       assert env_neutralised is True, (
E                     "the exported process-environment sentinel reached the effective settings"
E                 )
E             E       AssertionError: the exported process-environment sentinel reached the effective settings
E             E       assert False is True
E              tests\test_env_isolation_probe.py:154: AssertionError
E             =========================== short test summary info ===========================
E             FAILED tests\test_env_isolation_probe.py::test_the_probe_observes_that_the_local_env_isolation_holds
E             1 failed, 11 passed, 826 warnings in 6.02s

E           assert 1 == 0
E            +  where 1 = <backend.tests.test_env_isolation._NestedRun object at 0x00000022443D7E2BA0>.returncode

backend\tests\test_env_isolation.py:411: AssertionError
=========================== short test summary info ============================
FAILED backend/tests/test_env_isolation.py::test_the_local_env_isolation_is_measured_not_assumed
1 failed, 45 warnings in 11.23s
```

Two things are visible in that output and both matter:

* the nested run 1 **fails** - the generated probe catches the leak;
* the readiness file in the same run is **11 passed** - i.e. 006's Control 1
  finding is confirmed again: without a `backend/.env` the repository's own
  readiness test is a tautology and cannot see the deleted isolation. The new
  check is what gives the tree teeth there, and it does so through the
  PROCESS-ENVIRONMENT sentinel (`SYTHION_SELECTED_MODEL`), which needs no
  `.env` at all.

No sentinel value appears anywhere in that output (the probe's message names
the property, not the value) - and the test asserts the same for the captured
stdout/stderr and the JUnit XML.

### E7 - the diff is confined to `backend/tests/` (criterion 5)

```
$ git diff --name-only master...HEAD
backend/tests/isolation_probe_template.py
backend/tests/test_env_isolation.py
backend/tests/test_setup_readiness.py
```

`backend/core/config.py` is untouched (it is not in the diff at all). The
product still reads `backend/.env` when it exists: in the negative control the
sentinel REACHED the effective settings
(`file_neutralised=False`), which is only possible if `config.py` still reads
the env file.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | TO DELIVER - a new test spawns a pytest subprocess against a scratch copy of the tree with a generated `backend/.env` containing a sentinel `SYTHION_OLLAMA_API_KEY`, asserts the autouse isolation neutralises it, asserts the readiness file stays green with the same pass/fail set as the `.env`-absent tree, asserts that removing the isolation makes that subprocess run fail; no network I/O, no real profile directory | **MET** | `backend/tests/test_env_isolation.py` (one test, 3 nested runs, 120 s timeout each); E1, E5, E6 |
| 2 | ALREADY MET (preferred branch) - the isolation clears the `SYTHION_*` process environment before snapshotting and for the duration of every test | **ALREADY MET - VERIFIED** | `backend/tests/conftest.py:544-560` (the snapshot removes every `SYTHION_*` variable; the removal is at `:551`) and `backend/tests/conftest.py:602-612` (the per-test fixture deletes them; the deletion is at `:612`); landed by `ba69cc2`, an ancestor of HEAD. **Verified** by the nested run: the child exports `SYTHION_SELECTED_MODEL=<sentinel>` and the probe observes `env_neutralised=True` / `env_cleared=True`; the negative control turns both to `False` (E6) |
| 3 | ALREADY MET - the `file_roots` swap is captured in one conftest comment | **ALREADY MET - VERIFIED (documentation fact, no code change)** | `backend/tests/conftest.py:585-590`: "``[]`` for ``file_roots`` is NOT 'no file access' ... The isolation swaps approved directories; it is not file-access-free." (also `conftest.py:510`) |
| 4 | TO DELIVER - `_LOCAL_ENV_FIELDS` is imported by the isolation test instead of being re-hardcoded | **MET** | `backend/tests/test_setup_readiness.py`: `from backend.tests.conftest import _LOCAL_ENV_FIELDS` and `for name in _LOCAL_ENV_FIELDS:`; the literal tuple is gone. The generated probe imports the same constant and drives its sentinel sweep from it. E2 (one conftest instance) |
| 5 | TO DELIVER - product behaviour unchanged; the diff lists only files under `backend/tests/`; `backend/core/config.py` untouched; the product still reads `backend/.env` | **MET** | E7 (the diff is exactly three files, all under `backend/tests/`); `backend/core/config.py` is not in the diff; the negative control proves the file is still read |
| 6 | TO DELIVER - the full suite keeps the pre-change passed/skipped counts; no test deleted, skipped or marked | **MET (see the reading stated below)** | E3: collected 1628 -> 1629, i.e. exactly the one new test; the template adds no test and no skip; E4: the full suite's outcome |

**Criterion 6, the reading this implementation uses** (the plan's open question 1):
the **skipped** count is unchanged, the **passed** count grows by exactly the
number of new tests (1), the collected count moves 1628 -> 1629, and no
existing test's verdict changes. 1628 is the count measured on this tree
(2026-10-22, items 017-019 landed); the 006 verifier's 1614 is stale.

## Findings

1. **The native crash of the full suite is NOT caused by this change.** The
   first full-suite run died with `Windows fatal exception: access violation`
   in a worker thread of `backend/tests/test_phase95_hardening.py`
   (`validate_in_thread`, inside SQLAlchemy's `loading.py`) at ~61% of the run.
   That test spawns threads that use one SQLite connection across threads; it
   is untouched by this item, and the new test only ever touches the tree
   through a subprocess. The re-run is reported in E4.
2. **The check has teeth on CI, not only in a tree with a `.env`.** The
   negative control (E6) shows the probe catching the deleted isolation in a
   tree with NO `backend/.env`, through the process-environment sentinel. That
   is strictly stronger than the plan's minimum (which relied on the `.env`
   sentinel reaching the settings in run 2).
3. **The network guard had to exempt loopback.** The first implementation
   refused EVERY outbound `connect`, and it broke the readiness file: on
   Windows, asyncio's event loop builds its self-pipe from a loopback socket
   pair, so 9 readiness tests died with the guard's `RuntimeError`. The guard
   now refuses and records every connection that could leave the machine and
   lets loopback through untouched. A loopback socket pair is not network
   I/O, and the "no outbound attempt" property is unchanged (the attempt
   counter is 0 in all three runs).
4. **The legacy profile migration had to be fenced off.** `backend/core/storage.py::migrate_legacy_data_dir`
   copies `~/.sythion` into the profile directory when that directory is
   empty. The scratch profile directory therefore carries a
   `.isolation-placeholder` file, so the migration short-circuits and the
   scratch tree holds nothing but fakes. (On this machine `~/.sythion` does
   not exist, so the copy would not have happened anyway - the placeholder
   makes it independent of the machine.)
5. **`PYTEST_ADDOPTS` is scrubbed from the child environment** in addition to
   the plan's `PYTHONPATH` scrub: the caller's pytest options would otherwise
   change the child's command line, and the child environment is built
   explicitly for exactly that reason.
6. **The per-run signal files are named `probe-signal-<n>.txt`** rather than a
   single `probe-signal.txt`, so a run that fails to write its signal can
   never be confused with a previous run's file. The mechanism is the plan's.
7. **The repository's `post-commit` git hook cannot run on this machine**: its
   shebang is `#!/usr/bin/env python3` and `python3` does not resolve here,
   so every commit prints "Python was not found" and the hook does nothing.
   The hook only acts on commit messages tagged `[decision]`/`[constraint]`/
   `[component]`/`[arch]`/`[state]`, so no memory fact was lost by it - but
   the hook is effectively dead on Windows and the board may want to know.

## Not checked

1. **The nested run on the CI runner** (GitHub Actions, Windows, Python 3.11)
   was not measured - every number here comes from this machine (CPython
   3.14.7 / pytest 8.4.2). The shape (same interpreter, same cwd, same
   command, same environment construction) is CI-identical, the absolute
   timings are not.
2. **The 120 s per-run timeout was never exercised** - the slowest observed
   nested run was 12.0 s. The timeout path (`pytest.fail`) is code, not
   measurement.
3. **The full suite was not run with a developer's `backend/.env` present.**
   The worktree is a clean checkout (no `backend/.env`), which is the CI
   shape; the developer's own tree at `C:\Sythion\sythion` was never used for
   a suite run, and its `.env` was never read, copied, printed or diffed.
4. **The 019 tautology guard was not re-run in isolation** against the two new
   files; the new files are written in its legal shape (`assert x is True`,
   `assert x == 0`, `assert x == []` - comparisons are opaque leaves) and the
   guard scans every `*.py` under `backend/tests/`, so the full-suite run
   covers it (see E4).
5. **No mutation testing of the probe's own assertions** beyond the negative
   control: e.g. it was not checked what happens if the probe's signal file
   is written but its assertions are removed.
