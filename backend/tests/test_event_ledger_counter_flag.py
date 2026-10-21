"""Regression tests for item 017: counter-table flag must not stick.

Both tests rebuild the persisted state through a fresh ``DbEventLedger`` after
commit so they cannot pass for the wrong (in-memory only) reason.
"""

import gc
import os
import tempfile
import time
import uuid
from datetime import datetime
from typing import Tuple

from sqlalchemy import text

from backend.core.db_manager import DatabaseManager
from backend.models.database import Task
from backend.runtime.events import DbEventLedger, EventSequenceAllocationError


def _open_absent_counter_db():
    """Return (m, sess, p, tid) with ``task_event_sequences`` absent."""
    p = tempfile.mktemp(suffix=".db")
    m = DatabaseManager(p)
    sess = m.new_session()
    tid = str(uuid.uuid4())
    sess.add(
        Task(
            id=tid,
            original_request="regression",
            state="running",
            version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    sess.commit()
    sess.execute(text("DROP TABLE IF EXISTS task_event_sequences"))
    sess.commit()
    return m, sess, p, tid


def _cleanup(m, sess, p):
    if sess is not None:
        try:
            sess.close()
        except Exception:
            pass
    if m is not None:
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


def test_counter_table_not_stuck_variant_a_caller_rollback():
    m, sess, p, tid = _open_absent_counter_db()
    try:
        ledger = DbEventLedger(sess)
        first = ledger.append_event(tid, "counter_a", {"variant": "a"})
        assert first == 1

        # Caller rolls back the (successful) append: the counter-table creation
        # and the event row must both disappear.
        sess.rollback()

        # The next append on the same ledger must recreate the table and allocate.
        second = ledger.append_event(tid, "counter_a", {"variant": "a"})
        assert second == 1

        sess.commit()
    finally:
        try:
            sess.close()
        except Exception:
            pass
        sess = None

    # re-read through a fresh ledger
    m2 = DatabaseManager(p)
    s2 = m2.new_session()
    try:
        hist = DbEventLedger(s2).event_history(tid)
        assert len(hist) == 1, f"expected 1 persisted row, got {len(hist)}"
        assert [e["sequence_num"] for e in hist] == [1]
    finally:
        s2.close()
        m2.engine.dispose()

    _cleanup(m, None, p)


def test_counter_table_not_stuck_variant_b_failed_allocation():
    m, sess, p, tid = _open_absent_counter_db()

    import backend.runtime.events as ev

    original_sql = ev._ALLOCATION_SQL
    ev._ALLOCATION_SQL = text(
        "INSERT INTO does_not_exist (task_id, next_seq) VALUES (:task_id, :pending_max)"
    )

    ledger = DbEventLedger(sess)
    raised = None
    try:
        try:
            ledger.append_event(tid, "counter_b", {"variant": "b"})
        except EventSequenceAllocationError as exc:
            raised = exc
    finally:
        ev._ALLOCATION_SQL = original_sql

    assert raised is not None, "EventSequenceAllocationError was not raised"

    # rollback the failed (table-creation) transaction
    sess.rollback()

    # fault removed -> next append must succeed
    second = ledger.append_event(tid, "counter_b", {"variant": "b"})
    assert second == 1
    sess.commit()

    m2 = DatabaseManager(p)
    s2 = m2.new_session()
    try:
        hist = DbEventLedger(s2).event_history(tid)
        assert len(hist) == 1, f"expected 1 persisted row, got {len(hist)}"
        assert [e["sequence_num"] for e in hist] == [1]
    finally:
        s2.close()
        m2.engine.dispose()

    try:
        sess.close()
    except Exception:
        pass
    _cleanup(m, None, p)