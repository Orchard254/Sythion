"""Tests proving transaction-bound runner ownership is invalidated."""

import asyncio

import pytest
from sqlalchemy import text

from backend.api.schemas import TaskState
from backend.models.database import Task
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.task_runner import TaskRunner


def _runner(test_db):
    runner = TaskRunner(test_db)
    asyncio.run(runner.acquire_lease())
    return runner


def test_prior_successful_runner_mutation_then_caller_raw_transaction_refused(test_db):
    runner = _runner(test_db)
    asyncio.run(runner.create_task("success"))

    test_db.execute(text("BEGIN IMMEDIATE"))
    test_db.execute(
        text("INSERT INTO tasks (id, original_request, state, version, created_at, updated_at) VALUES (:id, :r, :s, 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"),
        {"id": "caller-raw", "r": "caller", "s": TaskState.CREATED.value},
    )

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("runner second"))

    assert test_db.execute(text("SELECT COUNT(*) FROM tasks WHERE id='caller-raw'")).scalar() == 1
    test_db.rollback()


def test_prior_failed_runner_mutation_then_caller_autoflush_refused(test_db):
    runner = _runner(test_db)
    task = asyncio.run(runner.create_task("task"))

    with pytest.raises(RuntimeError):
        asyncio.run(runner.transition_state(
            task_id=task.id,
            new_state=TaskState.COMPLETED,
            event_type="bad",
            event_data={},
        ))

    test_db.execute(text("BEGIN IMMEDIATE"))
    staged = Task(id="caller-autoflush", original_request="caller", state=TaskState.CREATED.value, version=1)
    test_db.add(staged)
    test_db.flush()

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("runner second"))

    assert staged in test_db or test_db.query(Task).filter_by(id="caller-autoflush").first() is not None
    test_db.rollback()


def test_shared_session_runners_do_not_share_transaction_ownership(test_db):
    first = _runner(test_db)
    second = _runner(test_db)

    asyncio.run(first.create_task("first"))

    test_db.execute(text("BEGIN IMMEDIATE"))
    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(second.create_task("second"))
    test_db.rollback()


def test_runner_transaction_ownership_does_not_persist_after_commit(test_db):
    runner = _runner(test_db)
    asyncio.run(runner.create_task("committed"))

    # Ownership must be gone after commit; no untrusted session.info claim.
    assert not runner._runner_owns_db_transaction()

    # Expired ownership release must be reported false and must not leave a
    # physical transaction open.
    expired_runner = TaskRunner(test_db)
    asyncio.run(expired_runner.acquire_lease())
    asyncio.run(expired_runner.acquire_task_ownership("expiry-task"))
    from datetime import datetime, timedelta
    row = test_db.query(TaskOwnershipRow).filter_by(task_id="expiry-task").first()
    if row is not None:
        row.expires_at = datetime.utcnow() - timedelta(seconds=1)
        test_db.commit()
    assert asyncio.run(expired_runner.release_task_ownership("expiry-task")) is False
    assert not expired_runner._physical_transaction_active()
    test_db.execute(text("BEGIN IMMEDIATE"))
    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(expired_runner.create_task("should refuse"))
    test_db.rollback()

    test_db.execute(text("BEGIN IMMEDIATE"))
    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("should refuse"))
    test_db.rollback()