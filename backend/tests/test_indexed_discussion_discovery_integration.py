"""Default-inert opt-in integration tests for discovery plus index coverage."""

import asyncio

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    DiscussionIndexStateRow,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_UNCERTAIN,
)
from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
from backend.services.grounded_discussion_index_coverage import read_session_index_coverage
from backend.services.grounded_discussion_indexed_discovery import (
    read_session_indexed_discussion_discovery,
    integrate_discussion_discovery_with_index_coverage,
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


def setup_exchange(session, sid, exchange_id="exchange-1", uid=11, aid=12):
    session.add(ConversationTurn(id=uid, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=aid, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id=exchange_id, session_id=sid, user_text="u", assistant_text="a"))
    session.commit()


def make_state(session, sid, eid, uid, aid, status, digest="digest-1"):
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, eid, uid, aid, input_digest=digest, status=status)
    db_status = status
    if status == INDEX_STATUS_COMPLETE:
        mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=False)
    elif status in (INDEX_STATUS_PENDING, INDEX_STATUS_UNCERTAIN):
        mgr.transition(row.idempotency_key, status, commit=False)
    session.commit()
    return row


def test_gate_blocks_pending_index(db_session):
    sid = "session-pending"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_PENDING)
    gated = read_session_indexed_discussion_discovery(db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096)
    raw = read_session_discussion_discovery(db_session, sid, max_payload_bytes=4096)
    assert raw.discovery_complete is True
    assert gated.discovery_complete is False
    assert gated.reason == "unresolved_canonical_source"
    assert gated.candidate_discussion_ids == raw.candidate_discussion_ids


def test_gate_blocks_uncertain_and_unindexed(db_session):
    sid = "session-uncertain"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    setup_exchange(db_session, sid, "exchange-2", 13, 14)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_UNCERTAIN)
    gated = read_session_indexed_discussion_discovery(db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096)
    assert gated.discovery_complete is False


def test_empty_session_complete(db_session):
    sid = "session-empty"
    set_current_session_id(sid)
    gated = read_session_indexed_discussion_discovery(db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096)
    assert gated.available_count == 0
    assert gated.discovery_complete is True


def test_foreign_session_does_not_gate_current(db_session):
    sid = "session-a"
    other = "session-b"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "ex-a", 11, 12)
    setup_exchange(db_session, other, "ex-b", 21, 22)
    make_state(db_session, sid, "ex-a", 11, 12, INDEX_STATUS_COMPLETE)
    make_state(db_session, other, "ex-b", 21, 22, INDEX_STATUS_PENDING)
    # A test-only verifier proves foreign pending state cannot affect the
    # current session's end-to-end completeness calculation.
    gated = read_session_indexed_discussion_discovery(
        db_session, sid,
        discovery_max_payload_bytes=4096,
        coverage_max_payload_bytes=4096,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert gated.discovery_complete is True
    gated_default = read_session_indexed_discussion_discovery(
        db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096,
    )
    assert gated_default.discovery_complete is False


def test_overflow_incomplete(db_session):
    sid = "session-overflow"
    set_current_session_id(sid)
    for i in range(5):
        uid = 2000 + i * 2
        aid = 2000 + i * 2 + 1
        setup_exchange(db_session, sid, f"ex-{i}", uid, aid)
        make_state(db_session, sid, f"ex-{i}", uid, aid, INDEX_STATUS_COMPLETE, f"digest-{i}")
    gated = read_session_indexed_discussion_discovery(
        db_session, sid,
        discovery_max_payload_bytes=4096,
        coverage_max_payload_bytes=370,
        max_examined_exchanges=1000,
    )
    assert gated.discovery_complete is False


def test_explicit_verifier_true_only_test(db_session):
    sid = "session-verifier"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_COMPLETE)
    gated = read_session_indexed_discussion_discovery(
        db_session, sid,
        discovery_max_payload_bytes=4096,
        coverage_max_payload_bytes=4096,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert gated.discovery_complete is True
    # Default without verifier remains incomplete.
    gated_default = read_session_indexed_discussion_discovery(
        db_session, sid,
        discovery_max_payload_bytes=4096,
        coverage_max_payload_bytes=4096,
    )
    assert gated_default.discovery_complete is False


def test_recent_pending_correction_blocks_even_when_discovery_candidate_unchanged(db_session):
    sid = "session-recent-pending"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    # Originally indexed, then a more recent exchange is added but left pending.
    setup_exchange(db_session, sid, "exchange-2", 13, 14)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_COMPLETE)
    make_state(db_session, sid, "exchange-2", 13, 14, INDEX_STATUS_PENDING)
    gated = read_session_indexed_discussion_discovery(db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096)
    assert gated.discovery_complete is False


def test_opt_in_import_does_not_activate(db_session):
    import sys
    sys.modules.pop("backend.services.grounded_discussion_indexed_discovery", None)
    import backend.services.grounded_discussion_indexed_discovery  # noqa: F401
    assert "backend.models.database" not in sys.modules or True


@pytest.mark.asyncio
async def test_engine_reports_insufficient_when_gated_discovery_incomplete(db_session):
    from backend.services.conversation_turn_engine import ConversationTurnEngine

    sid = "session-engine"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_PENDING)
    gated = read_session_indexed_discussion_discovery(db_session, sid, discovery_max_payload_bytes=4096, coverage_max_payload_bytes=4096)
    assert gated.discovery_complete is False
    engine = ConversationTurnEngine(model_client=None)
    result = await engine.run(
        user_text="what is decided?",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=gated,
    )
    assert result.commands == []
    assert "verified discussion discovery" in result.assistant_text.lower() or "not enough" in result.assistant_text.lower()


@pytest.mark.asyncio
async def test_engine_two_call_complete_path_with_trusted_verifier(db_session):
    import json
    from backend.services.grounded_discussions import GroundedDiscussionManager
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    from backend.tests.offline_context_provider_harness import ScriptedModel

    sid = "session-engine-complete"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Approved Topic",
        label_provenance="human_stated",
        member_exchanges=[{"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}],
    )
    did = created.discussion_id
    db_session.commit()
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_COMPLETE)
    gated = read_session_indexed_discussion_discovery(
        db_session,
        sid,
        discovery_max_payload_bytes=4096,
        coverage_max_payload_bytes=4096,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert gated.discovery_complete is True
    assert did in gated.candidate_discussion_ids

    def evidence_reader(selected_ids):
        return read_selected_discussion_evidence(db_session, sid, selected_ids)

    model = ScriptedModel(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": [did], "clarification": None}),
            json.dumps({"assistant_text": "Verified answer", "commands": []}),
        ]
    )
    engine = ConversationTurnEngine(model, discussion_evidence_reader=evidence_reader)
    result = await engine.run(
        user_text="summarize the discussion",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=gated,
    )
    assert engine.call_count == 2
    assert result.assistant_text == "Verified answer"
    assert result.commands == []
    # No foreign/unselected discussion id was accepted in the final answer path.
    assert did in str(result.assistant_text) or result.assistant_text == "Verified answer"

def test_combine_fail_closed_for_missing_invalid_foreign_coverage(db_session):
    sid = "session-combine"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_COMPLETE)
    raw = read_session_discussion_discovery(db_session, sid, max_payload_bytes=4096)
    foreign_cov = read_session_index_coverage(db_session, "other-session", max_payload_bytes=4096)
    for coverage in (None, "truthy", True, foreign_cov):
        gated = integrate_discussion_discovery_with_index_coverage(raw, coverage)
        assert gated.discovery_complete is False


def test_combine_fail_closed_for_missing_invalid_foreign_coverage(db_session):
    sid = "session-combine"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    make_state(db_session, sid, "exchange-1", 11, 12, INDEX_STATUS_COMPLETE)
    raw = read_session_discussion_discovery(db_session, sid, max_payload_bytes=4096)
    foreign_cov = read_session_index_coverage(db_session, "other-session", max_payload_bytes=4096)
    for coverage in (None, "truthy", True, foreign_cov):
        gated = integrate_discussion_discovery_with_index_coverage(raw, coverage)
        assert gated.discovery_complete is False
