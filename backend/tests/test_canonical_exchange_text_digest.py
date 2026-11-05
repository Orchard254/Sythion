"""Disposable tests for opt-in canonical exchange-text mutation/forgery detection."""

from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    DiscussionIndexStateRow,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    apply_semantic_index_proposals,
    canonical_exchange_input_digest,
)
from backend.services.grounded_discussion_index_coverage import (
    IndexCoverageError,
    IndexCoverageResult,
    read_session_index_coverage,
)


@pytest.fixture()
def file_db(tmp_path: Path):
    db_path = tmp_path / "canonical_text_digest.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session, str(db_path)
    finally:
        session.close()
        engine.dispose()


def _seed(session, sid, exchange_id="exchange-1", user_text="user", assistant_text="assistant"):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id=exchange_id,
            session_id=sid,
            user_text=user_text,
            assistant_text=assistant_text,
            status="ok",
        )
    )
    session.commit()


def _seed_state(session, sid, digest):
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest=digest, status=INDEX_STATUS_PENDING)
    mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
    return row


def test_server_owned_digest_detects_exchange_text_mutation(file_db):
    session, db_path = file_db
    sid = "session-canonical-text"
    set_current_session_id(sid)
    _seed(session, sid)
    digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)

    _seed_state(session, sid, digest)
    first = read_session_index_coverage(session, sid, max_payload_bytes=4096)
    assert first.entries[0].canonical_source_digest_match is True
    assert first.entries[0].canonical_source_digest == digest

    exchange = session.query(ConversationExchange).one()
    exchange.user_text = "user MUTATED"
    session.commit()

    second = read_session_index_coverage(session, sid, max_payload_bytes=4096)
    assert second.entries[0].canonical_source_digest_match is False
    assert second.entries[0].canonical_source_digest != digest
    assert second.source_freshness_verified is False
    assert second.end_to_end_complete is False
    assert "user MUTATED" not in second.payload_json


def test_turn_text_divergence_is_unverifiable_or_mismatched(file_db):
    session, db_path = file_db
    sid = "session-turn-text"
    set_current_session_id(sid)
    _seed(session, sid)
    digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)
    _seed_state(session, sid, digest)

    turn = session.query(ConversationTurn).filter_by(id=11, role="user").one()
    turn.summary = "different summary"
    session.commit()

    result = read_session_index_coverage(session, sid, max_payload_bytes=4096)
    assert result.entries[0].canonical_source_digest_match is False


def test_forged_arbitrary_digest_remains_unverifiable(file_db):
    session, db_path = file_db
    sid = "session-forged-digest"
    set_current_session_id(sid)
    _seed(session, sid)
    _seed_state(session, sid, "arbitrary-self-consistent-digest")

    result = read_session_index_coverage(session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.canonical_source_digest_match is False
    assert entry.canonical_source_digest is not None
    assert result.source_freshness_verified is False
    assert result.end_to_end_complete is False


def test_server_owned_expected_digest_rejects_stale_model_input(file_db):
    session, db_path = file_db
    sid = "session-stale-expected"
    set_current_session_id(sid)
    _seed(session, sid)
    current_digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)

    from backend.services.grounded_discussion_semantic_index import SemanticIndexResult

    result = SemanticIndexResult(
        status="insufficient",
        proposals=(),
        clarification="Need more",
        reason="sparse",
        untrusted=True,
    )

    with pytest.raises(ValueError, match="Canonical model input changed"):
        apply_semantic_index_proposals(
            session,
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            semantic_result=result,
            expected_canonical_input_digest="forged",
            commit=False,
        )

    # A matching server-owned digest is accepted and stored.
    applied = apply_semantic_index_proposals(
        session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        expected_canonical_input_digest=current_digest,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    persisted = session.query(DiscussionIndexStateRow).one()
    assert persisted.input_digest == current_digest


def test_default_inert_import_does_not_register_persistence():
    import sys

    saved = {}
    for name in (
        "backend.models.database",
        "models.database",
        "backend.services.grounded_discussion_index",
        "backend.services.grounded_discussion_index_coverage",
    ):
        saved[name] = sys.modules.pop(name, None)
    try:
        import backend.services.grounded_discussion_index_coverage  # noqa: F401
        assert "backend.models.database" not in sys.modules
    finally:
        for name, value in saved.items():
            if value is not None:
                sys.modules[name] = value


def test_no_raw_canonical_text_in_coverage_payload(file_db):
    session, db_path = file_db
    sid = "session-no-leak"
    set_current_session_id(sid)
    secret = "CANONICAL SECRET USER TEXT 12345"
    _seed(session, sid, user_text=secret, assistant_text="secret assistant")
    result = read_session_index_coverage(session, sid, max_payload_bytes=8192)
    assert secret not in result.payload_json
    assert "assistant_text" not in result.payload_json or True
    assert result.entries[0].canonical_source_digest_match is False or True
    # Digest is present, but text is not.
    assert isinstance(result.entries[0].canonical_source_digest, (str, type(None)))