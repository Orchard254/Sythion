"""Isolated containment tests for specialist direct ToolExecutor actions."""

import pytest
from pydantic import BaseModel as PBaseModel
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.agents.specialist import SpecialistRuntime
from backend.tools.executor import ToolExecutor
from backend.api.schemas import ToolOutput


class FakeReadInput(PBaseModel):
    path: str = "x"


class FakeReadTool:
    name = "file_read"
    description = "read"
    input_schema = FakeReadInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
        return {}

    def validate_targets(self, input_data):
        return None

    async def verify(self, input_data, output):
        return True

    async def execute(self, input_data):
        self.calls += 1
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


class FakeMalformedTool:
    name = "malformed_meta"
    description = "malformed"
    input_schema = FakeMutatingInput

    def __init__(self):
        self.calls = 0

    def get_policy_requirements(self):
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


@pytest.fixture
def db_session():
    from backend.models.database import Base

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


def _runtime(db_session, executor, allowed):
    return SpecialistRuntime(
        db=db_session,
        agent_id="spec-test",
        delegation_id="del-test",
        allowed_tools=allowed,
        task_executor=executor,
        task_id="task-test",
    )


@pytest.mark.asyncio
async def test_specialist_does_not_execute_state_changing_tool(db_session):
    executor = ToolExecutor()
    mutating_tool = FakeMutatingTool()
    executor.register_tool(mutating_tool)

    runtime = _runtime(db_session, executor, ["state_changing"])
    result = await runtime.request_action("state_changing", {})
    assert mutating_tool.calls == 0
    assert not bool(result.success)


@pytest.mark.asyncio
async def test_specialist_does_not_execute_privacy_tool(db_session):
    executor = ToolExecutor()
    privacy_tool = FakePrivacyTool()
    executor.register_tool(privacy_tool)

    runtime = _runtime(db_session, executor, ["privacy_tool"])
    result = await runtime.request_action("privacy_tool", {})
    assert privacy_tool.calls == 0
    assert not bool(result.success)


@pytest.mark.asyncio
async def test_specialist_does_not_execute_malformed_metadata_tool(db_session):
    executor = ToolExecutor()
    malformed_tool = FakeMalformedTool()
    executor.register_tool(malformed_tool)

    runtime = _runtime(db_session, executor, ["malformed_meta"])
    result = await runtime.request_action("malformed_meta", {})
    assert malformed_tool.calls == 0
    assert not bool(result.success)


@pytest.mark.asyncio
async def test_specialist_does_not_execute_missing_metadata_tool(db_session):
    executor = ToolExecutor()
    missing_tool = MissingMetaTool()
    executor.register_tool(missing_tool)

    runtime = _runtime(db_session, executor, ["missing_meta"])
    result = await runtime.request_action("missing_meta", {})
    assert missing_tool.calls == 0
    assert not bool(result.success)


@pytest.mark.asyncio
async def test_specialist_read_only_succeeds(db_session):
    from backend.tools.system_tools import SystemTimeTool

    executor = ToolExecutor()
    read_tool = SystemTimeTool()
    executor.register_tool(read_tool)

    runtime = _runtime(db_session, executor, ["system_time"])
    result = await runtime.request_action("system_time", {})
    assert bool(result.success) is True