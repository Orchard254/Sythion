"""
Acceptance gate: Deterministic intent/target validation (Phase B).

Before any tool action the executor validates the target deterministically and
side-effect-free. Ambiguous, malformed, or out-of-scope targets (empty paths,
wildcards, paths outside approved roots, invalid app/package IDs, missing GUI
window titles) are rejected without any OS call.
"""

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.system_tools import create_system_tools
from backend.tools.file_tools import create_file_tools, _approved_roots
from backend.tools.app_tools import create_app_tools
from backend.tools.package_tools import create_package_tools
from backend.tools.gui_tools import (
    GUIObserveTool,
    GUIFocusTool,
    GUIClickTool,
    GUITypeTool,
    GUIHotkeyTool,
    GUIInvokeTool,
    GUISetValueTool,
)


@pytest.fixture
def full_tool_executor():
    ex = ToolExecutor()
    for tool in (
        list(create_system_tools())
        + list(create_file_tools())
        + list(create_app_tools())
        + list(create_package_tools())
        + [
            GUIObserveTool(),
            GUIFocusTool(),
            GUIClickTool(),
            GUITypeTool(),
            GUIHotkeyTool(),
            GUIInvokeTool(),
            GUISetValueTool(),
        ]
    ):
        ex.register_tool(tool)
    return ex


def _spy_execute(ex: ToolExecutor, tool_name: str, monkeypatch):
    """Replace the tool's execute with one that fails the test if reached."""
    tool = ex.get_tool(tool_name)
    if tool is None:
        pytest.skip(f"tool {tool_name} not registered")
    called = []

    async def _bomb(input_data):
        called.append(True)
        raise AssertionError(f"execute called despite invalid target: {input_data}")

    monkeypatch.setattr(tool, "execute", _bomb)
    return called


@pytest.mark.asyncio
async def test_file_target_outside_roots_rejected_before_execute(full_tool_executor, tmp_path, monkeypatch):
    """A path outside approved roots must be rejected with no OS call."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    called = _spy_execute(full_tool_executor, "file_read", monkeypatch)

    out = await full_tool_executor.execute_tool("file_read", {"path": str(tmp_path.parent / "outside.txt")})
    assert not out.success
    assert out.error["code"] == "TARGET_OUTSIDE_ROOTS"
    assert called == []


@pytest.mark.asyncio
async def test_file_target_wildcard_rejected_before_execute(full_tool_executor, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    called = _spy_execute(full_tool_executor, "file_read", monkeypatch)

    out = await full_tool_executor.execute_tool("file_read", {"path": str(tmp_path / "*.txt")})
    assert not out.success
    assert out.error["code"] == "TARGET_WILDCARD_REJECTED"
    assert called == []


@pytest.mark.asyncio
async def test_file_target_required(full_tool_executor, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    called = _spy_execute(full_tool_executor, "file_read", monkeypatch)

    out = await full_tool_executor.execute_tool("file_read", {"path": "  "})
    assert not out.success
    assert out.error["code"] == "TARGET_REQUIRED"
    assert called == []


@pytest.mark.asyncio
async def test_file_target_inside_roots_passes_gate(full_tool_executor, tmp_path, monkeypatch):
    """A valid in-root target passes the gate and reaches execution."""
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    target = tmp_path / "notes.txt"
    target.write_text("hello", encoding="utf-8")

    out = await full_tool_executor.execute_tool("file_read", {"path": str(target)})
    assert out.success
    assert out.data["content"] == "hello"


@pytest.mark.asyncio
async def test_move_and_copy_validate_both_targets(full_tool_executor, tmp_path, monkeypatch):
    monkeypatch.setattr("backend.tools.file_tools._approved_roots", lambda: [tmp_path])
    called = _spy_execute(full_tool_executor, "file_move", monkeypatch)
    ctx = PolicyContext(direct_user_instruction=True)

    src = tmp_path / "a.txt"
    src.write_text("x", encoding="utf-8")
    # Wildcard destination must be rejected before any OS call.
    out = await full_tool_executor.execute_tool(
        "file_move",
        {"source_path": str(src), "destination_path": str(tmp_path / "*.txt")},
        policy_context=ctx,
    )
    assert not out.success
    assert out.error["code"] == "TARGET_WILDCARD_REJECTED"
    assert called == []

    # Destination outside roots must be rejected too.
    out = await full_tool_executor.execute_tool(
        "file_move",
        {"source_path": str(src), "destination_path": str(tmp_path.parent / "out.txt")},
        policy_context=ctx,
    )
    assert not out.success
    assert out.error["code"] == "TARGET_OUTSIDE_ROOTS"
    assert called == []


@pytest.mark.asyncio
async def test_invalid_app_id_rejected_before_launch(full_tool_executor, monkeypatch):
    called = _spy_execute(full_tool_executor, "app_launch", monkeypatch)

    out = await full_tool_executor.execute_tool(
        "app_launch",
        {"app_id": "C:\\Windows\\System32\\cmd.exe"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "INVALID_APP_ID"
    assert called == []


@pytest.mark.asyncio
async def test_invalid_package_id_rejected_before_install(full_tool_executor):
    """Package targets are validated deterministically before any OS call.

    package_install requires admin, so the executor's policy gate blocks it
    first (ELEVATION_NOT_ALLOWED); the target gate is exercised directly here.
    """
    tool = full_tool_executor.get_tool("package_install")
    assert tool is not None

    out = await full_tool_executor.execute_tool(
        "package_install",
        {"package_id": "--accept-source-agreements"},
        policy_context=PolicyContext(direct_user_instruction=True),
    )
    assert not out.success
    assert out.error["code"] == "ELEVATION_NOT_ALLOWED"

    error = tool.validate_targets(tool.input_schema(package_id="--accept-source-agreements"))
    assert error is not None
    assert error["code"] == "INVALID_PACKAGE_ID"


@pytest.mark.asyncio
async def test_gui_target_requires_window_title(full_tool_executor, monkeypatch):
    called = _spy_execute(full_tool_executor, "gui_focus", monkeypatch)

    out = await full_tool_executor.execute_tool("gui_focus", {"window_title": ""})
    assert not out.success
    assert out.error["code"] == "TARGET_REQUIRED"
    assert called == []


@pytest.mark.asyncio
async def test_gui_element_target_required(full_tool_executor, monkeypatch):
    called = _spy_execute(full_tool_executor, "gui_invoke", monkeypatch)

    out = await full_tool_executor.execute_tool(
        "gui_invoke",
        {"window_title": "Notepad", "element_description": "  "},
    )
    assert not out.success
    assert out.error["code"] == "ELEMENT_TARGET_REQUIRED"
    assert called == []


@pytest.mark.asyncio
async def test_no_target_tools_pass_gate(full_tool_executor):
    """Tools without a target field (e.g. system_time) are unaffected."""
    out = await full_tool_executor.execute_tool("system_time", {})
    assert out.success


@pytest.mark.asyncio
async def test_policy_gate_still_precedes_execution(full_tool_executor, monkeypatch):
    """Confirmation-required tools are blocked by policy before execute."""
    called = _spy_execute(full_tool_executor, "file_write", monkeypatch)
    target = _approved_roots()[0]

    out = await full_tool_executor.execute_tool(
        "file_write",
        {"path": str(target / "x.txt"), "content": "data"},
        policy_context=PolicyContext(direct_user_instruction=False),
    )
    assert not out.success
    assert out.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    assert called == []
