"""
Tests for BrowserTabsTool.

Contract: discovers and lists open browser tabs across Chrome, Edge, Brave, and Firefox
with their titles, indices, active status, and parent window metadata.
"""

import sys
import asyncio
import pytest
from unittest.mock import MagicMock

import backend.tools.system_tools as system_tools
from backend.tools.system_tools import BrowserTabsTool
from backend.api.schemas import BrowserTabsInput, ToolOutput


@pytest.fixture
def mock_browser_environment(monkeypatch):
    """Provide a mock win32gui, win32process, and psutil environment."""
    mock_gui = MagicMock()
    mock_proc = MagicMock()

    # Browser window table: (hwnd, title, is_visible)
    fake_windows = [
        (2001, "Google Antigravity Auth Success - Google Chrome", True),
        (2002, "Microsoft Edge - Home", True),
        (2003, "Notepad", True),
    ]

    def fake_enum_windows(callback, extra):
        for hwnd, title, is_vis in fake_windows:
            callback(hwnd, extra)

    def fake_is_window_visible(hwnd):
        for h, _, is_vis in fake_windows:
            if h == hwnd:
                return is_vis
        return False

    def fake_get_window_text(hwnd):
        for h, title, _ in fake_windows:
            if h == hwnd:
                return title
        return ""

    def fake_get_thread_proc_id(hwnd):
        if hwnd == 2001:
            return (0, 7001)  # chrome.exe
        if hwnd == 2002:
            return (0, 7002)  # msedge.exe
        return (0, 7003)

    mock_gui.EnumWindows.side_effect = fake_enum_windows
    mock_gui.IsWindowVisible.side_effect = fake_is_window_visible
    mock_gui.GetWindowText.side_effect = fake_get_window_text
    mock_proc.GetWindowThreadProcessId.side_effect = fake_get_thread_proc_id

    monkeypatch.setitem(sys.modules, "win32gui", mock_gui)
    monkeypatch.setitem(sys.modules, "win32process", mock_proc)

    class FakeProcess:
        def __init__(self, pid):
            self.pid = pid
        def name(self):
            if self.pid == 7001:
                return "chrome.exe"
            if self.pid == 7002:
                return "msedge.exe"
            return "notepad.exe"

    monkeypatch.setattr(system_tools.psutil, "Process", FakeProcess)
    return mock_gui


@pytest.fixture
def browser_tool():
    return BrowserTabsTool()


class TestBrowserTabsTool:
    def test_browser_tabs_policy_requirements(self, browser_tool):
        """Should be safe, read-only with no confirmation required."""
        reqs = browser_tool.get_policy_requirements()
        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False
        assert reqs["read_only"] is True

    def test_browser_tabs_discovery(self, browser_tool, mock_browser_environment):
        """Should return discovered browser tabs and window metadata."""
        input_data = BrowserTabsInput(browser="all")
        output = asyncio.run(browser_tool.execute(input_data))

        assert output.success is True
        assert "tabs" in output.data
        assert "count" in output.data
        assert output.data["count"] >= 2
        assert any("chrome" in t.get("browser", "").lower() for t in output.data["tabs"])

    def test_browser_tabs_filter_by_browser(self, browser_tool, mock_browser_environment):
        """Should filter by target browser name."""
        input_data = BrowserTabsInput(browser="chrome")
        output = asyncio.run(browser_tool.execute(input_data))

        assert output.success is True
        assert output.data["count"] > 0
        for tab in output.data["tabs"]:
            assert "chrome" in tab.get("browser", "").lower()

    def test_browser_tabs_no_matching_browser(self, browser_tool, mock_browser_environment):
        """Should return empty tabs list when filtered browser is not open."""
        input_data = BrowserTabsInput(browser="firefox")
        output = asyncio.run(browser_tool.execute(input_data))

        assert output.success is True
        assert output.data["count"] == 0
        assert output.data["tabs"] == []

    def test_verify_returns_true(self, browser_tool, mock_browser_environment):
        """Verify contract should return True on valid output."""
        input_data = BrowserTabsInput()
        output = asyncio.run(browser_tool.execute(input_data))

        assert asyncio.run(browser_tool.verify(input_data, output)) is True
