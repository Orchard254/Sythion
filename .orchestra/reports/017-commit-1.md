# Report: commit - 017 - attempt 1

- Item: 017 Event ledger counter-table flag must not stick after a rolled-back append
- Branch: `orchestra/017-counter-flag` (KEPT at the merged second parent)
- Implementation commit: `9b5b4531508eb36d1902252a182e275b8f955705`
- Merge commit: `29bb2433daf69fc0b2eddc2264366e92701140c0`
  (`Merge branch 'orchestra/017-counter-flag' (017 event ledger counter-table flag)`)
- Pre-merge master tip: `c856b6b`
- NOT PUSHED. No deploy, publish, migration, dispatch or secret rotation.

## What was merged

Exactly two files, as the plan required:

```
backend/runtime/events.py                       |  17 ++-
backend/tests/test_event_ledger_counter_flag.py | 145 ++++++++++++++++++++++++
2 files changed, 159 insertions(+), 3 deletions(-)
```

Nothing under `backend/models/`, `backend/migrations/` or `backend/core/db_manager.py`
was touched. `append_event` / `event_history` signatures and `_to_dict` keys are
unchanged (verified by the Verifier against the reviewed commit).

## The change

`DbEventLedger.append_event` no longer gates counter-table creation on the sticky
`_counter_table_checked` boolean. It now:

1. opens an outer transaction with `BEGIN IMMEDIATE` when the session is not already
   in one, so the DDL/DML participate in the caller's rollback instead of
   auto-committing under Python 3.11+ legacy sqlite3 transaction mode;
2. runs a transaction-resident existence check against `sqlite_master` inside the
   same transaction as the allocation;
3. issues `CREATE TABLE IF NOT EXISTS task_event_sequences` only when the table is
   absent.

`_counter_table_checked` is retained (initialised to `False`) for compatibility but
no longer gates correctness.

## Post-merge suite on master

Command: `python -m pytest backend/tests -q` from the repository root
(`backend/.env` present), run on the merged master tree:

```
1579 passed, 45 skipped, 425051 warnings in 341.68s (0:05:41)
PYTEST_EXIT:0
```

GREEN on the first attempt - no native crash (neither the COM burst nor the
sqlite3 access violation fired), so no re-run and no recovery were needed. The
count is identical to the Verifier's pre-merge full-suite measurement
(`1579 passed, 45 skipped`) taken on the reviewed commit, i.e. delta 0/0.

Raw output: `.orchestra/.tmp-suite-run1.txt` (scratch; not committed).

## Bookkeeping

- `board.json`: `items."017"` -> `status: merged`, `mergeSha: 29bb2433daf69fc0b2eddc2264366e92701140c0`,
  `branch: orchestra/017-counter-flag`, `owner: committer-1`, `round: 1`, `verifyRounds: 1`,
  `conflicts: 0`, `sensitivity: sensitive`, `domain: data`.
- `backlog.md`: item 017 `Status: merged (merge sha 29bb243, ...)`.
- `log.md`: committer entry appended.
- The branch `orchestra/017-counter-flag` is KEPT at `9b5b453` (the merged second
  parent) so the work is provably in master from the branch ref, matching the
  convention used for items 001 and 004.
- Worktrees for this item (wt-017, wt-017-review, wt-017-verify) removed after
  the merge; no stale worktree metadata left behind.
