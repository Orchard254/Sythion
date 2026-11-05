"""Isolated checks that task deletion/retention does not delete conversation memory.

A task is work inside a discussion context; the exchange, turns, named context
and sibling task associations must survive task purge/retention. Context and
conversation deletion remain separate explicit actions.
"""

from datetime import datetime, timedelta

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models.database import (
    Base,
    ConversationExchange,
    ConversationTaskAssociation,
    ConversationTurn,
    DiscussionContext,
    DiscussionContextMembership,
    Task,
)
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.discussion_contexts import read_exchange_context
from backend.services.retention import purge_expired, purge_task


SESSION_ID = "task-retention-preserve"


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'task-retention-preserve.db'}")

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", SESSION_ID)
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


async def _shared_context(db, task_state="running", purge_aged=False):
    db.add(Task(id="task-a", original_request="Chess plan", state=task_state, version=1))
    db.add(Task(id="task-b", original_request="Chess plan", state=task_state, version=1))
    db.commit()
    if purge_aged:
        old = datetime.utcnow() - timedelta(days=200)
        db.get(Task, "task-a").created_at = old
        db.get(Task, "task-b").created_at = old
        db.commit()

    runtime = ConversationRuntime(db)
    first = await runtime._persist_conversation("Chess plan", "Accepted", task_id="task-a")
    origin = read_exchange_context(db, runtime.session_id, first.exchange_id)
    second = await runtime._persist_conversation(
        "Follow-up", "Next", context_id=origin.context_id, task_id="task-b"
    )
    followup = read_exchange_context(db, runtime.session_id, second.exchange_id)
    db.commit()
    return runtime, first, second, origin, followup


def _assert_preserved(db, first, second, origin, followup, exchanges=2, turns=4, contexts=1, memberships=2, associations=1):
    assert db.query(ConversationExchange).count() == exchanges
    assert db.query(ConversationTurn).count() == turns
    assert db.query(DiscussionContext).count() == contexts
    assert db.query(DiscussionContextMembership).count() == memberships
    assert db.query(ConversationTaskAssociation).count() == associations
    assert read_exchange_context(db, SESSION_ID, first.exchange_id) == origin
    assert read_exchange_context(db, SESSION_ID, second.exchange_id) == followup
    context = db.get(DiscussionContext, origin.context_id)
    assert context is not None and context.name == "Chess plan"


@pytest.mark.asyncio
async def test_purge_task_preserves_shared_conversation_and_other_association(db):
    runtime, first, second, origin, followup = await _shared_context(db)

    assert purge_task(db, "task-a") is not None

    assert db.get(Task, "task-a") is None
    assert db.get(Task, "task-b").state == "running"
    assert runtime.get_task_association(first.exchange_id) is None
    assert runtime.get_task_association(second.exchange_id).task_id == "task-b"
    _assert_preserved(db, first, second, origin, followup, associations=1)


@pytest.mark.asyncio
async def test_purge_task_removing_all_tasks_preserves_shared_context(db):
    runtime, first, second, origin, followup = await _shared_context(db)

    assert purge_task(db, "task-a") is not None
    assert purge_task(db, "task-b") is not None

    assert db.get(Task, "task-a") is None
    assert db.get(Task, "task-b") is None
    assert db.query(ConversationTaskAssociation).count() == 0
    _assert_preserved(db, first, second, origin, followup, associations=0)


@pytest.mark.asyncio
async def test_task_retention_expiry_preserves_conversation_and_context(db):
    runtime, first, second, origin, followup = await _shared_context(db, task_state="completed", purge_aged=True)

    receipts = purge_expired(db, days=90)
    assert {r["task_id"] for r in receipts} == {"task-a", "task-b"}
    assert db.query(Task).count() == 0
    assert db.query(ConversationTaskAssociation).count() == 0
    _assert_preserved(db, first, second, origin, followup, associations=0)


@pytest.mark.asyncio
async def test_task_purge_commit_failure_restores_conversation_and_context(db, monkeypatch):
    runtime, first, second, origin, followup = await _shared_context(db)

    def fail_commit():
        raise RuntimeError("retention commit failed")

    before_counts = (
        db.query(Task).count(),
        db.query(ConversationExchange).count(),
        db.query(ConversationTurn).count(),
        db.query(DiscussionContext).count(),
        db.query(DiscussionContextMembership).count(),
        db.query(ConversationTaskAssociation).count(),
    )

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="retention commit failed"):
        purge_task(db, "task-a")

    assert (
        db.query(Task).count(),
        db.query(ConversationExchange).count(),
        db.query(ConversationTurn).count(),
        db.query(DiscussionContext).count(),
        db.query(DiscussionContextMembership).count(),
        db.query(ConversationTaskAssociation).count(),
    ) == before_counts
    assert db.get(Task, "task-a").state == "running"
    assert runtime.get_task_association(first.exchange_id).task_id == "task-a"
    assert read_exchange_context(db, SESSION_ID, first.exchange_id) == origin
    assert read_exchange_context(db, SESSION_ID, second.exchange_id) == followup
