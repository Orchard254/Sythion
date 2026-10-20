# Report: baseline - 000 - attempt 2

Role: baseline capture (read-only; this file is the only thing I wrote).
Repo: `C:\Sythion\sythion`, branch `master`.
Source tree unchanged during my measurements: `git diff --stat 2d6fe8d e224319 -- backend docs frontend src-tauri scripts Makefile README.md` is empty
(the only commits that landed mid-run were the orchestrator's own `.orchestra/` commits; HEAD moved `2d6fe8d` -> `e224319`).

No network was used. Every artifact below is on local disk.

---

## What I did

All commands were run with working directory `C:\Sythion\sythion` unless stated otherwise.
Raw outputs are in `C:\Users\orcha\AppData\Local\Temp\opencode\` (outside the repo).

### Full-suite runs (3 independent samples)

| # | Command | Output file | Result |
|---|---|---|---|
| 1 | `python -m pytest backend/tests -q` | `baseline-full-run-2.txt` | `1564 passed, 45 skipped, 407733 warnings in 436.42s (0:07:16)` - exit 0 |
| 2 | `python -m pytest backend/tests -q -rs -p no:cacheprovider` | `baseline-full-run-3.txt` | **CRASHED** - `Windows fatal exception: access violation`, exit code `-1073741819` (0xC0000005), no pytest summary |
| 3 | `python -m pytest backend/tests -q -p no:cacheprovider` | `baseline-full-run-4.txt` | `1564 passed, 45 skipped, 406252 warnings in 388.53s (0:06:28)` - exit 0 |

Run #1 was started in the background FIRST, redirected straight to a file with
`cmd /c "... > file 2>&1"` (no `Select-Object -Last N` pipe), so the fatal-exception header
was never lost from the head of the file. Runs #2 and #3 used the same redirection.
`C:\Sythion\sythion\.orchestra\baseline-pytest.txt` was **not** modified.

### Targeted runs

| Command | Output file | Result |
|---|---|---|
| `python -m pytest backend/tests/acceptance -q -p no:cacheprovider` | `acceptance.txt` | `191 passed, 2 skipped, 16330 warnings in 25.72s`, exit 0 |
| `python -m pytest backend/tests/acceptance -q -rs -p no:cacheprovider` | `acceptance-rs.txt` | `191 passed, 2 skipped in 35.09s`; 2 skips are the symlink-privilege skips |
| `python -m pytest backend/tests --collect-only -q -p no:cacheprovider` | `collect-all.txt` | `1609 tests collected in 99.92s (0:01:39)` |
| `python -m pytest tests --collect-only -q -p no:cacheprovider` **from `backend/`** | `collect-from-backend.txt` | `1609 tests collected in 133.52s` (CI's command; same count) |
| `python -m pytest backend/tests --collect-only -q -p no:cacheprovider -m gui` | `m-gui.txt` | `no tests collected (1609 deselected) in 225.63s` |
| `... -m network` | `m-network.txt` | `no tests collected (1609 deselected) in 222.62s` |
| `... -m acceptance` | `m-acceptance.txt` | `no tests collected (1609 deselected) in 250.20s` |
| `... -m slow` | `m-slow.txt` | `1/1609 tests collected (1608 deselected)` -> `backend/tests/test_phase105_concurrency.py::TestFairnessSlotAllocation::test_long_running_fairness` |
| `... -m "not acceptance"` (the `make test-unit` expression) | `m-not-acceptance.txt` | `1609 tests collected in 180.41s` (0 deselected) |
| `... -m "not slow and not flaky and not integration and not gui and not network"` (the `make test-fast` expression) | `m-testfast.txt` | `1608/1609 tests collected (1 deselected) in 182.60s` |
| `python -m pytest backend/tests/test_new_tools.py -p no:cacheprovider` (no `-q`, for the config header) | `header.txt` | header shows `rootdir: C:\Sythion\sythion` and **no `configfile:` line** |
| `npm run lint` **from `frontend/`** | `frontend-lint.txt` | exit 0, no output (`eslint . --ext ts,tsx --report-unused-disable-directives --max-warnings 0`) |
| `node_modules\.bin\vitest.cmd run` **from `frontend/`** | `frontend-vitest.txt` | `Test Files 5 passed (5)`, `Tests 49 passed (49)`, 1.29 s, exit 0 |

### Import gates

| CWD | Command | Exit | Output |
|---|---|---|---|
| repo root | `python -c "import backend.main"` | **0** | - |
| repo root | `python -c "import backend.main; import backend.api.routes"` | **0** | - |
| `backend/` | `python -c "import backend.main"` | **1** | `ModuleNotFoundError: No module named 'backend'` |
| `backend/` | `python -c "import backend.main; import backend.api.routes"` (the exact CI step, `ci.yml:35-36`) | **1** | `ModuleNotFoundError: No module named 'backend'` |
| `backend/` | `python -c "import main; import api.routes"` | **1** | `ImportError: attempted relative import with no known parent package` (`backend/main.py:17`) |

### Crash characterisation

| Command | Output | Result |
|---|---|---|
| `python -m pytest backend/tests/test_new_tools.py -q -p no:cacheprovider` | `repro-newtools-file.txt` | `22 passed, 1082 warnings in 4.60s`, exit 0, 0 faults |
| `python -m pytest backend/tests/test_new_tools.py::TestGUIScreenshotTool::test_gui_screenshot_with_window_title -q -p no:cacheprovider` **x30, separate processes** | `loop-shot-1..30.txt` | 30/30 exit 0, 0 fatal exceptions |
| `python -m pytest backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed -q -p no:cacheprovider` **x25, separate processes** | `loop-budget-1..25.txt` | 25/25 exit 0, 0 fatal exceptions |
| `python C:\Users\orcha\AppData\Local\Temp\opencode\repro_uia.py 5` (inline: `_run_on_worker(lambda: _uia_driver.observe("NonExistentWindow12345", 1, 1))` x5) | `repro-uia-1.txt` | `ITER 0..4 ok found=False`, exit 0, 0 faults |
| static analysis of the pre-existing `C:\Sythion\sythion\.orchestra\baseline-pytest.txt` | `analyze_baseline_pytest.py` / `analyze2.py` | see below |

---

## Evidence

### E1 - Full suite, clean run (run #1), tail of `baseline-full-run-2.txt`

```
1564 passed, 45 skipped, 407733 warnings in 436.42s (0:07:16)
```

`EXIT=0`. Zero occurrences of `Windows fatal exception` in the file (checked with a byte-level scan of both UTF-8 and UTF-16LE decodings).
Progress markers present for every step 4% -> 100%.

### E2 - Full suite, clean run (run #3), tail of `baseline-full-run-4.txt`

```
1564 passed, 45 skipped, 406252 warnings in 388.53s (0:06:28)
```

`EXIT=0`. Zero `Windows fatal exception` occurrences. Progress markers 4% -> 100%.

Two independent clean runs agree on **1564 passed / 45 skipped**. The warning count differs
between runs (407733 vs 406252) - that counter is not stable and should not be quoted as a baseline.

### E3 - Full suite, crash (run #2), `baseline-full-run-3.txt`

`EXIT=-1073741819` (= `0xC0000005`, STATUS_ACCESS_VIOLATION).
Head of the file:

```
.......................................................ss............... [  4%]
...
........................................................................ [ 35%]
....................................................Windows fatal exception: access violation

Thread 0x0000e500 [Thread-90 (tfn)] (most recent call first):
  File "...\sqlalchemy\engine\default.py", line 1192 in do_execute
  ...
  File "C:\Sythion\sythion\backend\agents\coordination.py", line 1127 in atomic_commit_spent
  File "C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py", line 265 in spend
  File "C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py", line 272 in tfn

Current thread 0x00000254 [Thread-89 (tfn)] (most recent call first):
  File "...\sqlalchemy\orm\loading.py", line 231 in chunks
  ...
  File "C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py", line 265 in spend
  File "C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py", line 272 in tfn

Thread 0x00006fb0 [sythion-uia-mta] (most recent call first):
  File "...\threading.py", line 373 in wait
  File "...\queue.py", line 210 in get
  File "C:\Sythion\sythion\backend\tools\gui_tools.py", line 973 in _run

Thread 0x00002b38 (most recent call first):
  File "...\threading.py", line 1133 in join
  File "C:\Sythion\sythion\backend\tests\test_phase105_concurrency.py", line 275 in test_concurrent_budget_does_not_exceed
```

So in run #2 the faulting code is `backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
(three threads each calling `DelegationPlanService.atomic_commit_spent`, `backend/agents/coordination.py:1127`, which issues `BEGIN IMMEDIATE`
on a file-backed SQLite DB). The `sythion-uia-mta` thread was alive and idle in this dump - it was **not** the culprit here.
The run died with no pytest summary, so the 1609-test suite never finished in run #2.

### E4 - The pre-existing orchestrator capture is a FAULT STORM, not one crash

`C:\Sythion\sythion\.orchestra\baseline-pytest.txt` is 3,949,512 bytes and is **UTF-16LE** (first bytes `ff fe 2e 00 2e 00 2e 00`) - PowerShell 5.1 re-encoded the native command's stdout/stderr. Decoded as UTF-16LE it contains:

```
total fatal lines: 295
total thread dumps: 294
distinct uia thread ids: ['0x0000a128']
fatal_codes Counter({'0x80040155': 277, '0x8001010d': 18})
gui_tools_frames Counter({('403','observe'): 294, ('981','_run'): 294, ('992','run'): 294,
                          ('426','_find_window'): 196, ('417','_find_window'): 98})
dispatch lambda sites: Counter({1537: 147, 1749: 147})
progress markers present: ['4','8','13','17','22','26','31','35','40','44','49','53','58']
pytest summary found: None
```

By last-seen progress marker, **all 295 faults occurred in one burst at the 35% mark**; the run then continued to 58% (where it was killed) and produced no summary.

The captured frame is `gui_tools.py:417 in _find_window` -> `:403 in observe` -> `:1537 in <lambda>` -> `:981 in _run`, i.e. `GUIScreenshotTool.execute` -> `_run_on_worker(lambda: _uia_driver.observe(...))` -> `root.FindFirst(4, exact)`.
`gui_tools.py:1749` is the second dispatch site (`GUIBatchFormTool.execute`).
The `asyncio_0` thread is parked at `gui_tools.py:992` (`future.result(timeout=...)`) waiting for the dead worker.

**Correction to the brief.** The brief's "HARD EVIDENCE" block quotes `line 426 in _find_window` and `line 1749 in <lambda>` and attributes it to the orchestrator's capture. The orchestrator's capture file actually shows `line 417 in _find_window` and `line 1537 in <lambda>` for its first dump. `426/1749` are the *Researcher's* capture (R-007) and are also the second-most-common site in the orchestrator's capture. Both sites occur in the same function, so the substance is unaffected, but the brief and the orchestrator's own capture are not describing the same dump.

### E5 - Which tests reach the faulting frames

From the ordered collection list (`collect-all.txt`, 1609 ids):

```
index 602  backend/tests/test_new_tools.py::TestGUIScreenshotTool::test_gui_screenshot_returns_output
index 604  backend/tests/test_new_tools.py::TestGUIScreenshotTool::test_gui_screenshot_with_window_title
index 606  backend/tests/test_new_tools.py::TestGUIBatchFormTool::test_gui_batch_form_returns_output
index 628  backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed
```

- index 604 is the **only** test in the suite that calls `GUIScreenshotTool.execute` with a `window_title`
  (`backend/tests/test_new_tools.py:260` -> `gui_tools.py:1534-1537` -> `gui_tools.py:403` -> `gui_tools.py:417/426`).
  It is the exact frame in the dump.
- index 606 is the **only** test that calls `GUIBatchFormTool.execute` with a `window_title`
  (`backend/tests/test_new_tools.py:291-294` -> `gui_tools.py:1747-1749`).
  It is the other dispatch site in the dump.
- index 628 is the crash site of run #2 (E3).

The 147/147 split between dispatch sites 1537 and 1749 in E4 therefore maps 1:1 onto those two tests.

### E6 - The COM apartment theory does NOT explain 0x80040155

```
Get-ItemProperty "HKLM:\SOFTWARE\Classes\CLSID\{FF48DBA4-60EF-4201-AA87-54103EEF594E}\InprocServer32"
  (default)      : C:\Windows\System32\uiautomationcore.dll
  ThreadingModel : Both
```

`CLSID_CUIAutomation` is registered `ThreadingModel = Both`, so it **can** be instantiated from an MTA.
The naive "STA-only class created in an MTA" explanation for `CLASS_E_CLASSNOTAVAILABLE` is therefore refuted.

What the code actually does (all confirmed by reading the source):

- `backend/tools/gui_tools.py:35-36` - `if not hasattr(sys, "coinit_flags"): sys.coinit_flags = 0x0` (COINIT_MULTITHREADED).
  Empirically **neither** `comtypes` nor `pywin32.pythoncom` sets `sys.coinit_flags`
  (`python -c "import pythoncom, sys; print(getattr(sys,'coinit_flags','<unset>'))"` -> `<unset>`;
  same for `import comtypes`), so the guard is always true and COM is always initialised MTA.
- `comtypes/__init__.py` calls `CoInitializeEx()` at import time using `sys.coinit_flags` -> MTA in the importing thread.
- `backend/tools/gui_tools.py:968` - the worker thread explicitly calls `CoInitializeEx(None, 0x0)` -> MTA.
- `backend/tools/gui_tools.py:77` - `_UIA_CALL_TIMEOUT = 10.0`; `gui_tools.py:987-992` - `future.result(timeout=...)`.
- `backend/tools/gui_tools.py:1023-1036` - `_run_on_worker` retries **once** after `_gui_worker.restart()`.

I did not determine *why* UIAutomationCore raises a native `0x80040155` / `0x8001010d` from the root element enumeration. That remains open (see Not checked).

### E7 - Marker configuration

`backend/tests/conftest.py:30-47` declares **eight** markers: `slow`, `flaky`, `integration`, `acceptance`, `gui`, `network`, `process_kill`, `provider_e2e`.
The repo has no `pytest.ini` / `pyproject.toml` / `setup.cfg` (the only matches under the repo are vendored copies in `build/staging/whisper-src/whispercpp-0.0.17/`, which are not repo config); the pytest header prints `rootdir:` and no `configfile:` line.

Exactly three `PytestUnknownMarkWarning` entries appear in the full-run output:

```
backend/tests/test_phase105_concurrency.py:39:  Unknown pytest.mark.concurrency
backend/tests/test_phase1075_hardening.py:35:    Unknown pytest.mark.concurrency
backend/tests/test_phase115_soak.py:15:          Unknown pytest.mark.gated_soak
```

### E8 - The two acceptance skips

```
SKIPPED [1] backend\tests\acceptance\test_canonical_file_checks.py:49: Creating symbolic links requires elevated privileges
SKIPPED [1] backend\tests\acceptance\test_canonical_file_checks.py:68: Creating symbolic links requires elevated privileges
```

The doc claim that the 2 acceptance skips are the symlink-privilege skips in
`backend/tests/acceptance/test_canonical_file_checks.py` is **true**. The claim that there are 196 of them is not.

### E9 - No database was touched

`backend/sythion.db` (0 bytes) and `backend/test_voice.db` (45056 bytes) have the same mtimes before and after my runs
(2026-08-07 22:38 and 2026-08-03 06:42). `%LOCALAPPDATA%\Sythion (Synth)` contains only `python-runtime/` - there is **no** Sythion database there.
The suite's DB fixture is in-memory SQLite (`backend/tests/conftest.py:61-83`, `StaticPool`), and the tests that touch a file DB use temp paths.

---

## Baseline numbers

- **Collected total** (`pytest backend/tests --collect-only -q`): **1609** (99.92 s). The same 1609 are collected from `backend/` with `pytest tests --collect-only -q` (133.52 s).
- **Full suite result**:
  - run #1: **1564 passed, 0 failed, 45 skipped, 0 errors, 436.42 s (0:07:16)** - exit 0
  - run #2: **no result** - interpreter killed by a native access violation, exit `0xC0000005`
  - run #3: **1564 passed, 0 failed, 45 skipped, 0 errors, 388.53 s (0:06:28)** - exit 0
  - two clean samples out of three; wall time 389-436 s
- **Acceptance suite** (`python -m pytest backend/tests/acceptance -q`): **191 passed, 0 failed, 2 skipped**, 25.72 s (a second `-rs` run: 191 passed, 2 skipped, 35.09 s)
- **Frontend lint** (`cd frontend && npm run lint`): **exit 0**, no output, no warnings
- **Frontend vitest** (`cd frontend && node_modules/.bin/vitest.cmd run`): **5 files / 49 tests passed**, 1.29 s, exit 0
- **Backend import gate from repo root**: `python -c "import backend.main"` -> **exit 0**; with `import backend.api.routes` -> **exit 0**
- **Backend import gate from `backend/`**: `python -c "import backend.main"` -> **exit 1** (`ModuleNotFoundError: No module named 'backend'`); with `import backend.api.routes` -> **exit 1**

---

## Pre-existing failures

| # | Test | Symptom | Evidence | Reproduces alone? |
|---|---|---|---|---|
| 1 | (suite level, not a test) `python -m pytest backend/tests -q` | Interpreter killed: `Windows fatal exception: access violation`, exit `0xC0000005`. No pytest summary, run aborts around the 39% mark. Faulting threads are the two `tfn` threads of `backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed` (`test_phase105_concurrency.py:265` -> `backend/agents/coordination.py:1127 atomic_commit_spent`), i.e. an access violation inside the native SQLite driver under 3 concurrent `BEGIN IMMEDIATE` writers. | `baseline-full-run-3.txt` (full dump quoted in E3); exit code -1073741819 | **No.** 25/25 isolated runs of that single test passed with exit 0 (`loop-budget-1..25.txt`). Not reproduced in run #1 or run #3 of the full suite either. |
| 2 | (suite level, not a test) `python -m pytest backend/tests -q` | 294 native COM faults in one burst at the 35% mark, all on the single thread `sythion-uia-mta`, in `_UIADriver._find_window`. 277x `0x80040155` (CLASS_E_CLASSNOTAVAILABLE), 18x `0x8001010d` (RPC_E_INVALID_REFERENCE). The run then continued to 58% and was killed. | `C:\Sythion\sythion\.orchestra\baseline-pytest.txt`, decoded and counted (E4). Also seen by the Researcher (R-007) at `gui_tools.py:426`. | **No.** 30/30 isolated runs of `TestGUIScreenshotTool::test_gui_screenshot_with_window_title` (the only test that reaches `gui_tools.py:1537 -> :403 -> :417/426`) passed with exit 0 (`loop-shot-1..30.txt`); 1/1 run of the whole `test_new_tools.py` file passed (22 passed); 5/5 inline `_run_on_worker(...observe...)` calls succeeded. |
| 3 | - | **No ordinary test failures.** Both clean full-suite runs were `1564 passed, 45 skipped, 0 failed`. | `baseline-full-run-2.txt`, `baseline-full-run-4.txt` | - |

The 45 skips are not failures; they are gated real-system tests (the two symlink-privilege skips are item 1 of `acceptance-rs.txt`, the rest are power/network/system-audio/provider/process-kill gates).

---

## Docs-vs-reality deltas

Measured baseline: **1564 passed, 45 skipped, 1609 collected** (full suite) and **191 passed, 2 skipped** (acceptance suite),
full-suite wall time **389-436 s**, acceptance **25.7 s**.

| Document | file:line | Documented | Measured |
|---|---|---|---|
| README.md badge | `README.md:6` | `683 passed / 2 skipped` | `1564 passed / 45 skipped` (the badge also contradicts the prose in the same file) |
| README.md | `README.md:103` | `692 passed, 32 skipped` | `1564 passed, 45 skipped` |
| README.md | `README.md:103` | breakdown `32 skipped = 27 power/network real-system tests + 3 volume system-audio tests + 2 symlink privilege tests` | 45 skipped, not 32 - the per-category breakdown is therefore wrong too (I could not obtain a per-skip breakdown for the full suite; see Not checked) |
| README.md | `README.md:104` | acceptance `196 passed, 2 skipped` | `191 passed, 2 skipped` |
| README.md | `README.md:106-107` | the 2 skips are the symlink-privilege skips in `backend/tests/acceptance/test_canonical_file_checks.py` | **TRUE** (E8) |
| docs/ROADMAP.md | `docs/ROADMAP.md:24` | `659 passed, 32 skipped` (dated 2026-08-16) | `1564 passed, 45 skipped` |
| docs/ROADMAP.md | `docs/ROADMAP.md:25` | acceptance `196 passed, 2 skipped` | `191 passed, 2 skipped` |
| docs/TESTING.md | `docs/TESTING.md:7` | `make test` -> `692 passed, 32 skipped` | `1564 passed, 45 skipped` |
| docs/TESTING.md | `docs/TESTING.md:9` | `make test-acceptance` -> `196 passed, 2 skipped` | `191 passed, 2 skipped` |
| docs/TESTING.md | `docs/TESTING.md:30-33` | "Current verified baseline: 692 passed, 32 skipped ... 196 passed, 2 skipped" | `1564 passed, 45 skipped` / `191 passed, 2 skipped` |
| docs/TESTING.md | `docs/TESTING.md:32-33` | the 2 acceptance skips are the symlink-privilege skips | **TRUE** (E8) |
| docs/TESTING.md | `docs/TESTING.md:39` | `make test` -> "Full backend suite (~55s)" | **436.42 s / 388.53 s** - ~7-8x the claim |
| docs/TESTING.md | `docs/TESTING.md:40` | `make test-fast` -> "Fast suite (~25s)" | `make test-fast` selects 1608 of 1609 tests, i.e. it is the full suite minus one test, and measured at full-suite speed (~7 min), not 25 s |
| docs/TESTING.md | `docs/TESTING.md:41` | `make test-acceptance` -> "~10s" | 25.72 s |
| docs/TESTING.md | `docs/TESTING.md:43-44` | `make test-gui` / `make test-network` are meaningful selections | both collect **0** tests and exit 0 |
| docs/TESTING.md | `docs/TESTING.md:16-24` | the suite runs from the repo root **or** from `backend/` | **TRUE** - `pytest tests --collect-only` from `backend/` collects the same 1609 |
| docs/TESTING.md | `docs/TESTING.md:100` | CI "runs on push/PR to `main`" | **TRUE** (`ci.yml:4-5`); `git branch -a` -> `master`, `origin/HEAD -> origin/master`, `origin/master` - no `main` exists |
| docs/TESTING.md | `docs/TESTING.md:126` | "pytest runs in strict asyncio mode" | **TRUE** - header prints `asyncio: mode=Mode.STRICT` |
| docs/PROJECT_STATUS.md | `docs/PROJECT_STATUS.md:41` | `692 passed, 32 skipped` | `1564 passed, 45 skipped` |
| docs/PROJECT_STATUS.md | `docs/PROJECT_STATUS.md:42` | acceptance `196 passed, 2 skipped` | `191 passed, 2 skipped` |
| docs/PROJECT_STATUS.md | `docs/PROJECT_STATUS.md:44-45` | the 2 skips are the symlink-privilege skips | **TRUE** (E8) |
| C:\Sythion\SYTHION_TODOS.md | `SYTHION_TODOS.md:5` | `349 backend tests green, 2 skipped` | `1564 passed, 45 skipped` (1609 collected) |

Every documented backend-suite figure (692 / 683 / 659 / 349) is **below the collected count of 1609**, so none of them can be correct
for this tree under any Python version - the Researcher's R-004 argument holds.

---

## Research claims confirmed or refuted

| Claim | Verdict | My independent evidence |
|---|---|---|
| **R-001** - CI `backend-tests` "Import gate" step can never succeed as configured | **confirmed** | From the repo root `python -c "import backend.main; import backend.api.routes"` -> exit 0. From `backend/` the identical command -> exit 1, `ModuleNotFoundError: No module named 'backend'`. `ci.yml:12-14` sets `working-directory: backend` for the whole job and `ci.yml:35-36` runs the gate inside it. **Extra:** the alternative fix suggested in R-001's direction ("change the import target to `main`/`api.routes` when run from `backend/`") also fails - `python -c "import main; import api.routes"` from `backend/` -> exit 1, `ImportError: attempted relative import with no known parent package`, because `backend/main.py:17-25` uses package-relative imports. The only working invocation is from the repo root. |
| **R-002** - CI never triggers on this repository's only branch | **confirmed** | `git branch -a` -> `* master`, `remotes/origin/HEAD -> origin/master`, `remotes/origin/master`; no `main`. `ci.yml:4-5` is `on: push: branches: [main]`. |
| **R-003** - `-m gui` / `-m network` / `-m acceptance` each collect 0 of 1609 | **confirmed** | `--collect-only` with each expression: `no tests collected (1609 deselected)` in all three cases (`m-gui.txt`, `m-network.txt`, `m-acceptance.txt`). |
| **R-003** - `make test-unit` == the full suite | **confirmed** | `-m "not acceptance"` -> `1609 tests collected` (0 deselected), i.e. byte-identical selection to the unfiltered suite. |
| **R-003** - `make test-fast` deselects exactly 1 test | **confirmed** | `-m "not slow and not flaky and not integration and not gui and not network"` -> `1608/1609 tests collected (1 deselected)`. The deselected test is `backend/tests/test_phase105_concurrency.py::TestFairnessSlotAllocation::test_long_running_fairness` (the only `slow`-marked test; `-m slow` -> `1/1609 tests collected`). |
| **R-004** - full suite = `1564 passed, 45 skipped`, `1609 collected` | **confirmed** | Two independent clean runs: `1564 passed, 45 skipped` (436.42 s and 388.53 s), `--collect-only` -> `1609 tests collected`. Identical to the Researcher's numbers. |
| **R-004** - full suite = `533 s` | **refuted** | My two clean runs: **436.42 s** and **388.53 s**. The Researcher's 533 s is not reproduced; treat "roughly 6.5-7.5 minutes" as the range, and note that a third run never completed (native crash). |
| **R-004** - every documented figure (692 / 683 / 659) is below the collected count, so it cannot be right under any Python version | **confirmed** | Collected = 1609. 692, 683, 659 and 349 are all far below it. |
| **R-004** - `docs/TESTING.md:39` claims the full suite takes "~55s" | **confirmed** | The line reads: `make test` \| `Full backend suite (~55s)`. Measured 436.42 s / 388.53 s -> the documented runtime is ~7-8x too small. |
| **R-004** - the acceptance figure "196 passed, 2 skipped" is wrong | **confirmed** | `python -m pytest backend/tests/acceptance -q` -> `191 passed, 2 skipped` (two runs). The *skip* claim (2 symlink-privilege skips) is correct; the *count* is not. |
| **R-005** - no `pytest.ini`/`pyproject.toml`/`setup.cfg`; pytest runs with `inifile: None` | **confirmed** | No such file exists in the repo (only vendored copies under `build/staging/whisper-src/`, which are not repo config). The pytest header prints `rootdir: C:\Sythion\sythion` and no `configfile:` line. |
| **R-005** - the undeclared markers are exactly `concurrency` and `gated_soak` | **confirmed** | Exactly 3 `PytestUnknownMarkWarning` entries in the full-run output: `concurrency` at `test_phase105_concurrency.py:39`, `concurrency` at `test_phase1075_hardening.py:35`, `gated_soak` at `test_phase115_soak.py:15`. `conftest.py:31-47` declares the other eight. |
| **R-005 (and the orchestrator's own baseline note)** - "slow/flaky/integration/gui/network/acceptance/provider_e2e are undeclared markers" | **refuted** | All eight are declared in `backend/tests/conftest.py::pytest_configure` (`backend/tests/conftest.py:31-47`). The orchestrator's `goal.md` assumption 7 and `log.md` line 7 are wrong on this point; the Researcher's correction stands. |
| **R-007** - the suite intermittently hard-crashes the interpreter inside the `sythion-uia-mta` COM/UIA worker thread | **confirmed** | The pre-existing orchestrator capture contains 294 faulthandler dumps, every one of them on the single thread `0x0000a128 [sythion-uia-mta]`, in `_UIADriver._find_window` (gui_tools.py:417/426) under `observe` (:403), with codes `0x80040155` (x277) and `0x8001010d` (x18) (E4). The Researcher's own capture shows the same fault class at `gui_tools.py:426`. |
| **R-007** - "roughly one run in two dies with a native fault in the COM/UIA worker rather than reporting a test failure" | **partly confirmed, and the framing is wrong** | (a) The COM fault is *not* a single event: the captured run contains a burst of 294 faults at one point in the run, and the run **did not die** - it continued to 58% (E4). (b) The fault is **not confined to the COM/UIA worker**: my own run #2 died with a completely different native fault - `Windows fatal exception: access violation` (`0xC0000005`) - in the two `tfn` threads of `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`, while the `sythion-uia-mta` thread was idle and healthy (E3). The orchestrator's `log.md:6` attributes the crash to `test_phase95_hardening.py::test_two_callers_at_boundary_only_one_admits`; that test contains no GUI code and appears nowhere in the capture, so that attribution is a misreading of a concurrent thread dump. |
| **R-007** - "observed once, not reproduced on demand" | **confirmed and extended** | I have now observed native faults in **2 of 5** full-suite runs I or the orchestrator captured (Researcher: 1 of 2; orchestrator: 1 of 1; mine: 1 of 3). Neither fault reproduces in isolation (30/30 and 25/25 single-test runs clean). |
| **R-006** - `datetime.utcnow()` deprecation flood, ~407k warnings | **partly confirmed** | Warning totals are real but **not stable**: 407,733 (run #1) vs 406,252 (run #3) vs the Researcher's 407,719. I did not independently re-count the 184 call sites; the flood is clearly present in the output (`backend/tools/objective_planning_tool.py:391` and `:776`, `backend/tests/test_work_state.py:818-819` etc.). |
| **R-016** - four `assert ... or True` tautologies | **not checked** | Out of scope for a baseline capture; I did not re-verify the four line numbers. |
| **R-011 / R-012 / R-013 / R-014 / R-015 / R-017 / R-018** | **not checked** | Outside the baseline slice (packaging, CLI, CI pinning, docs drift, repo hygiene). The one adjacent fact I did measure: the frontend test suite **does** pass locally (5 files / 49 tests), but CI still has no job that runs it (`ci.yml` has `frontend` = lint + build only, no `npm test`). |

---

## The intermittent crash

### Summary

I could **not** reproduce either native fault in isolation. What I could do is characterise both faults precisely and
identify the exact tests that reach the faulting code. The single most important correction is that **the crash is not
one crash**: the suite has at least **two distinct native-failure modes**, and the "COM fault" that both the Researcher
and the brief describe is a *burst* of hundreds of faults in one run rather than a single fatal event.

### Fault A - COM / UIA (`0x80040155`, `0x8001010d`) in `sythion-uia-mta`

- **Observed in**: the pre-existing capture `C:\Sythion\sythion\.orchestra\baseline-pytest.txt` (294 dumps), and the Researcher's run (R-007).
- **Shape**: 294 faulthandler thread dumps, all on one thread (`0x0000a128`, `sythion-uia-mta`), all inside
  `_UIADriver._find_window` - `gui_tools.py:417` (`root.FindFirst(4, exact)`) 98x and `gui_tools.py:426` (`root.FindAll(4, wcond)`) 196x -
  reached via `observe` (`gui_tools.py:403`) from the two dispatch lambdas at `gui_tools.py:1537` (147x, `GUIScreenshotTool.execute`)
  and `gui_tools.py:1749` (147x, `GUIBatchFormTool.execute`), executed in `_UIAWorker._run` (`gui_tools.py:981`).
- **Codes**: 277x `0x80040155` (CLASS_E_CLASSNOTAVAILABLE), 18x `0x8001010d` (RPC_E_INVALID_REFERENCE).
- **Timing**: all 295 in one burst at the 35% progress mark; the run then continued to 58% and was killed by the orchestrator.
- **Blast radius**: the `asyncio_0` thread is parked at `gui_tools.py:992` (`future.result(timeout=10.0)`) waiting for a worker that will never answer.
  `_run_on_worker` (`gui_tools.py:1029-1036`) then restarts the worker and retries once - which is the most likely reason the fault repeats.
- **Does it kill the process?** In the captured run, no. The suite kept going for another 23 percentage points.
- **Root cause**: not established. `CLSID_CUIAutomation` is registered `ThreadingModel = Both` (E6), so the obvious
  "STA-only COM class instantiated from an MTA" story is wrong. `sys.coinit_flags` is always forced to MTA
  (`gui_tools.py:35-36`) and the worker explicitly initialises MTA (`gui_tools.py:968`), so the apartment is consistent.
  Something inside UIAutomationCore's root-element enumeration is raising a native fault that CPython surfaces as an
  unhandled Windows exception.

### Fault B - access violation (`0xC0000005`) in the SQLite concurrency test

- **Observed in**: my run #2 (`baseline-full-run-3.txt`). This fault is **new** - neither the Researcher nor the orchestrator saw it.
- **Shape**: `Windows fatal exception: access violation`, exit code `0xC0000005`. Two `tfn` threads of
  `backend/tests/test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`
  (`test_phase105_concurrency.py:260-275`) were inside SQLAlchemy/`sqlite3` native code
  (`sqlalchemy/engine/default.py:1192 do_execute` and `sqlalchemy/orm/loading.py:231 chunks`) calling
  `DelegationPlanService.atomic_commit_spent` (`backend/agents/coordination.py:1127`), which issues `BEGIN IMMEDIATE`
  against a file-backed SQLite DB from three concurrent threads. The main thread was at `test_phase105_concurrency.py:275` in `threading.join`.
- **Does it kill the process?** **Yes** - unlike Fault A, this one terminated the interpreter with no pytest summary.
- **Note**: `backend/core/db_manager.py:59-61` builds each `DatabaseManager` with its own engine
  (`connect_args={"check_same_thread": False, "timeout": 30}`), so the three threads use three separate connections to the
  same file. A native access violation inside `sqlite3` under concurrent `BEGIN IMMEDIATE` writers is therefore not
  obviously the "same connection used from two threads" mistake - I could not establish the mechanism.

### Smallest command that triggers it

- **Smallest command that reaches the exact Fault-A frame**: `python -m pytest backend/tests/test_new_tools.py::TestGUIScreenshotTool::test_gui_screenshot_with_window_title -q`
  (it is the only test that executes `gui_tools.py:1537 -> :403 -> :417/:426`, i.e. the frame in the dump).
  **It did not trigger the fault in 30 consecutive separate-process runs.** Same for the sibling batch-form test.
- **Smallest command that produced Fault A**: `python -m pytest backend/tests -q` (full suite). One observed instance.
- **Smallest command that produced Fault B**: `python -m pytest backend/tests -q` (full suite). One observed instance;
  25/25 isolated runs of the offending test alone were clean.
- **Honest conclusion**: **I could not reproduce either fault on demand.** The full-suite command is the smallest command
  I observed to produce a native fault (2 of 5 full-suite runs across all observers), and the single-test commands that
  reach the identical code frames never faulted in 55 combined attempts.

### Rates observed (all full-suite runs of `python -m pytest backend/tests -q` on this machine)

| Run | Observer | Outcome |
|---|---|---|
| 1 | Researcher | native COM fault (R-007) |
| 2 | Researcher | clean, `1564 passed, 45 skipped`, 533 s |
| 3 | orchestrator | 294-fault COM burst at 35%, killed at 58% |
| 4 | **me** | clean, `1564 passed, 45 skipped`, 436.42 s |
| 5 | **me** | **access violation `0xC0000005`**, killed at ~39% |
| 6 | **me** | clean, `1564 passed, 45 skipped`, 388.53 s |

=> **2 native crashes in 6 full-suite runs (~33%)**, of two different kinds.

---

## Environment variables of this baseline

- **Interpreter**: CPython **3.14.7**, `C:\Users\orcha\AppData\Local\Programs\Python\Python314\python.exe` (64-bit)
- **pytest**: 8.4.2, pluggy 1.6.0; plugins `anyio-4.15.1`, `asyncio-0.23.8`; `asyncio: mode=Mode.STRICT`
- **OS**: Windows (win32), build 19041+ family - `sys.platform == 'win32'`
- **Working directory**: `C:\Sythion\sythion` for every suite run; `C:\Sythion\sythion\backend` for the import-gate-from-`backend/` measurements and the `pytest tests --collect-only` run; `C:\Sythion\sythion\frontend` for lint and vitest
- **Branch / commit**: `master`; HEAD `e224319a23139af3adc3f4f641083bd3c22721e0` (the source tree is identical to the `2d6fe8d` I started from - only `.orchestra/` changed)
- **Node**: `node_modules/.bin/vitest.cmd run` (vitest 1.6.1) - used directly so that **no network access and no `npm install`** was needed; `frontend/node_modules` was already present
- **Network**: not used. No URL was fetched, no package was installed.
- **Relevant env vars**: none set by me. `SYTHION_AUTONOMY_LEVEL` is deleted by the autouse fixture `backend/tests/conftest.py:50-57`;
  `SYTHION_RUN_PROCESS_KILL` and `SYTHION_RUN_PROVIDER_E2E` were **not** set, so the `process_kill` / `provider_e2e` gated tests skip.
- **Databases**: **no real database was read, written, reset, dropped, migrated or seeded.** `backend/sythion.db` (0 bytes) and
  `backend/test_voice.db` (45,056 bytes) are byte- and mtime-identical before and after (2026-08-07 22:38 / 2026-08-03 06:42).
  `%LOCALAPPDATA%\Sythion (Synth)` contains only `python-runtime/`; there is no Sythion DB there. The suite's fixture DB is
  in-memory SQLite (`conftest.py:61-83`).
- **Sidecar**: never launched. `scripts/launch_sidecar.py`, `scripts/smoke_test.py`, `backend/cli.py` were not executed.
- **Repo mutation**: none. `git status --short` shows only `.orchestra/` entries, all created by the orchestrator, not by me.
  The only file I wrote is this report.
- **Commands deliberately NOT run** (per brief): `make clean`, `npx tauri build`, `scripts/stage_python_runtime.py`, any DB reset/migrate/seed.

---

## Findings

1. **The baseline numbers in the Researcher report are right about the counts and wrong about the time.** `1564 passed / 45 skipped / 1609 collected`
   reproduced exactly in two independent clean runs. The `533 s` did not: I measured 436.42 s and 388.53 s. Anyone quoting a single runtime
   should quote a range and the interpreter.
2. **The suite is not deterministic on this machine, and the crash has two different faces.** 2 of 6 observed full-suite runs died natively.
   One face is the COM/UIA fault in `sythion-uia-mta` (the one the brief and R-007 describe) - a *burst* of 294 faults that does **not**
   kill the process. The other face is an **access violation in the native SQLite driver** inside
   `test_phase105_concurrency.py::TestBudgetAtomicity::test_concurrent_budget_does_not_exceed`, which **does** kill the process.
   A fix scoped only to `gui_tools.py` would leave the suite able to die.
3. **The "smallest reproducer" is the full suite.** The single test that reaches the faulting frame
   (`test_new_tools.py::TestGUIScreenshotTool::test_gui_screenshot_with_window_title`) was clean in 30/30 runs, and the
   concurrency test was clean in 25/25. Any future "reproduce the crash" work must run the full suite (or a large slice of it) - a
   single-test loop will report "not reproducible" and send the next agent down the wrong path.
4. **The `sythion-uia-mta` thread was healthy during the access violation.** The dump is a snapshot of all threads; the brief's
   and `log.md`'s attribution of the crash to the test the *main* thread happened to be running
   (`test_phase95_hardening.py::test_two_callers_at_boundary_only_one_admits`) is a misreading. The faulting thread must be
   identified from the dump, not from the main thread's stack.
5. **`CLSID_CUIAutomation` is `ThreadingModel = Both`**, so the obvious apartment-mismatch explanation for
   `CLASS_E_CLASSNOTAVAILABLE` is wrong. `sys.coinit_flags` is *always* forced to MTA by `gui_tools.py:35-36` (neither comtypes nor
   pywin32 sets it), and the worker explicitly initialises MTA. Whatever raises the native fault is inside
   UIAutomationCore's root-element enumeration, not in the apartment choice.
6. **The CI import gate is red for a reason that cannot be fixed by changing the import target.** `backend/main.py:17-25` uses
   package-relative imports, so `import main` from `backend/` also fails. The gate must run from the repo root
   (or `ci.yml` must add the repo root to `PYTHONPATH`).
7. **The suite's own test command works from `backend/`**: `pytest tests --collect-only -q` from `backend/` collects the same 1609
   tests. So `ci.yml:33` is fine; only `ci.yml:35-36` is broken. `docs/TESTING.md:16-24` is accurate on this point.
8. **The frontend is green**: lint exit 0 (`--max-warnings 0`), vitest 5 files / 49 tests passed. The frontend suite still has no CI job
   (`ci.yml:72-93` runs lint + build only), so that greenness is invisible to the pipeline.
9. **The acceptance suite is `191 passed, 2 skipped`, not `196 passed, 2 skipped`** - and the 2 skips really are the
   symlink-privilege skips in `backend/tests/acceptance/test_canonical_file_checks.py`, so only the count is wrong.
10. **The warning count is not a stable baseline** (407,733 / 406,252 / 407,719 across three runs of the same tree).
    Do not put it in a doc.
11. **Marker arithmetic worth recording**: `gui`, `network` and `acceptance` are declared but applied to zero tests;
    `slow` is applied to exactly one (`test_phase105_concurrency.py::TestFairnessSlotAllocation::test_long_running_fairness`);
    `concurrency` and `gated_soak` are applied but never declared (3 warnings);
    `flaky`, `integration`, `process_kill` and `provider_e2e` are declared and applied elsewhere or not at all.
    `make test-unit` == the full suite; `make test-fast` == the full suite minus one test; `make test-gui` and
    `make test-network` == zero tests, exit 0.

---

## Not checked

- **Root cause of the COM fault.** I established the fault class, the thread, the exact frames, the burst shape and the fact that
  the naive apartment-mismatch theory is wrong - but I did not determine *why* UIAutomationCore raises a native
  `0x80040155`/`0x8001010d` from `root.FindFirst`/`root.FindAll`. No debugger, no COM trace, no Windows Event Log query.
- **Root cause of the access violation.** I localised it to `test_phase105_concurrency.py:265-275` ->
  `coordination.py:1127 atomic_commit_spent` -> native sqlite3, but I did not determine whether it is a SQLite library
  concurrency bug, a SQLAlchemy/driver interaction, or a CPython-level issue. No debugger, no crash dump analysis.
- **Reproduction rate.** 2 crashes in 6 full-suite runs is far too small a sample to state a rate. I ran 3 full-suite runs
  myself (1 crash) and 55 isolated single-test runs (0 crashes). I did not run the suite 20 times.
- **The per-skip breakdown of the 45 full-suite skips.** The `-rs` run that would have produced it was the run that crashed.
  I therefore cannot verify or refute `README.md:103`'s claim that the skips are "27 power/network real-system tests +
  3 volume system-audio tests + 2 symlink privilege tests" (the total, 45, is already wrong). Only the 2 acceptance
  skips were verified individually.
- **Whether the full-suite skips differ between runs.** 45 in both clean runs, and the same 2 acceptance skips, but I did not
  enumerate all 45.
- **Python 3.11 / CI parity.** CI pins Python 3.11 (`ci.yml:21`); this machine has 3.14.7. I did not install or run 3.11,
  so I cannot say whether the counts or the crashes differ there. Every number in this report is a 3.14.7 number.
- **CI itself.** GitHub Actions was never executed. `ci.yml` was only read.
- **The Tauri shell, the installer, the release payload and the packaged runtime.** `npx tauri build`,
  `scripts/stage_python_runtime.py` and `scripts/verify_release_payload.py` were not run (forbidden / destructive).
- **The live sidecar and the smoke test.** `scripts/launch_sidecar.py`, `scripts/smoke_test.py` and `backend/cli.py` were not
  run, so `docs/TESTING.md:96`'s "Currently 6/6 live" is unverified.
- **The frontend build.** `npm run build` (tsc + vite) was not run - only `npm run lint` and `vitest run`. The brief asked for
  lint and vitest; the build was out of the requested scope and I chose not to spend the time.
- **R-016 and the remaining Researcher candidates** (R-002 was confirmed, R-011/012/013/014/015/017/018 were not checked).
- **The other ~38 files under `docs/`.** I read README.md, docs/TESTING.md, docs/PROJECT_STATUS.md, docs/ROADMAP.md,
  docs/QUALITY_GATES.md, docs/acceptance-test-map.md and the SYTHION_TODOS header. Further doc-vs-code drift may exist
  in the files I did not open.
- **The `sythion-uia-mta` worker's lifecycle under the fault.** I did not instrument `_UIAWorker.start/stop/restart`
  (`gui_tools.py:940-1012`) to establish whether the 147 repetitions come from `_run_on_worker`'s single retry
  (`gui_tools.py:1029-1036`) or from a different mechanism. The arithmetic is consistent with 98 `observe()` calls each
  faulting 3 times (1x `FindFirst` + 2x `FindAll`), which does not match `_run_on_worker`'s one-retry policy, so there is
  an unexplained gap.
- **Whether the crash is machine-load sensitive.** My run #3 ran while other work was in flight and was clean; run #2 ran
  alone and crashed. That is not enough to conclude anything about load sensitivity - it is one data point each way.
