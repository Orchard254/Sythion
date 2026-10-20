# Backlog

<!-- Scout's ranked backlog for this run: one block per item, best first.
     Every block opens with a three-digit item heading followed by a title,
     and carries the fields Why, Acceptance (a numbered list), Size, Risk,
     Likely files, Sensitivity and Status - one field per line, spelled
     exactly as in the run's item contract.
     Every status change is mirrored into board.json (items.<id>.status);
     board.json is authoritative and status.py reports any mismatch as drift.
     The stage order is fixed and no stage is skipped:
     proposed -> ready -> planned -> building -> in-review -> verifying ->
     accepted -> merged.
     This run's five items are all sized and queued as ready; the remaining
     research candidates were rejected with reasons recorded in research.md. -->

### 001 CI actually gates this repository
- Why: goal.md's "release and operator readiness" and "verification before claims" (docs/QUALITY_GATES.md:34-44, :88-97) rest on a CI pipeline that has never gated this repository: R-002 (the workflow triggers on `main`; the only branch is `master`, so a push skips all four jobs) and R-001 (the `backend-tests` job runs its import gate from `backend/`, where `import backend.main` fails with `ModuleNotFoundError`, so the one gate that does exist is red for a reason unrelated to the code). R-014 (the 49-test vitest suite is never executed by CI) and R-015 (CI installs pytest unpinned on top of the pinned requirements, so the version exercised is never the declared one) are the same story: the pipeline does not test what the repository says it tests. One file, one branch, one reviewable change.
- Acceptance:
  1. `.github/workflows/ci.yml` declares `on.push.branches` containing `master` (verified branch set: `git branch -a` -> `master`, `remotes/origin/master`; there is no `main`), and the workflow can also be started by hand (`workflow_dispatch`).
  2. The import-gate step's exact command, executed from the working directory that step declares, exits 0. Proof recorded in the item report: the command as written in `ci.yml` run locally with its declared `working-directory` -> exit code 0 (today `cd backend && python -c "import backend.main; import backend.api.routes"` -> exit 1, `ModuleNotFoundError: No module named 'backend'`).
  3. The `frontend` job contains a step that runs the vitest suite in non-watch mode, and `frontend/package.json` maps a non-interactive script to it (`vitest run`, with `vitest` watch kept under a separate `test:watch` script). Proof recorded: `cd frontend && npm test` exits 0 and reports 5 test files / 49 tests passed (the same count the suite reports today).
  4. No `pip install` line in `.github/workflows/ci.yml` installs `pytest` or `pytest-asyncio` without a version constraint, and the version CI installs is the one pinned in `backend/requirements.txt` (`pytest==7.4.4`, `pytest-asyncio==0.23.3`).
  5. `git diff --name-only` for this item lists only `.github/workflows/ci.yml` and `frontend/package.json` - no runtime module, documentation file or release-payload file is touched.
  6. The orchestrator records in `.orchestra/follow-ups.md` that the repaired workflow must be observed on GitHub after the merge; running it is outside the repository and is never performed here.
- Size: M
- Risk: low (workflow YAML plus one package.json script; no runtime code, no secrets, no payload or auth surface)
- Likely files: `.github/workflows/ci.yml`, `frontend/package.json`
- Sensitivity: infra
- Status: merged

### 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Why: R-003 and R-005 are one story and both are Gate 4 + Gate 8 failures. The Makefile's marker targets select nothing (`-m gui`, `-m network`, `-m acceptance` collect 0 of 1609 tests; `-m "not slow and not flaky and not integration and not gui and not network"` deselects exactly 1), and the repository has no pytest configuration file at all, so the suite runs with no `configfile:` and two markers that are applied to real tests (`concurrency`, `gated_soak`) are never declared. Every future agent that trusts `make test-fast` or `make test-gui` is wrong about what ran. This item deliberately does not edit `docs/TESTING.md`; item 003 owns that file and documents the final state.
- Acceptance:
  1. A pytest configuration file exists at the repository root (`pytest.ini` or `pyproject.toml`), and the header of `python -m pytest backend/tests --collect-only` names it (a `configfile:` line pointing at the repo-root file). Today that header has no `configfile:` line at all.
  2. Every marker applied anywhere under `backend/tests/` is declared, specifically `concurrency` and `gated_soak`, and a full-suite run emits zero `PytestUnknownMarkWarning` (today: 3 warnings, from `backend/tests/test_phase105_concurrency.py:39`, `backend/tests/test_phase1075_hardening.py:35`, `backend/tests/test_phase115_soak.py:15`).
  3. No Makefile selection can pass while collecting zero tests: for every `-m` expression used in the `Makefile`, `python -m pytest backend/tests -m "<same expression>" --collect-only -q` reports a collected count greater than zero, or the target that uses it has been removed from the `Makefile`. A marker applied to zero tests is either applied to the tests it describes or dropped from the Makefile's expressions.
  4. The suite's behaviour is unchanged apart from the intended selection fixes: `asyncio_mode` is set explicitly to `strict` (the mode the header reports today), the number of collected tests is identical before and after the change (1609 on CPython 3.14.7 at the time of the change), and `python -m pytest backend/tests -q` exits 0 with the same passed/skipped counts as the pre-change baseline.
  5. No warning-to-error filter is introduced: there is no `filterwarnings = error::DeprecationWarning` (or any new `error` filter) in the new configuration, and the suite's warning count does not increase and does not turn into failures. The `datetime.utcnow()` deprecation flood is explicitly out of scope for this item.
  6. `git diff --name-only` lists only `Makefile`, the new root pytest configuration file, `backend/tests/conftest.py` and test files whose marker classification was corrected; no file under `backend/` outside `backend/tests/` is touched.
- Size: M
- Risk: med (correcting which tests a marker selects changes what `make test-fast` and `make test-unit` run; a wrong classification would silently narrow the suite)
- Likely files: `Makefile`, `pytest.ini`, `backend/tests/conftest.py`, `backend/tests/**`
- Sensitivity: none
- Status: building

### 003 Documentation states the measured baseline and drops deleted modules
- Why: R-004 and R-017 are both Gate 4 ("docs must reflect reality") and Gate 9 ("honest handling of debt"), and they edit the same documentation files, so they ship as one branch. R-004: every documented baseline in the repository is wrong - 692 passed / 32 skipped (README:103, docs/TESTING.md:30, docs/PROJECT_STATUS.md:41, docs/OPERATIONS.md:159), 683 (README badge :6), 659 (docs/ROADMAP.md:24), 196 acceptance (README:104, docs/TESTING.md:9, docs/PROJECT_STATUS.md:42, docs/ROADMAP.md:25, docs/OPERATIONS.md:160) - while 1609 tests are collected, and the quoted runtimes ("~55s", "~25s", "~10s") are off by roughly 10x. The badge and the prose of the same file contradict each other. R-017: `docs/CODEBASE_BOUNDARIES.md` (named by docs/QUALITY_GATES.md:50-53 as an authority doc), `backend/services/README.md`, `docs/ROADMAP.md:36` and `docs/PROJECT_STATUS.md:16-18` still list `backend/services/response_strategy.py` and `backend/services/speech_act_classifier.py`, both deleted by commit `db10f86`.
- Acceptance:
  1. `scripts/measure_test_baseline.py` exists and, run from the repository root, executes the backend suite and the acceptance suite and prints a machine-readable summary (JSON) containing at least: passed, skipped, collected, duration_seconds, python_version, platform. The script hard-codes no counts: `grep -E "1564|1609|692|683|659|196" scripts/measure_test_baseline.py` matches nothing.
  2. The baseline quoted in `README.md` (badge and prose), `docs/TESTING.md`, `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md` and `docs/OPERATIONS.md` equals the numbers printed by the last executed run of that script, and each of those documents states the interpreter and OS the numbers were measured on (and that CI pins Python 3.11).
  3. The README badge and the README prose state the same numbers (today: badge "683 passed / 2 skipped" vs prose "692 passed, 32 skipped" in the same file).
  4. The runtime estimates "~55s", "~25s" and "~10s" in `docs/TESTING.md` are replaced by measured values or explicitly labelled as machine-specific.
  5. The unverified live claim "Currently 6/6 live" (docs/TESTING.md and docs/OPERATIONS.md) is removed or re-labelled as a manual check that no executed run in this repository produced.
  6. `backend/services/response_strategy.py` and `backend/services/speech_act_classifier.py` appear in none of `docs/CODEBASE_BOUNDARIES.md`, `backend/services/README.md`, `docs/ROADMAP.md`, `docs/PROJECT_STATUS.md`, and a committed check script (e.g. `scripts/check_doc_paths.py`) that resolves every `backend/...` path mentioned in those four documents exits 0 and reports zero missing paths.
  7. `docs/TESTING.md`'s "Common Test Commands" table, its "Pytest Markers" table and its "CI Pipeline" section describe the `Makefile` and `.github/workflows/ci.yml` as they exist in the merged tree. This item owns `docs/TESTING.md` and is merged after items 001 and 002 so those sections can be checked against the final files.
- Size: M
- Risk: low (documentation plus two small read-only scripts; no runtime behaviour, no payload, no auth surface)
- Likely files: `scripts/measure_test_baseline.py`, `scripts/check_doc_paths.py`, `README.md`, `docs/TESTING.md`, `docs/PROJECT_STATUS.md`, `docs/ROADMAP.md`, `docs/OPERATIONS.md`, `docs/CODEBASE_BOUNDARIES.md`, `backend/services/README.md`
- Sensitivity: none
- Status: ready

### 004 Four tautological assertions replaced by the assertions they stood in for
- Why: R-016. Four assertions end in `or True`, so they are unconditionally true and can never fail, each inside a suite whose name promises exactly the property it does not check: per-thread history isolation (`backend/tests/test_phase105_concurrency.py:530`), fallback handling (`:746`), delegation attribution (`backend/tests/test_phase1075_hardening.py:765`) and shadow-policy shape (`backend/tests/test_phase125_planning_validation.py:230`). Gate 3 ("verification before claims") and Gate 8 ("testing baseline discipline") both fail while four suites advertise guarantees they do not check.
- Acceptance:
  1. `grep -rn "or True" backend/tests` matches nothing; each of the four sites listed above is replaced by the concrete assertion it stood in for, or the property is re-stated as an explicit negative assertion.
  2. The three affected files still pass: `python -m pytest backend/tests/test_phase105_concurrency.py backend/tests/test_phase1075_hardening.py backend/tests/test_phase125_planning_validation.py -q` exits 0.
  3. If a real assertion fails, the outcome is a fix in the production code or a defect reported in the item's report - a failing real assertion is never resolved by restoring `or True` (criterion 1 makes that detectable).
  4. The dead `side_effect` assignment in `backend/tests/test_phase105_concurrency.py` (the never-awaited `fake_chat` coroutine) is removed, and the run no longer emits `RuntimeWarning: coroutine 'TestModelCallConcurrency.test_provider_calls_isolated.<locals>.fake_chat' was never awaited`.
  5. A committed guard prevents re-introduction: a check (e.g. `backend/tests/test_no_tautological_asserts.py`, or a script under `scripts/`) fails when an `assert` statement under `backend/tests/` contains a top-level `or True`; it runs as part of the suite and exits 0 today.
  6. `python -m pytest backend/tests -q` exits 0 with the same passed/skipped counts as the pre-change baseline - no test is deleted, skipped or marked to make the suite green.
- Size: S
- Risk: med (the tautologies were hiding the real outcome, so the correct assertion may expose a genuine defect in the concurrency/hardening code)
- Likely files: `backend/tests/test_phase105_concurrency.py`, `backend/tests/test_phase1075_hardening.py`, `backend/tests/test_phase125_planning_validation.py`, `backend/tests/test_no_tautological_asserts.py`
- Sensitivity: none
- Status: in-review

### 005 Operator tooling resolves the endpoint from the handshake file
- Why: R-012 and R-013 are one story about the operator release gate. The handshake contract says the sidecar binds 127.0.0.1 on an ephemeral port and publishes `{port, token}` in `sidecar.json` (`backend/core/handshake.py:8-13`), but `scripts/smoke_test.py:21` and the root `cli.py:26` hard-code `http://127.0.0.1:8000/api/v1` - so when port 8000 is occupied both tools talk to whatever else is listening there, carrying the control token with them - and `backend/cli.py:25-29` re-derives the handshake path instead of using `storage.handshake_path()`, so it cannot find a sidecar launched with a custom `SYTHION_DATA_DIR` (the packaged shell always sets one). `docs/OPERATIONS.md:108` documents the smoke test as a release gate, and `docs/acceptance-test-map.md` lists it as such.
- Acceptance:
  1. `scripts/smoke_test.py` and the root `cli.py` resolve their base URL from the handshake file (`sidecar.json` -> `port`) through `backend/core/storage.py::handshake_path()` (so `SYTHION_DATA_DIR` is honoured) and keep their control-token lookup pointed at the handshake/token files only. With a handshake file containing port 54321 they target `http://127.0.0.1:54321`; with no handshake file they exit with a message naming the path they looked at and never silently fall back to port 8000. An explicit `--url`/`--port` override remains available for the documented dev flow.
  2. `backend/cli.py` resolves the same file through `storage.handshake_path()`; the duplicated `%LOCALAPPDATA%\Sythion (Synth)` derivation and its "intentionally duplicated" note are gone.
  3. A committed test (e.g. `backend/tests/test_cli_endpoint_resolution.py`) asserts, under a temporary `SYTHION_DATA_DIR`, that the resolver used by `scripts/smoke_test.py` / `cli.py` and the one used by `backend/cli.py` resolve to the same path and the same base URL, and that the port comes from the handshake file. The test performs no network I/O and touches no real profile directory.
  4. Invariant, no auth change: `git diff --name-only` lists no file under `backend/core/` (in particular `handshake.py`, `auth.py` and `profile_lock.py` are untouched), the control token is still read from the handshake/token files only - no new token source - and the `X-Control-Token` / `Origin` handling in the edited tools is unchanged.
  5. `docs/OPERATIONS.md` states how the endpoint is resolved (handshake file) and keeps the explicit port override for the documented dev flow on port 8000. This item touches only the smoke-test section of that file; the "Current verified baseline" section belongs to item 003.
  6. `python -m pytest backend/tests -q` exits 0 with the same passed/skipped counts as the pre-change baseline.
- Size: M
- Risk: med (operator tooling that carries the control token; the change must not alter what is authenticated or where the token comes from - pinned by criterion 4)
- Likely files: `scripts/smoke_test.py`, `cli.py`, `backend/cli.py`, `backend/tests/test_cli_endpoint_resolution.py`, `docs/OPERATIONS.md`
- Sensitivity: none
- Status: ready
