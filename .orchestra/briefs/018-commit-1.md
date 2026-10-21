# Brief: commit - 018 - attempt 1

- Item: 018 The vault-write guard must not be swallowed by the FastAPI global exception handler
- Role: committer (VCS only - merges; no push)
- Reviewed commit: `3359610` (branch `orchestra/018-vault-guard-surface`)
- Verification: `VERDICT: FIXED` (verifications/018-v1.md)

## Read

- `C:\Sythion\sythion\.orchestra\plans\018.md`
- `C:\Sythion\sythion\.orchestra\verifications\018-v1.md`
- `C:\Sythion\sythion\.orchestra\reports\018-implement-1.md` and `018-implement-2.md`

## Task

1. Rebase `orchestra/018-vault-guard-surface` onto master @ current HEAD. Expect clean.
2. Merge into master with `--no-ff`. Record the merge commit SHA.
3. Re-run the full backend suite ON MASTER, with the vault sentinel armed:
   `$env:SYTHION_VAULT_SENTINEL='1'`
   `python -m pytest backend/tests -q -p no:warnings --deselect backend/tests/acceptance/test_secret_storage.py::test_credential_manager_round_trip`
   and THEN, separately and WITHOUT the sentinel:
   `python -m pytest backend/tests/acceptance/test_secret_storage.py -q`
   (that one test legitimately needs the real API; it writes and deletes the
   TEST-SCOPED target `Sythion (Synth) [TEST]/ollama_api_key`, never the product
   target). Expected: 1582 passed, 45 skipped, 1 deselected, then 10 passed.
   If the suite dies natively, re-run once; report either outcome.
4. Record `mergeSha` in `board.json` (item 018 -> status merged) and 018's status
   in `backlog.md`. Do NOT push. Append a committer entry to `.orchestra/log.md`
   and a committer critique entry to `.orchestra/critiques.md`.
5. Remove this item's worktrees (wt-018, wt-018-review, wt-018-review2,
   wt-018-verify, and any wt-018-* scratch trees). Keep the branch ref
   (second parent) for provability.
6. The two `(018)` follow-up entries are ALREADY recorded in `follow-ups.md` by
   the orchestrator - do not add them again.

## Constraints

- Never push. No deploy, publish, migration, dispatch or secret rotation.
- Only append to board.json / backlog.md / log.md / critiques.md; do not rewrite history.
- Arm the sentinel for the full-suite run. Never write to the real product
  credential target.

## Output contract

Report file `C:\Sythion\sythion\.orchestra\reports\018-commit-1.md` with: merge
sha, suite numbers (both runs), recovery (if any), worktrees removed, and the
sentinel-reached count. Reply with a short summary. The FILE is the deliverable.