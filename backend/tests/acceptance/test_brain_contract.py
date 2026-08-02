"""
Acceptance gate: model brain contract (Phase B).

The approved contract (JARVIS_FINAL_HANDOFF_BRIEF.md:117-138) requires:
- Only a policy-filtered subset of read-only tools are model-visible, exposed
  directly (no execute_tool wrapper).
- Control functions are exactly: request_user_input, run_skill, complete_task,
  fail_task.
- Tool output has a bounded redacted model_summary view for the model.
- Model context includes the current durable task state.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine
from backend.tools.executor import ToolExecutor
from backend.tools.system_tools import create_system_tools
from backend.tools.file_tools import create_file_tools
from backend.tools.app_tools import create_app_tools
from backend.tools.package_tools import create_package_tools


@pytest.fixture
def full_tool_executor():
    ex = ToolExecutor()
    for tool in (
        list(create_system_tools())
        + list(create_file_tools())
        + list(create_app_tools())
        + list(create_package_tools())
    ):
        ex.register_tool(tool)
    return ex


def test_no_execute_tool_wrapper_in_decision_set(db_session, full_tool_executor):
    """The model may never see an execute_tool indirection wrapper."""
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    decision_names = {t["name"] for t in engine._decision_tools()}
    assert decision_names == {
        "complete_task",
        "fail_task",
        "request_user_input",
        "run_skill",
    }


def test_model_visible_tools_are_read_only_subset(db_session, full_tool_executor):
    """Only policy-filtered read-only tools reach the model; mutating and
    confirmation-required tools are excluded."""
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    visible = {t["name"] for t in engine._model_visible_tools()}

    # Read-only system tools are visible.
    assert "system_time" in visible
    assert "system_battery" in visible

    # Mutating file/app/package tools are never model-visible.
    assert "file_write" not in visible
    assert "file_delete" not in visible
    assert "app_launch" not in visible
    assert "app_close" not in visible
    assert "package_install" not in visible
    assert "package_uninstall" not in visible

    # No raw implementation or artifact leaks into the visible metadata.
    for t in engine._model_visible_tools():
        assert "input_schema" not in t["description"]
        assert len(t["description"]) <= 200


def test_system_prompt_teaches_direct_tool_calls(db_session, full_tool_executor):
    """The prompt must instruct direct tool calls and list no wrapper."""
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    prompt = engine._build_system_prompt()
    assert "execute_tool" not in prompt
    assert "call one directly" in prompt
    assert "system_time" in prompt


def test_context_uses_current_state(db_session, full_tool_executor):
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    messages = engine._build_context(
        task_id="t-1",
        original_request="do the thing",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )
    assert "Current State: running" in messages[1]["content"]


def test_context_prefers_bounded_model_summary(db_session, full_tool_executor):
    """The model sees the bounded model_summary, never the raw full output."""
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    history = [{
        "event_type": "tool.executed",
        "event_data": {
            "tool_name": "system_time",
            "success": True,
            "output": {"leak": "x" * 1000},
            "model_summary": "time is now",
        },
        "created_at": "2026-01-01T00:00:00",
    }]
    messages = engine._build_context(
        task_id="t-1",
        original_request="what time",
        normalized_goal=None,
        event_history=history,
        current_state="running",
    )
    content = messages[1]["content"]
    assert "time is now" in content
    assert ("x" * 1000) not in content


def test_decision_input_schemas_are_pydantic(db_session, full_tool_executor):
    """Every control function has a Pydantic input schema (per brief)."""
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    for tool in engine._decision_tools():
        schema = tool["input_schema"]
        assert hasattr(schema, "model_fields")
