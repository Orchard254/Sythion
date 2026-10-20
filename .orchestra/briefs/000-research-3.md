# Brief: baseline - 000 - attempt 2

- Item: 000 Establish the verified test-suite baseline for the run
- Role: baseline capture (a research-support role: read-only, reports only)
- Goal (the slice that matters): Sythion's release-confidence signal is the
  backend suite. `docs/QUALITY_GATES.md:88-97` ("Gate 8 - Testing baseline
  discipline") and `docs/PROJECT_STATUS.md:37-45` ("Verified test baseline")
  require the suite's real state to be known and reported honestly. Every later
  stage - Implementer, Reviewer, Verifier, Committer - needs an accurate
  baseline so a pre-existing failure is never blamed on new work.

## Why this is attempt 2

Attempt 1 died with `getaddrinfo ENOTFOUND opencode.ai` - a transient network
fault, not a reasoning failure, and it produced no report file. The network is
fixed. **Do not fetch anything from the internet in this brief.** Every source
you need is on the local disk. If you find yourself wanting a URL, you do not
need one.

## Read

- `C:\Sythion\sythion\.orchestra\goal.md` - the goal, the commands, the
  off-limits list.
- `C:\Sythion\sythion\.orchestra\research.md` - the Researcher's candidate queue.
  Read **R-004** (documented baselines are wrong) and **R-007** (intermittent
  interpreter crash). Your job is to independently CONFIRM or REFUTE those two.
  Do not assume the Researcher is right.
- `C:\Sythion\sythion\Makefile` - the `test`, `test-acceptance`, `test-unit`
  targets and the marker-based selection they use.
- `C:\Sythion\sythion\backend/tests/conftest.py` - marker declarations and fixtures.
- `C:\Sythion\sythion\docs/TESTING.md`, `docs/PROJECT_STATUS.md`,
  `docs/acceptance-test-map.md` - the claims you are measuring against.
- `C:\Sythion\sythion\backend/tools/gui_tools.py` (the `sythion-uia-mta` worker:
  `_find_window` ~:426, `observe` ~:403, `_run` ~:981, dispatch ~:1749)
- `C:\Sythion\sythion\backend/tests/test_phase95_hardening.py` (the test named
  in the first crash trace)

## Write

- `C:\Sythion\sythion\.orchestra\reports\baseline-001.md` - the ONLY file you
  may write. Do not modify any other file, do not create branches, do not
  commit.

## Context

- Current status: the Researcher's report has been critiqued and ACCEPTED
  (see `.orchestra/critiques.md`). No backlog items exist yet.
- Branch: `master` in `C:\Sythion\sythion` (read-only stage; do not switch
  branches).
- Plan: n/a
- Previous findings: attempt 1 produced no report.

**What the Researcher claims - verify each independently:**

| Claim | How to check |
|---|---|
| R-004: full suite = `1564 passed, 45 skipped`, `1609 collected`, `533 s` | run it |
| R-004: every documented figure (692 / 683 / 659) is below even the collected count, so it cannot be right under any Python version | read the doc claims, compare |
| R-004: `docs/TESTING.md:39` claims the full suite takes "~55s" | read the line |
| R-007: the suite intermittently hard-crashes the interpreter in the `sythion-uia-mta` COM/UIA worker thread | see the hard evidence below |
| R-003: `-m gui`, `-m network`, `-m acceptance` each collect **0** of 1609 tests | `--collect-only` |
| R-003: `make test-unit` == full suite; `make test-fast` deselects exactly 1 test | `--collect-only` with those marker expressions |
| R-005: no `pytest.ini`/`pyproject.toml`/`setup.cfg` exists; `concurrency` and `gated_soak` are the two undeclared markers | `pytest` prints `inifile: None`; look for `PytestUnknownMarkWarning` |

**HARD EVIDENCE the Researcher did not have** (the orchestrator captured it from
the killed run; the fatal-error header was lost from the Researcher's copy):

```
Windows fatal exception: code 0x80040155
...
Current thread 0x0000a844 [sythion-uia-mta] (most recent call first):
  File "...\backend\tools\gui_tools.py", line 426 in _find_window
  File "...\backend\tools\gui_tools.py", line 403 in observe
  File "...\backend\tools\gui_tools.py", line 1749 in <lambda>
  File "...\backend\tools\gui_tools.py", line 981 in _run
```

`0x80040155` is `CLASS_E_CLASSNOTAVAILABLE` - a COM failure raised inside the
UI-Automation worker thread. The run did not stop there: progress output
continued past the fault to 58% before the orchestrator killed it.

A partial capture of that run is at
`C:\Sythion\sythion\.orchestra\baseline-pytest.txt` (3.9 MB, ends mid-run at
58%). Read its HEAD for the fault header. Do not overwrite it - write your own
runs to a NEW file.

## Constraints

- **Read-only.** The one exception is `reports/baseline-001.md`.
- **No network.** Do not fetch URLs, do not run `npm install`, do not reach for
  the internet for anything. Everything you need is on disk.
- Do not run destructive commands: `rm -rf`, `git reset --hard`, `git clean -fd`,
  `git checkout -- .`, `git stash drop`, force-push, or anything that deletes
  files git does not track.
- **Never reset, drop, migrate or seed a real database.** `backend/sythion.db`,
  `backend/test_voice.db` and `%LOCALAPPDATA%\Sythion (Synth)` may hold real
  user data. Do not run `make clean`, do not launch the sidecar against the
  real profile, do not run `npx tauri build`, do not run
  `scripts/stage_python_runtime.py` (it `rmtree`s the committed runtime).
- Safe and encouraged: `python -m pytest backend/tests -q`,
  `python -m pytest backend/tests -q --collect-only`,
  `python -m pytest backend/tests/acceptance -q`,
  `python -m pytest <file>::<test> -q`, `python -c "import backend.main"`,
  `git log/show/diff`, text searches, `npm run lint`.
- Do not push, deploy, publish, or touch real data.
- Do not ask the user anything. If something needs a decision you cannot make,
  say so in the report and stop.
- The suite is SLOW (~9 minutes for a full run). Budget accordingly:
  - Start the full run FIRST, in the background, redirected to your own file.
  - While it runs, do the cheap targeted work (marker collection counts,
    acceptance suite, doc-claim reading, import gate).
  - Do not poll the background run in a loop; do the other work and check once
    at the end.
  - Redirect to a file, never through a pipe that truncates the head - the
    fatal-error header is at the TOP of the output and a `Select-Object -Last N`
    pipe loses it. This is exactly how the first two captures were spoiled.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\baseline-001.md` with this shape:

```
# Report: baseline - 000 - attempt 2

## What I did
<commands run, with the exact command lines and where each output was captured>

## Evidence
<every claim with its evidence: command + output, file:line, test name>

## Baseline numbers
- Collected total (pytest --collect-only -q): <n>
- Full suite result: <n passed, n failed, n skipped, n errors, <time>>
- Acceptance suite result: <...>
- Frontend lint: <...>
- Frontend vitest: <...>   (npx vitest run, from frontend/)
- Backend import gate from repo root: <exit code>
- Backend import gate from backend/ : <exit code>

## Pre-existing failures
| # | Test | Symptom | Evidence | Reproduces alone? |
|---|---|---|---|---|

## Docs-vs-reality deltas
<every claim in README.md / docs/PROJECT_STATUS.md / docs/ROADMAP.md /
 docs/TESTING.md / C:\Sythion\SYTHION_TODOS.md that the real baseline
 contradicts, with both numbers and file:line>

## Research claims confirmed or refuted
| Claim | Verdict (confirmed / refuted / partly) | Your independent evidence |
|---|---|---|

## The intermittent crash
<your own attempt to characterise it: how many runs, what the fault was,
 whether it reproduces with the GUI test file alone, and the smallest
 command that triggers it - or an honest "could not reproduce">

## Environment variables of this baseline
<interpreter version, OS, working directory, pytest version, relevant env
 vars, whether any database was touched>

## Findings
<anything the next stage needs to know>

## Not checked
<REQUIRED. What you did not verify, and why. An empty section is a finding.>
```

- Every claim needs evidence. A claim without a command output, a `file:line`
  or a test name is not a claim.
- If a number came from a targeted run, say so. If it came from a full run,
  say so. Do not blend them.
- The "Research claims confirmed or refuted" table is the part the Scout and
  the Verifier will lean on hardest. Be rigorous: if you could not check a
  claim, write "not checked", never "confirmed".

## Previous attempt (if any)

- Attempt 1 died on `getaddrinfo ENOTFOUND opencode.ai` before producing any
  output. The network is fixed, and this brief forbids network use entirely.
  Everything that attempt would have done is available locally.
