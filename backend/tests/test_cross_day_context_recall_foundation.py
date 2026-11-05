"""Focused fail-closed foundation tests for cross-day context recall."""

import importlib

from datetime import datetime as _dt

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import (
    Base,
    ConversationExchange,
    SessionLineage,
    Task,
)
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.cross_day_context_recall import (
    CrossDayContextSnapshot,
    read_cross_day_context_recall,
)
from backend.services.retention import purge_task, purge_expired


SESSION_ID = "cross-day-session"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'cross-day-context.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", SESSION_ID)
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


async def seed_data(db):
    db.add(Task(id="task-one", original_request="Chess plan", state="running", version=2))
    db.commit()
    await ConversationRuntime(db)._persist_conversation("Just chatting", "Hello.")
    await ConversationRuntime(db)._persist_conversation("Chess plan", "Accepted.", task_id="task-one")
    db.add(SessionLineage(
        session_id="trusted-session",
        generation=7,
        started_at=_dt.utcnow(),
        system_generated=True,
        provenance="system_uuid4",
        created_at=_dt.utcnow(),
    ))
    db.commit()
    db.add(ConversationExchange(id="trusted-e", session_id="trusted-session", user_text="U", assistant_text="R"))
    db.commit()
    return None


@pytest.mark.asyncio
async def test_no_records_for_basic_valid_scope(db):
    await seed_data(db)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID, "trusted-session"], limit=10)
    assert isinstance(snapshot, CrossDayContextSnapshot)
    assert snapshot.data_returned is False
    assert snapshot.disclosure_allowed is False
    assert snapshot.access_allowed is False
    assert snapshot.fail_closed is True
    assert snapshot.records == ()
    assert snapshot.has_more is False


@pytest.mark.asyncio
async def test_fail_closed_with_caller_booleans(db):
    await seed_data(db)
    for kwargs in (
        {"trusted_only": False},
        {"allow_untrusted": True},
        {"include_untrusted": True},
        {"trusted_only": True, "allow_untrusted": True},
    ):
        snapshot = read_cross_day_context_recall(db, [SESSION_ID], **kwargs)
        assert snapshot.records == ()
        assert snapshot.data_returned is False
        assert snapshot.access_allowed is False


@pytest.mark.asyncio
async def test_fail_closed_for_arbitrary_consistent_labels(db):
    await seed_data(db)
    snapshot = read_cross_day_context_recall(
        db,
        ["trusted-session", SESSION_ID],
        profile="profile-a",
        profile_id="profile-a",
        profiles=["profile-a"],
    )
    assert snapshot.records == ()
    assert snapshot.data_returned is False
    assert snapshot.cross_profile_disclosure_blocked is True
    assert "profile_task_boundary_unauthenticated" in snapshot.ambiguity_reasons


@pytest.mark.asyncio
async def test_fail_closed_for_trusted_lineage_rows(db):
    await seed_data(db)
    snapshot = read_cross_day_context_recall(db, ["trusted-session"], trusted_only=True)
    assert snapshot.records == ()
    assert snapshot.data_returned is False
    assert snapshot.access_allowed is False


@pytest.mark.asyncio
async def test_fail_closed_for_override_labels(db, monkeypatch):
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", SESSION_ID)
    monkeypatch.setattr(
        "backend.core.session.is_trusted_session_id",
        lambda session_id, db=None: True,
    )
    await seed_data(db)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID], profile="x", profile_id="x")
    assert snapshot.records == ()
    assert snapshot.data_returned is False
    assert snapshot.access_allowed is False


@pytest.mark.asyncio
async def test_deleted_task_context_does_not_disclose_data(db):
    db.add(Task(id="task-del", original_request="Chess", state="running", version=1))
    db.commit()
    runtime = ConversationRuntime(db)
    await runtime._persist_conversation("Chess", "Accepted", task_id="task-del")
    purge_task(db, "task-del")
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert snapshot.records == ()
    assert snapshot.data_returned is False


@pytest.mark.asyncio
async def test_read_only_no_transaction_management(db, monkeypatch):
    await seed_data(db)
    pending = ConversationExchange(id="pending", session_id=SESSION_ID, user_text="pending", assistant_text="pending")
    db.add(pending)

    def forbidden(*args, **kwargs):
        raise AssertionError("reader must not manage transactions")

    monkeypatch.setattr(db, "flush", forbidden)
    monkeypatch.setattr(db, "commit", forbidden)
    monkeypatch.setattr(db, "rollback", forbidden)

    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert snapshot.records == ()
    assert snapshot.data_returned is False
    assert pending in db.new


def test_autoflush_contract_required():
    class NoAutoflushSession:
        pass

    with pytest.raises(ValueError, match="no_autoflush"):
        read_cross_day_context_recall(NoAutoflushSession(), [SESSION_ID])


@pytest.mark.asyncio
async def test_read_errors_do_not_disclose(db, monkeypatch):
    await seed_data(db)

    def unavailable(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    # The fail-closed reader does not query, so an unavailable query is never
    # reached and therefore cannot disclose or fail after reading.
    # Ensure the normal no-query path does not call query.
    calls = {"n": 0}

    def counting_query(*args, **kwargs):
        calls["n"] += 1
        raise AssertionError("cross-day reader must not query")

    monkeypatch.setattr(db, "query", counting_query)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert calls["n"] == 0
    assert snapshot.records == ()
    assert snapshot.data_returned is False


@pytest.mark.asyncio
async def test_class_wrapper_and_mapping_access(db):
    from backend.services.cross_day_context_recall import CrossDayContextRecallService

    service = CrossDayContextRecallService(db)
    snapshot = service.read([SESSION_ID], limit=5)
    assert snapshot["records"] == ()
    assert snapshot.get("data_returned") is False
    assert snapshot.to_dict()["data_returned"] is False
    assert "profile_task_boundary_unauthenticated" in snapshot["ambiguity_reasons"]


@pytest.mark.asyncio
async def test_session_ids_are_not_echoed(db):
    await seed_data(db)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert snapshot.requested_session_ids == ()
    assert snapshot.observed_sessions == ()
    assert snapshot.trusted_session_ids == ()
    assert snapshot.excluded_session_ids == ()


@pytest.mark.asyncio
async def test_immutable_snapshot(db):
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    with pytest.raises(Exception):
        snapshot.records = ("x",)
    with pytest.raises(Exception):
        snapshot.data_returned = True


@pytest.mark.asyncio
async def test_purge_and_retention_do_not_change_fail_closed(db):
    await seed_data(db)
    purge_expired(db)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert snapshot.records == ()
    assert snapshot.data_returned is False


def test_primary_module_only_no_alias_modules():
    aliased = [
        "context_recall",
        "cross_day_recall",
        "cross_day_context",
        "cross_day_context_recall_service",
    ]
    for name in aliased:
        with pytest.raises(ModuleNotFoundError):
            importlib.import_module(f"backend.services.{name}")


def test_no_raw_db_query_proof(db, monkeypatch):
    calls = {"count": 0}

    def _query(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("database query must not be called")

    monkeypatch.setattr(db, "query", _query)
    snapshot = read_cross_day_context_recall(db, [SESSION_ID])
    assert calls["count"] == 0
    assert snapshot.records == ()
    assert snapshot.data_returned is False
    assert snapshot.access_allowed is False
    assert snapshot.fail_closed is True
