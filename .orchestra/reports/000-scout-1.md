# Report: scout - 001 - attempt 1

## What I did

- Read the brief (`.orchestra/briefs/000-scout-1.md`), `goal.md`, `research.md`
  (R-001..R-018), `backlog.md`, `board.json`, `critiques.md`,
  `docs/QUALITY_GATES.md`, `docs/ROADMAP.md`.
- Read the repository artifacts the candidates point at, to make the acceptance
  criteria checkable rather than rhetorical: `Makefile`,
  `.github/workflows/ci.yml`, `docs/TESTING.md`, `README.md`,
  `docs/PROJECT_STATUS.md`, `docs/CODEBASE_BOUNDARIES.md`,
  `docs/OPERATIONS.md`, `backend/tests/conftest.py`,
  `backend/tests/test_phase105_concurrency.py`,
  `backend/tests/test_phase1075_hardening.py`,
  `backend/tests/test_phase125_planning_validation.py`,
  `backend/cli.py`, `cli.py`, `scripts/smoke_test.py`,
  `scripts/launch_sidecar.py`, `backend/core/handshake.py`,
  `backend/core/storage.py`, `backend/services/README.md`.
- Ran four read-only commands (below). I did not run the full backend suite
  (9 minutes each), did not launch the sidecar, did not build anything, did not
  create a branch, did not commit, and did not write outside `.orchestra/`.
- Wrote four files: `.orchestra/backlog.md` (overwritten), `.orchestra/board.json`
  (overwritten), `.orchestra/research.md` (edited in place - only `- Status:`
  lines changed and `- Rejected reason:` lines added),
  `.orchestra/reports/scout-001.md` (this file).

Commands run (all from `C:\Sythion\sythion`):

- `python -m pytest backend/tests -m gui --collect-only -q` -> `no tests
  collected (1609 deselected) in 135.76s`, `EXIT=5`
- `python -m pytest backend/tests/acceptance/test_canonical_file_checks.py --collect-only`
  -> header `rootdir: C:\Sythion\sythion`, `pytest-8.4.2`, `asyncio: mode=Mode.STRICT`,
  `collected 6 items`, **no `configfile:` line**
- `python -c "import _pytest.terminal; ..."` -> the only config-file string in
  pytest 8's terminal writer is `result.append("configfile: " + bestrelpath(...))`
- `Get-ChildItem -Filter pytest.ini / pyproject.toml / setup.cfg` at the repo root
  -> no match (all three absent)
- `Select-String` for `692|683|659|196 passed|55s|25s` over `README.md` and `docs/**/*.md`
  -> 13 hits across 6 files (see Evidence)

## Evidence

Every claim in the backlog traces to one of these.

- **R-001/R-002 (item 001)** - taken from the orchestrator's confirmed facts
  (import gate exits 1 from `backend/`, 0 from the repo root; `git branch -a` ->
  only `master`/`origin/master`; `ci.yml:4-5` triggers on `main`). I read
  `.github/workflows/ci.yml` myself: `defaults.run.working-directory: backend`
  (`:12-14`) wraps the import gate (`:35-36`); the install step (`:24-28`) adds
  `python -m pip install pytest pytest-asyncio` with no version constraint on
  top of `requirements.txt` (`backend/requirements.txt:43-44` pins
  `pytest==7.4.4`, `pytest-asyncio==0.23.3`); the `frontend` job (`:72-93`) has
  no vitest step.
- **R-003 (item 002)** - I re-measured one of the three zero-selection targets
  myself: `python -m pytest backend/tests -m gui --collect-only -q` ->
  `no tests collected (1609 deselected)`, **exit code 5**. See Findings - the
  Researcher's "still exit 0" is wrong, the conclusion is not. `Makefile:20-37`
  confirms the five marker-based targets; `backend/tests/conftest.py:30-47`
  declares `slow`, `flaky`, `integration`, `acceptance`, `gui`, `network`,
  `process_kill`, `provider_e2e`.
- **R-005 (item 002)** - no `pytest.ini`, `pyproject.toml` or `setup.cfg` exists
  at the repo root (three `Get-ChildItem` checks, all empty). The pytest 8.4.2
  header has **no** `configfile:` line; pytest only prints that line when a
  config file is found. `concurrency` and `learning_soak` are applied but not
  declared (confirmed by the run output: `PytestUnknownMarkWarning` for
  `learning_soak` at `backend/tests/test_phase115_soak.py:15`).
- **R-004 (item 003)** - the stale numbers are real and widespread. A
  `Select-String` for `692|683|659` over `README.md` + `docs/**/*.md` returns:
  `README.md:6` (badge, 683), `README.md:103` (692), `docs/OPERATIONS.md:159`
  (692), `docs/PROJECT_STATUS.md:41` (692), `docs/ROADMAP.md:24` (659),
  `docs/TESTING.md:7` and `:30` (692). `196 passed, 2 skipped` appears at
  `README.md:104`, `docs/OPERATIONS.md:160`, `docs/PROJECT_STATUS.md:42`,
  `docs/ROADMAP.md:25`, `docs/TESTING.md:9` and `:31`. The runtime estimates are
  at `docs/TESTING.md:39`, `:40`, `:41`, `:111`, `:112`. The badge (683) and the
  prose (692) of the same README contradict each other. My own collect-only run
  reports **1609 collected**, so every documented figure is below the collected
  count - the direction of R-004 is not in doubt; the exact 1564/45 figures
  remain the Researcher's, unconfirmed by me.
- **R-016 (item 004)** - verified visually at all four sites:
  `backend/tests/test_phase105_concurrency.py:530`
  (`assert len(tids_in_hist) == 1 or True`), `:746`
  (`assert not fail_res.get("fallback") is False or True`),
  `backend/tests/test_phase1075_hardening.py:765`
  (`assert analysis.get("delegation_id") is None or True`),
  `backend/tests/test_phase125_planning_validation.py:230`
  (`assert "actual_policy" in d and ... or True`). The dead `side_effect`
  assignment that produces the never-awaited `fake_chat` coroutine is at
  `backend/tests/test_phase105_concurrency.py:730`, in the same file as the
  `:746` tautology.
- **R-017 (item 003)** - `docs/CODEBASE_BOUNDARIES.md:213-215` and `:221-222`
  list `backend/services/speech_act_classifier.py` and
  `backend/services/response_strategy.py`;
  `backend/services/README.md:55-56` repeats them;
  `Test-Path backend/services/response_strategy.py` -> False,
  `Test-Path backend/services/speech_act_classifier.py` -> False;
  `docs/ROADMAP.md:36` and `docs/PROJECT_STATUS.md:16-18` still advertise
  "response strategy" as current.
- **R-012/R-013 (item 005)** - `scripts/smoke_test.py:21`
  (`BASE_URL = "http://127.0.0.1:8000/api/v1"`) and `cli.py:26` hard-code port
  8000; `backend/cli.py:25-29` re-derives the handshake path from
  `%LOCALAPPDATA%\Sythion (Synth)` and its own docstring (`:20-23`) says the
  duplication is "intentionally" kept. The contract they are violating is in
  `backend/core/handshake.py:8-13` (sidecar binds an ephemeral port and writes
  `{port, token}` to `sidecar.json`), and `backend/core/storage.py:46-47`
  (`handshake_path()`) is the helper that honours `SYTHION_DATA_DIR`. The
  dev launcher already produces a handshake file
  (`scripts/launch_sidecar.py:61-72` -> `read_handshake()`), so resolving from it
  works for the documented dev flow as well as for the packaged app.
- **R-006..R-011, R-018 (rejected)** - rejected on the grounds recorded in
  `research.md`, not on re-measured numbers. The decisive facts I did check:
  `scripts/stage_python_runtime.py` is the only way to rebuild the payload and
  it destroys the committed runtime first, which is why R-008/R-009/R-010/R-011
  cannot be verified in this run; `goal.md` lists release/publishing/signing/
  installer/distribution as off-limits.

## Acceptance criteria

| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | Every one of the 18 candidates ends `adopted -> <item-id>` or `rejected` with a reason | PASS | `Select-String` over `research.md`: 18 `- Status:` lines (11 `adopted`, 7 `rejected`), 7 `- Rejected reason:` lines, 0 `queued` |
| 2 | `backlog.md` holds one block per item in the required shape | PASS | 5 blocks (`### 001`..`### 005`), each with `- Why:`, `- Acceptance:` (6-7 numbered criteria), `- Size:`, `- Risk:`, `- Likely files:`, `- Sensitivity:`, `- Status: ready` |
| 3 | `board.json` mirrors every item's status and sensitivity | PASS | `items.001..005` all `status: ready`; `001` = `infra`, the rest = `none`; `owner/branch` null, `round/verifyRounds/conflicts` = 0; `roles` block copied verbatim from the previous file |
| 4 | No item behaviourally changes auth, secrets, DB schema, the public API or a safety gate | PASS | No item touches `backend/core/`, `backend/models/`, `backend/migrations/`, `backend/api/routes.py`, `backend/api/schemas.py`, `backend/tools/gui_tools.py`, `backend/tools/package_tools.py` or `backend/core/budgets.py`. Item 005's invariant (criterion 4) forbids changes under `backend/core/` and keeps the token source and origin check unchanged |
| 5 | Acceptance criteria are objectively checkable without this conversation | PASS | Every criterion names a command, a file, a grep pattern or a number that is re-measured at verification time; no criterion says "better", "clearer" or "correct" |
| 6 | Backlog size fits the run budget (5 merged items) | PASS | Exactly 5 items, all `ready`, ranked best first |
| 7 | `research.md` edited in place, not rewritten | PASS | Only `- Status:` lines changed and `- Rejected reason:` lines inserted; every candidate's Source / Source detail / Evidence / Files / Severity / Direction text is byte-identical to the Researcher's |
| 8 | Nothing outside `.orchestra/` touched; no branch, no commit | PASS | `git status --short` shows only `.orchestra/**` changes (pre-existing `.orchestra/` edits by the orchestrator included); no `git checkout -b`, no `git commit` |

## Findings

1. **Correction to R-003.** The Researcher's evidence says `make test-gui` and
   `make test-network` "run zero tests and still exit 0". They do not: pytest's
   "no tests collected" exit code is **5**, and I measured it
   (`-m gui --collect-only -q` -> `no tests collected (1609 deselected)`,
   `EXIT=5`). So those targets fail loudly; they do not silently pass. The
   candidate's substance survives - the targets still claim selections they do
   not perform, `docs/TESTING.md:37-58` still describes them as meaningful
   selections, and `make test-fast` still deselects exactly one of 1609 tests -
   so item 002 stays, but its criterion 3 is written as "no Makefile selection
   can pass while collecting zero tests", not "they currently pass silently".
2. **Correction to R-005's wording.** "the repo runs with `inifile: None`" is a
   pytest-7-era phrase; on the installed pytest 8.4.2 the terminal writer only
   emits `configfile: <path>` when a config file exists, so the observable today
   is the *absence* of that line. Item 002's criterion 1 is phrased against the
   observable ("the header names the repo-root config file"), not against the
   Researcher's phrase.
3. **Merge-order and file-ownership plan** (the orchestrator's `conflicts`
   counter is real, so I planned around it). Each item owns a disjoint file
   set: 001 -> `.github/workflows/ci.yml`, `frontend/package.json`; 002 ->
   `Makefile`, `pytest.ini`, `backend/tests/**`; 003 -> `scripts/*.py` and the
   documentation files; 004 -> three test files + one new guard test; 005 ->
   `scripts/smoke_test.py`, `cli.py`, `backend/cli.py`, one new test,
   `docs/OPERATIONS.md`. Two deliberate overlaps remain, in different regions:
   `docs/OPERATIONS.md` (003 edits the baseline block at :159-161; 005 edits the
   smoke-test block at :85-115) and `docs/TESTING.md` (003 owns it outright).
   Item 002 therefore does **not** touch `docs/TESTING.md` at all - the marker
   and command tables are documented by item 003.
4. **One ordering dependency.** Item 003 documents the CI pipeline section and
   the Makefile command/marker tables, so it must be merged **after** 001 and
   002 (its criterion 7 says to check those sections against the merged tree).
   The ranking was ordered to make that natural: 001 -> 002 -> 003.
5. **Two candidates merged into one item on purpose.** R-004 and R-017 both
   edit the same documentation files (`README.md`, `docs/TESTING.md`,
   `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`, `docs/OPERATIONS.md`,
   `docs/CODEBASE_BOUNDARIES.md`, `backend/services/README.md`). Splitting them
   would produce two branches editing the same lines for no added value, so
   they ship as one "documentation states the truth" branch. Similarly R-001 +
   R-002 + R-014 + R-015 are one `.github/workflows/ci.yml` change, and R-012 +
   R-013 are one "operator tooling resolves the endpoint" change.
6. **Sensitivity choices.** Item 001 = `infra`: it is release/build
   infrastructure, and `.github/workflows/**` is an external path for the
   orchestrator's classifier, so the actual GitHub run becomes a follow-up
   (item 001's criterion 6 asks the orchestrator to record it). Item 005 = `none`,
   with a pinned invariant instead of a gate: it reads the handshake file's
   port and path and does not change token issuance, origin checking or the
   handshake contract, and criterion 4 forbids any diff under `backend/core/`.
   All other items are `none`.
7. **The four packaging candidates (R-008..R-011) are one family.** All four
   need the release payload to be rebuilt, and the only script that rebuilds it
   (`scripts/stage_python_runtime.py`) deletes the committed 202.9 MB runtime
   first - so none of them is verifiable inside this run, and all four are
   release/distribution changes that `goal.md` puts off-limits. They are
   rejected with that reason, and each rejection names the concrete follow-up
   for the human release owner rather than dropping the finding.
8. **R-007 was rejected despite being the highest-severity defect.** A native
   COM fault that appears in roughly one run in two cannot be turned into an
   acceptance criterion that anyone can check on demand, and the only in-repo
   containment would change `backend/tools/gui_tools.py` - the file that holds
   the off-limits elevated-window / secure-desktop denial gate. Rejecting it
   with an explicit follow-up (isolate the `sythion-uia-mta` worker in a child
   process, prove containment with an injected-fault test, measure the failure
   rate) is more honest than shipping an item whose acceptance criteria could
   not be evaluated.
9. **`C:\Sythion\SYTHION_TODOS.md` is deliberately out of scope** for item 003.
   R-004 lists it as a stale source ("349 backend tests green"), but it lives
   outside this repository (the parent directory is a separate git repo), and
   the brief forbids touching files outside `.orchestra/`. It is flagged in the
   item's report instead.

## Not checked

- **I did not run the full backend suite.** Two collect-only invocations
  (135 s and a few seconds) were enough for the claims I used; the
  1564 passed / 45 skipped / 533 s figures remain the Researcher's, unconfirmed
  by me, exactly as the brief states.
- **I did not re-derive the orchestrator's confirmed facts** for R-001, R-002,
  R-003 (marker usage), R-007 or R-016 beyond the targeted checks listed above.
  I did independently confirm the R-003 exit code and the R-005 header wording,
  which is where I found the two corrections.
- **I did not measure R-006, R-008, R-009, R-010, R-011 or R-018.** I did not
  count the 184 `utcnow()` call sites, did not verify the 21.6 MB of duplicate
  archives, did not diff `requirements.lock.txt` against the staged
  `*.dist-info` set, did not confirm that pytest/coverage are present in the
  shipped runtime, and did not check all thirteen root scripts (I read
  `verify_tools.py` and `test_work_state_bug.py` only). The rejections of those
  candidates rest on `goal.md`'s off-limits list and on verification cost, not
  on re-measuring their numbers.
- **I did not reproduce R-012 or R-013.** No sidecar was launched, no request
  was made to a live backend, and `scripts/smoke_test.py` was not executed.
  Their failure modes are read from the handshake contract, the launcher and the
  storage helper.
- **I did not run vitest.** I did not execute `npx vitest run`; the 5 files /
  49 tests figure is the Researcher's, and I did not check that `node`/`npm`
  are on PATH here.
- **I did not check the ~38 documents the Researcher did not read** for further
  doc-vs-code drift, and I did not audit `docs/acceptance-test-map.md`,
  `docs/release-plan.md` or `docs/DECISIONS.md`. Item 003's path-existence
  check (criterion 6) will surface some of it.
- **I did not verify the orchestrator's own tooling.** `status.py` and
  `verify.py` are not in this repository, so I could not confirm how they treat
  the backlog's HTML comment. The scaffold comment contained an example
  `### 001 Short title` heading and example `- Status:`/`- Size:` lines that a
  naive line-based parser could read as a phantom item, so I replaced that
  comment with a parse-safe one and verified with my own parser that the
  backlog yields exactly five items whose status and sensitivity match
  `board.json`.
- **I did not build the Tauri shell, the release payload or the installer**,
  did not run `scripts/stage_python_runtime.py` (it would have deleted the
  committed runtime), did not run `scripts/verify_release_payload.py`, and did
  not touch `backend/sythion.db` or `%LOCALAPPDATA%\Sythion (Synth)`.
- **I did not check whether the git hooks or a pre-commit hook validate the
  backlog format**, and I did not commit anything - so the CRLF/LF normalisation
  that `core.autocrlf=true` will apply to the files I wrote has not been
  exercised yet.
