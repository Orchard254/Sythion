# Report 010-research-1 — DbEventLedger.append_event concurrent event loss

Research pass over `sythion/` (primary tree) as of commit `3ba0799` plus untracked `sythion/` subtree. No fix attempted. Facts below cite `file:line`; inferences are labelled.

## 1. Implementation location

- `sythion/backend/runtime/events.py:35-73` (`DbEventLedger.append_event`).
- Class wraps a SQLAlchemy `Session`; model is `backend/models/database.py:79-93` (`TaskEvent`).
- Schema-level unique index created at `sythion/backend/core/db_manager.py:128-131`.
- Packaged duplicate at `sythion/src-tauri/resources/backend/runtime/events.py` is **byte-identical** to the primary copy (verified by direct comparison).

Relevant excerpt (`events.py:41-73`):

```python
def append_event(self, task_id, event_type, event_data):
    last = (
        self._session.query(TaskEvent)
        .filter_by(task_id=task_id)
        .order_by(TaskEvent.sequence_num.desc())
        .first()
    )
    # Account for events appended earlier in THIS uncommitted transaction
    # (sessions may run with autoflush=False): ...
    pending_max = max(
        (
            e.sequence_num
            for e in self._session.new
            if isinstance(e, TaskEvent) and e.task_id == task_id
        ),
        default=0,
    )
    sequence = max(last.sequence_num if last else 0, pending_max) + 1
    event = TaskEvent(
        task_id=task_id, event_type=event_type,
        event_data=json.dumps(event_data),
        sequence_num=sequence, created_at=datetime_utcnow(),
    )
    self._session.add(event)
    return sequence
```

`events.py:100-107` `latest_sequence` is a read-only variant of the read path. `_to_dict` (`events.py:82-88`) deliberately omits `task_id`, which is why the concurrency test re-reads owning rows by `TaskEvent.id`.

## 2. TaskEvent table shape

`backend/models/database.py:79-93`:

```python
class TaskEvent(Base):
    __tablename__ = "task_events"
    id = Column(Integer, primary_key=True, autoincrement=True)
    task_id = Column(String, ForeignKey("tasks.id"), nullable=False)
    event_type = Column(String, nullable=False)
    event_data = Column(Text, nullable=False)
    sequence_num = Column(Integer, nullable=False)
    created_at = Column(DateTime, default=datetime.utcnow)
```

Note: **no `__table_args__` unique constraint on `(task_id, sequence_num)` in the ORM model**. The guard is added outside the model at DB bootstrap time:

`backend/core/db_manager.py:124-131`:

```python
conn.execute(text(
    "CREATE UNIQUE INDEX IF NOT EXISTS uq_task_events_task_sequence "
    "ON task_events (task_id, sequence_num)"
))
```

- PK: `id` (`INTEGER … AUTOINCREMENT`) — surrogate, never collides.
- Guard: unique index `uq_task_events_task_sequence` on `(task_id, sequence_num)`.
- Indexes on `task_events`: `uq_task_events_task_sequence` (composite, left-anchored on `task_id`) plus `ix_task_events_created_at` (`db_manager.py:140-143`).
- Foreign key `task_id -> tasks.id` is declared but SQLite FK enforcement is **not** switched on in `db_manager.py` (`PRAGMA foreign_keys` never issued). Inference: FKs are declarative only.
- `sessions` use `autocommit=False, autoflush=False` (`db_manager.py:76-80`), so a SELECT does **not** see rows pending in the same session; that is exactly why the `session.new` compensation exists.

There is no schema-level sequence generator (no `SEQUENCE`, no `AUTOINCREMENT` on `sequence_num`). Sequence allocation is application-level.

## 3. Read-modify-write sequence and where interleaving is possible

Sequence within a single `append_event` call:
1. `events.py:47-52` — `SELECT … ORDER BY sequence_num DESC LIMIT 1` (committed state for this session’s view).
2. `events.py:56-64` — scan `self._session.new` (only **this** session’s uncommitted additions) to compensate for autoflush-less pending rows, then `sequence = max(committed_max, pending_max) + 1`.
3. `events.py:65-72` — construct `TaskEvent(sequence_num=sequence)` and `session.add`.
4. (Implicit) application later commits, which executes the INSERT and enforces the unique index.

Interleaving: two sessions (different threads/processes, or even different `DatabaseManager` instances over the same file) each execute steps 1–3 before either reaches commit. Each SELECT sees the same committed maximum; `self._session.new` is per-session, so it cannot see the other session’s pending rows. Both sessions compute the **same** `sequence`, `add` their row, and return the same int. At commit, the second INSERT hits `uq_task_events_task_sequence` and raises `IntegrityError`. If the failing transaction has multiple appends batched (as the test does, and as callers do), the entire transaction rolls back, losing **those** appends.

This is the defect recorded as candidate R-021 / deferred item 008 (`memory` fact id 1270, backend is `test` evidence). A scratch repro variant reported survival counts `[10, 5]` etc. — up to 50% loss per task — while the suite printed `1 passed`.

## 4. Mechanism-level classification

Primary classification: **(b) stale read of offset/sequence**, compounded by **(a) lost transaction due to stale read**, **(d) SQLite deferred isolation permitting non-serialised readers**, and manifesting as **(c) PK/unique-index collision on `(task_id, sequence_num)`**.

- It is **not** (e) “missing unique constraint” — the constraint exists and is what surfaces the collision.
- It is **not** an autoflush-`session.new` bug: that compensation was the Phase 8.75 fix (memory ids 198/206) and only covers rows pending in the *same* uncommitted transaction.
- Root read happens before write (`events.py:47-52`) with no reservation; two sessions each allocate from the same visible maximum. Isolation level is whatever SQLAlchemy/Python sqlite3 default gives (` isolation_level` not set in `create_engine`, `db_manager.py:59-63`), i.e. deferred read → write transactions. No retry, no backoff, no “next free sequence” re-check at commit.
- The 50% loss is transaction-scoped: a session that appended 5 events in one transaction and then loses on the 3rd sequence has all 5 rolled back by `get_session`/`new_session` callers’ exception handling (or caught and ignored by worker code — the Phase 105 test’s `write_events` has no error propagation).

The concrete product invariants affected:
- `tasks` side is unaffected: state transitions that appended an event which then rolled back may have the task state persisted by a separate commit or not at all, depending on caller ordering; most callers commit once after append, so a failed append aborts the state change too.
- Event history becomes non-monotonic in count but remains internally consistent for surviving committed rows.

## 5. Call sites

`DbEventLedger` is constructed in 28 non-test locations and `append_event` is called from 88 non-test locations. Grouped by module (construction / append counts):

| Module | `DbEventLedger(...)` | `.append_event(...)` |
|---|---:|---:|
| agents/coordination.py | 2 | 14 |
| agents/delegation.py | 1 | 22 |
| agents/service.py | 1 | 3 |
| agents/specialist.py | 2 | 3 |
| api/routes.py | 0 (3 reads/constructions inline) | 2 appends + events_after read |
| cognitive/pipeline.py | 1 | 1 |
| learning/planning_store.py | 2 | 3 |
| learning/store.py | 1 | 1 |
| runtime/events.py | 1 | 0 |
| runtime/leases.py | 1 | 2 |
| runtime/interfaces.py | 0 | 0 (protocol only) |
| services/cognitive_runtime.py | 1 | 2 |
| services/goal_manager.py | 1 | 7 |
| services/main_agent.py | 1 | 9 |
| services/memory_service.py | 1 | 6 |
| services/model_selection.py | 1 | 3 |
| services/planner.py | 1 | 5 |
| services/task_orchestrator.py | 1 | 7 |
| services/task_runner.py | 1 | 5 |
| tools/objective_planning_tool.py | 1 | 0 (event_history passed as callable) |

Cross-process exposure: calls happen from multiple `DatabaseManager` instances over the same file (e.g. `test_phase105_concurrency.py:505-513` creates one manager per thread), and routes create a fresh `DbEventLedger(db)` per request. Real runner code normally holds a single runner lease per profile, but specialist/agent paths and background orchestration create separate sessions, so same-`task_id` contention is reachable.

HTTP exposure: `GET /api/v1/tasks/{task_id}/events` (`api/routes.py:662-684`) reads via `events_after`; the append paths in routes (`api/routes.py:1180-1184`, `:1431-1433`) append-and-commit inside request handlers. `append_event`’s `int` return is **not** part of any HTTP response body — event reads are the public contract. Inference: fixing allocation does not need to change the HTTP schema; it only changes how many rows survive under contention.

## 6. Tests

Direct `DbEventLedger`/`runtime.events` users (17 files):
`test_cognitive_live.py`, `test_goal_planning.py`, `test_kernel_concurrency.py`, `test_legacy_sunset.py`, `test_phase105_concurrency.py`, `test_phase1075_hardening.py`, `test_phase10_provider_e2e.py`, `test_phase125_capture_observability.py`, `test_phase125_real_execution_capture.py`, `test_phase6_model_selection.py`, `test_phase875_live_reasoning.py`, `test_phase8_cognitive.py`, `test_phase975_admission_recovery.py`, `test_phase975_provider_e2e.py`, `test_phase9_agents.py`, `test_runtime_kernel.py`, `test_skill_engine.py`.

Broader `TaskEvent` touchers (33 files, listed in the research scratch; includes acceptance tests).

Two concurrency-relevant tests:

- `backend/tests/test_phase105_concurrency.py:495-554` (`TestEventLedgerConcurrency::test_no_duplicate_sequence_and_no_cross_contamination`) deliberately uses 2 tasks × 2 threads × 5 appends = 10 attempted per task, and asserts duplicate-sequence absence and ownership only. It explicitly does **not** assert count, with a comment at `:526-534` calling the count “racy” and the defect “reported, not asserted”. The worker closure `write_events` (`:505-513`) swallows exceptions (no `try/except`, but exceptions in a `threading.Thread` target are not propagated to the joiner), which is why a failing commit leaves the suite green.
- `backend/tests/test_kernel_concurrency.py:224-249` (`test_event_ordering_remains_correct_under_interleaving`) drives two ledgers in one session across two different `task_id`s with `await asyncio.sleep(0)` interleaving. It passes because the sequence space is per-task and no two sessions share a task; it does **not** exercise cross-session same-task allocation, so it never sees the collision.

## 7. Public API / contract impact

- Interface `EventLedger.append_event` (`backend/runtime/interfaces.py:27-35`) returns `int`. Any fix should preserve that signature and semantics.
- DB schema unchanged in shape (`task_events`, `uq_task_events_task_sequence`) for consumers; only row counts/volume behaviour under contention changes.
- HTTP: no request/response schema change; `/tasks/{task_id}/events` already exposes `sequence_num` arrays that may simply be longer/contiguous after a correct fix.

## 8. Suggested invariant for a regression test

The count-level invariant the current tests avoid pinning:

> For a pre-created task, launch `T=100` threads where each thread creates its **own** `DatabaseManager`/session over the same DB file, appends `N=10` events (`for i in range(N): ledger.append_event(tid, f"e{i}", {"t": t, "i": i})`), then `commit()`s once, propagating any exception out of the thread target (e.g. collect into a queue and re-raise). After all threads join: (1) `count(TaskEvent where task_id=tid) == T*N` (1000), (2) `select sequence_num` is exactly `set(range(1, T*N+1))` with no duplicates and no gaps, (3) every row’s `task_id == tid`. Equivalently per-thread: no `IntegrityError` bubbles, or all conflicts are retried internally and the final DB state matches.

Important detail: the existing harness masks failures; a valid regression must propagate worker exceptions and assert the count, not just uniqueness.

## 9. Difficulty

**L (large).** Allocation must become atomic with respect to concurrent sessions while preserving (i) the “participates in caller’s transaction” contract, (ii) autoflush-less sessions, (iii) cross-session visibility, and (iv) event history ordering/query invariants. 88 append sites + 28 construction sites + 17 tests depend on the current read-add-return behaviour; a naive single-writer/lock change would serialise all task appends application-wide and risk perf/regression, so care is needed. T-shirt estimate is large, not medium.

## 10. Open items not in this report

- Exact `PRAGMA`/transaction isolation level in effect at runtime (Python `sqlite3` implicit `BEGIN` vs SQLAlchemy) was not measured; only the declared engine config was read.
- Whether `PRAGMA foreign_keys` should be on is out of scope for this defect.
- No attempt was made to run or modify tests (research-only).
