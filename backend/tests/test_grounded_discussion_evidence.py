"""Isolated tests for the exact selected-discussion evidence adapter."""

import json
import os
from types import MappingProxyType

import pytest
from sqlalchemy.exc import OperationalError
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.services.grounded_discussions import (
        Base, DiscussionRow, DiscussionExchangeLinkRow, DiscussionEventRow,
        GroundedDiscussionManager,
    )
    from backend.models.database import (
        ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
    )
    from backend.services.grounded_discussion_evidence import (
        SelectedDiscussionEvidenceError, read_selected_discussion_evidence,
        SelectedDiscussionEvidenceEntry, SelectedDiscussionEvidenceResult,
        _deep_freeze, _deep_thaw, _empty_coverage,
    )
except ImportError:  # pragma: no cover - alternate invocation layout
    from services.grounded_discussions import (
        Base, DiscussionRow, DiscussionExchangeLinkRow, DiscussionEventRow,
        GroundedDiscussionManager,
    )
    from models.database import (
        ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
    )
    from services.grounded_discussion_evidence import (
        SelectedDiscussionEvidenceError, read_selected_discussion_evidence,
        SelectedDiscussionEvidenceEntry, SelectedDiscussionEvidenceResult,
        _deep_freeze, _deep_thaw, _empty_coverage,
    )


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SYTHION_") or key.startswith("LEGACY_PROFILE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("pydantic_settings.BaseSettings.__init__", lambda *a, **k: None, raising=False)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'grounded-evidence.db'}")
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
        created_at = __import__("datetime").datetime.utcnow()
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


def test_taskless_topic(db):
    session, _ = db
    sid = "session-taskless"
    eid = "exchange-taskless"
    ut, at = 101, 102
    _seed_exchange(session, sid, eid, ut, at, "Lantern museum facts", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "Lantern museum facts",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    assert result.selected_discussion_ids == (created.discussion_id,)
    assert result.discussion_count == 1
    entry = result.entries[0]
    assert entry.label == "Lantern museum facts"
    assert entry.task_id is None
    assert entry.members[0].exchange_id == eid
    assert entry.members[0].canonical_user_text == "Lantern museum facts"
    assert entry.semantic_complete is False
    assert result.untrusted is True
    assert result.revalidation_required_before_dispatch is True


def test_duplicate_labels_isolated(db):
    session, _ = db
    sid = "session-dup"
    mgr = GroundedDiscussionManager(session)
    d1 = mgr.create_discussion(sid, "same label")
    d2 = mgr.create_discussion(sid, "same label")
    result = read_selected_discussion_evidence(session, sid, [d1.discussion_id, d2.discussion_id])
    assert result.discussion_count == 2
    assert [e.discussion_id for e in result.entries] == [d1.discussion_id, d2.discussion_id]
    assert result.entries[0].discussion_id != result.entries[1].discussion_id


def test_multiple_discussions_same_exchange(db):
    session, _ = db
    sid = "session-shared"
    eid = "exchange-shared"
    ut, at = 201, 202
    _seed_exchange(session, sid, eid, ut, at, "shared text", "shared reply")
    mgr = GroundedDiscussionManager(session)
    d1 = mgr.create_discussion(
        sid, "first topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    d2 = mgr.create_discussion(
        sid, "second topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    result = read_selected_discussion_evidence(session, sid, [d1.discussion_id, d2.discussion_id])
    assert result.discussion_count == 2
    assert all(len(e.members) == 1 for e in result.entries)
    assert all(e.members[0].exchange_id == eid for e in result.entries)


def test_correction_preserved_with_many_unrelated_exchanges(db):
    session, _ = db
    sid = "session-lantern"
    eid0 = "lantern-initial"
    ut0, at0 = 1, 2
    _seed_exchange(session, sid, eid0, ut0, at0,
                   "Project Lantern: one indoor museum visit, $9 budget, no cafés.",
                   "Project Lantern is one indoor museum visit, $9 budget, no cafés.")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "Lantern",
        member_exchanges=[{"exchange_id": eid0, "user_turn_id": ut0, "assistant_turn_id": at0}],
    )
    mgr.add_correction(
        created.discussion_id, sid,
        target_exchange_id=eid0, target_user_turn_id=ut0, target_assistant_turn_id=at0,
        correction_text="Budget-only correction: reduce Lantern budget to $5; keep museum visit and no cafés.",
    )
    # Seed more than 20 unrelated exchanges; they must not displace the source.
    for i in range(25):
        _seed_exchange(session, sid, f"unrelated-{i}", 1000 + i * 2, 1001 + i * 2,
                       f"unrelated user {i}", f"unrelated assistant {i}")
    # PaperKite separate discussion.
    paper_eid = "paperkite"
    pu, pa = 9001, 9002
    _seed_exchange(session, sid, paper_eid, pu, pa,
                   "PaperKite: $5 prize, no food.",
                   "PaperKite budget $5, no food, prizes.")
    paper_disc = mgr.create_discussion(
        sid, "PaperKite",
        member_exchanges=[{"exchange_id": paper_eid, "user_turn_id": pu, "assistant_turn_id": pa}],
    )

    result = read_selected_discussion_evidence(
        session, sid, [created.discussion_id, paper_disc.discussion_id])
    assert result.discussion_count == 2
    lantern = result.entries[0]
    assert len(lantern.members) == 1
    assert "$9 budget" in (lantern.members[0].canonical_user_text or "")
    assert "no cafés" in (lantern.members[0].canonical_user_text or "")
    assert any(e.is_correction for e in lantern.events)
    correction = [e for e in lantern.events if e.is_correction][0]
    assert correction.payload_text == "Budget-only correction: reduce Lantern budget to $5; keep museum visit and no cafés."
    paper = result.entries[1]
    assert "$5" in (paper.members[0].canonical_user_text or "")
    assert "no food" in (paper.members[0].canonical_user_text or "")


def test_exact_task_state_freshness_and_associations(db):
    session, _ = db
    sid = "session-task"
    tid = "task-42"
    eid = "task-exchange"
    ut, at = 301, 302
    _seed_exchange(session, sid, eid, ut, at, "task user", "task assistant", task_id=tid)
    session.add(ConversationTaskAssociation(
        exchange_id=eid, task_id=tid, user_turn_id=ut, assistant_turn_id=at))
    session.flush()
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "task discussion", task_id=tid,
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    assert created.task_id == tid
    # Freshness: mutate task state (not metadata) and read again.
    task = session.query(Task).filter_by(id=tid).first()
    task.state = "completed"
    task.version = 9
    task.final_result = "fresh result"
    session.flush()

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    member = result.entries[0].members[0]
    assert member.exact_task_association is True
    assert member.task_state == "completed"
    assert member.task_version == 9
    assert member.task_final_result == "fresh result"

    # Foreign/mismatched association: mismatched turn IDs do not surface task state.
    sid2 = "session-task2"
    eid2 = "task-exchange2"
    ut2, at2 = 401, 402
    _seed_exchange(session, sid2, eid2, ut2, at2, "task2 user", "task2 assistant", task_id=None)
    # Foreign task association with matching turns but nonexistent task.
    session.add(ConversationTaskAssociation(
        exchange_id=eid2, task_id="foreign-task", user_turn_id=ut2, assistant_turn_id=at2))
    session.flush()
    mgr2 = GroundedDiscussionManager(session)
    created2 = mgr2.create_discussion(
        sid2, "task discussion 2",
        member_exchanges=[{"exchange_id": eid2, "user_turn_id": ut2, "assistant_turn_id": at2}],
    )
    result2 = read_selected_discussion_evidence(session, sid2, [created2.discussion_id])
    member2 = result2.entries[0].members[0]
    assert member2.exact_task_association is False
    assert member2.task_id is None
    assert result2.entries[0].discussion_id == created2.discussion_id


def test_source_missing_and_role_drift(db):
    session, _ = db
    sid = "session-missing"
    eid = "exchange-missing"
    ut, at = 501, 502
    _seed_exchange(session, sid, eid, ut, at, "source text", "source reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "source topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    # Simulate role drift by changing user turn role.
    session.query(ConversationTurn).filter_by(id=ut, session_id=sid).update({"role": "assistant"})
    session.flush()

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    member = result.entries[0].members[0]
    assert member.source_available is False
    assert result.entries[0].member_coverage.unresolved_member_count == 1
    assert result.entries[0].member_coverage.member_source_complete is False
    assert result.is_structurally_complete is False
    assert "unresolved_or_missing_member_source" in result.entries[0].insufficiency_reasons

    # Missing source row: delete assistant turn via direct delete.
    session.query(ConversationTurn).filter_by(id=at, session_id=sid).delete(synchronize_session=False)
    session.flush()
    result2 = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    assert result2.entries[0].member_coverage.unresolved_member_count >= 1
    assert result2.is_structurally_complete is False


def test_zero_bounds_and_partial_event_linkage(db):
    session, _ = db
    sid = "session-bounds"
    eid = "exchange-bounds"
    ut, at = 601, 602
    _seed_exchange(session, sid, eid, ut, at, "source text", "source reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "bounds topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    mgr.add_correction(
        created.discussion_id, sid,
        target_exchange_id=eid, target_user_turn_id=ut, target_assistant_turn_id=at,
        correction_text="correction text",
    )

    result = read_selected_discussion_evidence(
        session, sid, [created.discussion_id], member_limit=0, event_limit=0)
    entry = result.entries[0]
    assert entry.members == ()
    assert entry.events == ()
    assert entry.member_coverage.member_available_count == 1
    assert entry.member_coverage.member_omitted_count == 1
    assert entry.event_coverage.event_available_count == 2
    assert entry.event_coverage.event_omitted_count == 2
    assert entry.insufficiency_reasons  # non-empty
    assert result.insufficient is True
    assert result.is_structurally_complete is False

    # Partial source-event linkage remains visible as unresolved.
    # Inject a legacy event with a missing target subject directly into storage.
    legacy = DiscussionEventRow(
        discussion_id=created.discussion_id,
        session_id=sid,
        event_type="correction",
        target_exchange_id=eid,
        target_user_turn_id=ut,
        target_assistant_turn_id=999999,
        payload_text="legacy partial subject",
        provenance="legacy_unverified",
    )
    session.add(legacy)
    session.flush()
    result2 = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    # The prior correction event target is valid; legacy null-subject is treated as
    # unresolved subject in coverage accounting.
    assert result2.entries[0].event_coverage.event_unresolved_subject_count >= 1
    assert "unresolved_event_subject" in result2.entries[0].insufficiency_reasons


def test_raw_unicode_ids_strict(db):
    session, _ = db
    sid = "session-Ω-unicode"
    eid = "exchange-π"
    ut, at = 701, 702
    _seed_exchange(session, sid, eid, ut, at, "unicode user", "unicode assistant")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "unicode label",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    assert result.session_id == sid
    assert result.entries[0].discussion_id == created.discussion_id

    # Padded/foreign/unknown IDs fail the whole request.
    for bad in (" " + created.discussion_id, created.discussion_id + " ", "no-such-discussion"):
        with pytest.raises(SelectedDiscussionEvidenceError):
            read_selected_discussion_evidence(session, sid, [created.discussion_id, bad])
    with pytest.raises(SelectedDiscussionEvidenceError):
        read_selected_discussion_evidence(session, " " + sid, [created.discussion_id])
    with pytest.raises(SelectedDiscussionEvidenceError):
        read_selected_discussion_evidence(session, sid, [""])


def test_text_2000_flags_source_unchanged(db):
    session, _ = db
    sid = "session-text"
    eid = "exchange-text"
    ut, at = 801, 802
    long_user = "U" * 2001 + "é"
    long_assistant = "A" * 2005
    _seed_exchange(session, sid, eid, ut, at, long_user, long_assistant)
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "text topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    member = result.entries[0].members[0]
    assert member.user_text_truncated is True
    assert member.assistant_text_truncated is True
    assert isinstance(member.user_text, str) and len(member.user_text) <= 2000
    assert isinstance(member.assistant_text, str) and len(member.assistant_text) <= 2000
    # Canonical and source DB remain full.
    assert member.canonical_user_text == long_user
    assert member.canonical_assistant_text == long_assistant
    row = session.query(ConversationExchange).filter_by(id=eid, session_id=sid).first()
    assert row.user_text == long_user
    assert row.assistant_text == long_assistant
    assert result.entries[0].has_role_text_truncation is True
    assert result.is_structurally_complete is False


def test_unknown_foreign_padded_ids_whole_fail_no_silent_drop(db):
    session, _ = db
    sid = "session-fail"
    eid = "exchange-fail"
    ut, at = 901, 902
    _seed_exchange(session, sid, eid, ut, at, "text", "reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    valid = created.discussion_id
    for bad_list in (
        [valid, "foreign-id"],
        [valid, " " + valid],
        [valid, valid + " "],
    ):
        with pytest.raises(SelectedDiscussionEvidenceError):
            read_selected_discussion_evidence(session, sid, bad_list)
    # No hidden partial result: exception before return.


def test_repair_events_represented_without_effective_truth(db):
    session, _ = db
    sid = "session-repair"
    eid = "exchange-repair"
    ut, at = 1001, 1002
    _seed_exchange(session, sid, eid, ut, at, "repair source", "repair reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "repair topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    corr = mgr.add_correction(
        created.discussion_id, sid,
        target_exchange_id=eid, target_user_turn_id=ut, target_assistant_turn_id=at,
        correction_text="first correction",
    )
    repair = mgr.repair_link(
        created.discussion_id, sid,
        prior_event_id=corr.id,
        target_exchange_id=eid, target_user_turn_id=ut, target_assistant_turn_id=at,
        repair_text="repair note: linkage updated",
    )
    assert repair.prior_event_id == corr.id

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    entry = result.entries[0]
    types = [e.event_type for e in entry.events]
    assert "correction" in types
    assert "link_repair" in types
    repair_event = [e for e in entry.events if e.is_link_repair][0]
    assert repair_event.payload_text == "repair note: linkage updated"
    assert repair_event.prior_event_id == corr.id
    assert repair_event.target_source_complete is True
    # Raw history stays fallible; no effective corrected truth is asserted.
    assert entry.semantic_complete is False
    assert result.semantic_complete is False


def test_read_only_no_mutation_and_no_dispatch(db):
    session, _ = db
    sid = "session-readonly"
    eid = "exchange-readonly"
    ut, at = 1101, 1102
    _seed_exchange(session, sid, eid, ut, at, "readonly source", "readonly reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "readonly topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    pending_before = len(session.new)
    rowcount_before = session.query(DiscussionRow).count()
    linkcount_before = session.query(DiscussionExchangeLinkRow).count()
    eventcount_before = session.query(DiscussionEventRow).count()

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])

    assert len(session.new) == pending_before
    assert session.query(DiscussionRow).count() == rowcount_before
    assert session.query(DiscussionExchangeLinkRow).count() == linkcount_before
    assert session.query(DiscussionEventRow).count() == eventcount_before
    assert result is not None
    # No service object claims dispatch authorization.
    assert not hasattr(result, "authorize_dispatch")
    assert result.revalidation_required_before_dispatch is True


def test_task_bearing_entry_and_result_to_dict_and_getitem_json_serializable(db):
    session, engine = db
    sid = "session-task-serialize"
    tid = "task-serialize"
    eid = "task-exchange-serialize"
    ut, at = 1301, 1302
    _seed_exchange(session, sid, eid, ut, at, "task user", "task assistant", task_id=tid)
    association = ConversationTaskAssociation(
        exchange_id=eid, task_id=tid, user_turn_id=ut, assistant_turn_id=at,
    )
    session.add(association)
    # Declared API result is Text, so keep JSON text as the stored payload.
    task = session.query(Task).filter_by(id=tid).first()
    task.final_result = '{"nested": [1, true, null, 2.5, "text", {}, []]}'
    task.state = "completed"
    task.version = 7
    session.flush()

    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "task serialization topic",
        task_id=tid,
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    pending_before = len(session.new)
    dirty_before = len(session.dirty)
    deleted_before = len(session.deleted)

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    entry = result.entries[0]

    assert entry.discussion_task_fields is not None
    task_fields = entry.discussion_task_fields
    assert isinstance(task_fields, MappingProxyType)

    entry_payload = entry.to_dict()
    result_payload = result.to_dict()
    json.dumps(entry_payload)
    json.dumps(result_payload)

    assert entry["discussion_id"] == entry.discussion_id
    assert result["discussion_count"] == result.discussion_count
    assert result["entries"][0]["discussion_task_fields"] == entry_payload["discussion_task_fields"]

    assert entry_payload["discussion_task_fields"]["task_final_result"] == task.final_result
    assert isinstance(entry_payload["discussion_task_fields"]["task_final_result"], str)

    entry_payload["discussion_task_fields"]["injected"] = "changed"
    assert "injected" not in task_fields

    # coverage helper is imported from the adapter module above.

    direct_entry = SelectedDiscussionEvidenceEntry(
        discussion_id="direct",
        session_id=sid,
        label="direct",
        label_provenance="human_stated",
        label_unverified=False,
        task_id=None,
        created_at=None,
        updated_at=None,
        member_count_available=0,
        members=(),
        member_coverage=_empty_coverage(),
        events=(),
        event_coverage=_empty_coverage(),
        discussion_task_fields=_deep_freeze({
            "task_final_result": {
                "items": [1, 2],
                "truthy": True,
                "nothing": None,
                "value": 2.5,
                "text": "value",
            }
        }),
        provenance_values=(),
        has_unverified_or_legacy_provenance=False,
        has_role_text_truncation=False,
        is_structurally_complete=True,
        semantic_complete=False,
        insufficiency_reasons=(),
    )
    direct_payload = direct_entry.to_dict()
    json.dumps(direct_payload)
    direct_payload["discussion_task_fields"]["task_final_result"]["items"].append(999)
    assert direct_entry.discussion_task_fields["task_final_result"]["items"] == (1, 2)
    assert isinstance(direct_entry.discussion_task_fields["task_final_result"]["items"], tuple)

    result_payload["entries"][0]["discussion_id"] = "mutated"
    assert result.entries[0].discussion_id == created.discussion_id

    row = session.query(ConversationExchange).filter_by(id=eid, session_id=sid).first()
    assert row.user_text == "task user"
    task_row = session.query(Task).filter_by(id=tid).first()
    assert task_row.final_result == '{"nested": [1, true, null, 2.5, "text", {}, []]}'
    assert len(session.new) == pending_before
    assert len(session.dirty) == dirty_before
    assert len(session.deleted) == deleted_before
    assert result.untrusted is True
    assert result.semantic_complete is False
    assert result.revalidation_required_before_dispatch is True


def test_nested_mappingproxy_mutation_raises_and_arrays_immutable():
    original = {
        "outer": {"inner": [1, 2, 3], "empty_map": {}, "number": 1},
        "empty_list": [],
        "empty_map": {},
    }
    frozen = _deep_freeze(original)

    original["outer"]["inner"].append(4)
    original["new"] = "added"
    assert frozen["outer"]["inner"] == (1, 2, 3)
    assert "new" not in frozen

    assert isinstance(frozen, MappingProxyType)
    with pytest.raises(TypeError):
        frozen["new"] = "value"
    assert isinstance(frozen["outer"], MappingProxyType)
    with pytest.raises(TypeError):
        frozen["outer"]["new"] = "value"

    assert isinstance(frozen["outer"]["inner"], tuple)
    assert isinstance(frozen["empty_list"], tuple)
    with pytest.raises(TypeError):
        frozen["outer"]["inner"][0] = 999
    with pytest.raises((TypeError, AttributeError)):
        frozen["empty_list"].append(1)

    assert frozen["outer"]["empty_map"] == {}
    assert isinstance(frozen["outer"]["empty_map"], MappingProxyType)
    with pytest.raises(TypeError):
        frozen["outer"]["empty_map"]["x"] = 1

    thawed = _deep_thaw(frozen)
    assert isinstance(thawed, dict)
    assert isinstance(thawed["outer"], dict)
    assert isinstance(thawed["outer"]["inner"], list)
    thawed["outer"]["inner"].append(999)
    thawed["outer"]["new_key"] = "changed"
    assert frozen["outer"]["inner"] == (1, 2, 3)
    assert "new_key" not in frozen["outer"]


def test_deep_freeze_thaw_roundtrip_json_nested():
    value = {
        "list": [1, True, None, 2.5, "text", {"nested": []}, []],
        "map": {"nested": {"empty": []}},
        "empty_list": [],
        "empty_map": {},
        "tuple_like_input_becomes_list": [1, 2],
    }
    frozen = _deep_freeze(value)
    thawed = _deep_thaw(frozen)

    assert thawed == value
    assert type(thawed) is dict
    assert type(thawed["list"]) is list
    assert type(thawed["empty_list"]) is list
    assert type(thawed["empty_map"]) is dict
    assert type(thawed["map"]["nested"]) is dict
    assert type(thawed["map"]["nested"]["empty"]) is list
    assert thawed["list"][3] == 2.5
    assert thawed["list"][1] is True
    assert thawed["list"][2] is None
    assert thawed["empty_list"] == []
    assert thawed["empty_map"] == {}
    assert _deep_thaw(_deep_freeze(thawed)) == value

    assert _deep_thaw(_deep_freeze({})) == {}
    assert _deep_thaw(_deep_freeze([])) == []


def test_operational_error_propagates_member_read(db, monkeypatch):
    session, engine = db
    sid = "session-op-member"
    eid = "exchange-op-member"
    ut, at = 1401, 1402
    _seed_exchange(session, sid, eid, ut, at, "op member", "op reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "op member topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )

    import backend.services.grounded_discussion_evidence as evidence_mod

    target = evidence_mod._ConversationExchange
    original_query = session.query

    def wrapped(model, *args, **kwargs):
        if model is target:
            raise OperationalError("member read failed", {}, Exception("injected"))
        return original_query(model, *args, **kwargs)

    monkeypatch.setattr(session, "query", wrapped)
    pending = Task(id="pending-member", original_request="pending", state="running", version=1)
    session.add(pending)
    pending_before = len(session.new)
    dirty_before = len(session.dirty)
    deleted_before = len(session.deleted)

    with pytest.raises(OperationalError) as exc:
        read_selected_discussion_evidence(session, sid, [created.discussion_id])

    assert type(exc.value) is OperationalError
    assert "member read failed" in str(exc.value)
    assert len(session.new) == pending_before
    assert len(session.dirty) == dirty_before
    assert len(session.deleted) == deleted_before


def test_operational_error_propagates_task_read(db, monkeypatch):
    session, engine = db
    sid = "session-op-task"
    tid = "task-op-task"
    eid = "exchange-op-task"
    ut, at = 1501, 1502
    _seed_exchange(session, sid, eid, ut, at, "op task", "op reply", task_id=tid)
    association = ConversationTaskAssociation(
        exchange_id=eid, task_id=tid, user_turn_id=ut, assistant_turn_id=at,
    )
    session.add(association)
    session.flush()

    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "op task topic", task_id=tid,
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )

    import backend.services.grounded_discussion_evidence as evidence_mod

    target = evidence_mod._Task
    original_query = session.query

    def wrapped(model, *args, **kwargs):
        if model is target:
            raise OperationalError("task read failed", {}, Exception("injected"))
        return original_query(model, *args, **kwargs)

    monkeypatch.setattr(session, "query", wrapped)
    pending = Task(id="pending-task", original_request="pending", state="running", version=1)
    session.add(pending)
    pending_before = len(session.new)
    dirty_before = len(session.dirty)
    deleted_before = len(session.deleted)

    with pytest.raises(OperationalError) as exc:
        read_selected_discussion_evidence(session, sid, [created.discussion_id])

    assert type(exc.value) is OperationalError
    assert "task read failed" in str(exc.value)
    assert len(session.new) == pending_before
    assert len(session.dirty) == dirty_before
    assert len(session.deleted) == deleted_before


def test_operational_error_propagates_event_read(db, monkeypatch):
    session, engine = db
    sid = "session-op-event"
    eid = "exchange-op-event"
    ut, at = 1601, 1602
    _seed_exchange(session, sid, eid, ut, at, "op event", "op reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "op event topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )
    mgr.add_correction(
        created.discussion_id, sid,
        target_exchange_id=eid, target_user_turn_id=ut, target_assistant_turn_id=at,
        correction_text="op correction",
    )

    import backend.services.grounded_discussion_evidence as evidence_mod

    target = evidence_mod._EventRow
    original_query = session.query

    def wrapped(model, *args, **kwargs):
        if model is target:
            raise OperationalError("event read failed", {}, Exception("injected"))
        return original_query(model, *args, **kwargs)

    monkeypatch.setattr(session, "query", wrapped)
    pending = Task(id="pending-event", original_request="pending", state="running", version=1)
    session.add(pending)
    pending_before = len(session.new)
    dirty_before = len(session.dirty)
    deleted_before = len(session.deleted)

    with pytest.raises(OperationalError) as exc:
        read_selected_discussion_evidence(session, sid, [created.discussion_id])

    assert type(exc.value) is OperationalError
    assert "event read failed" in str(exc.value)
    assert len(session.new) == pending_before
    assert len(session.dirty) == dirty_before
    assert len(session.deleted) == deleted_before


def test_missing_deleted_roles_session_association_incompleteness(db):
    session, engine = db
    sid = "session-missing-roles"
    eid = "exchange-missing-roles"
    ut, at = 1701, 1702
    _seed_exchange(session, sid, eid, ut, at, "missing source", "missing reply")
    mgr = GroundedDiscussionManager(session)
    created = mgr.create_discussion(
        sid, "missing roles topic",
        member_exchanges=[{"exchange_id": eid, "user_turn_id": ut, "assistant_turn_id": at}],
    )

    session.query(ConversationTurn).filter_by(id=ut, session_id=sid).update({"role": "assistant"})
    session.add(ConversationTaskAssociation(
        exchange_id=eid, task_id="foreign-task", user_turn_id=ut, assistant_turn_id=at,
    ))
    session.query(ConversationTurn).filter_by(id=at, session_id=sid).delete(synchronize_session=False)
    session.flush()

    with pytest.raises(SelectedDiscussionEvidenceError):
        read_selected_discussion_evidence(session, " " + sid, [created.discussion_id])
    with pytest.raises(SelectedDiscussionEvidenceError):
        read_selected_discussion_evidence(session, sid, [created.discussion_id, "no-such-discussion"])

    result = read_selected_discussion_evidence(session, sid, [created.discussion_id])
    entry = result.entries[0]
    assert entry.members[0].source_available is False
    assert entry.members[0].exact_task_association is False
    assert entry.member_source_complete is False
    assert result.is_structurally_complete is False
    assert entry.semantic_complete is False
    assert result.semantic_complete is False
    json.dumps(entry.to_dict())
    json.dumps(result.to_dict())
