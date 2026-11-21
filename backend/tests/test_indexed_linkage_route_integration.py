"""Offline route integration for indexed grounded discussion linkage (no fake engine)."""

import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.models.database import Base, ConversationExchange, ConversationTurn, Task
    from backend.core.session import set_current_session_id
    from backend.api.dependencies import reset_grounded_discussion_provider
    from backend.api.routes import assistant_turn, _resolve_grounded_discussion_service
    from backend.api.schemas import AssistantTurnRequest
    from backend.services.conversation_runtime import ConversationRuntime, ModelClient
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    from backend.services.grounded_discussions import Base as GroundedBase
    from backend.services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
    )
    from backend.services.grounded_discussion_indexed_linkage import (
        GroundedDiscussionIndexedLinkageService,
    )
except ImportError:  # pragma: no cover - alternate root
    from models.database import Base, ConversationExchange, ConversationTurn, Task
    from core.session import set_current_session_id
    from api.dependencies import reset_grounded_discussion_provider
    from api.routes import assistant_turn, _resolve_grounded_discussion_service
    from api.schemas import AssistantTurnRequest
    from services.conversation_runtime import ConversationRuntime, ModelClient
    from services.conversation_turn_engine import ConversationTurnEngine
    from services.grounded_discussions import Base as GroundedBase
    from services.grounded_discussion_index import (
        DiscussionIndexManager,
        INDEX_STATUS_COMPLETE,
    )
    from services.grounded_discussion_indexed_linkage import (
        GroundedDiscussionIndexedLinkageService,
    )


@pytest.fixture
def db_session():
    import backend.models.database  # noqa: F401
    import backend.services.grounded_discussions  # noqa: F401
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


def _reset():
    set_current_session_id("indexed-linkage-route-session")


@pytest.fixture(autouse=True)
def _session():
    _reset()
    try:
        reset_grounded_discussion_provider()
    except Exception:
        pass
    yield
    _reset()


def _setup_fictional_data(session, sid):
    uid = 311
    aid = 312
    session.add(ConversationTurn(id=uid, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=aid, session_id=sid, role="assistant", summary="a"))
    session.add(ConversationExchange(id="indexed-exchange", session_id=sid, user_text="u", assistant_text="a"))
    session.flush()
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, "indexed-exchange", uid, aid, input_digest="digest-indexed", status=INDEX_STATUS_COMPLETE)
    mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=False)
    session.commit()


@pytest.mark.asyncio
async def test_default_inactive_resolution_does_not_install_service(db_session, monkeypatch):
    assert _resolve_grounded_discussion_service(db_session, None) is None

    import backend.services.conversation_runtime as crt

    class WorkingModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            return json.dumps({"assistant_text": "Default route answer.", "commands": []})

    monkeypatch.setattr(crt, "ModelClient", WorkingModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    session = db_session
    sid = "indexed-linkage-route-session"
    set_current_session_id(sid)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(client_request_id=f"default-indexed-{uuid.uuid4()}", text="hello")
    resp = await assistant_turn(req, bg, session, None)

    assert resp.assistant_text == "Default route answer."
    assert resp.status == "completed"
    assert session.query(ConversationExchange).count() == 1
    try:
        from backend.services.grounded_discussions import DiscussionRow

        assert session.query(DiscussionRow).count() == 0
    except Exception:
        pass


@pytest.mark.asyncio
async def test_real_engine_injected_indexed_unknown_freshness_is_insufficient_without_model(
    db_session, monkeypatch
):
    import backend.services.conversation_runtime as crt

    model_calls = {"chat": 0}

    class SafetyModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            model_calls["chat"] += 1
            raise AssertionError("model must not be invoked")

    monkeypatch.setattr(crt, "ModelClient", SafetyModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    session = db_session
    sid = "indexed-linkage-route-session"
    set_current_session_id(sid)
    _setup_fictional_data(session, sid)

    svc = GroundedDiscussionIndexedLinkageService(session)
    captured = {}

    original = svc.discover_session_candidates

    def spy(db, session_id, **kwargs):
        result = original(db, session_id, **kwargs)
        captured["discovery"] = result
        return result

    svc.discover_session_candidates = spy

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(client_request_id=f"indexed-{uuid.uuid4()}", text="hello")
    resp = await assistant_turn(req, bg, session, None, svc)

    assert captured["discovery"].discovery_complete is False
    assert resp.assistant_text == ""
    assert resp.status_text == ConversationTurnEngine._INSUFFICIENT_DISCOVERY_ASSISTANT_TEXT
    assert resp.background_task_ids == []
    assert model_calls["chat"] == 0