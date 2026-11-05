"""Narrow fail-closed tests for candidate discovery/model-input state changes."""

import asyncio
import json
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.core.session import set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import (
    Base as GroundedBase,
    GroundedDiscussionManager,
    DiscussionRow,
    DiscussionExchangeLinkRow,
)
from backend.services.grounded_discussion_index import DiscussionIndexStateRow
from backend.services.grounded_discussion_server_owned_semantic_index import (
    run_server_owned_semantic_index,
)


def _engine(tmp_path: Path, name: str):
    db_path = tmp_path / name
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False},
        future=True,
    )
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    return engine, db_path


def _seed(session, sid):
    session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="a"))
    session.add(
        ConversationExchange(
            id="exchange-1",
            session_id=sid,
            user_text="server-owned user text",
            assistant_text="server-owned assistant text",
            status="ok",
        )
    )
    session.flush()
    manager = GroundedDiscussionManager(session)
    created = manager.create_discussion(
        session_id=sid,
        label="Candidate Label",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
    )
    session.commit()
    return created.discussion_id


@pytest.mark.asyncio
async def test_candidate_label_rename_during_provider_await_fails_closed(tmp_path):
    engine, db_path = _engine(tmp_path, "label.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-label"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            mutator = Factory()
            try:
                discussion = mutator.query(DiscussionRow).filter_by(session_id=sid).one()
                discussion.label = "Renamed Label"
                mutator.commit()
            finally:
                mutator.close()
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    outcome = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert outcome.rejected_reason == "ValueError"
    assert outcome.canonical_source_mutation_detected is True
    assert outcome.applied is False
    assert Factory().query(DiscussionIndexStateRow).count() == 0
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_membership_removal_during_provider_await_fails_closed(tmp_path):
    engine, db_path = _engine(tmp_path, "membership.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-membership"
    set_current_session_id(sid)
    seed = Factory()
    try:
        did = _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            mutator = Factory()
            try:
                link = mutator.query(DiscussionExchangeLinkRow).filter_by(session_id=sid).one()
                mutator.delete(link)
                mutator.commit()
            finally:
                mutator.close()
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    outcome = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert outcome.rejected_reason == "ValueError"
    assert outcome.canonical_source_mutation_detected is True
    assert outcome.applied is False
    assert Factory().query(DiscussionIndexStateRow).count() == 0
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_candidate_addition_during_provider_await_fails_closed(tmp_path):
    engine, db_path = _engine(tmp_path, "candidate-addition.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-addition"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            mutator = Factory()
            try:
                manager = GroundedDiscussionManager(mutator)
                manager.create_discussion(
                    session_id=sid,
                    label="Added Later",
                    label_provenance="human_stated",
                    member_exchanges=[],
                )
                mutator.commit()
            finally:
                mutator.close()
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    outcome = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert outcome.rejected_reason == "ValueError"
    assert outcome.canonical_source_mutation_detected is True
    assert outcome.applied is False
    assert Factory().query(DiscussionIndexStateRow).count() == 0
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_no_candidate_mutation_applies_and_replay_is_idempotent(tmp_path):
    engine, db_path = _engine(tmp_path, "idempotent.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-idempotent"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    first = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert first.applied is False
    assert first.rejected_reason is None
    # Terminal replay after candidate mutation must not self-invalidate.
    mutator = Factory()
    try:
        discussion = mutator.query(DiscussionRow).filter_by(session_id=sid).one()
        discussion.label = "Changed After Apply"
        mutator.commit()
    finally:
        mutator.close()

    caller2 = Factory()
    second = await run_server_owned_semantic_index(
        caller2, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert second.rejected_reason is None
    assert second.applied is False
    caller.close()
    caller2.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_writer_real_sqlite_label_race_between_revalidation_and_write_fail_closed(tmp_path):
    engine, db_path = _engine(tmp_path, "race-label.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-race-label"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    from backend.services.grounded_discussion_index import canonical_exchange_input_digest as original_digest

    calls = {"count": 0}

    def race_digest(session, session_id, exchange_id, user_turn_id, assistant_turn_id):
        result = original_digest(session, session_id, exchange_id, user_turn_id, assistant_turn_id)
        calls["count"] += 1
        # Mutate after first revalidation check begins.
        if calls["count"] == 2:
            mutator = Factory()
            try:
                discussion = mutator.query(DiscussionRow).filter_by(session_id=sid).one()
                discussion.label = "raced rename"
                mutator.commit()
            finally:
                mutator.close()
        return result

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    import backend.services.grounded_discussion_server_owned_semantic_index as orch
    with pytest.MonkeyPatch().context() as patcher:
        patcher.setattr("backend.services.grounded_discussion_index.canonical_exchange_input_digest", race_digest)
        patcher.setattr(orch, "canonical_exchange_input_digest", race_digest, raising=False)
        outcome = await run_server_owned_semantic_index(
            caller, Client(), sid, "exchange-1", 11, 12,
            session_factory=Factory, owns_session=True,
        )

    assert outcome.applied is False
    assert outcome.rejected_reason in {"ValueError", "capture_failed"}
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_begin_immediate_failure_fails_closed_without_writes(tmp_path):
    engine, db_path = _engine(tmp_path, "lock-failure.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-lock-failure"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    from sqlalchemy.orm import session as sqlalchemy_session_mod

    original_execute = sqlalchemy_session_mod.Session.execute

    def failing_execute(self, statement, *args, **kwargs):
        if "BEGIN IMMEDIATE" in str(statement).upper():
            raise RuntimeError("injected BEGIN IMMEDIATE failure")
        return original_execute(self, statement, *args, **kwargs)

    with pytest.MonkeyPatch().context() as patcher:
        patcher.setattr(sqlalchemy_session_mod.Session, "execute", failing_execute)
        outcome = await run_server_owned_semantic_index(
            caller, Client(), sid, "exchange-1", 11, 12,
            session_factory=Factory, owns_session=True,
        )

    assert outcome.applied is False
    assert outcome.rejected_reason in {"ValueError", "RuntimeError"}
    assert Factory().query(DiscussionIndexStateRow).count() == 0
    caller.close()
    engine.dispose()



@pytest.mark.asyncio
async def test_concurrent_wal_writer_between_capture_reads_is_consistent_then_rejected(tmp_path):
    engine, db_path = _engine(tmp_path, "wal-capture-consistency.db")
    from sqlalchemy import text as _text
    with engine.connect() as _conn:
        _conn.execute(_text("PRAGMA journal_mode=WAL"))
        _conn.commit()
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-wal-capture"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    from backend.services.grounded_discussions import GroundedDiscussionManager

    original_read = GroundedDiscussionManager.read_session_discussions

    def raced_read(self, *args, **kwargs):
        mutator = Factory()
        try:
            discussion = mutator.query(DiscussionRow).filter_by(session_id=sid).one()
            discussion.label = "concurrent-wal-rename"
            mutator.commit()
        finally:
            mutator.close()
        return original_read(self, *args, **kwargs)

    with pytest.MonkeyPatch().context() as patcher:
        patcher.setattr(GroundedDiscussionManager, "read_session_discussions", raced_read)
        outcome = await run_server_owned_semantic_index(
            caller, Client(), sid, "exchange-1", 11, 12,
            session_factory=Factory, owns_session=True,
        )

    assert outcome.applied is False
    assert outcome.rejected_reason in {"ValueError", "capture_failed"}
    assert outcome.canonical_source_mutation_detected is True
    assert Factory().query(DiscussionIndexStateRow).count() == 0
    caller.close()
    engine.dispose()

@pytest.mark.asyncio
async def test_ambiguous_multiple_terminal_rows_fail_closed_without_writes(tmp_path):
    engine, db_path = _engine(tmp_path, "multi-row.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-multi-row"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    from backend.services.grounded_discussion_index import (
        DiscussionIndexStateRow, INDEX_STATUS_COMPLETE,
    )
    row_session = Factory()
    try:
        row_session.add(DiscussionIndexStateRow(
            idempotency_key="idx-old-1",
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            input_digest="old-digest-1",
            status=INDEX_STATUS_COMPLETE,
            created_at=__import__("datetime").datetime.utcnow(),
            updated_at=__import__("datetime").datetime.utcnow(),
        ))
        row_session.add(DiscussionIndexStateRow(
            idempotency_key="idx-old-2",
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=11,
            assistant_turn_id=12,
            input_digest="old-digest-2",
            status=INDEX_STATUS_COMPLETE,
            created_at=__import__("datetime").datetime.utcnow(),
            updated_at=__import__("datetime").datetime.utcnow(),
        ))
        row_session.commit()
    finally:
        row_session.close()

    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    outcome = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert outcome.applied is False
    assert outcome.rejected_reason in {"ValueError", "RuntimeError"}
    assert Factory().query(DiscussionIndexStateRow).count() == 2
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_historical_canonical_mismatch_terminal_row_fails_closed_without_writes(tmp_path):
    engine, db_path = _engine(tmp_path, "historical-mismatch.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-historical"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    first = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert first.applied is False
    assert first.rejected_reason is None

    mutator = Factory()
    try:
        exchange = mutator.query(ConversationExchange).filter_by(id="exchange-1", session_id=sid).one()
        exchange.user_text = "historically changed"
        mutator.commit()
    finally:
        mutator.close()

    caller2 = Factory()
    second = await run_server_owned_semantic_index(
        caller2, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert second.applied is False
    assert second.rejected_reason in {"ValueError", "RuntimeError"}
    caller.close()
    caller2.close()
    engine.dispose()
@pytest.mark.asyncio
async def test_terminal_row_different_canonical_identity_rejects_before_writes(tmp_path):
    engine, db_path = _engine(tmp_path, "identity-mismatch.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-identity-mismatch"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    from backend.services.grounded_discussion_index import (
        DiscussionIndexStateRow, INDEX_STATUS_COMPLETE,
    )
    row_session = Factory()
    try:
        row_session.add(DiscussionIndexStateRow(
            idempotency_key="idx-terminal-identity",
            session_id=sid,
            exchange_id="exchange-1",
            user_turn_id=99,
            assistant_turn_id=98,
            input_digest="any",
            status=INDEX_STATUS_COMPLETE,
            created_at=__import__("datetime").datetime.utcnow(),
            updated_at=__import__("datetime").datetime.utcnow(),
        ))
        row_session.commit()
    finally:
        row_session.close()

    before_discussions = Factory().query(DiscussionRow).count()
    before_memberships = Factory().query(DiscussionExchangeLinkRow).count()

    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    outcome = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert outcome.applied is False
    assert outcome.rejected_reason in {"ValueError", "RuntimeError"}
    assert Factory().query(DiscussionIndexStateRow).count() == 1
    assert Factory().query(DiscussionRow).count() == before_discussions
    assert Factory().query(DiscussionExchangeLinkRow).count() == before_memberships
    caller.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_historical_digest_mismatch_reports_no_current_mutation(tmp_path):
    engine, db_path = _engine(tmp_path, "historical-digest.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-historical-digest"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    first = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert first.applied is False
    assert first.rejected_reason is None

    mutator = Factory()
    try:
        from backend.services.grounded_discussion_index import DiscussionIndexStateRow
        row = mutator.query(DiscussionIndexStateRow).one()
        row.input_digest = "historically-different-digest"
        mutator.commit()
    finally:
        mutator.close()

    before_state = Factory().query(DiscussionIndexStateRow).count()
    before_discussions = Factory().query(DiscussionRow).count()
    before_memberships = Factory().query(DiscussionExchangeLinkRow).count()

    caller2 = Factory()
    second = await run_server_owned_semantic_index(
        caller2, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert second.applied is False
    assert second.rejected_reason in {"ValueError", "RuntimeError"}
    assert second.canonical_source_mutation_detected is False
    assert Factory().query(DiscussionIndexStateRow).count() == before_state
    assert Factory().query(DiscussionRow).count() == before_discussions
    assert Factory().query(DiscussionExchangeLinkRow).count() == before_memberships
    caller.close()
    caller2.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_normal_replay_after_candidate_only_change_is_noop_no_false_freshness(tmp_path):
    engine, db_path = _engine(tmp_path, "candidate-only-replay.db")
    Factory = sessionmaker(bind=engine, autoflush=False, future=True)
    sid = "session-candidate-only-replay"
    set_current_session_id(sid)
    seed = Factory()
    try:
        _seed(seed, sid)
    finally:
        seed.close()
    caller = Factory()

    class Client:
        async def chat(self, *, messages, **kwargs):
            return json.dumps(
                {"status": "insufficient", "proposals": [], "clarification": "Need more", "reason": "sparse"}
            )

    first = await run_server_owned_semantic_index(
        caller, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert first.applied is False
    assert first.rejected_reason is None

    mutator = Factory()
    try:
        discussion = mutator.query(DiscussionRow).filter_by(session_id=sid).one()
        discussion.label = "candidate only rename"
        mutator.commit()
    finally:
        mutator.close()

    before_state = Factory().query(DiscussionIndexStateRow).count()
    before_discussions = Factory().query(DiscussionRow).count()
    before_memberships = Factory().query(DiscussionExchangeLinkRow).count()

    caller2 = Factory()
    second = await run_server_owned_semantic_index(
        caller2, Client(), sid, "exchange-1", 11, 12,
        session_factory=Factory, owns_session=True,
    )
    assert second.applied is False
    assert second.rejected_reason is None
    assert second.canonical_source_mutation_detected is False
    assert Factory().query(DiscussionIndexStateRow).count() == before_state
    assert Factory().query(DiscussionRow).count() == before_discussions
    assert Factory().query(DiscussionExchangeLinkRow).count() == before_memberships
    caller.close()
    caller2.close()
    engine.dispose()
