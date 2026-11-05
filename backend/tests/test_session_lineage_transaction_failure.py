"""Fail-closed coverage for lineage transaction failures."""

import pytest

import backend.core.session as session_module
import backend.models.database as database_module
from backend.core.db_manager import DatabaseManager
from backend.models.database import Base, SettingsMetadata, SessionLineage, set_db_manager


class Frozen:
    _now = __import__("datetime").datetime(2026, 8, 6, 9, 0, 0)

    @classmethod
    def now(cls, tz=None):
        return cls._now

    @staticmethod
    def fromisoformat(value):
        return __import__("datetime").datetime.fromisoformat(value)


@pytest.fixture(autouse=True)
def _clean():
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""
    yield
    session_module._EXPLICIT_SESSION_ID = ""
    session_module._FALLBACK_SESSION_ID = ""


@pytest.fixture
def prepared(tmp_path, monkeypatch):
    db_manager = DatabaseManager(str(tmp_path / "tx-failure.db"))
    set_db_manager(db_manager)
    monkeypatch.setattr(session_module, "datetime", Frozen)
    with db_manager.get_session():
        pass
    return db_manager


def test_prune_failure_rolls_back_publish(prepared, monkeypatch):
    def fail_prune(db):
        raise RuntimeError("forced prune failure")

    monkeypatch.setattr(session_module, "_prune_session_lineage", fail_prune)

    returned = session_module.get_session_id()
    assert session_module._FALLBACK_SESSION_ID == returned

    with prepared.get_session() as db:
        assert db.query(SettingsMetadata).filter_by(key="current_session_id").first() is None
        assert db.query(SessionLineage).count() == 0


def test_lineage_insert_failure_rolls_back_before_commit(prepared, monkeypatch):
    original_lineage = database_module.SessionLineage

    class Boom:
        def __init__(*args, **kwargs):
            raise RuntimeError("forced lineage insert failure")

    monkeypatch.setattr(database_module, "SessionLineage", Boom)

    returned = session_module.get_session_id()
    assert session_module._FALLBACK_SESSION_ID == returned

    monkeypatch.setattr(database_module, "SessionLineage", original_lineage)
    with prepared.get_session() as db:
        assert db.query(SettingsMetadata).filter_by(key="current_session_id").first() is None
        assert db.query(SessionLineage).count() == 0