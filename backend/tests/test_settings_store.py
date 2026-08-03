"""
Tests for the non-secret settings store.

Privacy contract: only whitelisted non-secret keys persist to the
settings_metadata table; unknown keys and secrets are rejected. Values come
back typed, and file_roots round-trips as a JSON list.
"""
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, SettingsMetadata
from backend.core import settings_store


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


class TestUpdateValidation:
    def test_unknown_key_rejected(self, db_session):
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"not_a_setting": "x"})

    def test_secret_key_rejected(self, db_session):
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"ollama_api_key": "secret"})

    def test_boolean_validation(self, db_session):
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"voice_recording_enabled": "yes"})
        settings_store.update_settings(db_session, {"voice_recording_enabled": True})
        result = settings_store.get_settings(db_session)
        assert result["voice_recording_enabled"] is True

    def test_integer_validation(self, db_session):
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"retention_days": 0})
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"retention_days": "abc"})
        settings_store.update_settings(db_session, {"retention_days": 30})
        assert settings_store.get_settings(db_session)["retention_days"] == 30

    def test_control_characters_rejected(self, db_session):
        with pytest.raises(ValueError):
            settings_store.update_settings(db_session, {"voice_tts_voice": "bad\nvoice"})

    def test_file_roots_roundtrip(self, db_session):
        settings_store.update_settings(db_session, {"file_roots": ["C:\\Work", "D:\\Docs"]})
        result = settings_store.get_settings(db_session)
        assert result["file_roots"] == ["C:\\Work", "D:\\Docs"]

    def test_file_roots_from_comma_string(self, db_session):
        settings_store.update_settings(db_session, {"file_roots": "C:\\A, C:\\B"})
        result = settings_store.get_settings(db_session)
        assert result["file_roots"] == ["C:\\A", "C:\\B"]

    def test_partial_failure_persists_nothing(self, db_session):
        """A bad key in the batch aborts the whole update atomically."""
        with pytest.raises(ValueError):
            settings_store.update_settings(
                db_session, {"retention_days": 10, "bogus": 1}
            )
        result = settings_store.get_settings(db_session)
        assert "retention_days" not in result

    def test_unset_returns_empty(self, db_session):
        assert settings_store.get_settings(db_session) == {}


class TestPersistence:
    def test_value_persisted_to_table(self, db_session):
        settings_store.update_settings(db_session, {"voice_enabled": True})
        row = db_session.query(SettingsMetadata).filter_by(key="voice_enabled").first()
        assert row is not None
        assert row.value == "true"

    def test_update_overwrites(self, db_session):
        settings_store.update_settings(db_session, {"retention_days": 30})
        settings_store.update_settings(db_session, {"retention_days": 60})
        rows = db_session.query(SettingsMetadata).filter_by(key="retention_days").all()
        assert len(rows) == 1
        assert rows[0].value == "60"
