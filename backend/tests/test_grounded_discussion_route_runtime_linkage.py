"""Disabled-by-default route/runtime grounded discussion linkage integration tests."""

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.models.database import Base, ConversationExchange, ConversationTurn, Task
    from backend.core.session import set_current_session_id
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from backend.services.conversation_runtime import ConversationRuntime
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    from backend.services.conversation_runtime import ModelClient
    from backend.services.grounded_discussions import (
        Base as GroundedBase,
        GroundedDiscussionManager,
        DiscussionRow,
        DiscussionExchangeLinkRow,
    )
    from backend.services.grounded_discussion_linkage import GroundedDiscussionLinkageService
except ImportError:  # pragma: no cover - alternate root
    from models.database import Base, ConversationExchange, ConversationTurn, Task
    from core.session import set_current_session_id
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest
    from services.conversation_runtime import ConversationRuntime
    from services.conversation_turn_engine import ConversationTurnEngine
    from services.conversation_runtime import ModelClient
    from services.grounded_discussions import (
        Base as GroundedBase,
        GroundedDiscussionManager,
        DiscussionRow,
        DiscussionExchangeLinkRow,
    )
    from services.grounded_discussion_linkage import GroundedDiscussionLinkageService


class FakeEngine:
    def __init__(self, model_client=None, discussion_evidence_reader=None):
        self.model_client = model_client
        self.discussion_evidence_reader = discussion_evidence_reader

    async def run_turn(self, user_text, recent_turns=None, clarification_descriptors=None, **kwargs):
        return {
            "assistant_text": "Grounded linkage answer.",
            "commands": [],
        }


@pytest.fixture
def db_session():
    import backend.models.database  # noqa: F401
    import backend.services.grounded_discussions  # noqa: F401
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _reset():
    from backend.core.session import set_current_session_id
    set_current_session_id("route-runtime-linkage-session")


@pytest.fixture(autouse=True)
def _session():
    _reset()
    yield
    _reset()


@pytest.mark.asyncio
async def test_runtime_linkage_validates_and_persists_within_transaction(db_session):
    sid = "route-runtime-linkage-session"
    session = db_session
    ex_id = "link-exchange"
    svc = GroundedDiscussionLinkageService(session)
    proposal = {
        "label": "Provisional Lantern",
        "label_provenance": "explicit_validated_proposal",
        "link_provenance": "explicit_validated_proposal",
        "correction_text": "Correct the museum budget.",
    }
    runtime = ConversationRuntime(session, grounded_discussion_service=svc)
    identity = await runtime._persist_conversation(
        "user text",
        "assistant text",
        ex_id,
        status="completed",
        commit=False,
        grounded_discussion_service=svc,
        grounded_discussion_linkage=proposal,
    )
    uid = identity.user_turn_id
    aid = identity.assistant_turn_id
    session.commit()
    # Canonical identity remains exact.
    assert identity.exchange_id == ex_id
    assert isinstance(uid, int) and isinstance(aid, int)
    assert session.query(DiscussionRow).count() == 1
    row = session.query(DiscussionRow).one()
    assert row.session_id == sid
    link = session.query(DiscussionExchangeLinkRow).one()
    assert (link.exchange_id, link.user_turn_id, link.assistant_turn_id) == (ex_id, uid, aid)
    # Proposed correction event was persisted as explicit provenance.
    assert session.query(__import__("backend.services.grounded_discussions", fromlist=["DiscussionEventRow"]).DiscussionEventRow).count() >= 1


@pytest.mark.asyncio
async def test_runtime_linkage_failure_rolls_back_grounded_and_canonical_rows(db_session):
    sid = "route-runtime-linkage-session"
    session = db_session
    ex_id = "bad-link-exchange"
    svc = GroundedDiscussionLinkageService(session)
    bad_proposal = {
        "label": "Bad",
        "label_provenance": "totally_invalid_provenance",
    }
    runtime = ConversationRuntime(session, grounded_discussion_service=svc)
    with pytest.raises(RuntimeError):
        await runtime._persist_conversation(
            "u",
            "a",
            ex_id,
            commit=True,
            grounded_discussion_service=svc,
            grounded_discussion_linkage=bad_proposal,
        )
    # Both grounded rows and the newly-attempted canonical exchange remain absent.
    assert session.query(DiscussionRow).count() == 0
    assert session.query(DiscussionExchangeLinkRow).count() == 0
    # The canonical exchange from this attempt is rolled back too.
    assert session.query(ConversationExchange).filter_by(id=ex_id).count() == 0


@pytest.mark.asyncio
async def test_route_answer_only_isolated_linkage(db_session, monkeypatch):
    import backend.services.conversation_turn_engine as cte
    import backend.services.conversation_runtime as crt
    monkeypatch.setattr(cte, "ConversationTurnEngine", FakeEngine)
    monkeypatch.setattr(crt, "ModelClient", lambda *a, **k: object())
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    session = db_session
    sid = "route-runtime-linkage-session"
    from backend.core.session import set_current_session_id
    set_current_session_id(sid)

    # Route no longer reads ambient service proposal authority.
    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    req = AssistantTurnRequest(client_request_id=f"route-link-{uuid.uuid4()}", text="hello")
    resp = await assistant_turn(req, bg, session, None)
    assert resp.assistant_text == "Grounded linkage answer."
    assert resp.status == "completed"
    # Canonical conversation persisted, but no ambient linkage occurred.
    assert session.query(ConversationExchange).count() == 1
    assert session.query(DiscussionRow).count() == 0


@pytest.mark.asyncio
async def test_default_disabled_no_grounded_linkage_or_import_side_effect(db_session, monkeypatch):
    import backend.services.conversation_turn_engine as cte
    import backend.services.conversation_runtime as crt
    monkeypatch.setattr(cte, "ConversationTurnEngine", FakeEngine)
    monkeypatch.setattr(crt, "ModelClient", lambda *a, **k: object())
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    session = db_session
    sid = "route-runtime-linkage-session"
    from backend.core.session import set_current_session_id
    set_current_session_id(sid)

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    req = AssistantTurnRequest(client_request_id=f"default-{uuid.uuid4()}", text="hello")
    resp = await assistant_turn(req, bg, session, None)
    assert resp.status == "completed"
    assert session.query(ConversationExchange).count() == 1
    assert session.query(DiscussionRow).count() == 0
    assert session.query(DiscussionExchangeLinkRow).count() == 0

@pytest.mark.asyncio
async def test_operation_savepoint_preserves_unrelated_pending_row(db_session):
    sid = "route-runtime-linkage-session"
    session = db_session
    svc = GroundedDiscussionLinkageService(session)
    session.add(ConversationTurn(id=99, session_id=sid, role="assistant", summary="pending"))
    session.flush()
    bad_proposal = {"label": "Bad", "label_provenance": "totally_invalid_provenance"}
    runtime = ConversationRuntime(session, grounded_discussion_service=svc)
    with pytest.raises(RuntimeError):
        await runtime._persist_conversation(
            "u", "a", "savepoint-exchange",
            commit=False, grounded_discussion_service=svc,
            grounded_discussion_linkage=bad_proposal,
        )
    assert session.query(ConversationExchange).count() == 0
    assert session.query(DiscussionRow).count() == 0
    assert session.query(ConversationTurn).filter_by(id=99).count() == 1
    session.commit()
    assert session.query(ConversationTurn).filter_by(id=99).count() == 1
