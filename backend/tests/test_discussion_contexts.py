"""Named context persistence only: no model matching, permissions, or effects."""

from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier
from unittest.mock import AsyncMock

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.api.routes import assistant_turn
from backend.api.schemas import AssistantTurnRequest
from backend.core.db_manager import DatabaseManager
from backend.models.database import (
    Base, Task, ConversationExchange, ConversationTurn, ConversationTaskAssociation,
    DiscussionContext, DiscussionContextMembership,
)
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.discussion_contexts import (
    context_name, ensure_exchange_context, prune_empty_contexts, read_exchange_context,
)
from backend.services.retention import purge_conversation_history
from backend.services.task_instruction_revision import TaskInstructionRevision
from backend.services.task_runner import TaskRunner


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'named-contexts.db'}")

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "named-context-test")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def counts(db):
    return tuple(db.query(model).count() for model in (
        ConversationExchange, ConversationTurn, DiscussionContext, DiscussionContextMembership,
    ))


@pytest.mark.asyncio
async def test_ordinary_conversation_automatically_gets_named_exact_context_without_task(db):
    runtime = ConversationRuntime(db)
    runtime._build_context = AsyncMock(return_value="")
    text = "  Explain chess openings\n for a beginner  "
    response = "Full answer " * 300
    runtime._get_model_response = AsyncMock(return_value=response)
    result = await runtime.handle_conversation(text)
    membership = read_exchange_context(db, runtime.session_id, result["conversation_id"])
    assert membership.name == "Explain chess openings for a beginner"
    assert membership.name_source == "initial_request_excerpt"
    exchange = db.get(ConversationExchange, membership.exchange_id)
    assert exchange.user_text == text and exchange.assistant_text == response
    assert db.get(ConversationTurn, membership.user_turn_id).role == "user"
    assert db.get(ConversationTurn, membership.assistant_turn_id).role == "assistant"
    assert db.query(Task).count() == 0
    assert counts(db) == (1, 2, 1, 1)


@pytest.mark.asyncio
async def test_identical_names_are_distinct_and_session_scoped(db):
    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("Chess plan", "one")
    second = await runtime._persist_conversation("Chess plan", "two")
    left = read_exchange_context(db, runtime.session_id, first.exchange_id)
    right = read_exchange_context(db, runtime.session_id, second.exchange_id)
    assert left.name == right.name == "Chess plan"
    assert left.context_id != right.context_id
    assert (left.user_turn_id, left.assistant_turn_id) == first.turn_ids
    assert (right.user_turn_id, right.assistant_turn_id) == second.turn_ids
    assert read_exchange_context(db, "other-session", first.exchange_id) is None
    assert read_exchange_context(db, runtime.session_id, "missing") is None


@pytest.mark.asyncio
async def test_explicit_same_session_membership_keeps_name_and_cleans_only_last_member(db):
    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("Original discussion", "answer")
    origin = read_exchange_context(db, runtime.session_id, first.exchange_id)
    second = await runtime._persist_conversation("Follow-up", "next", context_id=origin.context_id)
    followup = read_exchange_context(db, runtime.session_id, second.exchange_id)
    assert followup.context_id == origin.context_id and followup.name == origin.name
    await runtime._unpersist_conversation(first)
    assert counts(db) == (1, 2, 1, 1)
    assert read_exchange_context(db, runtime.session_id, second.exchange_id) == followup
    await runtime._unpersist_conversation(second)
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("target", ["other-session", "missing"])
async def test_foreign_or_missing_context_does_not_fall_back_or_persist_partial_rows(db, target):
    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("Original discussion", "answer")
    bound = read_exchange_context(db, runtime.session_id, first.exchange_id)
    if target == "other-session":
        runtime.session_id = "other-session"
        context_id = bound.context_id
    else:
        context_id = "ctx-invented"
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await runtime._persist_conversation("new", "reply", context_id=context_id)
    assert counts(db) == (1, 2, 1, 1)
    assert db.get(DiscussionContext, bound.context_id).name == "Original discussion"


@pytest.mark.asyncio
async def test_existing_membership_is_idempotent_but_never_reassigned_or_renamed(db):
    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("First", "one")
    second = await runtime._persist_conversation("Second", "two")
    a = read_exchange_context(db, runtime.session_id, first.exchange_id)
    b = read_exchange_context(db, runtime.session_id, second.exchange_id)
    assert ensure_exchange_context(db, runtime.session_id, first.exchange_id, *first.turn_ids) == a.context_id
    with pytest.raises(ValueError, match="cannot be reassigned"):
        ensure_exchange_context(db, runtime.session_id, first.exchange_id, *first.turn_ids, context_id=b.context_id)
    db.rollback()
    assert read_exchange_context(db, runtime.session_id, first.exchange_id) == a
    assert read_exchange_context(db, runtime.session_id, second.exchange_id) == b


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["context", "membership", "commit"])
async def test_failure_rolls_back_exchange_turns_context_and_membership(db, monkeypatch, failure):
    model = DiscussionContext if failure == "context" else DiscussionContextMembership

    def fail(*args):
        raise RuntimeError("injected context persistence failure")

    if failure == "commit":
        monkeypatch.setattr(db, "commit", fail)
    else:
        event.listen(model, "before_insert", fail)
    try:
        with pytest.raises(RuntimeError, match="Conversation persistence failed"):
            await ConversationRuntime(db)._persist_conversation("hello", "reply")
        assert counts(db) == (0, 0, 0, 0)
        assert db.query(Task).count() == 0
    finally:
        if failure != "commit":
            event.remove(model, "before_insert", fail)


@pytest.mark.asyncio
async def test_caller_owned_transaction_keeps_context_and_exchange_invisible_then_rolls_back(db):
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("hello", "reply", commit=False)
    assert read_exchange_context(db, runtime.session_id, identity.exchange_id) is not None
    with sessionmaker(bind=db.bind)() as reader:
        assert counts(reader) == (0, 0, 0, 0)
    db.rollback()
    assert counts(db) == (0, 0, 0, 0)


@pytest.mark.asyncio
async def test_duplicate_exchange_never_creates_or_reassigns_context(db):
    runtime = ConversationRuntime(db)
    original = await runtime._persist_conversation("hello", "reply", "exchange")
    context = read_exchange_context(db, runtime.session_id, original.exchange_id)
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await runtime._persist_conversation("different human text", "different", "exchange")
    assert counts(db) == (1, 2, 1, 1)
    assert read_exchange_context(db, runtime.session_id, original.exchange_id) == context
    assert db.get(ConversationExchange, "exchange").user_text == "hello"


@pytest.mark.asyncio
async def test_revision_reuses_exact_initiating_context_without_creating_work(db):
    db.add(Task(id="task", original_request="Chess plan", state="running", version=2))
    db.commit()
    runtime = ConversationRuntime(db)
    source = await runtime._persist_conversation("Chess plan", "Accepted", "source", task_id="task")
    context = read_exchange_context(db, runtime.session_id, source.exchange_id)
    runner = TaskRunner(db)
    assert (await runner.acquire_task_ownership("task", worker_id="test")).acquired
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("task")
    await revision.append("task", 2, "Maximum 15 minutes", runtime, "followup", "source")
    followup = read_exchange_context(db, runtime.session_id, "followup")
    assert (followup.context_id, followup.name) == (context.context_id, "Chess plan")
    assert not revision.claim("task", snapshot.revision)
    assert db.query(Task).count() == 1
    assert db.query(ConversationTaskAssociation).one().task_id == "task"
    assert counts(db) == (2, 4, 1, 2)


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", ["exchange", "user_turn", "assistant_turn"])
@pytest.mark.parametrize("fk_enabled", [True, False])
async def test_retention_removes_affected_membership_and_only_empty_contexts(db, expired, fk_enabled):
    # Toggle FK checks before any transaction; cleanup must not rely on cascades.
    db.connection().exec_driver_sql(f"PRAGMA foreign_keys={'ON' if fk_enabled else 'OFF'}")
    db.commit()
    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("Original", "answer")
    source = read_exchange_context(db, runtime.session_id, first.exchange_id)
    second = await runtime._persist_conversation("Current", "answer", context_id=source.context_id)
    now = datetime.utcnow()
    old = now - timedelta(days=100)
    if expired == "exchange":
        db.get(ConversationExchange, first.exchange_id).created_at = old
    else:
        turn_id = first.user_turn_id if expired == "user_turn" else first.assistant_turn_id
        db.get(ConversationTurn, turn_id).created_at = old
    db.commit()
    purge_conversation_history(db, days=90, now=now)
    assert read_exchange_context(db, runtime.session_id, first.exchange_id) is None
    remaining = read_exchange_context(db, runtime.session_id, second.exchange_id)
    assert remaining.context_id == source.context_id
    assert db.query(DiscussionContext).count() == 1
    db.get(ConversationExchange, second.exchange_id).created_at = old
    db.commit()
    purge_conversation_history(db, days=90, now=now)
    assert db.query(DiscussionContext).count() == 0
    assert db.query(DiscussionContextMembership).count() == 0


@pytest.mark.asyncio
async def test_cleanup_commit_failure_restores_context_and_membership(db, monkeypatch):
    runtime = ConversationRuntime(db)
    original = await runtime._persist_conversation("hello", "answer")
    context = read_exchange_context(db, runtime.session_id, original.exchange_id)

    def fail():
        raise RuntimeError("cleanup commit failed")

    monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError, match="cleanup commit failed"):
        await runtime._unpersist_conversation(original)
    assert counts(db) == (1, 2, 1, 1)
    assert read_exchange_context(db, runtime.session_id, original.exchange_id) == context


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transition", "membership"])
async def test_context_failure_never_schedules_work_or_leaves_success_metadata(db, monkeypatch, failure):
    class Engine:
        def __init__(self, *args):
            pass

        async def run_turn(self, *args, **kwargs):
            return {"assistant_text": "Accepted", "commands": [{"type": "start_task", "request": "Synthetic work"}]}

    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", Engine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda: object())

    async def no_worker(*args):
        pytest.fail("No real worker may execute")

    monkeypatch.setattr("backend.api.routes.run_task", no_worker)
    if failure == "transition":
        transition = TaskRunner.transition_state

        async def fail(self, *args, **kwargs):
            if kwargs.get("new_state").value == "running":
                raise RuntimeError("transition failed")
            return await transition(self, *args, **kwargs)

        monkeypatch.setattr(TaskRunner, "transition_state", fail)
    else:
        def fail(*args):
            raise RuntimeError("membership failed")

        event.listen(DiscussionContextMembership, "before_insert", fail)
    bg = BackgroundTasks()
    try:
        with pytest.raises(HTTPException) as error:
            await assistant_turn(AssistantTurnRequest(text="Synthetic work", client_request_id="discussion-context-turn"), bg, db, None)
        assert error.value.status_code == 500
        assert not bg.tasks
        assert counts(db) == (0, 0, 0, 0)
        assert db.query(Task).one().state == "failed"
    finally:
        if failure == "membership":
            event.remove(DiscussionContextMembership, "before_insert", fail)


def test_concurrent_identical_exchanges_get_distinct_contexts(db):
    import asyncio

    barrier = Barrier(2)

    def persist(index):
        with sessionmaker(bind=db.bind)() as writer:
            runtime = ConversationRuntime(writer)
            barrier.wait(timeout=10)
            identity = asyncio.run(runtime._persist_conversation("Chess plan", "answer", f"concurrent-{index}"))
            return read_exchange_context(writer, runtime.session_id, identity.exchange_id)

    with ThreadPoolExecutor(max_workers=2) as pool:
        contexts = list(pool.map(persist, range(2)))
    assert contexts[0].context_id != contexts[1].context_id
    assert contexts[0].name == contexts[1].name == "Chess plan"
    assert counts(db) == (2, 4, 2, 2)


@pytest.mark.asyncio
async def test_additive_schema_creation_preserves_legacy_rows_without_speculative_backfill(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "legacy-session")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    path = str(tmp_path / "legacy.db")
    old = create_engine(f"sqlite:///{path}")
    excluded = {"discussion_contexts", "discussion_context_memberships"}
    Base.metadata.create_all(old, tables=[table for table in Base.metadata.sorted_tables if table.name not in excluded])
    with sessionmaker(bind=old)() as writer:
        writer.add(ConversationExchange(id="legacy", session_id="legacy-session", user_text="Old exact text", assistant_text="Old exact reply"))
        writer.add_all([ConversationTurn(id=1, session_id="legacy-session", role="user", summary="legacy summary"),
                        ConversationTurn(id=2, session_id="legacy-session", role="assistant", summary="legacy reply")])
        writer.commit()
    old.dispose()
    manager = DatabaseManager(path)
    try:
        with manager.get_session() as migrated:
            assert counts(migrated) == (1, 2, 0, 0)
            legacy = migrated.get(ConversationExchange, "legacy")
            assert (legacy.user_text, legacy.assistant_text) == ("Old exact text", "Old exact reply")
            assert migrated.get(ConversationTurn, 1).summary == "legacy summary"
            runtime = ConversationRuntime(migrated)
            created = await runtime._persist_conversation("New discussion", "New reply")
            assert read_exchange_context(migrated, runtime.session_id, created.exchange_id).name == "New discussion"
            assert read_exchange_context(migrated, runtime.session_id, "legacy") is None
        manager.engine.dispose()
        restarted = DatabaseManager(path)
        try:
            with restarted.get_session() as reader:
                assert counts(reader) == (2, 4, 1, 1)
        finally:
            restarted.engine.dispose()
    finally:
        manager.engine.dispose()


def test_names_are_bounded_labels_not_unique_or_authorizing_keys():
    assert context_name("  ") == "Conversation"
    assert len(context_name("word " * 100)) == 96
    assert context_name("new_work=true; permission=approved") == "new_work=true; permission=approved"


@pytest.mark.asyncio
@pytest.mark.parametrize("fail_followup", [False, True])
async def test_legacy_task_context_is_backfilled_only_from_exact_association_on_accepted_revision(db, fail_followup):
    db.add(Task(id="legacy-task", original_request="Original chess discussion", state="running", version=2))
    db.commit()
    runtime = ConversationRuntime(db)
    source = await runtime._persist_conversation("Original chess discussion", "Accepted", "legacy-source", task_id="legacy-task")
    bound = read_exchange_context(db, runtime.session_id, source.exchange_id)
    db.query(DiscussionContextMembership).filter_by(exchange_id=source.exchange_id).delete()
    prune_empty_contexts(db, [bound.context_id])
    db.commit()
    assert counts(db) == (1, 2, 0, 0)
    runner = TaskRunner(db)
    assert (await runner.acquire_task_ownership("legacy-task", worker_id="test")).acquired
    revision = TaskInstructionRevision(runner)
    snapshot = revision.open("legacy-task")

    def fail(_mapper, _connection, target):
        if target.exchange_id == "followup":
            raise RuntimeError("followup membership failed")

    if fail_followup:
        event.listen(DiscussionContextMembership, "before_insert", fail)
    try:
        if fail_followup:
            with pytest.raises(RuntimeError, match="Conversation persistence failed"):
                await revision.append("legacy-task", 2, "15 minutes", runtime, "followup", "legacy-source")
            assert counts(db) == (1, 2, 0, 0)
            assert db.get(Task, "legacy-task").normalized_goal is None
            assert revision.claim("legacy-task", snapshot.revision)
        else:
            await revision.append("legacy-task", 2, "15 minutes", runtime, "followup", "legacy-source")
            source_context = read_exchange_context(db, runtime.session_id, "legacy-source")
            followup_context = read_exchange_context(db, runtime.session_id, "followup")
            assert source_context.context_id == followup_context.context_id
            assert source_context.name == "Original chess discussion"
            assert counts(db) == (2, 4, 1, 2)
        assert db.query(Task).count() == 1
    finally:
        if fail_followup:
            event.remove(DiscussionContextMembership, "before_insert", fail)


@pytest.mark.asyncio
@pytest.mark.parametrize("mode", ["history", "task"])
async def test_retention_commit_failure_rolls_back_context_and_record_changes(db, monkeypatch, mode):
    from backend.services.retention import purge_task

    runtime = ConversationRuntime(db)
    original = await runtime._persist_conversation("Retained discussion", "answer")
    context = read_exchange_context(db, runtime.session_id, original.exchange_id)
    old = datetime.utcnow() - timedelta(days=100)
    db.get(ConversationExchange, original.exchange_id).created_at = old
    if mode == "task":
        db.add(Task(id="task", original_request="Synthetic", state="completed", version=1))
        db.flush()
        # A task-specific turn referenced by this synthetic membership exercises
        # FK-safe complete-record cleanup too, not just transcript expiry.
        db.get(ConversationTurn, original.user_turn_id).task_id = "task"
    db.commit()

    def fail():
        raise RuntimeError("retention commit failed")

    monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError, match="retention commit failed"):
        if mode == "task":
            purge_task(db, "task")
        else:
            purge_conversation_history(db, days=90)
    assert counts(db) == (1, 2, 1, 1)
    assert read_exchange_context(db, runtime.session_id, original.exchange_id) == context
    if mode == "task":
        assert db.get(Task, "task").state == "completed"


@pytest.mark.asyncio
async def test_retention_does_not_sweep_unrelated_user_contexts(db):
    runtime = ConversationRuntime(db)
    original = await runtime._persist_conversation("Old", "answer")
    db.get(ConversationExchange, original.exchange_id).created_at = datetime.utcnow() - timedelta(days=100)
    db.add(DiscussionContext(id="unrelated", session_id=runtime.session_id, name="Preserved context", name_source="explicit"))
    db.commit()
    purge_conversation_history(db, days=90)
    assert db.query(DiscussionContext).one().id == "unrelated"


@pytest.mark.asyncio
async def test_context_read_does_not_flush_unsaved_user_edits(db, monkeypatch):
    runtime = ConversationRuntime(db)
    original = await runtime._persist_conversation("Original", "answer")
    identity = read_exchange_context(db, runtime.session_id, original.exchange_id)
    context = db.get(DiscussionContext, identity.context_id)
    context.name = "User's unsaved context name"

    def no_flush(*args, **kwargs):
        pytest.fail("Read lookup must not flush user changes")

    with monkeypatch.context() as patch:
        patch.setattr(db, "flush", no_flush)
        assert read_exchange_context(db, runtime.session_id, original.exchange_id) == identity
    assert context.name == "User's unsaved context name" and context in db.dirty
    db.rollback()


def test_multi_membership_reader_keeps_newest_exchange_and_context_count(db):
    """One named thread with many memberships must contribute one row — its
    newest exchange — and coverage must count contexts, not membership rows.
    Exchange ids are random UUIDs, so ordering by id drops the freshest answer.
    """
    from backend.services.discussion_contexts import (
        read_session_discussion_contexts,
        read_session_discussion_contexts_with_coverage,
    )

    sid = "thread-session"
    db.add(DiscussionContext(
        id="thread-ctx", session_id=sid, name="Thread",
        name_source="initial_request_excerpt", created_at=datetime.utcnow(),
    ))
    base = datetime(2026, 1, 1, 12, 0, 0)
    for i in range(5):
        # Newest exchange ('conv-e') sorts LAST by exchange_id ascending.
        exchange_id = f"conv-{chr(ord('a') + i)}"
        db.add(ConversationTurn(id=100 + i * 2, session_id=sid, role="user",
                                summary=f"user {i}"))
        db.add(ConversationTurn(id=101 + i * 2, session_id=sid, role="assistant",
                                summary=f"assistant {i}"))
        db.add(ConversationExchange(
            id=exchange_id, session_id=sid, user_text=f"user {i}",
            assistant_text=f"answer {i}", created_at=base + timedelta(minutes=i),
        ))
        db.add(DiscussionContextMembership(
            exchange_id=exchange_id, context_id="thread-ctx",
            user_turn_id=100 + i * 2, assistant_turn_id=101 + i * 2,
        ))
    db.commit()

    rows = read_session_discussion_contexts(db, sid, limit=3)
    assert len(rows) == 1  # one row per context, not per membership
    assert rows[0].exchange_id == "conv-e"  # newest by created_at

    contexts, coverage = read_session_discussion_contexts_with_coverage(db, sid, limit=3)
    assert len(contexts) == 1
    assert contexts[0].exchange_id == "conv-e"
    assert coverage.available_count == 1  # one context, not five memberships
    assert coverage.returned_count == 1
    assert coverage.omitted_count == 0
