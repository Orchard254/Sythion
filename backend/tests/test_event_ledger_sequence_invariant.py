"""Invariant test for DbEventLedger sequence allocation under contention."""

import gc
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime

import pytest

from backend.core.db_manager import DatabaseManager
from backend.models.database import Task
from backend.runtime.events import DbEventLedger


@pytest.mark.concurrency
def test_event_ledger_100_threads_10_events_gapless_and_no_swallowed_exceptions():
    p = tempfile.mktemp(suffix=".db")
    # Pre-create the task and seed the schema before launching threads so
    # concurrent workers only append, never bootstrap the schema in parallel.
    tid = str(uuid.uuid4())
    m = DatabaseManager(p)
    sess = m.new_session()
    sess.add(Task(id=tid, original_request="invariant", state="running",
                 version=1, created_at=datetime.utcnow(),
                 updated_at=datetime.utcnow()))
    sess.commit()
    sess.close()
    m.engine.dispose()

    T = 100
    N = 10
    errors = []

    def write_events(thread_index):
        try:
            mgr = DatabaseManager(p)
            s = mgr.new_session()
            try:
                ledger = DbEventLedger(s)
                for i in range(N):
                    ledger.append_event(tid, f"e{i}", {"t": thread_index, "i": i})
                s.commit()
            except Exception as exc:  # noqa: BLE001
                s.rollback()
                errors.append(exc)
            finally:
                s.close()
                mgr.engine.dispose()
        except Exception as exc:  # noqa: BLE001
            errors.append(exc)

    threads = [threading.Thread(target=write_events, args=(i,)) for i in range(T)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    # Worker exceptions must propagate (no swallowing).
    assert not errors, errors[0] if errors else None

    mgr2 = DatabaseManager(p)
    s2 = mgr2.new_session()
    try:
        hist = DbEventLedger(s2).event_history(tid)
        seqs = [e["sequence_num"] for e in hist]
        assert len(hist) == T * N, f"expected {T * N} rows got {len(hist)}"
        assert len(set(seqs)) == T * N, "duplicate sequence numbers present"
        assert sorted(seqs) == list(range(1, T * N + 1)), "gaps in sequence numbers"
        assert DbEventLedger(s2).latest_sequence(tid) == T * N
        # ownership: re-read owning rows by primary key
        from backend.models.database import TaskEvent
        event_ids = [e["id"] for e in hist]
        owning_rows = s2.query(TaskEvent).filter(TaskEvent.id.in_(event_ids)).all()
        assert len(owning_rows) == len(event_ids)
        assert {row.task_id for row in owning_rows} == {tid}
    finally:
        s2.close()
        mgr2.engine.dispose()

    # Cleanup (gc/sleep to avoid native faults on Windows).
    gc.collect()
    time.sleep(0.15)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except Exception:
            pass
