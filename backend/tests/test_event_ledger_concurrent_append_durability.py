"""Durability evidence for item 008: concurrent event appends.

Each writer uses its own SQLAlchemy session over a shared SQLite database.
Appends must never lose or collide allocations, must form a gapless
contiguous sequence, and must all be visible after commit.
"""

import gc
import os
import tempfile
import threading
import time
import uuid
from datetime import datetime

from backend.core.db_manager import DatabaseManager
from backend.models.database import Task
from backend.runtime.events import DbEventLedger


def _cleanup(p):
    gc.collect()
    time.sleep(0.12)
    for suffix in ("", "-wal", "-shm"):
        try:
            os.remove(p + suffix)
        except OSError:
            pass


def test_concurrent_append_event_Allocations_are_gapless_and_durable():
    p = tempfile.mktemp(suffix=".db")
    task_id = str(uuid.uuid4())

    mgr0 = DatabaseManager(p)
    sess0 = mgr0.new_session()
    sess0.add(
        Task(
            id=task_id,
            original_request="durability item 008",
            state="running",
            version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
    )
    sess0.commit()
    sess0.close()
    mgr0.engine.dispose()

    workers = 4
    appends_per_worker = 8
    collected = []
    collected_lock = threading.Lock()
    errors = []

    def worker(worker_index):
        mgr = DatabaseManager(p)
        session = mgr.new_session()
        try:
            ledger = DbEventLedger(session)
            for local_index in range(appends_per_worker):
                sequence = ledger.append_event(
                    task_id,
                    "concurrent_append",
                    {"worker": worker_index, "local": local_index},
                )
                session.commit()
                with collected_lock:
                    collected.append(sequence)
        except Exception as exc:  # report, do not abort the whole run silently
            errors.append(f"worker {worker_index}: {exc!r}")
            try:
                session.rollback()
            except Exception:
                pass
        finally:
            session.close()
            mgr.engine.dispose()

    threads = [
        threading.Thread(target=worker, args=(index,), name=f"writer-{index}")
        for index in range(workers)
    ]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert not errors, errors
    total = workers * appends_per_worker
    assert len(collected) == total, (len(collected), total)
    assert len(set(collected)) == total, "duplicate sequence allocation"
    assert sorted(collected) == list(range(1, total + 1)), "non-contiguous sequence"

    # Re-read through a fresh ledger so the result is durable, not session-local.
    reader_mgr = DatabaseManager(p)
    reader_session = reader_mgr.new_session()
    try:
        history = DbEventLedger(reader_session).event_history(task_id)
    finally:
        reader_session.close()
        reader_mgr.engine.dispose()

    assert len(history) == total, (len(history), total)
    assert [event["sequence_num"] for event in history] == list(range(1, total + 1))
    assert all(event["event_type"] == "concurrent_append" for event in history)

    _cleanup(p)
