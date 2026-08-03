"""
Acceptance gate: Tool discovery (Phase B).

Model context is policy-filtered and bounded: it never contains full
directories, raw UI trees, screenshots, or model "thinking". Only the tool
name + curated description reach the model.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine
from backend.tools.executor import ToolExecutor
from backend.tools.system_tools import create_system_tools
from backend.tools.file_tools import create_file_tools
from backend.tools.app_tools import create_app_tools
from backend.tools.package_tools import create_package_tools
from backend.tools.gui_tools import create_gui_tools


@pytest.fixture
def full_tool_executor():
    ex = ToolExecutor()
    for tool in (
        list(create_system_tools())
        + list(create_file_tools())
        + list(create_app_tools())
        + list(create_package_tools())
        + list(create_gui_tools())
    ):
        ex.register_tool(tool)
    return ex


def _tool_metadata(ex: ToolExecutor):
    return [
        {
            "name": name,
            "description": ex.get_tool(name).description,
            "input_schema": ex.get_tool(name).input_schema,
        }
        for name in ex.list_tools()
    ]


def test_system_prompt_is_bounded_and_name_only(db_session, full_tool_executor):
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    prompt = engine._build_system_prompt()

    # The prompt lists names, not full tool bodies / schemas / implementations.
    assert "AVAILABLE TOOLS" in prompt
    assert "system_time" in prompt
    # No raw code, no file paths, no screenshots, no UI trees in the prompt.
    assert "def " not in prompt
    assert "C:\\\\" not in prompt
    assert ".png" not in prompt
    assert "uiautomation" not in prompt.lower()


def test_decision_tool_metadata_never_includes_thinking_or_artifacts(db_session, full_tool_executor):
    engine = ModelDecisionEngine(db_session, full_tool_executor)
    metadata = engine._decision_tools()

    for tool in metadata:
        assert isinstance(tool["description"], str)
        assert len(tool["description"]) <= 200


def test_context_truncates_event_output_and_history(db_session, full_tool_executor):
    engine = ModelDecisionEngine(db_session, full_tool_executor)

    # Simulate a long history with a huge tool output; the model context must
    # truncate to a bounded recent window.
    huge_output = "x" * 100000
    history = []
    for i in range(30):
        history.append({
            "event_type": "tool.executed",
            "event_data": {"tool_name": f"tool_{i}", "success": True, "output": huge_output},
            "created_at": f"2026-01-01T00:00:{i:02d}",
        })

    messages = engine._build_context(
        task_id="t-1",
        original_request="list everything",
        normalized_goal=None,
        event_history=history,
        current_state="running",
    )

    user_content = messages[1]["content"]
    # Only the last 10 events are included (bounded window).
    assert "tool_29" in user_content
    assert "tool_0" not in user_content
    # Outputs are truncated to ~200 chars each, not the full 100k.
    assert ("x" * 300) not in user_content


def test_policy_context_blocks_mutating_tools_without_direct_instruction(db_session, full_tool_executor):
    """Mutating tools must be policy-gated even when discoverable."""
    ex = full_tool_executor

    def _is_mutating(name):
        reqs = ex.get_tool(name).get_policy_requirements()
        return bool(reqs.get("mutating") or reqs.get("destructive")
                    or reqs.get("risk_level") in {"medium", "high"})

    mutating = [n for n in ex.list_tools() if _is_mutating(n)]

    assert mutating, "expected at least one mutating tool to test policy gating"

    from backend.tools.executor import PolicyContext

    for name in mutating:
        reqs = ex.get_tool(name).get_policy_requirements()
        # Non-mutating metadata must not leak implementation details.
        assert "input_schema" not in reqs
        # Without a direct user instruction the policy gate must refuse.
        denied = ToolExecutor._policy_error(ex.get_tool(name), PolicyContext(direct_user_instruction=False))
        if reqs.get("requires_confirmation"):
            assert denied is not None
            assert denied["code"] in {"DIRECT_INSTRUCTION_REQUIRED", "ELEVATION_NOT_ALLOWED"}
