# Brief: commit - 001 - attempt 1

- Item: 001 CI actually gates this repository
- Role: committer
- Goal (the slice that matters): Sythion's release-confidence signal is its CI
  pipeline. Item 001 repaired it and the Verifier proved the outcome. Your job
  is the version control: merge the branch into `master`, locally, and prove
  the base branch is still sound.

## Read

- `C:\Sythion\sythion\.orchestra\plans\001.md` - the plan.
- `C:\Sythion\sythion\.orchestra\verifications/001-v1.md` - the Verifier's
  judgement (`VERDICT: FIXED`) and its reproduction of every original problem.
- `C:\Sythion\sythion\.orchestra\reviews/001-r1.md` - the Reviewer's judgement.
- `C:\Sythion\sythion\.orchestra\reports/001-implement-1.md` - the
  implementer's evidence.
- `C:\Sythion\sythion\.orchestra\follow-ups.md` - the outside actions already
  queued. **You must not perform any of them.**
- `C:\Sythion\sythion\.orchestra/goal.md` - the goal and the commands.

## Write

- The merge commit on `master`.
- `C:\Sythion\sythion\.orchestra\reports/001-commit-1.md` - your report.
- `C:\Sythion\sythion\.orchestra\board.json` and
  `C:\Sythion\sythion\.orchestra\backlog.md` - item 001's status becomes
  `merged` in BOTH. (`backlog.md` item 001's `- Status:` line, and
  `board.json`'s `items."001".status`.) The orchestrator owns `board.json`,
  so **you change `backlog.md` and the orchestrator changes `board.json`** -
  but you must state the new status explicitly in your report so the
  orchestrator can mirror it. Do not leave the two files disagreeing.
- `C:\Sythion\sythion\.orchestra/log.md` - append one line for the merge.

## Context

- Current status: item 001 is `accepted` in the board; the plan, the
  implementation, the review (`VERDICT: ACCEPT`) and the verification
  (`VERDICT: FIXED`) are all done. You are the last stage before the merge.
- Branch: `orchestra/001-ci-gates-repo`, one commit `9ef7153`, based on
  `e224319`.
- Base branch: **`master`**.
- Plan: `plans/001.md`.
- Previous findings: the plan was redone once (its criterion 4 contradicted its
  own pin-guard step); the fix is Rule P, verified by the Reviewer and the
  Verifier independently.

### State you need

- `master` has moved on since the branch was cut (`e224319` -> `da6c5c6` ->
  `163adab` -> `eb787ee` -> `c407899` -> ...). **Every one of those commits is
  `.orchestra/` state only** - no runtime file, no workflow, no frontend file
  changed. So a rebase should be trivial, but you must still rebase (the
  skill requires the branch to sit on the current base) and you must still
  check for conflicts.
- The branch's diff is exactly two files: `.github/workflows/ci.yml` and
  `frontend/package.json`. Neither has been touched by any other item.
- Two other branches exist (`orchestra/002-test-selection-truth`,
  `orchestra/004-real-assertions`) and are **not** yours to merge. Leave them
  alone. Do not delete them.
- The branch is currently checked out in the worktree
  `C:\Users\orcha\AppData\Local\Temp\opencode\wt-001`. You may use that
  worktree, but do not create a second worktree for the same branch (git will
  refuse). Clean up the worktree when you are done - see the cleanup step
  below.

### Facts already established - do not re-derive them

- The import command is byte-identical to master's and exits 0 from the repo
  root, 1 from `backend/`.
- Rule P on the branch: 0 offenders; on master: 2.
- The `provider-e2e` job differs from master's ONLY by the deleted unpinned
  install line; its `if:` / `environment:` / `defaults` / `env:` block / pytest
  invocation are byte-identical.
- `frontend/package.json`: `"test": "vitest run"`, `"test:watch": "vitest"`.
  `package-lock.json` untouched.

### Pre-existing conditions - do NOT blame the merge for these

1. **A clean checkout has no `backend/.env`** (gitignored), so
   `test_setup_readiness.py::test_readiness_states` fails there. The
   orchestrator and the Verifier both proved it: no key -> `1 failed, 7
   passed`; a dummy key -> `8 passed`; the main tree -> `8 passed`.
   `master` already contains `backend/.env` (it is untracked and ignored), so
   a suite run **from the main tree** is green. A suite run from a fresh
   worktree or a CI runner is not - that is item 006's subject and it is
   PRE-EXISTING.
2. **The suite is not deterministic here** - 2 native crashes in 6 observed
   full-suite runs, of two different kinds; neither reproduces in isolation;
   the smallest reproducer is the full suite. If a suite run dies that way,
   re-run it, record BOTH attempts, and report it as a pre-existing condition.

## Constraints

- **Only the Committer merges, and only after `VERDICT: FIXED`.** That
  condition is met.
- Merge **locally** with `--no-ff`. **NEVER PUSH.** Never deploy, publish,
  migrate, or rotate anything.
- Do not perform any outside action. The GitHub observation of the workflow is
  already in `follow-ups.md`; it is the human's to do, not yours.
- Rebase the branch onto the current `master` FIRST, then merge.
- After the merge, **re-run the full suite on `master`** and report the
  result. Run it from `C:\Sythion\sythion` (the main tree), where
  `backend/.env` exists, so the result is comparable with the baseline
  (`1564 passed, 45 skipped, 1609 collected`). If the suite fails, **undo the
  merge** with `git revert -m 1 <merge-sha>` and report it - the item goes
  back to the Implementer and that counts as a verify round. Do not paper over
  a failure.
- If there is a merge conflict: resolve it, re-run the tests, and report the
  conflict. A conflict is not an improvement round; it is counted separately
  (max 2), then the item is blocked.
- Commit any work the Implementer left uncommitted on the branch, with a clear
  message. (The Implementer committed everything; check and say so.)
- After a successful merge and a green suite:
  - remove the worktree `C:\Users\orcha\AppData\Local\Temp\opencode\wt-001`
    with `git worktree remove <path>` (it must be clean first - if it is not,
    say so rather than forcing it);
  - delete the branch `orchestra/001-ci-gates-repo` with
    `git branch -d orchestra/001-ci-gates-repo` (it is merged, so `-d` will
    work). Leave the OTHER two orchestra branches alone.
- Do not touch `backend/.env`, `backend/sythion.db`, `backend/test_voice.db` or
  `%LOCALAPPDATA%\Sythion (Synth)`.
- Do not run `make clean`, `scripts/stage_python_runtime.py`, migrations, or
  `npx tauri build`. Do not run or dispatch the GitHub workflow.
- State what you did not check.
- Do not ask the user anything.

## Output contract

Write `C:\Sythion\sythion\.orchestra\reports\001-commit-1.md`:

```
# Report: commit - 001 - attempt 1

## What I did
<the exact commands: rebase, merge, suite run, worktree removal, branch deletion>

## Evidence
<every claim with its evidence: command + output>

## Merge
- Base branch:
- Branch merged:
- Rebase result / conflicts:
- Merge commit sha:
- Files changed by the merge (git diff --name-only before..after):

## Suite on master after the merge
- Command:
- Result:
- Comparison with the pre-merge baseline (1564 passed / 45 skipped / 1609 collected):

## Item status
- Item 001 status to record: merged
- (the orchestrator mirrors this into board.json)

## Cleanup
- Worktree removed:
- Branch deleted:

## Not checked
<REQUIRED>
```

- Reply with a short summary: the merge sha, the suite result, the status to
  record. The FILE is the deliverable.

## Previous attempt (if any)

- None. This is commit attempt 1.
