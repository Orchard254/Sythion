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
