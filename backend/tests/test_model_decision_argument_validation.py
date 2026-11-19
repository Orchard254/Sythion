"""Model output is a proposal: tool arguments must satisfy declared schemas.

Fail-closed tests for the decision boundary. The raw model argument object
must never be forwarded to the execution path when it is malformed, ambiguous,
or names a tool the model is not allowed to call.
"""

import pytest
from pydantic import BaseModel, ConfigDict, Field

from backend.api.schemas import ToolOutput
from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError
from backend.tools.executor import BaseTool, ToolExecutor


class RequiredArgInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    value: str = Field(..., description="A required value")


class ReadOnlyRequiredArgTool(BaseTool[RequiredArgInput]):
    name = "schema_probe_readonly"
    description = "Read-only tool with a required argument"
    input_schema = RequiredArgInput

    def get_policy_requirements(self) -> dict:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "read_only": True,
            "mutating": False,
            "destructive": False,
        }

    async def execute(self, input_data: RequiredArgInput) -> ToolOutput:
        return ToolOutput(success=True, output={"value": input_data.value}, duration_ms=0)


@pytest.fixture
def engine(test_db):
    executor = ToolExecutor()
    executor.register_tool(ReadOnlyRequiredArgTool())
    return ModelDecisionEngine(test_db, executor)


def _response(name, arguments):
    return {
        "choices": [{"message": {"tool_calls": [
            {"function": {"name": name, "arguments": arguments}},
        ]}}]
    }


def _code(exc):
    return exc.value.code


def test_valid_arguments_are_normalized_not_forwarded_raw(engine):
    kind, params = engine._parse_decision(_response(
        "complete_task", '{"result":"done"}'))
    assert kind == "complete_task"
    assert params == {"result": "done"}


def test_non_object_arguments_are_rejected(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response("complete_task", '"just text"'))
    assert _code(exc) == "INVALID_ARGUMENTS"


def test_missing_required_field_is_rejected(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response("complete_task", '{}'))
    assert _code(exc) == "INVALID_ARGUMENTS"


def test_extra_field_is_rejected(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response(
            "complete_task", '{"result":"done","approved":true}'))
    assert _code(exc) == "INVALID_ARGUMENTS"


def test_duplicate_json_keys_are_rejected(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response(
            "complete_task", '{"result":"first","result":"second"}'))
    assert _code(exc) == "INVALID_JSON"


def test_hidden_tool_is_rejected_even_as_a_second_call(engine):
    response = {
        "choices": [{"message": {"tool_calls": [
            {"function": {"name": "complete_task", "arguments": '{"result":"done"}'}},
            {"function": {"name": "file_write", "arguments": '{}'}},
        ]}}]
    }
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(response)
    assert _code(exc) == "INVALID_DECISION_TYPE"
    assert engine.pop_pending_tool_calls() == []


def test_execute_tool_wrapper_rejects_invalid_inner_parameters(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response("execute_tool", (
            '{"tool_name":"schema_probe_readonly","params":{}}'
        )))
    assert _code(exc) == "INVALID_ARGUMENTS"


def test_execute_tool_wrapper_accepts_valid_inner_parameters(engine):
    kind, params = engine._parse_decision(_response("execute_tool", (
        '{"tool_name":"schema_probe_readonly","params":{"value":"ok"}}'
    )))
    assert kind == "execute_tool"
    assert params["tool_name"] == "schema_probe_readonly"
    assert params["params"] == {"value": "ok"}


def test_direct_visible_tool_call_is_schema_validated(engine):
    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_response("schema_probe_readonly", '{}'))
    assert _code(exc) == "INVALID_ARGUMENTS"
