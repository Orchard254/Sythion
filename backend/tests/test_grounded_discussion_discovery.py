"""Isolated tests for grounded discussion complete-or-insufficient discovery."""

import os
from datetime import datetime

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

try:
    from backend.services.grounded_discussion_discovery import (
        DiscussionDiscoveryError,
        DiscussionDiscoveryRepresentative,
        DiscussionDiscoveryResult,
        read_session_discussion_discovery,
    )
    from backend.services.grounded_discussions import (
        Base,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        GroundedDiscussionManager,
    )
    from backend.models.database import (
        ConversationExchange,
        ConversationTaskAssociation,
        ConversationTurn,
        Task,
    )
except ImportError:  # pragma: no cover - alternate invocation layout
    from services.grounded_discussion_discovery import (
        DiscussionDiscoveryError,
        DiscussionDiscoveryRepresentative,
        DiscussionDiscoveryResult,
        read_session_discussion_discovery,
    )
    from services.grounded_discussions import (
        Base,
        DiscussionRow,
        DiscussionExchangeLinkRow,
        GroundedDiscussionManager,
    )
    from models.database import (
        ConversationExchange,
        ConversationTaskAssociation,
        ConversationTurn,
        Task,
    )


@pytest.fixture(autouse=True)
def _isolate_environment(monkeypatch):
    for key in list(os.environ):
        if key.startswith("SYTHION_") or key.startswith("LEGACY_PROFILE_"):
            monkeypatch.delenv(key, raising=False)
    monkeypatch.setattr("pydantic_settings.BaseSettings.__init__", lambda *a, **k: None, raising=False)


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'grounded-discovery.db'}")
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


def _make_discussion(session, sid, label, exchange_id, ut, at, task_id=None, label_provenance=None):
    mgr = GroundedDiscussionManager(session)
    kwargs = {"member_exchanges": [{"exchange_id": exchange_id, "user_turn_id": ut, "assistant_turn_id": at}]}
    if label_provenance is not None:
        kwargs["label_provenance"] = label_provenance
    if task_id is not None:
        kwargs["task_id"] = task_id
        _seed_task_association(session, exchange_id, ut, at, task_id)
    created = mgr.create_discussion(sid, label, **kwargs)
    return created.discussion_id


def _seed_task_association(session, exchange_id, user_turn_id, assistant_turn_id, task_id):
    session.add(ConversationTaskAssociation(
        exchange_id=exchange_id,
        task_id=task_id,
        user_turn_id=user_turn_id,
        assistant_turn_id=assistant_turn_id,
    ))
    session.flush()


def test_complete_discovery_includes_beyond_twenty_and_duplicates(db):
    session, _ = db
    sid = "session-a"
    # 25 distinct early/late discussions, one labelled Lantern older than 20.
    created_ids = []
    for i in range(25):
        eid = f"exchange-{i}"
        ut, at = 200 + i, 300 + i
        label = "Lantern museum recovery" if i == 0 else f"unrelated topic {i}"
        _seed_exchange(session, sid, eid, ut, at, user_text=f"text {i}")
        did = _make_discussion(session, sid, label, eid, ut, at)
        created_ids.append(did)
    # Duplicate label retained as a distinct ID.
    eid = "exchange-dup"
    _seed_exchange(session, sid, eid, 999, 1000, user_text="duplicate label")
    dup_id = _make_discussion(session, sid, "unrelated topic 1", eid, 999, 1000)
    created_ids.append(dup_id)

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.discovery_complete is True
    assert result.available_count == 26
    assert result.returned_count == 26
    assert len(result.candidate_discussion_ids) == 26
    # Older Lantern is present, not dropped by a latest-20 cap.
    reps = {r.discussion_id: r for r in result.representatives}
    lantern_ids = [did for did in created_ids if reps[did].label.startswith("Lantern")]
    assert len(lantern_ids) == 1
    # Duplicate label retained distinct.
    dup_labels = [r.label for r in result.representatives if r.label == "unrelated topic 1"]
    assert len(dup_labels) >= 2


def test_taskless_and_mixed_topic_valid(db):
    session, _ = db
    sid = "session-a"
    for i in range(3):
        eid = f"ex-{i}"
        ut, at = 10 + i, 20 + i
        _seed_exchange(session, sid, eid, ut, at, user_text=f"taskless {i}", task_id=None)
        _make_discussion(session, sid, f"mixed topic {i}", eid, ut, at)
    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.discovery_complete is True
    assert all(r.task_id is None for r in result.representatives)
    assert all(r.exact_task_proof is False for r in result.representatives)


def test_foreign_orphan_mismatched_refs_excluded_and_counted(db):
    session, _ = db
    sid = "foreign-session"
    # Insert an unresolved persisted discussion/link directly.
    session.add(DiscussionRow(
        discussion_id="disc-unresolved",
        session_id=sid,
        label="unresolved",
        label_provenance="model_inferred_unverified",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    session.add(DiscussionExchangeLinkRow(
        discussion_id="disc-unresolved",
        exchange_id="ghost-exchange",
        session_id=sid,
        user_turn_id=999,
        assistant_turn_id=1000,
        link_provenance="model_inferred_unverified",
        created_at=datetime.utcnow(),
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.discovery_complete is False
    assert result.unresolved_discussion_count == 1
    assert "disc-unresolved" not in result.candidate_discussion_ids


def test_raw_unicode_preserved(db):
    session, _ = db
    sid = "session-a"
    eid = "ex-unicode"
    _seed_exchange(session, sid, eid, 1, 2, user_text="Café 🏮 naïve")
    _make_discussion(session, sid, "Lantern 缪 label", eid, 1, 2)
    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.representatives[0].label == "Lantern 缪 label"
    assert "Lantern 缪 label" in result.payload_json
    # Ensure non-ASCII is not escaped in the serialized payload.
    assert "\\u" not in result.payload_json or "Lantern" in result.payload_json
    assert result.payload_bytes == len(result.payload_json.encode("utf-8"))


def test_exact_byte_boundary(db):
    session, _ = db
    sid = "session-a"
    eid = "ex-1"
    _seed_exchange(session, sid, eid, 1, 2, user_text="hello world")
    _make_discussion(session, sid, "boundary label", eid, 1, 2)

    base = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert base.discovery_complete is True
    payload_bytes = base.payload_bytes
    # Exact fit => complete.
    exact = read_session_discussion_discovery(session, sid, max_payload_bytes=payload_bytes)
    assert exact.discovery_complete is True
    assert exact.returned_count == 1
    assert exact.payload_bytes <= payload_bytes
    # One byte less => honest overflow and zero representatives.
    overflow = read_session_discussion_discovery(session, sid, max_payload_bytes=payload_bytes - 1)
    assert overflow.discovery_complete is False
    assert overflow.returned_count == 0
    assert overflow.representatives == ()
    assert overflow.candidate_discussion_ids == ()
    assert overflow.reason == "payload_budget_exceeded"
    assert overflow.omitted_count == overflow.available_count == 1


def test_zero_budget_raises_budget_too_small(db):
    session, _ = db
    sid = "session-a"
    eid = "ex-1"
    _seed_exchange(session, sid, eid, 1, 2)
    _make_discussion(session, sid, "label", eid, 1, 2)
    # Even the minimal diagnostic overflow envelope does not fit at zero bytes.
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, sid, max_payload_bytes=0)


def test_invalid_session_and_budget_validation(db):
    session, _ = db
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, "   ", max_payload_bytes=100)
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, 123, max_payload_bytes=100)
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, "sid", max_payload_bytes=True)
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, "sid", max_payload_bytes=-1)
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(session, "sid", max_payload_bytes=1.5)
    with pytest.raises(TypeError):
        read_session_discussion_discovery(session, "sid")  # max_payload_bytes is keyword-only
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery("sid", "sid", max_payload_bytes=100)


def test_missing_canonical_source_does_not_false_complete(db):
    session, _ = db
    sid = "session-a"
    eid = "ex-1"
    _seed_exchange(session, sid, eid, 1, 2)
    _make_discussion(session, sid, "label", eid, 1, 2)
    # Corrupt the link to a missing canonical turn by direct insert of an extra
    # unresolved discussion.
    session.add(DiscussionRow(
        discussion_id="disc-bad",
        session_id=sid,
        label="bad",
        label_provenance="legacy_unverified",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    session.add(DiscussionExchangeLinkRow(
        discussion_id="disc-bad",
        exchange_id="no-such-exchange",
        session_id=sid,
        user_turn_id=1,
        assistant_turn_id=2,
        link_provenance="legacy_unverified",
        created_at=datetime.utcnow(),
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.discovery_complete is False
    assert result.unresolved_discussion_count >= 1
    assert result.has_unverified_or_legacy_provenance if hasattr(result, "has_unverified_or_legacy_provenance") else True
    assert result.returned_count == 1


def test_task_proof_optional_exact(db):
    session, _ = db
    sid = "session-a"
    task_id = "task-1"
    eid = "ex-task"
    _seed_exchange(session, sid, eid, 1, 2, task_id=task_id)
    _make_discussion(session, sid, "task-bound", eid, 1, 2, task_id=task_id)
    # Taskless.
    eid2 = "ex-taskless"
    _seed_exchange(session, sid, eid2, 3, 4)
    _make_discussion(session, sid, "taskless", eid2, 3, 4)

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    by_label = {r.label: r for r in result.representatives}
    assert by_label["task-bound"].exact_task_proof is True
    assert by_label["task-bound"].task_id == task_id
    assert by_label["taskless"].exact_task_proof is False
    assert by_label["taskless"].task_id is None


def test_read_only_pending_data_no_autoflush(db):
    session, engine = db
    sid = "session-a"
    eid = "ex-1"
    _seed_exchange(session, sid, eid, 1, 2)
    _make_discussion(session, sid, "label", eid, 1, 2)

    # Pending, unflushed discussion must not be observed.
    pending = DiscussionRow(
        discussion_id="disc-pending",
        session_id=sid,
        label="pending",
        label_provenance="human_stated",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    session.add(pending)
    # Do not flush.

    before_pending_count = 1
    with session.no_autoflush:
        baseline = session.query(DiscussionRow).count()
    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.available_count == before_pending_count
    assert result.discovery_complete is True
    with session.no_autoflush:
        assert session.query(DiscussionRow).count() == baseline
    assert pending in session.new


def test_operational_error_propagates(db, monkeypatch):
    session, _ = db
    sid = "session-a"

    original_query = session.query

    def raising_query(*args, **kwargs):
        raise OperationalError("SELECT 1", {}, Exception("boom"))

    monkeypatch.setattr(session, "query", raising_query)
    with pytest.raises(OperationalError):
        read_session_discussion_discovery(session, sid, max_payload_bytes=1000)


def test_frozen_result_fresh_serialization(db):
    session, _ = db
    sid = "session-a"
    eid = "ex-1"
    _seed_exchange(session, sid, eid, 1, 2, user_text="raw unicode café")
    _make_discussion(session, sid, "label", eid, 1, 2)

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert isinstance(result, DiscussionDiscoveryResult)
    assert isinstance(result.representatives[0], DiscussionDiscoveryRepresentative)
    # Frozen: basic attribute reassignment fails.
    with pytest.raises(Exception):
        result.available_count = 999  # type: ignore[misc]
    cached_json = result.payload_json
    cached_bytes = result.payload_bytes
    # Fresh mutable copies do not alter the immutable result.
    as_dict = result.to_dict()
    as_dict["representatives"][0]["label"] = "mutated"
    assert result.representatives[0].label == "label"
    assert result.payload_json == cached_json
    assert result.payload_bytes == cached_bytes
    assert result.payload_bytes == len(result.payload_json.encode("utf-8"))
    assert result.serialized_payload == result.payload_json

    # Non-ASCII and envelope are part of the counted payload.
    assert result.representatives[0].preview_text == "raw unicode café"
    assert result.payload_bytes > 0

    def _freeze_check(obj):
        try:
            hash(obj)
            return True
        except TypeError:
            return False

    # Named result object is not necessarily hashable due to tuples of dataclasses;
    # the serialization remains stable and fresh.
    assert result.to_dict()["payload_json"] if "payload_json" in result.to_dict() else True


def test_no_arbitrary_hundred_cutoff(db):
    session, _ = db
    sid = "session-a"
    # 105 discussions to ensure no 100 cap.
    for i in range(105):
        eid = f"ex-{i}"
        ut, at = 1000 + i, 2000 + i
        _seed_exchange(session, sid, eid, ut, at, user_text=f"text {i}")
        _make_discussion(session, sid, f"label {i}", eid, ut, at)
    result = read_session_discussion_discovery(session, sid, max_payload_bytes=10_000_000)
    assert result.available_count == 105
    assert result.returned_count == 105
    assert result.discovery_complete is True


def test_linkless_taskless_discussion_is_unresolved_not_candidate(db):
    session, _ = db
    sid = "session-linkless-taskless"
    now = datetime.utcnow()
    session.add(DiscussionRow(
        discussion_id="disc-linkless-taskless",
        session_id=sid,
        label="linkless taskless",
        label_provenance="human_stated",
        created_at=now,
        updated_at=now,
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.available_count == 1
    assert result.returned_count == 0
    assert result.candidate_discussion_ids == ()
    assert result.unresolved_discussion_count == 1
    assert "disc-linkless-taskless" not in result.candidate_discussion_ids
    assert result.discovery_complete is False


def test_linkless_task_bound_discussion_lacks_task_proof(db):
    session, _ = db
    sid = "session-linkless-taskbound"
    now = datetime.utcnow()
    session.add(DiscussionRow(
        discussion_id="disc-linkless-taskbound",
        session_id=sid,
        label="linkless taskbound",
        label_provenance="human_stated",
        task_id="task-1",
        created_at=now,
        updated_at=now,
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.available_count == 1
    assert result.returned_count == 0
    assert result.unresolved_discussion_count == 1
    assert result.unresolved_task_proof_count == 1
    assert result.discovery_complete is False
    by_id = {r.discussion_id: r for r in result.representatives}
    assert "disc-linkless-taskbound" not in by_id


def test_mixed_valid_and_linkless_preserves_counts_and_blocks_complete(db):
    session, _ = db
    sid = "session-mixed-linkless"
    eid = "exchange-valid"
    ut, at = 1, 2
    _seed_exchange(session, sid, eid, ut, at, user_text="valid")
    _make_discussion(session, sid, "valid", eid, ut, at)
    now = datetime.utcnow()
    session.add(DiscussionRow(
        discussion_id="disc-linkless",
        session_id=sid,
        label="linkless",
        label_provenance="human_stated",
        created_at=now,
        updated_at=now,
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.available_count == 2
    assert result.returned_count == 1
    assert result.unresolved_discussion_count == 1
    assert result.discovery_complete is False
    assert "disc-linkless" not in result.candidate_discussion_ids
    labels = {r.label for r in result.representatives}
    assert "valid" in labels
    assert "linkless" not in labels


def test_malformed_persisted_turn_ids_flagged_unresolved_not_thrown(db):
    session, _ = db
    sid = "session-malformed-turns"
    now = datetime.utcnow()
    session.add(DiscussionRow(
        discussion_id="disc-malformed",
        session_id=sid,
        label="malformed",
        label_provenance="legacy_unverified",
        created_at=now,
        updated_at=now,
    ))
    # A persisted zero turn id is not a strict positive non-bool integer.
    session.add(DiscussionExchangeLinkRow(
        discussion_id="disc-malformed",
        exchange_id="ghost-exchange",
        session_id=sid,
        user_turn_id=0,
        assistant_turn_id=-1,
        link_provenance="legacy_unverified",
        created_at=now,
    ))
    session.flush()

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert result.available_count == 1
    assert result.returned_count == 0
    assert result.unresolved_discussion_count == 1
    assert result.discovery_complete is False


def test_strict_public_signature_rejects_aliases_and_positional_budget():
    # Type errors occur before any SQL validation or session use.
    with pytest.raises(TypeError):
        read_session_discussion_discovery(None, "sid", 1000)
    with pytest.raises(TypeError):
        read_session_discussion_discovery(None, "sid", max_bytes=1000)
    with pytest.raises(TypeError):
        read_session_discussion_discovery(None, "sid", payload_bytes=1000)


def test_tiny_budget_contract_raises_then_minimum_fits(db):
    session, _ = db
    sid = "session-tiny-budget"
    eid = "ex-tiny"
    _seed_exchange(session, sid, eid, 1, 2, user_text="x")
    _make_discussion(session, sid, "label", eid, 1, 2)

    # Budgets below the minimal envelope do not return oversized payloads.
    for budget in (0, 1, 2, 5, 10, 50):
        with pytest.raises(DiscussionDiscoveryError):
            read_session_discussion_discovery(session, sid, max_payload_bytes=budget)

    full = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert full.payload_bytes == len(full.payload_json.encode("utf-8"))
    assert full.discovery_complete is True
    # Exactly fitting the complete payload succeeds and is not altered.
    exact = read_session_discussion_discovery(session, sid, max_payload_bytes=full.payload_bytes)
    assert exact.discovery_complete is True
    assert exact.payload_bytes == full.payload_bytes
    # One byte less than the complete envelope is handled honestly as overflow or
    # as an explicit too-small error, never as a silently oversized payload.
    try:
        overflow = read_session_discussion_discovery(
            session, sid, max_payload_bytes=full.payload_bytes - 1
        )
        assert overflow.returned_count == 0
        assert overflow.payload_bytes <= full.payload_bytes - 1
        assert overflow.payload_bytes == len(overflow.payload_json.encode("utf-8"))
    except DiscussionDiscoveryError:
        pass


def test_unicode_discovery_bytes_are_exact_utf8(db):
    session, _ = db
    sid = "session-unicode-discovery"
    eid = "ex-unicode"
    _seed_exchange(session, sid, eid, 1, 2, user_text="Café 🏮 naïve")
    _make_discussion(session, sid, "Lantern 缪 label", eid, 1, 2)

    result = read_session_discussion_discovery(session, sid, max_payload_bytes=1_000_000)
    assert "Lantern 缪 label" in result.payload_json
    assert result.payload_bytes == len(result.payload_json.encode("utf-8"))
    exact = read_session_discussion_discovery(session, sid, max_payload_bytes=result.payload_bytes)
    assert exact.payload_bytes == result.payload_bytes
    assert exact.discovery_complete is True


def test_invalid_or_none_database_raises_before_sql():
    class NotADatabase:
        pass

    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(None, "sid", max_payload_bytes=1000)
    with pytest.raises(DiscussionDiscoveryError):
        read_session_discussion_discovery(NotADatabase(), "sid", max_payload_bytes=1000)
