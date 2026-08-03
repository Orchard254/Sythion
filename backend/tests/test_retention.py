"""
Phase F retention and purge tests.

Owner-configurable retention (default 90 days) and complete-record purge with
non-sensitive purge receipts. Purge deletes the task row AND its full audit
event record; receipts never contain task content.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task, TaskEvent, PurgeReceipt, SettingsMetadata
from backend.services.retention import (
    DEFAULT_RETENTION_DAYS,
    get_retention_days,
    list_purge_receipts,
    purge_expired,
    purge_task,
)


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


def _task(db, task_id, created_days_ago=0, state="completed", with_events=2):
    task = Task(
        id=task_id,
        original_request="sensitive request text that must never reach a receipt",
        state=state,
        version=1,
        created_at=datetime.utcnow() - timedelta(days=created_days_ago),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    for i in range(with_events):
        db.add(TaskEvent(
            task_id=task_id,
            event_type="tool.executed",
            event_data='{"success": true}',
            sequence_num=i + 1,
        ))
    db.commit()
    return task


class TestRetentionDefaults:
    def test_default_retention_days(self, db_session):
        assert get_retention_days(db_session) == DEFAULT_RETENTION_DAYS == 90

    def test_persisted_retention_setting_wins(self, db_session):
        db_session.add(SettingsMetadata(key="retention_days", value="30"))
        db_session.commit()
        assert get_retention_days(db_session) == 30

    def test_invalid_persisted_value_falls_back(self, db_session):
        db_session.add(SettingsMetadata(key="retention_days", value="not-a-number"))
        db_session.commit()
        assert get_retention_days(db_session) == DEFAULT_RETENTION_DAYS


class TestCompleteRecordPurge:
    def test_purge_task_removes_record_and_events(self, db_session):
        _task(db_session, "t-old", created_days_ago=200)
        receipt = purge_task(db_session, "t-old", reason="owner_requested")

        assert receipt is not None
        assert receipt["task_id"] == "t-old"
        assert receipt["event_count"] == 2
        assert db_session.query(Task).filter_by(id="t-old").first() is None
        assert db_session.query(TaskEvent).filter_by(task_id="t-old").count() == 0

    def test_purge_unknown_task_returns_none(self, db_session):
        assert purge_task(db_session, "does-not-exist") is None

    def test_receipt_is_non_sensitive(self, db_session):
        """The receipt contains identity + timing only, never request text."""
        _task(db_session, "t-secret")
        purge_task(db_session, "t-secret")
        rows = db_session.query(PurgeReceipt).all()
        assert rows
        serialized = str([r.__dict__ for r in rows])
        assert "sensitive request text" not in serialized

    def test_receipt_persisted_and_listed(self, db_session):
        _task(db_session, "t1", created_days_ago=100)
        purge_task(db_session, "t1", reason="retention_expired")
        receipts = list_purge_receipts(db_session)
        assert len(receipts) == 1
        assert receipts[0]["task_id"] == "t1"
        assert receipts[0]["reason"] == "retention_expired"
        assert receipts[0]["event_count"] == 2

    def test_receipts_never_include_task_content(self, db_session):
        _task(db_session, "t2")
        purge_task(db_session, "t2")
        for receipt in list_purge_receipts(db_session):
            values = " ".join(str(v) for v in receipt.values())
            assert "sensitive request text" not in values


class TestRetentionExpiry:
    def test_expired_records_purged(self, db_session):
        _task(db_session, "t-old", created_days_ago=200)
        _task(db_session, "t-recent", created_days_ago=10)
        receipts = purge_expired(db_session, days=90)

        assert len(receipts) == 1
        assert receipts[0]["task_id"] == "t-old"
        assert db_session.query(Task).filter_by(id="t-old").first() is None
        assert db_session.query(Task).filter_by(id="t-recent").first() is not None

    def test_nothing_purged_within_window(self, db_session):
        _task(db_session, "t-recent", created_days_ago=10)
        assert purge_expired(db_session, days=90) == []
        assert db_session.query(Task).count() == 1

    def test_explicit_now_injected(self, db_session):
        _task(db_session, "t-old", created_days_ago=200)
        now = datetime.utcnow()
        receipts = purge_expired(db_session, days=90, now=now)
        assert len(receipts) == 1

    def test_uses_configured_retention(self, db_session):
        db_session.add(SettingsMetadata(key="retention_days", value="10"))
        db_session.commit()
        _task(db_session, "t-15d", created_days_ago=15)
        _task(db_session, "t-5d", created_days_ago=5)
        receipts = purge_expired(db_session)
        assert [r["task_id"] for r in receipts] == ["t-15d"]

    def test_purge_is_complete_record_no_partial_state(self, db_session):
        _task(db_session, "t-full", created_days_ago=300, with_events=5)
        purge_task(db_session, "t-full")
        # No orphaned events, no leftover task, exactly one receipt.
        assert db_session.query(TaskEvent).filter_by(task_id="t-full").count() == 0
        assert db_session.query(PurgeReceipt).count() == 1
