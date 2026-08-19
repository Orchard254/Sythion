"""
Tests for the WindowListTool.

Contract: lists all open windows with titles, handles, and visibility state.
Uses win32gui on Windows, falls back to empty list on other platforms.
"""

import sys
import pytest
from unittest.mock import MagicMock

from backend.tools.system_tools import WindowListTool
from backend.api.schemas import WindowListInput, ToolOutput


@pytest.fixture
def mock_win32(monkeypatch):
    """Provide a mock win32gui and win32process environment for deterministic testing."""
    mock_gui = MagicMock()
    mock_proc = MagicMock()

    # Window table: (hwnd, title, is_visible, is_minimized, pid)
    fake_windows = [
        (1001, "Main Editor - VS Code", True, False, 5001),
        (1002, "Terminal - PowerShell", True, True, 5002),
        (1003, "Background Worker", False, False, 5003),
        (1004, "", True, False, 5004),  # Empty title, should be skipped
    ]

    def fake_enum_windows(callback, extra):
        for hwnd, title, is_vis, is_min, pid in fake_windows:
            callback(hwnd, extra)

    def fake_is_window_visible(hwnd):
        for h, _, is_vis, _, _ in fake_windows:
            if h == hwnd:
                return is_vis
        return False

    def fake_is_iconic(hwnd):
        for h, _, _, is_min, _ in fake_windows:
            if h == hwnd:
                return is_min
        return False

    def fake_get_window_text(hwnd):
        for h, title, _, _, _ in fake_windows:
            if h == hwnd:
                return title
        return ""

    def fake_get_thread_proc_id(hwnd):
        for h, _, _, _, pid in fake_windows:
            if h == hwnd:
                return (0, pid)
        return (0, 0)

    mock_gui.EnumWindows.side_effect = fake_enum_windows
    mock_gui.IsWindowVisible.side_effect = fake_is_window_visible
    mock_gui.IsIconic.side_effect = fake_is_iconic
    mock_gui.GetWindowText.side_effect = fake_get_window_text
    mock_proc.GetWindowThreadProcessId.side_effect = fake_get_thread_proc_id

    monkeypatch.setitem(sys.modules, "win32gui", mock_gui)
    monkeypatch.setitem(sys.modules, "win32process", mock_proc)
    return mock_gui


@pytest.fixture
def window_tool():
    return WindowListTool()


class TestWindowListExecute:
    def test_returns_windows(self, window_tool, mock_win32):
        """Should return a list of windows."""
        import asyncio
        input_data = WindowListInput()
        output = asyncio.run(window_tool.execute(input_data))

        assert output.success is True
        assert "windows" in output.data
        assert "count" in output.data
        assert output.data["count"] > 0

    def test_windows_have_required_fields(self, window_tool, mock_win32):
        """Each window should have hwnd, title, pid, process_name, is_visible, is_minimized."""
        import asyncio
        input_data = WindowListInput()
        output = asyncio.run(window_tool.execute(input_data))

        assert len(output.data["windows"]) > 0
        for window in output.data["windows"]:
            assert "hwnd" in window
            assert "title" in window
            assert "pid" in window
            assert "process_name" in window
            assert "is_visible" in window
            assert "is_minimized" in window


    def test_visible_only_filter(self, window_tool, mock_win32):
        """When visible_only=True, only visible windows should be returned."""
        import asyncio
        input_data = WindowListInput(visible_only=True, include_minimized=True)
        output = asyncio.run(window_tool.execute(input_data))

        assert output.data["count"] > 0
        for window in output.data["windows"]:
            assert window["is_visible"] is True

    def test_include_minimized(self, window_tool, mock_win32):
        """When include_minimized=True, minimized windows should be included."""
        import asyncio
        input_data = WindowListInput(include_minimized=True, visible_only=False)
        output = asyncio.run(window_tool.execute(input_data))

        assert output.success is True
        minimized_found = any(w["is_minimized"] for w in output.data["windows"])
        assert minimized_found is True

    def test_exclude_minimized(self, window_tool, mock_win32):
        """When include_minimized=False, minimized windows should be excluded."""
        import asyncio
        input_data = WindowListInput(include_minimized=False, visible_only=False)
        output = asyncio.run(window_tool.execute(input_data))

        assert output.data["count"] > 0
        for window in output.data["windows"]:
            assert window["is_minimized"] is False

    def test_verify_returns_true(self, window_tool, mock_win32):
        """Verify should return True for successful output."""
        import asyncio
        input_data = WindowListInput()
        output = asyncio.run(window_tool.execute(input_data))

        assert asyncio.run(window_tool.verify(input_data, output)) is True

    def test_get_policy_requirements(self, window_tool):
        """Should return read-only policy."""
        reqs = window_tool.get_policy_requirements()
        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False
        assert reqs["read_only"] is True

