# Brief: commit - 004 - attempt 1

- Item: 004 Four tautological assertions replaced by the assertions they stood in for
- Role: committer
- Goal (the slice that matters): Sythion's "better" includes **verification
  before claims** and **honest debt reporting** (`goal.md`,
  `docs/QUALITY_GATES.md:34-44` Gate 3, `:88-97` Gate 8). Four assertions in the
  concurrency and hardening suites ended in `or True` and could never fail. The
  Verifier proved by mutation testing that the replacements are real
  properties. Your job is the version control: merge the branch into `master`,
  locally, and prove the base branch is still sound.

## Read

- `C:\Sythion\sythion\.orchestra\plans\004.md` - the plan.
- `C:\Sythion\sythion\.orchestra\verifications/004-v1.md` - the Verifier's
  judgement (`VERDICT: FIXED`), its mutation-testing table, and its per-test
  JUnit-XML regression comparison.
- `C:\Sythion\sythion\.orchestra\reviews/004-r1.md` - the Reviewer's judgement.
- `C:\Sythion\sythion\.orchestra\reports/004-implement-1.md` - the implementer's
  evidence, including the reported defect **D-1**.
- `C:\Sythion\sythion\.orchestra\follow-ups.md` - the outside actions already
  queued. **You must not perform any of them.**

## Write

- The merge commit on `master`.
- `C:\Sythion\sythion\.orchestra\reports\000-commit-2.md` - your report.
- `C:\Sythion\sythion\.orchestra\backlog.md` - item 004's `- Status:` line
  becomes `merged`. The orchestrator owns `board.json` and will mirror it -
  state the new status explicitly in your report.

## Context

- Current status: item 004 is `accepted` in the board (the Verifier returned
  FIXED, the Reviewer returned ACCEPT). You are the last stage.
- Branch: `orchestra/004-real-assertions`, commit `05589b2`, based on `da6c5c6`.
- Base branch: **`master`**.
- Plan: `plans/004.md`.
- Previous findings: the plan deliberately scoped the item to tests only; the
  implementer reported the production defect D-1 (event loss in
  `DbEventLedger.append_event`) rather than fixing it, and the Verifier
  confirmed `backend/runtime/events.py` is byte-identical to master.

### Facts already established - do not re-derive them

- The branch's diff is exactly four files, all test files:
  `backend/tests/test_no_tautological_asserts.py` (new),
  `backend/tests/test_phase105_concurrency.py`,
  `backend/tests/test_phase1075_hardening.py`,
  `backend/tests/test_phase125_planning_validation.py`.
  **No production module is touched.**
- The Verifier proved by mutation testing that each new assertion FAILS when
  the production behaviour it guards is broken, while master PASSES.
- The Verifier's regression check: collected ids 1609 -> 1611, difference
  exactly the two guard tests, and a **per-test JUnit-XML comparison of two
  complete full-suite runs gives 0 pre-existing tests changed outcome**.
- The suite with the orchestrator's environment: `1566 passed, 45 skipped`,
  exit 0. The branch worktree has no `backend/.env`, so a run there shows
  `1 failed` - the PRE-EXISTING `test_setup_readiness.py::test_readiness_states`
  failure (item 006's subject, a gitignored `.env`).
- The classifier computes tier **SENSITIVE** for this diff, but it is a keyword
  screen false positive: the matched lines are `owner_rows` / `owner_task_ids`,
  local variable names in a test, and the `access` pattern matches the word
  "owner". The substantive sensitive-change gate is met: the change is local
  and reversible, the invariant is pinned by the four real assertions and the
  guard, the Reviewer verified that invariant by name (it read the production
  code for each site and judged whether the assertion can fail), and no
  production data, credentials or live config are touched. `board.json`
  records `sensitivity: sensitive` accordingly.

### Two pre-existing conditions - do NOT blame the merge for these

1. **The suite is not deterministic on this machine.** Across six observed
   full-suite runs there were **2 native crashes of two different kinds**: a
   BURST of COM faults (`0x80040155`) on the `sythion-uia-mta` thread that does
   NOT kill the process, and a native sqlite3 access violation (`0xC0000005`)
   that DOES kill the interpreter - seen in `test_phase95_hardening.py`,
   `test_phase105_concurrency.py` and, in the Committer's post-merge run for
   item 001, `test_phase95_hardening.py::validate_in_thread` ->
   `backend/agents/delegation.py:394/406`. Neither reproduces in isolation;
   the smallest reproducer is the FULL SUITE. If your suite run dies that
   way: **re-run it**, record BOTH attempts verbatim, and report it as a
   pre-existing condition - do not revert a good merge over it, and do not
   report it as this item's failure.
2. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails there. The MAIN TREE
   `C:\Sythion\sythion` HAS the `.env`, so run the suite from the main tree and
   the count is comparable with the baseline.

## Constraints

- **Only the Committer merges, and only after `VERDICT: FIXED`.** Met.
- Merge **locally** with `--no-ff`. **NEVER PUSH.** Never deploy, publish,
  migrate, or rotate anything. Do not perform any outside action - the
  GitHub observation belongs to the human.
- Rebase the branch onto the current `master` FIRST, then merge. `master` has
  moved on since the branch was cut (item 001 was merged); every commit since
  is `.orchestra/` state plus item 001's two files, none of which this item
  touches. Still check for conflicts and report them.
- After the merge, **re-run the full suite on `master`** from
  `C:\Sythion\sythion` and report the result, compared against
  `1566 passed / 45 skipped / 1611 collected`. If the suite fails, **undo the
  merge** with `git revert -m 1 <merge-sha>` and report it. Do not paper over a
  failure.
- **Record `items."004".mergeSha` in `board.json` is the ORCHESTRATOR's job** -
  you must state the merge sha in your report so it can be recorded. You may
  edit `backlog.md`'s status line; do not edit `board.json`.
- **DO NOT DELETE THE BRANCH.** The orchestrator's audit requires the branch ref
  to exist for a merged item so that the work can be shown to be in the base
  branch. Leave `orchestra/004-real-assertions` in place, pointing at the
  commit that was merged. (Item 001's branch was deleted and the orchestrator
  had to recreate it - do not repeat that.)
- You may remove the worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-004`
  with `git worktree remove <path>` if it is clean; if it is not clean, SAY SO
  rather than forcing it. Leave `orchestra/002-test-selection-truth` and its
  worktree alone - that is another item's branch.
- Do not touch `backend/.env`, `backend/sythion.db`, `backend/test_voice.db` or
  `%LOCALAPPDATA%\Sythion (Synth)`.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`. Do not run or dispatch the GitHub workflow.
- State what you did not check.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports/000-commit-2.md`:

```
# Report: commit - 004 - attempt 1

## What I did
<the exact commands: rebase, merge, suite run, worktree removal>

## Evidence
<every claim with its evidence: command + output>

## Merge
- Base branch:
- Branch merged:
- Rebase result / conflicts:
- Merge commit sha:
- Files changed by the merge:

## Suite on master after the merge
- Command:
- Result:
- Comparison with the pre-merge baseline (1566 passed / 45 skipped / 1611 collected):

## Item status
- Item 004 status to record: merged
- Merge sha for the orchestrator to record as items."004".mergeSha: <sha>

## Not checked
<REQUIRED>
```

- Reply with a short summary: the merge sha, the suite result, the status to
  record. The FILE is the deliverable.

## Previous attempt (if any)

- None for this item. (Item 001's commit was `000-commit-1.md`.)
