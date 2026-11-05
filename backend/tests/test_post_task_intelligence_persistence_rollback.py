"""Temp-SQLite regression tests for post-task conversation-turn persistence.

Covers independent committed/verified turns, isolated optional extraction
failures, no phantom rows, preserved turn ordering, and caller-owned transaction
safety.
"""
import asyncio
from unittest.mock import patch

import pytest
from sqlalchemy import text

from backend.api.schemas import TaskState
from backend.core.session import set_current_session_id
from backend.models.database import ConversationTurn, MemoryEntry, Task
from backend.services.memory_service import MemoryService
from backend.services.post_task_intelligence import save_conversation_turn


def _task(task_id, request, result="done"):
    return Task(
        id=task_id,
        original_request=request,
        state=TaskState.COMPLETED.value,
        final_result=result,
    )


@pytest.mark.asyncio
async def test_turns_persist_before_first_extraction_failure(test_db):
    set_current_session_id("regr_session_first")
    task = _task("task_first", "my name is Alice")
    test_db.add(task)
    test_db.commit()

    with patch.object(MemoryService, "create", side_effect=RuntimeError("boom")):
        await save_conversation_turn(test_db, "task_first", task)

    turns = (
        test_db.query(ConversationTurn)
        .filter_by(task_id="task_first")
        .order_by(ConversationTurn.id)
        .all()
    )
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[1].role == "assistant"
    # Failed optional extraction must leave no memory rows (no phantom data).
    assert test_db.query(MemoryEntry).count() == 0


@pytest.mark.asyncio
async def test_first_extraction_succeeds_later_failure_preserves_prior(test_db):
    set_current_session_id("regr_session_later")
    task = _task("task_later", "my name is Bob and I prefer VS Code")
    test_db.add(task)
    test_db.commit()

    real_create = MemoryService.create
    state = {"calls": 0}

    def flaky_create(self, *args, **kwargs):
        state["calls"] += 1
        if state["calls"] == 2:
            raise RuntimeError("later boom")
        return real_create(self, *args, **kwargs)

    with patch.object(MemoryService, "create", flaky_create):
        await save_conversation_turn(test_db, "task_later", task)

    turns = test_db.query(ConversationTurn).filter_by(task_id="task_later").all()
    assert len(turns) == 2

    active = test_db.query(MemoryEntry).filter_by(status="ACTIVE").all()
    assert len(active) == 1
    assert active[0].value == "Bob"


@pytest.mark.asyncio
async def test_turn_ordering_preserved(test_db):
    set_current_session_id("regr_session_order")
    task = _task("task_order", "my name is Carol")
    test_db.add(task)
    test_db.commit()

    await save_conversation_turn(test_db, "task_order", task)

    turns = (
        test_db.query(ConversationTurn)
        .filter_by(task_id="task_order")
        .order_by(ConversationTurn.id)
        .all()
    )
    assert [t.role for t in turns] == ["user", "assistant"]
    assert turns[0].created_at is not None
    assert turns[1].created_at is not None


@pytest.mark.asyncio
async def test_no_pending_rows_after_exception(test_db):
    set_current_session_id("regr_session_nophantom")
    task = _task("task_nophantom", "my name is Dave")
    test_db.add(task)
    test_db.commit()

    with patch.object(MemoryService, "create", side_effect=RuntimeError("boom")):
        await save_conversation_turn(test_db, "task_nophantom", task)

    # No staged rows left behind.
    assert len(test_db.new) == 0
    assert len(test_db.dirty) == 0
    assert len(test_db.deleted) == 0


@pytest.mark.asyncio
async def test_caller_owned_transaction_is_not_forced(test_db):
    set_current_session_id("regr_session_caller")
    task = _task("task_caller", "my name is Eve")
    test_db.add(task)
    test_db.commit()

    marker = Task(
        id="caller-row",
        original_request="caller owns this",
        state=TaskState.CREATED.value,
    )
    test_db.add(marker)
    test_db.flush()

    await save_conversation_turn(test_db, "task_caller", task)

    # The function must report/stop instead of committing caller-owned work.
    assert test_db.in_transaction() is True
    assert test_db.query(ConversationTurn).filter_by(task_id="task_caller").count() == 0
    # Clean up the caller-owned transaction without touching committed rows.
    test_db.rollback()


@pytest.mark.asyncio
async def test_turns_persist_after_post_task_pipeline_caller_read(test_db):
    set_current_session_id("regr_session_caller_read")
    task = _task("task_caller_read", "my name is Nora")
    test_db.add(task)
    test_db.commit()

    # Simulate an ordinary PostTaskPipeline runner.get_task read/autobegin.
    read_task = test_db.query(Task).filter_by(id="task_caller_read").first()
    assert read_task is not None

    await save_conversation_turn(test_db, "task_caller_read", task)

    assert (
        test_db.query(ConversationTurn)
        .filter_by(task_id="task_caller_read")
        .count()
        == 2
    )


@pytest.mark.asyncio
async def test_stops_on_preexisting_written_transaction(test_db):
    set_current_session_id("regr_session_written_tx")
    task = _task("task_written_tx", "my name is Oscar")
    test_db.add(task)
    test_db.commit()

    uncommitted = Task(
        id="written-tx-row",
        original_request="uncommitted caller write",
        state=TaskState.CREATED.value,
    )
    test_db.add(uncommitted)
    test_db.flush()

    await save_conversation_turn(test_db, "task_written_tx", task)

    assert (
        test_db.query(ConversationTurn).filter_by(task_id="task_written_tx").count()
        == 0
    )

    conn = test_db.connection()
    dbapi = getattr(conn, "connection", None)
    dbapi = getattr(dbapi, "dbapi_connection", dbapi)
    physical = getattr(dbapi, "in_transaction", False)
    physical = physical() if callable(physical) else physical
    assert physical is True

    test_db.rollback()
    assert test_db.query(Task).filter_by(id="task_written_tx").first() is not None
    assert (
        test_db.query(ConversationTurn).filter_by(task_id="task_written_tx").count()
        == 0
    )


@pytest.mark.asyncio
async def test_explicit_begin_transaction_is_preserved(test_db):
    from sqlalchemy.orm.session import SessionTransactionOrigin

    set_current_session_id("regr_session_explicit_begin")
    task = _task("task_explicit_begin", "my name is Quinn")
    test_db.add(task)
    test_db.commit()

    tx = test_db.begin()
    assert test_db.get_transaction() is not None
    assert test_db.get_transaction().origin is SessionTransactionOrigin.BEGIN

    await save_conversation_turn(test_db, "task_explicit_begin", task)

    assert (
        test_db.query(ConversationTurn)
        .filter_by(task_id="task_explicit_begin")
        .count()
        == 0
    )
    # The caller's explicit transaction must remain, not be committed/rolled back.
    current_tx = test_db.get_transaction()
    assert current_tx is not None
    assert current_tx.origin is SessionTransactionOrigin.BEGIN

    test_db.rollback()
    assert test_db.query(Task).filter_by(id="task_explicit_begin").first() is not None
