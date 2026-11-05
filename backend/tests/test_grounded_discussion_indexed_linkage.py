"""Tests for default-inactive indexed grounded discussion service adapter."""

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.models.database import Base, ConversationExchange, ConversationTurn
    from backend.core.session import set_current_session_id
    from backend.services.grounded_discussions import Base as GroundedBase
    from backend.services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
        INDEX_STATUS_PENDING,
    )
    from backend.services.grounded_discussion_indexed_discovery import (
        read_session_indexed_discussion_discovery,
    )
    from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
    from backend.services.grounded_discussion_indexed_linkage import (
        GroundedDiscussionIndexedLinkageService,
    )
except ImportError:  # pragma: no cover - alternate root
    from models.database import Base, ConversationExchange, ConversationTurn
    from core.session import set_current_session_id
    from services.grounded_discussions import Base as GroundedBase
    from services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
        INDEX_STATUS_PENDING,
    )
    from services.grounded_discussion_indexed_discovery import (
        read_session_indexed_discussion_discovery,
    )
    from services.grounded_discussion_discovery import read_session_discussion_discovery
    from services.grounded_discussion_evidence import read_selected_discussion_evidence
    from services.grounded_discussion_indexed_linkage import (
        GroundedDiscussionIndexedLinkageService,
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


def _setup_exchange(session, sid, exchange_id="exchange-1", uid=11, aid=12):
    session.add(ConversationTurn(id=uid, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=aid, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id=exchange_id, session_id=sid, user_text="u", assistant_text="a"))
    session.commit()


def _make_complete_state(session, sid, eid, uid, aid, digest="digest-1"):
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, eid, uid, aid, input_digest=digest, status=INDEX_STATUS_COMPLETE)
    if False:
        pass
    mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=False)
    session.commit()


def test_adapter_uses_indexed_not_raw(db_session, monkeypatch):
    sid = "session-adapter-indexed"
    set_current_session_id(sid)
    sentinel = object()
    calls = {"indexed": 0, "raw": 0}

    def fake_indexed(db, session_id, **kwargs):
        calls["indexed"] += 1
        assert kwargs.get("source_freshness_verifier") is None
        return sentinel

    def fake_raw(*args, **kwargs):
        calls["raw"] += 1
        raise AssertionError("raw discovery must not be called")

    monkeypatch.setattr(
        "backend.services.grounded_discussion_indexed_discovery.read_session_indexed_discussion_discovery",
        fake_indexed,
    )
    monkeypatch.setattr(
        "backend.services.grounded_discussion_discovery.read_session_discussion_discovery",
        fake_raw,
    )

    service = GroundedDiscussionIndexedLinkageService(db_session)
    result = service.discover_session_candidates(db_session, sid)
    assert result is sentinel
    assert calls["indexed"] == 1
    assert calls["raw"] == 0

    # discover alias should also route to indexed.
    result2 = service.discover(db_session, sid)
    assert result2 is sentinel
    assert calls["indexed"] == 2


def test_unknown_freshness_remains_incomplete(db_session):
    sid = "session-unknown-freshness"
    set_current_session_id(sid)
    _setup_exchange(db_session, sid)
    _make_complete_state(db_session, sid, "exchange-1", 11, 12)

    service = GroundedDiscussionIndexedLinkageService(db_session)
    result = service.discover_session_candidates(
        db_session, sid, max_payload_bytes=4096
    )
    assert getattr(result, "discovery_complete", None) is False
    assert getattr(result, "reason", None) == "unresolved_canonical_source"


def test_evidence_reader_retained(db_session, monkeypatch):
    sid = "session-evidence-retained"
    set_current_session_id(sid)

    marker = {"called": False}

    def fake_read_selected(db, sid_arg, selected_ids):
        marker["called"] = True
        return ("evidence", list(selected_ids))

    monkeypatch.setattr(
        "backend.services.grounded_discussion_evidence.read_selected_discussion_evidence",
        fake_read_selected,
    )

    service = GroundedDiscussionIndexedLinkageService(db_session)
    reader = service.make_evidence_reader(db_session, sid)
    assert reader([42]) == ("evidence", [42])
    assert marker["called"] is True

    assert service.read_discussion_evidence(db_session, sid, selected_ids=[7]) == ("evidence", [7])


def test_verifier_is_ignored_even_if_supplied(db_session, monkeypatch):
    sid = "session-verifier-ignored"
    set_current_session_id(sid)

    captured = {}

    def fake_indexed(db, session_id, **kwargs):
        captured.update(kwargs)
        return object()

    monkeypatch.setattr(
        "backend.services.grounded_discussion_indexed_discovery.read_session_indexed_discussion_discovery",
        fake_indexed,
    )

    service = GroundedDiscussionIndexedLinkageService(
        db_session, source_freshness_verifier=lambda *a, **k: True
    )
    assert service.source_freshness_verifier is None
    service.discover_session_candidates(
        db_session, sid, source_freshness_verifier=lambda *a, **k: True
    )
    assert captured.get("source_freshness_verifier") is None