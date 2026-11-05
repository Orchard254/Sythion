"""Isolated tests for grounded labelled discussion persistence."""

import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.services.grounded_discussions import (
        Base,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        DiscussionEventRow,
        GroundedDiscussionManager,
    )
    from backend.models.database import (
        ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
    )
except ImportError:  # pragma: no cover - alternate invocation layout
    from services.grounded_discussions import (
        Base,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        DiscussionEventRow,
        GroundedDiscussionManager,
    )
    from models.database import (
        ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
    )


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SYTHION_") or key.startswith("LEGACY_PROFILE_"):
            monkeypatch.delenv(key, raising=False)
    # Keep Settings, if constructed, from reading a .env file.
    monkeypatch.setattr("pydantic_settings.BaseSettings.__init__", lambda *a, **k: None, raising=False)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'grounded-discussion.db'}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    yield session, engine
    session.close()
    engine.dispose()


def _seed_exchange(session, session_id, exchange_id, user_turn_id, assistant_turn_id,
                   user_text="user text", assistant_text="assistant text", task_id=None,
                   created_at=None):
    if created_at is None:
        created_at = datetime.utcnow()
    if task_id is not None:
        session.add(Task(id=task_id, original_request="request", state="running", version=2))
    session.add(ConversationExchange(
        id=exchange_id, session_id=session_id, user_text=user_text,
        assistant_text=assistant_text, created_at=created_at,
    ))
    session.add(ConversationTurn(
        id=user_turn_id, session_id=session_id, role="user", summary=user_text,
        created_at=created_at,
    ))
    session.add(ConversationTurn(
        id=assistant_turn_id, session_id=session_id, role="assistant",
        summary=assistant_text, created_at=created_at,
    ))
    session.flush()
    return exchange_id


def test_taskless_discussion_with_exact_member(db):
    session, _ = db
    sid = "session-a"
    eid = "exchange-1"
    ut, at = 101, 102
    _seed_exchange(session, sid, eid, ut, at, "Lantern museum facts", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "Lantern museum facts",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    assert created.discussion_id.startswith("disc-")
    assert created.task_id is None
    # Flush-only persistence is visible in the same session.
    assert session.query(DiscussionRow).count() == 1
    assert session.query(DiscussionExchangeLinkRow).count() == 1
    view = mgr.read_discussion(session, sid, created.discussion_id)
    assert view is not None
    assert len(view.members) == 1
    assert view.members[0].exchange_id == eid
    assert view.members[0].user_turn_id == ut
    assert view.members[0].assistant_turn_id == at


def test_duplicate_labels_remain_separate(db):
    session, _ = db
    sid = "session-b"
    mgr = GroundedDiscussionManager(session)
    d1 = mgr.create_discussion(sid, "same label")
    d2 = mgr.create_discussion(sid, "same label")
    assert d1.discussion_id != d2.discussion_id
    assert session.query(DiscussionRow).filter_by(label="same label").count() == 2


def test_mixed_topic_exchange_is_single_exact_link(db):
    session, _ = db
    sid = "session-c"
    eid = "exchange-mixed"
    ut, at = 201, 202
    _seed_exchange(session, sid, eid, ut, at, "museum and cafe mixed", "mixed reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "mixed topic label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
        link_provenance="model_inferred_unverified",
    )
    view = mgr.read_discussion(session, sid, created.discussion_id)
    assert len(view.members) == 1
    assert view.members[0].link_provenance == "model_inferred_unverified"


def test_valid_correction_preserves_earlier_exchange_text(db):
    session, _ = db
    sid = "session-d"
    eid = "exchange-lantern"
    ut, at = 301, 302
    user_text = "What is Lantern museum price?"
    assistant_text = "Lantern is $9 and has no cafes."
    _seed_exchange(session, sid, eid, ut, at, user_text, assistant_text)
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "Lantern $9 no cafes",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    before_user = session.query(ConversationExchange).filter_by(id=eid).first().user_text
    before_assistant = session.query(ConversationExchange).filter_by(id=eid).first().assistant_text

    event = mgr.add_correction(
        created.discussion_id, sid,
        target_exchange_id=eid, target_user_turn_id=ut, target_assistant_turn_id=at,
        correction_text="Budget-only correction: Lantern entry is $9, no cafe.",
        provenance="explicit_validated_proposal",
    )
    assert event.event_type == "correction"
    assert event.payload_text == "Budget-only correction: Lantern entry is $9, no cafe."

    exchange = session.query(ConversationExchange).filter_by(id=eid).first()
    assert exchange.user_text == before_user == user_text
    assert exchange.assistant_text == before_assistant == assistant_text
    view = mgr.read_discussion(session, sid, created.discussion_id)
    assert len(view.events) >= 2
    assert any(e.event_type == "correction" for e in view.events)


def test_cross_session_reference_rejected(db):
    session, _ = db
    sid = "session-e"
    other = "session-other"
    eid = "exchange-foreign"
    ut, at = 401, 402
    _seed_exchange(session, other, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    with pytest.raises(ValueError):
        mgr.create_discussion(
            sid, "label",
            member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
        )
    # Correction target in another discussion/session is rejected.
    d = mgr.create_discussion(sid, "label")
    _seed_exchange(session, sid, "exchange-local", 403, 404, "t", "r")
    with pytest.raises(ValueError):
        mgr.add_correction(d.discussion_id, sid, target_exchange_id=eid,
                          target_user_turn_id=ut, target_assistant_turn_id=at,
                          correction_text="x")


def test_foreign_mismatched_padded_nonstring_ids_rejected(db):
    session, _ = db
    sid = "session-f"
    eid = "exchange-f"
    ut, at = 501, 502
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    # padded session
    with pytest.raises(ValueError):
        mgr.create_discussion(" " + sid, "label")
    # nonstring exchange id
    with pytest.raises(ValueError):
        mgr.create_discussion(sid, "label", member_exchanges=[
            {"exchange_id": 123, "user_turn_id": ut, "assistant_turn_id": at}
        ])
    # boolean turn id
    with pytest.raises(ValueError):
        mgr.create_discussion(sid, "label", member_exchanges=[
            {"exchange_id": eid, "user_turn_id": True, "assistant_turn_id": at}
        ])
    # mismatched turn ids
    with pytest.raises(ValueError):
        mgr.create_discussion(sid, "label", member_exchanges=[
            {"exchange_id": eid, "user_turn_id": ut + 999, "assistant_turn_id": at}
        ])
    # orphan exchange
    with pytest.raises(ValueError):
        mgr.create_discussion(sid, "label", member_exchanges=[
            {"exchange_id": "missing-exchange", "user_turn_id": ut, "assistant_turn_id": at}
        ])
    # padded discussion id in read (requires a created discussion)
    created = mgr.create_discussion(sid, "label")
    with pytest.raises(ValueError):
        mgr.get_discussion(session, " " + sid, created.discussion_id)


def test_wrong_correction_target_rejected(db):
    session, _ = db
    sid = "session-g"
    e1, e2 = "exchange-1", "exchange-2"
    _seed_exchange(session, sid, e1, 601, 602, "text1", "reply1")
    _seed_exchange(session, sid, e2, 603, 604, "text2", "reply2")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": e1, "user_turn_id": 601, "assistant_turn_id": 602}],
    )
    # e2 is not linked -> correction rejected.
    with pytest.raises(ValueError):
        mgr.add_correction(created.discussion_id, sid, target_exchange_id=e2,
                          target_user_turn_id=603, target_assistant_turn_id=604,
                          correction_text="wrong target")


def test_repair_is_append_only(db):
    session, _ = db
    sid = "session-h"
    eid = "exchange-repair"
    ut, at = 701, 702
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    correction = mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                                   target_user_turn_id=ut, target_assistant_turn_id=at,
                                   correction_text="needs repair")
    repair = mgr.repair_link(created.discussion_id, sid, prior_event_id=correction.id,
                             target_exchange_id=eid, target_user_turn_id=ut,
                             target_assistant_turn_id=at, repair_text="link repaired")
    assert repair.event_type == "link_repair"
    assert repair.prior_event_id == correction.id
    # Old correction row is preserved.
    db_session = session
    events = db_session.query(DiscussionEventRow).filter_by(
        discussion_id=created.discussion_id).all()
    assert len(events) >= 3
    assert any(e.id == correction.id for e in events)
    assert any(e.prior_event_id == correction.id for e in events)


def test_caller_rollback_removes_new_rows(db):
    session, _ = db
    sid = "session-i"
    eid = "exchange-rollback"
    ut, at = 801, 802
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    session.flush()
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                      target_user_turn_id=ut, target_assistant_turn_id=at,
                      correction_text="correction")
    assert session.query(DiscussionRow).count() == 1
    assert session.query(DiscussionEventRow).count() >= 2
    session.rollback()
    # After rollback, rows are gone in a fresh view of the same connection.
    assert session.query(DiscussionRow).count() == 0
    assert session.query(DiscussionExchangeLinkRow).count() == 0
    assert session.query(DiscussionEventRow).count() == 0


def test_caller_commit_survives_reload(tmp_path):
    db_path = tmp_path / "commit.db"
    engine = create_engine(f"sqlite:///{db_path}")
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine)
    session = Session()
    sid = "session-j"
    eid = "exchange-commit"
    ut, at = 901, 902
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    session.commit()
    session.close()

    session2 = Session()
    mgr2 = GroundedDiscussionManager(session2)
    view = mgr2.read_discussion(session2, sid, created.discussion_id)
    assert view is not None
    assert len(view.members) == 1
    session2.close()
    engine.dispose()


def test_no_helper_commit(db, monkeypatch):
    session, _ = db
    sid = "session-k"
    eid = "exchange-nocommit"
    ut, at = 1001, 1002
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    session.flush()

    class _CommitForbidden(Exception):
        pass

    def _forbidden(*_args, **_kwargs):
        raise _CommitForbidden("commit must not be called by the slice")

    monkeypatch.setattr(session, "commit", _forbidden)
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                      target_user_turn_id=ut, target_assistant_turn_id=at,
                      correction_text="correction")
    # Read must also avoid commit.
    view = mgr.read_discussion(session, sid, created.discussion_id)
    assert view is not None


def test_reader_includes_later_correction_beyond_twenty_unrelated(db):
    session, _ = db
    sid = "session-l"
    # Target discussion with two members so a caller cap can omit one member.
    e0, e1 = "exchange-target-0", "exchange-target-1"
    _seed_exchange(session, sid, e0, 1101, 1102, "target 0", "reply 0")
    _seed_exchange(session, sid, e1, 1103, 1104, "target 1", "reply 1")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "target label",
        member_exchanges=[
            {"exchange_id": e0, "user_turn_id": 1101, "assistant_turn_id": 1102},
            {"exchange_id": e1, "user_turn_id": 1103, "assistant_turn_id": 1104},
        ],
    )
    mgr.add_correction(created.discussion_id, sid, target_exchange_id=e0,
                      target_user_turn_id=1101, target_assistant_turn_id=1102,
                      correction_text="later correction beyond twenty")
    # Twenty-plus unrelated discussions/exchanges.
    for i in range(25):
        eid = f"unrelated-{i}"
        _seed_exchange(session, sid, eid, 2000 + i, 3000 + i, f"unrelated {i}", f"reply {i}")
        mgr.create_discussion(sid, f"unrelated label {i}",
                             member_exchanges=[{"exchange_id": eid, "user_turn_id": 2000 + i,
                                               "assistant_turn_id": 3000 + i}])

    # Bounded read omits a member but must still surface the correction event.
    view = mgr.read_discussion(session, sid, created.discussion_id, member_limit=1)
    assert len(view.members) == 1
    assert view.coverage.member_omitted_count == 1
    assert any(e.payload_text == "later correction beyond twenty" for e in view.events)
    assert view.coverage.event_available_count >= 3
    # Direct full read preserves exact members.
    full = mgr.read_discussion(session, sid, created.discussion_id)
    assert len(full.members) == 2
    assert full.coverage.member_omitted_count == 0


def test_validated_proposal_linkage_accepted_and_ambiguous_rejected(db):
    session, _ = db
    sid = "session-m"
    eid = "exchange-proposal"
    ut, at = 1201, 1202
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    correction_text = "budget-only from explicit validated proposal"
    proposal = {
        "discussion_id": created.discussion_id,
        "session_id": sid,
        "target_exchange_id": eid,
        "target_user_turn_id": ut,
        "target_assistant_turn_id": at,
        "correction_text": correction_text,
    }
    event = mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                             target_user_turn_id=ut, target_assistant_turn_id=at,
                             correction_text=correction_text,
                             validated_proposal=proposal,
                             provenance="explicit_validated_proposal")
    assert event.provenance == "explicit_validated_proposal"
    assert event.payload_text == correction_text

    # Ambiguous/mismatched proposal is rejected.
    bad_proposal = dict(proposal)
    bad_proposal["target_exchange_id"] = "different-exchange"
    with pytest.raises(ValueError):
        mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                          target_user_turn_id=ut, target_assistant_turn_id=at,
                          correction_text=correction_text, validated_proposal=bad_proposal)

    # Unknown proposal field rejected.
    unknown_proposal = dict(proposal)
    unknown_proposal["semantic_score"] = 0.9
    with pytest.raises(ValueError):
        mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                          target_user_turn_id=ut, target_assistant_turn_id=at,
                          correction_text=correction_text, validated_proposal=unknown_proposal)


def _make_task_bound_member(db_session, session_id, task_id, exchange_id, ut, at, assoc_task_id=None):
    if assoc_task_id is None:
        assoc_task_id = task_id
    for tid in {task_id, assoc_task_id}:
        existing = db_session.query(Task).filter_by(id=tid).first()
        if existing is None:
            db_session.add(Task(id=tid, original_request="request", state="running", version=2))
    _seed_exchange(db_session, session_id, exchange_id, ut, at, "text", "reply", task_id=None)
    assoc = ConversationTaskAssociation(
        exchange_id=exchange_id, task_id=assoc_task_id,
        user_turn_id=ut, assistant_turn_id=at,
    )
    db_session.add(assoc)
    db_session.flush()
    return exchange_id


def test_operation_atomicity_failed_later_write_leaves_no_partial_rows(db):
    from sqlalchemy import event

    session, _ = db
    sid = "session-atomic"
    seed_e = "exchange-atomic-seed"
    _seed_exchange(session, sid, seed_e, 1301, 1302, "seed", "reply")
    mgr = GroundedDiscussionManager(session)
    unrelated = mgr.create_discussion(sid, "unrelated surviving", member_exchanges=[])

    e_a = "exchange-atomic-a"
    e_b = "exchange-atomic-b"
    _seed_exchange(session, sid, e_a, 1311, 1312, "A", "reply A")
    _seed_exchange(session, sid, e_b, 1313, 1314, "B", "reply B")
    existing = mgr.create_discussion(
        sid, "existing", member_exchanges=[{"exchange_id": e_a, "user_turn_id": 1311, "assistant_turn_id": 1312}])

    before_discussions = session.query(DiscussionRow).count()
    before_links = session.query(DiscussionExchangeLinkRow).count()
    before_events = session.query(DiscussionEventRow).count()

    session._fail_injected = True
    session._flush_count = 0

    def _inject(session_obj, flush_context, instances):
        if not getattr(session_obj, "_fail_injected", False):
            return
        session_obj._flush_count += 1
        if session_obj._flush_count >= 2:
            session_obj._fail_injected = False
            raise RuntimeError("injected operation failure")

    event.listen(session, "before_flush", _inject)
    try:
        with pytest.raises((ValueError, RuntimeError)):
            mgr.create_discussion(
                sid, "should rollback",
                member_exchanges=[
                    {"exchange_id": e_b, "user_turn_id": 1313, "assistant_turn_id": 1314},
                ],
            )
    finally:
        event.remove(session, "before_flush", _inject)

    assert session.query(DiscussionRow).filter_by(label="should rollback").count() == 0
    assert session.query(DiscussionRow).count() == before_discussions
    assert session.query(DiscussionExchangeLinkRow).count() == before_links
    assert session.query(DiscussionEventRow).count() == before_events

    session.commit()
    Session = sessionmaker(bind=session.get_bind())
    check = Session()
    assert check.query(DiscussionRow).filter_by(discussion_id=unrelated.discussion_id).count() == 1
    assert check.query(DiscussionRow).filter_by(label="should rollback").count() == 0
    check.close()


def test_zero_limit_returns_empty_with_complete_counts(db):
    session, _ = db
    sid = "session-zero"
    eid = "exchange-zero"
    ut, at = 1401, 1402
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    # Add a second member so an empty slice has omitted count 1.
    eid2 = "exchange-zero-2"
    _seed_exchange(session, sid, eid2, 1403, 1404, "text2", "reply2")
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[
            {"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at},
            {"exchange_id": eid2, "user_turn_id": 1403, "assistant_turn_id": 1404},
        ],
    )
    mgr.add_correction(created.discussion_id, sid, target_exchange_id=eid,
                      target_user_turn_id=ut, target_assistant_turn_id=at,
                      correction_text="correction")
    view = mgr.read_discussion(session, sid, created.discussion_id, member_limit=0, event_limit=0)
    assert view.members == ()
    assert len(view.events) == 0
    assert view.coverage.member_available_count == 2
    assert view.coverage.member_returned_count == 0
    assert view.coverage.member_omitted_count == 2
    assert view.coverage.event_available_count >= 3
    assert view.coverage.event_returned_count == 0
    assert view.coverage.event_omitted_count == view.coverage.event_available_count
    full = mgr.read_discussion(session, sid, created.discussion_id)
    assert any(e.payload_text == "correction" for e in full.events)


def test_task_bound_later_link_requires_matching_task_association(db):
    session, _ = db
    sid = "session-task-link"
    task_id = "task-link-1"
    other_task = "task-link-2"
    e_valid = "exchange-task-valid"
    e_missing = "exchange-task-missing"
    e_wrong = "exchange-task-wrong"
    _make_task_bound_member(session, sid, task_id, e_valid, 1501, 1502, assoc_task_id=task_id)
    _seed_exchange(session, sid, e_missing, 1503, 1504, "text", "reply")
    _make_task_bound_member(session, sid, other_task, e_wrong, 1505, 1506, assoc_task_id=other_task)
    other_session = "session-task-link-foreign"
    _seed_exchange(session, other_session, "exchange-task-foreign", 1507, 1508, "text", "reply")

    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "task-bound", task_id=task_id,
        member_exchanges=[],
    )

    # Same exact task association can be linked later.
    link = mgr.link_exchange(created.discussion_id, sid, e_valid, 1501, 1502)
    assert link.exchange_id == e_valid

    # Missing association is not treated as compatible.
    with pytest.raises(ValueError):
        mgr.link_exchange(created.discussion_id, sid, e_missing, 1503, 1504)

    # Wrong or foreign task.
    with pytest.raises(ValueError):
        mgr.link_exchange(created.discussion_id, sid, e_wrong, 1505, 1506)
    with pytest.raises(ValueError):
        mgr.link_exchange(created.discussion_id, sid, "exchange-task-foreign", 1507, 1508)
    # Actually foreign-session exchange is invisible.
    # Padded/invalid IDs.
    with pytest.raises(ValueError):
        mgr.link_exchange(created.discussion_id, sid, " " + e_valid, 1501, 1502)


def test_missing_source_rows_are_not_false_complete(db):
    session, _ = db
    sid = "session-missing-source"
    e1 = "exchange-missing-1"
    e2 = "exchange-missing-2"
    _seed_exchange(session, sid, e1, 1601, 1602, "text 1", "reply 1")
    _seed_exchange(session, sid, e2, 1603, 1604, "text 2", "reply 2")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[
            {"exchange_id": e1, "user_turn_id": 1601, "assistant_turn_id": 1602},
            {"exchange_id": e2, "user_turn_id": 1603, "assistant_turn_id": 1604},
        ],
    )
    mgr.add_correction(created.discussion_id, sid, target_exchange_id=e1,
                      target_user_turn_id=1601, target_assistant_turn_id=1602,
                      correction_text="correction on potentially missing source")
    # Simulate canonical source drift by deleting one source exchange only.
    session.query(ConversationExchange).filter_by(id=e1, session_id=sid).delete(synchronize_session=False)
    session.flush()

    view = mgr.read_discussion(session, sid, created.discussion_id)
    assert view.coverage.link_count == 2
    assert view.coverage.member_available_count == 1
    assert view.coverage.unresolved_member_count == 1
    assert view.coverage.member_source_complete is False
    assert view.coverage.event_unresolved_subject_count >= 1
    assert view.coverage.event_source_complete is False
    # Original link still counted; reader does not delete it.
    assert session.query(DiscussionExchangeLinkRow).filter_by(discussion_id=created.discussion_id).count() == 2


def test_repair_prior_null_subject_cannot_rebind(db):
    session, _ = db
    sid = "session-null-repair"
    eid = "exchange-null-repair"
    ut, at = 1701, 1702
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    # Insert an event without an exact member subject (disposable simulation).
    session.add(DiscussionEventRow(
        discussion_id=created.discussion_id, session_id=sid,
        event_type="correction", target_exchange_id=None,
        target_user_turn_id=None, target_assistant_turn_id=None,
        payload_text="subjectless", provenance="model_inferred_unverified",
    ))
    session.flush()
    subjectless = (
        session.query(DiscussionEventRow)
        .filter_by(discussion_id=created.discussion_id, payload_text="subjectless")
        .first()
    )
    assert subjectless is not None
    with pytest.raises(ValueError):
        mgr.repair_link(created.discussion_id, sid, prior_event_id=subjectless.id,
                        target_exchange_id=eid, target_user_turn_id=ut,
                        target_assistant_turn_id=at, repair_text="attempted rebind")
    # Prior subjectless event remains and has no repair pointer.
    assert session.query(DiscussionEventRow).filter_by(id=subjectless.id).first().prior_event_id is None