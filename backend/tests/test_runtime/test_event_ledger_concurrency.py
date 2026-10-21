"""Concurrency coverage for :class:`DbEventLedger` (item 008 redo)."""

import gc
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime

import pytest

from backend.core.db_manager import DatabaseManager
from backend.models.database import Task, TaskEvent
from backend.runtime.events import DbEventLedger, EventSequenceAllocationError

pytestmark = [pytest.mark.concurrency]


def fresh_db():
    p = tempfile.mktemp(suffix=".db")
    m = DatabaseManager(p)
    sess = m.new_session()
    return m, sess, p


def cleanup(m, p, sess=None):
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
    try:
        m.engine.dispose()
    except Exception:
        pass
    gc.collect()
    time.sleep(0.12)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except Exception:
            pass


def _make_task(sess, tid=None):
    tid = tid or str(uuid.uuid4())
    sess.add(Task(id=tid, original_request="t", state="running", version=1,
                  created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
    sess.commit()
    return tid


@pytest.mark.concurrency
def test_event_ledger_100_threads_10_events_gapless_and_no_swallowed_exceptions():
    p = tempfile.mktemp(suffix=".db")
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
        event_ids = [e["id"] for e in hist]
        owning_rows = s2.query(TaskEvent).filter(TaskEvent.id.in_(event_ids)).all()
        assert len(owning_rows) == len(event_ids)
        assert {row.task_id for row in owning_rows} == {tid}
    finally:
        s2.close()
        mgr2.engine.dispose()

    gc.collect()
    time.sleep(0.15)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except Exception:
            pass


def test_multi_append_preserves_earlier_pending_rows():
    m, sess, p = fresh_db()
    try:
        tid = _make_task(sess)
        ledger = DbEventLedger(sess)
        first = ledger.append_event(tid, "e1", {"i": 0})
        second = ledger.append_event(tid, "e2", {"i": 1})
        assert first == 1
        assert second == 2
        # Unflushed appends are visible to latest_sequence in the same txn.
        assert ledger.latest_sequence(tid) == 2
        # The first append may have been flushed into the uncommitted
        # transaction by the second allocation, but it is not lost: both
        # sequence numbers are present and latest_sequence reflects them.
        sess.rollback()
    finally:
        cleanup(m, p, sess)


def test_latest_sequence_accounts_for_pending_rows():
    m, sess, p = fresh_db()
    try:
        tid = _make_task(sess)
        ledger = DbEventLedger(sess)
        ledger.append_event(tid, "e1", {"i": 0})
        assert ledger.latest_sequence(tid) == 1
        ledger.append_event(tid, "e2", {"i": 1})
        assert ledger.latest_sequence(tid) == 2
        sess.rollback()
    finally:
        cleanup(m, p, sess)


def test_migration_idempotent_and_counter_floor():
    tid = str(uuid.uuid4())
    p = tempfile.mktemp(suffix=".db")
    m = DatabaseManager(p)
    sess = m.new_session()
    sess.add(Task(id=tid, original_request="t", state="running", version=1,
                  created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
    sess.commit()
    # Existing (possibly out-of-band) events at 1 and 3.
    sess.add(TaskEvent(task_id=tid, event_type="legacy", event_data="{}", sequence_num=1, created_at=datetime.utcnow()))
    sess.add(TaskEvent(task_id=tid, event_type="legacy", event_data="{}", sequence_num=3, created_at=datetime.utcnow()))
    sess.commit()
    sess.close()
    m.engine.dispose()

    # Reopen: migration re-runs and backfills.
    m2 = DatabaseManager(p)
    s2 = m2.new_session()
    seq = DbEventLedger(s2).append_event(tid, "after", {"x": 1})
    s2.commit()
    assert seq >= 4
    assert DbEventLedger(s2).latest_sequence(tid) >= 4
    s2.close()
    m2.engine.dispose()

    gc.collect()
    time.sleep(0.12)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except Exception:
            pass


def test_event_ledger_concurrency_kernel_ordering_still():
    m, sess, p = fresh_db()
    try:
        from concurrent.futures import ThreadPoolExecutor
        tida = str(uuid.uuid4())
        tidb = str(uuid.uuid4())
        ta = Task(id=tida, original_request="a", state="running", version=1, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        tb = Task(id=tidb, original_request="b", state="running", version=1, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
        sess.add_all([ta, tb])
        sess.commit()

        def writer(tid, indices):
            mgr = DatabaseManager(p)
            s = mgr.new_session()
            try:
                ledger = DbEventLedger(s)
                for i in indices:
                    ledger.append_event(tid, f"e{i}", {"i": i})
                s.commit()
            finally:
                s.close()
                mgr.engine.dispose()

        with ThreadPoolExecutor(max_workers=4) as ex:
            f1 = ex.submit(writer, tida, range(10))
            f2 = ex.submit(writer, tidb, range(10))
            f1.result()
            f2.result()
        s2 = m.new_session()
        try:
            sa = [e["sequence_num"] for e in DbEventLedger(s2).event_history(tida)]
            sb = [e["sequence_num"] for e in DbEventLedger(s2).event_history(tidb)]
            assert sa == list(range(1, 11))
            assert sb == list(range(1, 11))
        finally:
            s2.close()
    finally:
        cleanup(m, p, sess)