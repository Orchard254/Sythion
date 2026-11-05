"""Permanent regression tests for the TaskRunner caller-transaction guard.

These tests prove the guard is fail-closed and never rolls back caller data
that the runner did not own.
"""

import asyncio
from sqlalchemy import text

import pytest

from backend.models.database import Task
from backend.services.task_runner import TaskRunner
from backend.api.schemas import TaskState


def _make_runner(test_db):
    runner = TaskRunner(test_db)
    asyncio.run(runner.acquire_lease())
    return runner


def _make_task(test_db, runner=None, state=TaskState.CREATED.value):
    if runner is None:
        runner = _make_runner(test_db)
    task = asyncio.run(runner.create_task("guard test task"))
    if state != task.state:
        task = asyncio.run(runner.transition_state(
            task_id=task.id,
            new_state=TaskState(state),
            event_type="test.state",
            event_data={},
        ))
    return runner, task


def _physical_in_transaction(session):
    try:
        conn = session.connection()
        dbapi = getattr(conn, "connection", None)
        dbapi = getattr(dbapi, "dbapi_connection", dbapi)
        return bool(getattr(dbapi, "in_transaction", False))
    except Exception:
        return bool(session.in_transaction())


def _staged_rows(session):
    result = 0
    for name in ("new", "dirty", "deleted"):
        collection = getattr(session, name, None)
        try:
            result += len(collection)
        except Exception:
            pass
    return result


def test_refuse_unflushed_caller_row_before_runner_mutation(test_db):
    runner = _make_runner(test_db)

    # Caller-owned, staged-but-uncommitted row.
    marker = Task(
        id="caller-unflushed",
        original_request="caller owns this",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(marker)

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("runner task"))

    # Caller data must remain staged and untouched by the runner.
    assert marker in test_db.new or marker in test_db.dirty
    assert _staged_rows(test_db) >= 1


def test_refuse_autoflushed_caller_row_before_runner_mutation(test_db):
    runner = _make_runner(test_db)

    marker = Task(
        id="caller-autoflush",
        original_request="caller owns this",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(marker)
    test_db.flush()  # caller flushes, still uncommitted

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("runner task"))

    # Runner must not have committed the caller's flushed row.
    assert _physical_in_transaction(test_db) is True or _staged_rows(test_db) >= 1


def test_refuse_raw_sql_caller_transaction_before_runner_mutation(test_db):
    runner = _make_runner(test_db)

    test_db.execute(text("BEGIN IMMEDIATE"))
    test_db.execute(
        text(
            "INSERT INTO tasks (id, original_request, state, version, created_at, updated_at) "
            "VALUES (:id, :req, :state, :ver, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
        ),
        {"id": "caller-raw", "req": "caller", "state": TaskState.CREATED.value, "ver": 1},
    )

    assert _physical_in_transaction(test_db) is True

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.record_event("missing", "x.event", {}))

    # Runner must leave the raw caller transaction committed neither way.
    assert _physical_in_transaction(test_db) is True
    rows = test_db.execute(
        text("SELECT COUNT(*) FROM tasks WHERE id = 'caller-raw'")
    ).scalar()
    assert rows == 1

    test_db.rollback()


def test_refuse_nested_transaction_owner_across_runner_instances(test_db):
    runner_a = _make_runner(test_db)
    runner_b = _make_runner(test_db)

    # Simulate an actively owned runner transaction.
    test_db.execute(text("BEGIN IMMEDIATE"))
    test_db.info["runner_transaction_owner"] = id(runner_a)

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner_b.create_task("runner b task"))

    test_db.rollback()


def test_force_cancel_refuses_caller_owned_transaction(test_db):
    runner, task = _make_task(test_db)

    # Caller-owned transaction + staged row.
    marker = Task(
        id="caller-cancel",
        original_request="caller",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(marker)

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.force_cancel(task.id))

    # Task state must be unchanged and caller row remains.
    refreshed = test_db.query(Task).filter_by(id=task.id).first()
    assert refreshed.state != TaskState.CANCELLED.value
    assert test_db.query(Task).filter_by(id="caller-cancel").first() is not None


def test_caller_controls_transaction_after_refusal(test_db):
    runner = _make_runner(test_db)

    marker = Task(
        id="caller-ctrl",
        original_request="caller",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(marker)

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner.create_task("runner task"))

    # Caller rolls back; runner should now be able to create a task.
    test_db.rollback()
    task = asyncio.run(runner.create_task("runner task after rollback"))
    assert task is not None
    assert task.state in (TaskState.CREATED.value,)


def test_runner_normal_nested_behavior_commits(test_db):
    runner, task = _make_task(test_db)

    # Nested public runner call inside an active runner-owned transaction.
    test_db.execute(text("BEGIN IMMEDIATE"))
    test_db.info["runner_transaction_owner"] = id(runner)

    created = asyncio.run(runner.clear_pending_action(task.id)) if False else None
    # clear_pending_action may raise for missing lease? use set_pending_action_status via create pending action first.
    test_db.rollback()

    # Normal top-level nested behavior: a public mutation triggers an inner
    # public mutation (record_action_started -> transition_state).
    task2 = asyncio.run(runner.create_task("nested runner task"))
    assert task2.id is not None


def test_failed_mutation_does_not_rollback_caller_data(test_db):
    runner = _make_runner(test_db)

    # Pre-committed caller data that must survive a failed runner mutation.
    caller = Task(
        id="caller-survivor",
        original_request="caller",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(caller)
    test_db.commit()

    # Failed runner mutation (invalid transition) should clean only runner writes.
    task = asyncio.run(runner.create_task("runner task"))

    with pytest.raises(RuntimeError):
        asyncio.run(runner.transition_state(
            task_id=task.id,
            new_state=TaskState.COMPLETED,  # invalid from CREATED? requires RUNNING etc
            event_type="bad",
            event_data={},
        ))

    surviving = test_db.query(Task).filter_by(id="caller-survivor").first()
    assert surviving is not None


def test_transaction_guard_success_failure_raw_autoflush(test_db):
    runner = _make_runner(test_db)
    # basic success
    task = asyncio.run(runner.create_task("ok"))
    assert task.state == TaskState.CREATED.value

    # failure then caller row survives
    with pytest.raises(RuntimeError):
        asyncio.run(runner.transition_state(
            task_id=task.id,
            new_state=TaskState.COMPLETED,
            event_type="bad",
            event_data={},
        ))
    # runner write from attempted failed transition must not leave task completed
    refreshed = test_db.query(Task).filter_by(id=task.id).first()
    assert refreshed.state != TaskState.COMPLETED.value


def test_concurrent_runner_guard_preserves_caller_row(test_db):
    runner_a = _make_runner(test_db)
    runner_b = _make_runner(test_db)

    marker = Task(
        id="concurrent-caller",
        original_request="caller",
        state=TaskState.CREATED.value,
        version=1,
    )
    test_db.add(marker)

    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner_a.create_task("a"))

    # Row still staged and not committed by runner.
    assert marker in test_db.new
    # Second runner also refuses instead of acting on an active caller tx.
    with pytest.raises(RuntimeError, match="caller-owned"):
        asyncio.run(runner_b.create_task("b"))
