# Windows goal and release matrix — 2026-11-10

## Provenance and result

This is an **owner-supplied Windows PowerShell test transcript**, not a test
execution performed by the assistant. Run timestamp in the JSON report:
**2026-11-09 22:52:41 UTC** (2026-11-10 01:52:41 EAT).
Platform: **Windows 11**, Python **3.14.7**. Source commit:
`09cc363a3e009017ee31f943a70abd8a6df34f26`, reported
`clean: true`. All recorded exit codes are 0 and the runner reports
`checks_passed_windows_acceptance_still_required`.

| Stage | Owner-reported observation | Result |
| --- | --- | --- |
| Refresh script and runner regression tests | 18 passed | PASS |
| Mirror before refresh | 27 changed/missing paths | Stale, corrected |
| Explicit `refresh_staged_backend.py --apply` | Verified refreshed mirror; previous mirror retained under `build/staging` | PASS |
| Mirror after refresh | Staged backend matches source | PASS |
| Release payload tests | 60 passed | PASS |
| Focused goal ORM and policy | 225 passed | PASS |
| Real Windows subprocess process-kill recovery | 3 passed | PASS |
| Staged backend preflight | Passed | PASS |
| Full backend suite | **4,392 passed, 60 skipped, 10 xfailed** | PASS |
| Frontend production build | Exit 0 | PASS |
| Frontend Vitest | **257 passed / 29 files** | PASS |
| Installed Windows/Tauri application manual acceptance | `installed_windows_acceptance_performed: false` | **NOT RUN** |

The 60 skips and 10 expected failures remain visible, not reclassified as
passing tests. No GitHub Actions were invoked. The total warning count is high
(particularly Python 3.14 and pytest-asyncio deprecations); no warning cleanup
or compatibility audit is claimed by this result.

## Commit-bound test-output digests

These SHA-256 values are copied from the owner's runner report. They identify
captured test streams but do not replace the original logs or prove installed
application behavior.

- Focused goal ORM/policy: `02dc06c159c2091c2dc3f364fca867bcfdfaa6b96cde9cec7a45ae7af2cf93e7`
- Process-kill recovery: `1ed3ba70d570c92ec8a3d4cc7cb6d945bb8718ce7535052ac34f88a7522fb43e`
- Full backend: `7474f0f156cae5f6fb1d53b8747f13d47dd3192130982c104a7970ca8a605e07`

The report was generated with:

```powershell
python scripts/run_goal_acceptance.py --process-kill --full-backend --frontend --report "$env:TEMP\sythion-goal-acceptance.json"
```

## Remaining installed-app gate — not certified

Do not close S194–S206 from this transcript alone. The next work is manual
acceptance **in the installed packaged Windows application**, separate from
Pytest, isolated subprocesses, a dev sidecar, or a Tauri --no-bundle build.
Record actual observations (screenshots where safe, event/step counts, build
revision and sanitized failure details), not assumptions.

1. **Installer integrity and startup.** From an installer built from the
   tested source and verified release payload, confirm the installed app can
   start the sidecar, reach the normal health state and open Goals/Tasks without
   a Python-from-repository dependency. Check that protected app state and
   credentials remain outside installed resources.
2. **Goal and plan visibility.** In a disposable test goal, verify the displayed
   active plan, blocked dependencies, `Next work` readiness, linked tasks
   and persisted progress. A read-only preview must never create tasks, unlock
   steps, approve actions or mark the goal completed.
3. **Safe real-tool work.** Use an explicitly approved temporary workspace and
   read-only FileRead/FileHash task. Independently inspect the file bytes and
   hash, event history, step status and UI counts. The action must remain
   subject to normal runtime permissions and leases.
4. **Actual app restart with unresolved work.** On a separate disposable test
   goal **with no consequential external action in flight**, close/end the
   installed app mid-step, reopen it and verify the old step is not requeued
   or duplicated automatically. Confirm the UI displays review/blocked state
   and an explicit resume is not offered without valid fresh preflight.
5. **Verified terminal recovery and changed plans.** Verify a task previously
   completed with independent evidence is reconciled exactly once and that
   superseded-plan tasks, foreign goal references and duplicated evidence
   cannot advance the active goal. Confirm repeated startup doesn't append
   duplicate completion events.
6. **Failure visibility and consent.** A simulated worker failure or uncertain
   effect must remain unverified/review-required rather than reported as
   completed; recovery must not mint a replacement task without normal checks.
   Test permission denial and failed approvals without relaxing guardrails.
7. **Installed release evidence.** Record version/commit, OS, installer path,
   UI checks, app-process restart observations and results per case. Keep the
   owner-specific environment data and any secrets out of committed artifacts.

Do not intentionally interrupt a real mutating action or operational workload
to perform restart testing. Use only disposable, read-only test work.
