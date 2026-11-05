"""Regression: narrow safety blocker for task-scoped ownership/action records.

A mutating action must never reach the external executor unless the durable
task-ownership/action record has actually been committed. Both a failed/raising
begin_task_action and a commit rollback must fail closed before the executor.
"""

import asyncio

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task
from backend.services.task_runner import TaskRunner
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


def _create_task(engine, task_id):
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        session.add(Task(id=task_id, original_request=f"request {task_id}", state=TaskState.CREATED.value, version=1))
        session.commit()
    finally:
        session.close()


class MutatingTool:
    def get_policy_requirements(self):
        return {"mutating": True}


class SpyExecutor:
    def __init__(self):
        self.calls = 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        return MutatingTool()

    async def execute_tool(self, tool_name, input_data, policy_context=None):
        self.calls += 1
        return type("R", (), {
            "success": True,
            "model_summary": "ok",
            "data": None,
            "error": None,
            "evidence": None,
            "duration_ms": 1,
        })()


async def _run_blocked(tmp_path, mode):
    task_id = "safety-task"
    db_path = str(tmp_path / f"{mode}.db")
    engine = _make_file_db(db_path)
    try:
        _create_task(engine, task_id)
        SessionLocal = sessionmaker(bind=engine)

        original_begin = TaskRunner.begin_task_action
        original_make_decision = ModelDecisionEngine.make_decision

        decision_calls = 0

        async def scripted_make_decision(self, task_id, *args, **kwargs):
            nonlocal decision_calls
            decision_calls += 1
            if decision_calls == 1:
                return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})
            raise RuntimeError("done by test")

        if mode == "false_return":
            async def begin_fn(self, *args, **kwargs):
                return False
            TaskRunner.begin_task_action = begin_fn
        elif mode == "raise":
            async def begin_fn(self, *args, **kwargs):
                raise RuntimeError("begin failed by test")
            TaskRunner.begin_task_action = begin_fn
        elif mode == "commit_rollback":
            async def begin_fn(self, task_id=None, action_id=None, **kwargs):
                target_task = task_id or self._task_ownership_task_id
                target_nonce = self._task_ownership_nonce
                if not target_task or not target_nonce or not action_id:
                    return False
                try:
                    self.task_ownership.begin_action(
                        str(target_task), str(target_nonce), str(action_id), **kwargs
                    )
                except Exception:
                    self.db.rollback()
                    return False
                # Simulate a commit failure after a successful durable write attempt.
                self.db.rollback()
                return False
            TaskRunner.begin_task_action = begin_fn
        else:
            raise ValueError(mode)

        ModelDecisionEngine.make_decision = scripted_make_decision

        session = SessionLocal()
        try:
            runner = TaskRunner(session)
            ex = SpyExecutor()
            loop = TaskExecutionLoop(session, runner, ex)
            await runner.acquire_task_ownership(task_id=task_id, worker_id="w")
            try:
                await loop.execute_task(task_id)
            except Exception:
                pass

            assert ex.calls == 0, f"executor invoked {ex.calls} times in {mode}"

            task_row = session.query(Task).filter_by(id=task_id).first()
            assert task_row is None or task_row.state != TaskState.COMPLETED.value
        finally:
            session.close()
            TaskRunner.begin_task_action = original_begin
            ModelDecisionEngine.make_decision = original_make_decision
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_begin_task_action_false_return_fail_closed(tmp_path):
    await _run_blocked(tmp_path, "false_return")


@pytest.mark.asyncio
async def test_begin_task_action_raise_fail_closed(tmp_path):
    await _run_blocked(tmp_path, "raise")


@pytest.mark.asyncio
async def test_begin_task_action_commit_rollback_fail_closed(tmp_path):
    await _run_blocked(tmp_path, "commit_rollback")


@pytest.mark.asyncio
async def test_runner_begin_task_action_reports_false_on_commit_failure(tmp_path):
    db_path = str(tmp_path / "runner-commit.db")
    engine = _make_file_db(db_path)
    try:
        _create_task(engine, "t1")
        SessionLocal = sessionmaker(bind=engine)
        session = SessionLocal()
        try:
            runner = TaskRunner(session)
            await runner.acquire_task_ownership(task_id="t1", worker_id="w")
            original_commit = session.commit

            def boom():
                raise RuntimeError("commit boom")

            session.commit = boom
            try:
                assert await runner.begin_task_action(task_id="t1", action_id="a1") is False
            finally:
                session.commit = original_commit
        finally:
            session.close()
    finally:
        engine.dispose()
