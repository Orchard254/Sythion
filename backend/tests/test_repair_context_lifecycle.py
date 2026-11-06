"""Behavioral tests for model-decision repair context delivery."""

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'repair.db'}", connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "repair-test")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


from backend.tests.test_task_instruction_revision import seed


@pytest.mark.asyncio
async def test_repair_context_is_delivered_then_cleared(db, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    _, _, _ = await seed(db, acquire=False)

    seen_repair = []

    class Model:
        def __init__(self):
            self.calls = 0

        async def make_decision(self, **kwargs):
            self.calls += 1
            seen_repair.append(kwargs.get("repair_context"))
            if self.calls == 1:
                from backend.services.model_engine import ModelDecisionError

                raise ModelDecisionError("INVALID_DECISION_TYPE", "unknown tool")
            return "complete_task", {"result": "done"}

        def pop_pending_tool_calls(self):
            return []

    from backend.services.task_runner import TaskRunner
    from backend.tools.executor import ToolExecutor
    from backend.services.task_execution_loop import TaskExecutionLoop

    loop = TaskExecutionLoop(db, TaskRunner(db), ToolExecutor())
    loop.model_engine = Model()

    await loop.execute_task("original")

    assert seen_repair[0] is None
    assert seen_repair[1] is not None
    assert "Do NOT retry gui_*" in seen_repair[1]
    db.expire_all()
    from backend.models.database import Task

    assert db.get(Task, "original").state == "completed"
    assert db.get(Task, "original").final_result == "done"


@pytest.mark.asyncio
async def test_repair_context_not_required_after_success(db, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    _, _, _ = await seed(db, acquire=False)

    class Model:
        def __init__(self):
            self.calls = 0

        async def make_decision(self, **kwargs):
            self.calls += 1
            if self.calls == 1:
                from backend.services.model_engine import ModelDecisionError

                raise ModelDecisionError("INVALID_DECISION_TYPE", "unknown tool")
            assert kwargs.get("repair_context") is not None
            return "complete_task", {"result": "done"}

        def pop_pending_tool_calls(self):
            return []

    from backend.services.task_runner import TaskRunner
    from backend.tools.executor import ToolExecutor
    from backend.services.task_execution_loop import TaskExecutionLoop

    loop = TaskExecutionLoop(db, TaskRunner(db), ToolExecutor())
    loop.model_engine = Model()
    await loop.execute_task("original")

    db.expire_all()
    from backend.models.database import Task

    assert db.get(Task, "original").state == "completed"
