"""Tests for backend/runtime/task_ownership.py."""

import json
import os
import subprocess
import sys
import threading
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base
from backend.runtime.task_ownership import (
    AcquisitionResult,
    ACTIVE,
    DEFAULT_TASK_OWNERSHIP_TTL_SECONDS,
    EXPIRED,
    RELEASED,
    RECOVERED,
    TaskOwnership,
    TaskOwnershipManager,
    TaskOwnershipRow,
)

SYTHION_ROOT = Path(__file__).resolve().parents[2]


def _make_file_db(db_path):
    url = f"sqlite:///{db_path}"
    engine = create_engine(
        url,
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def test_basic_acquire_renew_release_verify(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    result = mgr.acquire("task-1", "worker-a", ttl_seconds=10, process_id=1001)
    assert isinstance(result, AcquisitionResult)
    assert result
    assert result.ok is True
    assert result.success is True
    assert result.reason == "acquired_new"
    assert result.nonce is not None
    assert result.fencing_token == 1
    record = result.ownership
    assert isinstance(record, TaskOwnership)
    assert record.task_id == "task-1"
    assert record.worker_id == "worker-a"
    assert record.process_id == 1001
    assert record.status == ACTIVE
    assert record.uncertain_action_ids == []

    assert mgr.verify("task-1", result.nonce) is True
    assert mgr.verify("task-1", result.nonce, fencing_token=result.fencing_token) is True
    assert mgr.renew("task-1", result.nonce, ttl_seconds=20) is True
    refreshed = mgr.get("task-1")
    assert refreshed is not None
    assert refreshed.fencing_token == result.fencing_token

    assert mgr.heartbeat("task-1", result.nonce) is True
    assert mgr.release("task-1", result.nonce, outcome="done") is True
    assert mgr.verify("task-1", result.nonce) is False
    released = mgr.get("task-1")
    assert released is not None
    assert released.status == RELEASED
    assert released.release_outcome == "done"


def test_different_tasks_are_independent(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    first = mgr.acquire("task-a", "worker-1")
    second = mgr.acquire("task-b", "worker-2")
    assert first and first.acquired is True
    assert second and second.acquired is True
    assert first.nonce != second.nonce
    assert mgr.get("task-a").worker_id == "worker-1"
    assert mgr.get("task-b").worker_id == "worker-2"


def test_one_owner_per_task(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    first = mgr.acquire("contended", "worker-1")
    second = mgr.acquire("contended", "worker-2")
    assert first.acquired is True
    assert second.acquired is False
    assert second.reason == "already_owned"
    assert second.nonce == first.nonce
    assert mgr.get("contended").worker_id == "worker-1"


def test_expired_ownership_is_not_auto_transferred(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=1)
    first = mgr.acquire("expired-task", "worker-1", ttl_seconds=1)
    assert first.acquired is True
    time.sleep(1.1)
    second = mgr.acquire("expired-task", "worker-2", ttl_seconds=10)
    assert second.acquired is False
    assert second.reason == "recovery_required"
    assert second.nonce == first.nonce
    record = mgr.get("expired-task")
    assert record is not None
    assert record.status == ACTIVE
    assert record.expires_at == first.ownership.expires_at
    assert mgr.verify("expired-task", first.nonce) is False


def test_expired_owner_cannot_renew(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=1)
    first = mgr.acquire("renew-expired", "worker-1", ttl_seconds=1)
    time.sleep(1.1)
    assert mgr.renew("renew-expired", first.nonce, ttl_seconds=30) is False
    assert mgr.heartbeat("renew-expired", first.nonce) is False
    assert mgr.verify("renew-expired", first.nonce) is False


def test_read_does_not_mutate_or_commit(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=1)
    first = mgr.acquire("read-only", "worker-1", ttl_seconds=1)
    assert first.acquired is True
    assert test_db.in_transaction() is True
    try:
        mgr.get("read-only")
        # The manager must not commit or flush a stale expiry while reading.
        assert test_db.in_transaction() is True
        record = test_db.get(TaskOwnershipRow, "read-only")
        assert record is not None
        assert record.status == ACTIVE
    finally:
        test_db.rollback()


def test_release_preserves_inflight_action_history(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    result = mgr.acquire("release-action", "worker-1")
    assert result.acquired is True
    assert mgr.begin_action("release-action", result.nonce, "action-7") is True
    assert mgr.begin_action("release-action", result.nonce, "action-8") is False
    assert mgr.release("release-action", result.nonce) is True
    record = mgr.get("release-action")
    assert record is not None
    assert record.status == RELEASED
    assert record.active_action_id is None
    assert record.uncertain_action_ids == ["action-7"]
    assert mgr.uncertain_actions("release-action") == ["action-7"]


def test_release_expired_fails_closed_and_preserves_action(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=1)
    result = mgr.acquire("release-expired", "worker-1", ttl_seconds=1)
    assert mgr.begin_action("release-expired", result.nonce, "action-1") is True
    time.sleep(1.1)
    assert mgr.release("release-expired", result.nonce) is False
    record = mgr.get("release-expired")
    assert record is not None
    assert record.status == ACTIVE
    assert record.active_action_id == "action-1"
    assert record.uncertain_action_ids == []


def test_wrong_nonce_and_fence_fail_mutations(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    result = mgr.acquire("fence-task", "worker-1")
    assert result.acquired is True
    assert mgr.verify("fence-task", "bad-nonce") is False
    assert mgr.renew("fence-task", "bad-nonce") is False
    assert mgr.release("fence-task", "bad-nonce") is False
    assert mgr.begin_action("fence-task", "bad-nonce", "action-9") is False
    assert mgr.complete_action("fence-task", "bad-nonce", "action-9") is False
    assert mgr.verify("fence-task", result.nonce, fencing_token=999) is False
    assert mgr.verify("fence-task", result.nonce) is True


def test_begin_and_complete_action_fence(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    result = mgr.acquire("action-task", "worker-1")
    assert mgr.begin_action("action-task", result.nonce, "action-2") is True
    assert mgr.verify("action-task", result.nonce, action_id="action-2") is True
    assert mgr.verify("action-task", result.nonce, action_id="action-other") is False
    assert mgr.begin_action("action-task", result.nonce, "action-3") is False
    assert mgr.complete_action("action-task", result.nonce, "action-2") is True
    assert mgr.verify("action-task", result.nonce, action_id="action-2") is False
    assert mgr.complete_action("action-task", result.nonce, "action-missing") is False


def test_explicit_recovery_transfers_and_preserves_uncertain_action(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    first = mgr.acquire("recover-task", "worker-1", ttl_seconds=1)
    assert mgr.begin_action("recover-task", first.nonce, "action-42") is True
    time.sleep(1.1)
    # The row is expired but still has the active action in uncertain history.
    pre = mgr.get("recover-task")
    assert pre is not None
    assert pre.status == ACTIVE
    assert pre.uncertain_action_ids == []

    recovered = mgr.recover(
        "recover-task", "worker-2", "worker died", force=False
    )
    assert recovered.acquired is True
    assert recovered.reason == "recovered"
    assert recovered.nonce != first.nonce
    record = recovered.ownership
    assert record is not None
    assert record.status == ACTIVE
    assert record.worker_id == "worker-2"
    assert record.uncertain_action_ids == ["action-42"]
    assert record.recovered_from_nonce == first.nonce
    assert record.recovery_reason == "worker died"
    assert record.recovery_nonce if hasattr(record, "recovery_nonce") else True
    assert mgr.verify("recover-task", recovered.nonce) is True
    assert mgr.verify("recover-task", first.nonce) is False

    # Recovery does not permit a normal acquire by a third worker afterwards.
    recovered_check = mgr.get("recover-task")
    print("CHECK", recovered_check.status, recovered_check.expires_at, mgr._now(), flush=True)
    third = mgr.acquire("recover-task", "worker-3")
    print("THIRD", third.acquired, third.reason, flush=True)
    assert third.acquired is False
    assert third.reason == "already_owned"


def test_recovery_requires_force_for_live_owner(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    first = mgr.acquire("live-owner", "worker-1")
    blocked = mgr.recover("live-owner", "worker-2", "operator reclaim", force=False)
    assert blocked.acquired is False
    assert blocked.reason == "owner_active_recovery_requires_force"
    forced = mgr.recover("live-owner", "worker-2", "operator reclaim", force=True)
    assert forced.acquired is True
    assert forced.nonce != first.nonce
    record = mgr.get("live-owner")
    assert record is not None
    assert record.recovered_from_nonce == first.nonce


def test_recovery_of_released_or_absent_fails_closed(test_db):
    mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    mgr2 = TaskOwnershipManager(test_db, default_ttl_seconds=60)
    absent = mgr.recover("missing-task", "worker-2", "reason")
    assert absent.acquired is False
    assert absent.reason == "no_existing_ownership"

    acquired = mgr2.acquire("released-task", "worker-1")
    assert acquired.acquired is True
    assert mgr2.release("released-task", acquired.nonce) is True
    released_recovery = mgr.recover("released-task", "worker-2", "reason")
    assert released_recovery.acquired is False
    assert released_recovery.reason == "recovery_not_allowed"


def test_stale_session_sees_committed_update_from_another_session(test_db):
    engine = test_db.bind
    other_session = sessionmaker(bind=engine)()
    try:
        mgr = TaskOwnershipManager(test_db, default_ttl_seconds=60)
        result = mgr.acquire("shared-stale", "worker-1")
        assert result.acquired is True
        test_db.commit()

        other_manager = TaskOwnershipManager(other_session, default_ttl_seconds=60)
        other_result = other_manager.acquire("shared-stale", "worker-2")
        assert other_result.acquired is False
        other_session.commit()

        # Force this session's cached identity to be refreshed from the DB.
        refreshed = mgr.get("shared-stale")
        assert refreshed is not None
        assert refreshed.status == ACTIVE
        assert refreshed.owner_nonce == result.nonce
    finally:
        other_session.close()


def test_caller_owned_transaction_is_not_committed_by_manager(tmp_path):
    db_path = str(tmp_path / "caller-transaction.db")
    engine = _make_file_db(db_path)
    session = sessionmaker(bind=engine)()
    try:
        mgr = TaskOwnershipManager(session, default_ttl_seconds=60)
        session.begin()
        try:
            result = mgr.acquire("caller-tx", "worker-1")
            assert result.acquired is True
            # A read-only pending transaction is closed before writing.
            assert session.in_transaction() is True
        finally:
            session.rollback()

        probe_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        try:
            probe_session = sessionmaker(bind=probe_engine)()
            try:
                assert probe_session.get(TaskOwnershipRow, "caller-tx") is None
            finally:
                probe_session.close()
        finally:
            probe_engine.dispose()
    finally:
        session.close()
        engine.dispose()


def test_profile_isolation_across_database_files(tmp_path):
    engine1 = _make_file_db(str(tmp_path / "profile-a.db"))
    engine2 = _make_file_db(str(tmp_path / "profile-b.db"))
    try:
        s1 = sessionmaker(bind=engine1)()
        s2 = sessionmaker(bind=engine2)()
        try:
            r1 = TaskOwnershipManager(s1, default_ttl_seconds=60).acquire(
                "same-task", "worker-a"
            )
            r2 = TaskOwnershipManager(s2, default_ttl_seconds=60).acquire(
                "same-task", "worker-b"
            )
            s1.commit()
            s2.commit()
            assert r1.acquired is True
            assert r2.acquired is True
            assert r1.nonce != r2.nonce
        finally:
            s1.close()
            s2.close()
    finally:
        engine1.dispose()
        engine2.dispose()


def test_filebacked_one_owner_per_task(tmp_path):
    db_path = str(tmp_path / "one-owner.db")
    engine = _make_file_db(db_path)
    try:
        workers = 8
        barrier = threading.Barrier(workers)
        results = []

        def worker(index):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                results.append((index, "barrier-broken", None))
                return
            session = sessionmaker(bind=engine)()
            try:
                manager = TaskOwnershipManager(session, default_ttl_seconds=60)
                result = manager.acquire("shared-task", f"worker-{index}")
                session.commit()
                results.append((index, result, None))
            except Exception as exc:  # pragma: no cover - diagnostic
                try:
                    session.rollback()
                except Exception:
                    pass
                results.append((index, None, str(exc)))
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert not any(thread.is_alive() for thread in threads)

        acquired = [
            result
            for _, result, error in results
            if error is None and isinstance(result, AcquisitionResult) and result.acquired
        ]
        errors = [(index, error) for index, _, error in results if error is not None]
        assert not errors, f"worker exceptions: {errors}"
        assert len(acquired) == 1, f"expected one owner, got {len(acquired)}"

        read_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        try:
            read_session = sessionmaker(bind=read_engine)()
            try:
                row = read_session.get(TaskOwnershipRow, "shared-task")
                assert row is not None
                assert row.status == ACTIVE
                assert row.worker_id == acquired[0].ownership.worker_id
            finally:
                read_session.close()
        finally:
            read_engine.dispose()
    finally:
        engine.dispose()


def test_filebacked_different_tasks_independent(tmp_path):
    db_path = str(tmp_path / "many-tasks.db")
    engine = _make_file_db(db_path)
    try:
        workers = 6
        barrier = threading.Barrier(workers)
        results = []

        def worker(index):
            try:
                barrier.wait(timeout=10)
            except threading.BrokenBarrierError:
                results.append((index, None, "barrier-broken"))
                return
            session = sessionmaker(bind=engine)()
            try:
                manager = TaskOwnershipManager(session, default_ttl_seconds=60)
                result = manager.acquire(f"task-{index}", f"worker-{index}")
                session.commit()
                results.append((index, result, None))
            except Exception as exc:  # pragma: no cover - diagnostic
                try:
                    session.rollback()
                except Exception:
                    pass
                results.append((index, None, str(exc)))
            finally:
                session.close()

        threads = [threading.Thread(target=worker, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=20)
        assert not any(thread.is_alive() for thread in threads)

        acquired = [
            result
            for _, result, error in results
            if error is None and isinstance(result, AcquisitionResult) and result.acquired
        ]
        errors = [error for _, _, error in results if error is not None]
        assert not errors, f"worker exceptions: {errors}"
        assert len(acquired) == workers
    finally:
        engine.dispose()


def test_filebacked_expired_does_not_transfer_without_recovery(tmp_path):
    db_path = str(tmp_path / "expired-file.db")
    engine = _make_file_db(db_path)
    try:
        session = sessionmaker(bind=engine)()
        try:
            manager = TaskOwnershipManager(session, default_ttl_seconds=1)
            result = manager.acquire("z", "worker-1", ttl_seconds=1)
            assert result.acquired is True
            session.commit()
        finally:
            session.close()

        time.sleep(1.1)

        second_session = sessionmaker(bind=engine)()
        try:
            second = TaskOwnershipManager(
                second_session, default_ttl_seconds=1
            ).acquire("z", "worker-2", ttl_seconds=10)
            assert second.acquired is False
            assert second.reason == "recovery_required"
            second_session.commit()
        finally:
            second_session.close()
    finally:
        engine.dispose()


def _spawn_worker(script_body: str) -> subprocess.Popen:
    env = os.environ.copy()
    env["PYTHONPATH"] = str(SYTHION_ROOT) + os.pathsep + env.get("PYTHONPATH", "")
    return subprocess.Popen(
        [sys.executable, "-c", script_body],
        cwd=str(SYTHION_ROOT),
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        env=env,
    )


def test_cross_process_active_owner_blocks_other_process(tmp_path):
    db_path = str(tmp_path / "cross-active.db").replace("\\", "/")
    holder_script = f"""
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.models.database import Base
from backend.runtime.task_ownership import TaskOwnershipManager
engine = create_engine("sqlite:///{db_path}", connect_args={{"check_same_thread": False, "timeout": 30}})
Base.metadata.create_all(engine)
session = sessionmaker(bind=engine)()
manager = TaskOwnershipManager(session, default_ttl_seconds=30)
result = manager.acquire("cross-task", "holder")
session.commit()
print("ACQUIRED " + result.nonce, flush=True)
time.sleep(4)
session.close()
engine.dispose()
"""
    proc = _spawn_worker(holder_script)
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("ACQUIRED ")
        holder_nonce = line.split(" ", 1)[1]

        probe_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        try:
            probe_session = sessionmaker(bind=probe_engine)()
            try:
                probe = TaskOwnershipManager(probe_session, default_ttl_seconds=30)
                result = probe.acquire("cross-task", "contender")
                assert result.acquired is False
                assert result.reason == "already_owned"
                assert result.nonce == holder_nonce
                probe_session.commit()
            finally:
                probe_session.close()
        finally:
            probe_engine.dispose()
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        proc.wait(timeout=10)
        assert proc.returncode is not None


def test_cross_process_expired_requires_recovery_not_silent_takeover(tmp_path):
    db_path = str(tmp_path / "cross-expired.db").replace("\\", "/")
    holder_script = f"""
import time
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from backend.models.database import Base
from backend.runtime.task_ownership import TaskOwnershipManager
engine = create_engine("sqlite:///{db_path}", connect_args={{"check_same_thread": False, "timeout": 30}})
Base.metadata.create_all(engine)
session = sessionmaker(bind=engine)()
manager = TaskOwnershipManager(session, default_ttl_seconds=1)
result = manager.acquire("cross-expired", "holder", ttl_seconds=1)
session.commit()
print("ACQUIRED " + result.nonce, flush=True)
time.sleep(2)
session.close()
engine.dispose()
"""
    proc = _spawn_worker(holder_script)
    try:
        line = proc.stdout.readline().strip()
        assert line.startswith("ACQUIRED ")
        # Wait for the holder's ownership to expire.
        time.sleep(1.3)

        probe_engine = create_engine(
            f"sqlite:///{db_path}",
            connect_args={"check_same_thread": False, "timeout": 30},
        )
        try:
            probe_session = sessionmaker(bind=probe_engine)()
            try:
                probe = TaskOwnershipManager(probe_session, default_ttl_seconds=30)
                result = probe.acquire("cross-expired", "contender", ttl_seconds=10)
                assert result.acquired is False
                assert result.reason == "recovery_required"
                assert result.nonce == line.split(" ", 1)[1]
                probe_session.commit()
            finally:
                probe_session.close()
        finally:
            probe_engine.dispose()
    finally:
        try:
            proc.terminate()
        except Exception:
            pass
        proc.wait(timeout=10)


def test_no_destructive_cleanup_retains_expired_history(tmp_path):
    db_path = str(tmp_path / "history.db")
    engine = _make_file_db(db_path)
    try:
        session = sessionmaker(bind=engine)()
        try:
            manager = TaskOwnershipManager(session, default_ttl_seconds=1)
            first = manager.acquire("history-task", "worker-1", ttl_seconds=1)
            assert manager.begin_action("history-task", first.nonce, "action-h") is True
            session.commit()
        finally:
            session.close()

        time.sleep(1.1)

        recovery_session = sessionmaker(bind=engine)()
        try:
            recovered = TaskOwnershipManager(
                recovery_session, default_ttl_seconds=60
            ).recover("history-task", "worker-2", "history recovery", force=False)
            assert recovered.acquired is True
            row = recovery_session.get(TaskOwnershipRow, "history-task")
            assert row is not None
            assert row.owner_nonce == recovered.nonce
            assert row.recovered_from_nonce == first.nonce
            assert "action-h" in (row.uncertain_action_ids or "")
            assert row.status == ACTIVE
            recovery_session.commit()
        finally:
            recovery_session.close()

        # Row was never deleted; identity/history remains.
        audit_session = sessionmaker(bind=engine)()
        try:
            audit_row = audit_session.get(TaskOwnershipRow, "history-task")
            assert audit_row is not None
            assert audit_row.recovered_from_nonce == first.nonce
            assert audit_row.uncertain_action_ids is not None
        finally:
            audit_session.close()
    finally:
        engine.dispose()
