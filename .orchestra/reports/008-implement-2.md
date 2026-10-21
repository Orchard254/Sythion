# Implementation report 008 (redo)

VERDICT: ACCEPT

- New commit SHA: 11af412790f1b926ab883e507969f7a0d8259947
- Branch: orchestra/008-redo (based on dceacf0)
- Worktree: C:/Users/orcha/AppData/Local/Temp/opencode/wt-008-redo

## Changes
- `backend/runtime/events.py`: replaced read-modify-write allocation with a single SQL
  UPSERT (`task_event_sequences ... ON CONFLICT(task_id) DO UPDATE ... RETURNING
  next_seq`); wrapped each attempt in a SAVEPOINT so a retry cannot discard
  earlier pending appends in the same transaction; accounts for pending unflushed
  events via `pending_max`; `latest_sequence()` now combines persisted rows,
  counter, and pending rows; lazy, idempotent counter-table creation for
  non-migrated (in-memory) databases; 3 bounded retries; raises
  `EventSequenceAllocationError(RuntimeError)` on exhaustion.
- `backend/core/db_manager.py`: busy-timeout raised to 30s and applied to every
  connection through a connect-event listener; additive migration creates and
  backfills `task_event_sequences` idempotently; `uq_task_events_task_sequence`
  retained.
- `backend/tests/test_runtime/test_event_ledger_concurrency.py`: new temp-DB
  invariant test (100 threads x 10 events = 1000 gapless sequences, propagated
  worker exceptions) plus pending-row and migration-idempotency coverage.

## Risks addressed
1. Multi-append rollback destructiveness: per-attempt SAVEPOINT replaces the
   previous blanket `session.rollback()`.
2. `latest_sequence`: now offsets pending (pre-flush) rows in the same transaction.
3. Allocation no longer relies on multi-statement Python reads; a single
   UPSERT serialises via the SQLite writer lock with 3 retries under busy_timeout.

## Targeted tests
- `backend/tests/test_runtime/test_event_ledger_concurrency.py`: 5 passed
- `backend/tests/test_kernel_concurrency.py` + `backend/tests/test_phase105_concurrency.py`
  + `backend/tests/test_runtime_kernel.py`: 48 passed, 2 skipped
- `backend/tests/test_migration_persistence.py`: 8 passed

Baseline preserved: no full-suite run attempted (180s budget respected).