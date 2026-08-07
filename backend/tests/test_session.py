"""
Tests for persisted session identity (backend/core/session.py).

Contract: the session id survives restarts via the settings store; a new local
calendar day (checked on every read) or an explicit reset starts a new id; a
broken settings store degrades to a stable in-memory id without raising.
"""

import datetime as _dt
import logging
import uuid

import pytest

from backend.core import session as session_module
from backend.core import settings_store
from backend.core.db_manager import DatabaseManager
from backend.models.database import set_db_manager, SettingsMetadata


class _FrozenDatetime:
    """Stand-in for session.datetime; now() returns a controllable value."""

    _now: _dt.datetime = None

    @classmethod
    def now(cls, tz=None):
        return cls._now

    @staticmethod
    def fromisoformat(value):
        return _dt.datetime.fromisoformat(value)


def _frozen(dt):
    _FrozenDatetime._now = dt


@pytest.fixture(autouse=True)
def _clean_session_state():
    """Isolate the process-global in-memory session fields between tests."""
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""
    yield
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""


@pytest.fixture
def session_db(tmp_path, monkeypatch):
    db_path = str(tmp_path / "test_session.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    monkeypatch.setattr(session_module, "datetime", _FrozenDatetime)
    with db_mgr.get_session() as db:
        yield db


def _persist(db, session_id, started_at):
    settings_store.set_setting(db, "current_session_id", session_id)
    settings_store.set_setting(db, "session_started_at", started_at)
    db.commit()


def _assert_uuid(value):
    uuid.UUID(value)
    return value


def test_first_boot_mints_and_persists(session_db):
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    sid = _assert_uuid(session_module.get_session_id())

    row = session_db.query(SettingsMetadata).filter_by(key="current_session_id").first()
    assert row is not None
    assert row.value == sid
    started = session_db.query(SettingsMetadata).filter_by(key="session_started_at").first()
    assert started.value == "2026-08-06T09:00:00"

    # A second read returns the same id and writes nothing new.
    assert session_module.get_session_id() == sid
    assert session_db.query(SettingsMetadata).filter_by(key="current_session_id").count() == 1


def test_same_day_restart_reuses_persisted_id(session_db):
    _persist(session_db, "persisted-session-1", "2026-08-06T09:00:00")
    _frozen(_dt.datetime(2026, 8, 6, 17, 30, 0))  # "later today"

    assert session_module.get_session_id() == "persisted-session-1"
    started = session_db.query(SettingsMetadata).filter_by(key="session_started_at").first()
    assert started.value == "2026-08-06T09:00:00"  # untouched: no new write


def test_midnight_rollover_mints_new_id(session_db):
    _persist(session_db, "yesterday-session-1", "2026-08-05T23:00:00")
    _frozen(_dt.datetime(2026, 8, 6, 0, 30, 0))

    sid = _assert_uuid(session_module.get_session_id())

    assert sid != "yesterday-session-1"
    row = session_db.query(SettingsMetadata).filter_by(key="current_session_id").first()
    assert row.value == sid
    started = session_db.query(SettingsMetadata).filter_by(key="session_started_at").first()
    assert started.value == "2026-08-06T00:30:00"


def test_unparseable_started_at_mints_new_id(session_db):
    _persist(session_db, "broken-session-1", "not-a-date")
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    sid = _assert_uuid(session_module.get_session_id())

    assert sid != "broken-session-1"


def test_db_read_failure_degrades_in_memory(session_db, monkeypatch, caplog):
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    def _boom(db, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(settings_store, "get_setting", _boom)

    with caplog.at_level(logging.WARNING):
        sid = session_module.get_session_id()

    _assert_uuid(sid)
    assert "in-memory" in caplog.text
    # The degraded id is stable within the process, not re-minted per call.
    assert session_module.get_session_id() == sid


def test_db_write_failure_degrades_in_memory(session_db, monkeypatch, caplog):
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    def _boom(db, key):
        raise RuntimeError("db down")

    monkeypatch.setattr(settings_store, "set_setting", _boom)

    with caplog.at_level(logging.WARNING):
        sid = session_module.get_session_id()

    _assert_uuid(sid)
    assert "in-memory" in caplog.text


def test_reset_persists_and_survives_restart(session_db):
    _persist(session_db, "old-session-1", "2026-08-06T08:00:00")
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    new_id = session_module.reset_session_id()
    assert new_id != "old-session-1"
    _assert_uuid(new_id)

    row = session_db.query(SettingsMetadata).filter_by(key="current_session_id").first()
    assert row.value == new_id
    assert session_db.query(SettingsMetadata).filter_by(key="current_session_id").count() == 1

    # Simulate a restart: clear all in-process memory, read from DB again.
    session_module._FALLBACK_SESSION_ID = ""
    session_module._EXPLICIT_SESSION_ID = ""
    assert session_module.get_session_id() == new_id


def test_set_current_session_id_overrides_until_reset(session_db):
    _frozen(_dt.datetime(2026, 8, 6, 9, 0, 0))

    session_module.set_current_session_id("explicit-cli-id")
    assert session_module.get_session_id() == "explicit-cli-id"

    reset_id = session_module.reset_session_id()
    assert reset_id != "explicit-cli-id"
    assert session_module.get_session_id() == reset_id
