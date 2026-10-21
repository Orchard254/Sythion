# Report: implement - 017 - attempt 1

- Branch: `orchestra/017-counter-flag` (from master)
- Commit SHA: `9b5b4531508eb36d1902252a182e275b8f955705`
- Files changed:
  - `backend/runtime/events.py`
  - `backend/tests/test_event_ledger_counter_flag.py`
- No changes under `backend/models/`, `backend/migrations/`, or `backend/core/db_manager.py`.

## What changed

`DbEventLedger.append_event` no longer uses the sticky `_counter_table_checked`
boolean to skip counter-table creation. It now runs a transaction-resident
existence check (`sqlite_master`) in the same transaction as the allocation and
re-creates `task_event_sequences` with `CREATE TABLE IF NOT EXISTS` only when the
query shows it is absent. An explicit `BEGIN IMMEDIATE` is issued when the
session is not already in a transaction, so DDL/DML participate in the
caller's rollback instead of auto-committing under Python 3.11+ legacy
sqlite3 transaction mode. `_counter_table_checked` remains initialized to
`False` only for compatibility and is no longer the gating condition.

Two named regression tests were added covering caller-rollback (variant a) and
failed allocation (variant b), each verifying the persisted result through a
fresh `DbEventLedger` after commit.

## Test output

```
$ python -m pytest backend/tests/test_event_ledger_counter_flag.py backend/tests/test_event_ledger_sequence_invariant.py backend/tests/test_runtime/test_event_ledger_concurrency.py -q
8 passed, 2423 warnings in 20.73s
```

A 4-run repeat of the same three test files passed all four runs with no
failures. On the base tree (cached-flag gating restored) both new regression
tests fail (`2 failed`), confirming they fail before and pass after the change.