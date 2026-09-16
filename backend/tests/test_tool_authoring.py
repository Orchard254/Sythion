"""
Tests for the L3 tool-authoring pipeline.

Contract (Phase 0 protected runtime contract): model-authored tool proposals
are validated with STATIC analysis only — the implementation source is never
compiled or executed in this process. Proposals are stored as inert review
artifacts; owner approval records a decision, never a live executable.
"""

import json
import pytest

from backend.services.tool_authoring import (
    ToolProposal,
    validate_tool_name,
    validate_description,
    validate_input_schema,
    validate_implementation,
    validate_tool_proposal,
    static_interface_check,
)
from backend.models.database import ProposedTool


# --- Validation tests -------------------------------------------------------

class TestToolNameValidation:
    def test_valid_name(self):
        assert validate_tool_name("my_cool_tool") == []

    def test_rejects_uppercase(self):
        errors = validate_tool_name("MyTool")
        assert len(errors) == 1
        assert "must match" in errors[0]

    def test_rejects_starts_with_digit(self):
        errors = validate_tool_name("1tool")
        assert len(errors) == 1

    def test_rejects_collision_with_builtin(self):
        errors = validate_tool_name("system_time")
        assert len(errors) == 1
        assert "collides" in errors[0]

    def test_rejects_too_long(self):
        errors = validate_tool_name("a" * 65)
        assert len(errors) == 1


class TestDescriptionValidation:
    def test_valid_description(self):
        assert validate_description("A tool that reads files") == []

    def test_rejects_too_long(self):
        errors = validate_description("x" * 501)
        assert len(errors) == 1
        assert "500" in errors[0]

    def test_rejects_secret_hints(self):
        errors = validate_description("A tool that uses api_key")
        assert len(errors) == 1
        assert "api_key" in errors[0]


class TestInputSchemaValidation:
    def test_valid_schema(self):
        schema = {
            "type": "object",
            "properties": {
                "path": {"type": "string", "description": "File path"},
            },
            "required": ["path"],
        }
        assert validate_input_schema(schema) == []

    def test_rejects_coordinate_keys(self):
        schema = {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
            },
        }
        errors = validate_input_schema(schema)
        assert len(errors) == 1
        assert "coordinate" in errors[0]

    def test_rejects_secret_keys(self):
        schema = {
            "type": "object",
            "properties": {
                "api_key": {"type": "string"},
            },
        }
        errors = validate_input_schema(schema)
        assert len(errors) == 1
        assert "secret" in errors[0]


class TestImplementationValidation:
    def test_valid_implementation(self):
        impl = '''
from backend.tools.executor import BaseTool
from backend.api.schemas import ToolOutput

class MyTool(BaseTool):
    name = "my_tool"
    description = "A test tool"
    input_schema = None

    async def execute(self, input_data):
        return ToolOutput(success=True, data={})

    def get_policy_requirements(self):
        return {"risk_level": "none"}
'''
        errors = validate_implementation(impl)
        assert errors == []

    def test_rejects_forbidden_import(self):
        impl = '''
import subprocess

class MyTool:
    name = "my_tool"
    description = "A test tool"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
'''
        errors = validate_implementation(impl)
        assert any("Forbidden import" in e for e in errors)

    def test_rejects_eval(self):
        impl = '''
class MyTool:
    name = "my_tool"
    description = "A test tool"
    input_schema = None

    async def execute(self, input_data):
        eval("print('hack')")
        return None

    def get_policy_requirements(self):
        return {}
'''
        errors = validate_implementation(impl)
        assert any("eval(" in e for e in errors)

    def test_rejects_syntax_error(self):
        impl = "def broken("
        errors = validate_implementation(impl)
        assert any("syntax error" in e for e in errors)

    def test_rejects_no_class(self):
        impl = '''
def my_tool():
    pass
'''
        errors = validate_implementation(impl)
        assert any("must define a class" in e for e in errors)

    def test_rejects_no_execute(self):
        impl = '''
class MyTool:
    name = "my_tool"
    description = "A test tool"
    input_schema = None

    def get_policy_requirements(self):
        return {}
'''
        errors = validate_implementation(impl)
        assert any("execute" in e for e in errors)

    def test_rejects_no_policy(self):
        impl = '''
class MyTool:
    name = "my_tool"
    description = "A test tool"
    input_schema = None

    async def execute(self, input_data):
        return None
'''
        errors = validate_implementation(impl)
        assert any("get_policy_requirements" in e for e in errors)


class TestProposalValidation:
    def test_full_validation(self):
        proposal = ToolProposal(
            name="my_tool",
            description="A tool that does something useful",
            input_schema={
                "type": "object",
                "properties": {"path": {"type": "string"}},
            },
            implementation='''
from backend.tools.executor import BaseTool
from backend.api.schemas import ToolOutput

class MyTool(BaseTool):
    name = "my_tool"
    description = "A tool that does something useful"
    input_schema = None

    async def execute(self, input_data):
        return ToolOutput(success=True, data={})

    def get_policy_requirements(self):
        return {"risk_level": "none"}
''',
        )
        result = validate_tool_proposal(proposal)
        assert result.valid is True
        assert result.errors == []


# --- Static interface checks (never execute the implementation) --------------

class TestStaticInterfaceCheck:
    def test_valid_tool_passes(self):
        proposal = ToolProposal(
            name="my_tool",
            description="A valid tool",
            input_schema={"type": "object", "properties": {}},
            implementation='''
from backend.tools.executor import BaseTool

class MyTool(BaseTool):
    name = "my_tool"
    description = "A valid tool"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {"risk_level": "none"}
''',
        )
        result = static_interface_check(proposal)
        assert result.passed is True
        assert "NOT executed" in result.detail

    def test_syntax_error_fails_without_executing(self):
        proposal = ToolProposal(
            name="broken",
            description="Broken tool",
            input_schema={"type": "object", "properties": {}},
            implementation="def broken(",
        )
        result = static_interface_check(proposal)
        assert result.passed is False
        assert any("syntax error" in e for e in result.errors)

    def test_no_class_fails(self):
        proposal = ToolProposal(
            name="noclass",
            description="No class tool",
            input_schema={"type": "object", "properties": {}},
            implementation="def my_tool(): pass",
        )
        result = static_interface_check(proposal)
        assert result.passed is False
        assert any("class" in e.lower() for e in result.errors)

    def test_top_level_raise_is_never_triggered(self):
        """Code that would crash on execution stays inert during checking."""
        proposal = ToolProposal(
            name="crasher",
            description="Crashes if ever executed",
            input_schema={"type": "object", "properties": {}},
            implementation='''
raise RuntimeError("I must never run")

from backend.tools.executor import BaseTool

class Crasher(BaseTool):
    name = "crasher"
    description = "Crashes"
    input_schema = None

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
''',
        )
        # Static analysis completes; nothing raised.
        result = static_interface_check(proposal)
        assert isinstance(result.passed, bool)

    def test_missing_interface_attrs_reported_statically(self):
        proposal = ToolProposal(
            name="incomplete",
            description="Incomplete tool",
            input_schema={"type": "object", "properties": {}},
            implementation='''
from backend.tools.executor import BaseTool

class Incomplete(BaseTool):
    name = "incomplete"

    async def execute(self, input_data):
        return None

    def get_policy_requirements(self):
        return {}
''',
        )
        result = static_interface_check(proposal)
        assert result.passed is False
        joined = "; ".join(result.errors)
        assert "description" in joined or "input_schema" in joined
