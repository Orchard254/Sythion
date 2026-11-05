"""Offline file-backed tests for the canonical-source drift diagnostic."""

import os
import tempfile
import threading
import time

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    canonical_exchange_input_digest,
)
from backend.services.canonical_source_drift_diagnostic import (
    IndexCoverageError,
    CanonicalSourceDriftDiagnosticResult,
    read_session_canonical_source_drift_diagnostic,
)


@pytest.fixture()
def file_db(tmp_path):
    db_path = tmp_path / "canonical_drift.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    with engine.begin() as c:
        c.execute(text("PRAGMA journal_mode=WAL"))
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session, str(db_path), engine
    finally:
        session.close()
        engine.dispose()


def _seed_complete(session, sid, digest=None):
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="exchange-1", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    if digest is None:
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
        "backend.services.canonical_source_drift_diagnostic",
    ):
        saved[name] = sys.modules.pop(name, None)
    try:
        import backend.services.canonical_source_drift_diagnostic  # noqa: F401
        assert "backend.models.database" not in sys.modules
        assert "models.database" not in sys.modules
    finally:
        for name, value in saved.items():
            if value is not None:
                sys.modules[name] = value


def test_match_on_clean_state(file_db):
    session, db_path, engine = file_db
    sid = "session-match"
    digest = _seed_complete(session, sid)
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
    assert isinstance(result, CanonicalSourceDriftDiagnosticResult)
    assert result.snapshot_validated is True
    assert result.available_count == 1
    assert result.canonical_source_digest_match is True
    assert result.canonical_source_digest_mismatch is False
    assert result.entries[0].canonical_source_digest_match is True
    assert result.source_freshness_verified is False
    assert result.authoritative is False


def test_drift_detected_after_text_mutation(file_db):
    session, db_path, engine = file_db
    sid = "session-drift"
    _seed_complete(session, sid)
    exchange = session.query(ConversationExchange).one()
    exchange.user_text = "mutated"
    session.commit()
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
    assert result.canonical_source_digest_match is False
    assert result.canonical_source_digest_mismatch is True
    assert result.entries[0].canonical_source_digest_match is False


def test_unindexed_exchange_fails_closed(file_db):
    session, db_path, engine = file_db
    sid = "session-unindexed"
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="exchange-1", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
    assert result.unindexed_count == 1
    assert result.canonical_source_digest_match is False
    assert result.canonical_source_digest_mismatch is True


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
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
    assert result.insufficient_count == 1
    assert result.canonical_source_digest_match is False


def test_concurrent_writer_snapshot(file_db):
    session, db_path, engine = file_db
    sid = "session-concurrent"
    _seed_complete(session, sid)
    eng2 = create_engine(f"sqlite:///{db_path}", future=True)
    def writer():
        time.sleep(0.2)
        s2 = sessionmaker(bind=eng2, autoflush=False, future=True)()
        s2.add(ConversationTurn(id=99, session_id=sid, role="user", summary="u2"))
        s2.add(ConversationExchange(id="exchange-2", session_id=sid, user_text="u2", assistant_text="a2"))
        s2.commit()
        s2.close()
    th = threading.Thread(target=writer)
    th.start()
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=100000)
    th.join()
    assert result.available_count == 1
    eng2.dispose()


def test_caller_pending_rows_preserved_and_rejected(file_db):
    session, db_path, engine = file_db
    sid = "session-caller"
    set_current_session_id(sid)
    # Leave an uncommitted pending row in the caller session.
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    assert session.new
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
    # Fail closed due to ambiguous/unproven transaction ownership.
    assert result.snapshot_validated is False
    # Caller row preserved (not flushed/committed/deleted).
    assert session.new


def test_file_path_input(file_db):
    session, db_path, engine = file_db
    sid = "session-path"
    # Build a complete DB then reopen via path.
    _seed_complete(session, sid)
    session.close()
    engine.dispose()
    result = read_session_canonical_source_drift_diagnostic(db_path, sid, max_payload_bytes=8192)
    assert result.available_count == 1
    assert result.canonical_source_digest_match is True
    assert result.snapshot_validated is True


def test_caller_owned_transaction_does_not_claim_owner_verification(file_db):
    session, db_path, engine = file_db
    sid = "session-caller-owned-flag"
    _seed_complete(session, sid)
    # Caller retains clean, open transaction. Diagnostic must use that snapshot
    # without claiming it independently verified ownership of it.
    connection = session.connection()
    connection.exec_driver_sql("BEGIN")
    try:
        result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192)
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
        read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=1)

def test_enumeration_limit_incomplete(file_db):
    session, db_path, engine = file_db
    sid = "session-enum"
    set_current_session_id(sid)
    # Two exchanges.
    for i in (1, 2):
        session.add(ConversationTurn(id=10 + i, session_id=sid, role="user", summary="u"))
        session.add(ConversationTurn(id=20 + i, session_id=sid, role="assistant", summary="a"))
        session.add(ConversationExchange(id=f"exchange-{i}", session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()
    result = read_session_canonical_source_drift_diagnostic(session, sid, max_payload_bytes=8192, max_examined_exchanges=1)
    assert result.reason in ("enumeration_limit_exceeded", "enumeration_incomplete", "payload_budget_exceeded")
    assert result.coverage_complete is False
    assert result.canonical_source_digest_match is False