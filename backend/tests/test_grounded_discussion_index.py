"""Offline tests for durable grounded discussion indexing application."""

import hashlib
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, ConversationExchange, ConversationTurn, Task
from backend.core.session import set_current_session_id
from backend.services.grounded_discussions import (
    Base as GroundedBase,
    GroundedDiscussionManager,
    DiscussionRow,
    DiscussionExchangeLinkRow,
    DiscussionEventRow,
)
from backend.services.grounded_discussion_semantic_index import (
    SemanticIndexResult,
    SemanticIndexSnapshot,
)
from backend.services.grounded_discussion_index import (
    DiscussionIndexManager,
    apply_semantic_index_proposals,
    INDEX_STATUS_PENDING,
    INDEX_STATUS_IN_PROGRESS,
    INDEX_STATUS_COMPLETE,
    INDEX_STATUS_INSUFFICIENT,
    INDEX_STATUS_UNCERTAIN,
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


def setup_exchange(session, sid, exchange_id="exchange-1"):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="user"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="assistant"))
    session.add(ConversationExchange(id=exchange_id, session_id=sid, user_text="user", assistant_text="assistant"))
    session.commit()


def make_result(status="proposals", proposals=(), clarification=None, reason="ok"):
    return SemanticIndexResult(
        status=status,
        proposals=tuple(proposals),
        clarification=clarification,
        reason=reason,
        untrusted=True,
    )


def test_receive_indexes_run_and_replays_without_duplicate_state(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    mgr = DiscussionIndexManager(db_session, db=db_session)
    row, created = mgr.receive(sid, "exchange-1", 11, 12, input_digest="digest-1")
    assert created is True
    assert row.status == INDEX_STATUS_PENDING
    key = row.idempotency_key
    row2, created2 = mgr.receive(sid, "exchange-1", 11, 12, input_digest="digest-1")
    assert created2 is False
    assert row2.idempotency_key == key
    assert db_session.query(DiscussionIndexStateRow if False else __import__("backend.services.grounded_discussion_index", fromlist=["DiscussionIndexStateRow"]).DiscussionIndexStateRow).count() == 1


def test_receive_rejects_mismatched_binding(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    mgr = DiscussionIndexManager(db_session, db=db_session)
    mgr.receive(sid, "exchange-1", 11, 12, input_digest="digest-1")
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow
    with pytest.raises(ValueError):
        mgr.receive(sid, "exchange-1", 11, 99, input_digest="digest-1")
    with pytest.raises(ValueError):
        mgr.receive(sid, "exchange-1", 11, 12, input_digest="other")
    assert db_session.query(DiscussionIndexStateRow).count() == 1


def test_apply_insufficient_does_not_create_grounded_rows(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(status="insufficient", proposals=[], clarification="Need more", reason="sparse")
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert applied["applied_count"] == 0
    assert db_session.query(DiscussionRow).count() == 0
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow
    state = db_session.query(DiscussionIndexStateRow).one()
    assert state.status == INDEX_STATUS_INSUFFICIENT


def test_apply_new_label_creates_discussion_and_link(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(
        proposals=(
            {"discussion_id": None, "label": "New Semantic Topic", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="new topic",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["applied_count"] == 1
    assert db_session.query(DiscussionRow).count() == 1
    assert db_session.query(DiscussionExchangeLinkRow).count() == 1
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow
    state = db_session.query(DiscussionIndexStateRow).one()
    assert state.status == INDEX_STATUS_COMPLETE
    assert len(json.loads(state.result_json)["applied_discussion_ids"]) == 1


def test_apply_existing_discussion_correction_claim_remains_unresolved(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Existing Topic",
        label_provenance="human_stated",
        member_exchanges=[],
    )
    did = created.discussion_id
    gm.link_exchange(
        did,
        sid,
        "exchange-1",
        11,
        12,
        link_provenance="human_stated",
    )
    db_session.commit()

    result = make_result(
        proposals=(
            {"discussion_id": did, "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12,
             "correction_text": "Correct this topic"},
        ),
        reason="correction",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert db_session.query(DiscussionEventRow).filter_by(discussion_id=did, event_type="correction").count() == 0
    assert any("correction-event-representation-not-implemented" in item for item in (applied.get("unresolved") or []))


def test_apply_rejects_foreign_discussion_without_mutation(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(
        proposals=(
            {"discussion_id": "foreign-discussion", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="foreign",
    )
    with pytest.raises(ValueError):
        apply_semantic_index_proposals(
            db_session,
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            semantic_result=result,
            commit=False,
        )
    db_session.rollback()
    assert db_session.query(DiscussionRow).count() == 0
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow
    assert db_session.query(DiscussionIndexStateRow).count() == 0


def test_apply_anchor_mismatch_rejects_and_leaves_uncertain(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(
        proposals=(
            {"discussion_id": None, "label": "Bad Anchor", "session_id": "other",
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="bad anchor",
    )
    with pytest.raises(ValueError):
        apply_semantic_index_proposals(
            db_session,
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            semantic_result=result,
            commit=False,
        )
    db_session.rollback()
    assert db_session.query(DiscussionRow).count() == 0


def test_apply_savepoint_preserves_unrelated_pending_row(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    db_session.add(ConversationTurn(id=99, session_id=sid, role="assistant", summary="pending"))
    db_session.flush()
    result = make_result(
        proposals=(
            {"discussion_id": "foreign-discussion", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="foreign",
    )
    with pytest.raises(ValueError):
        apply_semantic_index_proposals(
            db_session,
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            semantic_result=result,
            commit=False,
        )
    assert db_session.query(ConversationTurn).filter_by(id=99).count() == 1
    db_session.rollback()
    assert db_session.query(ConversationTurn).filter_by(id=99).count() == 0


def test_apply_replay_does_not_duplicate_discussions(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(
        proposals=(
            {"discussion_id": None, "label": "Replay Topic", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="replay",
    )
    first = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
        input_digest="digest-replay",
    )
    assert first["replicated"] is False
    initial_count = db_session.query(DiscussionRow).count()
    second = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
        input_digest="digest-replay",
    )
    assert second["replicated"] is True
    assert db_session.query(DiscussionRow).count() == initial_count


def test_apply_requires_nonempty_proposals_for_proposals_status(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(status="proposals", proposals=(), clarification=None, reason="bad")
    with pytest.raises(ValueError):
        apply_semantic_index_proposals(
            db_session,
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            semantic_result=result,
            commit=False,
        )
    db_session.rollback()


def test_multi_topic_proposals_create_multiple_discussions(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    result = make_result(
        proposals=(
            {"discussion_id": None, "label": "Topic One", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
            {"discussion_id": None, "label": "Topic Two", "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12},
        ),
        reason="multi",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["applied_count"] == 2
    assert db_session.query(DiscussionRow).count() == 2
    assert db_session.query(DiscussionExchangeLinkRow).count() == 2


def test_uncertain_provider_result_transition_and_no_provider_call(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    mgr = DiscussionIndexManager(db_session, db=db_session)
    state, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest="uncertain")
    assert state.status == INDEX_STATUS_PENDING
    mgr.transition(state.idempotency_key, INDEX_STATUS_IN_PROGRESS, commit=False)
    mgr.transition(state.idempotency_key, INDEX_STATUS_UNCERTAIN, error_code="provider_timeout", commit=True)
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow
    row = db_session.query(DiscussionIndexStateRow).one()
    assert row.status == INDEX_STATUS_UNCERTAIN
    assert row.error_code == "provider_timeout"


def test_successful_transition_clears_stale_error_code(db_session):
    """A row that failed then completed must not still carry the old error."""
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow

    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    mgr = DiscussionIndexManager(db_session, db=db_session)
    state, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest="recovered")
    mgr.transition(state.idempotency_key, INDEX_STATUS_IN_PROGRESS, commit=False)
    mgr.transition(state.idempotency_key, INDEX_STATUS_UNCERTAIN,
                   error_code="provider_timeout", commit=False)
    mgr.transition(state.idempotency_key, INDEX_STATUS_COMPLETE,
                   result_json={"indexed": 2}, commit=True)

    row = db_session.query(DiscussionIndexStateRow).one()
    assert row.status == INDEX_STATUS_COMPLETE
    assert row.result_json is not None
    assert row.error_code is None


def test_error_transition_clears_stale_result_json(db_session):
    """Symmetrically, a later failure must not keep an earlier result payload."""
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow

    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    mgr = DiscussionIndexManager(db_session, db=db_session)
    state, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest="re-failed")
    mgr.transition(state.idempotency_key, INDEX_STATUS_COMPLETE,
                   result_json={"indexed": 1}, commit=False)
    mgr.transition(state.idempotency_key, INDEX_STATUS_UNCERTAIN,
                   error_code="retry_failed", commit=True)

    row = db_session.query(DiscussionIndexStateRow).one()
    assert row.status == INDEX_STATUS_UNCERTAIN
    assert row.error_code == "retry_failed"
    assert row.result_json is None


def test_unlinked_existing_correction_does_not_fabricate_event_or_link(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Existing Topic",
        label_provenance="human_stated",
        member_exchanges=[],
    )
    did = created.discussion_id
    db_session.commit()

    result = make_result(
        proposals=(
            {"discussion_id": did, "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12,
             "correction_text": "Attempted correction"},
        ),
        reason="unlinked correction",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert applied["applied_discussion_ids"] == []
    assert any("correction" in item for item in (applied.get("unresolved") or []))
    assert db_session.query(DiscussionExchangeLinkRow).filter_by(discussion_id=did).count() == 0
    assert db_session.query(DiscussionEventRow).filter_by(discussion_id=did, event_type="correction").count() == 0


def test_repair_text_is_not_silently_ignored(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Existing Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
    )
    did = created.discussion_id
    db_session.commit()

    result = make_result(
        proposals=(
            {"discussion_id": did, "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12,
             "repair_text": "Repair claim"},
        ),
        reason="repair",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert any("repair" in item for item in (applied.get("unresolved") or []))
    assert db_session.query(DiscussionEventRow).filter_by(discussion_id=did, event_type="link_repair").count() == 0


def test_correction_event_targets_pre_existing_member_not_unproven_current_exchange(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=7, session_id=sid, role="user", summary="old user"))
    db_session.add(ConversationTurn(id=8, session_id=sid, role="assistant", summary="old assistant"))
    db_session.add(ConversationExchange(id="exchange-old", session_id=sid, user_text="old user", assistant_text="old assistant"))
    setup_exchange(db_session, sid)

    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Existing Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-old", "user_turn_id": 7, "assistant_turn_id": 8}
        ],
    )
    did = created.discussion_id
    db_session.commit()

    result = make_result(
        proposals=(
            {"discussion_id": did, "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12,
             "correction_text": "Correction referencing current-looking target only"},
        ),
        reason="unproven target",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert db_session.query(DiscussionEventRow).filter_by(discussion_id=did, event_type="correction").count() == 0


def test_multi_member_correction_without_exact_target_fails_closed(db_session):
    sid = "session-index"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=7, session_id=sid, role="user", summary="old user"))
    db_session.add(ConversationTurn(id=8, session_id=sid, role="assistant", summary="old assistant"))
    db_session.add(ConversationExchange(id="exchange-old", session_id=sid, user_text="old user", assistant_text="old assistant"))
    db_session.add(ConversationTurn(id=9, session_id=sid, role="user", summary="even older"))
    db_session.add(ConversationTurn(id=10, session_id=sid, role="assistant", summary="even older assistant"))
    db_session.add(ConversationExchange(id="exchange-older", session_id=sid, user_text="even older", assistant_text="older"))
    setup_exchange(db_session, sid)

    gm = GroundedDiscussionManager(db_session)
    created = gm.create_discussion(
        session_id=sid,
        label="Existing Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-old", "user_turn_id": 7, "assistant_turn_id": 8},
            {"exchange_id": "exchange-older", "user_turn_id": 9, "assistant_turn_id": 10},
        ],
    )
    did = created.discussion_id
    db_session.commit()

    result = make_result(
        proposals=(
            {"discussion_id": did, "session_id": sid,
             "target_exchange_id": "exchange-1", "target_user_turn_id": 11,
             "target_assistant_turn_id": 12,
             "correction_text": "Ambiguous correction target"},
        ),
        reason="ambiguous",
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id="exchange-1",
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied["status"] == INDEX_STATUS_INSUFFICIENT
    assert db_session.query(DiscussionEventRow).filter_by(discussion_id=did, event_type="correction").count() == 0


def test_duplicate_unresolved_reasons_are_deduplicated(db_session):
    sid = 'session-index'
    set_current_session_id(sid)
    setup_exchange(db_session, sid)
    gm = GroundedDiscussionManager(db_session)
    first = gm.create_discussion(session_id=sid, label='Existing Topic', label_provenance='human_stated', member_exchanges=[])
    second = gm.create_discussion(session_id=sid, label='Existing Topic', label_provenance='human_stated', member_exchanges=[])
    db_session.commit()
    result = make_result(
        proposals=(
            {'discussion_id': first.discussion_id, 'session_id': sid,
             'target_exchange_id': 'exchange-1', 'target_user_turn_id': 11,
             'target_assistant_turn_id': 12,
             'correction_text': 'Same correction'},
            {'discussion_id': second.discussion_id, 'session_id': sid,
             'target_exchange_id': 'exchange-1', 'target_user_turn_id': 11,
             'target_assistant_turn_id': 12,
             'correction_text': 'Same correction'},
        ),
        reason='duplicate unresolved',
    )
    applied = apply_semantic_index_proposals(
        db_session,
        session_id=sid,
        exchange_id='exchange-1',
        user_turn_id=11,
        assistant_turn_id=12,
        semantic_result=result,
        commit=True,
    )
    assert applied['status'] == INDEX_STATUS_INSUFFICIENT
    reasons = applied.get('unresolved') or []
    assert len(reasons) == len(set(reasons))
