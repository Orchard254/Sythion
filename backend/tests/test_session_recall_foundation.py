"""Focused foundation tests for explicit-scoped multi-session recall."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, ConversationExchange
from backend.services.session_recall import (
    read_session_recall_candidates,
    read_session_recall_candidate,
)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'session-recall.db'}")
    Base.metadata.create_all(engine)
    try:
        yield sessionmaker(bind=engine)()
    finally:
        engine.dispose()


def test_explicit_scope_reads_only_requested_sessions(db):
    db.add_all([
        ConversationExchange(id="e1", session_id="session-a", user_text="A1", assistant_text="R1"),
        ConversationExchange(id="e2", session_id="session-b", user_text="B1", assistant_text="R2"),
        ConversationExchange(id="e3", session_id="session-c", user_text="C1", assistant_text="R3"),
    ])
    db.commit()

    snapshot = read_session_recall_candidates(db, ["session-a", "session-b"], limit=10)
    assert snapshot.session_ids == ("session-a", "session-b")
    assert {candidate.session_id for candidate in snapshot.candidates} == {"session-a", "session-b"}
    assert {candidate.exchange_id for candidate in snapshot.candidates} == {"e1", "e2"}
    assert snapshot.has_more is False
    assert snapshot.coverage == "retained_exchanges_in_explicit_sessions"


def test_scope_must_be_non_empty_and_exact(db):
    with pytest.raises(ValueError):
        read_session_recall_candidates(db, [])
    with pytest.raises(ValueError):
        read_session_recall_candidates(db, [""])
    db.add(ConversationExchange(id="e1", session_id="session-a", user_text="A", assistant_text="R"))
    db.commit()
    assert read_session_recall_candidate(db, "other-session", "e1") is None


def test_limit_is_bounded_and_reports_has_more(db):
    for index in range(3):
        db.add(ConversationExchange(
            id=f"e{index}",
            session_id="session-a",
            user_text=f"U{index}",
            assistant_text=f"R{index}",
        ))
    db.commit()

    snapshot = read_session_recall_candidates(db, ["session-a"], limit=2)
    assert len(snapshot.candidates) == 2
    assert snapshot.has_more is True


def test_reader_is_read_only_no_spill_or_commit(db, monkeypatch):
    db.add(ConversationExchange(id="e1", session_id="session-a", user_text="U", assistant_text="R"))
    db.flush()
    pending = ConversationExchange(id="pending", session_id="session-a", user_text="pending", assistant_text="pending")
    db.add(pending)
    original_user_text = "U"

    def forbidden(*args, **kwargs):
        raise AssertionError("reader must not manage caller transactions")

    monkeypatch.setattr(db, "flush", forbidden)
    monkeypatch.setattr(db, "commit", forbidden)
    monkeypatch.setattr(db, "rollback", forbidden)

    snapshot = read_session_recall_candidates(db, ["session-a"])
    assert snapshot.candidates[0].user_text == original_user_text
    assert pending in db.new


def test_invalid_limit_fails_closed(db):
    for limit in (0, -1, 51, True, "10", 1.5):
        with pytest.raises(ValueError):
            read_session_recall_candidates(db, ["session-a"], limit=limit)


def test_read_failure_propagates(db, monkeypatch):
    def unavailable(*args, **kwargs):
        raise RuntimeError("Database unavailable")

    monkeypatch.setattr(db, "query", unavailable)
    with pytest.raises(RuntimeError, match="Database unavailable"):
        read_session_recall_candidates(db, ["session-a"])


def test_session_recall_candidate_exact_lookup(db):
    db.add(ConversationExchange(id="e1", session_id="session-a", user_text="U", assistant_text="R"))
    db.commit()
    candidate = read_session_recall_candidate(db, "session-a", "e1")
    assert candidate is not None
    assert candidate.exchange_id == "e1"
    assert read_session_recall_candidate(db, "session-b", "e1") is None