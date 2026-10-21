VERDICT: ACCEPT

# 008 — DbEventLedger.append_event durability (report)

## Scope
Worktree `orchestra/008-dbeventledger-durability` (commit `cb6516fa78a79ab44a4c01deb24273cd6005b030`), branch `orchestra/008-dbeventledger-durability`. Base `885e425`.

## Changes
- `backend/models/database.py`: added `TaskEventSequence` model (table `task_event_sequences`).
- `backend/core/db_manager.py`: migration creates/backfills `task_event_sequences`, `PRAGMA busy_timeout=30000` on every connection, explicit connect-event listener.
- `backend/runtime/events.py`: `append_event` now allocates via locked, per-transaction counter (atomic write serialisation); no stale read-then-write; bounded retry on transient lock error; raises `EventSequenceAllocationError(RuntimeError)` after 8 failed attempts.
- Packaged copies in `src-tauri/resources/backend/...` kept byte-identical.
- New test `backend/tests/test_event_ledger_sequence_invariant.py`.

## Evidence
- classify.py: NORMAL (no remote/data deletion/public-API touch).
- Targeted subset: `python -m pytest backend/tests -q -k 'EventLedger or events'` -> `12 passed, 1603 deselected` (64s).
- Invariant stress (100 threads × 10 events, one task): PASS, 1000 unique gapless rows, set(range(1,1001)), no swallowed exceptions; completed in 6.07s (3 runs stable).
- Supporting: `test_kernel_concurrency.py`, `test_runtime_kernel.py` (14 passed), `test_migration_persistence.py` (8 passed), `test_phase105_concurrency.py` preserved (1 passed, now also gapless due to fix).

## Coverage / not checked
- Did not run full suite (known Windows sqlite native faults); isolated file passes present.
- HTTP paths not changed; no schema-version mechanism introduced (consistent with repo convention).
- Legacy DB backfill aligns counter to last allocated; archival behaviour of counter rows not exercised.

## Self-directed notes
- Second stress run without race regressions. Worker exceptions propagate via re-raise pattern. No in-process locks added. Counter semantics store last allocated sequence; backfill initially updated to `MAX(sequence_num)` (not +1) to avoid transactional seam gap.
- Git sha: `cb6516fa78a79ab44a4c01deb24273cd6005b030`.
