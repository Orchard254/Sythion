"""Bounded discussion-context coverage and base eviction proof.

This suite proves the 20-row canonical bound while exposing exact same-session
coverage scope.  It does not claim any approximate natural-language retrieval
is available; cold retrieval remains unimplemented.
"""

import json
from datetime import datetime, timedelta

import pytest
from fastapi import BackgroundTasks
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from backend.core.session import set_current_session_id
    from backend.models.database import (
        Base, ConversationExchange, ConversationTaskAssociation, ConversationTurn,
        DiscussionContext, DiscussionContextMembership, Task,
    )
    from backend.services.conversation_turn_engine import ConversationTurnEngine
    from backend.services.discussion_contexts import (
        DiscussionContextCoverage,
        read_session_discussion_contexts,
        read_session_discussion_contexts_with_coverage,
    )
except ImportError:
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest
    from core.session import set_current_session_id
    from models.database import (
        Base, ConversationExchange, ConversationTaskAssociation, ConversationTurn,
        DiscussionContext, DiscussionContextMembership, Task,
    )
    from services.conversation_turn_engine import ConversationTurnEngine
    from services.discussion_contexts import (
        DiscussionContextCoverage,
        read_session_discussion_contexts,
        read_session_discussion_contexts_with_coverage,
    )


SESSION_ID = "coverage-session"
STATE = {}


@pytest.fixture
def db(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'coverage.db'}")
    Base.metadata.create_all(engine)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def _seed_exact_contexts(db, *, count, start=datetime(2026, 1, 1, 0, 0, 0)):
    for i in range(count):
        exchange_id = f"exchange-exact-{i}"
        user_id = 1000 + (i * 2)
        assistant_id = user_id + 1
        user_text = f"Exact context text for topic {i}."
        assistant_text = f"Exact answer text for topic {i}."
        db.add(ConversationExchange(id=exchange_id, session_id=SESSION_ID,
                                   user_text=user_text, assistant_text=assistant_text))
        db.add_all([
            ConversationTurn(id=user_id, session_id=SESSION_ID, role="user", summary=user_text),
            ConversationTurn(id=assistant_id, session_id=SESSION_ID, role="assistant", summary=assistant_text),
        ])
        db.add(DiscussionContext(id=f"context-exact-{i}", session_id=SESSION_ID,
                                name=user_text[:96], name_source="initial_request_excerpt",
                                created_at=start + timedelta(minutes=i)))
        db.add(DiscussionContextMembership(context_id=f"context-exact-{i}",
                                          exchange_id=exchange_id,
                                          user_turn_id=user_id,
                                          assistant_turn_id=assistant_id))
    db.commit()


def test_exact_coverage_snapshot_for_zero_history(db):
    contexts, coverage = read_session_discussion_contexts_with_coverage(db, SESSION_ID, limit=20)
    assert contexts == ()
    assert coverage == DiscussionContextCoverage(0, 0, 0)


def test_exact_coverage_count_is_same_query_before_limit(db):
    _seed_exact_contexts(db, count=23)
    contexts, coverage = read_session_discussion_contexts_with_coverage(db, SESSION_ID, limit=20)
    assert len(contexts) == 20
    assert coverage.available_count == 23
    assert coverage.returned_count == 20
    assert coverage.omitted_count == 3
    assert all(len(context.user_text or "") <= 2000 for context in contexts)


def test_base_reader_still_bounded_at_twenty_with_true_eviction_proof(db):
    _seed_exact_contexts(db, count=23)
    rows = read_session_discussion_contexts(db, SESSION_ID, limit=20)
    contexts, coverage = read_session_discussion_contexts_with_coverage(db, SESSION_ID, limit=20)
    assert len(rows) == 20
    assert {row.context_id for row in rows} == {context.context_id for context in contexts}
    assert {row.exchange_id for row in rows} == {context.exchange_id for context in contexts}
    oldest = {f"context-exact-{i}" for i in (0, 1, 2)}
    assert oldest.isdisjoint({row.context_id for row in rows})
    assert coverage.omitted_count == 3


def test_coverage_excludes_foreign_orphan_and_mismatched_memberships(db):
    _seed_exact_contexts(db, count=20)
    # Foreign exchange makes this context invisible.
    db.add(ConversationExchange(id="exchange-foreign", session_id="other-session",
                               user_text="Foreign", assistant_text="Foreign"))
    db.add_all([
        ConversationTurn(id=9001, session_id="other-session", role="user", summary="Foreign"),
        ConversationTurn(id=9002, session_id="other-session", role="assistant", summary="Foreign"),
    ])
    db.add(DiscussionContext(id="context-foreign", session_id=SESSION_ID,
                            name="Foreign", name_source="initial_request_excerpt",
                            created_at=datetime(2026, 2, 1)))
    db.add(DiscussionContextMembership(context_id="context-foreign",
                                      exchange_id="exchange-foreign",
                                      user_turn_id=9001, assistant_turn_id=9002))
    # Orphan context without membership.
    db.add(DiscussionContext(id="context-orphan", session_id=SESSION_ID,
                            name="Orphan", name_source="initial_request_excerpt",
                            created_at=datetime(2026, 2, 2)))
    db.commit()
    contexts, coverage = read_session_discussion_contexts_with_coverage(db, SESSION_ID, limit=20)
    assert len(contexts) == 20
    assert coverage.available_count == 20
    assert coverage.returned_count == 20
    assert coverage.omitted_count == 0
    assert {context.context_id for context in contexts}.isdisjoint({"context-foreign", "context-orphan"})


def test_coverage_closed_strict_shape_and_normalization(db):
    _seed_exact_contexts(db, count=2)
    contexts, _ = read_session_discussion_contexts_with_coverage(db, SESSION_ID, limit=20)
    engine = ConversationTurnEngine.__new__(ConversationTurnEngine)
    valid = engine._normalize_discussion_context_coverage(
        {"available_count": 2, "returned_count": 2, "omitted_count": 0}, contexts
    )
    assert valid == {"available_count": 2, "returned_count": 2, "omitted_count": 0}
    assert type(valid["available_count"]) is int
    assert type(valid["returned_count"]) is int
    assert type(valid["omitted_count"]) is int

    cases = [
        None,  # absent is unknown and handled as None; no invalid raise
    ]
    assert engine._normalize_discussion_context_coverage(None, contexts) is None

    invalid = [
        True,
        {"available_count": True, "returned_count": 2, "omitted_count": 0},
        {"available_count": 2, "returned_count": True, "omitted_count": 0},
        {"available_count": 2, "returned_count": 2, "omitted_count": True},
        {"available_count": 2, "returned_count": 2, "omitted_count": -1},
        {"available_count": 3, "returned_count": 2, "omitted_count": 0},
        {"available_count": 2, "returned_count": 1, "omitted_count": 1},
        {"allowed": 2},
    ]
    for case in invalid:
        if case is None:
            continue
        with pytest.raises(Exception):
            engine._normalize_discussion_context_coverage(case, contexts)

    bad_bool_case = {"available_count": 0, "returned_count": 0, "omitted_count": 0}
    with pytest.raises(Exception):
        engine._normalize_discussion_context_coverage({**bad_bool_case, "available_count": True}, contexts)


@pytest.mark.asyncio
async def test_route_real_engine_fake_model_passes_canonical_coverage_and_eviction(monkeypatch):
    set_current_session_id("assistant-turn-test-session")
    client_responses = ['{"assistant_text": "ok", "commands": []}']

    class FakeModelClient:
        def __init__(self, *args, **kwargs):
            self.calls = []
            self.responses = list(client_responses)

        async def chat(self, messages, **kwargs):
            self.calls.append(messages)
            return self.responses.pop(0) if self.responses else '{"assistant_text": "ok", "commands": []}'

    def factory(*args, **kwargs):
        created = FakeModelClient()
        STATE["client"] = created
        return created

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", factory)
    try:
        monkeypatch.setattr("services.conversation_runtime.ModelClient", factory)
    except Exception:
        pass

    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
    from fastapi import BackgroundTasks

    db_engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(db_engine)
    session = sessionmaker(bind=db_engine)()
    try:
        now = datetime(2026, 11, 3, 10, 0, 0)
        # Older task-era context that is evicted from newest 20.
        db = session
        session_id = "assistant-turn-test-session"
        long_user = "Old Project Paper Kite original $15 comparison; café evidence."
        db.add(ConversationExchange(id="paperkite-original", session_id=session_id,
                                     user_text=long_user, assistant_text="old task result"))
        db.add_all([
            ConversationTurn(id=1, session_id=session_id, role="user", summary=long_user),
            ConversationTurn(id=2, session_id=session_id, role="assistant", summary="old task result"),
        ])
        db.add(DiscussionContext(id="paperkite-original-ctx", session_id=session_id,
                                name=long_user[:96], name_source="initial_request_excerpt", created_at=now))
        db.add(DiscussionContextMembership(context_id="paperkite-original-ctx",
                                          exchange_id="paperkite-original", user_turn_id=1,
                                          assistant_turn_id=2))
        # enough newer contexts to push the original outside newest 20
        for i in range(25):
            user_text = f"Newer unrelated context {i}."
            db.add(ConversationExchange(id=f"newer-{i}", session_id=session_id,
                                       user_text=user_text, assistant_text=f"reply {i}"))
            db.add_all([
                ConversationTurn(id=100 + (i * 2), session_id=session_id, role="user", summary=user_text),
                ConversationTurn(id=101 + (i * 2), session_id=session_id, role="assistant", summary=f"reply {i}"),
            ])
            db.add(DiscussionContext(id=f"newer-{i}-ctx", session_id=session_id,
                                    name=user_text[:96], name_source="initial_request_excerpt",
                                    created_at=now + timedelta(minutes=i + 1)))
            db.add(DiscussionContextMembership(context_id=f"newer-{i}-ctx",
                                              exchange_id=f"newer-{i}",
                                              user_turn_id=100 + (i * 2),
                                              assistant_turn_id=101 + (i * 2)))
        db.commit()

        request = AssistantTurnRequest(client_request_id="coverage-regression",
                                     text="New conversation-only check.")
        bg = BackgroundTasks()
        await assistant_turn(request, bg, db, None)

        client = STATE["client"]
        assert len(client.calls) == 1
        payload = json.loads(client.calls[0][1]["content"])
        contexts = payload["discussion_contexts"]
        coverage = payload["discussion_context_coverage"]
        assert len(contexts) == 20
        assert coverage["available_count"] == 26
        assert coverage["returned_count"] == 20
        assert coverage["omitted_count"] == 6
        assert type(coverage["available_count"]) is int
        assert type(coverage["returned_count"]) is int
        assert type(coverage["omitted_count"]) is int
        assert all(not str(context.get("context_id", "")).startswith("paperkite") for context in contexts)
    finally:
        session.close()
        db_engine.dispose()


def test_cold_retrieval_not_claimed_fixed():
    # Explicit marker test: runnable code intentionally has no retrieval helper.
    import backend.services.discussion_contexts as module

    assert not hasattr(module, "read_cold_discussion_contexts")
    assert not hasattr(module, "_normalize_evidence_text")
    assert not hasattr(module, "_escape_like_pattern")
    # Explicit marker test: runnable code intentionally has no retrieval helper.
    import backend.services.discussion_contexts as module

    assert not hasattr(module, "read_cold_discussion_contexts")
    assert not hasattr(module, "_normalize_evidence_text")
    assert not hasattr(module, "_escape_like_pattern")