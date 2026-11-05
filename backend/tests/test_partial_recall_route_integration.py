"""Offline route integration for partial (read-only limited) discussion recall (no fake engine)."""

import asyncio
import json
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.core.session import set_current_session_id
    from backend.api.dependencies import reset_grounded_discussion_provider
    from backend.api.routes import assistant_turn, _resolve_grounded_discussion_service
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
    from backend.services.grounded_discussion_discovery import DiscussionDiscoveryResult
except ImportError:  # pragma: no cover - alternate invocation layout
    from core.session import set_current_session_id
    from api.dependencies import reset_grounded_discussion_provider
    from api.routes import assistant_turn, _resolve_grounded_discussion_service
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
    from services.grounded_discussion_discovery import DiscussionDiscoveryResult


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
    sid = "partial-recall-route-session"
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
    sid = "partial-recall-route-session"
    set_current_session_id(sid)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(client_request_id=f"default-partial-{uuid.uuid4()}", text="hello")
    resp = await assistant_turn(req, bg, session, None)

    assert resp.assistant_text == "Default route answer."
    assert resp.status == "completed"
    assert resp.background_task_ids == []


@pytest.mark.asyncio
async def test_real_engine_partial_recall_eligible_returns_original_evidence_and_caveat(db_session, monkeypatch):
    import backend.services.conversation_runtime as crt
    import backend.services.grounded_discussion_evidence as ev_mod

    session = db_session
    sid = "partial-recall-route-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible(session, sid)

    captured = {"selected_ids": None, "evidence": None}

    class ScriptedModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            messages = a[0] if a else k.get("messages", [])
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
                    "commands": [{"type": "start_task", "request": "delete a file"}],
                }
            )

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    original_evidence = ev_mod.read_selected_discussion_evidence

    def spy_evidence(sess, sid_arg, selected_ids, *a, **k):
        result = original_evidence(sess, sid_arg, selected_ids, *a, **k)
        captured["selected_ids"] = list(selected_ids)
        captured["evidence"] = result
        return result

    monkeypatch.setattr(ev_mod, "read_selected_discussion_evidence", spy_evidence)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(
        client_request_id=f"partial-{uuid.uuid4()}", text="what did we discuss?"
    )
    resp = await assistant_turn(req, bg, session, None, svc)

    assert resp.assistant_text.startswith(
        "This recall may be incomplete; other conversations or later "
        "corrections may exist.\n\n"
    )
    assert "Recalled original evidence." in resp.assistant_text
    assert resp.background_task_ids == []
    assert resp.status == "completed"

    assert captured["selected_ids"] == [candidate_id]
    evidence = captured["evidence"]
    assert evidence is not None
    entry = evidence.entries[0]
    member = entry.members[0]
    assert member.canonical_user_text == "original user evidence text"
    assert member.canonical_assistant_text == "original assistant evidence text"


@pytest.mark.asyncio
async def test_partial_recall_incomplete_index_returns_no_interactive_calls(db_session, monkeypatch):
    import backend.services.conversation_runtime as crt

    session = db_session
    sid = "partial-recall-route-session"
    set_current_session_id(sid)
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id="exchange-incomplete",
            session_id=sid,
            user_text="incomplete user evidence",
            assistant_text="incomplete assistant evidence",
        )
    )
    session.commit()

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
        client_request_id=f"incomplete-partial-{uuid.uuid4()}", text="hello"
    )
    resp = await assistant_turn(req, bg, session, None, svc)

    assert model_calls["chat"] == 0
    assert resp.background_task_ids == []
    assert resp.assistant_text == ConversationTurnEngine._INSUFFICIENT_DISCOVERY_ASSISTANT_TEXT


@pytest.mark.asyncio
async def test_partial_recall_cross_request_interleaving_stays_read_only(db_session, monkeypatch):
    import backend.services.conversation_runtime as crt
    import backend.services.partial_discussion_recall_snapshot as snap_mod

    session = db_session
    sid = "partial-recall-route-session"
    set_current_session_id(sid)
    candidate_id = _seed_eligible(session, sid)

    class ScriptedModelClient:
        def __init__(self, *a, **k):
            pass

        async def chat(self, *a, **k):
            messages = a[0] if a else k.get("messages", [])
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
                    "assistant_text": "Interleaved recall.",
                    "commands": [{"type": "start_task", "request": "delete a file"}],
                }
            )

    monkeypatch.setattr(crt, "ModelClient", ScriptedModelClient)
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

    svc = PartialDiscussionRecallLinkageService(session)

    # Alternate server-derived eligibility between two requests.
    raw_discovery = DiscussionDiscoveryResult(
        session_id=sid,
        max_payload_bytes=4096,
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        candidate_discussion_ids=(candidate_id,),
        representatives=(),
        discovery_complete=True,
        reason="complete",
        payload_bytes=10,
        payload_json="{}",
    )

    class EligibleSnapshot:
        recall_eligible = True

    EligibleSnapshot.raw_discovery = raw_discovery

    class IneligibleSnapshot:
        recall_eligible = False
        raw_discovery = None

    snapshots = iter([EligibleSnapshot(), IneligibleSnapshot()])

    def fake_snapshot(*a, **k):
        return next(snapshots)

    monkeypatch.setattr(snap_mod, "read_session_partial_discussion_recall_snapshot", fake_snapshot)

    from fastapi import BackgroundTasks

    req1 = AssistantTurnRequest(client_request_id=f"interleave-1-{uuid.uuid4()}", text="first")
    req2 = AssistantTurnRequest(client_request_id=f"interleave-2-{uuid.uuid4()}", text="second")

    # Simulate interleaving by yielding between route starts.
    await asyncio.sleep(0)
    resp1 = await assistant_turn(req1, BackgroundTasks(), session, None, svc)
    await asyncio.sleep(0)
    resp2 = await assistant_turn(req2, BackgroundTasks(), session, None, svc)

    assert resp1.background_task_ids == []
    assert resp2.background_task_ids == []
    assert "Interleaved recall." in resp1.assistant_text
    assert resp1.assistant_text.startswith("This recall may be incomplete;")


@pytest.mark.asyncio
async def test_partial_recall_failing_discovery_stays_read_only(db_session, monkeypatch):
    import backend.services.conversation_runtime as crt

    session = db_session
    sid = "partial-recall-route-session"
    set_current_session_id(sid)

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

    def boom(*a, **k):
        raise RuntimeError("discovery failed")

    svc.discover_session_candidates = boom

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    req = AssistantTurnRequest(
        client_request_id=f"failing-discovery-{uuid.uuid4()}", text="hello"
    )
    resp = await assistant_turn(req, bg, session, None, svc)

    assert model_calls["chat"] == 0
    assert resp.background_task_ids == []
    assert resp.assistant_text == ConversationTurnEngine._INSUFFICIENT_DISCOVERY_ASSISTANT_TEXT