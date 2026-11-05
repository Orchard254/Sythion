"""Regression: consequential dispatch is blocked before any external action,
even when pending action evidence is internally approved.
"""

import asyncio
import json

import pytest

from backend.api.schemas import TaskState
from backend.models.database import Base, Task, TaskEvent
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.task_runner import TaskRunner
from backend.tools.executor import ToolExecutor
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


class _CountingFakeEffect:
    def __init__(self):
        self.calls = 0
        self.started = 0

    def get_policy_requirements(self):
        return {"mutating": True, "requires_confirmation": True, "risk_level": "high"}

    async def execute(self, validated_input):
        self.calls += 1

    async def verify(self, validated_input, output):
        return True

    def validate_targets(self, validated_input):
        return None

    _input_schema = object()


@pytest.fixture
def db_session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def task_runner(db_session):
    runner = TaskRunner(db_session)
    asyncio.run(runner.acquire_lease())
    try:
        yield runner
    finally:
        asyncio.run(runner.release_lease())


@pytest.mark.asyncio
async def test_internal_approved_pending_action_does_not_dispatch_and_policy_is_nonzero(db_session, task_runner):
    task = await task_runner.create_task("pending consent")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    fake = _CountingFakeEffect()
    loop = TaskExecutionLoop(db_session, task_runner, ToolExecutor())
    loop.tool_executor.get_tool = lambda name: fake

    await task_runner.transition_state(
        task.id,
        TaskState.RUNNING,
        "action.pending_approval",
        {"tool_name": "fake-effect", "params": {"path": "target.txt"}, "action_id": "action-1"},
    )
    fake.calls == 0

    started_before = db_session.query(TaskEvent).filter_by(task_id=task.id, event_type="action.started").count()
    result = await loop._execute_tool(
        task.id,
        {"tool_name": "fake-effect", "params": {"path": "target.txt"}},
        approved_by_user=True,
        action_id="action-1",
    )
    started_after = db_session.query(TaskEvent).filter_by(task_id=task.id, event_type="action.started").count()

    assert result is False
    assert fake.calls == 0
    assert started_after == started_before
    assert started_after == 0
    # Pending is not consumed/cleared by the blocked attempt.
    assert await task_runner.get_task(task.id) is not None


@pytest.mark.asyncio
async def test_unrelated_internal_approval_does_not_execute_pending_b(db_session, task_runner):
    task = await task_runner.create_task("pending b")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    fake = _CountingFakeEffect()
    loop = TaskExecutionLoop(db_session, task_runner, ToolExecutor())
    loop.tool_executor.get_tool = lambda name: fake

    await task_runner.transition_state(
        task.id,
        TaskState.RUNNING,
        "action.pending_approval",
        {"tool_name": "fake-effect", "params": {"path": "target.txt"}, "action_id": "pending-b"},
    )
    await task_runner.transition_state(
        task.id,
        TaskState.RUNNING,
        "action.approved",
        {"action_id": "unrelated", "decision": "approved"},
    )
    pending_before = await task_runner.get_task(task.id)
    from backend.services.task_execution_loop import UnresolvedActionEvidenceError

    with pytest.raises(UnresolvedActionEvidenceError):
        await loop._execute_tool(
            task.id,
            {"tool_name": "fake-effect", "params": {"path": "target.txt"}},
            approved_by_user=True,
            action_id="pending-b",
        )
    started_after = db_session.query(TaskEvent).filter_by(task_id=task.id, event_type="action.started").count()
    assert fake.calls == 0
    assert started_after == 0
