"""Isolated commit-fault characterization tests for runner action transactions.

These exercise TaskRunner.start_mutating_action and finish_mutating_action
directly against private temp SQLite databases. They do not use the execution
loop, production admission gate, or any trusted consent staging. A FakeEffect is
an unconnected dummy; fake.calls == 0 only shows these low-level APIs do not call
that dummy and is not proof that production dispatch is absent.

Commit faults are simulated in-process by replacing session.commit. They are
not process death, database corruption, power-loss, network, or OS restart tests.
They characterize low-level atomicity/ownership/event-append boundaries only; they
do not exercise admission, consent approval, locking in another process, or
any live/profile/device/vault/network/staged/reset/commit path.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task, TaskEvent
from backend.api.schemas import TaskState
from backend.runtime.task_ownership import TaskOwnershipManager
from backend.services.execution_components import CompletionVerifier
from backend.services.task_runner import TaskRunner


PARAMS = {"sandbox_resource": "commit fault probe"}


class FakeEffect:
    def __init__(self):
        self.calls = 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        from types import SimpleNamespace

        return SimpleNamespace(get_policy_requirements=lambda: {"mutating": True})

    async def execute_tool(self, **kwargs):
        self.calls += 1
        return {"success": True, "fake_effect": self.calls}


def _make_engine(tmp_path, name, journal_mode):
    db_path = str(tmp_path / f"{name}.db")
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as connection:
        actual = connection.exec_driver_sql(f"PRAGMA journal_mode={journal_mode}").scalar()
        assert str(actual).upper() == journal_mode.upper()
        connection.commit()
    Base.metadata.create_all(engine)
    return engine


async def _owned_runner(engine):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    task_id = "commit-fault-task"
    session.add(Task(id=task_id, original_request="probe", state=TaskState.RUNNING.value, version=1))
    session.commit()
    runner = TaskRunner(session)
    result = await runner.acquire_task_ownership(task_id, worker_id="owner")
    assert getattr(result, "acquired", False)
    return session, runner, task_id, SessionLocal


RESULT_DATA = {
    "success": True,
    "verification": "passed",
    "tool_name": "fake-effect",
    "mutating": True,
    "params": PARAMS,
}


@pytest.mark.asyncio
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
async def test_start_mutating_action_before_commit_fault_preserves_durable_state(tmp_path, journal_mode):
    engine = _make_engine(tmp_path, "start-before", journal_mode)
    session, runner, task_id, SessionLocal = await _owned_runner(engine)
    fake = FakeEffect()
    try:
        manager = runner.task_ownership
        before_started = session.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count()
        before_executed = session.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count()
        before_task_active = session.query(Task).filter_by(id=task_id).one().active_action_id
        before_owner_active = manager.get(task_id).active_action_id
        before_uncertain = manager.get(task_id).uncertain_action_ids

        original_commit = session.commit

        def boom():
            raise RuntimeError("commit fault before commit")

        session.commit = boom
        with pytest.raises(RuntimeError, match="commit fault before commit"):
            await runner.start_mutating_action(task_id, "action-start", "fake-effect", PARAMS)
        session.commit = original_commit

        with SessionLocal() as observer:
            task = observer.query(Task).filter_by(id=task_id).one()
            owner = TaskOwnershipManager(observer).get(task_id)
            assert task.active_action_id == before_task_active
            assert owner.active_action_id == before_owner_active
            assert owner.uncertain_action_ids == before_uncertain
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count() == before_started
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count() == before_executed
            assert fake.calls == 0
            verifier = CompletionVerifier(observer, lambda tid: TaskRunner(observer).ledger.event_history(tid))
            assert verifier.precondition_error(task_id) is None
            dbapi = observer.connection().connection.dbapi_connection
            assert not getattr(dbapi, "in_transaction", False)
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
async def test_start_mutating_action_after_commit_fault_preserves_durable_evidence(tmp_path, journal_mode):
    engine = _make_engine(tmp_path, "start-after", journal_mode)
    session, runner, task_id, SessionLocal = await _owned_runner(engine)
    fake = FakeEffect()
    try:
        manager = runner.task_ownership
        before_started = session.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count()
        before_uncertain = manager.get(task_id).uncertain_action_ids

        original_commit = session.commit

        def boom():
            original_commit()
            raise RuntimeError("commit fault after commit")

        session.commit = boom
        with pytest.raises(RuntimeError, match="commit fault after commit"):
            await runner.start_mutating_action(task_id, "action-start", "fake-effect", PARAMS)
        session.commit = original_commit

        with SessionLocal() as observer:
            task = observer.query(Task).filter_by(id=task_id).one()
            owner = TaskOwnershipManager(observer).get(task_id)
            assert task.active_action_id == "action-start"
            assert owner.active_action_id == "action-start"
            assert owner.uncertain_action_ids == before_uncertain
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count() == before_started + 1
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count() == 0
            started_events = [event for event in runner.ledger.event_history(task_id) if event["event_type"] == "action.started"]
            assert len(started_events) == before_started + 1
            started_payload = started_events[-1]["event_data"]
            assert started_payload == {
                "action_id": "action-start",
                "tool_name": "fake-effect",
                "approved": False,
                "owner_nonce": owner.owner_nonce,
                "fencing_token": owner.fencing_token,
            }
            assert started_payload["owner_nonce"] == runner.task_ownership_nonce
            assert fake.calls == 0
            verifier = CompletionVerifier(observer, lambda tid: TaskRunner(observer).ledger.event_history(tid))
            assert verifier.action_evidence_error(task_id) == "An action outcome is unresolved"
            assert verifier.precondition_error(task_id) == "An action outcome is unresolved"
            dbapi = observer.connection().connection.dbapi_connection
            assert not getattr(dbapi, "in_transaction", False)
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
async def test_finish_mutating_action_before_commit_fault_preserves_durable_state(tmp_path, journal_mode):
    engine = _make_engine(tmp_path, "finish-before", journal_mode)
    session, runner, task_id, SessionLocal = await _owned_runner(engine)
    fake = FakeEffect()
    try:
        await runner.start_mutating_action(task_id, "action-finish", "fake-effect", PARAMS)
    except Exception as exc:  # pragma: no cover - setup guard
        session.close()
        engine.dispose()
        raise AssertionError(f"start failed: {exc!r}")

    try:
        manager = runner.task_ownership
        before_executed = session.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count()
        before_started = session.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count()
        before_task_active = session.query(Task).filter_by(id=task_id).one().active_action_id
        before_owner_active = manager.get(task_id).active_action_id
        before_uncertain = manager.get(task_id).uncertain_action_ids

        original_commit = session.commit

        def boom():
            raise RuntimeError("commit fault before commit")

        session.commit = boom
        with pytest.raises(RuntimeError, match="commit fault before commit"):
            await runner.finish_mutating_action(task_id, "action-finish", RESULT_DATA)
        session.commit = original_commit

        with SessionLocal() as observer:
            task = observer.query(Task).filter_by(id=task_id).one()
            owner = TaskOwnershipManager(observer).get(task_id)
            assert task.active_action_id == before_task_active
            assert owner.active_action_id == before_owner_active
            assert owner.uncertain_action_ids == before_uncertain
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count() == before_executed
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count() == before_started
            assert fake.calls == 0
            verifier = CompletionVerifier(observer, lambda tid: TaskRunner(observer).ledger.event_history(tid))
            assert verifier.action_evidence_error(task_id) == "An action outcome is unresolved"
            assert verifier.precondition_error(task_id) == "An action outcome is unresolved"
            dbapi = observer.connection().connection.dbapi_connection
            assert not getattr(dbapi, "in_transaction", False)
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("journal_mode", ["DELETE", "WAL"])
async def test_finish_mutating_action_after_commit_fault_preserves_durable_evidence(tmp_path, journal_mode):
    engine = _make_engine(tmp_path, "finish-after", journal_mode)
    session, runner, task_id, SessionLocal = await _owned_runner(engine)
    fake = FakeEffect()
    try:
        await runner.start_mutating_action(task_id, "action-finish", "fake-effect", PARAMS)
    except Exception as exc:  # pragma: no cover - setup guard
        session.close()
        engine.dispose()
        raise AssertionError(f"start failed: {exc!r}")

    try:
        manager = runner.task_ownership
        before_executed = session.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count()
        before_started = session.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count()
        before_uncertain = manager.get(task_id).uncertain_action_ids

        original_commit = session.commit

        def boom():
            original_commit()
            raise RuntimeError("commit fault after commit")

        session.commit = boom
        with pytest.raises(RuntimeError, match="commit fault after commit"):
            await runner.finish_mutating_action(task_id, "action-finish", RESULT_DATA)
        session.commit = original_commit

        with SessionLocal() as observer:
            task = observer.query(Task).filter_by(id=task_id).one()
            owner = TaskOwnershipManager(observer).get(task_id)
            assert task.active_action_id is None
            assert owner.active_action_id is None
            assert owner.uncertain_action_ids == before_uncertain
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="tool.executed").count() == before_executed + 1
            assert observer.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count() == before_started
            executed_events = [event for event in TaskRunner(observer).ledger.event_history(task_id) if event["event_type"] == "tool.executed"]
            assert len(executed_events) == before_executed + 1
            executed_payload = executed_events[-1]["event_data"]
            assert executed_payload == {
                "success": True,
                "verification": "passed",
                "tool_name": "fake-effect",
                "mutating": True,
                "params": PARAMS,
                "action_id": "action-finish",
            }
            assert fake.calls == 0
            verifier = CompletionVerifier(observer, lambda tid: TaskRunner(observer).ledger.event_history(tid))
            assert verifier.action_evidence_error(task_id) is None
            assert verifier.precondition_error(task_id) is None
            dbapi = observer.connection().connection.dbapi_connection
            assert not getattr(dbapi, "in_transaction", False)
    finally:
        try:
            session.rollback()
        except Exception:
            pass
        session.close()
        engine.dispose()
