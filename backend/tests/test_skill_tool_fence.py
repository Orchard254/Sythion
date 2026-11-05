"""Isolated fencing tests for indirect skill tool dispatch."""

import json

import pytest
from pydantic import BaseModel as PBaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState, ToolOutput
from backend.models.database import Base, Task
from backend.services.skill_engine import (
    SkillDefinition,
    SkillStep,
    add_skill_version,
    create_skill,
    execute_skill,
    allowed_tool_names,
)
from backend.tools.executor import ToolExecutor
from backend.tools.system_tools import SystemTimeTool


class FakeReadInput(PBaseModel):
    path: str = "x"


class FakeReadTool:
    name = "file_read"
    description = "read"
    input_schema = FakeReadInput

    def __init__(self, side_effect=None):
        self.side_effect = side_effect
        self.calls = 0

    def get_policy_requirements(self):
        return {}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
        if self.side_effect:
            await self.side_effect(input_data)
        return ToolOutput(success=True, data={"value": "read"}, model_summary="read")


class FakeMutatingInput(PBaseModel):
    path: str = "x"


class FakeMutatingTool:
    name = "state_changing"
    description = "mutates"
    input_schema = FakeMutatingInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {"mutating": True, "app_internal": True}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"value": "mutated"}, model_summary="mutated")


class FakeMalformedTool:
    name = "malformed_meta"
    description = "malformed"
    input_schema = FakeMutatingInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        # Truthy non-boolean metadata is malformed and must fail closed.
        return {"mutating": "yes"}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"value": "mutated"}, model_summary="mutated")


class MissingMetaTool:
    name = "missing_meta"
    description = "missing"
    input_schema = FakeMutatingInput

    def __init__(self):
        self.calls = 0

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"value": "mutated"}, model_summary="mutated")


class FakeRunner:
    def __init__(self):
        self.events = []

    async def get_task(self, task_id):
        return None

    async def record_event(self, task_id, event_type, event_data):
        self.events.append((event_type, event_data))

    async def transition_state(self, **kwargs):
        return None


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


def _skill_with_tool(db, executor_name, tool_name):
    from backend.services.skill_engine import SkillDefinition, SkillStep

    definition = SkillDefinition(
        version="1.0.0",
        name="fence test",
        steps=[SkillStep(step_id="s1", tool=tool_name, inputs={})],
    )
    skill = create_skill(db, skill_id=f"skill.{tool_name}", name=definition.name, description="")
    add_skill_version(db, skill, definition, [])
    db.commit()
    task = Task(id=f"t-{tool_name}", original_request="run", state="running", version=1)
    db.add(task)
    db.commit()
    return task.id


def test_allowed_tool_names_fail_closed_for_state_changing_and_malformed():
    executor = ToolExecutor()
    read_tool = SystemTimeTool()
    mutating_tool = FakeMutatingTool()
    malformed_tool = FakeMalformedTool()
    missing_tool = MissingMetaTool()
    executor.register_tool(read_tool)
    executor.register_tool(mutating_tool)
    executor.register_tool(malformed_tool)
    executor.register_tool(missing_tool)

    allowed = allowed_tool_names(executor)
    # A genuinely reviewed safe built-in remains allowed.
    assert "system_time" in allowed
    assert "state_changing" not in allowed
    assert "malformed_meta" not in allowed
    assert "missing_meta" not in allowed


@pytest.mark.asyncio
async def test_execute_skill_does_not_execute_state_changing_tool(db_session):
    executor = ToolExecutor()
    read_tool = FakeReadTool()
    mutating_tool = FakeMutatingTool()
    executor.register_tool(read_tool)
    executor.register_tool(mutating_tool)

    task_id = _skill_with_tool(db_session, "mutating", "state_changing")
    runner = FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.state_changing",
        inputs={},
        tool_executor=executor,
    )

    assert mutating_tool.calls == 0
    assert not outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool_name"] == "state_changing"
    assert tool_events[0]["success"] is False
    assert tool_events[0]["mutating"] is True
    assert tool_events[0]["error"]["code"] == "SKILL_STATE_CHANGING_TOOL_DENIED"


@pytest.mark.asyncio
async def test_execute_skill_does_not_execute_malformed_metadata_tool(db_session):
    executor = ToolExecutor()
    malformed_tool = FakeMalformedTool()
    executor.register_tool(malformed_tool)

    task_id = _skill_with_tool(db_session, "malformed", "malformed_meta")
    runner = FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.malformed_meta",
        inputs={},
        tool_executor=executor,
    )

    assert malformed_tool.calls == 0
    assert not outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert tool_events[0]["mutating"] is True


@pytest.mark.asyncio
async def test_execute_skill_read_only_continues(db_session):
    executor = ToolExecutor()
    read_tool = SystemTimeTool()
    executor.register_tool(read_tool)

    task_id = _skill_with_tool(db_session, "readonly", "system_time")
    runner = FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.system_time",
        inputs={},
        tool_executor=executor,
    )

    # SystemTimeTool's execute does not expose a simple call count, so success is
    # the legitimate read-only coverage assertion.
    assert outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert tool_events[0]["tool_name"] == "system_time"
    assert tool_events[0]["mutating"] is False


class FakePrivacyInput(PBaseModel):
    value: str = "x"


class FakePrivacyTool:
    name = "privacy_tool"
    description = "privacy-sensitive read"
    input_schema = FakePrivacyInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": False,
            "read_only": True,
            "privacy_sensitive": True,
        }

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"value": "private"}, model_summary="private")


@pytest.mark.asyncio
async def test_execute_skill_does_not_dispatch_privacy_gated_tool(db_session):
    executor = ToolExecutor()
    privacy_tool = FakePrivacyTool()
    executor.register_tool(privacy_tool)

    task_id = _skill_with_tool(db_session, "privacy", "privacy_tool")
    runner = FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.privacy_tool",
        inputs={},
        tool_executor=executor,
    )

    assert privacy_tool.calls == 0
    assert not outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert tool_events[0]["mutating"] is False
    assert tool_events[0]["error"]["code"] == "SKILL_TOOL_NOT_ALLOWED"


def test_actual_registered_skill_subset_excludes_privacy_screen_and_ui_tools():
    from backend.tools.registry import register_all_tools

    executor = ToolExecutor()
    register_all_tools(executor)
    allowed = allowed_tool_names(executor)

    # browser_screenshot captures browser content and is now privacy-gated.
    for forbidden in ("clipboard_read", "gui_observe", "gui_screenshot", "browser_screenshot"):
        assert forbidden not in allowed
    assert len(allowed) == 27


@pytest.mark.asyncio
async def test_browser_screenshot_real_metadata_does_not_dispatch(db_session):
    from backend.tools.system_tools import create_system_tools
    from backend.tools.executor import ToolExecutor

    executor = ToolExecutor()
    tool = next(t for t in create_system_tools() if t.name == "browser_screenshot")
    executor.register_tool(tool)

    # A rough internal call counter: wrap execute without changing metadata.
    original_execute = tool.execute

    async def execute_spy(*args, **kwargs):
        raise AssertionError("browser_screenshot.execute must not be called")

    tool.execute = execute_spy

    task_id = _skill_with_tool(db_session, "browser", "browser_screenshot")
    runner = FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.browser_screenshot",
        inputs={},
        tool_executor=executor,
    )

    assert not outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert tool_events[0]["tool_name"] == "browser_screenshot"
    assert tool_events[0]["mutating"] is False
    assert tool_events[0]["error"]["code"] == "SKILL_TOOL_NOT_ALLOWED"

    # Restore for hygiene even though no effect occurred.
    tool.execute = original_execute