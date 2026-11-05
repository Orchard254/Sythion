"""Association is durable metadata, never authority to replay/continue work."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timedelta
from threading import Barrier

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.api.routes import assistant_turn
from backend.api.schemas import AssistantTurnRequest, TaskState
from backend.models.database import (
    Base, ConversationExchange, ConversationTaskAssociation, ConversationTurn, Task,
)
from backend.services.conversation_runtime import ConversationRuntime


@pytest.fixture
def db(tmp_path, monkeypatch):
    # File-backed and FK-enforcing, with no application profile or model access.
    engine = create_engine(f"sqlite:///{tmp_path / 'associations.sqlite'}")

    @event.listens_for(engine, "connect")
    def foreign_keys(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "association-test")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


def task(db, task_id="original", state="created"):
    row = Task(id=task_id, original_request="same request", state=state, version=1)
    db.add(row)
    db.commit()
    return row


def assert_counts(db, exchanges, turns, associations):
    assert db.query(ConversationExchange).count() == exchanges
    assert db.query(ConversationTurn).count() == turns
    assert db.query(ConversationTaskAssociation).count() == associations


@pytest.fixture
def dispatch(monkeypatch):
    class Engine:
        def __init__(self, *a, **k):
            pass

        async def run_turn(self, *a, **k):
            return {"assistant_text": "Requested background work.", "commands": [
                {"type": "start_task", "request": "same request"},
            ]}

    async def no_worker(*a, **k):
        pytest.fail("Association tests must not run a worker")

    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", Engine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda: object())
    monkeypatch.setattr("backend.api.routes.run_task", no_worker)


@pytest.mark.asyncio
async def test_dispatch_persists_exact_identity_before_scheduling(db, dispatch):
    bg = BackgroundTasks()
    original_add = bg.add_task

    def observe(*args, **kwargs):
        # Observe committed data from another session, not pending ORM rows.
        with sessionmaker(bind=db.bind)() as reader:
            row = reader.query(ConversationTaskAssociation).one()
            exchange = reader.query(ConversationExchange).one()
            assert row.exchange_id == exchange.id
            assert reader.get(ConversationTurn, row.user_turn_id).role == "user"
            assert reader.get(ConversationTurn, row.assistant_turn_id).role == "assistant"
            assert row.task_id == args[2]
        original_add(*args, **kwargs)

    bg.add_task = observe
    response = await assistant_turn(AssistantTurnRequest(text="do work", client_request_id="association-persist"), bg, db, None)
    identity = ConversationRuntime(db).get_task_association(response.exchange_id)
    assert identity.task_id == response.background_task_ids[0]
    assert len(bg.tasks) == 1
    assert_counts(db, 1, 2, 1)


@pytest.mark.asyncio
async def test_identity_survives_database_reopen_and_completion(db):
    original = task(db)
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", task_id=original.id)
    # Completion between persistence and a later follow-up must not lose binding.
    original.state = "completed"
    original.final_result = "Original result"
    original.version = 9
    db.commit()
    url = db.bind.url
    db.close()
    db.bind.dispose()
    reopened = create_engine(url)
    try:
        with sessionmaker(bind=reopened)() as reader:
            other = ConversationRuntime(reader)
            assert other.get_task_association(identity.exchange_id) == identity
            assert other.get_task_association(identity.exchange_id) == identity
            assert reader.query(Task).count() == 1
            persisted = reader.get(Task, identity.task_id)
            assert (persisted.state, persisted.version, persisted.final_result) == (
                "completed", 9, "Original result",
            )
    finally:
        reopened.dispose()


@pytest.mark.asyncio
async def test_pure_conversation_and_other_session_have_no_association(db):
    original = task(db)
    runtime = ConversationRuntime(db)
    pure = await runtime._persist_conversation("same", "answer")
    linked = await runtime._persist_conversation("same", "answer", task_id=original.id)
    assert runtime.get_task_association(pure.exchange_id) is None
    runtime.session_id = "different-session"
    assert runtime.get_task_association(linked.exchange_id) is None
    assert_counts(db, 2, 4, 1)


@pytest.mark.asyncio
@pytest.mark.parametrize("collision", ["exchange", "task"])
async def test_duplicate_identity_fails_atomically_without_rebinding(db, collision):
    first = task(db, "first", "completed")
    second = task(db, "second")
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", "exchange", task_id=first.id)
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await runtime._persist_conversation(
            "same", "answer", "exchange" if collision == "exchange" else "another",
            task_id=second.id if collision == "exchange" else first.id,
        )
    assert_counts(db, 1, 2, 1)
    assert runtime.get_task_association(identity.exchange_id) == identity
    assert (db.get(Task, "first").state, db.get(Task, "second").state) == ("completed", "created")


@pytest.mark.asyncio
async def test_unknown_task_does_not_persist_partial_exchange(db):
    with pytest.raises(RuntimeError, match="Conversation persistence failed"):
        await ConversationRuntime(db)._persist_conversation("same", "answer", task_id="missing")
    assert_counts(db, 0, 0, 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["insert", "commit"])
async def test_association_persistence_failure_never_dispatches(db, dispatch, monkeypatch, failure):
    if failure == "insert":
        def fail(*args):
            raise RuntimeError("association insert failed")

        event.listen(ConversationTaskAssociation, "before_insert", fail)
    else:
        original_commit = db.commit

        def fail():
            if db.query(ConversationTaskAssociation).count() > 0:
                raise RuntimeError("association commit failed")
            original_commit()

        monkeypatch.setattr(db, "commit", fail)
    bg = BackgroundTasks()
    try:
        with pytest.raises(HTTPException) as error:
            await assistant_turn(AssistantTurnRequest(text="do work", client_request_id="association-test"), bg, db, None)
        assert error.value.status_code == 500
        assert bg.tasks == []
        assert_counts(db, 0, 0, 0)
        assert db.query(Task).one().state == TaskState.FAILED.value
    finally:
        if failure == "insert":
            event.remove(ConversationTaskAssociation, "before_insert", fail)


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["transition", "schedule"])
async def test_dispatch_failure_cleans_only_exact_association(db, dispatch, monkeypatch, failure):
    from backend.services.task_runner import TaskRunner

    sibling_task = task(db, "sibling")
    runtime = ConversationRuntime(db)
    sibling = await runtime._persist_conversation("do work", "Requested background work.", task_id=sibling_task.id)
    bg = BackgroundTasks()
    if failure == "transition":
        original_transition = TaskRunner.transition_state

        async def fail(self, *args, **kwargs):
            if kwargs.get("new_state") == TaskState.RUNNING:
                raise RuntimeError("transition failed")
            return await original_transition(self, *args, **kwargs)

        monkeypatch.setattr(TaskRunner, "transition_state", fail)
    else:
        def fail(*args, **kwargs):
            raise RuntimeError("schedule failed")

        bg.add_task = fail
    with pytest.raises(HTTPException) as error:
        await assistant_turn(AssistantTurnRequest(text="do work", client_request_id="association-test"), bg, db, None)
    assert error.value.status_code == 500
    assert bg.tasks == []
    assert_counts(db, 1, 2, 1)
    assert runtime.get_task_association(sibling.exchange_id) == sibling
    assert db.get(Task, "sibling").state == "created"
    assert db.query(Task).filter(Task.id != "sibling").one().state == "failed"


@pytest.mark.asyncio
async def test_cleanup_requires_matching_exact_turn_identity(db):
    original = task(db)
    runtime = ConversationRuntime(db)
    linked = await runtime._persist_conversation("same", "answer", task_id=original.id)
    sibling = await runtime._persist_conversation("same", "answer")
    forged = dict(linked, turn_ids=sibling.turn_ids)
    with pytest.raises(ValueError, match="exact turn identities"):
        await runtime._unpersist_conversation(forged)
    assert_counts(db, 2, 4, 1)
    assert runtime.get_task_association(linked.exchange_id) == linked


@pytest.mark.asyncio
async def test_cleanup_failure_rolls_back_association_and_exact_rows(db, monkeypatch):
    original = task(db)
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", task_id=original.id)

    def fail():
        raise RuntimeError("cleanup commit failed")

    monkeypatch.setattr(db, "commit", fail)
    with pytest.raises(RuntimeError, match="cleanup commit failed"):
        await runtime._unpersist_conversation(identity)
    assert_counts(db, 1, 2, 1)
    assert runtime.get_task_association(identity.exchange_id) == identity
    assert db.get(Task, original.id).state == "created"


@pytest.mark.asyncio
async def test_duplicate_binding_from_another_session_cannot_replace_completed_task(db):
    original = task(db, state="completed")
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", task_id=original.id)
    with sessionmaker(bind=db.bind)() as other_db:
        other = ConversationRuntime(other_db)
        with pytest.raises(RuntimeError, match="Conversation persistence failed"):
            await other._persist_conversation("same", "answer", task_id=original.id)
    assert_counts(db, 1, 2, 1)
    assert runtime.get_task_association(identity.exchange_id) == identity
    assert db.get(Task, original.id).state == "completed"


@pytest.mark.asyncio
async def test_lookup_reads_persisted_identity_without_flushing_pending_changes(db):
    original = task(db)
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", task_id=original.id)
    pending = db.query(ConversationTaskAssociation).one()
    pending.task_id = "not-persisted"
    assert runtime.get_task_association(identity.exchange_id) == identity
    assert pending in db.dirty
    db.rollback()


@pytest.mark.asyncio
async def test_duplicate_cleanup_fails_without_touching_sibling_or_task(db):
    original = task(db)
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("same", "answer", task_id=original.id)
    sibling = await runtime._persist_conversation("same", "answer")
    await runtime._unpersist_conversation(identity)
    with pytest.raises(RuntimeError, match="expected persisted rows"):
        await runtime._unpersist_conversation(identity)
    assert_counts(db, 1, 2, 0)
    assert db.query(ConversationExchange).one().id == sibling.exchange_id
    assert db.get(Task, original.id).state == "created"


@pytest.mark.asyncio
async def test_dispatch_cleanup_failure_surfaces_error_and_preserves_durable_identity(db, dispatch, monkeypatch):
    from backend.services.task_runner import TaskRunner

    transition = TaskRunner.transition_state

    async def fail_transition(self, *args, **kwargs):
        if kwargs.get("new_state") == TaskState.RUNNING:
            raise RuntimeError("transition failed")
        return await transition(self, *args, **kwargs)

    async def fail_cleanup(*args, **kwargs):
        raise RuntimeError("cleanup failed")

    monkeypatch.setattr(TaskRunner, "transition_state", fail_transition)
    monkeypatch.setattr(ConversationRuntime, "_unpersist_conversation", fail_cleanup)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as error:
        await assistant_turn(AssistantTurnRequest(text="do work", client_request_id="association-test"), bg, db, None)
    assert error.value.status_code == 500
    assert "Could not remove assistant conversation" in error.value.detail
    assert bg.tasks == []
    assert_counts(db, 1, 2, 1)
    exchange = db.query(ConversationExchange).one()
    identity = ConversationRuntime(db).get_task_association(exchange.id)
    assert identity.task_id == db.query(Task).one().id
    assert db.query(Task).one().state == "failed"


@pytest.mark.asyncio
@pytest.mark.parametrize("expired", ["exchange", "user_turn", "assistant_turn"])
async def test_history_retention_removes_only_expired_association(db, expired):
    from backend.services.retention import purge_conversation_history

    runtime = ConversationRuntime(db)
    first = task(db, "first")
    second = task(db, "second")
    identity = await runtime._persist_conversation("same", "answer", task_id=first.id)
    sibling = await runtime._persist_conversation("same", "answer", task_id=second.id)
    now = datetime.utcnow()
    old = now - timedelta(days=100)
    if expired == "exchange":
        db.get(ConversationExchange, identity.exchange_id).created_at = old
    else:
        turn_id = identity.user_turn_id if expired == "user_turn" else identity.assistant_turn_id
        db.get(ConversationTurn, turn_id).created_at = old
    db.commit()
    purge_conversation_history(db, days=90, now=now)
    assert runtime.get_task_association(identity.exchange_id) is None
    assert runtime.get_task_association(sibling.exchange_id) == sibling
    assert db.query(Task).count() == 2


@pytest.mark.asyncio
async def test_task_purge_removes_association_without_changing_other_work(db):
    from backend.services.retention import purge_task

    runtime = ConversationRuntime(db)
    first = task(db, "first", "completed")
    second = task(db, "second", "running")
    identity = await runtime._persist_conversation("same", "answer", task_id=first.id)
    sibling = await runtime._persist_conversation("same", "answer", task_id=second.id)
    assert purge_task(db, first.id) is not None
    assert runtime.get_task_association(identity.exchange_id) is None
    assert runtime.get_task_association(sibling.exchange_id) == sibling
    assert db.get(Task, "second").state == "running"


def test_concurrent_associations_have_one_winner_without_replay(db):
    task(db, "original", "completed")
    barrier = Barrier(2)

    def persist(index):
        with sessionmaker(bind=db.bind)() as writer:
            runtime = ConversationRuntime(writer)
            barrier.wait(timeout=10)
            try:
                return asyncio.run(runtime._persist_conversation(
                    "same", "answer", f"concurrent-{index}", task_id="original",
                ))
            except RuntimeError:
                return None

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(persist, range(2)))
    winners = [identity for identity in results if identity is not None]
    assert len(winners) == 1
    assert_counts(db, 1, 2, 1)
    assert ConversationRuntime(db).get_task_association(winners[0].exchange_id) == winners[0]
    assert db.get(Task, "original").state == "completed"


@pytest.mark.asyncio
async def test_additive_table_creation_preserves_existing_conversation(db):
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("existing", "answer")
    task(db)
    # Model the old schema using only this isolated test database.
    ConversationTaskAssociation.__table__.drop(db.bind)
    Base.metadata.create_all(db.bind)
    assert_counts(db, 1, 2, 0)
    assert db.get(ConversationExchange, identity.exchange_id).user_text == "existing"
    linked = await runtime._persist_conversation("new", "answer", task_id="original")
    assert runtime.get_task_association(linked.exchange_id) == linked
    assert_counts(db, 2, 4, 1)
