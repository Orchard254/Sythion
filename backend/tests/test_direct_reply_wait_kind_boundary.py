"""Clarification-only reply boundaries, not permission grants or execution.

These tests enforce the fail-closed direct-reply boundary. They use
the real HTTP handler and TaskRunner with an in-memory database, but replace
background execution and executor lookup. No application lifespan is started.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api import routes
from backend.models import database
from backend.models.database import Base, Task, TaskEvent, get_db
from backend.services import task_execution_loop
from backend.services.task_runner import TaskRunner


WAIT_CASES = [
    pytest.param('{"kind":"clarification"}', id="clarification"),
    pytest.param(
        '{"kind":"permission","status":"awaiting","action_id":"synthetic-action",'
        '"tool_name":"synthetic-never-executed","params":{"target":"synthetic-target"}}',
        id="permission",
    ),
    pytest.param('{"kind":"unknown"}', id="unknown-kind"),
    pytest.param('{"question":"Synthetic legacy question"}', id="legacy-untyped"),
    pytest.param(None, id="missing-metadata"),
    pytest.param('not-json', id="malformed-metadata"),
    pytest.param('[]', id="non-object-metadata"),
]


@pytest.fixture
def direct_reply_boundary(test_db, monkeypatch):
    scheduled = []
    executor_sentinel = object()

    def forbid_profile_database(*args, **kwargs):
        pytest.fail("This test must not access a profile database")

    def stub_run_task(db, task_id, executor):
        # Record scheduling synchronously; the scheduled coroutine does nothing.
        assert db is test_db
        assert executor is executor_sentinel
        scheduled.append(task_id)

        async def no_operation():
            return None

        return no_operation()

    monkeypatch.setattr(database, "get_db_manager", forbid_profile_database)
    monkeypatch.setattr(routes, "get_runner", lambda db: TaskRunner(db))
    monkeypatch.setattr(routes, "get_tool_executor", lambda: executor_sentinel)
    monkeypatch.setattr(task_execution_loop, "run_task", stub_run_task)

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        yield SimpleNamespace(client=client, db=test_db, scheduled=scheduled)


def seed_wait(harness, metadata):
    task = Task(
        id="synthetic-wait",
        original_request="Synthetic request only",
        state="waiting_for_user",
        version=7,
        pending_question="Synthetic question?",
        pending_action=metadata,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    harness.db.add(task)
    harness.db.commit()
    return task.id


def assert_reply_accepted(harness, task_id, metadata, response):
    assert response.status_code == 200
    assert response.json()["state"] == "running"
    assert response.json()["version"] == 8
    assert "pending_action" not in response.json()
    harness.db.expire_all()
    task = harness.db.query(Task).filter_by(id=task_id).one()
    assert task.state == "running"
    assert task.version == 8
    assert task.pending_question is None
    assert task.pending_action == metadata
    assert task.active_action_id is None
    assert task.normalized_goal == "Synthetic request only\n[clarification] Synthetic answer"
    events = harness.db.query(TaskEvent).filter_by(task_id=task_id).all()
    assert [event.event_type for event in events] == ["user.replied"]
    assert json.loads(events[0].event_data)["answer"] == "Synthetic answer"
    assert harness.scheduled == [task_id]
    # A clarification reply produces no consent or execution events.


def assert_wait_unchanged(harness, task_id, metadata):
    harness.db.expire_all()
    task = harness.db.query(Task).filter_by(id=task_id).one()
    assert task.state == "waiting_for_user"
    assert task.version == 7
    assert task.pending_question == "Synthetic question?"
    assert task.pending_action == metadata
    assert task.normalized_goal is None
    assert harness.db.query(TaskEvent).filter_by(task_id=task_id).count() == 0
    assert harness.scheduled == []


@pytest.mark.parametrize("metadata", WAIT_CASES)
def test_non_inferred_direct_reply_only_resumes_typed_clarification(direct_reply_boundary, metadata):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    response = harness.client.post(f"/tasks/{task_id}/reply", json={
        "answer": "Synthetic answer", "inferred": False, "expected_version": 7,
    })
    if metadata == '{"kind":"clarification"}':
        assert_reply_accepted(harness, task_id, metadata, response)
    else:
        assert response.status_code == 409
        assert_wait_unchanged(harness, task_id, metadata)


@pytest.mark.parametrize("metadata", WAIT_CASES)
@pytest.mark.parametrize("expected_version", [6, 8], ids=["older", "future"])
def test_mismatched_version_preserves_every_wait(direct_reply_boundary, metadata, expected_version):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    response = harness.client.post(f"/tasks/{task_id}/reply", json={
        "answer": "Synthetic answer", "inferred": False, "expected_version": expected_version,
    })
    assert response.status_code == 409
    assert "question changed" in response.json()["detail"]
    assert_wait_unchanged(harness, task_id, metadata)


@pytest.mark.parametrize("metadata", WAIT_CASES)
def test_inferred_reply_requires_explicit_clarification_kind(direct_reply_boundary, metadata):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    response = harness.client.post(f"/tasks/{task_id}/reply", json={
        "answer": "Synthetic answer", "inferred": True, "expected_version": 7,
    })
    if metadata == '{"kind":"clarification"}':
        assert_reply_accepted(harness, task_id, metadata, response)
    else:
        assert response.status_code == 409
        assert_wait_unchanged(harness, task_id, metadata)


@pytest.mark.parametrize("metadata", WAIT_CASES[:3])
def test_omitted_flags_cannot_bypass_wait_kind_boundary(direct_reply_boundary, metadata):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    response = harness.client.post(f"/tasks/{task_id}/reply", json={"answer": "Synthetic answer"})
    if metadata == '{"kind":"clarification"}':
        assert_reply_accepted(harness, task_id, metadata, response)
    else:
        assert response.status_code == 409
        assert_wait_unchanged(harness, task_id, metadata)


@pytest.mark.parametrize("metadata", WAIT_CASES[:1])
def test_repeated_reply_cannot_resume_an_already_running_task(direct_reply_boundary, metadata):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    payload = {"answer": "Synthetic answer", "inferred": False, "expected_version": 7}
    first = harness.client.post(f"/tasks/{task_id}/reply", json=payload)
    assert_reply_accepted(harness, task_id, metadata, first)
    repeated = harness.client.post(f"/tasks/{task_id}/reply", json=payload)
    assert repeated.status_code == 400
    assert_reply_accepted(harness, task_id, metadata, first)


async def runner_reply(db, task_id, version):
    runner = TaskRunner(db)
    assert await runner.acquire_lease(task_id=task_id)
    try:
        return await runner.reply_to_waiting(task_id, "Synthetic answer", version)
    finally:
        await runner.release_lease()


@pytest.mark.parametrize("metadata", WAIT_CASES[1:])
def test_direct_runner_cannot_bypass_wait_kind_boundary(direct_reply_boundary, metadata):
    harness = direct_reply_boundary
    task_id = seed_wait(harness, metadata)
    with pytest.raises(RuntimeError, match="explicitly typed clarification"):
        asyncio.run(runner_reply(harness.db, task_id, 7))
    assert_wait_unchanged(harness, task_id, metadata)


@pytest.mark.parametrize("version", [6, 8])
def test_direct_runner_rejects_stale_or_future_version(direct_reply_boundary, version):
    harness = direct_reply_boundary
    metadata = '{"kind":"clarification"}'
    task_id = seed_wait(harness, metadata)
    with pytest.raises(RuntimeError, match="question changed"):
        asyncio.run(runner_reply(harness.db, task_id, version))
    assert_wait_unchanged(harness, task_id, metadata)


@pytest.fixture
def isolated_reply_sessions(tmp_path):
    engine = create_engine(
        f"sqlite:///{(tmp_path / 'reply-boundary.sqlite').as_posix()}",
        connect_args={"check_same_thread": False, "timeout": 10},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine)
    with sessions() as db:
        seed_wait(SimpleNamespace(db=db), '{"kind":"clarification"}')
    try:
        yield sessions
    finally:
        engine.dispose()


@pytest.mark.parametrize("replacement", WAIT_CASES[1:])
def test_direct_runner_refreshes_cached_metadata_before_reply(isolated_reply_sessions, monkeypatch, replacement):
    sessions = isolated_reply_sessions
    with sessions() as stale_db:
        cached = stale_db.query(Task).filter_by(id="synthetic-wait").one()
        assert json.loads(cached.pending_action)["kind"] == "clarification"
        # Simulate another writer changing metadata even without a version bump.
        with sessions() as writer:
            writer.query(Task).filter_by(id=cached.id).update({Task.pending_action: replacement})
            writer.commit()
        runner = TaskRunner(stale_db)
        monkeypatch.setattr(runner, "_verify_lease", lambda: True)
        with pytest.raises(RuntimeError, match="explicitly typed clarification"):
            asyncio.run(runner.reply_to_waiting(cached.id, "Synthetic answer", 7))
        assert_wait_unchanged(SimpleNamespace(db=stale_db, scheduled=[]), cached.id, replacement)


def test_concurrent_direct_runner_replies_commit_only_once(isolated_reply_sessions, monkeypatch):
    sessions = isolated_reply_sessions
    barrier = Barrier(2)

    def reply():
        with sessions() as db:
            # Both callers initially observe the same question/version.
            cached = db.query(Task).filter_by(id="synthetic-wait").one()
            assert cached.version == 7
            runner = TaskRunner(db)
            # Isolate the atomic reply invariant from the outer route lease.
            monkeypatch.setattr(runner, "_verify_lease", lambda: True)
            barrier.wait(timeout=10)
            try:
                asyncio.run(runner.reply_to_waiting(cached.id, "Synthetic answer", 7))
                return "accepted"
            except RuntimeError as exc:
                assert "question changed" in str(exc)
                return "rejected"

    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(lambda _: reply(), range(2)))
    assert sorted(results) == ["accepted", "rejected"]
    with sessions() as db:
        task = db.query(Task).filter_by(id="synthetic-wait").one()
        assert task.state == "running"
        assert task.version == 8
        assert [e.event_type for e in db.query(TaskEvent).all()] == ["user.replied"]


@pytest.mark.parametrize("replacement", WAIT_CASES[1:3])
def test_metadata_writer_wins_race_before_atomic_reply(isolated_reply_sessions, monkeypatch, replacement):
    sessions = isolated_reply_sessions
    entering_transaction = Event()

    def reply():
        with sessions() as db:
            cached = db.query(Task).filter_by(id="synthetic-wait").one()
            assert cached.pending_action == '{"kind":"clarification"}'
            runner = TaskRunner(db)
            monkeypatch.setattr(runner, "_verify_lease", lambda: True)
            ensure_transaction = runner.task_ownership._ensure_transaction

            def signal_transaction():
                entering_transaction.set()
                return ensure_transaction()

            monkeypatch.setattr(runner.task_ownership, "_ensure_transaction", signal_transaction)
            with pytest.raises(RuntimeError, match="explicitly typed clarification"):
                asyncio.run(runner.reply_to_waiting(cached.id, "Synthetic answer", 7))

    with sessions() as writer:
        # The writer holds the SQLite write reservation while the other session
        # reads its old cached clarification and attempts its atomic reply.
        TaskRunner(writer).task_ownership._ensure_transaction()
        writer.query(Task).filter_by(id="synthetic-wait").update({Task.pending_action: replacement})
        with ThreadPoolExecutor(max_workers=1) as pool:
            future = pool.submit(reply)
            try:
                assert entering_transaction.wait(timeout=10)
            finally:
                writer.commit()
            future.result(timeout=10)
    with sessions() as db:
        assert_wait_unchanged(SimpleNamespace(db=db, scheduled=[]), "synthetic-wait", replacement)


def test_reply_event_failure_rolls_back_transition(direct_reply_boundary, monkeypatch):
    harness = direct_reply_boundary
    metadata = '{"kind":"clarification"}'
    task_id = seed_wait(harness, metadata)

    def fail_event(*args, **kwargs):
        raise RuntimeError("Synthetic ledger failure")

    monkeypatch.setattr(TaskRunner, "_append_event", fail_event)
    with pytest.raises(RuntimeError, match="Synthetic ledger failure"):
        asyncio.run(runner_reply(harness.db, task_id, 7))
    assert_wait_unchanged(harness, task_id, metadata)
