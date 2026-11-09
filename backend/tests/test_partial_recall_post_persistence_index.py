"""Offline tests for narrowly opt-in post-persistence semantic indexing."""

import asyncio
import json
import logging
import uuid
from types import SimpleNamespace

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.core.session import set_current_session_id
    from backend.api.dependencies import reset_grounded_discussion_provider
    from backend.api.routes import assistant_turn, _resolve_grounded_discussion_service, _run_partial_recall_post_persistence_semantic_index
    from backend.api.schemas import AssistantTurnRequest
    from backend.services.conversation_runtime import ConversationRuntime, ModelClient
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
except ImportError:  # pragma: no cover
    from core.session import set_current_session_id
    from api.dependencies import reset_grounded_discussion_provider
    from api.routes import assistant_turn, _resolve_grounded_discussion_service, _run_partial_recall_post_persistence_semantic_index
    from api.schemas import AssistantTurnRequest
    from services.conversation_runtime import ConversationRuntime, ModelClient
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


@pytest.fixture
def file_db(tmp_path):
    db_path = tmp_path / "partial-index.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session, engine
    finally:
        session.close()
        engine.dispose()


@pytest.fixture(autouse=True)
def _session():
    sid = "partial-index-session"
    set_current_session_id(sid)
    try:
        reset_grounded_discussion_provider()
    except Exception:
        pass
    yield
    set_current_session_id(sid)


def _seed_eligible(session, sid):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id="exchange-1",
            session_id=sid,
            user_text="original user evidence text",
            assistant_text="original assistant evidence text",
        )
    )
    session.commit()
    digest = canonical_exchange_input_digest(session, sid, "exchange-1", 11, 12)
    mgr = DiscussionIndexManager(session, db=session)
    row, _ = mgr.receive(sid, "exchange-1", 11, 12, input_digest=digest, status="pending")
    mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
    session.commit()
    gm = GroundedDiscussionManager(session)
    discussion = gm.create_discussion(
        session_id=sid,
        label="Partial Recall Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
        link_provenance="human_stated",
    )
    session.commit()
    return discussion.discussion_id


class ScriptedModelClient:
    _candidate_id = None

    def __init__(self, *a, **k):
        pass

    async def chat(self, *a, **k):
        messages = a[0] if a else k.get("messages", [])
        text = " ".join(m.get("content", "") for m in (messages or []))
        if "identify which grounded discussions" in text.lower() or "identify which" in text.lower():
            selected = [ScriptedModelClient._candidate_id] if ScriptedModelClient._candidate_id else []
            return json.dumps(
                {
                    "outcome": "selected",
                    "selected_discussion_ids": selected,
                    "clarification": None,
                }
            )
        return json.dumps(
            {
                "assistant_text": "Normal completion answer.",
                "commands": [],
            }
        )


class IndexModelClient:
    calls = 0

    def __init__(self, *a, **k):
        pass

    async def chat(self, *a, **k):
        IndexModelClient.calls += 1
        return json.dumps(
            {
                "status": "insufficient",
                "proposals": [],
                "clarification": "Need more",
                "reason": "sparse",
            }
        )


class SafetyModelClient:
    def __init__(self, *a, **k):
        pass

    async def chat(self, *a, **k):
        raise AssertionError("model must not be invoked")


@pytest.mark.asyncio
async def test_normal_completion_schedules_index(file_db, monkeypatch):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible(session, sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    # Patch the scripted client to select the eligible discussion.
    ScriptedModelClient._candidate_id = candidate_id

    req = AssistantTurnRequest(
        client_request_id=f"normal-{uuid.uuid4()}", text="what did we discuss?"
    )
    bg = BackgroundTasks()
    resp = await assistant_turn(req, bg, session, None, svc)

    assert resp.status == "completed"
    # Opt-in scheduling adds exactly one post-persistence indexing task.
    assert len(bg.tasks) == 1


@pytest.mark.asyncio
async def test_default_no_service_does_not_schedule(file_db, monkeypatch):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    req = AssistantTurnRequest(
        client_request_id=f"default-{uuid.uuid4()}", text="hello"
    )
    bg = BackgroundTasks()
    resp = await assistant_turn(req, bg, session, None, None)

    assert resp.status == "completed"
    assert len(bg.tasks) == 0


@pytest.mark.asyncio
async def test_insufficient_discovery_text_does_not_schedule(file_db, monkeypatch):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", SafetyModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    def boom(*a, **k):
        raise RuntimeError("discovery failed")

    svc.discover_session_candidates = boom
    req = AssistantTurnRequest(
        client_request_id=f"insufficient-{uuid.uuid4()}", text="hello"
    )
    bg = BackgroundTasks()
    resp = await assistant_turn(req, bg, session, None, svc)

    assert resp.assistant_text == ConversationTurnEngine._INSUFFICIENT_DISCOVERY_ASSISTANT_TEXT
    assert len(bg.tasks) == 0


@pytest.mark.asyncio
async def test_failed_persistence_does_not_schedule_or_index(file_db, monkeypatch):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    async def boom(*a, **k):
        raise RuntimeError("persist failed")

    monkeypatch.setattr(
        "backend.services.conversation_runtime.ConversationRuntime._persist_conversation",
        boom,
    )

    svc = PartialDiscussionRecallLinkageService(session)
    req = AssistantTurnRequest(
        client_request_id=f"persist-fail-{uuid.uuid4()}", text="hello"
    )
    bg = BackgroundTasks()
    with pytest.raises(Exception):
        await assistant_turn(req, bg, session, None, svc)
    assert len(bg.tasks) == 0


@pytest.mark.asyncio
async def test_failed_indexing_does_not_affect_response(file_db, monkeypatch):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible(session, sid)

    import backend.services.conversation_runtime as crt

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    ScriptedModelClient._candidate_id = candidate_id

    async def raising_helper(*a, **k):
        raise RuntimeError("indexing exploded")

    monkeypatch.setattr(
        "backend.api.routes._run_partial_recall_post_persistence_semantic_index",
        raising_helper,
    )

    svc = PartialDiscussionRecallLinkageService(session)
    req = AssistantTurnRequest(
        client_request_id=f"index-fail-{uuid.uuid4()}", text="what did we discuss?"
    )
    bg = BackgroundTasks()
    resp = await assistant_turn(req, bg, session, None, svc)
    assert resp.status == "completed"
    try:
        await bg()
    except Exception:
        pass
    assert "Normal completion answer." in resp.assistant_text


@pytest.mark.asyncio
async def test_real_hook_contains_orchestrator_failure(file_db, monkeypatch, caplog):
    session, engine = file_db
    sid = "partial-index-session"
    candidate_id = _seed_eligible(session, sid)

    import backend.services.conversation_runtime as crt
    import backend.services.grounded_discussion_server_owned_semantic_index as server_index
    from backend.models.database import Task
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptRow

    ScriptedModelClient._candidate_id = candidate_id
    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)
    calls = []
    secret = "SECRET_EXCEPTION_CONTENT_9f4d"

    async def failing_orchestrator(*args, **kwargs):
        calls.append((args, kwargs))
        raise RuntimeError(secret)

    monkeypatch.setattr(server_index, "run_server_owned_semantic_index", failing_orchestrator)
    bg = BackgroundTasks()
    response = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"real-failure-{uuid.uuid4()}", text="what did we discuss?"),
        bg, session, None, PartialDiscussionRecallLinkageService(session),
    )
    assert response.status == "completed"
    assert "Normal completion answer." in response.assistant_text
    assert len(bg.tasks) == 1
    scheduled = bg.tasks[0]
    assert scheduled.func is _run_partial_recall_post_persistence_semantic_index
    assert scheduled.kwargs["receipt_id"] == response.composer_receipt_id
    session.commit()
    response_before = response.dict()
    receipt = session.query(ComposerRequestReceiptRow).filter_by(request_id=response.composer_receipt_id, session_id=sid).one()
    assert receipt.status == "resolved"
    tasks_before = [tuple(getattr(task, column.name) for column in Task.__table__.columns)
                    for task in session.query(Task).order_by(Task.id).all()]
    continued = []
    bg.add_task(continued.append, "ran")

    with caplog.at_level(logging.INFO):
        await bg()  # The real hook must contain the exception without a test-side catch.

    assert len(calls) == 1
    assert calls[0][0][1] is scheduled.args[6]
    assert continued == ["ran"]
    assert bg.tasks[0] is scheduled
    assert response.dict() == response_before
    session.expire_all()
    assert receipt.status == "resolved"
    assert [tuple(getattr(task, column.name) for column in Task.__table__.columns)
            for task in session.query(Task).order_by(Task.id).all()] == tasks_before
    assert "orchestrator_error" in caplog.text
    assert secret not in caplog.text
    assert all(secret not in repr(record.__dict__) for record in caplog.records)


@pytest.mark.asyncio
async def test_route_committed_receipt_allows_indexing_before_explicit_session_commit(file_db, monkeypatch, caplog):
    session, engine = file_db
    sid = "partial-index-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible(session, sid)

    import backend.services.conversation_runtime as crt

    ScriptedModelClient._candidate_id = candidate_id
    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    req = AssistantTurnRequest(
        client_request_id=f"durable-{uuid.uuid4()}", text="what did we discuss?"
    )
    bg = BackgroundTasks()
    resp = await assistant_turn(req, bg, session, None, svc)
    assert resp.status == "completed"
    assert len(bg.tasks) == 1
    scheduled = bg.tasks[0]
    assert scheduled.func is _run_partial_recall_post_persistence_semantic_index
    assert scheduled.kwargs["receipt_id"] == resp.composer_receipt_id
    scheduled_client = scheduled.args[6]
    assert isinstance(scheduled_client, ScriptedModelClient)

    import backend.services.grounded_discussion_server_owned_semantic_index as server_index

    orchestrator = server_index.run_server_owned_semantic_index
    orchestrator_calls = []

    async def tracked_orchestrator(*args, **kwargs):
        orchestrator_calls.append((args, kwargs))
        return await orchestrator(*args, **kwargs)

    IndexModelClient.calls = 0
    monkeypatch.setattr(scheduled_client, "chat", IndexModelClient().chat)
    monkeypatch.setattr(server_index, "run_server_owned_semantic_index", tracked_orchestrator)

    # The route commits the resolved durable receipt before scheduling, so the
    # first background run verifies that committed receipt and invokes indexing
    # immediately; an outer/secondary commit is not required for eligibility.
    with caplog.at_level(logging.INFO, logger="backend.api.routes"):
        await bg()
    assert len(orchestrator_calls) == 1
    assert IndexModelClient.calls == 1
    assert "insufficient" in caplog.text

    # The same scheduled task can be rerun against the already committed durable
    # receipt/client without retrying the assistant request.
    session.commit()
    await bg()
    assert len(orchestrator_calls) == 2
    assert orchestrator_calls[0][0][1] is scheduled_client
    assert IndexModelClient.calls == 2


@pytest.mark.asyncio
async def test_real_capture_failure_logs_no_row_without_provider(file_db, monkeypatch, caplog):
    session, engine = file_db
    sid = "partial-index-session"
    candidate_id = _seed_eligible(session, sid)
    import backend.services.conversation_runtime as crt
    from backend.services.grounded_discussion_index import DiscussionIndexStateRow

    ScriptedModelClient._candidate_id = candidate_id
    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)
    bg = BackgroundTasks()
    response = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"capture-{uuid.uuid4()}", text="what did we discuss?"),
        bg, session, None, PartialDiscussionRecallLinkageService(session),
    )
    assert response.status == "completed"
    assert len(bg.tasks) == 1
    session.commit()
    scheduled = bg.tasks[0]
    args = list(scheduled.args)
    args[2] = "missing-exchange-SECRET"
    args[6] = SafetyModelClient()
    with caplog.at_level(logging.INFO, logger="backend.api.routes"):
        await scheduled.func(*args, **scheduled.kwargs)
    assert "reason=capture_failed state=none provider_calls=0" in caplog.text
    assert "SECRET" not in caplog.text
    assert session.query(DiscussionIndexStateRow).filter_by(exchange_id=args[2]).count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result,expected",
    [
        (SimpleNamespace(rejected_reason="capture_failed", state_status=None, provider_call_count=0),
         "reason=capture_failed state=none provider_calls=0"),
        (SimpleNamespace(rejected_reason=None, state_status="insufficient", provider_call_count=1),
         "reason=none state=insufficient provider_calls=1"),
        (SimpleNamespace(rejected_reason="SECRET_REASON", state_status="SECRET_STATUS", provider_call_count=42),
         "reason=other state=other provider_calls=other"),
        (RuntimeError("SECRET_EXCEPTION"), "orchestrator_error"),
    ],
)
async def test_index_outcome_diagnostics_are_bounded(file_db, monkeypatch, caplog, result, expected):
    session, engine = file_db
    sid = "partial-index-session"
    candidate_id = _seed_eligible(session, sid)
    import backend.services.conversation_runtime as crt
    import backend.services.grounded_discussion_server_owned_semantic_index as server_index

    ScriptedModelClient._candidate_id = candidate_id
    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)
    calls = []

    async def fake_index(*args, **kwargs):
        calls.append(1)
        if isinstance(result, Exception):
            raise result
        return result

    monkeypatch.setattr(server_index, "run_server_owned_semantic_index", fake_index)
    bg = BackgroundTasks()
    response = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"diagnostic-{uuid.uuid4()}", text="what did we discuss?"),
        bg, session, None, PartialDiscussionRecallLinkageService(session),
    )
    assert response.status == "completed"
    assert len(bg.tasks) == 1
    session.commit()
    with caplog.at_level(logging.INFO, logger="backend.api.routes"):
        await bg()
    assert calls == [1]
    assert expected in caplog.text
    assert "SECRET_" not in caplog.text
    assert "what did we discuss?" not in caplog.text
    assert response.composer_receipt_id not in caplog.text
