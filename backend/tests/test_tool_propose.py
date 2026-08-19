"""
Tests for the ToolProposeTool (L3 tool proposal endpoint).

Contract: the tool_propose tool must validate, sandbox-test, and store
tool proposals. Owner approval is required before the tool becomes live.
"""

import json
import pytest
from pathlib import Path

from backend.tools.system_tools import ToolProposeTool
from backend.api.schemas import ToolProposeInput, ToolOutput
from backend.models.database import set_db_manager, get_db_manager, ProposedTool
from backend.core.db_manager import DatabaseManager


@pytest.fixture
def db_setup(tmp_path):
    """Set up an in-memory database for testing."""
    db_path = str(tmp_path / "test_propose.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    yield db_mgr
    set_db_manager(None)


@pytest.fixture
def propose_tool(db_setup):
    return ToolProposeTool()


VALID_IMPL = '''
from backend.tools.executor import BaseTool
from backend.api.schemas import ToolOutput

class TestCounter(BaseTool):
    name = "test_counter"
    description = "A simple counter tool"
    input_schema = None

    async def execute(self, input_data):
        return ToolOutput(success=True, data={"count": 0})

    def get_policy_requirements(self):
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}
'''


class TestToolProposeExecute:
    def test_valid_proposal_passes(self, propose_tool):
        """A valid tool proposal should pass validation and sandbox."""
        input_data = ToolProposeInput(
            name="test_counter",
            description="A simple counter tool",
            input_schema={
                "type": "object",
                "properties": {
                    "start": {"type": "integer", "description": "Starting value"},
                },
            },
            implementation=VALID_IMPL,
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is True
        assert output.data is not None
        assert "proposal_id" in output.data
        assert output.data["name"] == "test_counter"
        assert output.data["status"] in ("sandbox_passed", "proposed")

    def test_forbidden_import_rejected(self, propose_tool):
        """A tool with forbidden imports should fail validation."""
        input_data = ToolProposeInput(
            name="bad_tool",
            description="A tool that uses subprocess",
            input_schema={"type": "object", "properties": {}},
            implementation='''
import subprocess

class BadTool:
    name = "bad_tool"
    description = "Bad"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
''',
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is False
        assert output.error is not None
        assert "VALIDATION_FAILED" in output.error.get("code", "")

    def test_forbidden_pattern_rejected(self, propose_tool):
        """A tool with eval() should fail validation."""
        input_data = ToolProposeInput(
            name="eval_tool",
            description="Uses eval",
            input_schema={"type": "object", "properties": {}},
            implementation='''
class EvalTool:
    name = "eval_tool"
    description = "Bad"
    input_schema = None

    async def execute(self, input_data):
        eval("print('hack')")
        return None

    def get_policy_requirements(self):
        return {}
''',
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is False
        assert "VALIDATION_FAILED" in output.error.get("code", "")

    def test_crash_in_init_fails_sandbox(self, propose_tool):
        """A tool that crashes on __init__ should fail sandbox test."""
        input_data = ToolProposeInput(
            name="crasher",
            description="Crashes on init",
            input_schema={"type": "object", "properties": {}},
            implementation='''
from backend.tools.executor import BaseTool

class Crasher(BaseTool):
    name = "crasher"
    description = "Crashes"
    input_schema = None

    def __init__(self):
        raise RuntimeError("I crash on init")

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
''',
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is True
        assert output.data["status"] == "proposed"
        sandbox = output.data.get("sandbox", {})
        assert sandbox.get("passed") is False

    def test_collision_with_builtin_rejected(self, propose_tool):
        """A tool with a name that collides with a built-in should fail validation."""
        input_data = ToolProposeInput(
            name="system_time",
            description="Collides with built-in",
            input_schema={"type": "object", "properties": {}},
            implementation=VALID_IMPL.replace("test_counter", "system_time"),
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is False
        assert "VALIDATION_FAILED" in output.error.get("code", "")

    def test_secret_in_description_rejected(self, propose_tool):
        """A tool with secret hints in description should fail validation."""
        input_data = ToolProposeInput(
            name="secret_tool",
            description="Uses api_key to authenticate",
            input_schema={"type": "object", "properties": {}},
            implementation=VALID_IMPL.replace("test_counter", "secret_tool"),
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is False
        assert "VALIDATION_FAILED" in output.error.get("code", "")

    def test_proposal_stored_in_database(self, propose_tool):
        """A valid proposal should be stored in the database."""
        input_data = ToolProposeInput(
            name="db_test_tool",
            description="Tests database storage",
            input_schema={"type": "object", "properties": {}},
            implementation=VALID_IMPL.replace("test_counter", "db_test_tool"),
        )
        import asyncio
        output = asyncio.run(propose_tool.execute(input_data))

        assert output.success is True
        proposal_id = output.data["proposal_id"]

        db_mgr = get_db_manager()
        with db_mgr.get_session() as session:
            stored = session.query(ProposedTool).filter_by(id=proposal_id).first()
            assert stored is not None
            assert stored.name == "db_test_tool"
            assert stored.description == "Tests database storage"
            assert stored.status in ("sandbox_passed", "proposed")
