"""Offline tests for opt-in indexing coverage of canonical exchanges."""

import hashlib
import json
import uuid
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    DiscussionIndexStateRow,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_IN_PROGRESS,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    INDEX_STATUS_UNCERTAIN,
)
from backend.services.grounded_discussion_index_coverage import (
    IndexCoverageError,
    IndexCoverageResult,
    IndexCoverageEntry,
    read_session_index_coverage,
)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def setup_turns(session, sid, user_id, assistant_id):
    session.add(ConversationTurn(id=user_id, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=assistant_id, session_id=sid, role="assistant", summary="a"))


def setup_exchange(session, sid, exchange_id="exchange-1", user_id=11, assistant_id=12, created_at=None):
    setup_turns(session, sid, user_id, assistant_id)
    exchange = ConversationExchange(
        id=exchange_id, session_id=sid, user_text="user", assistant_text="assistant",
        created_at=created_at or datetime.utcnow(),
    )
    session.add(exchange)
    session.commit()


def state_key(sid, eid, ut, at, digest):
    payload = {"session_id": sid, "exchange_id": eid, "user_turn_id": ut,
               "assistant_turn_id": at, "input_digest": digest}
    text = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return "idx-" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def make_state(session, sid, eid, ut, at, status=INDEX_STATUS_COMPLETE, digest="digest-1"):
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, eid, ut, at, input_digest=digest, status=INDEX_STATUS_PENDING)
    if status != INDEX_STATUS_PENDING:
        mgr.transition(row.idempotency_key, status, commit=False)
    session.commit()
    return row


def test_import_does_not_register_persistence():
    import sys

    saved = {}
    for name in ("backend.models.database", "models.database", "backend.services.grounded_discussion_index_coverage"):
        saved[name] = sys.modules.pop(name, None)
    try:
        import backend.services.grounded_discussion_index_coverage  # noqa: F401
        assert "backend.models.database" not in sys.modules
        assert "models.database" not in sys.modules
    finally:
        for name, value in saved.items():
            if value is not None:
                sys.modules[name] = value


def test_empty_session_complete(db_session):
    sid = "session-empty"
    set_current_session_id(sid)
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.available_count == 0
    assert result.returned_count == 0
    assert result.coverage_complete is True
    assert result.reason == "complete"
    assert "user_text" not in result.payload_json
    assert json.loads(result.payload_json)["entries"] == []


def test_keyword_only_budget_required(db_session):
    sid = "session-empty"
    with pytest.raises(IndexCoverageError):
        read_session_index_coverage(db_session, sid, max_payload_bytes=100)  # type: ignore[arg-type]
    with pytest.raises(IndexCoverageError):
        read_session_index_coverage(db_session, sid, max_payload_bytes=None)


def test_foreign_session_isolation(db_session):
    sid = "session-a"
    other = "session-b"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-a", 11, 12)
    setup_exchange(db_session, other, "ex-b", 21, 22)
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert [e.exchange_id for e in result.entries] == ["ex-a"]
    assert result.available_count == 1


def test_missing_state_visible_unindexed(db_session):
    sid = "session-missing"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1")
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.coverage_complete is False
    assert result.unindexed_count == 1
    entry = result.entries[0]
    assert entry.state_present is False
    assert entry.index_status == "unindexed"
    assert "no_index_state" in entry.reasons
    assert entry.is_complete is False
    assert "user_text" not in entry.to_dict()


def test_every_state_status(db_session):
    sid = "session-states"
    set_current_session_id(sid)
    statuses = [INDEX_STATUS_PENDING, INDEX_STATUS_IN_PROGRESS, INDEX_STATUS_COMPLETE,
                INDEX_STATUS_INSUFFICIENT, INDEX_STATUS_UNCERTAIN]
    for idx, status in enumerate(statuses, start=1):
        eid = f"ex-{idx}"
        setup_exchange(db_session, sid, eid, 100 + idx * 2, 100 + idx * 2 + 1)
    # create states after all exchanges
    for idx, status in enumerate(statuses, start=1):
        eid = f"ex-{idx}"
        ut = 100 + idx * 2
        at = 100 + idx * 2 + 1
        digest = f"digest-{idx}"
        expected_status = status
        if status == INDEX_STATUS_COMPLETE:
            make_state(db_session, sid, eid, ut, at, INDEX_STATUS_COMPLETE, digest)
        else:
            # create pending then transition terminal non-complete where appropriate
            row = make_state(db_session, sid, eid, ut, at, INDEX_STATUS_PENDING, digest)
            if status != INDEX_STATUS_PENDING:
                mgr = DiscussionIndexManager(db_session, db=db_session)
                mgr.transition(row.idempotency_key, status, commit=True)
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=10000)
    assert result.available_count == 5
    by_status = {e.exchange_id: e.index_status for e in result.entries}
    for idx, status in enumerate(statuses, start=1):
        assert by_status[f"ex-{idx}"] == status
    # pending/completion mix means incomplete
    assert result.coverage_complete is False
    assert result.pending_count == 1
    assert result.in_progress_count == 1
    assert result.insufficient_count == 1
    assert result.uncertain_count == 1
    assert result.indexed_count == 1
    assert result.mismatched_count == 0


def test_complete_state_anchors_digest_match(db_session):
    sid = "session-complete"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    digest = "digest-complete"
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, digest)
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.coverage_complete is True
    assert result.indexed_count == 1
    entry = result.entries[0]
    assert entry.is_complete is True
    assert entry.anchors_match is True
    assert entry.digest_match is True
    assert entry.reasons == ()


def test_mismatched_turn_anchor(db_session):
    sid = "session-anchor"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    digest = "digest-mismatch"
    # user turn id absent in canonical turns
    make_state(db_session, sid, "ex-1", 999, 12, INDEX_STATUS_COMPLETE, digest)
    # Force digest binding mismatch? Digest expected for 999; adjust state input digest to original turn? 
    # Simulate mismatched anchors by editing state row.
    state = db_session.query(DiscussionIndexStateRow).one()
    state.user_turn_id = 999
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.is_complete is False
    assert entry.anchors_match is False
    assert "user_turn_anchor_missing" in entry.reasons
    assert result.coverage_complete is False


def test_mismatched_digest_binding(db_session):
    sid = "session-digest"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    digest = "digest-original"
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, digest)
    state = db_session.query(DiscussionIndexStateRow).one()
    state.input_digest = "digest-changed"
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.is_complete is False
    assert entry.digest_match is False
    assert "idempotency_digest_binding_mismatch" in entry.reasons or "missing_input_digest" in entry.reasons
    assert result.coverage_complete is False


def test_idempotency_key_mismatch_fails(db_session):
    sid = "session-key"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    digest = "digest-key"
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, digest)
    state = db_session.query(DiscussionIndexStateRow).one()
    state.idempotency_key = "idx-tampered"
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.is_complete is False
    assert entry.digest_match is False
    assert result.coverage_complete is False


def test_correction_pending_blocks_completion(db_session):
    sid = "session-correction"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-old", 7, 8)
    setup_exchange(db_session, sid, "ex-new", 11, 12)
    digest_old = "old"
    make_state(db_session, sid, "ex-old", 7, 8, INDEX_STATUS_COMPLETE, digest_old)
    # new exchange unindexed -> incomplete
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.coverage_complete is False
    assert result.unindexed_count == 1
    assert "ex-new" in result.unindexed_exchange_ids


def test_more_than_twenty_history(db_session):
    sid = "session-history"
    set_current_session_id(sid)
    for i in range(25):
        uid = 1000 + i * 2
        aid = 1000 + i * 2 + 1
        setup_exchange(db_session, sid, f"ex-{i}", uid, aid)
        make_state(db_session, sid, f"ex-{i}", uid, aid, INDEX_STATUS_COMPLETE, f"digest-{i}")
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=100000)
    assert result.available_count == 25
    assert result.returned_count == 25
    assert result.coverage_complete is True
    assert len(result.entries) == 25


def test_byte_overflow_incomplete(db_session):
    sid = "session-overflow"
    set_current_session_id(sid)
    for i in range(5):
        uid = 2000 + i * 2
        aid = 2000 + i * 2 + 1
        setup_exchange(db_session, sid, f"ex-{i}", uid, aid)
        make_state(db_session, sid, f"ex-{i}", uid, aid, INDEX_STATUS_COMPLETE, f"digest-{i}")
    # tiny budget
    with pytest.raises(IndexCoverageError):
        read_session_index_coverage(db_session, sid, max_payload_bytes=10)
    # slightly larger but still too small for all entries -> overflow result
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=370)
    assert result.coverage_complete is False
    assert result.reason == "payload_budget_exceeded"
    assert result.returned_count == 0
    assert result.omitted_count == 5
    assert result.entries == ()


def test_rollback_uncommitted_state_not_complete(db_session):
    sid = "session-rollback"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    # uncommitted state: create row but rollback
    mgr = DiscussionIndexManager(db_session, db=db_session)
    mgr.receive(sid, "ex-1", 11, 12, input_digest="digest-rollback", status=INDEX_STATUS_COMPLETE)
    db_session.rollback()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.unindexed_count == 1
    assert result.coverage_complete is False


def test_rollback_uncommitted_complete_state(db_session):
    sid = "session-rollback2"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-ok")
    # tamper but rollback should restore complete
    state = db_session.query(DiscussionIndexStateRow).one()
    state.status = INDEX_STATUS_UNCERTAIN
    db_session.flush()
    db_session.rollback()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.coverage_complete is True


def test_manager_wrapper(db_session):
    from backend.services.grounded_discussion_index_coverage import IndexCoverageManager
    sid = "session-manager"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-mgr")
    mgr = IndexCoverageManager(db_session, db=db_session)
    result = mgr.scan(sid, max_payload_bytes=4096)
    assert result.coverage_complete is True
    assert mgr.db is db_session


def test_no_plaintext_history_in_payload(db_session):
    sid = "session-plaintext"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    d = result.to_dict()
    text = json.dumps(d, ensure_ascii=False)
    assert "user_text" not in text
    assert "assistant_text" not in text
    assert "summary" not in text


def test_duplicate_state_blocks_completion(db_session):
    sid = "session-dup"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-dup")
    # Insert duplicate state row manually with new turns
    db_session.add(ConversationTurn(id=13, session_id=sid, role="user", summary="u2"))
    db_session.add(ConversationTurn(id=14, session_id=sid, role="assistant", summary="a2"))
    db_session.flush()
    import uuid
    key2 = state_key(sid, "ex-1", 13, 14, "digest-dup2")
    db_session.add(DiscussionIndexStateRow(
        idempotency_key=key2,
        session_id=sid,
        exchange_id="ex-1",
        user_turn_id=13,
        assistant_turn_id=14,
        input_digest="digest-dup2",
        status=INDEX_STATUS_COMPLETE,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.coverage_complete is False
    assert "duplicate_index_state" in result.entries[0].reasons


def test_unknown_index_status_blocks(db_session):
    sid = "session-unknown"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-unk")
    state = db_session.query(DiscussionIndexStateRow).one()
    state.status = "weird"
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.is_complete is False
    assert "unknown_index_status" in entry.reasons
    assert result.coverage_complete is False


def test_canonical_source_changed_not_end_to_end_complete(db_session):
    sid = "session-source-change"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-source")
    # Simulate an unseen later correction to durable canonical source data,
    # while durable index state remains unchanged and self-consistent.
    exchange = db_session.query(ConversationExchange).one()
    exchange.user_text = "changed user text"
    exchange.assistant_text = "changed assistant text"
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    assert result.structural_state_complete is True
    assert result.coverage_complete is True
    assert result.source_freshness_verified is False
    assert result.end_to_end_complete is False


def test_self_consistent_arbitrary_digest_not_source_fresh(db_session):
    sid = "session-arbitrary-digest"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    digest = "arbitrary-self-consistent-digest"
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, digest)
    # Forged/self-consistent state remains structurally complete, but source
    # freshness is unknown without an authoritative verifier.
    state = db_session.query(DiscussionIndexStateRow).one()
    state.idempotency_key = state_key(sid, "ex-1", 11, 12, digest)
    state.input_digest = digest
    db_session.commit()
    result = read_session_index_coverage(db_session, sid, max_payload_bytes=4096)
    entry = result.entries[0]
    assert entry.is_complete is True
    assert entry.anchors_match is True
    assert entry.digest_match is True
    assert result.source_freshness_verified is False
    assert result.end_to_end_complete is False


def test_source_freshness_verifier_can_enable_end_to_end(db_session):
    sid = "session-verifier"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-verifier")
    result = read_session_index_coverage(
        db_session, sid, max_payload_bytes=4096,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert result.source_freshness_verified is True
    assert result.end_to_end_complete is True


def test_enumeration_bound_reports_incomplete(db_session):
    sid = "session-bound"
    set_current_session_id(sid)
    for i in range(5):
        uid = 3000 + i * 2
        aid = 3000 + i * 2 + 1
        setup_exchange(db_session, sid, f"ex-{i}", uid, aid)
        make_state(db_session, sid, f"ex-{i}", uid, aid, INDEX_STATUS_COMPLETE, f"digest-{i}")
    result = read_session_index_coverage(
        db_session, sid, max_payload_bytes=100000, max_examined_exchanges=2,
    )
    assert result.available_count == 3
    assert result.returned_count == 0
    assert result.reason == "enumeration_limit_exceeded"
    assert result.coverage_complete is False
    assert result.end_to_end_complete is False


def test_verifier_accepts_only_true_identity(db_session):
    sid = "session-verifier-identity"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-identity")
    for value in (1, "True", object(), 0, False, None):
        result = read_session_index_coverage(
            db_session, sid, max_payload_bytes=4096,
            source_freshness_verifier=lambda session, session_id, entries, states, value=value: value,
        )
        assert result.source_freshness_verified is False
        assert result.end_to_end_complete is False
    verified_result = read_session_index_coverage(
        db_session, sid, max_payload_bytes=4096,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert verified_result.source_freshness_verified is True
    assert verified_result.end_to_end_complete is True


def test_async_verifier_is_rejected_without_warning(db_session):
    import asyncio
    import warnings

    async def async_verifier(session, session_id, entries, states):
        return True

    sid = "session-async-verifier"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-async")
    with warnings.catch_warnings(record=True) as caught:
        warnings.simplefilter("always")
        result = read_session_index_coverage(
            db_session, sid, max_payload_bytes=4096,
            source_freshness_verifier=async_verifier,
        )
    assert result.source_freshness_verified is False
    assert result.end_to_end_complete is False
    assert not any("coroutine" in str(w.message).lower() for w in caught)


def test_verifier_ordinary_exception_fails_closed(db_session):
    sid = "session-verifier-exception"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-exc")

    def failing_verifier(session, session_id, entries, states):
        raise ValueError("provider unavailable")

    result = read_session_index_coverage(
        db_session, sid, max_payload_bytes=4096,
        source_freshness_verifier=failing_verifier,
    )
    assert result.source_freshness_verified is False
    assert result.end_to_end_complete is False


def test_verifier_cancellation_propagates(db_session):
    import asyncio

    sid = "session-verifier-cancel"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-1", 11, 12)
    make_state(db_session, sid, "ex-1", 11, 12, INDEX_STATUS_COMPLETE, "digest-cancel")

    def cancelling_verifier(session, session_id, entries, states):
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        read_session_index_coverage(
            db_session, sid, max_payload_bytes=4096,
            source_freshness_verifier=cancelling_verifier,
        )
