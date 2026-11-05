"""Focused foundation tests for atomic system-generated session lineage."""

import threading
from datetime import datetime as real_datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.core.session as session_module
from backend.core.db_manager import DatabaseManager
from backend.models.database import Base, set_db_manager, SessionLineage, SettingsMetadata


class Frozen:
    _now = real_datetime(2026, 8, 6, 9, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._now

    @staticmethod
    def fromisoformat(value):
        return real_datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def _clean_session_globals():
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""
    session_module._SESSION_LINEAGE_RETENTION_LIMIT = 30
    yield
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "session-lineage.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    monkeypatch.setattr(session_module, "datetime", Frozen)
    Frozen._now = real_datetime(2026, 8, 6, 9, 0, 0)
    with db_mgr.get_session() as db:
        yield db, db_mgr


def _set_now(value):
    Frozen._now = value


def test_first_boot_persists_one_trusted_lineage_row(session_db):
    _db, _db_mgr = session_db
    session_id = session_module.get_session_id()
    rows = session_module.get_session_lineage(trusted_only=True)
    assert len(rows) == 1
    assert rows[0].session_id == session_id
    assert rows[0].trusted is True
    assert rows[0].system_generated is True
    assert session_module.is_system_generated_session_id(session_id) is True


def test_same_day_restart_does_not_duplicate_lineage(session_db):
    db, _db_mgr = session_db
    first = session_module.get_session_id()
    _set_now(real_datetime(2026, 8, 6, 17, 0, 0))
    second = session_module.get_session_id()
    assert first == second
    assert len(session_module.get_session_lineage()) == 1


def test_midnight_rollover_records_new_trusted_generation(session_db):
    db, _db_mgr = session_db
    first = session_module.get_session_id()
    _set_now(real_datetime(2026, 8, 7, 0, 30, 0))
    second = session_module.get_session_id()
    assert second != first
    rows = session_module.get_session_lineage(trusted_only=True)
    assert len(rows) == 2
    assert [row.session_id for row in rows] == [first, second]
    assert all(row.trusted for row in rows)


def test_first_boot_concurrent_races_create_one_id_and_one_row(tmp_path, monkeypatch):
    db_path = str(tmp_path / "race.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    monkeypatch.setattr(session_module, "datetime", Frozen)
    Frozen._now = real_datetime(2026, 8, 6, 9, 0, 0)

    results = []
    lock = threading.Lock()

    def worker():
        value = session_module.get_session_id()
        with lock:
            results.append(value)

    threads = [threading.Thread(target=worker) for _ in range(16)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    assert len(set(results)) == 1
    assert len(session_module.get_session_lineage()) == 1
    assert len(session_module.get_trusted_session_ids()) == 1


def test_claim_failure_rolls_back_without_partial_lineage(session_db, monkeypatch):
    from backend.core import settings_store

    def fail_write(db, key, value):
        raise RuntimeError("forced settings write failure")

    monkeypatch.setattr(settings_store, "set_setting", fail_write)
    returned = session_module.get_session_id()
    assert session_module._FALLBACK_SESSION_ID == returned
    assert returned not in {row[0] for row in []}

    with session_db[1].get_session() as db:
        current = db.query(SettingsMetadata).filter_by(key="current_session_id").first()
        started = db.query(SettingsMetadata).filter_by(key="session_started_at").first()
        lineage_count = db.query(SessionLineage).count()
    assert current is None
    assert started is None
    assert lineage_count == 0


def test_recovery_clears_stale_fallback_after_db_returns(session_db):
    from backend.core import settings_store

    session_module._FALLBACK_SESSION_ID = "stale-in-memory-id"
    real_id = session_module.get_session_id()
    assert session_module._FALLBACK_SESSION_ID == ""
    assert real_id != "stale-in-memory-id"


def test_legacy_persisted_id_is_not_trusted(session_db):
    from backend.core import settings_store

    db, db_mgr = session_db
    legacy = "legacy-untrusted-id"
    with db_mgr.get_session() as db:
        settings_store.set_setting(db, "current_session_id", legacy)
        settings_store.set_setting(db, "session_started_at", real_datetime(2026, 8, 6, 9, 0, 0).isoformat())
        db.commit()

    assert session_module.get_session_id() == legacy
    assert session_module.is_system_generated_session_id(legacy) is False
    assert session_module.get_session_provenance(legacy) in ("legacy_or_untrusted", "unknown")


def test_explicit_override_is_not_persisted_as_lineage(session_db):
    db, _db_mgr = session_db
    system_id = session_module.get_session_id()
    session_module.set_current_session_id("owner-supplied-override-id")
    assert session_module.is_override_active() is True
    assert session_module.get_session_id() == "owner-supplied-override-id"
    assert session_module.is_system_generated_session_id("owner-supplied-override-id") is False
    assert session_module.get_session_provenance() == "override"
    session_module.clear_current_session_id()
    assert session_module.get_session_id() == system_id


def test_bounded_retention_prunes_old_generations(tmp_path, monkeypatch):
    db_path = str(tmp_path / "retention.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    monkeypatch.setattr(session_module, "datetime", Frozen)
    session_module._SESSION_LINEAGE_RETENTION_LIMIT = 2
    Frozen._now = real_datetime(2026, 8, 6, 9, 0, 0)
    with db_mgr.get_session() as db:
        pass

    first = session_module.get_session_id()
    Frozen._now = real_datetime(2026, 8, 7, 9, 0, 0)
    second = session_module.get_session_id()
    Frozen._now = real_datetime(2026, 8, 8, 9, 0, 0)
    third = session_module.get_session_id()

    lineage = session_module.get_session_lineage()
    assert first not in [row.session_id for row in lineage]
    assert [row.session_id for row in lineage] == [second, third]
    assert len(session_module.get_trusted_session_ids()) == 2


def test_unparseable_started_at_fails_closed_to_new_system_id(session_db):
    from backend.core import settings_store

    db, db_mgr = session_db
    legacy = "legacy-bad-timestamp"
    with db_mgr.get_session() as db:
        settings_store.set_setting(db, "current_session_id", legacy)
        settings_store.set_setting(db, "session_started_at", "not-a-date")
        db.commit()

    new_id = session_module.get_session_id()
    assert new_id != legacy
    assert session_module.is_system_generated_session_id(new_id) is True
    assert session_module.is_system_generated_session_id(legacy) is False