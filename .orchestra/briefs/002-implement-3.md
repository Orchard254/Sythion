# Brief: implement - 002 - attempt 3 (recovery of the redo round)

- Item: 002 Makefile test selections and pytest configuration stop claiming what they do not do
- Role: implementer (implementer-3, recovery dispatch)
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:45-59` Gate 4, `:88-97` Gate 8). The
  repository's test tooling must tell the truth.

## Why this dispatch exists

The redo round was interrupted by a **network fault** (`ECONNRESET`) after the
work was committed but **before the report was written**. Nothing was lost:
- The fix is committed as **`cc03a7b`** on `orchestra/002-test-selection-truth`,
  on top of the round-1 commit `0f27d93`.
- The worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-002` is
  **clean** (no uncommitted changes).

Your job is therefore **NOT to re-implement**. It is to **verify the committed
work against the three findings, gather the evidence, and write the report**.
If the verification shows a gap, fix it and commit on top. If it shows none,
commit nothing and write the report - that is a legitimate outcome.

## Read

- `C:\Sythion\sythion\.orchestra\reviews/002-r1.md` - **THE REVIEW.** The
  three findings your committed work must satisfy.
- `C:\Sythion\sythion\.orchestra\briefs/002-implement-2.md` - the brief the
  interrupted round was working from.
- `C:\Sythion\sythion\.orchestra\plans/002.md` - the plan.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 002's six criteria.
- `C:\Sythion\sythion\.orchestra\reports/002-implement-1.md` - round 1's
  report, for the shape and for the "before" numbers.
- The four files: `pytest.ini`, `Makefile`, `backend/tests/conftest.py`,
  `backend/tests/acceptance/test_gui_fixture.py`.

## Write

- `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md` - the evidence
  report for the redo round (shape below). **This is the deliverable.**
- Any further commits, only if the verification finds a real gap.

## Context

- Current status: item 002 is `needs-improvement`, review round 1 of a maximum
  2. The Reviewer returned `VERDICT: IMPROVE`.
- Branch: **`orchestra/002-test-selection-truth`** - worktree `wt-002` is
  already checked out at `cc03a7b`. Work there; do not create another
  worktree for the same branch (git will refuse).
- Plan: `plans/002.md`.

### What the orchestrator has already checked in commit cc03a7b

All three findings look addressed in the diff, and the fixes read as truthful
rather than as workarounds:
1. **`learning_soak`** now says "the synthetic learning-soak workload in
   `backend/tests/test_phase115_soak.py` (one test); despite the name it is
   NOT gated by an environment flag and runs in the default suite" - which
   matches the code.
2. **The fallback comment** now states the truth: the root `pytest.ini` is
   REQUIRED, a full-tree collection without it aborts with exit code 4, the
   fallback is declaration-only, and it explains why `pytest --markers` does
   not show the markers (`--markers` never loads the conftest). The
   `CANONICAL_MARKERS` set was reduced to marker NAMES, so the descriptions
   exist in exactly one place and cannot drift.
3. **The `gui` expectation** is now driven by a `LIVE_GUI_MODULES` registry
   rather than one hard-coded path, and `pytest.ini` says so.

The orchestrator also confirmed, by reproduction, the behaviour finding 2 is
about: with the root `pytest.ini` temporarily renamed away, a FULL-TREE
collection prints 1609 collected ids and then fails with **exit 4** and
`ERROR: selection-contract violation in the backend test tree: - no pytest
configuration file was used; expected pytest.ini at the repository root`.
(A PARTIAL run still exits 0 - the guard only fires on a full-tree collection,
which the new comment now also states.) The file was restored and the
worktree verified clean afterwards.

### What the Reviewer confirmed, and what must NOT regress

- Marker populations: `gui`=4, `network`=5 (identical to `provider_e2e`),
  `acceptance`=193, `slow`=1, `flaky`/`integration`=0, `process_kill`=8,
  `concurrency`=53, `learning_soak`=1.
- Makefile selections: `test-fast` -> 1599 selected / 10 deselected (1 slow +
  4 gui + 5 network), `test-unit` -> 1416/193, `test-gui` -> 4, `test-network`
  -> 5, `test-acceptance` -> 193. All exit 0.
- `1609` collected before and after; `asyncio: mode=Mode.STRICT`; **no
  `filterwarnings` and no `addopts` anywhere in the tree**; zero
  `PytestUnknownMarkWarning`.
- The `network` marker cannot cause a credentialed call - all 5 tests are
  gated by `SYTHION_RUN_PROVIDER_E2E=1`.
- The guard is silent on the clean tree, silent on partial runs, fires with
  exit 4 on drift, and tolerates a new plain acceptance module.
- The cross-item risk is cleared in the exact CI shape: `cd backend; pytest
  tests` -> 1609 collected, exit 0 under the PINNED toolchain (pytest 7.4.4 /
  pytest-asyncio 0.23.3), so the guard cannot redden the pipeline item 001
  repaired.
- `make` is genuinely unavailable on this machine (no binary, no WSL) - the
  Reviewer could not do better than running the recipe commands verbatim.

**Re-verify all of this after the fix and report the numbers.** Do not assume
the interrupted round ran the checks.

### Two nits the Reviewer raised - report your decision on each

- The `concurrency` marker is applied per test (53 times) while the marker
  itself is declared at module level. The new description says so.
- `check-test-selections` is not wired into CI. `ci.yml` is item 001's file
  and item 001 is MERGED - **do not edit it**. Say in the report what the
  integration should be and leave it to the orchestrator to queue.

## Constraints

- **Never push.** Never deploy, publish, or touch real data.
- **Never run destructive commands:** `rm -rf`, `git reset --hard`,
  `git clean -fd`, `git checkout -- .`, `git stash drop`, force-push.
- Do not edit `.github/workflows/ci.yml`, `backend/requirements*.txt`, any
  runtime module under `backend/` outside `backend/tests/`, or `docs/`.
- Do not rewrite history, do not rebase, do not amend `0f27d93` or `cc03a7b`.
  If you commit, commit on top of `cc03a7b`.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`.
- **Known hazard:** the suite is not deterministic on this machine (2 native
  crashes in 6 observed full-suite runs, of two different kinds; neither
  reproduces in isolation; the smallest reproducer is the FULL SUITE). If a
  run dies that way, re-run it and record BOTH attempts - it is a pre-existing
  condition, not your failure.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/002-implement-2.md`:

```
# Report: implement - 002 - attempt 2

## What I did
<the commands you ran; state plainly that the fix was already committed as
 cc03a7b by the interrupted round and that you verified it, or list what you
 changed>

## Evidence
<every claim with its evidence: command + output, file:line>

## The three findings
| # | Finding | Status | Evidence that it is now true |
|---|---|---|---|
| 1 | learning_soak description | fixed / not fixed | ... |
| 2 | fallback comment vs behaviour | ... | ... |
| 3 | gui expectation | ... | ... |

## Acceptance criteria
| # | Criterion | Result | Evidence |
|---|---|---|---|
| 1 | ... | ... | ... |
(all six, re-checked AFTER the fix)

## Marker populations and Makefile selections
<the measured numbers>

## The two nits
<what you decided about each>

## Findings
## Not checked
<REQUIRED>
```

- **Paste the ACTUAL command output.** A claim without output is not a claim.
- For finding 2, paste the full-tree collection WITHOUT the root `pytest.ini`
  (the `pytest.ini` temporarily renamed away, restored afterwards, `git status`
  verified clean) and show the exit code and the guard's message.
- For finding 3, show that a second live-GUI module no longer aborts the suite
  with a false message, and that the violation message is true in both
  directions.
- If a finding or a criterion is not satisfied, say so plainly. Do not restate
  it as satisfied.
- Reply with a short summary. The FILE is the deliverable.

## Previous attempt (if any)

- Round 1: `reports/002-implement-1.md` - ACCEPTED by the orchestrator, then
  the Reviewer returned `VERDICT: IMPROVE` with three findings.
- Round 2 (this one): the fix was committed as `cc03a7b` and the session died
  on `ECONNRESET` before the report was written. This dispatch recovers the
  evidence and the report.
