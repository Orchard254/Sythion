# Goal integration acceptance on a clean local checkout

**Purpose.** Reproduce the real goal ORM/TaskRunner/Planner tests on a machine
with the full Sythion source and installed dependencies, without spending
GitHub Actions minutes. This is evidence gathering, not an authorization
to enable autonomous work or claim Windows installed-app acceptance.

From the repository root, on Windows PowerShell:

```powershell
python scripts/run_goal_acceptance.py --process-kill --full-backend --frontend --report "$env:TEMP\sythion-goal-acceptance.json"
```

The default invocation (`python scripts/run_goal_acceptance.py`) runs
the focused real-database and pure-policy goal test files. Optional switches
add **actual Windows hard process-kill recovery**, the full backend suite
and frontend build/Vitest, respectively. The process-kill command sets
`SYTHION_RUN_PROCESS_KILL=1` only for the child pytest process. On Linux
or macOS, explicitly requesting `--process-kill` returns non-success;
an OS-skipped suite is never counted as acceptance.

The runner refuses to report acceptance on a dirty/unavailable Git checkout
or absent integration files. Its JSON includes commit, platform, commands
represented by named suites, counts, return codes and SHA-256 hashes of
test output, but not task payloads, prompts, tool outputs, raw logs or secrets.
Raw test output is streamed only to the executing terminal. A missing pytest
summary, skipped-only suite, failed exit status, any focused skip or native
fault marker makes the suite **not verified**, not passed.

A green runner result means **only that the selected test matrix passed on
the recorded source commit**. It never means the installed Tauri Windows app
has undergone manual UI/OS recovery acceptance. Record the JSON and manually
verify installed-app UI recovery blockers, no duplicate external action,
permissions, durable three-step progress and safe owner-directed continuation
before closing S194–S205. Do not change autonomy defaults to run these tests.

## Windows read-only adapter newline regression (2026-11-10)

The first Windows-focused run reported **220 passed, 2 failed** in the focused
goal suite. Both failures came from test assertions treating FileReadTool's
universal-newline-normalized text as identical to raw file bytes. The runtime's
FileHashTool correctly hashes the raw bytes; this was a test-contract mismatch,
not evidence of a broken SHA-256 or goal runner.

Tests now compare FileReadTool content to independent text-mode reads while
continuing to check `bytes_read`, `file_size`, SHA-256 over raw bytes and
unchanged source files. Three explicit LF/CRLF/CR byte fixtures exercise that
contract. No production tool code, action gates or goal safety behavior was
changed. **These fixes still require a fresh Windows rerun**; the previous
220/2 result must remain recorded as failed, not retroactively passed.

After pulling the commit, first run:

```powershell
python -m pytest -q backend/tests/test_goal_readonly_tool_adapter.py backend/tests/test_goal_execution_vertical_slice.py --disable-warnings
```

Then run the acceptance matrix with a commit-bound JSON report:

```powershell
python scripts/run_goal_acceptance.py --process-kill --full-backend --frontend --report "$env:TEMP\sythion-goal-acceptance.json"
```

A green focused suite does not prove the process-kill/full-backend/frontend
suite or manually tested installed Windows/Tauri behavior.


## Windows full-run diagnosis and targeted regression fixes (2026-11-10)

On clean commit e590bf8 / Windows 11 / Python 3.14.7, user-provided test evidence reports:

- Focused goal ORM/policy **225 passed**; real Windows process-kill **3 passed**; frontend build passed.
- Full backend **NOT VERIFIED**: native access violation (exit 3221225477) while two concurrent Phase 9.5 test threads queried the same SQLite StaticPool connection from independent Sessions. There was also an earlier unreported test F before the crash; its cause is **unknown**.
- Frontend Vitest **255 passed, 2 failed**: Goals.test.tsx mocked useBackend without the now-required useGoalNextWork hook.
- Installed Windows/Tauri application acceptance has not been performed; overall matrix remains **not verified**.

The Phase 9.5 test now uses disposable file-backed SQLite, two independent DBAPI connections and per-thread sessions; normal SQLite writer admission remains under test. Production agent delegation code and autonomy permissions are unchanged. The frontend test now supplies an explicitly absent next-work preview, without changing the application component.

After updating the checkout, run these tests first:

```powershell
git pull --ff-only origin master
python -m pytest -q backend/tests/test_phase95_hardening.py::TestAdmissionAtomicity::test_two_callers_at_boundary_only_one_admits --disable-warnings
npm run test --prefix frontend -- src/screens/Goals.test.tsx
```

Next identify the **other earlier backend failure** without restarting the entire long suite:

```powershell
python -m pytest -q backend/tests --maxfail=1 --disable-warnings
```

Once resolved, run the full acceptance runner with report capture. These repairs are *not yet rerun on Windows*; do not mark the full backend or frontend suite green based solely on this commit.

## Windows second acceptance investigation (2026-11-10)

User-reported clean checkout `3e60555` on Windows 11, Python 3.14.7:

- The formerly crashing Phase 9.5 SQLite concurrency case **passed** (1 test).
- The frontend targeted Goal screen suite improved to **1 passed / 1 failed**:
  the remaining failure was a missing `useTaskResumePreflight` export in
  the mocked `useBackend` module, used unconditionally by TaskDetailDrawer.
- The full backend ran through **1,471 passed, 9 skipped, 4 xfailed** before
  stopping at `test_preview_refuses_pending_uncommitted_orm_mutations`
  because an expired SQLAlchemy `goal.id` argument access autoflushed the
  staged `goal.title` change before the preview function was called.

A standalone SQLAlchemy 2.0 reproduction confirmed that an expired entity's
primary-key property may load/flush before the callee starts. The preview's
production guard already rejects a dirty/new/deleted ORM session at entry and
queries under `no_autoflush`. The test now caches `goal_id` *before* staging
changes and covers dirty goal, dirty step, new task and deleted step; it asserts
no flush occurs and the pending changes stay in the calling Session. The
frontend fixture now supplies a non-authorizing task resume preflight mock.
Neither change modifies runtime code or grants permission to resume work.

**Do not mark full backend/frontend accepted until Windows reruns** on this new
revision. For fast feedback, run:

```powershell
git pull --ff-only origin master
python -m pytest -q backend/tests/test_goal_work_preview_integration.py --disable-warnings
npm run test --prefix frontend -- src/screens/Goals.test.tsx
python -m pytest -q backend/tests --maxfail=1 --disable-warnings
```

Once the complete backend and frontend suites pass, repeat the commit-bound
acceptance runner with process-kill and the installed-app checks separately.


## Refreshed backend mirror workflow (Windows release acceptance)

Owner-run at clean commit 6db6135: goal-preview ORM integration **14 passed**, goal-linked UI **2 passed**. Full backend reached **3,369 passed, 57 skipped, 4 xfailed** before failing `test_staged_backend_mirror_matches_source` with 27 stale/missing paths in `src-tauri/resources/backend`. Full release suite remains **not verified**.

The Tauri resources directory is Git-ignored generated installer output, so `git pull` does not update its backend mirror. The existing `stage_clean_backend.py` purposely never overwrites the live mirror. A separate explicit promotion command now exists.

Run these commands in Windows PowerShell after pulling, with no active Tauri packaging process:

```powershell
git pull --ff-only origin master
python scripts/refresh_staged_backend.py --check
python -m pytest -q scripts/tests/test_refresh_staged_backend.py --disable-warnings
python scripts/refresh_staged_backend.py --apply
python scripts/refresh_staged_backend.py --check
python scripts/verify_release_payload.py --check-backend-mirror
python -m pytest -q backend/tests/test_release_payload.py --disable-warnings
```

The default `--check` does not modify anything. `--apply` intentionally stages a new safe backend copy outside Tauri resources, verifies byte equality and release safety, moves the prior mirror into a unique retained `build/staging/clean-backend-backup-*` directory, promotes the verified copy and rechecks. A failed promotion attempts to roll back. Existing prohibited artifacts or reparse points stop the process for manual review. It does not touch the Python runtime, source code or credentials, and the backup is outside bundled resources.

The goal acceptance runner now performs **read-only mirror preflight** before invoking the expensive full backend suite. Stale/missing/unsafe mirror -> `not_verified`, an explicit refresh instruction and no 19-minute full-backend run. It NEVER silently restages or weakens `test_release_payload`.

Five isolated synthetic checks of promotion/rollback passed in the assistant's Linux environment using staging adapters; actual Windows promotion, full packaging verification and installed app acceptance are still pending. Once mirror and release tests pass, repeat the full goal acceptance matrix, and separately verify the installed Tauri app.


## Full automated Windows matrix passed — installed app still required

Owner-supplied results for clean commit `09cc363` confirm 225 focused goal tests, 3 real process-kill cases, 4,392 backend passes (60 skipped/10 xfailed), green frontend build and 257 Vitest passes. Refresh/packaging parity also passed. The runner explicitly records `installed_windows_acceptance_performed: false`; the next gate is packaged installed-app UI and runtime recovery. The exact hashes, provenance, pass/skip counts and safe manual checklist are in `docs/evidence/GOAL_WINDOWS_TEST_MATRIX_2026-11-10.md`. Earlier sections above are chronological diagnoses and not the current automated matrix verdict.
