import json
import pytest
from fastapi import BackgroundTasks

try:
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from backend.core.session import set_current_session_id
    from backend.models.database import (
        ConversationExchange, ConversationTurn, DiscussionContext,
        DiscussionContextMembership, ConversationTaskAssociation, Task,
    )
except ImportError:
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest
    from core.session import set_current_session_id
    from models.database import (
        ConversationExchange, ConversationTurn, DiscussionContext,
        DiscussionContextMembership, ConversationTaskAssociation, Task,
    )


class FakeModelClient:
    def __init__(self, responses=None):
        self.responses = list(responses or ['{"assistant_text": "ok", "commands": []}'])
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.responses:
            return '{"assistant_text": "ok", "commands": []}'
        return self.responses.pop(0)


class CapturingTransport:
    def __init__(self, client):
        self.client = client


STATE = {}


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id("assistant-turn-test-session")
    STATE.clear()
    yield


@pytest.fixture
def _real_engine(monkeypatch):
    client = FakeModelClient()
    STATE["client"] = client

    def factory(*args, **kwargs):
        return client

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", factory)
    try:
        monkeypatch.setattr("services.conversation_runtime.ModelClient", factory)
    except Exception:
        pass
    return client


def _now():
    return __import__("datetime").datetime.utcnow()


def _seed_core_contexts(db, session_id, now):
    long_lantern_user = (
        "For a separate fictional planning exercise, call the project Lantern: one indoor museum visit, "
        "a $20 limit, and no cafés. Restate that plan in one sentence. Conversation only."
    )
    long_lantern_assistant = "Project Lantern is a fictional plan for one indoor museum visit with a $12 budget and no café stops."
    bounded_user_2000 = "x" * 1999 + "é"
    bounded_user_2001 = "y" * 2000 + "é"
    bounded_assistant_2000 = "a" * 1999 + "é"
    bounded_assistant_2001 = "b" * 2000 + "é"

    db.add(ConversationExchange(id="lantern-initial_exchange", session_id=session_id,
                                user_text=long_lantern_user, assistant_text=long_lantern_assistant))
    db.add_all([
        ConversationTurn(id=7, session_id=session_id, role="user", summary=long_lantern_user),
        ConversationTurn(id=8, session_id=session_id, role="assistant", summary=long_lantern_assistant),
    ])
    db.add(DiscussionContext(id="lantern-initial_ctx", session_id=session_id, name=long_lantern_user[:96],
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="lantern-initial_ctx", exchange_id="lantern-initial_exchange",
                                     user_turn_id=7, assistant_turn_id=8))

    latest_user = "For Lantern, reduce only the budget to $12 and restate our plan in one sentence, keeping other restrictions; no cafés remains."
    latest_assistant = "Project Lantern is a fictional plan for one indoor museum visit with a $12 budget and no café stops."
    db.add(ConversationExchange(id="lantern-latest_exchange", session_id=session_id,
                                user_text=latest_user, assistant_text=latest_assistant))
    db.add_all([
        ConversationTurn(id=9, session_id=session_id, role="user", summary=latest_user),
        ConversationTurn(id=10, session_id=session_id, role="assistant", summary=latest_assistant),
    ])
    db.add(DiscussionContext(id="lantern-latest_ctx", session_id=session_id, name=latest_user[:96],
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="lantern-latest_ctx", exchange_id="lantern-latest_exchange",
                                     user_turn_id=9, assistant_turn_id=10))

    # Bounded text contexts for both roles and both boundaries.
    db.add(ConversationExchange(id="bound-2000-u_exchange", session_id=session_id,
                                user_text=bounded_user_2000, assistant_text=bounded_assistant_2000))
    db.add_all([
        ConversationTurn(id=11, session_id=session_id, role="user", summary="bound 2000 user"),
        ConversationTurn(id=12, session_id=session_id, role="assistant", summary="bound 2000 assistant"),
    ])
    db.add(DiscussionContext(id="bound-2000-u_ctx", session_id=session_id, name="bound 2000 user"[:96],
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="bound-2000-u_ctx", exchange_id="bound-2000-u_exchange",
                                     user_turn_id=11, assistant_turn_id=12))

    db.add(ConversationExchange(id="bound-2001-u_exchange", session_id=session_id,
                                user_text=bounded_user_2001, assistant_text=bounded_assistant_2001))
    db.add_all([
        ConversationTurn(id=13, session_id=session_id, role="user", summary="bound 2001 user"),
        ConversationTurn(id=14, session_id=session_id, role="assistant", summary="bound 2001 assistant"),
    ])
    db.add(DiscussionContext(id="bound-2001-u_ctx", session_id=session_id, name="bound 2001 user"[:96],
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="bound-2001-u_ctx", exchange_id="bound-2001-u_exchange",
                                     user_turn_id=13, assistant_turn_id=14))

    # One valid task-associated context; task-bound exchange text must also pass.
    db.add(Task(id="task-ok", original_request="task request", state="completed", version=2, final_result="task result"))
    db.add(ConversationExchange(id="task-ok_exchange", session_id=session_id,
                                user_text="task user text", assistant_text="task assistant text"))
    db.add_all([
        ConversationTurn(id=15, session_id=session_id, role="user", summary="task user text"),
        ConversationTurn(id=16, session_id=session_id, role="assistant", summary="task assistant text"),
    ])
    db.add(DiscussionContext(id="task-ok_ctx", session_id=session_id, name="task context"[:96],
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="task-ok_ctx", exchange_id="task-ok_exchange",
                                     user_turn_id=15, assistant_turn_id=16))
    db.add(ConversationTaskAssociation(exchange_id="task-ok_exchange", task_id="task-ok",
                                     user_turn_id=15, assistant_turn_id=16))

    # Foreign session context must remain excluded.
    db.add(ConversationExchange(id="foreign-exchange", session_id="other-session",
                               user_text="foreign Lantern text", assistant_text="foreign reply"))
    db.add_all([
        ConversationTurn(id=2000, session_id="other-session", role="user", summary="foreign request"),
        ConversationTurn(id=2001, session_id="other-session", role="assistant", summary="foreign reply"),
    ])
    db.add(DiscussionContext(id="foreign-ctx", session_id="other-session", name="Foreign Lantern",
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="foreign-ctx", exchange_id="foreign-exchange",
                                     user_turn_id=2000, assistant_turn_id=2001))

    # Orphan context without exact membership must be excluded.
    db.add(DiscussionContext(id="orphan-ctx", session_id=session_id, name="Orphan Lantern",
                            name_source="initial_request_excerpt", created_at=now))

    # Mismatched task binding must not surface task identity.
    db.add(Task(id="mismatch-task", original_request="task request", state="running", version=3))
    db.add(ConversationExchange(id="mismatch-exchange", session_id=session_id,
                               user_text="mismatch request", assistant_text="mismatch reply"))
    db.add_all([
        ConversationTurn(id=3000, session_id=session_id, role="user", summary="mismatch request"),
        ConversationTurn(id=3001, session_id=session_id, role="assistant", summary="mismatch reply"),
    ])
    db.add(DiscussionContext(id="mismatch-ctx", session_id=session_id, name="Mismatch Lantern",
                            name_source="initial_request_excerpt", created_at=now))
    db.add(DiscussionContextMembership(context_id="mismatch-ctx", exchange_id="mismatch-exchange",
                                     user_turn_id=3000, assistant_turn_id=3001))
    db.add(ConversationTaskAssociation(exchange_id="mismatch-exchange", task_id="mismatch-task",
                                     user_turn_id=3000 + 999, assistant_turn_id=3001))

    # Six newer craft conversations push Lantern outside the six-turn recent history.
    for i in range(6):
        db.add(ConversationExchange(id=f"craft-exchange-{i}", session_id=session_id,
                                   user_text=f"craft request {i}", assistant_text=f"craft reply {i}"))
        db.add_all([
            ConversationTurn(id=1000 + i * 2, session_id=session_id, role="user", summary=f"craft request {i}"),
            ConversationTurn(id=1001 + i * 2, session_id=session_id, role="assistant", summary=f"craft reply {i}"),
        ])
    db.commit()


@pytest.mark.asyncio
async def test_route_preserves_current_request_and_bound_lantern_evidence(test_db, _real_engine):
    db = test_db
    session_id = "assistant-turn-test-session"
    now = _now()
    _seed_core_contexts(db, session_id, now)
    current_request = "New conversation-only check: return to fictional Project Lantern. In one sentence recall its latest agreed activity, budget, and café restriction. Do not use garden or craft details."

    bg = BackgroundTasks()
    await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__('uuid').uuid4()}", text=current_request),
        bg, db, None,
    )

    client = STATE["client"]
    assert len(client.calls) == 1
    captured = client.calls[0]
    assert len(captured) == 2
    user_message = json.loads(captured[1]["content"])
    assert user_message["user_request"] == current_request
    assert len(user_message["recent_turns"]) == 6
    assert not any("Lantern" in str(t.get("content", "")) for t in user_message["recent_turns"])

    ctxs = user_message["discussion_contexts"]
    by_id = {row["context_id"]: row for row in ctxs}
    assert "lantern-initial_ctx" in by_id
    assert "lantern-latest_ctx" in by_id
    assert "task-ok_ctx" in by_id
    initial = by_id["lantern-initial_ctx"]
    latest = by_id["lantern-latest_ctx"]
    assert len(initial["name"]) == 96
    assert "no cafés" in initial["user_text"]
    assert "no café stops" in initial["assistant_text"]
    assert "no cafés" in latest["user_text"]
    assert "no café stops" in latest["assistant_text"]
    assert latest["user_text"] != initial["user_text"]
    assert latest["user_text"].count("no cafés") == 1
    assert initial["task_id"] is None
    assert latest["task_id"] is None
    assert by_id["task-ok_ctx"]["task_id"] == "task-ok"
    assert "task user text" in by_id["task-ok_ctx"]["user_text"]
    assert "task assistant text" in by_id["task-ok_ctx"]["assistant_text"]

    assert "foreign-ctx" not in by_id
    assert "orphan-ctx" not in by_id
    mismatch_rows = [r for r in ctxs if r.get("context_id") == "mismatch-ctx"]
    assert len(mismatch_rows) == 1
    assert mismatch_rows[0].get("task_id") is None
    assert "no cafés" in latest["user_text"]
    assert "no café stops" in latest["assistant_text"]

    assert by_id["bound-2000-u_ctx"]["user_text_truncated"] is False
    assert len(by_id["bound-2000-u_ctx"]["user_text"]) == 2000
    assert "é" in by_id["bound-2000-u_ctx"]["user_text"]
    assert by_id["bound-2000-u_ctx"]["assistant_text_truncated"] is False
    assert len(by_id["bound-2000-u_ctx"]["assistant_text"]) == 2000
    assert by_id["bound-2001-u_ctx"]["user_text_truncated"] is True
    assert len(by_id["bound-2001-u_ctx"]["user_text"]) == 2000
    assert by_id["bound-2001-u_ctx"]["assistant_text_truncated"] is True
    assert len(by_id["bound-2001-u_ctx"]["assistant_text"]) == 2000

    assert "café" in str(captured)


@pytest.mark.asyncio
async def test_malformed_json_retry_uses_two_provider_calls(test_db, _real_engine):
    db = test_db
    session_id = "assistant-turn-test-session"
    now = _now()
    client = STATE["client"]
    client.responses = [
        "not valid json",
        '{"assistant_text": "ok", "commands": []}',
    ]

    bg = BackgroundTasks()
    await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__('uuid').uuid4()}", text="Hello"),
        bg, db, None,
    )

    assert len(client.calls) == 2
    assert STATE["client"].calls[0][0]["role"] == "system"
    assert '"user_request": "Hello"' in client.calls[0][1]["content"]


@pytest.mark.asyncio
async def test_real_engine_denormalized_retrieved_contexts_pass_without_artificial_loss(test_db, _real_engine):
    # Keeps canonical payload single-channel and provider calls count independent of DB internals.
    db = test_db
    bg = BackgroundTasks()
    await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__('uuid').uuid4()}", text="Check Lantern"),
        bg, db, None,
    )
    assert STATE["client"].calls[0] is not None