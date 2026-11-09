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
