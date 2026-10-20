# Brief: baseline - 000 - attempt 1

- Item: 000 Establish the verified test-suite baseline for the run
- Role: baseline capture (a research-support role: read-only, reports only)
- Goal (the slice that matters): Sythion's release-confidence signal is the
  backend suite. `docs/QUALITY_GATES.md:88-97` ("Gate 8 - Testing baseline
  discipline") and `docs/PROJECT_STATUS.md:37-45` ("Verified test baseline")
  require the suite's real state to be known and reported honestly, and every
  later stage (Implementer, Reviewer, Verifier, Committer) needs an accurate
  baseline so that a pre-existing failure is never blamed on new work.

## Read

- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the commands, the
  off-limits list.
- `C:\Sythion\sythion\Makefile` - the `test`, `test-acceptance`, `test-unit`
  targets and the marker-based selection they use.
- `C:\Sythion\sythion\backend/tests/conftest.py`,
  `C:\Sythion\sythion\backend/tests/acceptance/conftest.py`
- `C:\Sythion\sythion\docs/TESTING.md`, `docs/PROJECT_STATUS.md`,
  `docs/acceptance-test-map.md`
- `C:\Sythion\sythion\backend/tests/test_phase95_hardening.py` (the file
  implicated in the crash)
- `C:\Sythion\sythion\backend/tools/gui_tools.py` (around line 973, the
  `sythion-uia-mta` worker) and `C:\Sythion\sythion\backend/agents/service.py`
  (around line 83, `get_agent`)
- CogniGraph memory is connected in this workspace: `memory_query`
  (args `text`, `limit`, `intent`, `agent_type: "opencode"`, `fields`).
  1134 curated facts exist about this codebase; check it before asserting that
  something is unknown.

## Write

- `C:\Sythion\sythion\.orchestra\reports\baseline-001.md` - the ONLY file you
  may write. Do not modify any other file, do not create branches, do not
  commit.

## Context

- Current status: run start. No items exist yet.
- Branch: `master` in `C:\Sythion\sythion` (read-only stage; do not switch
  branches).
- Plan: n/a
- Previous findings: none.

**Already observed by the orchestrator** (verify these, do not merely repeat
them):

1. The local interpreter is CPython **3.14.7** (`python --version`). CI pins
   Python **3.11** (`.github/workflows/ci.yml:21`). There is no other
   interpreter on PATH; `python3.exe` is only the Microsoft Store stub, and
   `C:\Sythion\sythion\src-tauri\resources\python-runtime` is a bundled
   CPython **3.13**.
2. `python -m pytest backend/tests -q` **exited 1** and the interpreter printed
   a faulthandler thread dump. The dump implicated
   `backend/tests/test_phase95_hardening.py:68` and `:82`
   (`test_two_callers_at_boundary_only_one_admits`), the `sythion-uia-mta`
   worker thread at `backend/tools/gui_tools.py:973`, and
   `backend/agents/service.py:83` (`get_agent`). A partial capture exists at
   `C:\Sythion\sythion\.orchestra\baseline-pytest.txt` - it was truncated, so
   the fatal-error HEADER was lost. A full re-run was started with output
   redirected to that same path; check whether it has finished and what it
   contains before starting your own run.
3. `python -m pytest backend/tests -q` reports `inifile: None`. There is no
   `pytest.ini`, `pyproject.toml` or `setup.cfg` anywhere in the repo, yet the
   Makefile and CI select by marker (`-m "not slow and not flaky ..."`), and
   `slow`, `flaky`, `integration`, `gui`, `network`, `acceptance`,
   `provider_e2e` appear to be undeclared markers.
4. `cd frontend && npm run lint` exits 0 (clean).
5. The README claims **692 passed, 32 skipped** and the acceptance suite
   **196 passed, 2 skipped**; `docs/ROADMAP.md:24` claims **659 passed, 32
   skipped** as of 2026-08-16.

## Constraints

- **Read-only.** The one exception is `reports/baseline-001.md`.
- Do not run destructive commands: `rm -rf`, `git reset --hard`, `git clean -fd`,
  `git checkout -- .`, `git stash drop`, force-push, or anything that deletes
  files git does not track.
- **Never reset, drop, migrate or seed a real database.** `backend/sythion.db`,
  `backend/test_voice.db` and `%LOCALAPPDATA%\Sythion (Synth)` may hold real
  user data. Do not run `make clean`, do not launch the sidecar against the
  real profile, do not run `npx tauri build`.
- Safe and encouraged: `python -m pytest backend/tests -q`,
  `python -m pytest <file> -q`, `python -m pytest backend/tests -q --collect-only`,
  `python -m pytest <file>::<test> -q`, `python -c "import backend.main"`,
  `git log/show/diff`, text searches, `npm run lint`.
- Do not push, deploy, publish, or touch real data.
- Do not ask the user anything. If something needs a decision you cannot make,
  say so in the report and stop.
- The suite is SLOW (the first run took many minutes and was still in the 8%
  range after ~10 minutes). Budget your time: prefer targeted runs plus a
  `--collect-only` count over repeated full runs. If a full run is needed, run
  it in the background and do not poll it.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\baseline-001.md` with this shape:

```
# Report: baseline - 000 - attempt 1

## What I did
<commands run, with the exact command lines>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Baseline numbers
- Collected total (pytest --collect-only -q): <n>
- Full suite result: <n passed, n failed, n skipped, n errors> (or: crashed - details)
- Acceptance suite result: <...>
- Frontend lint: <...>
- Backend import gate: <...>

## Pre-existing failures
| # | Test | Symptom | Evidence | Reproduces alone? |
|---|---|---|---|---|

## Docs-vs-reality deltas
<every claim in README.md / docs/PROJECT_STATUS.md / docs/ROADMAP.md that the
 real baseline contradicts, with both numbers and file:line>

## Environment variables of this baseline
<interpreter version, OS, relevant env vars, working directory, whether a
 database was touched>

## Findings
<anything the next stage needs to know>

## Not checked
<REQUIRED. What you did not verify, and why. An empty section is a finding.>
```

- Every claim needs evidence. A claim without a command output, a `file:line`
  or a test name is not a claim.
- If the suite crashes, capture the **full** output including the fatal-error
  header, and determine whether the crash reproduces when the implicated test
  file is run alone. That distinction is the single most valuable thing you can
  establish: a crash that reproduces in isolation is a candidate backlog item;
  a crash that only appears in a full run is an ordering/isolation defect.
- Be explicit about whether each number came from a full run or a targeted run.

## Previous attempt (if any)

- None. This is attempt 1.
