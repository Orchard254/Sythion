"""
Acceptance gate: Read-only system-tool vertical slice (Phase B).

A full task runs end-to-end through a real read-only system tool: decision ->
policy + target validation -> tool execute -> fresh verification -> durable
tool.executed event -> honest completion. Evidence is recorded and the
completion precondition is satisfied by verified tool output.
"""

import pytest

from backend.api.schemas import TaskState
from backend.services.task_runner import TaskRunner
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import DecisionType
from backend.tools.executor import ToolExecutor
from backend.tools.system_tools import create_system_tools
from backend.tools.gui_tools import create_gui_tools
from backend.models.database import TaskEvent


def _event_codes(db_session, task_id):
    return [
        e.event_type for e in db_session.query(TaskEvent).filter_by(task_id=task_id).order_by(
            TaskEvent.sequence_num.asc()
        )
    ]


@pytest.mark.asyncio
async def test_readonly_system_tool_vertical_slice(db_session, task_runner):
    """A task driven through a real read-only tool completes with evidence."""
    ex = ToolExecutor()
    for tool in create_system_tools():
        ex.register_tool(tool)

    await task_runner.acquire_lease()
    task = await task_runner.create_task("What time is it?")
    await task_runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})

    loop = TaskExecutionLoop(db_session, task_runner, ex)
    calls = {"n": 0}

    async def _decision(*args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            # First turn: read-only system tool via a direct model call.
            return DecisionType.EXECUTE_TOOL, {"tool_name": "system_time", "params": {}}
        # Second turn: complete only after tool output was recorded.
        return DecisionType.COMPLETE_TASK, {"result": "The time is now"}

    loop.model_engine.make_decision = _decision

    await loop.execute_task(task.id)

    task = await task_runner.get_task(task.id)
    assert task.state == TaskState.COMPLETED.value
    assert task.final_result == "The time is now"

    events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(
        TaskEvent.sequence_num.asc()
    ).all()
    codes = [e.event_type for e in events]
    assert "task.created" in codes
    assert "task.execution_started" in codes
    assert "tool.executed" in codes
    assert "task.completed" in codes

    tool_event = next(e for e in events if e.event_type == "tool.executed")
    data = _load(tool_event.event_data)
    assert data["success"] is True
    assert data["tool_name"] == "system_time"
    assert data["verification"] == "passed"
    assert data["mutating"] is False
    assert isinstance(data.get("evidence"), dict)
    assert data["evidence"].get("source") == "system_clock"
    assert "datetime" in json_summary(data)
    await task_runner.release_lease()


def _load(raw):
    import json
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        return {}


def json_summary(data) -> str:
    import json
    return json.dumps(data.get("output") or data.get("model_summary") or data, default=str)


@pytest.mark.asyncio
async def test_readonly_system_tool_is_model_visible(db_session):
    """Read-only system tools are exposed directly to the model (no wrapper)."""
    from backend.services.model_engine import ModelDecisionEngine

    ex = ToolExecutor()
    for tool in create_system_tools():
        ex.register_tool(tool)
    for tool in create_gui_tools():
        ex.register_tool(tool)

    engine = ModelDecisionEngine(db_session, ex)
    names = {t["name"] for t in engine._model_visible_tools()}
    assert "system_time" in names
    assert "system_battery" in names
    assert "system_disk" in names
    assert "system_file_roots" in names
    assert "system_capabilities" in names

    # Mutating / screen-access tools must never be in the model-visible subset,
    # even when registered.
    assert "file_delete" not in names
    assert "app_launch" not in names
    assert "package_install" not in names
    assert "gui_click" not in names
    assert "gui_observe" not in names
    assert "gui_focus" not in names
