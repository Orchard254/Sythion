"""Tests for automatic pre-migration SQLite backup.

These tests use temporary databases only and never touch real profiles.
"""
import os
import sqlite3
import tempfile

import pytest

from backend.core import db_manager
from backend.core.db_manager import (
    DatabaseManager,
    _cleanup_partial_backup,
    _reserve_unique_backup_path,
    backup_existing_database,
    init_database,
    verify_sqlite_integrity,
)


def _seed_db(path, value=7):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.commit()
    conn.close()
    return path


def test_fresh_database_does_not_create_backup(tmp_path):
    db_path = str(tmp_path / "fresh.db")
    # Record any pre-existing files.
    before = set(os.listdir(tmp_path))
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()
    after = set(os.listdir(tmp_path))
    new_files = [f for f in (after - before) if "backup" in f]
    assert new_files == []
    # and no backup attr for fresh db
    assert mgr.pre_migration_backup_path is None


def test_existing_database_creates_backup_with_data(tmp_path):
    db_path = str(tmp_path / "existing.db")
    _seed_db(db_path, 11)
    before = set(os.listdir(tmp_path))
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()
    after = set(os.listdir(tmp_path))
    backup_files = [f for f in (after - before) if "backup" in f]
    assert len(backup_files) == 1
    backup_path = os.path.join(tmp_path, backup_files[0])
    assert os.path.exists(backup_path)
    # data preserved
    conn = sqlite3.connect(backup_path)
    assert conn.execute("SELECT x FROM t").fetchone() == (11,)
    conn.close()
    assert mgr.pre_migration_backup_path == backup_path


def test_wal_uncheckpointed_data_preserved(tmp_path):
    db_path = str(tmp_path / "wal.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (99)")
    conn.commit()
    # deliberately do not close or checkpoint; backup should still see 99
    backup_path = backup_existing_database(db_path)
    bc = sqlite3.connect(backup_path)
    assert bc.execute("SELECT x FROM t").fetchone() == (99,)
    bc.close()
    conn.close()


def test_backup_names_are_unique_and_non_overwriting(tmp_path):
    db_path = str(tmp_path / "multi.db")
    _seed_db(db_path, 3)
    p1 = backup_existing_database(db_path)
    p2 = backup_existing_database(db_path)
    assert p1 != p2
    assert os.path.exists(p1)
    assert os.path.exists(p2)


def test_init_database_existing_creates_backup(tmp_path):
    db_path = str(tmp_path / "init.db")
    _seed_db(db_path, 5)
    before = set(os.listdir(tmp_path))
    mgr = init_database(db_path)
    mgr.engine.dispose()
    after = set(os.listdir(tmp_path))
    backups = [f for f in (after - before) if "backup" in f]
    assert len(backups) == 1


def test_fail_closed_on_backup_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "fail.db")
    _seed_db(db_path, 1)

    def _raise(src, dst):
        raise RuntimeError("injected backup failure")

    monkeypatch.setattr(db_manager, "_sqlite_online_backup", _raise)
    # also patch module global used by function
    monkeypatch.setattr(db_manager, "_sqlite_backup", _raise)

    with pytest.raises(RuntimeError, match="injected backup failure"):
        backup_existing_database(db_path)

    # no partial backup artifact remains
    leftover = [f for f in os.listdir(tmp_path) if "backup" in f]
    assert leftover == []


def test_fail_closed_on_integrity_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "corrupt.db")
    _seed_db(db_path, 1)

    # source integrity passes but backup integrity fails
    orig = db_manager.verify_sqlite_integrity
    calls = {"n": 0}

    def _maybe(path):
        # First call (source) succeeds, second (backup) fails.
        calls["n"] += 1
        if calls["n"] == 1:
            return True
        return False

    monkeypatch.setattr(db_manager, "verify_sqlite_integrity", _maybe)
    # keep aliases in sync if referenced
    for alias in ("verify_database_integrity", "check_sqlite_integrity", "_verify_sqlite_integrity"):
        monkeypatch.setattr(db_manager, alias, _maybe, raising=False)

    with pytest.raises(RuntimeError):
        backup_existing_database(db_path)

    leftover = [f for f in os.listdir(tmp_path) if "backup" in f]
    assert leftover == []

    monkeypatch.setattr(db_manager, "verify_sqlite_integrity", orig)


def test_schema_and_receipt_style_mutation_still_backs_up(tmp_path):
    from sqlalchemy import create_engine, text

    db_path = str(tmp_path / "legacy_conv.db")
    # Legacy shape that requires _apply_migrations (task_id NOT NULL).
    engine = create_engine(f"sqlite:///{db_path}", connect_args={"check_same_thread": False})
    with engine.begin() as conn:
        conn.execute(text(
            "CREATE TABLE conversation_turns ("
            "id INTEGER PRIMARY KEY AUTOINCREMENT, "
            "session_id VARCHAR NOT NULL, "
            "task_id VARCHAR NOT NULL, "
            "role VARCHAR DEFAULT 'user', "
            "summary TEXT NOT NULL, "
            "created_at DATETIME)"
        ))
        conn.execute(text(
            "INSERT INTO conversation_turns (session_id, task_id, role, summary) "
            "VALUES ('s', 't', 'user', 'legacy')"
        ))
    engine.dispose()

    before = len([f for f in os.listdir(tmp_path) if "backup" in f])
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()
    after = len([f for f in os.listdir(tmp_path) if "backup" in f])
    assert after == before + 1

    # Migration still performed, proving protection covers schema mutation.
    conn = sqlite3.connect(db_path)
    task_id_col = next(c for c in conn.execute("PRAGMA table_info(conversation_turns)") if c[1] == "task_id")
    assert task_id_col[3] == 0
    conn.close()


def test_database_manager_aborts_on_backup_failure(tmp_path, monkeypatch):
    db_path = str(tmp_path / "abort.db")
    _seed_db(db_path, 1)

    def _raise(src, dst):
        raise RuntimeError("injected backup failure")

    monkeypatch.setattr(db_manager, "_sqlite_online_backup", _raise)
    with pytest.raises(RuntimeError, match="injected backup failure"):
        DatabaseManager(db_path)


def test_no_automatic_restore_or_deletion(tmp_path):
    db_path = str(tmp_path / "keep.db")
    _seed_db(db_path, 2)
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()
    # original database still present and readable
    assert os.path.exists(db_path)
    conn = sqlite3.connect(db_path)
    assert conn.execute("SELECT x FROM t").fetchone() == (2,)
    conn.close()


def test_verify_sqlite_integrity_ok(tmp_path):
    db_path = str(tmp_path / "ok.db")
    _seed_db(db_path, 1)
    assert verify_sqlite_integrity(db_path) is True


def test_every_existing_db_startup_creates_backup(tmp_path):
    # Strict conservative policy: the clone-probe "skip" optimization is
    # rejected, so every DatabaseManager startup on an existing database takes
    # a fresh snapshot before any possible live mutation.  Backups are never
    # pruned.
    db_path = str(tmp_path / "repeat.db")
    _seed_db(db_path, 2)
    m1 = DatabaseManager(db_path)
    m1.engine.dispose()
    backups1 = [f for f in os.listdir(tmp_path) if "backup" in f]
    assert len(backups1) == 1

    m2 = DatabaseManager(db_path)
    m2.engine.dispose()
    backups2 = [f for f in os.listdir(tmp_path) if "backup" in f]
    assert len(backups2) == 2

    m3 = DatabaseManager(db_path)
    m3.engine.dispose()
    backups3 = [f for f in os.listdir(tmp_path) if "backup" in f]
    assert len(backups3) == 3


def test_newer_or_unknown_user_version_still_backs_up(tmp_path):
    db_path = str(tmp_path / "newer.db")
    _seed_db(db_path, 2)
    # Simulate a DB whose user_version claims a newer/incompatible revision.
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA user_version = 999")
    conn.commit()
    conn.close()

    before = len([f for f in os.listdir(tmp_path) if "backup" in f])
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()
    after = len([f for f in os.listdir(tmp_path) if "backup" in f])
    assert after == before + 1


def test_cleanup_only_removes_owned_artifacts(tmp_path):
    nonowned = tmp_path / "not-owned.db"
    nonowned.write_text("sentinel")
    _cleanup_partial_backup(str(nonowned))
    assert nonowned.exists()

    reserved = _reserve_unique_backup_path(str(tmp_path / "owned.db"))
    assert os.path.exists(reserved)
    _cleanup_partial_backup(reserved)
    assert not os.path.exists(reserved)


def test_atomic_reservation_is_unique(tmp_path):
    one = _reserve_unique_backup_path(str(tmp_path / "r1.db"))
    two = _reserve_unique_backup_path(str(tmp_path / "r2.db"))
    assert one != two
    _cleanup_partial_backup(one)
    _cleanup_partial_backup(two)


def _seed_delete_db(path, value=7, user_version=7):
    conn = sqlite3.connect(path)
    conn.execute("PRAGMA journal_mode=DELETE")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (?)", (value,))
    conn.execute(f"PRAGMA user_version = {user_version}")
    conn.commit()
    conn.close()
    return path


def test_delete_journal_mode_database_is_backed_up_before_wal_conversion(tmp_path):
    # Independent audit regression: a database stored in DELETE journal mode
    # must receive a pre-mutation snapshot before startup converts it to WAL.
    db_path = str(tmp_path / "delete-mode.db")
    _seed_delete_db(db_path, 13, 9)

    before_modes = sqlite3.connect(db_path).execute("PRAGMA journal_mode").fetchone()[0]
    assert str(before_modes).upper() == "DELETE"

    baseline_uv = sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0]
    assert baseline_uv == 9

    before_backups = len([f for f in os.listdir(tmp_path) if "backup" in f])
    mgr = DatabaseManager(db_path)
    mgr.engine.dispose()

    after_backups = len([f for f in os.listdir(tmp_path) if "backup" in f])
    assert after_backups == before_backups + 1, "DELETE-mode DB was not snapshotted"

    # Startup converts to WAL.
    assert str(sqlite3.connect(db_path).execute("PRAGMA journal_mode").fetchone()[0]).upper() == "WAL"

    # SQLite version state is preserved by startup.
    assert sqlite3.connect(db_path).execute("PRAGMA user_version").fetchone()[0] == 9

    # At least one backup preserves the data, version state, and pre-conversion
    # journal mode must have been snapshot via the backup API.
    backups = [f for f in os.listdir(tmp_path) if "backup" in f]
    backup_path = os.path.join(tmp_path, backups[-1])
    bconn = sqlite3.connect(backup_path)
    assert bconn.execute("SELECT x FROM t").fetchone() == (13,)
    assert bconn.execute("PRAGMA user_version").fetchone()[0] == 9
    bconn.close()


def test_missing_or_empty_main_database_backup_fails_closed(tmp_path):
    # Sidecar-only or empty-main states are ambiguous and must fail closed.
    # Nothing is deleted or modified; the sidecars remain untouched.
    sidecar_only_path = str(tmp_path / "sidecar-only.db")
    sidecar = sidecar_only_path + "-wal"
    with open(sidecar, "w", encoding="utf-8") as f:
        f.write("pending")
    with pytest.raises(RuntimeError, match="missing or empty"):
        backup_existing_database(sidecar_only_path)
    assert os.path.exists(sidecar), "sidecar was removed despite fail-closed policy"

    empty_main_path = str(tmp_path / "empty-main.db")
    with open(empty_main_path, "w", encoding="utf-8"):
        pass
    empty_sidecar = empty_main_path + "-wal"
    with open(empty_sidecar, "w", encoding="utf-8") as f:
        f.write("pending")
    with pytest.raises(RuntimeError, match="missing or empty"):
        backup_existing_database(empty_main_path)
    assert os.path.exists(empty_sidecar), "sidecar was removed despite fail-closed policy"


def test_backup_is_self_contained_and_preserves_user_version(tmp_path):
    db_path = str(tmp_path / "wal-source.db")
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("CREATE TABLE t(x)")
    conn.execute("INSERT INTO t VALUES (42)")
    conn.execute("PRAGMA user_version = 123")
    conn.commit()
    conn.close()

    backup_path = backup_existing_database(db_path)
    assert not os.path.exists(backup_path + "-wal")
    assert not os.path.exists(backup_path + "-shm")
    assert not os.path.exists(backup_path + "-journal")

    bconn = sqlite3.connect(backup_path)
    try:
        assert bconn.execute("SELECT x FROM t").fetchone() == (42,)
        assert str(bconn.execute("PRAGMA journal_mode").fetchone()[0]).upper() == "DELETE"
        assert bconn.execute("PRAGMA user_version").fetchone()[0] == 123
    finally:
        bconn.close()


def test_profile_lock_ordering(tmp_path):
    from backend.core.profile_lock import ProfileLock

    db_path = str(tmp_path / "locked.db")
    _seed_db(db_path, 9)
    lock = ProfileLock(str(tmp_path / "profile.lock"))

    # Not held yet -> backup/init must be refused.
    with pytest.raises(RuntimeError, match="profile lock"):
        DatabaseManager(db_path, profile_lock=lock)

    assert lock.acquire() is True
    try:
        mgr = DatabaseManager(db_path, profile_lock=lock)
        mgr.engine.dispose()
    finally:
        lock.release()