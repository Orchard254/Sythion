"""Offline file-backed tests for the partial discussion recall snapshot."""

import tempfile
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    INDEX_STATUS_PENDING,
    canonical_exchange_input_digest,
)
from backend.services.partial_discussion_recall_snapshot import (
    IndexCoverageError,
    PartialDiscussionRecallSnapshotResult,
    read_session_partial_discussion_recall_snapshot,
)


@pytest.fixture()
def file_db(tmp_path):
    db_path = tmp_path / "partial_recall.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as c:
        c.execute(text("PRAGMA journal_mode=WAL"))
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session, str(db_path), engine
    finally:
        session.close()
        engine.dispose()


def _seed_complete(session, sid):
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="exchange-1", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest=digest, status=INDEX_STATUS_PENDING)
    mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
    session.commit()
    return digest


def test_import_does_not_register_persistence():
    import sys

    saved = {}
    for name in (
        "backend.models.database",
        "models.database",
        "backend.services.partial_discussion_recall_snapshot",
    ):
        saved[name] = sys.modules.pop(name, None)
    try:
        import backend.services.partial_discussion_recall_snapshot  # noqa: F401

        assert "backend.models.database" not in sys.modules
        assert "models.database" not in sys.modules
    finally:
        for name, value in saved.items():
            if value is not None:
                sys.modules[name] = value


def test_match_on_clean_state(file_db):
    session, db_path, engine = file_db
    sid = "session-match"
    _seed_complete(session, sid)
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert isinstance(result, PartialDiscussionRecallSnapshotResult)
    assert result.snapshot_validated is True
    assert result.available_count == 1
    assert result.canonical_source_digest_match is True
    assert result.canonical_source_digest_mismatch is False
    assert result.recall_eligible is True
    assert result.raw_discovery_complete is True
    assert result.source_freshness_verified is False
    assert result.model_input_freshness_verified is False
    assert result.provider_freshness_verified is False
    assert result.authoritative is False
    assert result.raw_discovery is not None


def test_drift_detected_after_text_mutation(file_db):
    session, db_path, engine = file_db
    sid = "session-drift"
    _seed_complete(session, sid)
    exchange = session.query(ConversationExchange).one()
    exchange.user_text = "mutated"
    session.commit()
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.recall_eligible is False
    assert result.canonical_source_digest_match is False
    assert result.canonical_source_digest_mismatch is True


def test_unindexed_exchange_fails_closed(file_db):
    session, db_path, engine = file_db
    sid = "session-unindexed"
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="exchange-1", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.unindexed_count == 1
    assert result.recall_eligible is False
    assert result.canonical_source_digest_match is False


def test_insufficient_status_fails_closed(file_db):
    session, db_path, engine = file_db
    sid = "session-insufficient"
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="exchange-1", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest=digest, status=INDEX_STATUS_PENDING)
    mgr.transition(row.idempotency_key, INDEX_STATUS_INSUFFICIENT, commit=True)
    session.commit()
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.insufficient_count == 1
    assert result.recall_eligible is False


def test_caller_pending_rows_preserved_and_rejected(file_db):
    session, db_path, engine = file_db
    sid = "session-caller"
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    assert session.new
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.snapshot_validated is False
    assert session.new


def test_file_path_input(file_db):
    session, db_path, engine = file_db
    sid = "session-path"
    _seed_complete(session, sid)
    session.close()
    engine.dispose()
    result = read_session_partial_discussion_recall_snapshot(db_path, sid, max_payload_bytes=8192)
    assert result.available_count == 1
    assert result.recall_eligible is True
    assert result.snapshot_validated is True


def test_caller_owned_transaction_does_not_claim_owner_verification(file_db):
    session, db_path, engine = file_db
    sid = "session-caller-owned-flag"
    _seed_complete(session, sid)
    connection = session.connection()
    connection.exec_driver_sql("BEGIN")
    try:
        result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    finally:
        connection.exec_driver_sql("COMMIT")
    assert result.snapshot_validated is True
    assert result.caller_owned_transaction is True
    assert result.transaction_owner_verified is False


def test_too_small_budget_raises(file_db):
    session, db_path, engine = file_db
    sid = "session-small"
    _seed_complete(session, sid)
    with pytest.raises(IndexCoverageError):
        read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=1)


def test_freshness_and_model_binding_unverified(file_db):
    session, db_path, engine = file_db
    sid = "session-freshness"
    _seed_complete(session, sid)
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.source_freshness_verified is False
    assert result.model_input_freshness_verified is False
    assert result.provider_freshness_verified is False
    pass
import threading
import time


def test_forced_diagnostic_exception_fail_closed(file_db, monkeypatch):
    session, db_path, engine = file_db
    sid = "session-exc"
    _seed_complete(session, sid)
    import backend.services.canonical_source_drift_diagnostic as canonical_mod
    import backend.services.partial_discussion_recall_snapshot as mod

    def boom(*args, **kwargs):
        raise RuntimeError("injected diagnostic failure")

    monkeypatch.setattr(canonical_mod, "read_session_canonical_source_drift_diagnostic", boom)
    monkeypatch.setattr(mod, "read_session_canonical_source_drift_diagnostic", boom)
    result = mod.read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    assert result.recall_eligible is False
    assert result.snapshot_validated is False
    assert result.diagnostic_reason == "diagnostic_failed:RuntimeError"
    assert session is not None


def test_caller_owned_transaction_preserved(file_db):
    session, db_path, engine = file_db
    sid = "session-caller-owned"
    _seed_complete(session, sid)
    session.begin()
    try:
        result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=8192)
    finally:
        session.rollback()
    assert result.snapshot_validated is True
    assert result.caller_owned_transaction is True
    assert result.transaction_owner_verified is False


def test_concurrent_writer_does_not_change_snapshot(file_db):
    session, db_path, engine = file_db
    sid = "session-concurrent"
    _seed_complete(session, sid)
    eng2 = create_engine(f"sqlite:///{db_path}", future=True)
    def writer():
        time.sleep(0.2)
        s2 = sessionmaker(bind=eng2, autoflush=False, future=True)()
        set_current_session_id(sid)
        s2.add(ConversationTurn(id=99, session_id=sid, role="user", summary="u2"))
        s2.add(ConversationTurn(id=100, session_id=sid, role="assistant", summary="a2"))
        s2.add(ConversationExchange(id="exchange-2", session_id=sid, user_text="u2", assistant_text="a2"))
        s2.commit()
        s2.close()
    th = threading.Thread(target=writer)
    th.start()
    result = read_session_partial_discussion_recall_snapshot(session, sid, max_payload_bytes=100000)
    th.join()
    assert result.available_count == 1
    eng2.dispose()
