"""Offline tests for lazy dual-anchor correction source-link storage."""

import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.core.session import set_current_session_id
from backend.services.grounded_discussions import (
    Base as GroundedBase,
    GroundedDiscussionManager,
    DiscussionRow,
    DiscussionExchangeLinkRow,
    DiscussionEventRow,
    DiscussionEventSourceLinkRow,
)
from backend.services.grounded_discussion_event_source_links import (
    INDEX_DUAL_ANCHOR_PROVEN,
    INDEX_DUAL_ANCHOR_UNORDERED,
    DualAnchorCoverage,
    attach_event_source_link,
    read_event_source_link,
    assess_dual_anchor_coverage,
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


def make_discussion(session, sid, members):
    gm = GroundedDiscussionManager(session)
    created = gm.create_discussion(
        session_id=sid,
        label="Dual Anchor Topic",
        label_provenance="human_stated",
        member_exchanges=members,
    )
    session.commit()
    return created.discussion_id


def add_event(session, sid, discussion_id, target_exchange, user_turn, assistant_turn):
    gm = GroundedDiscussionManager(session)
    event = gm.add_correction(
        discussion_id,
        sid,
        target_exchange_id=target_exchange,
        target_user_turn_id=user_turn,
        target_assistant_turn_id=assistant_turn,
        correction_text="Correction claim",
        provenance="human_stated",
    )
    session.commit()
    return event.id


def test_attach_requires_canonical_created_at_order(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    # Target created first, source created later.
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="target user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="target assistant"))
    db_session.add(ConversationExchange(id="target-ex", session_id=sid, user_text="target", assistant_text="target", created_at=__import__("datetime").datetime(2026, 1, 1)))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="source user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="source assistant"))
    db_session.add(ConversationExchange(id="source-ex", session_id=sid, user_text="source", assistant_text="source", created_at=__import__("datetime").datetime(2026, 1, 2)))
    db_session.commit()

    did = make_discussion(
        db_session, sid,
        [
            {"exchange_id": "target-ex", "user_turn_id": 1, "assistant_turn_id": 2},
            {"exchange_id": "source-ex", "user_turn_id": 3, "assistant_turn_id": 4},
        ],
    )
    event_id = add_event(db_session, sid, did, "target-ex", 1, 2)

    row = attach_event_source_link(
        db_session,
        event_id=event_id,
        session_id=sid,
        discussion_id=did,
        source_exchange_id="source-ex",
        source_user_turn_id=3,
        source_assistant_turn_id=4,
        target_exchange_id="target-ex",
        target_user_turn_id=1,
        target_assistant_turn_id=2,
        provenance="human_stated",
        commit=False,
    )
    assert row.event_id == event_id
    read = read_event_source_link(db_session, event_id)
    assert read is not None
    assert read.proven is True


def test_self_or_equal_or_future_target_rejected(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="assistant"))
    db_session.add(ConversationExchange(id="same-ex", session_id=sid, user_text="x", assistant_text="y", created_at=__import__("datetime").datetime(2026, 1, 1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id": "same-ex", "user_turn_id": 1, "assistant_turn_id": 2}])
    event_id = add_event(db_session, sid, did, "same-ex", 1, 2)

    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session,
            event_id=event_id,
            session_id=sid,
            discussion_id=did,
            source_exchange_id="same-ex",
            source_user_turn_id=1,
            source_assistant_turn_id=2,
            target_exchange_id="same-ex",
            target_user_turn_id=1,
            target_assistant_turn_id=2,
            commit=False,
        )
    db_session.rollback()
    assert db_session.query(DiscussionEventSourceLinkRow).count() == 0


def test_missing_sidecar_is_not_proven(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="assistant"))
    db_session.add(ConversationExchange(id="ex", session_id=sid, user_text="x", assistant_text="y"))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id": "ex", "user_turn_id": 1, "assistant_turn_id": 2}])
    event_id = add_event(db_session, sid, did, "ex", 1, 2)

    assert read_event_source_link(db_session, event_id) is None
    coverage = assess_dual_anchor_coverage(db_session, sid, did)
    assert coverage.event_count == 1
    assert coverage.sidecar_count == 0
    assert coverage.proven_count == 0
    assert coverage.missing_sidecar_count == 1


def test_foreign_session_or_unknown_discussion_fails_closed(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="assistant"))
    db_session.add(ConversationExchange(id="ex", session_id=sid, user_text="x", assistant_text="y", created_at=__import__("datetime").datetime(2026, 1, 2)))
    db_session.add(ConversationExchange(id="target", session_id=sid, user_text="t", assistant_text="t", created_at=__import__("datetime").datetime(2026, 1, 1)))
    db_session.commit()

    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session,
            event_id=999,
            session_id=sid,
            discussion_id="missing",
            source_exchange_id="ex",
            source_user_turn_id=1,
            source_assistant_turn_id=2,
            target_exchange_id="target",
            target_user_turn_id=1,
            target_assistant_turn_id=2,
            commit=False,
        )
    db_session.rollback()


def test_source_and_target_must_be_linked_members(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="src", assistant_text="src", created_at=__import__("datetime").datetime(2026, 1, 2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="tgt", assistant_text="tgt", created_at=__import__("datetime").datetime(2026, 1, 1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id": "src-ex", "user_turn_id": 1, "assistant_turn_id": 2}])
    # target is not a member, so sidecar cannot be proven.
    event_id = add_event(db_session, sid, did, "src-ex", 1, 2)

    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session,
            event_id=event_id,
            session_id=sid,
            discussion_id=did,
            source_exchange_id="src-ex",
            source_user_turn_id=1,
            source_assistant_turn_id=2,
            target_exchange_id="tgt-ex",
            target_user_turn_id=3,
            target_assistant_turn_id=4,
            commit=False,
        )
    db_session.rollback()


def test_attach_replay_does_not_duplicate_or_lose_pending_row(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="src", assistant_text="src", created_at=__import__("datetime").datetime(2026, 1, 2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="tgt", assistant_text="tgt", created_at=__import__("datetime").datetime(2026, 1, 1)))
    db_session.commit()
    did = make_discussion(
        db_session, sid,
        [
            {"exchange_id": "src-ex", "user_turn_id": 1, "assistant_turn_id": 2},
            {"exchange_id": "tgt-ex", "user_turn_id": 3, "assistant_turn_id": 4},
        ],
    )
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(
        did,
        sid,
        target_exchange_id="tgt-ex",
        target_user_turn_id=3,
        target_assistant_turn_id=4,
        correction_text="Correction claim",
        provenance="human_stated",
    )
    db_session.commit()
    event_id = event.id

    db_session.add(ConversationTurn(id=99, session_id=sid, role="assistant", summary="pending"))
    db_session.flush()

    attach_event_source_link(
        db_session,
        event_id=event_id,
        session_id=sid,
        discussion_id=did,
        source_exchange_id="src-ex",
        source_user_turn_id=1,
        source_assistant_turn_id=2,
        target_exchange_id="tgt-ex",
        target_user_turn_id=3,
        target_assistant_turn_id=4,
        provenance="human_stated",
        commit=False,
    )
    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session,
            event_id=event_id,
            session_id=sid,
            discussion_id=did,
            source_exchange_id="src-ex",
            source_user_turn_id=1,
            source_assistant_turn_id=2,
            target_exchange_id="tgt-ex",
            target_user_turn_id=3,
            target_assistant_turn_id=4,
            provenance="human_stated",
            commit=False,
        )
    assert db_session.query(DiscussionEventSourceLinkRow).filter_by(event_id=event_id).count() == 1
    assert db_session.query(ConversationTurn).filter_by(id=99).count() == 1

    coverage = assess_dual_anchor_coverage(db_session, sid, did)
    assert coverage.proven_count == 1
    assert coverage.missing_sidecar_count == 0

def test_member_linked_event_type_is_not_supported(db_session):
    sid = 'session-dual'
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role='user', summary='u'))
    db_session.add(ConversationTurn(id=2, session_id=sid, role='assistant', summary='a'))
    db_session.add(ConversationExchange(id='ex', session_id=sid, user_text='x', assistant_text='y', created_at=__import__('datetime').datetime(2026,1,2)))
    db_session.add(ConversationExchange(id='old', session_id=sid, user_text='o', assistant_text='o', created_at=__import__('datetime').datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{'exchange_id':'ex','user_turn_id':1,'assistant_turn_id':2},{'exchange_id':'old','user_turn_id':1,'assistant_turn_id':2}])
    event_id = None
    for row in db_session.query(DiscussionEventRow).filter_by(session_id=sid, discussion_id=did).all():
        if row.event_type == 'member_linked':
            event_id = row.id
    assert event_id is not None
    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session, event_id=event_id, session_id=sid, discussion_id=did,
            source_exchange_id='ex', source_user_turn_id=1, source_assistant_turn_id=2,
            target_exchange_id='old', target_user_turn_id=1, target_assistant_turn_id=2,
            commit=False,
        )
    db_session.rollback()


def test_import_does_not_create_runtime_table(db_session):
    import sqlalchemy as sa
    assert 'discussion_event_source_links' in GroundedBase.metadata.tables


def test_unverified_provenance_is_structurally_not_verified(db_session):
    sid = 'session-dual'
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role='user', summary='src user'))
    db_session.add(ConversationTurn(id=2, session_id=sid, role='assistant', summary='src assistant'))
    db_session.add(ConversationTurn(id=3, session_id=sid, role='user', summary='tgt user'))
    db_session.add(ConversationTurn(id=4, session_id=sid, role='assistant', summary='tgt assistant'))
    db_session.add(ConversationExchange(id='src-ex', session_id=sid, user_text='src', assistant_text='src', created_at=__import__('datetime').datetime(2026,1,2)))
    db_session.add(ConversationExchange(id='tgt-ex', session_id=sid, user_text='tgt', assistant_text='tgt', created_at=__import__('datetime').datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{'exchange_id':'src-ex','user_turn_id':1,'assistant_turn_id':2},{'exchange_id':'tgt-ex','user_turn_id':3,'assistant_turn_id':4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4, correction_text='claim', provenance='human_stated')
    db_session.commit()
    attach_event_source_link(
        db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id='src-ex', source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4,
        provenance='model_inferred_unverified', commit=False,
    )
    read = read_event_source_link(db_session, event.id)
    assert read is not None
    assert read.provenance_verified is False
    assert read.proven is False
    assert read.reason == 'unverified-provenance'


def test_pending_flushed_and_unflushed_rows_survive_duplicate_attach(db_session):
    sid = 'session-dual'
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role='user', summary='src user'))
    db_session.add(ConversationTurn(id=2, session_id=sid, role='assistant', summary='src assistant'))
    db_session.add(ConversationTurn(id=3, session_id=sid, role='user', summary='tgt user'))
    db_session.add(ConversationTurn(id=4, session_id=sid, role='assistant', summary='tgt assistant'))
    db_session.add(ConversationExchange(id='src-ex', session_id=sid, user_text='src', assistant_text='src', created_at=__import__('datetime').datetime(2026,1,2)))
    db_session.add(ConversationExchange(id='tgt-ex', session_id=sid, user_text='tgt', assistant_text='tgt', created_at=__import__('datetime').datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{'exchange_id':'src-ex','user_turn_id':1,'assistant_turn_id':2},{'exchange_id':'tgt-ex','user_turn_id':3,'assistant_turn_id':4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4, correction_text='claim', provenance='human_stated')
    db_session.commit()
    event_id = event.id
    attach_event_source_link(
        db_session, event_id=event_id, session_id=sid, discussion_id=did,
        source_exchange_id='src-ex', source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4,
        provenance='human_stated', commit=False,
    )
    # A pending unflushed row.
    db_session.add(ConversationTurn(id=99, session_id=sid, role='assistant', summary='pending-unflushed'))
    db_session.add(ConversationTurn(id=100, session_id=sid, role='assistant', summary='pending-flushed'))
    db_session.flush()
    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session, event_id=event_id, session_id=sid, discussion_id=did,
            source_exchange_id='src-ex', source_user_turn_id=1, source_assistant_turn_id=2,
            target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4,
            provenance='human_stated', commit=False,
        )
    assert db_session.query(ConversationTurn).filter_by(id=99).count() == 1
    assert db_session.query(ConversationTurn).filter_by(id=100).count() == 1
    assert db_session.query(DiscussionEventSourceLinkRow).filter_by(event_id=event_id).count() == 1
    # Outer rollback removes successful attach and pending rows.
    db_session.rollback()
    assert db_session.query(DiscussionEventSourceLinkRow).filter_by(event_id=event_id).count() == 0
    assert db_session.query(ConversationTurn).filter_by(id=99).count() == 0
    assert db_session.query(ConversationTurn).filter_by(id=100).count() == 0


def test_equal_timestamps_are_rejected_as_not_strictly_earlier(db_session):
    sid = 'session-dual'
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role='user', summary='src user'))
    db_session.add(ConversationTurn(id=2, session_id=sid, role='assistant', summary='src assistant'))
    db_session.add(ConversationTurn(id=3, session_id=sid, role='user', summary='tgt user'))
    db_session.add(ConversationTurn(id=4, session_id=sid, role='assistant', summary='tgt assistant'))
    same = __import__('datetime').datetime(2026,1,1)
    db_session.add(ConversationExchange(id='src-ex', session_id=sid, user_text='src', assistant_text='src', created_at=same))
    db_session.add(ConversationExchange(id='tgt-ex', session_id=sid, user_text='tgt', assistant_text='tgt', created_at=same))
    db_session.commit()
    did = make_discussion(db_session, sid, [{'exchange_id':'src-ex','user_turn_id':1,'assistant_turn_id':2},{'exchange_id':'tgt-ex','user_turn_id':3,'assistant_turn_id':4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4, correction_text='claim', provenance='human_stated')
    db_session.commit()
    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session, event_id=event.id, session_id=sid, discussion_id=did,
            source_exchange_id='src-ex', source_user_turn_id=1, source_assistant_turn_id=2,
            target_exchange_id='tgt-ex', target_user_turn_id=3, target_assistant_turn_id=4,
            provenance='human_stated', commit=False,
        )
    db_session.rollback()


def test_read_event_source_pair_returns_source_and_target_texts(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source text", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target text", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="human_stated", commit=False)
    db_session.commit()
    pair = read_event_source_pair(db_session, event.id)
    assert pair["structural_complete"] is True
    assert pair["source"]["user_text"] == "source text"
    assert pair["target"]["user_text"] == "target text"
    assert "unverified-provenance" not in pair["gaps"]


from backend.services.grounded_discussion_event_source_links import read_event_source_pair


def test_read_event_source_pair_reports_missing_sidecar_and_canonical_rows(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source text", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="src-ex", target_user_turn_id=1, target_assistant_turn_id=2, correction_text="claim", provenance="human_stated")
    db_session.commit()
    pair = read_event_source_pair(db_session, event.id)
    assert pair["available"] is False
    assert pair["structural_complete"] is False
    assert "missing-sidecar" in pair["gaps"] or "missing-sidecar" in [g for g in pair["gaps"]]
    assert "missing-canonical-target" in pair["gaps"] or pair["target"] is None


def test_commit_true_is_rejected_before_writing(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="assistant"))
    db_session.add(ConversationExchange(id="ex", session_id=sid, user_text="x", assistant_text="y", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"ex","user_turn_id":1,"assistant_turn_id":2}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="ex", target_user_turn_id=1, target_assistant_turn_id=2, correction_text="claim", provenance="human_stated")
    db_session.commit()
    before = db_session.query(DiscussionEventSourceLinkRow).count()
    with pytest.raises(ValueError):
        attach_event_source_link(
            db_session, event_id=event.id, session_id=sid, discussion_id=did,
            source_exchange_id="ex", source_user_turn_id=1, source_assistant_turn_id=2,
            target_exchange_id="ex", target_user_turn_id=1, target_assistant_turn_id=2,
            provenance="human_stated", commit=True,
        )
    after = db_session.query(DiscussionEventSourceLinkRow).count()
    assert before == after


def test_forged_sidecar_for_unsupported_event_does_not_complete_or_expose_text(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source secret", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target text", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    gm = GroundedDiscussionManager(db_session)
    did = gm.create_discussion(session_id=sid, label="Topic", label_provenance="human_stated", member_exchanges=[
        {"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},
        {"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4},
    ])
    db_session.commit()
    member_event_id = None
    for row in db_session.query(DiscussionEventRow).filter_by(session_id=sid, discussion_id=did.discussion_id):
        if row.event_type == "member_linked":
            member_event_id = row.id
    assert member_event_id is not None
    from backend.services.grounded_discussions import DiscussionEventSourceLinkRow
    db_session.add(DiscussionEventSourceLinkRow(
        event_id=member_event_id,
        session_id=sid,
        discussion_id=did.discussion_id,
        source_exchange_id="src-ex",
        source_user_turn_id=1,
        source_assistant_turn_id=2,
        target_exchange_id="tgt-ex",
        target_user_turn_id=3,
        target_assistant_turn_id=4,
        provenance="human_stated",
        created_at=__import__("datetime").datetime(2026,1,2),
    ))
    db_session.commit()

    link = read_event_source_link(db_session, member_event_id)
    assert link is not None
    assert link.proven is False
    assert link.structural_proven is False
    assert link.reason == "unsupported-event"

    pair = read_event_source_pair(db_session, member_event_id)
    assert pair["available"] is False
    assert pair["structural_complete"] is False
    assert pair["source"] is None
    assert pair["target"] is None
    assert "source secret" not in str(pair)


def test_deleted_member_linkage_marks_source_target_missing_and_hides_text(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source secret", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target text", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="human_stated", commit=False)
    db_session.commit()

    # Break structural membership while leaving canonical and sidecar rows.
    db_session.query(DiscussionExchangeLinkRow).filter_by(
        discussion_id=did, exchange_id="src-ex"
    ).delete(synchronize_session=False)
    db_session.commit()

    link = read_event_source_link(db_session, event.id)
    assert link is not None
    assert link.structural_proven is False
    assert link.reason == "missing-source-member"

    pair = read_event_source_pair(db_session, event.id)
    assert pair["structural_complete"] is False
    assert pair["available"] is False
    assert pair["source"] is None
    assert pair["target"] is None
    assert "source secret" not in str(pair)


def test_unverified_provenance_keeps_texts_with_structurally_complete_gap(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source text", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target text", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="model_inferred_unverified", commit=False)
    db_session.commit()
    pair = read_event_source_pair(db_session, event.id)
    assert pair["structural_complete"] is True
    assert pair["provenance_verified"] is False
    assert "unverified-provenance" in pair["gaps"]
    assert pair["source"]["user_text"] == "source text"
    assert pair["target"]["user_text"] == "target text"


def test_unicode_text_is_bounded_with_explicit_truncation_flag(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    long_text = "x" * 50
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text=long_text, assistant_text="answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="human_stated", commit=False)
    db_session.commit()
    pair = read_event_source_pair(db_session, event.id, max_text_chars=10)
    assert pair["structural_complete"] is True
    assert pair["source"]["text_truncated"] is True
    assert len(pair["source"]["user_text"]) == 10
    assert "source-text-truncated" in pair["gaps"]


def test_malformed_source_created_at_is_incomparable_and_hides_text(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source secret", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target text", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="human_stated", commit=False)
    db_session.commit()

    from sqlalchemy import text
    db_session.execute(text("UPDATE conversation_exchanges SET created_at='not-a-date' WHERE id='src-ex'"))
    db_session.commit()

    pair = read_event_source_pair(db_session, event.id)
    assert pair["available"] is False
    assert pair["structural_complete"] is False
    assert pair["source"] is None
    assert pair["target"] is None
    assert "missing-canonical-source" in pair["gaps"] or "incomparable-chronology" in pair["gaps"]
    assert "source secret" not in str(pair)


def test_malformed_target_created_at_is_incomparable_and_hides_text(db_session):
    sid = "session-dual"
    set_current_session_id(sid)
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    db_session.add(ConversationExchange(id="src-ex", session_id=sid, user_text="source text", assistant_text="source answer", created_at=__import__("datetime").datetime(2026,1,2)))
    db_session.add(ConversationExchange(id="tgt-ex", session_id=sid, user_text="target secret", assistant_text="target answer", created_at=__import__("datetime").datetime(2026,1,1)))
    db_session.commit()
    did = make_discussion(db_session, sid, [{"exchange_id":"src-ex","user_turn_id":1,"assistant_turn_id":2},{"exchange_id":"tgt-ex","user_turn_id":3,"assistant_turn_id":4}])
    gm = GroundedDiscussionManager(db_session)
    event = gm.add_correction(did, sid, target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4, correction_text="claim", provenance="human_stated")
    db_session.commit()
    attach_event_source_link(db_session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=1, source_assistant_turn_id=2,
        target_exchange_id="tgt-ex", target_user_turn_id=3, target_assistant_turn_id=4,
        provenance="human_stated", commit=False)
    db_session.commit()

    from sqlalchemy import text
    db_session.execute(text("UPDATE conversation_exchanges SET created_at='not-a-date' WHERE id='tgt-ex'"))
    db_session.commit()

    pair = read_event_source_pair(db_session, event.id)
    assert pair["available"] is False
    assert pair["structural_complete"] is False
    assert pair["source"] is None
    assert pair["target"] is None
    assert "target secret" not in str(pair)
