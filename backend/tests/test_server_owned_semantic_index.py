"""Disposable tests for default-inert server-owned semantic indexing orchestration."""

import asyncio
import json

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase
from backend.services.grounded_discussion_semantic_index import SemanticIndexBudget
from backend.services.grounded_discussion_server_owned_semantic_index import run_server_owned_semantic_index
from backend.services.grounded_discussion_index import DiscussionIndexStateRow, canonical_exchange_input_digest


@pytest.fixture()
def db_session(tmp_path_factory):
    # A private file-backed engine has a lock-protected, multi-connection pool that
    # can be proven isolated for safely owned capture sessions.
    db_path = tmp_path_factory.mktemp("db") / "server_owned.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _seed(session, sid, user_text="server-owned user text", assistant_text="server-owned assistant text"):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id="exchange-1",
            session_id=sid,
            user_text=user_text,
            assistant_text=assistant_text,
            status="ok",
        )
    )
    session.commit()


@pytest.mark.asyncio
async def test_server_owned_orchestration_binds_model_input_and_is_inert_by_import(db_session):
    import sys

    sid = "session-server-owned"
    set_current_session_id(sid)
    _seed(db_session, sid)

    captured = []

    class Client:
        async def chat(self, *, messages, **kwargs):
            captured.append(messages)
            payload = {
                "status": "insufficient",
                "proposals": [],
                "clarification": "Need more",
                "reason": "sparse",
            }
            return json.dumps(payload)

    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        db_session,
        Client(),
        sid,
        "exchange-1",
        11,
        12,
        budget=budget,
    )

    assert outcome.applied is False
    assert outcome.state_status == "insufficient"
    assert outcome.provider_call_count == 1
    assert budget.consumed_calls == 1
    assert outcome.model_input_binding_verified is False  # fail closed: no provider receipt/full semantic persistence
    assert outcome.session_ownership_explicitly_verified is False
    assert outcome.overall_end_to_end_complete_possible is False
    assert outcome.model_input_digest is not None

    parsed_messages = json.loads(captured[0][1]["content"])
    assert parsed_messages["source_exchange"]["user_text"] == "server-owned user text"
    assert parsed_messages["source_exchange"]["user_turn_id"] == 11

    saved = {}
    for name in ("backend.models.database", "models.database"):
        saved[name] = sys.modules.pop(name, None)
    try:
        import backend.services.grounded_discussion_server_owned_semantic_index  # noqa: F401
        assert "backend.models.database" not in sys.modules
    finally:
        for name, value in saved.items():
            if value is not None:
                sys.modules[name] = value


@pytest.mark.asyncio
async def test_server_owned_orchestrator_rejects_concurrent_source_mutation(db_session):
    sid = "session-mutation"
    set_current_session_id(sid)
    _seed(db_session, sid)

    class Client:
        async def chat(self, *, messages, **kwargs):
            exchange = db_session.query(ConversationExchange).one()
            exchange.user_text = "mutated during model call"
            db_session.commit()
            return json.dumps(
                {
                    "status": "insufficient",
                    "proposals": [],
                    "clarification": "Need more",
                    "reason": "sparse",
                }
            )

    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        db_session,
        Client(),
        sid,
        "exchange-1",
        11,
        12,
        budget=budget,
    )
    assert outcome.applied is False
    assert outcome.rejected_reason == "ValueError"
    assert outcome.canonical_source_mutation_detected is True
    assert outcome.provider_call_count == 1
    assert outcome.model_input_binding_verified is False
    assert outcome.session_ownership_explicitly_verified is False
    assert outcome.model_input_binding_verified is False
    assert budget.consumed_calls == 1
    assert db_session.query(DiscussionIndexStateRow).count() == 0


@pytest.mark.asyncio
async def test_server_owned_provider_failure_charges_budget_and_does_not_apply(db_session):
    sid = "session-provider-failure"
    set_current_session_id(sid)
    _seed(db_session, sid)

    class Client:
        async def chat(self, *, messages, **kwargs):
            raise RuntimeError("provider unavailable")

    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        db_session,
        Client(),
        sid,
        "exchange-1",
        11,
        12,
        budget=budget,
    )
    assert outcome.applied is False
    assert outcome.rejected_reason == "SemanticIndexingError"
    assert outcome.provider_call_count == 1
    assert outcome.model_input_binding_verified is False
    assert outcome.session_ownership_explicitly_verified is False
    assert outcome.model_input_binding_verified is False
    assert budget.consumed_calls == 1
    assert db_session.query(DiscussionIndexStateRow).count() == 0


@pytest.mark.asyncio
async def test_server_owned_cancellation_propagates_and_budget_charged(db_session):
    sid = "session-cancel"
    set_current_session_id(sid)
    _seed(db_session, sid)

    class Client:
        async def chat(self, *, messages, **kwargs):
            raise asyncio.CancelledError()

    budget = SemanticIndexBudget(max_provider_calls=1)
    with pytest.raises(asyncio.CancelledError):
        await run_server_owned_semantic_index(
            db_session,
            Client(),
            sid,
            "exchange-1",
            11,
            12,
            budget=budget,
        )
    assert budget.consumed_calls == 1


# Permanent audit tests.
def test_caller_unflushed_pending_rows_reject_without_provider_or_mutation(db_session):
    sid = "session-unflushed"
    set_current_session_id(sid)
    _seed(db_session, sid)
    db_session.add(
        ConversationExchange(id="pending-unflushed", session_id=sid, user_text="pending", assistant_text="pending")
    )

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    provider = NoProvider()
    outcome = asyncio.run(
        run_server_owned_semantic_index(db_session, provider, sid, "exchange-1", 11, 12)
    )
    assert outcome.rejected_reason in {"caller_session_busy", "CallerSessionBusyError"}
    assert outcome.provider_call_count == 0
    assert NoProvider.calls == 0
    assert db_session.new
    assert any(getattr(row, "id", None) == "pending-unflushed" for row in db_session.new)


def test_caller_flushed_pending_rows_reject_without_provider_or_mutation(db_session):
    sid = "session-flushed"
    set_current_session_id(sid)
    _seed(db_session, sid)
    db_session.add(
        ConversationExchange(id="pending-flushed", session_id=sid, user_text="pending", assistant_text="pending")
    )
    db_session.flush()
    assert db_session.in_transaction()

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    provider = NoProvider()
    outcome = asyncio.run(
        run_server_owned_semantic_index(db_session, provider, sid, "exchange-1", 11, 12)
    )
    assert outcome.rejected_reason in {"caller_session_busy", "CallerSessionBusyError"}
    assert outcome.provider_call_count == 0
    assert NoProvider.calls == 0
    assert db_session.in_transaction()
    persisted = db_session.execute(
        select(ConversationExchange).where(ConversationExchange.id == "pending-flushed")
    ).scalars().first()
    assert persisted is not None
    db_session.rollback()


@pytest.mark.asyncio
async def test_outer_caller_transaction_rejected_with_no_provider(db_session):
    sid = "session-outer-rollback"
    set_current_session_id(sid)
    _seed(db_session, sid)
    db_session.add(
        ConversationExchange(id="outer-pending", session_id=sid, user_text="x", assistant_text="y")
    )

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    outcome = await run_server_owned_semantic_index(db_session, NoProvider(), sid, "exchange-1", 11, 12)
    assert outcome.rejected_reason in {"caller_session_busy", "CallerSessionBusyError"}
    assert outcome.provider_call_count == 0
    assert NoProvider.calls == 0
    assert db_session.new


@pytest.mark.asyncio
async def test_factory_path_detects_actual_concurrent_wal_mutation(tmp_path):
    db_path = tmp_path / "concurrency.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, future=True)

    sid = "session-wal"
    set_current_session_id(sid)
    caller_session = SessionFactory()
    _seed(caller_session, sid)
    digest = canonical_exchange_input_digest(caller_session, sid, "exchange-1", 11, 12)
    assert digest
    caller_session.rollback()

    class ConcurrentMutatingClient:
        def __init__(self, factory):
            self.factory = factory
            self.calls = 0

        async def chat(self, *, messages, **kwargs):
            self.calls += 1
            mutator = self.factory()
            try:
                exchange = mutator.query(ConversationExchange).filter_by(id="exchange-1", session_id=sid).one()
                exchange.user_text = "mutated from another session"
                mutator.commit()
            finally:
                mutator.close()
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    mutating_client = ConcurrentMutatingClient(SessionFactory)
    assert mutating_client.calls == 0

    outcome = await run_server_owned_semantic_index(
        caller_session,
        mutating_client,
        sid,
        "exchange-1",
        11,
        12,
        session_factory=SessionFactory, owns_session=True,
    )
    assert mutating_client.calls == 1
    assert outcome.rejected_reason == "ValueError"
    assert outcome.canonical_source_mutation_detected is True
    assert outcome.provider_call_count == 1
    assert "mutated from another session" == SessionFactory().query(ConversationExchange).filter_by(id="exchange-1").one().user_text

    engine.dispose()


# Additional immutable session-ownership audit tests.
def test_factory_returning_caller_session_rejects_and_preserves_pending_rows():
    # Build a session whose identity is exposed through a factory alias.
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    caller = Session()
    set_current_session_id("session-factory-alias")
    _seed(caller, "session-factory-alias")
    caller.add(ConversationExchange(id="alias-pending", session_id="session-factory-alias", user_text="p", assistant_text="p"))
    factory = lambda: caller

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    outcome = asyncio.run(
        run_server_owned_semantic_index(factory, NoProvider(), "session-factory-alias", "exchange-1", 11, 12, session_factory=factory, owns_session=True)
    )
    assert outcome.rejected_reason == "session_ownership_not_verified"
    assert outcome.provider_call_count == 0
    assert NoProvider.calls == 0
    # Ownership cannot be verified from the (possibly caller/shared) factory; the
    # caller's pending state is left entirely untouched.
    assert any(getattr(row, "id", None) == "alias-pending" for row in caller.new)
    caller.rollback()
    engine.dispose()


def test_shared_session_gets_busy_after_provider_fails_closed_without_apply():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    shared = Session()
    set_current_session_id("session-shared")
    _seed(shared, "session-shared")
    factory = lambda: shared

    class MutatingProvider:
        def __init__(self):
            self.calls = 0

        async def chat(self, *, messages, **kwargs):
            self.calls += 1
            shared.add(ConversationExchange(id="shared-pending", session_id="session-shared", user_text="x", assistant_text="y"))
            return json.dumps({"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"})

    shared_close_calls = []
    orig_shared_close = shared.close
    def shared_close_spy(*args, **kwargs):
        shared_close_calls.append((args, kwargs))
        return orig_shared_close(*args, **kwargs)
    shared.close = shared_close_spy
    rollback_calls = []
    orig_shared_rollback = shared.rollback
    def shared_rollback_spy(*args, **kwargs):
        rollback_calls.append((args, kwargs))
        return orig_shared_rollback(*args, **kwargs)
    shared.rollback = shared_rollback_spy
    provider = MutatingProvider()
    try:
        outcome = asyncio.run(
            run_server_owned_semantic_index(
                factory, provider, "session-shared", "exchange-1", 11, 12, session_factory=factory, owns_session=True
            )
        )
    finally:
        shared.rollback = orig_shared_rollback
        shared.close = orig_shared_close
    assert outcome.rejected_reason == "session_ownership_not_verified"
    assert outcome.applied is False
    assert provider.calls == 0
    assert outcome.provider_call_count == 0
    assert shared.query(DiscussionIndexStateRow).count() == 0
    # Shared/caller-supplied sessions are never rolled back or closed, even if
    # they appear clean; ambiguity fails closed instead.
    assert rollback_calls == []
    assert shared_close_calls == []
    # No caller/shared pending or persisted state was introduced.
    assert not any(getattr(row, "id", None) == "shared-pending" for row in shared.new)
    shared.rollback()
    engine.dispose()


@pytest.mark.asyncio
async def test_file_backed_concurrent_source_mutation_between_revalidation_and_write_fail_closed(tmp_path):
    db_path = tmp_path / "between.db"
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)

    sid = "session-between"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller_session = Factory()
    # The exchange already exists from the committed seed session.  The caller
    # session remains clean and derives its bind from the same engine.

    from backend.services.grounded_discussion_index import canonical_exchange_input_digest as original_digest

    calls = {"count": 0}

    def race_digest(session, session_id, exchange_id, user_turn_id, assistant_turn_id):
        result = original_digest(session, session_id, exchange_id, user_turn_id, assistant_turn_id)
        calls["count"] += 1
        if calls["count"] == 2:
            # Mutate canonical source after the first revalidation begins/while
            # the application write path is being prepared.
            mutator = Factory()
            try:
                exchange = mutator.query(ConversationExchange).filter_by(id="exchange-1", session_id=sid).one()
                exchange.user_text = "second-session mutation"
                mutator.commit()
            finally:
                mutator.close()
        return result

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps({"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"})

    with pytest.MonkeyPatch().context() as patcher:
        patcher.setattr("backend.services.grounded_discussion_index.canonical_exchange_input_digest", race_digest)
        import backend.services.grounded_discussion_server_owned_semantic_index as orch
        patcher.setattr(orch, "canonical_exchange_input_digest", race_digest, raising=False)
        outcome = await run_server_owned_semantic_index(
            caller_session, Client(), sid, "exchange-1", 11, 12, session_factory=Factory,
            owns_session=True,
        )

    # Strict fail-closed race proof: the injected mutation is observed during
    # revalidation, so at least two canonical digests execute and the operation
    # is rejected before applying proposals.
    assert calls["count"] >= 2
    assert outcome.rejected_reason in {"ValueError", "capture_failed"}
    assert outcome.applied is False
    engine.dispose()



@pytest.mark.asyncio
async def test_connection_bound_session_rejects_before_provider(tmp_path_factory):
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    db_path = tmp_path_factory.mktemp("db") / "conn.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    connection = engine.connect()
    Session = sessionmaker(bind=connection, autoflush=False, future=True)
    session = Session()
    sid = "session-conn"
    set_current_session_id(sid)
    _seed(session, sid)
    session.commit()

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    provider = NoProvider()
    rollback_calls = []

    orig_rollback = session.rollback
    def rollback_spy(*args, **kwargs):
        rollback_calls.append((args, kwargs))
        return orig_rollback(*args, **kwargs)

    session.rollback = rollback_spy
    close_calls = []

    orig_close = session.close
    def close_spy(*args, **kwargs):
        close_calls.append((args, kwargs))
        return orig_close(*args, **kwargs)

    session.close = close_spy
    try:
        outcome = await run_server_owned_semantic_index(session, provider, sid, "exchange-1", 11, 12)
    finally:
        session.rollback = orig_rollback
        session.close = orig_close
        session.close()
        connection.close()
        engine.dispose()

    assert outcome.rejected_reason == "session_ownership_not_verified"
    assert outcome.session_ownership_explicitly_verified is False
    assert outcome.model_input_binding_verified is False
    assert NoProvider.calls == 0
    assert provider.calls == 0
    assert rollback_calls == []
    assert close_calls == []


@pytest.mark.asyncio
async def test_sqlite_inmemory_shared_pool_rejects_before_provider():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    sid = "session-mem"
    set_current_session_id(sid)
    _seed(session, sid)
    session.commit()

    class NoProvider:
        calls = 0

        async def chat(self, **kwargs):
            self.calls += 1
            raise AssertionError("provider must not be called")

    provider = NoProvider()
    outcome = await run_server_owned_semantic_index(session, provider, sid, "exchange-1", 11, 12)
    session.close()
    engine.dispose()

    assert outcome.rejected_reason == "session_ownership_not_verified"
    assert outcome.session_ownership_explicitly_verified is False
    assert outcome.model_input_binding_verified is False
    assert NoProvider.calls == 0
    assert provider.calls == 0
