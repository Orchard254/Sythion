"""
Regression tests for the single action-budget authority.

Contract: settings.max_actions_per_task (surfaced through TaskBudget.max_actions)
is the ONLY definition of the per-task action limit. The execution loop must
read it from the budget, not from a private constant.
"""

import inspect

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.core.config import settings
from backend.models.database import Base
from backend.services.task_execution_loop import (
    DecisionType,
    TaskExecutionLoop,
)
from backend.core.budgets import TaskBudget


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def task_runner(db_session):
    from backend.services.task_runner import TaskRunner

    return TaskRunner(db_session)


def _failed_code(db, task_id):
    from backend.models.database import TaskEvent
    import json as _json

    failed = (
        db.query(TaskEvent)
        .filter_by(task_id=task_id, event_type="task.failed")
        .first()
    )
    if not failed:
        return None
    data = _json.loads(failed.event_data)
    return data.get("code")


# --- Single authority ---------------------------------------------------------


def test_loop_has_no_private_action_limit_constant():
    """The duplicated MAX_ACTIONS_PER_TASK constant was removed."""
    assert not hasattr(TaskExecutionLoop, "MAX_ACTIONS_PER_TASK")


def test_task_budget_resolves_limit_from_settings(monkeypatch):
    monkeypatch.setattr(settings, "max_actions_per_task", 7)
    budget = TaskBudget()
    assert budget.max_actions == 7


def test_explicit_override_still_possible():
    budget = TaskBudget(max_actions=3)
    assert budget.max_actions == 3


def test_loop_source_reads_budget_max_actions():
    source = inspect.getsource(TaskExecutionLoop.execute_task)
    assert "budget.max_actions" in source
    assert "MAX_ACTIONS_PER_TASK" not in source


# --- Enforcement ---------------------------------------------------------------


@pytest.mark.asyncio
async def test_configured_action_limit_fails_task_honestly(
    db_session, task_runner, monkeypatch
):
    """Exceeding the configured limit fails with ACTION_LIMIT_EXCEEDED."""
    monkeypatch.setattr(settings, "max_actions_per_task", 2)

    await task_runner.acquire_lease()
    task = await task_runner.create_task("Bounded task")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    decisions = {"n": 0}

    async def _always_tool(*args, **kwargs):
        decisions["n"] += 1
        return DecisionType.EXECUTE_TOOL, {
            "tool_name": "system_time",
            "params": {"n": decisions["n"]},
        }

    async def _tool(*args, **kwargs):
        return None

    loop.model_engine.make_decision = _always_tool
    loop._execute_decision = _tool

    await loop.execute_task(task.id)

    refreshed = await task_runner.get_task(task.id)
    assert refreshed.state == TaskState.FAILED.value
    assert _failed_code(db_session, task.id) == "ACTION_LIMIT_EXCEEDED"
    # Exactly max_actions decisions were allowed before the honest failure.
    assert decisions["n"] == 2
    await task_runner.release_lease()