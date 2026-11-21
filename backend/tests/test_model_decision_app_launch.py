"""Regression tests for the app_launch advertise/visibility contract.

A model must never be told about a tool that the same mode's visibility filter
would reject. When a registered-but-hidden tool is nonetheless called, the
decision boundary must fail closed with an honest error instead of the
misleading "Unknown decision type" after pointless repair attempts.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError
from backend.services.model_prompt_contract import build_system_prompt


class _AppLaunchClient:
    """Provider that deterministically proposes the same hidden-tool call."""

    def __init__(self):
        self.calls = 0

    def provider_name(self):
        return "test-client"

    async def decide(self, messages, tools, max_tokens, **kwargs):
        self.calls += 1
        return {
            "choices": [{"message": {"tool_calls": [{
                "function": {"name": "app_launch",
                             "arguments": '{"app_id":"notepad"}'},
            }]}}],
            "usage": {"total_tokens": 1},
        }


def _app_launch_response():
    return {"message": {"tool_calls": [{
        "function": {"name": "app_launch", "arguments": '{"app_id":"notepad"}'},
    }]}}


@pytest.fixture
def loop_db(tmp_path, monkeypatch):
    from sqlalchemy import create_engine, event
    from sqlalchemy.orm import sessionmaker

    from backend.models.database import Base

    engine = create_engine(
        f"sqlite:///{tmp_path / 'applaunch.db'}", connect_args={"timeout": 10}
    )

    @event.listens_for(engine, "connect")
    def _setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "applaunch-test")
    monkeypatch.setattr(
        "backend.services.conversation_state.update_state", lambda *a, **k: None
    )
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


@pytest.mark.parametrize("autonomy,expected_visible", [
    ("guarded", False),
    ("autonomous", True),
])
def test_prompt_advertises_app_launch_exactly_when_visible(
    test_db, tool_executor, monkeypatch, autonomy, expected_visible,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)

    visible = {t["name"] for t in engine._model_visible_tools()}
    prompt = engine._build_system_prompt()

    assert ("app_launch" in visible) is expected_visible
    # The prompt must name the tool only if the same mode will accept its call.
    assert ("`app_launch`" in prompt) is expected_visible
    if not expected_visible:
        assert "app_launch" not in prompt


def test_prompt_without_app_launch_in_available_tools_never_advertises_it():
    prompt = build_system_prompt(
        available_tools="system_time, system_capabilities", active_autonomy="autonomous"
    )
    assert "app_launch" not in prompt
    assert "app_focus" not in prompt


def test_hidden_app_launch_call_fails_closed_with_honest_error(
    test_db, tool_executor, monkeypatch,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    engine = ModelDecisionEngine(test_db, tool_executor)

    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_app_launch_response())

    assert exc.value.code == "TOOL_NOT_AVAILABLE"
    assert "check AVAILABLE TOOLS" in exc.value.message
    assert "Unknown decision type" not in exc.value.message
    # Nothing was parked for later execution.
    assert engine.pop_pending_tool_calls() == []


@pytest.mark.asyncio
async def test_deterministically_invalid_type_returns_honest_fail_task(
    test_db, tool_executor, monkeypatch,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    client = _AppLaunchClient()
    engine = ModelDecisionEngine(test_db, tool_executor, model_client=client)

    kind, params = await engine.make_decision(
        task_id="t-applaunch-async",
        original_request="open notepad",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )

    assert kind == "fail_task"
    assert params["code"] == "TOOL_NOT_AVAILABLE"
    assert "check AVAILABLE TOOLS" in params["reason"]
    # A deterministically invalid type is not repaired three times.
    assert client.calls == 1


def test_autonomous_app_launch_call_maps_to_execute_tool(
    test_db, tool_executor, monkeypatch,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    engine = ModelDecisionEngine(test_db, tool_executor)

    kind, params = engine._parse_decision(_app_launch_response())

    assert kind == "execute_tool"
    assert params["tool_name"] == "app_launch"
    assert params["params"]["app_id"] == "notepad"


@pytest.mark.asyncio
async def test_loop_fails_honestly_without_three_repairs(
    loop_db, tool_executor, monkeypatch,
):
    from backend.models.database import Task
    from backend.services.task_execution_loop import TaskExecutionLoop
    from backend.services.task_runner import TaskRunner
    from backend.tests.test_task_instruction_revision import seed

    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    await seed(loop_db, acquire=False)
    client = _AppLaunchClient()
    engine = ModelDecisionEngine(loop_db, tool_executor, model_client=client)
    loop = TaskExecutionLoop(loop_db, TaskRunner(loop_db), tool_executor)
    loop.model_engine = engine

    await loop.execute_task("original")

    loop_db.expire_all()
    task = loop_db.get(Task, "original")
    reason = task.terminal_reason or ""
    assert task.state == "failed"
    assert "check AVAILABLE TOOLS" in reason
    assert "repair attempts" not in reason
    assert "Unknown decision type" not in reason
    assert client.calls == 1
