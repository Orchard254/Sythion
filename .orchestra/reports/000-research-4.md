# Research pass - cycle 4 (post-merge, fresh pass)

**Role:** research (read-only)
**Brief:** `.orchestra/briefs/000-research-4.md`
**Report:** `.orchestra/reports/000-research-4.md` (this file - the only file written)
**Scope:** fresh scan of the merged post-`007`/`008`/`009`/`011`/`012`/`013`/`015`/`016`
tree. Cross-referenced against `research.md` (R-001..R-024) and the open
follow-ups in `follow-ups.md`; no candidate below duplicates an existing
R-id. The follow-up cycle landed fixes in the event ledger (008), the test
isolator and vault guard (006/007), and operator endpoint resolution (015);
the three new candidates below are debts those or earlier changes leave
behind, all code-fixable.

Baseline for this pass was taken as read from the brief and the merged tree
(1621 collected post-merge); **no full suite was re-run**, and no source file
was edited.

## Sources cross-referenced

- `research.md` - R-001..R-024 (status: adopted / rejected, with assigned
  item ids), so none of R-025..R-027 restate a queued candidate.
- `follow-ups.md` - open operator/human items; each is re-evaluated below.
- `critiques.md` - the 008 round-1 reviewer risks (multi-append rollback,
  `latest_sequence` pending rows, allocation-in-Python) and the 008 round-2
  close-out, plus the 004/006/007 follow-up notes.
- `backend/runtime/events.py` - newly refactored (item 008, sha `d2eb448`).
- `backend/tests/conftest.py` - process-env isolation and relocated vault
  guard (items 006/007).
- `backend/cli.py`, `cli.py`, `scripts/smoke_test.py` - operator endpoint
  resolution (item 015).

## New candidates

### R-025 - `DbEventLedger._counter_table_checked` sticks after a rolled-back `CREATE TABLE`

- Source: local (item 008 regression)
- Source detail:
  - `backend/runtime/events.py:102-110` (lazy counter-table creation + the flag)
  - `backend/runtime/events.py:78-167` (`append_event`, including the savepoint
    loop that consumes it)
  - `backend/core/db_manager.py:148-158` (the schema-time migration/backfill
    that is the *other* way this table gets created)
- Evidence:
  `append_event` creates the counter table with a statement that is **not**
  wrapped in the savepoint:

  ```python
  # backend/runtime/events.py:103-110
  if not self._counter_table_checked:
      self._session.execute(
          text(
              "CREATE TABLE IF NOT EXISTS task_event_sequences "
              "(task_id VARCHAR NOT NULL PRIMARY KEY, next_seq INTEGER NOT NULL)"
          )
      )
      self._counter_table_checked = True
  ```

  The flag is then set unconditionally, before the allocation attempts. The
  statement participates in the *caller's* transaction, so a caller that
  rolls back (the documented contract for `append_event` is "does NOT commit";
  `backend/runtime/events.py:4-7`) takes the table creation with it. On the
  next call on the **same** `DbEventLedger` instance, `_counter_table_checked`
  is still `True`, so the table is never re-created, and the upsert in
  `_ALLOCATION_SQL` fails with `no such table: task_event_sequences`. The guard
  is also not savepoint-scoped: a failure inside the allocation loop can leave
  the flag `True` while nothing has committed. The counter is a *schema object*,
  but the code treats its existence as a one-time, session-local boolean
  instead of as part of the transactional state the schema migration in
  `db_manager.py:148-158` already establishes elsewhere.
- Files touched (a fix): `backend/runtime/events.py` (and a regression test in
  `backend/tests/test_event_ledger_sequence_invariant.py` or
  `backend/tests/test_runtime/test_event_ledger_concurrency.py`).
- Severity: **med**
- Code-fixable: **yes**
- Direction (not done here):
  make creation transactional and re-checkable - either drop the cached flag
  and always run `CREATE TABLE IF NOT EXISTS` (cheap, idempotent, and already
  safe under contention on SQLite), or move the flag to `False` on
  `EventSequenceAllocationError` and gate it on successful return. Add the
  missing failure-mode test: create a ledger on a fresh DB, force one rolled
  back `append_event`, then assert the next append on the same ledger succeeds.

### R-026 - the vault-write guard's exception is swallowed by the global app handler, so a future HTTP-driven write never goes red

- Source: local (carry-over of the 007 follow-up; now worth an item because the
  guard has landed in the root `conftest` and the hole is fixed-distance away)
- Source detail:
  - `backend/main.py:274-285` (`global_exception_handler`, registered as
    `@app.exception_handler(Exception)`)
  - `backend/tests/conftest.py:652-742` (the relocated `_forbid_product_credential_writes`
    guard and `ProductCredentialWriteAttempt`)
- Evidence:
  - The relocated guard raises `ProductCredentialWriteAttempt(AssertionError)`
    *before* the real `win32cred` call, which is exactly right for a unit test
    that calls `CredentialManagerStore` directly.
  - `backend/main.py:274-285` turns any unhandled exception into a 500 response
    (`JSONResponse(status_code=500, ...)`). The conftest docstring already
    states the consequence
    (`backend/tests/conftest.py:683-687`: "A future test that drove a vault
    write through `backend.main.app` would get a 500 response instead of this
    exception - the write would still be blocked, but the test would not go
    red"). No acceptance test drives a write through `main.app` today
    (`grep -rn "main.app\|backend.main.app" backend/tests` -> no match), so
    today's 1621-test tree is safe. The exposure is *latent*: the moment such a
    test is added, the guard silently stops being a check and becomes a
    circuit-breaker whose success is indistinguishable from a 500.
- Files touched (a fix): `backend/main.py` and `backend/tests/conftest.py`
  (define/recognise the violation as something that is neither a 500-producing
  crash nor a normal `HTTPException`), or the documented constraint converted
  into an enforced assertion.
- Severity: **low-med**
- Code-fixable: **yes**
- Direction (not done here):
  either make the handler re-raise / return a distinct non-2xx outcome for
  `ProductCredentialWriteAttempt` (and assert that exact outcome in a test), or
  harden the conftest comment into a `pytest.skip`-style precondition that
  fails if `main.app` is ever used for a secrets write. Either way, add a
  regression test that constructs `TestClient(app)` and asserts the guard
  surfaces (not 500) when a product-prefixed target is written.

### R-027 - the tautology detector's AST rule is narrower than its own `grep` claim

- Source: local (item 004 critique; follow-up note already queued)
- Source detail:
  - `backend/tests/test_no_tautological_asserts.py:3-16` and `:53-62`
    (detection rules: `ast.BoolOp` with `ast.Or` and a `True` constant)
  - `backend/tests/test_no_tautological_asserts.py:121-122` (the self-test that
    pins the detector has teeth)
  - `.orchestra/critiques.md` (004 review) - the over-claim recorded there.
- Evidence:
  As written, the detector matches an `assert` whose *top-level* test is `Or`
  containing the literal `True`. It does **not** match any of the following
  (each is still unconditionally true and would pass the suite today):

  - `assert (x or True) and y`
  - `assert not (x or True)`
  - `assert x or (y or True)`
  - `assert True`

  The implementation even documents this gap
  (`backend/tests/test_no_tautological_asserts.py:121-122` quotes the detector
  as needing the "top-level disjunction", i.e. NOT nested) and the 004 review
  (`critiques.md`) explicitly records that the plan's claim that the AST rule
  is "a superset of the literal `... or True` text pattern" is **false** for
  those three shapes. The acceptance criterion only asks for the top-level
  case, so `item 004` stays green - but the guard exists precisely so no
  `assert ... or True` can ever be re-introduced, and these four shapes
  defeat it.
- Files touched (a fix): `backend/tests/test_no_tautological_asserts.py`.
- Severity: **low**
- Code-fixable: **yes**
- Direction (not done here):
  extend `_is_tautology` to recur through `ast.And` / `ast.UnaryOp(ast.Not)` /
  grouping `ast.Expr` and to flag a bare `ast.Constant(True)` at any guard
  position, then broaden the detector's self-test (the tooth-check at `:113+`)
  with one probe per new shape. Also update the guard's docstring comment so
  the claim it makes matches what it actually checks.

## Revisiting the open follow-ups

Summary of the items currently open in `follow-ups.md`. Splits are
"code-fixable" vs "human/external" per the brief's requirement; each entry
is tagged whether it is **resolved by the merged follow-up cycle**, **still
code-tractable**, or **human/external**.

### Resolved by the follow-up cycle (no action left)

- `(006) process env` - F2 closed by commit `ba69cc2`: the isolator now
  clears every `SYTHION_*` variable and snapshots with the env file disabled
  (`conftest.py:500-619`). SCRIPT evidence: `_clean_settings_snapshot()`.
- `(007) vault guard relocation` - closed by `bac7308`: the guard is now in
  the *root* `backend/tests/conftest.py` (`:622-742`), not just the acceptance
  conftest; measured universal across the 1621-test tree.
- `(002) --ignore` false positive - closed by commit `8077ba5`: `_is_full_tree_collection`
  returns `False` on `--ignore`/`--ignore-glob`/`collect_ignore`
  (`conftest.py:190-228`).
- `(006) F8 file_roots docs` - closed by `7f67147` / `cfa639c`.
- `(006) F4 soak docstring` - closed by `a56e0f2` (verified `startswith("Gated")`
  is now absent).
- `(003) F6 stale pytest-report.xml` - closed by `a56e0f2`.
- `(003) F3 --check-collected cost wording` - closed by `05c03f7`.

### Still code-tractable (could become a future item without a human hand)

- `(001) tauri-build no-op assert` - **sweep commit**, one line
  (`if (-not (Test-Path "...")) { throw "..." }`). Code-only, but blocked by
  CI/release ownership today.
- `(002) F5/F7` - the duplicated pytest config in
  `build/staging/.../pyproject.toml`, the hand-maintained "53 tests" count and
  the second registry copy in `test_gui_fixture.py` (`conftest.py:85-87`,
  `LIVE_GUI_MODULES`). Tractable as hygiene; low severity.
- `(003) vendored README` still lists the deleted `response_strategy.py` /
  `speech_act_classifier.py`
  (`src-tauri/resources/backend/services/README.md`) - a one-line doc edit,
  technically inside the release payload, so file-touched but shippable as docs.
- `(003) F7 BOM-safe load` in `measure_test_baseline.py` - `utf-8-sig` one
  liner.
- `(004) nested/bare `or True`` - *this is R-027*.
- `(004) provider-gated assertion never executed` - *gated, not code* except
  that a small hostile test in the harness could exercise it; human credentialed run still required.
- `(006) F1 unverified isolation` - a nested pytest run against a generated
  `.env` would turn the isolation into a measured check; code-tractable.
- `(007) main.app swallows the guard's exception` - *this is R-026*.
- `(000) `C:\Ai\sythion`` stale paths in `cli.py:543` and
  `scripts/smoke_test.py:212` and the 13 root ad-hoc scripts - code, but the
  test-path residue was already rejected as too low-value (R-018 rejected).
- `(006) admission cross-process test` - `test_admission_concurrency_process.py:114`
  (`assert 3 <= 2`), pre-existing master failure. Code-level and tractable,
  but its root cause is concurrency timing, not isolation; needs reproduction
  before a fix.

### Human / external (unchanged in status; not code items)

- `(001)` GitHub first real run, runner-side `pip install` on Python 3.11
  (`whispercpp` source build), `RUN_PROVIDER_E2E` state verification - live.
- `(001)` optional `main` rename and workflow hardening nits.
- `(001) tauri no-op assert` is code text but operator-action-owned; listed
  as deferred on purpose (`.github/workflows/ci.yml`).
- `(004)` provider-gated credentialed run.
- `(000)` non-deterministic native crashes (Fault A `0x80040155` in
  `gui_tools.py`, Fault B `0xC0000005` sqlite3 atomicity in `coordination.py:1127`)
  - native, smallest reproducer is the *full* suite; not tractable from a
  single-test loop (see `goal.md` baseline notes).
- `(000)` scratch-worktree disk exhaustion during verification runs - tooling,
  not code.
- `vosk` lock drift, installer shipping the pytest stack, 611-line dead
  `voice_service.py` and the 253 MB tracked runtime - release/distribution,
  off-limits (`scripts/stage_python_runtime.py` `rmtree`s the committed runtime),
  stays with the release owner.
- Secret-rotation / vault state after item 007.

## Limits of this pass

- No full suite was run; no collect-only run was performed. Counts above come
  from the brief's post-merge figure (1621 collected).
- No CI was executed and no Tauri shell was built; the live smoke-test path
  was read, not run.
- No web/advisory sources; no CVE claims.
- `src-tauri/resources/backend/cli.py` was compared to `backend/cli.py` via the
  existing endpoint-resolution test but was not executed standalone.
- The three new candidates are proposed from static reading of the merged
  tree; each carries an "Add the missing failure-mode test" instruction, and
  none is filed as an item by this report - the board decision is the Scout's.

## Conclusion

Three new code-fixable candidates are proposed: **R-025** (event-ledger counter
table flag surviving a rolled-back transaction), **R-026** (vault guard's
exception disappearing inside the FastAPI global handler), and **R-027**
(tautology detector missing nested / bare `or True` and bare `assert True`).
Each has a concrete, bounded fix and a named missing test. The follow-up
cycle resolved seven more of the deferred items than were open a cycle ago;
the ones that remain open and code-tractable are listed above, and the rest
stay with the human / release owner.

No file other than this report was created or modified. No branch, commit, or
push was made.