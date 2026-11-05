"""Default-inert local end-to-end wiring over approved grounded seams.

All tests use disposable SQLite and fake providers only.  No production route,
scheduler, provider, migration, or protected integration is enabled.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
from backend.services.grounded_discussion_index import (
    apply_semantic_index_proposals,
    DiscussionIndexStateRow,
    INDEX_STATUS_INSUFFICIENT,
)
from backend.services.grounded_discussion_semantic_index import (
    SemanticDiscussionIndexer,
    SemanticIndexSnapshot,
)
from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
from backend.services.grounded_discussion_index_coverage import read_session_index_coverage
from backend.services.grounded_discussion_indexed_discovery import (
    read_session_indexed_discussion_discovery,
)
from backend.services.conversation_turn_engine import ConversationTurnEngine


class FakeModelClient:
    def __init__(self, payload):
        self.payload = payload
        self.calls = 0

    async def chat(self, messages, **kwargs):
        self.calls += 1
        return json.dumps(self.payload)


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
    session.add(ConversationTurn(id=uid, session_id=sid, role="user", summary=f"u {uid}"))
    session.add(ConversationTurn(id=aid, session_id=sid, role="assistant", summary=f"a {aid}"))
    session.add(ConversationExchange(id=exchange_id, session_id=sid, user_text="u", assistant_text="a"))
    session.commit()


def semantic_new_topic_model():
    return FakeModelClient({
        "status": "proposals",
        "proposals": [
            {
                "label": "New Topic",
                "session_id": "future-set",
                "target_exchange_id": "future-set",
                "target_user_turn_id": 1,
                "target_assistant_turn_id": 2,
            }
        ],
        "clarification": None,
        "reason": "one new topic",
    })


def test_semantic_proposal_indexes_new_session_and_discovery(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    model = semantic_new_topic_model()
    # Replace bindable fields in the scripted payload with exact caller-owned values.
    model.payload["proposals"][0]["session_id"] = sid
    model.payload["proposals"][0]["target_exchange_id"] = "exchange-1"
    model.payload["proposals"][0]["target_user_turn_id"] = 11
    model.payload["proposals"][0]["target_assistant_turn_id"] = 12
    indexer = SemanticDiscussionIndexer(model, budget=None, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        source_user_text="u",
        source_assistant_text="a",
        candidate_discussion_ids=(),
        candidate_labels={},
    )
    result = asyncio_run(indexer.index(snapshot))
    assert model.calls == 1
    assert result.status == "proposals"

    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
        input_digest="semantic-digest-1",
    )
    assert applied["status"] == "complete"  # indexing state, not semantic authority
    assert applied["applied_count"] == 1
    assert db_session.query(DiscussionIndexStateRow).one().status == "complete"

    # Discovery now contains the exact session's grounded discussion.
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=100000)
    assert discovery.available_count == 1
    assert discovery.discovery_complete is True
    assert discovery.candidate_discussion_ids

    # Separate-budget semantic run did not reuse engine budget semantics; the same
    # model was invoked exactly once.
    assert model.calls == 1


def test_irrelevant_foreign_session_is_excluded_end_to_end(db_session):
    sid = "session-e2e"
    other = "foreign-session"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    setup_exchange(db_session, other, "foreign-exchange", 21, 22)
    model = semantic_new_topic_model()
    model.payload["proposals"][0]["session_id"] = sid
    model.payload["proposals"][0]["target_exchange_id"] = "exchange-1"
    model.payload["proposals"][0]["target_user_turn_id"] = 11
    model.payload["proposals"][0]["target_assistant_turn_id"] = 12
    indexer = SemanticDiscussionIndexer(model, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(sid, "exchange-1", 11, 12, source_user_text="u", source_assistant_text="a", candidate_discussion_ids=())
    result = asyncio_run(indexer.index(snapshot))
    apply_semantic_index_proposals(
        db_session, session_id=sid, exchange_id="exchange-1", user_turn_id=11, assistant_turn_id=12,
        semantic_result=result, commit=True, input_digest="semantic-digest-foreign",
    )
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=100000)
    foreign = read_session_discussion_discovery(db_session, other, max_payload_bytes=100000)
    assert foreign.available_count == 0
    assert discovery.available_count == 1
    # No foreign discussion or exchange became part of current discovery/evidence.
    assert "foreign-exchange" not in str(discovery.to_dict())


def test_index_coverage_end_to_end_under_default_untrusted_verifier(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    model = semantic_new_topic_model()
    model.payload["proposals"][0]["session_id"] = sid
    model.payload["proposals"][0]["target_exchange_id"] = "exchange-1"
    model.payload["proposals"][0]["target_user_turn_id"] = 11
    model.payload["proposals"][0]["target_assistant_turn_id"] = 12
    indexer = SemanticDiscussionIndexer(model, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(sid, "exchange-1", 11, 12, source_user_text="u", source_assistant_text="a", candidate_discussion_ids=())
    result = asyncio_run(indexer.index(snapshot))
    apply_semantic_index_proposals(
        db_session, session_id=sid, exchange_id="exchange-1", user_turn_id=11, assistant_turn_id=12,
        semantic_result=result, commit=True, input_digest="semantic-digest-cov",
    )
    # Default coverage remains incomplete for a nonempty session: no invented trusted verifier.
    coverage = read_session_index_coverage(db_session, sid, max_payload_bytes=100000)
    assert coverage.structural_state_complete is True
    assert coverage.end_to_end_complete is False
    # Explicitly injected verifier is test-only and clearly labelled; not runtime authority.
    gated = read_session_indexed_discussion_discovery(
        db_session, sid, discovery_max_payload_bytes=100000, coverage_max_payload_bytes=100000,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert gated.discovery_complete is True


def test_pending_later_correction_blocks_stale_answer(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    setup_exchange(db_session, sid, "exchange-2", 13, 14)
    model = semantic_new_topic_model()
    model.payload["proposals"][0]["session_id"] = sid
    model.payload["proposals"][0]["target_exchange_id"] = "exchange-1"
    model.payload["proposals"][0]["target_user_turn_id"] = 11
    model.payload["proposals"][0]["target_assistant_turn_id"] = 12
    indexer = SemanticDiscussionIndexer(model, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(sid, "exchange-1", 11, 12, source_user_text="u", source_assistant_text="a", candidate_discussion_ids=())
    result = asyncio_run(indexer.index(snapshot))
    apply_semantic_index_proposals(
        db_session, session_id=sid, exchange_id="exchange-1", user_turn_id=11, assistant_turn_id=12,
        semantic_result=result, commit=True, input_digest="semantic-digest-1",
    )
    # A newer exchange is present but not indexed.
    gated = read_session_indexed_discussion_discovery(
        db_session, sid, discovery_max_payload_bytes=100000, coverage_max_payload_bytes=100000,
        source_freshness_verifier=lambda session, session_id, entries, states: True,
    )
    assert gated.discovery_complete is False
    # Engine with incomplete discovery must not execute the two model calls.
    result_engine = asyncio_run(run_engine(db_session, gated))
    assert result_engine.commands == []


def test_unresolved_correction_event_cannot_complete(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Correction Target",
        label_provenance="human_stated",
        member_exchanges=[{"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}],
    )
    did = created.discussion_id
    db_session.commit()
    model = FakeModelClient({
        "status": "proposals",
        "proposals": [
            {
                "discussion_id": did,
                "session_id": sid,
                "target_exchange_id": "exchange-1",
                "target_user_turn_id": 11,
                "target_assistant_turn_id": 12,
                "correction_text": "later correction",
            }
        ],
        "clarification": None,
        "reason": "correction",
    })
    eligible = (did,)
    indexer = SemanticDiscussionIndexer(model, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(
        sid, "exchange-1", 11, 12, source_user_text="u", source_assistant_text="a",
        candidate_discussion_ids=(did,), candidate_labels={did: "Correction Target"},
        eligible_correction_target_ids=eligible,
    )
    result = asyncio_run(indexer.index(snapshot))
    assert result.status == "proposals"
    applied = apply_semantic_index_proposals(
        db_session, session_id=sid, exchange_id="exchange-1", user_turn_id=11, assistant_turn_id=12,
        semantic_result=result, commit=True, input_digest="semantic-correction",
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert applied["applied_count"] == 0  # correction claim remains unresolved, not completed
    state = db_session.query(DiscussionIndexStateRow).one()
    assert state.status == INDEX_STATUS_INSUFFICIENT
    coverage = read_session_index_coverage(db_session, sid, max_payload_bytes=100000)
    assert coverage.structural_state_complete is False
    assert coverage.end_to_end_complete is False


def test_ambiguous_semantic_proposal_leads_to_clarification_not_complete(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    model = FakeModelClient({
        "status": "ambiguous",
        "proposals": [],
        "clarification": "Need more evidence",
        "reason": "unclear",
    })
    indexer = SemanticDiscussionIndexer(model, max_provider_calls=1)
    snapshot = SemanticIndexSnapshot(sid, "exchange-1", 11, 12, source_user_text="u", source_assistant_text="a", candidate_discussion_ids=())
    result = asyncio_run(indexer.index(snapshot))
    applied = apply_semantic_index_proposals(
        db_session, session_id=sid, exchange_id="exchange-1", user_turn_id=11, assistant_turn_id=12,
        semantic_result=result, commit=True, input_digest="semantic-ambiguous",
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=100000)
    # No grounded discussion was created; discovery candidate set is empty, so retrieval
    # cannot complete and reports its normal insufficiency rather than fabricated data.
    assert discovery.available_count == 0


def test_selected_evidence_pair_gate_uses_exact_session_and_member(db_session):
    sid = "session-e2e"
    set_current_session_id(sid)
    setup_exchange(db_session, sid, "exchange-1", 11, 12)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Pair Gate Topic",
        label_provenance="human_stated",
        member_exchanges=[{"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}],
    )
    db_session.commit()
    evidence = read_selected_discussion_evidence(
        db_session, sid, (created.discussion_id,),
        source_pair_readback=True,
        source_pair_readback_reader=__import__("backend.services.grounded_discussion_event_source_links", fromlist=["read_event_source_pair"]).read_event_source_pair,
    )
    assert evidence.session_id == sid
    assert evidence.discussion_count == 1
    # Empty event set keeps paired readback complete without fabricating events.
    assert evidence.source_pair_event_count == 0
    assert evidence.source_pair_complete is True


def test_empty_session_local_end_to_end(db_session):
    sid = "session-e2e-empty"
    set_current_session_id(sid)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=100000)
    coverage = read_session_index_coverage(db_session, sid, max_payload_bytes=100000)
    assert discovery.available_count == 0
    assert discovery.discovery_complete is True
    assert coverage.available_count == 0
    assert coverage.coverage_complete is True


def asyncio_run(coro):
    import asyncio
    return asyncio.run(coro)


async def run_engine(db_session, discovery):
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    engine = ConversationTurnEngine(model_client=None)
    return await engine.run(
        user_text="summarize",
        recent_turns=[],
        clarification_descriptors=[],
        discussion_discovery=discovery,
    )