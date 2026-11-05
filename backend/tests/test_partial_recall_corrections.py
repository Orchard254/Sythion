"""Offline partial recall correction tests (disposable SQLite only)."""

import datetime
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.core.session import set_current_session_id
    from backend.api.dependencies import reset_grounded_discussion_provider
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from backend.services.conversation_runtime import ModelClient
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    from backend.services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
    from backend.models.database import Base, ConversationExchange, ConversationTurn
    from backend.services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
        canonical_exchange_input_digest,
    )
    from backend.services.partial_discussion_recall_linkage import (
        PartialDiscussionRecallLinkageService,
    )
    from backend.services.grounded_discussion_event_source_links import attach_event_source_link
except ImportError:  # pragma: no cover - alternate invocation layout
    from core.session import set_current_session_id
    from api.dependencies import reset_grounded_discussion_provider
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest
    from services.conversation_runtime import ModelClient
    from services.conversation_turn_engine import ConversationTurnEngine
    from services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
    from models.database import Base, ConversationExchange, ConversationTurn
    from services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
        canonical_exchange_input_digest,
    )
    from services.partial_discussion_recall_linkage import (
        PartialDiscussionRecallLinkageService,
    )
    from services.grounded_discussion_event_source_links import attach_event_source_link


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


@pytest.fixture(autouse=True)
def _session():
    sid = "partial-recall-corrections-session"
    set_current_session_id(sid)
    try:
        reset_grounded_discussion_provider()
    except Exception:
        pass
    yield
    set_current_session_id(sid)


def _seed_eligible_correction_evidence(session, sid):
    session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="tgt u"))
    session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="tgt a"))
    session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="src u"))
    session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="src a"))
    t0 = datetime.datetime(2026, 1, 1)
    t1 = datetime.datetime(2026, 1, 2)
    session.add(
        ConversationExchange(
            id="tgt-ex", session_id=sid, user_text="original user evidence text",
            assistant_text="original assistant evidence text", created_at=t0,
        )
    )
    session.add(
        ConversationExchange(
            id="src-ex", session_id=sid, user_text="later correction source text",
            assistant_text="later correction source answer", created_at=t1,
        )
    )
    session.commit()

    mgr = DiscussionIndexManager(session, db=session)
    for exchange_id, uid, aid in (("tgt-ex", 1, 2), ("src-ex", 3, 4)):
        digest = canonical_exchange_input_digest(session, sid, exchange_id, uid, aid)
        row, _ = mgr.receive(sid, exchange_id, uid, aid, input_digest=digest, status="pending")
        mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
    session.commit()

    gm = GroundedDiscussionManager(session)
    discussion = gm.create_discussion(
        session_id=sid,
        label="Partial Recall Correction Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "tgt-ex", "user_turn_id": 1, "assistant_turn_id": 2},
            {"exchange_id": "src-ex", "user_turn_id": 3, "assistant_turn_id": 4},
        ],
        link_provenance="human_stated",
    )
    session.commit()
    did = discussion.discussion_id

    event = gm.add_correction(
        did, sid,
        target_exchange_id="tgt-ex",
        target_user_turn_id=1,
        target_assistant_turn_id=2,
        correction_text="later correction event text",
        provenance="human_stated",
    )
    session.commit()
    attach_event_source_link(
        session, event_id=event.id, session_id=sid, discussion_id=did,
        source_exchange_id="src-ex", source_user_turn_id=3, source_assistant_turn_id=4,
        target_exchange_id="tgt-ex", target_user_turn_id=1, target_assistant_turn_id=2,
        provenance="human_stated", commit=False,
    )
    session.commit()
    return did


def _seed_unresolved_correction(session, sid):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id="exchange-1", session_id=sid,
            user_text="original user evidence text",
            assistant_text="original assistant evidence text",
        )
    )
    session.commit()

    gm = GroundedDiscussionManager(session)
    discussion = gm.create_discussion(
        session_id=sid,
        label="Unresolved Correction Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
        link_provenance="human_stated",
    )
    session.commit()
    did = discussion.discussion_id
    gm.add_correction(
        did, sid,
        target_exchange_id="exchange-1",
        target_user_turn_id=11,
        target_assistant_turn_id=12,
        correction_text="unresolved later correction text",
        provenance="human_stated",
    )
    session.commit()
    return did


@pytest.mark.asyncio
async def test_partial_recall_correction_event_reaches_final_evidence(db_session, monkeypatch):
    session = db_session
    sid = "partial-recall-corrections-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible_correction_evidence(session, sid)

    captured_calls = []

    class ScriptedModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            messages = k.get("messages", a[0] if a else [])
            captured_calls.append([dict(m) for m in (messages or [])])
            text = " ".join(m.get("content", "") for m in (messages or []))
            if "identify which grounded discussions" in text.lower() or "identify which" in text.lower():
                return json.dumps(
                    {
                        "outcome": "selected",
                        "selected_discussion_ids": [candidate_id],
                        "clarification": None,
                    }
                )
            return json.dumps(
                {
                    "assistant_text": "Recalled original evidence.",
                    "commands": [],
                }
            )

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(
        client_request_id=f"partial-correction-{uuid.uuid4()}", text="what did we discuss?"
    )
    resp = await assistant_turn(req, bg, session, None, svc)

    assert resp.background_task_ids == []
    assert resp.assistant_text.startswith(
        "This recall may be incomplete; other conversations or later "
        "corrections may exist.\n\n"
    )
    assert "Recalled original evidence." in resp.assistant_text

    assert len(captured_calls) >= 2
    final_payload = json.dumps(captured_calls[-1])
    assert "original user evidence text" in final_payload
    assert "later correction event text" in final_payload
    assert "later correction source text" in final_payload


@pytest.mark.asyncio
async def test_unresolved_correction_or_insufficient_index_blocks_without_interactive_call(db_session, monkeypatch):
    import backend.services.conversation_runtime as crt

    session = db_session
    sid = "partial-recall-corrections-session"
    set_current_session_id(sid)
    _seed_unresolved_correction(session, sid)

    model_calls = {"chat": 0}

    class SafetyModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            model_calls["chat"] += 1
            raise AssertionError("model must not be invoked")

    monkeypatch.setattr(crt, "ModelClient", SafetyModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(
        client_request_id=f"unresolved-correction-{uuid.uuid4()}", text="hello"
    )
    resp = await assistant_turn(req, bg, session, None, svc)

    assert model_calls["chat"] == 0
    assert resp.background_task_ids == []
    assert resp.assistant_text == ConversationTurnEngine._INSUFFICIENT_DISCOVERY_ASSISTANT_TEXT
