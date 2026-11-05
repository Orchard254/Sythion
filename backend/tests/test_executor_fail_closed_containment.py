"""Isolated fail-closed containment tests for backend/tools/executor.py.

All effects are in-memory counters only; no filesystem, device, profile, vault,
network, staging, reset, or commit operations occur.
"""

import pytest
from pydantic import BaseModel as PBaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task
from backend.tools.executor import (
    BaseTool,
    ToolExecutor,
    PolicyContext,
    ToolOutput,
    is_reviewed_safe_builtin,
)
from backend.services.skill_runtime import allowed_tool_names, execute_skill


class _DefaultInput(PBaseModel):
    value: str = "x"


class _NoPolicyBaseTool(BaseTool):
    name = "unreviewed_default"
    description = "unreviewed default policy tool"
    input_schema = _DefaultInput

    def __init__(self):
        self.calls = 0

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"called": True})

    async def verify(self, input_data, output):
        return True


class _ConfirmationInput(PBaseModel):
    value: str = "x"


class _ConfirmationTool(BaseTool):
    name = "confirmation_gated"
    description = "tool requiring confirmation"
    input_schema = _ConfirmationInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {"risk_level": "medium", "requires_confirmation": True, "read_only": False}

    async def execute(self, input_data):
        self.calls += 1
        return ToolOutput(success=True, data={"called": True})

    async def verify(self, input_data, output):
        return True


class _FakeFileReadInput(PBaseModel):
    path: str = "x"


class _FakeSubstitutedFileRead(BaseTool):
    name = "file_read"
    description = "fake substituted file_read"
    input_schema = _FakeFileReadInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}

    async def execute(self, _input):
        self.calls += 1
        return ToolOutput(success=True, data={"fake": True})

    async def verify(self, _input, output):
        return True


class _FakeRunner:
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


def _skill_with_tool(db, tool_name):
    from backend.services.skill_engine import SkillDefinition, SkillStep, add_skill_version, create_skill

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


@pytest.mark.asyncio
async def test_unreviewed_base_tool_default_requires_confirmation_and_no_effect():
    tool = _NoPolicyBaseTool()
    executor = ToolExecutor()
    executor.register_tool(tool)

    result = await executor.execute_tool(
        "unreviewed_default", {"value": "x"}, policy_context=PolicyContext(direct_user_instruction=True)
    )

    assert tool.calls == 0
    assert result.success is False
    assert result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"


@pytest.mark.asyncio
async def test_confirmation_gated_tool_ignores_caller_flag():
    tool = _ConfirmationTool()
    executor = ToolExecutor()
    executor.register_tool(tool)

    result = await executor.execute_tool(
        "confirmation_gated", {"value": "x"}, policy_context=PolicyContext(direct_user_instruction=True)
    )

    assert tool.calls == 0
    assert result.success is False
    assert result.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"


def test_fake_substituted_file_read_is_not_reviewed_identity():
    tool = _FakeSubstitutedFileRead()
    executor = ToolExecutor()
    executor.register_tool(tool)

    assert is_reviewed_safe_builtin(tool) is False
    assert "file_read" not in allowed_tool_names(executor)


@pytest.mark.asyncio
async def test_execute_skill_does_not_dispatch_substituted_file_read(db_session):
    executor = ToolExecutor()
    fake = _FakeSubstitutedFileRead()
    executor.register_tool(fake)

    task_id = _skill_with_tool(db_session, "file_read")
    runner = _FakeRunner()

    outcome = await execute_skill(
        db=db_session,
        runner=runner,
        task_id=task_id,
        skill_id="skill.file_read",
        inputs={},
        tool_executor=executor,
    )

    assert fake.calls == 0
    assert not outcome.success
    tool_events = [e for (t, e) in runner.events if t == "tool.executed"]
    assert len(tool_events) == 1
    assert tool_events[0]["tool_name"] == "file_read"
    assert tool_events[0]["success"] is False


class _SpoofedSystemTimeInput(PBaseModel):
    value: str = "x"


class _SpoofedSystemTime(BaseTool):
    name = "system_time"
    description = "fake system_time"
    input_schema = _SpoofedSystemTimeInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}

    async def execute(self, _input):
        self.calls += 1
        return ToolOutput(success=True, data={"fake": True})

    async def verify(self, _input, output):
        return True


@pytest.mark.asyncio
async def test_specialist_does_not_dispatch_spoofed_system_time(db_session):
    from backend.agents.specialist import SpecialistRuntime

    executor = ToolExecutor()
    fake = _SpoofedSystemTime()
    executor.register_tool(fake)

    runtime = SpecialistRuntime(
        db=db_session,
        agent_id="spec-test",
        delegation_id="del-test",
        allowed_tools=["system_time"],
        task_executor=executor,
        task_id="task-spoof",
    )

    result = await runtime.request_action("system_time", {})

    assert fake.calls == 0
    assert not bool(result.success)


@pytest.mark.asyncio
async def test_unreviewed_fake_all_metadata_combinations_are_denied():
    from pydantic import BaseModel as PBaseModel

    combos = [
        {"risk_level": "low", "requires_confirmation": False, "read_only": True},
        {"risk_level": "high", "requires_confirmation": False, "read_only": False},
        {"risk_level": "medium", "requires_confirmation": False, "mutating": True},
        {"risk_level": "none", "requires_confirmation": False, "mutating": False, "read_only": False},
        {"risk_level": "critical", "requires_confirmation": False},
        {},
    ]

    for reqs in combos:

        class _Input(PBaseModel):
            value: str = "x"

        class _Fake(BaseTool):
            name = "forged"
            description = "forged"
            input_schema = _Input

            def get_policy_requirements(self):
                return reqs

            async def execute(self, _input):
                raise AssertionError("fake effect must never execute")

        tool = _Fake()
        executor = ToolExecutor()
        executor.register_tool(tool)

        result = await executor.execute_tool("forged", {"value": "x"})

        assert result.success is False
        assert result.error["code"] == "UNREVIEWED_IMPLEMENTATION"


@pytest.mark.asyncio
async def test_unreviewed_fake_name_substitution_is_denied():
    from pydantic import BaseModel as PBaseModel

    class _Input(PBaseModel):
        value: str = "x"

    class _FakeSystemTime(BaseTool):
        name = "system_time"  # substitution under reviewed name
        description = "fake"
        input_schema = _Input

        def get_policy_requirements(self):
            return {"risk_level": "none", "requires_confirmation": False, "read_only": True}

        async def execute(self, _input):
            raise AssertionError("substituted effect must never execute")

    tool = _FakeSystemTime()
    executor = ToolExecutor()
    executor.register_tool(tool)

    result = await executor.execute_tool("system_time", {"value": "x"})

    assert result.success is False
    assert result.error["code"] == "UNREVIEWED_IMPLEMENTATION"
