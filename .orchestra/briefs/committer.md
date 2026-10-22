# Brief: commit - <ITEM>

- Item: <ITEM TITLE>
- Role: committer (merge, re-verify, bookkeeping)
- Branch to merge: `<BRANCH>` (tip `<SHA>`)
- Goal: merge the reviewed and verified branch into `master` LOCALLY, re-run
  the suites on the merged result, and update the run's bookkeeping. Nothing
  is pushed.

## Preconditions - check these before merging

- The round-2 Reviewer returned `VERDICT: ACCEPT` (`.orchestra/reviews/<ITEM>-r2.md`).
- The Verifier returned `VERDICT: FIXED` (`.orchestra/verifications/<ITEM>-v2.md`).
- If EITHER is missing or is not the verdict above, **STOP and report that**;
  do not merge.

## Merge

1. `cd C:\Sythion\sythion`
2. Make sure `master` is at the current tip and the working tree is clean
   apart from `.orchestra/` bookkeeping. Do not discard anyone's work.
3. Merge the branch into `master`. A plain merge is preferred - the branches
   merge cleanly (`git merge-tree --write-tree master <BRANCH>` exits 0), so
   there should be NO conflict. If there IS a conflict, STOP and report it
   rather than resolving it by guessing.
4. **Do NOT push.** This repository's history is local only.

## Re-verify on the MERGED result

- `python -m pytest backend/tests -q` - run it in the BACKGROUND and poll to
  completion. It takes 4-9 minutes.
  - Known pre-existing problems on this machine, which are NOT this item's
    fault and must not be attributed to it: the
    `test_phase975_admission_multiproc.py` contention flakes (they pass in
    isolation) and an intermittent native crash `0xC0000005` that reproduces
    on the pre-change base. If a run dies natively, re-run once and report
    BOTH outcomes.
- `cd frontend && npm test` - the whole vitest suite, report the file and
  test counts.
- `cd frontend && npm run lint` - exit 0, no warnings.
- `cd frontend && npm run build` - exit 0.
- `cargo test` in `src-tauri` - exit 0 (24 tests). The user's own committed
  `sidecar.rs` change must still pass.
- Compare the counts against the branch's own reported numbers. The merged
  result must show the SAME collected/passed counts as the branch, plus
  whatever `master` contributed. Report any difference.

## Bookkeeping (all under `.orchestra/`)

- `board.json` - set this item's `status` to `"merged"`, `mergeSha` to the
  merge sha, `owner` to your owner id, and keep `round` / `verifyRounds` as
  they are. Re-parse the file afterwards and confirm it is valid JSON.
- `backlog.md` - mark the item's acceptance criteria as met, with the
  measured evidence (the counts you measured, not the numbers the
  implementer quoted).
- `critiques.md` - append a committer entry: the reviewed commit, the
  verification verdict, the merge sha, the counts you measured on the merged
  result, the scopes you checked, and anything you could not determine.
- `log.md` - append a line recording the merge: item, branch, merge sha, the
  measured counts, and the verification verdicts that gated it.
- `follow-ups.md` - append anything the merge surfaced (only if new).
- Commit the bookkeeping with a message that names the item and the merge sha.

## Worktrees

- The item's worktree under `C:\Users\orcha\AppData\Local\Temp\opencode\` may
  be removed **only after** the merge is done and the tree is clean. Inspect
  it first (`git status` in the worktree) and only remove a worktree that is
  clean and whose commits are all reachable from `master`. Do not remove any
  worktree another agent may still own.

## Constraints

- No push. No deploy. No destructive commands (`git reset --hard`,
  `git clean -fd`, branch deletion of unmerged work).
- Never read, copy or print the developer's real `backend/.env`, and never let
  a real `~/.sythion` be copied anywhere.
- Do not edit product source to make a suite pass. If something fails,
  report it.
- Do not use `git add -A`; stage deliberately.

## Output contract

- The merge sha.
- The measured counts on the merged result (backend collected/passed/skipped,
  vitest files/tests, lint, build, cargo).
- The verdicts that gated the merge.
- The bookkeeping you changed.
- Anything you could not determine.

Reply with a short summary. The FILE is the deliverable (the critique entry
in `.orchestra/critiques.md`).
