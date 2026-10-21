# Backlog

<!-- Scout's ranked backlog (cycle 3). One block per item the run can build next,
     in value order, then the deferred references. Each proposed item's status is
     mirrored in board.json (items.<id>.status). Stage order:
     proposed -> ready -> planned -> building -> in-review -> verifying ->
     accepted -> merged. -->

### 017 Event ledger counter-table flag must not stick after a rolled-back append
- Why: R-025 - `DbEventLedger._counter_table_checked` (`backend/runtime/events.py:102-110`) is set to `True` unconditionally, before the allocation attempts, even though the unguarded `CREATE TABLE` runs in the caller's transaction. A caller that rolls back (the documented contract for `append_event` is "does NOT commit") takes the table creation with it, but the flag survives on the same `DbEventLedger`; the next append skips creation and `_ALLOCATION_SQL` then fails with `no such table: task_event_sequences`. The schema migration in `backend/core/db_manager.py:148-158` is the correct owner of this object; the code treats its existence as a one-time boolean. The same flag can also be left `True` after a failure inside the allocation loop.
- Acceptance:
  1. A regression test (new or in `backend/tests/test_event_ledger_sequence_invariant.py` / `backend/tests/test_runtime/test_event_ledger_concurrency.py`) fails before the fix and passes after it: on a fresh DB, force one rolled-back `append_event` on a ledger, then assert the next `append_event` on the *same* ledger succeeds and persists the event.
  2. Two variants of the roll-back are covered: (a) a caller rollback after a successful `append_event` that is not committed, and (b) an `EventSequenceAllocationError` / failed allocation inside the savepoint loop. The test names which variant each sub-case exercises.
  3. `git diff --name-only` lists no file under `backend/models/` or `backend/migrations/`; the `uq_task_events_task_sequence` unique index on `task_events (task_id, sequence_num)` keeps the same definition; `backend/core/db_manager.py:128-131` is unchanged.
  4. The fix lives in `backend/runtime/events.py` only (plus its test). Creation is transactional and re-checkable: either always run idempotent `CREATE TABLE IF NOT EXISTS`, or move the flag to `False` on allocation failure. `append_event` and `event_history` keep their signatures and `_to_dict` (`:82-88`) keeps its keys/values.
  5. The concurrency assertion `backend/tests/test_event_ledger_sequence_invariant.py` (or equivalent) still passes 10 consecutive runs, and the full suite keeps the pre-change passed/skipped counts (CPython 3.14.7).
- Size: S
- Risk: med (the write path of the audit/replay ledger; a half-stuck flag turns a successful caller rollback into a hard "no such table" on the next call)
- Likely files: `backend/runtime/events.py`, `backend/tests/test_event_ledger_sequence_invariant.py`, `backend/tests/test_runtime/test_event_ledger_concurrency.py`
- Sensitivity: sensitive
- Domain: data
- Status: merged (merge sha `29bb243`, branch `orchestra/017-counter-flag`, verifier FIXED)

### 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Why: R-026 - item 007's guard in `backend/tests/conftest.py:622-742` raises `ProductCredentialWriteAttempt` before any `win32cred` call, which is correct for code that calls `CredentialManagerStore` directly. But `backend/main.py:274-285`'s `@app.exception_handler(Exception)` turns any unhandled exception into a 500 (`JSONResponse(status_code=500, ...)`), so a test that drove a vault write through `backend.main.app` would see a 500 indistinguishable from any other failure - the write would still be blocked, but the test would not go red. No acceptance test uses `main.app` today (grep: no match), so the hole is latent; the conftest docstring already states the consequence.
- Acceptance:
  1. A regression test constructs `TestClient(backend.main.app)`, triggers a product-prefixed (`Sythion (Synth)/...`) vault write, and asserts a distinct, documented outcome - not a generic 500 and not a silently-2xx response. It fails before the fix and passes after it.
  2. The implementation chooses one documented shape and sticks to it: either `backend/main.py` re-raises / maps `ProductCredentialWriteAttempt` to a distinct status code, or the guard's exception type is made visible to the handler. `ProductCredentialWriteAttempt` is defined/recognised consistently between `backend/main.py` and `backend/tests/conftest.py`.
  3. The handler keeps handling genuine unexpected exceptions as 500s; no other response contract changes. `backend/core/secrets.py`'s store selection, `set_secret`, `has_secret` and `resolve_ollama_api_key` keep their current behaviour.
  4. `git diff --name-only` lists only `backend/main.py`, `backend/tests/conftest.py` and the new test file. No model, migration or schema file is touched. No product credential is written by any test.
  5. The full suite keeps the pre-change passed/skipped counts.
- Size: S
- Risk: med (operator-facing API surface and a safety guard; the change must keep "blocked" observable without weakening the 500 contract for real faults)
- Likely files: `backend/main.py`, `backend/tests/conftest.py`, `backend/tests/test_vault_guard_exception_surfaces.py`
- Sensitivity: sensitive
- Domain: security
- Status: merged (merge sha `25a9c02`, branch `orchestra/018-vault-guard-surface`, verifier FIXED)

### 019 Tautology detector must also catch nested and bare `or True`
- Why: R-027 - `backend/tests/test_no_tautological_asserts.py` only matches a top-level `ast.BoolOp(ast.Or)` containing `ast.Constant(True)`, so `assert (x or True) and y`, `assert not (x or True)`, `assert x or (y or True)` and `assert True` (all unconditionally true) defeat the guard that exists precisely to prevent them. The plan's claim that the AST rule is "a superset of the literal `... or True` text pattern" is false for those four shapes. The acceptance case for item 004 only asked for the top-level case, so item 004 stays green - but the guard is a canary, not a proof.
- Acceptance:
  1. `_is_tautology` recurses through `ast.And`, `ast.UnaryOp(ast.Not)` and grouping so a literal `True` at any guard position is flagged, and a bare `ast.Constant(True)` assert is flagged. The four new shapes from R-027 each cause the detector to fail when present.
  2. The detector's self-test (its tooth-check) gains one probe per new shape, and removing any one probe's tautology makes that probe pass while re-introducing it fails again.
  3. The detector's docstring no longer claims to catch patterns it does not catch; the claim matches the implementation.
  4. No false positives on the existing legitimate assertion shapes (verified by running the detector over the whole `backend/tests/` tree; exit 0).
  5. No production file is touched; `git diff --name-only` lists only `backend/tests/test_no_tautological_asserts.py` **plus** `backend/tests/test_conversation_memory.py` and `backend/tests/test_phase12_planning_learning.py`. No other file. **AMENDED by the orchestrator (2026-10-22):** the original clause said "and that file alone, since no other test file changes", on the unmeasured assumption that the stricter rule would hit nothing. The Planner MEASURED that the stricter rule hits exactly two existing sites, and both are genuine placeholder tautologies (`assert True`): `test_conversation_memory.py:637` ("If we reach here, the verification code ran without error" - a read-back verification property that is asserted by nothing) and `test_phase12_planning_learning.py:366` in `test_learned_planning_cannot_change_runtime_permissions` ("No goal mutation via planning" - a security invariant asserted by nothing). Those two are FIXED in this item rather than exempted, so the guard applies uniformly to the whole tree with no exemption list, and the diff is a three-file test-only change.
- Size: S
- Risk: low (test-only guard; a too-strict rule would make the suite red on legitimate code, which criterion 4's full-tree run catches)
- Likely files: `backend/tests/test_no_tautological_asserts.py`
- Sensitivity: none
- Domain: none
- Status: merged (merge sha `df75bb6`, branch `orchestra/019-tautology-detector`, verifier FIXED)

### 020 The `.env` isolation must be measured, not assumed, and cover the process environment
- Why: code-tractable follow-up from item 006. The 006 verifier measured that (a) with the autouse isolation deleted, the readiness file is still green in a tree that has no `.env` - so "deleted isolation goes red on CI" is false - and (b) `SYTHION_OLLAMA_API_KEY` exported in the child process is NOT neutralised, because the clean snapshot uses `Settings(_env_file=None)` which still reads the process environment. The verification-written follow-ups (unverified isolation, isolation covers the file but not the environment) name the exact measurement that is missing.
- Acceptance:
  1. A new test spawns a pytest subprocess against a scratch copy of the tree with a generated `backend/.env` containing a sentinel `SYTHION_OLLAMA_API_KEY`, and asserts the autouse isolation neutralises it (the readiness file stays green with the same pass/fail set as the `.env`-absent tree); removing the isolation makes that subprocess run fail. The test performs no network I/O and touches no real profile directory.
  2. Scope decision recorded in the item report and enforced by code: either the isolation also clears `SYTHION_OLLAMA_API_KEY` and the other `SYTHION_*` variables from the process environment before snapshotting (preferred - makes the conftest docstring's "can never introduce a credential" claim true), or the docstring is corrected to state the file-only scope with the precise caveat. The chosen branch has a test asserting it.
  3. The `file_roots` swap documented in the 006 verifier's follow-up is captured in one conftest comment: with ambient `file_roots` set the approved roots change (not just their count).
  4. `_LOCAL_ENV_FIELDS` (or its replacement) is imported by the isolation test instead of being re-hardcoded, so a field added later is covered.
  5. Product behaviour is unchanged: `git diff --name-only` lists only files under `backend/tests/` (and an optional helper under `scripts/` if the nested run needs one). No `backend/core/` file is touched.
  6. The full suite keeps the pre-change passed/skipped counts; no test is deleted, skipped or marked.
- Size: M
- Risk: low (test-only; the risk is a flaky subprocess-based check, addressed by a temp directory and a fixed timeout)
- Likely files: `backend/tests/conftest.py`, `backend/tests/test_env_isolation.py`, `backend/tests/test_setup_readiness.py`
- Sensitivity: none
- Domain: none
- Status: proposed

## Deferred (referenced, not re-listed)

- Board items already deferred and unresolved, referenced instead of re-listed: `005` (operator endpoint resolution, external/infra), `008` (concurrent append event loss in `DbEventLedger.append_event`, data), `009` (readiness/onboarding endpoints read the key directly, security - sensitive-change gate).
- Human/external follow-ups stay in `follow-ups.md` and are NOT code items: the provider-gated credentialed run, the native-crash twin (Fault A in `gui_tools.py`, Fault B in `coordination.py:1127`), the clean-machine interactive acceptance run, runner-side and release-payload work (vosk lock drift, 253 MB tracked runtime, installer pytest stack, dead `voice_service.py`), the credential-vault rotation note after item 007, and the tauri-build no-op assert (operator/CI-owned).
- Code-tractable follow-ups evaluated this cycle but deliberately not promoted: `BOM-safe load` in `scripts/measure_test_baseline.py` (`utf-8-sig`, one line); the duplicated pytest config / second registry copy / hand-maintained "53 tests" count; the vendored `src-tauri/resources/backend/services/README.md` stale module list (release payload); stale `C:\Ai\sythion` paths in `cli.py:543` and `scripts/smoke_test.py:212` plus the root ad-hoc scripts (already rejected as R-018, too low-value); the admission cross-process test (`test_admission_concurrency_process.py:114`, `assert ... <= 2`) - pre-existing master failure whose root cause needs reproduction first; and the soak docstring one-liner. All remain recorded in `follow-ups.md`; they are deferred because each is either external/release-owned, gated on a human run, or needs a reproduction step that does not fit a bounded item this cycle.

## New backlog top order

1. `017` - event ledger counter-table flag (R-025) - med, S
2. `018` - vault-write guard visible through the FastAPI handler (R-026) - med, S
3. `019` - tautology detector nested/bare `or True` (R-027) - low, S
4. `020` - `.env` isolation measured plus process-env coverage (item 006 follow-ups) - low, M