# Brief: commit - 017 - attempt 1

- Item: 017 Event ledger counter-table flag must not stick after a rolled-back append
- Role: committer (VCS only - merges, no push)
- Reviewed commit: `9b5b4531508eb36d1902252a182e275b8f955705`
- Verification: `VERDICT: FIXED` (verifications/017-v1.md)

## Read

- `C:\Sythion\sythion\.orchestra\plans\017.md`
- `C:\Sythion\sythion\.orchestra\verifications\017-v1.md`
- `C:\Sythion\sythion\.orchestra\reports\017-implement-1.md`

## Task

1. Rebase `orchestra/017-counter-flag` onto master @ current HEAD. Expect clean.
2. Merge into master with `--no-ff`. Record the merge commit SHA.
3. Re-run the full backend suite on master. If the suite fails (native crash counts
   as a normal stochastic failure; re-run once more if it does), recover per
   protocol (reset to pre-merge tip, revert) and report.
4. Record `mergeSha` in `board.json` (item 017 -> status merged) and 017 status in
   `backlog.md`. Do NOT push. Update `.orchestra/log.md` with a committer entry.
5. Remove the finished worktrees for 017 (wt-017, wt-017-review, wt-017-verify, and wt-017-v* if any). Keep the branch ref (second parent) for provability, per prior items' convention.
6. Record the committer entry in `.orchestra/critiques.md`? No - the committer writes a
   critique entry per protocol (append to critiques.md).

## Constraints

- Never push.
- Only append to board.json/backlog.md/log.md/critiques.md; do not rewrite history.
- If the full suite crashes natively, re-run once. Report either outcome.

## Output contract

Report file `C:\Sythion\sythion\.orchestra\reports\017-commit-1.md` with: merge sha, pre/post suite counts, recovery (if any), worktrees removed. Reply with a short summary. The FILE is the deliverable.