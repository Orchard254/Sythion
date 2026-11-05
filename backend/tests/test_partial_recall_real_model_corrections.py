"""Real-provider partial recall correction test (disposable SQLite only).

Fictional seeded data only. No production files, real DB, activation, migration,
mirror, or secrets are touched.
"""

import datetime
import json
import os
import sys
import uuid

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

PROJECT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if PROJECT not in sys.path:
    sys.path.insert(0, PROJECT)

os.environ.setdefault("SYTHION_AUTONOMY_LEVEL", "guarded")

try:
    from backend.core.session import set_current_session_id
    from backend.api.dependencies import reset_grounded_discussion_provider
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from backend.services.conversation_runtime import ModelClient as RealModelClient
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
except ImportError:  # pragma: no cover
    from core.session import set_current_session_id
    from api.dependencies import reset_grounded_discussion_provider
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest
    from services.conversation_runtime import ModelClient as RealModelClient
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


def _provider_ready():
    try:
        from backend.core.config import settings, resolve_selected_model
        from backend.core.secrets import resolve_ollama_api_key
    except Exception:
        try:
            from core.config import settings, resolve_selected_model
            from core.secrets import resolve_ollama_api_key
        except Exception:
            return False
    try:
        if not resolve_ollama_api_key(getattr(settings, "ollama_api_key", None)):
            return False
        return bool(resolve_selected_model())
    except Exception:
        return False


pytestmark = [
    pytest.mark.provider_e2e,
    pytest.mark.skipif(
        os.environ.get("SYTHION_RUN_PROVIDER_E2E") != "1",
        reason="set SYTHION_RUN_PROVIDER_E2E=1",
    ),
    pytest.mark.skipif(not _provider_ready(), reason="no provider key/model"),
]


SID = "partial-recall-real-model-session"


def _seed(db_session, sid):
    db_session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="tgt u"))
    db_session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="tgt a"))
    db_session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="src u"))
    db_session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="src a"))
    t0 = datetime.datetime(2026, 1, 1)
    t1 = datetime.datetime(2026, 1, 2)
    db_session.add(
        ConversationExchange(
            id="tgt-ex",
            session_id=sid,
            user_text="original user evidence text: fictional protocol codename was Cobalt Sparrow",
            assistant_text="original assistant evidence text: initial codename recorded as Cobalt Sparrow",
            created_at=t0,
        )
    )
    db_session.add(
        ConversationExchange(
            id="src-ex",
            session_id=sid,
            user_text="later correction source text: human follow-up says use Emerald Falcon",
            assistant_text="later correction source answer: approved Emerald Falcon",
            created_at=t1,
        )
    )
    db_session.commit()

    mgr = DiscussionIndexManager(db_session, db=db_session)
    for exchange_id, uid, aid in (("tgt-ex", 1, 2), ("src-ex", 3, 4)):
        digest = canonical_exchange_input_digest(db_session, sid, exchange_id, uid, aid)
        row, _ = mgr.receive(sid, exchange_id, uid, aid, input_digest=digest, status="pending")
        mgr.transition(row.idempotency_key, INDEX_STATUS_COMPLETE, commit=True)
    db_session.commit()

    gm = GroundedDiscussionManager(db_session)
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
    db_session.commit()
    did = discussion.discussion_id

    event = gm.add_correction(
        did,
        sid,
        target_exchange_id="tgt-ex",
        target_user_turn_id=1,
        target_assistant_turn_id=2,
        correction_text="later correction event text: corrected approved codename to Emerald Falcon",
        provenance="human_stated",
    )
    db_session.commit()
    attach_event_source_link(
        db_session,
        event_id=event.id,
        session_id=sid,
        discussion_id=did,
        source_exchange_id="src-ex",
        source_user_turn_id=3,
        source_assistant_turn_id=4,
        target_exchange_id="tgt-ex",
        target_user_turn_id=1,
        target_assistant_turn_id=2,
        provenance="human_stated",
        commit=False,
    )
    db_session.commit()

    # Keep seeded evidence OUT of the recent-turn window.
    base = datetime.datetime(2026, 2, 1)
    for i in range(8):
        db_session.add(
            ConversationTurn(
                id=1000 + i,
                session_id=sid,
                role="user" if i % 2 == 0 else "assistant",
                summary=f"neutral filler turn {i}",
                created_at=base + datetime.timedelta(minutes=i),
            )
        )
    db_session.commit()

    return did


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id(SID)
    try:
        reset_grounded_discussion_provider()
    except Exception:
        pass
    yield
    set_current_session_id(SID)


@pytest.mark.asyncio
async def test_partial_recall_real_model_correction(monkeypatch):
    sid = SID
    set_current_session_id(sid)

    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        candidate_id = _seed(session, sid)

        captured_calls = []

        class CapturingModelClient(RealModelClient):
            async def chat(self, *a, **k):
                messages = k.get("messages", a[0] if a else [])
                captured_calls.append([dict(m) for m in (messages or [])])
                return await super().chat(*a, **k)

        monkeypatch.setattr(
            "backend.services.conversation_runtime.ModelClient", CapturingModelClient
        )
        monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)

        svc = PartialDiscussionRecallLinkageService(session)

        from fastapi import BackgroundTasks

        bg = BackgroundTasks()
        req = AssistantTurnRequest(
            client_request_id=f"partial-real-{uuid.uuid4()}",
            text="what did we discuss?",
        )
        resp = await assistant_turn(req, bg, session, None, svc)

        # Resolve selected model id for artifact.
        try:
            from backend.core.config import resolve_selected_model
            model_id = resolve_selected_model()
        except Exception:
            model_id = None

        scratchpad = os.path.join(PROJECT, "scratchpad")
        os.makedirs(scratchpad, exist_ok=True)
        artifact_path = os.path.join(
            scratchpad, f"real_model_partial_recall_{uuid.uuid4().hex}.json"
        )
        artifact = {
            "test": "test_partial_recall_real_model_correction",
            "timestamp_utc": datetime.datetime.utcnow().isoformat() + "Z",
            "provider_model": model_id,
            "session_id": sid,
            "candidate_discussion_id": candidate_id,
            "captured_call_count": len(captured_calls),
            "captured_model_inputs": captured_calls,
            "response_background_task_ids": list(resp.background_task_ids),
            "response_assistant_text": resp.assistant_text,
        }
        with open(artifact_path, "w", encoding="utf-8") as handle:
            json.dump(artifact, handle, ensure_ascii=False, indent=2, default=str)

        # Verification (no uncertain retry).
        assert captured_calls, "no model call was captured"
        assert len(captured_calls) <= ConversationTurnEngine.MAX_TOTAL_MODEL_CALLS
        final_payload = json.dumps(captured_calls[-1], ensure_ascii=False)
        assert "original user evidence text" in final_payload
        assert "later correction event text" in final_payload
        assert "later correction source text" in final_payload

        assert resp.background_task_ids == []
        assert resp.assistant_text.startswith(
            "This recall may be incomplete; other conversations or later "
            "corrections may exist.\n\n"
        )
        assert "Emerald Falcon" in resp.assistant_text
        # Result & artifact location for the runner/report.
        print(f"REAL_MODEL_PARTIAL_RECALL_ARTIFACT={artifact_path}")
    finally:
        session.close()
        engine.dispose()
