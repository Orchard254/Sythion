"""Physical transaction and caller-work regressions on isolated DELETE/WAL DBs."""

import asyncio
from datetime import datetime, timedelta

import pytest
from sqlalchemy import select, text

from backend.models.database import Task
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.task_runner import TaskRunner, _RUNNER_TRANSACTION_OWNERS
from backend.tests.test_followup_worker_race_probe import sessions


def _acquire(db, outcome):
    runner = TaskRunner(db)
    assert asyncio.run(runner.acquire_task_ownership("owned", worker_id="fake-worker")).acquired
    if outcome == "expired":
        db.get(TaskOwnershipRow, "owned").expires_at = datetime.utcnow() - timedelta(seconds=5)
        db.commit()
    return runner


def _insert(db, task_id):
    db.execute(text(
        "INSERT INTO tasks (id, original_request, state, version, created_at, updated_at) "
        "VALUES (:id, 'caller work', 'created', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": task_id})


def _ids(db):
    return set(db.execute(select(Task.id)).scalars())


@pytest.mark.parametrize("outcome", ["success", "expired", "wrong_nonce", "exception"])
def test_release_settles_its_physical_transaction(sessions, monkeypatch, outcome):
    with sessions() as db:
        runner = _acquire(db, outcome)
        nonce = runner.task_ownership_nonce
        before = runner.task_ownership.get("owned").to_dict()
        connection = db.connection().connection.dbapi_connection
        assert not connection.in_transaction
        real_release = runner.task_ownership.release

        def release_then_raise(*args, **kwargs):
            assert connection.in_transaction
            assert real_release(*args, **kwargs) is True
            _insert(db, "release-exception-write")
            raise RuntimeError("injected after ownership UPDATE")

        if outcome == "exception":
            monkeypatch.setattr(runner.task_ownership, "release", release_then_raise)
            with pytest.raises(RuntimeError, match="injected after ownership UPDATE"):
                asyncio.run(runner.release_task_ownership("owned"))
        else:
            kwargs = {"nonce": "not-the-owner"} if outcome == "wrong_nonce" else {}
            assert asyncio.run(runner.release_task_ownership("owned", **kwargs)) is (outcome == "success")

        # Inspect the retained DBAPI connection, not a helper that could hide errors.
        assert not connection.in_transaction
        assert id(connection) not in _RUNNER_TRANSACTION_OWNERS
        with sessions() as observer:
            owner = TaskRunner(observer).task_ownership.get("owned")
            assert _ids(observer) == set()
            if outcome == "success":
                assert owner.status == "RELEASED"
                assert owner.fencing_token == before["fencing_token"] + 1
                assert runner.task_ownership_nonce is None
            else:
                assert owner.to_dict() == before
                assert runner.task_ownership_nonce == nonce

        # A leaked physical transaction makes the next guarded lease mutation refuse.
        if outcome == "success":
            assert asyncio.run(runner.acquire_lease()) is True
        else:
            assert asyncio.run(runner.renew_lease()) is True
        assert not connection.in_transaction


@pytest.mark.parametrize("candidate", ["success", "expired", "exception"])
@pytest.mark.parametrize("work_kind", ["raw", "autoflush", "nested"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
def test_release_refuses_without_settling_any_caller_work(
    sessions, monkeypatch, candidate, work_kind, finish
):
    with sessions() as db:
        runner = _acquire(db, candidate)
        before = runner.task_ownership.get("owned").to_dict()
        calls = []
        real_release = runner.task_ownership.release

        def observed_release(*args, **kwargs):
            calls.append("release")
            if candidate == "exception":
                raise RuntimeError("candidate release exception")
            return real_release(*args, **kwargs)

        # All candidates must be refused before invoking the primitive at all.
        monkeypatch.setattr(runner.task_ownership, "release", observed_release)
        if work_kind == "autoflush":
            staged = Task(id="caller", original_request="caller work", state="created", version=1)
            db.add(staged)
            assert staged in db.new
            assert db.execute(select(Task).where(Task.id == "caller")).scalar_one() is staged
            assert not db.new and not db.dirty  # real query-induced autoflush
        else:
            db.execute(text("BEGIN IMMEDIATE"))
            _insert(db, "caller")

        outer = db.get_transaction()
        nested = None
        expected = {"caller"}
        if work_kind == "nested":
            nested = db.begin_nested()
            _insert(db, "savepoint")
            expected.add("savepoint")
        connection = db.connection().connection.dbapi_connection
        assert connection.in_transaction
        assert _ids(db) == expected
        with sessions() as observer:
            assert _ids(observer) == set()  # neither accidentally committed nor visible

        real_commit, real_rollback = db.commit, db.rollback
        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", lambda: pytest.fail("release committed caller work"))
            scoped.setattr(db, "rollback", lambda: pytest.fail("release rolled back caller work"))
            with pytest.raises(RuntimeError, match="caller-owned"):
                asyncio.run(runner.release_task_ownership("owned"))

        assert calls == []
        assert connection.in_transaction
        assert db.get_transaction() is outer and outer.is_active
        assert db.get_nested_transaction() is nested
        assert _ids(db) == expected
        if nested is not None:
            assert nested.is_active
            nested.rollback()
            expected.remove("savepoint")
            assert _ids(db) == expected  # savepoint still independently reversible
            assert outer.is_active and connection.in_transaction
        with sessions() as observer:
            assert _ids(observer) == set()
            assert TaskRunner(observer).task_ownership.get("owned").to_dict() == before

        if finish == "commit":
            real_commit()
        else:
            real_rollback()
        assert not connection.in_transaction
        with sessions() as observer:
            assert _ids(observer) == (expected if finish == "commit" else set())
            assert TaskRunner(observer).task_ownership.get("owned").to_dict() == before
