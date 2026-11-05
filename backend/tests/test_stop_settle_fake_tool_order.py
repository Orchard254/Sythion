"""Fake-only STOP/settlement ordering at the real SQLite writer-lock boundary."""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from types import SimpleNamespace

import pytest
from sqlalchemy import event

from backend.models.database import Task, TaskEvent
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.model_engine import DecisionType
from backend.services.task_runner import TaskRunner
from backend.tools.executor import ToolOutput
from backend.tests.test_followup_worker_race_probe import sessions, seed, worker


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["stop", "settlement"])
async def test_stop_and_settlement_are_serialized_at_writer_lock(sessions, monkeypatch, winner):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    returned = False
    boundary_seen = False
    stop_attempted = Event()
    trace, seen, stop_views = [], [], []
    stop_future = None

    class Mutator:
        calls = 0

        def list_tools(self):
            return []

        def get_tool(self, name):
            return SimpleNamespace(get_policy_requirements=lambda: {"mutating": True})

        async def execute_tool(self, **kwargs):
            nonlocal returned
            self.calls += 1
            returned = True
            return ToolOutput(success=True, data={"fake_effect": 1}, model_summary="fake mutation")

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            assert len(seen) == 1, "STOP must prevent another model decision"
            return DecisionType.EXECUTE_TOOL, {"tool_name": "fake-mutator", "params": {}}

        def pop_pending_tool_calls(self):
            return []

    def stop():
        async def run():
            with sessions() as stop_db:
                connection = stop_db.connection()

                @event.listens_for(connection, "before_cursor_execute")
                def attempting_lock(conn, cursor, statement, parameters, context, executemany):
                    if statement.strip().upper() == "BEGIN IMMEDIATE":
                        trace.append("stop_attempt")
                        stop_attempted.set()

                @event.listens_for(connection, "after_cursor_execute")
                def stop_has_writer_lock(conn, cursor, statement, parameters, context, executemany):
                    if statement.strip().upper() == "BEGIN IMMEDIATE":
                        # This is the database view AFTER STOP actually acquires
                        # the lock, not merely after the executor returns.
                        active = conn.exec_driver_sql(
                            "SELECT active_action_id FROM tasks WHERE id='original'"
                        ).scalar_one()
                        count = conn.exec_driver_sql(
                            "SELECT COUNT(*) FROM task_events "
                            "WHERE task_id='original' AND event_type='tool.executed'"
                        ).scalar_one()
                        stop_views.append((active, count))

                result = await TaskRunner(stop_db).force_cancel("original")
                assert result.state == "cancelled"
                trace.append("stop_committed")
        asyncio.run(run())

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="fake-stop") as pool:
        with sessions() as db:
            await seed(db)
            executor = Mutator()
            loop = worker(db, Model(), executor)
            real_ensure = loop.runner._ensure_transaction
            real_finish = loop.runner.finish_mutating_action

            def at_settlement_lock():
                nonlocal boundary_seen, stop_future
                if not returned or boundary_seen:
                    return real_ensure()
                boundary_seen = True
                assert not db.connection().connection.dbapi_connection.in_transaction
                with sessions() as observer:
                    task = observer.get(Task, "original")
                    owner = observer.get(TaskOwnershipRow, "original")
                    assert task.active_action_id and owner.active_action_id == task.active_action_id
                    assert json.loads(owner.uncertain_action_ids or "[]") == []
                    assert observer.query(TaskEvent).filter_by(event_type="action.started").count() == 1
                    assert observer.query(TaskEvent).filter_by(event_type="tool.executed").count() == 0
                if winner == "settlement":
                    assert real_ensure() is True  # actual BEGIN IMMEDIATE obtained
                    trace.append("settlement_locked")
                    stop_future = pool.submit(stop)
                    assert stop_attempted.wait(5), "STOP never attempted its writer lock"
                    return True
                stop_future = pool.submit(stop)
                stop_future.result(timeout=10)  # STOP durably commits BEFORE settlement BEGIN
                assert real_ensure() is True
                trace.append("settlement_locked")
                return True

            async def finish_then_observe_stop(*args, **kwargs):
                await real_finish(*args, **kwargs)  # real marker clear + tool event commit
                trace.append("settlement_committed")
                assert not db.connection().connection.dbapi_connection.in_transaction
                # Hold worker progression, not its writer lock, until STOP completes.
                await asyncio.to_thread(stop_future.result, timeout=10)

            monkeypatch.setattr(loop.runner, "_ensure_transaction", at_settlement_lock)
            monkeypatch.setattr(loop.runner, "finish_mutating_action", finish_then_observe_stop)
            await asyncio.wait_for(loop.execute_task("original"), 15)
            assert boundary_seen
            assert stop_future.result(timeout=10) is None

    assert executor.calls == 1 and seen == [None]
    if winner == "stop":
        assert trace == ["stop_attempt", "stop_committed", "settlement_locked"]
    else:
        assert trace[0:2] == ["settlement_locked", "stop_attempt"]
        assert set(trace[2:]) == {"settlement_committed", "stop_committed"}
    with sessions() as db:
        task = db.get(Task, "original")
        owner = db.get(TaskOwnershipRow, "original")
        started = db.query(TaskEvent).filter_by(task_id="original", event_type="action.started").all()
        executed = db.query(TaskEvent).filter_by(task_id="original", event_type="tool.executed").all()
        assert len(started) == 1
        action_id = json.loads(started[0].event_data)["action_id"]
        assert stop_views == ([(action_id, 0)] if winner == "stop" else [(None, 1)])
        assert len(executed) == (0 if winner == "stop" else 1)
        assert task.state == "cancelled" and task.terminal_reason == "cancelled_by_user"
        assert task.final_result is None and task.pending_action is None
        assert task.active_action_id == (action_id if winner == "stop" else None)
        assert owner.status == "RELEASED" and owner.active_action_id is None
        assert json.loads(owner.uncertain_action_ids) == ([action_id] if winner == "stop" else [])
        assert db.query(TaskEvent).filter_by(task_id="original", event_type="task.cancelled").count() == 1
        assert db.query(TaskEvent).filter_by(task_id="original", event_type="task.completed").count() == 0
        if executed:
            data = json.loads(executed[0].event_data)
            assert data["action_id"] == action_id and data["tool_name"] == "fake-mutator"
            assert data["success"] is True and data["verification"] == "passed"
        event_ids = {row.id for row in db.query(TaskEvent).all()}

    # A fresh worker on the terminal task must not replay the fake effect or infer.
    with sessions() as db:
        await asyncio.wait_for(worker(db, Model(), executor).execute_task("original"), 10)
    assert executor.calls == 1 and seen == [None]
    with sessions() as db:
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 1
        assert db.query(TaskEvent).filter_by(event_type="tool.executed").count() == len(executed)
        assert {row.id for row in db.query(TaskEvent).all()} == event_ids
        assert db.get(Task, "original").state == "cancelled"
        assert db.get(Task, "original").active_action_id == (action_id if winner == "stop" else None)
        assert json.loads(db.get(TaskOwnershipRow, "original").uncertain_action_ids) == (
            [action_id] if winner == "stop" else []
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_terminal_worker_retry_does_not_restart_or_infer(sessions, terminal):
    class NeverModel:
        async def make_decision(self, **kwargs):
            pytest.fail("Terminal worker retry must not infer")

    with sessions() as db:
        await seed(db)
        task = db.get(Task, "original")
        task.state = terminal
        task.terminal_reason = "retained-reason"
        task.final_result = "retained-result" if terminal == "completed" else None
        db.commit()
        before = (task.state, task.version, task.terminal_reason, task.final_result)
        events_before = db.query(TaskEvent).count()
        retry = worker(db, NeverModel()).execute_task("original")
        if terminal == "cancelled":
            await asyncio.wait_for(retry, 10)
        else:
            with pytest.raises(RuntimeError, match=f"Cannot execute terminal task original: {terminal}"):
                await asyncio.wait_for(retry, 10)
    with sessions() as db:
        task = db.get(Task, "original")
        assert (task.state, task.version, task.terminal_reason, task.final_result) == before
        assert db.query(TaskEvent).count() == events_before
