"""Isolated DELETE/WAL regressions for worker reuse and cleanup failure evidence."""

import asyncio
import json

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from backend.models.database import Task, TaskEvent
from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as Ledger
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.model_engine import DecisionType
from backend.tests.test_followup_worker_race_probe import sessions, seed, enqueue, worker


class Model:
    def __init__(self, error=None):
        self.error, self.calls = error, 0

    async def make_decision(self, **kwargs):
        self.calls += 1
        if self.error is not None:
            raise self.error
        return DecisionType.COMPLETE_TASK, {"result": "fake completion"}

    def pop_pending_tool_calls(self):
        return []


def _insert(db, task_id):
    db.execute(text(
        "INSERT INTO tasks (id, original_request, state, version, created_at, updated_at) "
        "VALUES (:id, 'caller work', 'created', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"
    ), {"id": task_id})


def _caller_work(db, kind):
    if kind == "autoflush":
        staged = Task(id="caller", original_request="caller work", state="created", version=1)
        db.add(staged)
        assert db.execute(select(Task).where(Task.id == "caller")).scalar_one() is staged
        assert not db.new and not db.dirty
    else:
        db.execute(text("BEGIN IMMEDIATE"))
        _insert(db, "caller")
    nested = None
    if kind == "nested":
        nested = db.begin_nested()
        _insert(db, "savepoint")
    return db.get_transaction(), nested, db.connection().connection.dbapi_connection


def _caller_ids(db):
    return set(db.execute(select(Task.id).where(Task.id.in_(["caller", "savepoint"]))).scalars())


@pytest.mark.asyncio
@pytest.mark.parametrize("first_state", ["cancelled", "completed", "failed"])
async def test_same_loop_reuse_does_not_carry_terminal_retry_into_next_task(sessions, first_state):
    primary = ValueError("second task model failure")
    model = Model(primary)
    with sessions() as db:
        await seed(db)
        db.get(Task, "original").state = first_state
        db.add(Task(id="second", original_request="second task", state="running", version=1))
        db.commit()
        loop = worker(db, model)
        if first_state == "cancelled":
            await loop.execute_task("original")
        else:
            with pytest.raises(RuntimeError, match="Cannot execute terminal task"):
                await loop.execute_task("original")
        assert model.calls == 0
        with pytest.raises(ValueError) as caught:
            await loop.execute_task("second")
        assert caught.value is primary
        assert model.calls == 1
    with sessions() as db:
        assert db.get(Task, "original").state == first_state
        assert db.get(Task, "second").state == "failed"
        events = db.query(TaskEvent).filter_by(task_id="second", event_type="task.failed").all()
        assert len(events) == 1
        assert json.loads(events[0].event_data)["code"] == "EXECUTION_ERROR"


@pytest.mark.asyncio
@pytest.mark.parametrize("release_failure", ["raise", "false"])
@pytest.mark.parametrize("body", ["completed", "cancelled", "error", "cancellation"])
async def test_ownership_release_failure_is_visible_and_preserves_primary_error(
    sessions, monkeypatch, release_failure, body, caplog
):
    primary = (ValueError("primary model failure") if body == "error" else
               asyncio.CancelledError("primary cancellation") if body == "cancellation" else None)
    model = Model(primary)
    calls = []
    with sessions() as db:
        await seed(db)
        if body == "cancelled":
            db.get(Task, "original").state = "cancelled"
            db.commit()
        loop = worker(db, model)

        async def fail_release(**kwargs):
            calls.append(kwargs)
            if release_failure == "raise":
                raise RuntimeError("injected ownership release failure")
            return False

        monkeypatch.setattr(loop.runner, "release_task_ownership", fail_release)
        with pytest.raises(type(primary) if primary is not None else RuntimeError) as caught:
            await loop.execute_task("original")
        if primary is not None:
            assert caught.value is primary
            assert any("ownership release" in note.lower() for note in caught.value.__notes__)
        else:
            assert "release" in str(caught.value).lower()
        assert len(calls) == 1
        assert "ownership release" in caplog.text.lower()
        assert not db.connection().connection.dbapi_connection.in_transaction
    with sessions() as db:
        assert db.get(TaskOwnershipRow, "original").status == "ACTIVE"
        assert db.get(Task, "original").state == {
            "completed": "completed", "cancelled": "cancelled", "error": "failed", "cancellation": "running"
        }[body]
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 0
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0


@pytest.mark.asyncio
async def test_failure_recording_error_does_not_replace_primary_model_error(sessions, monkeypatch, caplog):
    primary = ValueError("original model error")
    with sessions() as db:
        await seed(db)
        loop = worker(db, Model(primary))

        async def fail_recording(*args, **kwargs):
            raise RuntimeError("injected task failure recording error")

        monkeypatch.setattr(loop, "_fail_task", fail_recording)
        with pytest.raises(ValueError) as caught:
            await loop.execute_task("original")
        assert caught.value is primary
        assert any("failure recording" in note.lower() for note in primary.__notes__)
        assert "failure recording" in caplog.text.lower()
    with sessions() as db:
        assert db.get(Task, "original").state == "running"
        assert db.query(TaskEvent).filter_by(event_type="task.failed").count() == 0
        assert db.query(TaskEvent).filter_by(event_type="task.completed").count() == 0
        assert db.get(TaskOwnershipRow, "original").status == "RELEASED"


@pytest.mark.asyncio
async def test_ownership_loss_failure_recording_error_cannot_return_normally(sessions, monkeypatch):
    error = RuntimeError("ownership loss failure recording unavailable")
    with sessions() as db:
        await seed(db)
        model = Model()
        loop = worker(db, model)
        real_acquire = loop.runner.acquire_task_ownership

        async def acquire_then_lose(*args, **kwargs):
            result = await real_acquire(*args, **kwargs)
            loop._ownership_lost = True
            return result

        async def fail_recording(*args, **kwargs):
            raise error

        monkeypatch.setattr(loop.runner, "acquire_task_ownership", acquire_then_lose)
        monkeypatch.setattr(loop, "_fail_task", fail_recording)
        with pytest.raises(RuntimeError) as caught:
            await loop.execute_task("original")
        assert caught.value is error and model.calls == 0
    with sessions() as db:
        assert db.get(Task, "original").state == "running"
        assert db.query(TaskEvent).filter_by(event_type="task.completed").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["none", "ledger", "commit", "flush"])
async def test_not_applied_cleanup_commits_or_raises_and_rolls_back_only_owned_work(
    sessions, monkeypatch, failure
):
    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        db.get(Task, "original").state = "cancelled"
        db.commit()
        loop = worker(db, Model())
        connection = db.connection().connection.dbapi_connection
        real_terminal = Ledger.on_task_terminal

        def terminal_then_fail(self, *args, **kwargs):
            result = real_terminal(self, *args, **kwargs)
            if failure == "ledger":
                raise RuntimeError("injected ledger cleanup failure")
            if failure == "flush":
                db.add(Task(id="original", original_request="duplicate", state="created", version=1))
                db.flush()  # real SQL failure, including SQLAlchemy failed-session state
            return result

        monkeypatch.setattr(Ledger, "on_task_terminal", terminal_then_fail)
        with monkeypatch.context() as scoped:
            if failure == "commit":
                def fail_commit():
                    raise RuntimeError("injected cleanup commit failure")
                scoped.setattr(db, "commit", fail_commit)
            if failure == "none":
                await loop._mark_reviewed_followups_not_applied("original", "cancelled")
            else:
                with pytest.raises(IntegrityError if failure == "flush" else RuntimeError):
                    await loop._mark_reviewed_followups_not_applied("original", "cancelled")
        assert not connection.in_transaction and db.is_active
        assert Ledger(db).get("request").status == ("not_applied" if failure == "none" else "pending")
        assert db.execute(text("SELECT 1")).scalar_one() == 1
    with sessions() as db:
        receipt = Ledger(db).get("request")
        assert receipt.status == ("not_applied" if failure == "none" else "pending")
        assert receipt.instruction == "  Exactly fifteen minutes.  "
        assert db.get(Task, "original").state == "cancelled"


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["raw", "autoflush", "nested"])
@pytest.mark.parametrize("finish", ["commit", "rollback"])
async def test_not_applied_cleanup_refuses_without_settling_caller_work(sessions, monkeypatch, kind, finish):
    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        loop = worker(db, Model())
        outer, nested, connection = _caller_work(db, kind)
        expected = {"caller", "savepoint"} if nested else {"caller"}
        with monkeypatch.context() as scoped:
            scoped.setattr(db, "commit", lambda: pytest.fail("committed caller transaction"))
            scoped.setattr(db, "rollback", lambda: pytest.fail("rolled back caller transaction"))
            scoped.setattr(Ledger, "on_task_terminal", lambda *a, **k: pytest.fail("entered ledger with caller work"))
            with pytest.raises(RuntimeError, match="caller-owned"):
                await loop._mark_reviewed_followups_not_applied("original", "cancelled")
        assert connection.in_transaction and outer.is_active
        assert db.get_transaction() is outer and db.get_nested_transaction() is nested
        assert _caller_ids(db) == expected
        with sessions() as observer:
            assert _caller_ids(observer) == set()
            assert Ledger(observer).get("request").status == "pending"
        if nested:
            assert nested.is_active
            nested.rollback()
            expected.remove("savepoint")
            assert _caller_ids(db) == expected
        getattr(db, finish)()
        assert not connection.in_transaction
    with sessions() as observer:
        assert _caller_ids(observer) == (expected if finish == "commit" else set())
        assert Ledger(observer).get("request").status == "pending"


@pytest.mark.asyncio
async def test_real_not_applied_cleanup_does_not_commit_unrelated_raw_insert(sessions):
    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        loop = worker(db, Model())
        db.execute(text("BEGIN IMMEDIATE"))
        _insert(db, "caller")
        with pytest.raises(RuntimeError, match="caller-owned"):
            await loop._mark_reviewed_followups_not_applied("original", "cancelled")
        with sessions() as observer:
            assert _caller_ids(observer) == set()
            assert Ledger(observer).get("request").status == "pending"
        assert _caller_ids(db) == {"caller"}
        db.rollback()
        assert _caller_ids(db) == set()


@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["ledger", "commit"])
async def test_worker_surfaces_not_applied_failure_without_rewriting_completed_evidence(
    sessions, monkeypatch, failure
):
    error = RuntimeError(f"injected post-completion cleanup {failure} error")
    with sessions() as db:
        await seed(db)
        loop = worker(db, Model())
        real_cleanup = loop._mark_reviewed_followups_not_applied

        async def fail_post_completion_cleanup(*args, **kwargs):
            with monkeypatch.context() as scoped:
                def fail(*args, **kwargs):
                    raise error
                # Inject only once completion has committed, not into its
                # atomic terminal transition/receipt cleanup transaction.
                scoped.setattr(Ledger if failure == "ledger" else db,
                               "on_task_terminal" if failure == "ledger" else "commit", fail)
                await real_cleanup(*args, **kwargs)

        monkeypatch.setattr(loop, "_mark_reviewed_followups_not_applied", fail_post_completion_cleanup)
        with pytest.raises(RuntimeError) as caught:
            await loop.execute_task("original")
        assert caught.value is error
        assert not db.connection().connection.dbapi_connection.in_transaction
    with sessions() as db:
        task = db.get(Task, "original")
        assert task.state == "completed" and task.final_result is not None
        assert db.query(TaskEvent).filter_by(event_type="task.completed").count() == 1
        assert db.query(TaskEvent).filter_by(event_type="task.failed").count() == 0
        assert db.get(TaskOwnershipRow, "original").status == "RELEASED"


@pytest.mark.asyncio
async def test_real_release_post_update_exception_is_not_suppressed_by_worker(sessions, monkeypatch):
    error = RuntimeError("injected real release post-UPDATE failure")
    with sessions() as db:
        await seed(db)
        loop = worker(db, Model())
        real_release = loop.runner.task_ownership.release

        def release_then_fail(*args, **kwargs):
            assert real_release(*args, **kwargs) is True
            _insert(db, "release-only-write")
            raise error

        monkeypatch.setattr(loop.runner.task_ownership, "release", release_then_fail)
        with pytest.raises(RuntimeError) as caught:
            await loop.execute_task("original")
        assert caught.value is error
        assert not db.connection().connection.dbapi_connection.in_transaction
    with sessions() as db:
        assert db.get(TaskOwnershipRow, "original").status == "ACTIVE"
        assert db.get(Task, "release-only-write") is None
        assert db.get(Task, "original").state == "completed"
        assert db.query(TaskEvent).filter_by(event_type="task.completed").count() == 1
        assert db.query(TaskEvent).filter_by(event_type="task.failed").count() == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("kind", ["raw", "autoflush", "nested"])
async def test_primary_error_and_cleanup_refusal_preserve_new_caller_transaction(sessions, kind):
    primary = ValueError("primary with caller transaction")
    captured = []

    class CallerModel(Model):
        async def make_decision(self, **kwargs):
            captured.append(_caller_work(db, kind))
            raise primary

    with sessions() as db:
        await seed(db)
        loop = worker(db, CallerModel())
        with pytest.raises(ValueError) as caught:
            await loop.execute_task("original")
        assert caught.value is primary
        assert any("failure recording" in note.lower() for note in primary.__notes__)
        assert any("ownership release" in note.lower() for note in primary.__notes__)
        outer, nested, connection = captured[0]
        assert outer.is_active and connection.in_transaction
        assert db.get_transaction() is outer and db.get_nested_transaction() is nested
        assert _caller_ids(db) == ({"caller", "savepoint"} if nested else {"caller"})
        with sessions() as observer:
            assert _caller_ids(observer) == set()
            assert observer.get(Task, "original").state == "running"
        if nested:
            assert nested.is_active
            nested.rollback()
            assert _caller_ids(db) == {"caller"}
        db.rollback()
        assert not connection.in_transaction and _caller_ids(db) == set()
