# Commit brief 000-commit-3

You are the Committer for item 022.

## Inputs
- Item branch: `orchestra/022-concurrency-admission` at reviewed commit `50ec0ae18d55fe25905fe842c0f2bf1ada94a1bc`.
- Base branch: `master`.
- Plan/review/verification already accepted.

## Task
Merge `orchestra/022-concurrency-admission` into master using `--no-ff`, run the full backend suite on master after merge, and record mergeSha in board.json before any branch deletion. Do not push. If suite fails, reset/revert per skill recovery rules. Remove item worktree(s) if appropriate and safe. Do not alter documentation outside orchestration or source behaviour.

## Constraints
- The main worktree currently has small uncommitted documentation edits in README.md/docs (not part of this item). Preserve them, but merge should only bring item files.
- No destructive commands except sanctioned recovery.
- Do not ask user.

## Output
- `reports/000-commit-3.md`
- merged item status + mergeSha in board.json/backlog.
- Report full suite command and counts.