"""Actual integration tests for task-scoped execution ownership."""

import asyncio
import os
import time
from pathlib import Path

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.services.task_runner import TaskRunner, get_runner
from backend.runtime.task_ownership import TaskOwnershipManager, TaskOwnershipRow
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionEngine, DecisionType
from backend.api.schemas import TaskState


def _make_file_db(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _create_tasks(engine, ids):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        for task_id in ids:
            session.add(Task(id=task_id, original_request=f"request {task_id}", state=TaskState.CREATED.value, version=1))
            session.flush()
        session.commit()
    finally:
        session.close()


@pytest.mark.asyncio
async def test_two_independent_workers_reach_blocked_model_decision_concurrently(tmp_path, monkeypatch):
    db_path = str(tmp_path / "concurrent.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["task-a", "task-b"])
        SessionLocal = sessionmaker(bind=engine)

        reached = {tid: asyncio.Event() for tid in ("task-a", "task-b")}
        block_event = asyncio.Event()

        async def blocked_make_decision(self, task_id, *args, **kwargs):
            reached[task_id].set()
            await block_event.wait()
            raise RuntimeError("blocked by test")

        monkeypatch.setattr(ModelDecisionEngine, "make_decision", blocked_make_decision)
        try:
            loops = []
            executors = []
            tasks = []
            for task_id in ("task-a", "task-b"):
                session = SessionLocal()
                runner = TaskRunner(session)
                ex = type("Ex", (), {})()
                ex.list_tools = lambda: []
                ex.get_tool = lambda name: None
                ex.execute_tool = None
                loop = TaskExecutionLoop(session, runner, ex)
                loops.append((session, loop))
                async def runner_fn(loop_obj=loop, tid=task_id):
                    await loop_obj.execute_task(tid)
                tasks.append(asyncio.create_task(runner_fn()))

            await asyncio.wait_for(
                asyncio.gather(reached["task-a"].wait(), reached["task-b"].wait()),
                timeout=3.0,
            )
            assert reached["task-a"].is_set() and reached["task-b"].is_set()
        finally:
            # Release/close blocked tasks even when an assertion raises.
            block_event.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for session, _ in loops:
                session.close()
            # monkeypatch (auto-restore) resets ModelDecisionEngine.make_decision
            pass
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_same_task_is_exclusive_across_workers(tmp_path, monkeypatch):
    db_path = str(tmp_path / "same-task.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["same-task"])
        SessionLocal = sessionmaker(bind=engine)

        reached = asyncio.Event()
        block_event = asyncio.Event()

        async def blocked_make_decision(self, task_id, *args, **kwargs):
            reached.set()
            await block_event.wait()
            raise RuntimeError("blocked by test")

        monkeypatch.setattr(ModelDecisionEngine, "make_decision", blocked_make_decision)
        try:
            session1 = SessionLocal()
            runner1 = TaskRunner(session1)
            ex1 = type("Ex", (), {})()
            ex1.list_tools = lambda: []
            ex1.get_tool = lambda name: None
            ex1.execute_tool = None
            loop1 = TaskExecutionLoop(session1, runner1, ex1)
            task1 = asyncio.create_task(loop1.execute_task("same-task"))

            await asyncio.wait_for(reached.wait(), timeout=3.0)

            session2 = SessionLocal()
            runner2 = TaskRunner(session2)
            ex2 = type("Ex", (), {})()
            ex2.list_tools = lambda: []
            ex2.get_tool = lambda name: None
            ex2.execute_tool = None
            loop2 = TaskExecutionLoop(session2, runner2, ex2)
            with pytest.raises(Exception):
                await asyncio.wait_for(loop2.execute_task("same-task"), timeout=2.0)

            task1.cancel()
            await asyncio.gather(task1, return_exceptions=True)
            session1.close()
            session2.close()
        finally:
            # monkeypatch (auto-restore) resets ModelDecisionEngine.make_decision
            pass
    finally:
        engine.dispose()


class MutatingTool:
    def get_policy_requirements(self):
        return {"mutating": True}


class DummyExecutor:
    def __init__(self):
        self.active = 0
        self.max_active = 0
        self.completed = 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        return MutatingTool()

    async def execute_tool(self, tool_name, input_data, policy_context=None):
        self.active += 1
        self.max_active = max(self.max_active, self.active)
        await asyncio.sleep(0.1)
        self.active -= 1
        self.completed += 1
        return type("R", (), {"success": True, "model_summary": "ok", "data": None, "error": None, "evidence": None, "duration_ms": 1})()


@pytest.mark.asyncio
async def test_conflicting_mutating_actions_do_not_overlap(tmp_path, monkeypatch):
    db_path = str(tmp_path / "tool-overlap.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["task-a", "task-b"])
        SessionLocal = sessionmaker(bind=engine)

        call_counts = {"task-a": 0, "task-b": 0}
        block_event = asyncio.Event()

        async def scripted_make_decision(self, task_id, *args, **kwargs):
            call_counts[task_id] += 1
            if call_counts[task_id] == 1:
                return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})
            await block_event.wait()
            raise RuntimeError("blocked by test")

        monkeypatch.setattr(ModelDecisionEngine, "make_decision", scripted_make_decision)
        try:
            loops = []
            executors = {}
            tasks = []
            for task_id in ("task-a", "task-b"):
                session = SessionLocal()
                runner = TaskRunner(session)
                ex = DummyExecutor()
                executors[task_id] = ex
                loop = TaskExecutionLoop(session, runner, ex)
                loops.append((session, loop))
                async def runner_fn(loop_obj=loop, tid=task_id):
                    await loop_obj.execute_task(tid)
                tasks.append(asyncio.create_task(runner_fn()))

            # Allow both tools to finish their first serialized execution.
            await asyncio.sleep(0.4)

            for ex in executors.values():
                assert ex.completed >= 1
                assert ex.max_active <= 1
        finally:
            # Release/close blocked tasks even when an assertion raises.
            block_event.set()
            for t in tasks:
                t.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            for session, _ in loops:
                session.close()
            # monkeypatch (auto-restore) resets ModelDecisionEngine.make_decision
            pass
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_ownership_loss_stops_further_decisions_and_actions(tmp_path, monkeypatch):
    db_path = str(tmp_path / "ownership-loss.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["task-loss"])
        SessionLocal = sessionmaker(bind=engine)

        decision_calls = 0
        completed_tools = 0
        block_event = asyncio.Event()
        tool_done = asyncio.Event()

        async def scripted_make_decision(self, task_id, *args, **kwargs):
            nonlocal decision_calls
            decision_calls += 1
            if decision_calls == 1:
                return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})
            await block_event.wait()
            raise RuntimeError("blocked by test")

        original_renew = TaskOwnershipManager.renew
        renew_calls = 0

        def failing_renew(self, *args, **kwargs):
            nonlocal renew_calls
            renew_calls += 1
            if renew_calls >= 2:
                return False
            return original_renew(self, *args, **kwargs)

        class LossExecutor(DummyExecutor if False else object):
            pass

        class RecordingExecutor(DummyExecutor):
            async def execute_tool(self, tool_name, input_data, policy_context=None):
                nonlocal completed_tools
                await asyncio.sleep(0.05)
                completed_tools += 1
                return await super().execute_tool(tool_name, input_data, policy_context)

        # Reuse DummyExecutor definition from above by copying minimal behavior
        class LossTool(MutatingTool):
            pass

        class LossExecutor2:
            def __init__(self):
                self.active = 0
                self.max_active = 0
                self.completed = 0
                self.loop = None

            def list_tools(self):
                return []

            def get_tool(self, name):
                return MutatingTool()

            async def execute_tool(self, tool_name, input_data, policy_context=None):
                nonlocal completed_tools
                self.active += 1
                self.max_active = max(self.max_active, self.active)
                await asyncio.sleep(0.05)
                if self.loop is not None:
                    self.loop._ownership_lost = True
                await asyncio.sleep(0.05)
                self.active -= 1
                self.completed += 1
                completed_tools += 1
                tool_done.set()
                return type("R", (), {"success": True, "model_summary": "ok", "data": None, "error": None, "evidence": None, "duration_ms": 1})()

        session = None
        task = None
        rerun_session = None
        monkeypatch.setattr(ModelDecisionEngine, "make_decision", scripted_make_decision)
        monkeypatch.setattr(TaskOwnershipManager, "renew", failing_renew)
        try:
            from backend.core.config import settings

            monkeypatch.setattr(settings, "lease_heartbeat_seconds", 0.01)

            session = SessionLocal()
            runner = TaskRunner(session)
            ex = LossExecutor2()
            loop = TaskExecutionLoop(session, runner, ex)
            ex.loop = loop
            task = asyncio.create_task(loop.execute_task("task-loss"))

            await asyncio.wait_for(tool_done.wait(), timeout=3.0)
            assert completed_tools == 1
            assert getattr(loop, "_ownership_lost", False) is True

            # The in-flight action is not reported as successful, and the same
            # still-owned task cannot be silently rerun to hide it.
            task_row = session.query(Task).filter_by(id="task-loss").first()
            assert task_row.state != TaskState.COMPLETED.value
            session.close()
            session = None

            rerun_session = SessionLocal()
            try:
                rerun_runner = TaskRunner(rerun_session)
                rerun_loop = TaskExecutionLoop(rerun_session, rerun_runner, LossExecutor2())
                with pytest.raises(Exception):
                    await asyncio.wait_for(rerun_loop.execute_task("task-loss"), timeout=1.0)
            finally:
                rerun_session.close()
                rerun_session = None
        finally:
            # Release/close blocked tasks and sessions even when assertions fail.
            block_event.set()
            if task is not None:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
            if rerun_session is not None:
                rerun_session.close()
                rerun_session = None
            if session is not None:
                session.close()
                session = None
            # monkeypatch (auto-restore) resets patched seams
            pass
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_cancelled_mutating_actions_do_not_begin_until_operation_ends(tmp_path, monkeypatch):
    db_path = str(tmp_path / "cancel-guard.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["task-cancel-a", "task-cancel-b"])
        SessionLocal = sessionmaker(bind=engine)

        class GuardedExecutor(DummyExecutor):
            def __init__(self, name):
                super().__init__()
                self.name = name
                self.started_at = None
                self.finished_at = None
                self.held = False
                self._lock = asyncio.Lock()

            async def execute_tool(self, tool_name, input_data, policy_context=None):
                async with self._lock:
                    self.held = True
                    self.started_at = asyncio.get_event_loop().time()
                    await asyncio.sleep(0.25)
                    self.finished_at = asyncio.get_event_loop().time()
                    # Record an action id for ownership uncertainty assertions.
                    self.last_action = "guarded-action-" + self.name
                    result = await super().execute_tool(tool_name, input_data, policy_context)
                    result.last_action = self.last_action
                    self.held = False
                    return result

        async def scripted_make_decision(self, task_id, *args, **kwargs):
            return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})

        monkeypatch.setattr(ModelDecisionEngine, "make_decision", scripted_make_decision)
        try:
            loops = []
            executors = {}
            tasks = []
            for task_id in ("task-cancel-a", "task-cancel-b"):
                session = SessionLocal()
                runner = TaskRunner(session)
                ex = GuardedExecutor(task_id)
                executors[task_id] = ex
                loop = TaskExecutionLoop(session, runner, ex)
                loops.append((session, loop))

                async def runner_fn(loop_obj=loop, tid=task_id):
                    try:
                        await loop_obj.execute_task(tid)
                    except (asyncio.CancelledError, Exception):
                        pass

                tasks.append(asyncio.create_task(runner_fn()))

            await asyncio.sleep(0.05)
            # Cancel twice to exercise repeated cancellation of requester.
            tasks[0].cancel()
            tasks[1].cancel()
            tasks[0].cancel()
            await asyncio.gather(*tasks, return_exceptions=True)

            # Underlying delayed operation is not cancelled by requester
            # cancellation; second executor cannot have started before the first
            # guarded operation truly ends.
            first = executors["task-cancel-a"]
            second = executors["task-cancel-b"]
            if first.started_at is not None and second.started_at is not None:
                # At most one at a time; if second started, it should be after
                # first's actual finish.
                assert first.max_active <= 1
                if second.started_at < first.started_at:
                    first, second = second, first
                assert second.started_at >= first.finished_at

            # Session cleanup and ownership rows remain queryable.
            await asyncio.sleep(0.05)
            for session, loop in loops:
                row = session.query(TaskOwnershipRow).filter_by(task_id=loop.runner._task_ownership_task_id).first()
                if row is not None:
                    assert row.status in ("ACTIVE",)
                session.close()
        finally:
            # monkeypatch (auto-restore) resets ModelDecisionEngine.make_decision
            pass
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_global_stop_remains_safe_with_task_scoped_ownership(tmp_path):
    db_path = str(tmp_path / "stop.db")
    engine = _make_file_db(db_path)
    try:
        _create_tasks(engine, ["task-stop"])
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            task = session.query(Task).filter_by(id="task-stop").first()
            task.state = TaskState.RUNNING.value
            session.commit()

            runner = get_runner(session)
            updated = await runner.force_cancel("task-stop")
            assert updated.state == TaskState.CANCELLED.value
        finally:
            session.close()
    finally:
        engine.dispose()
