"""
Tests for GUI tools (Phase D: Windows-only, dedicated MTA worker, bounded).

GUI tools run every UIA call on one dedicated COM MTA worker thread, gate on a
live desktop session, deny security surfaces / elevated targets, prefer UIA
patterns, and use single-shot physical-pixel input followed by re-observation.
Tests substitute a fake driver at ``tools.gui_tools._uia_driver`` while the real
MTA worker thread still handles dispatch.
"""
import asyncio
import ctypes
import threading

import pytest
from unittest.mock import Mock

from tools.gui_tools import (
    GUIObserveTool,
    GUIFocusTool,
    GUIClickTool,
    GUITypeTool,
    GUIHotkeyTool,
    GUIInvokeTool,
    GUISetValueTool,
    _UIAWorker,
    _desktop_gate,
    _target_window_safety,
    _hotkey_error,
    _is_elevated,
    _session_accepts_input,
)
from api.schemas import (
    GUIObserveInput,
    GUIFocusInput,
    GUIClickInput,
    GUITypeInput,
    GUIHotkeyInput,
    GUIInvokeInput,
    GUISetValueInput,
)


class FakeDriver:
    """Stand-in for the comtypes UIA driver. Records calls, returns canned data."""

    def __init__(self):
        self.calls = []

    def observe(self, title, depth, count):
        self.calls.append(("observe", title, depth, count))
        return {
            "found": True,
            "title": title,
            "hwnd": 0x1234,
            "pid": 999,
            "observed_at": 0.0,
            "ui_tree": {"name": title, "bounded": True},
        }

    def focus(self, title):
        self.calls.append(("focus", title))
        return {"ok": True, "window": {"hwnd": 0x1234, "title": title, "foreground": True}}

    def invoke(self, title, description):
        self.calls.append(("invoke", title, description))
        return {"ok": True, "method": "uia_invoke", "element": {"name": description, "rect": [0, 0, 10, 10]}}

    def set_value(self, title, description, value):
        self.calls.append(("set_value", title, description, value))
        return {"ok": True, "method": "uia_value", "element": {"name": description}}

    def single_click(self, title, x, y):
        self.calls.append(("single_click", title, x, y))
        return {"ok": True, "method": "coordinate_click", "point": {"x": x, "y": y},
                "window_rect": [0, 0, 100, 100], "reobserved": {"found": True}}

    def type_text(self, title, text):
        self.calls.append(("type_text", title, text))
        return {"ok": True, "method": "send_input_unicode", "typed": text, "reobserved": {"found": True}}

    def hotkey(self, title, keys):
        self.calls.append(("hotkey", title, list(keys)))
        return {"ok": True, "method": "send_input_hotkey", "keys": list(keys), "reobserved": {"found": True}}


@pytest.fixture
def gui_env(monkeypatch):
    """Deterministic GUI environment: fake driver, open desktop gate, safe target, foreground window."""
    fake = FakeDriver()
    monkeypatch.setattr("tools.gui_tools._uia_driver", fake)
    monkeypatch.setattr("tools.gui_tools._desktop_gate", lambda: None)
    monkeypatch.setattr("tools.gui_tools._target_window_safety", lambda hwnd, title: None)
    monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.GetForegroundWindow", lambda: 0x1234)
    return fake


# --------------------------------------------------------------------------
# The MTA worker.
# --------------------------------------------------------------------------

class TestUIAWorker:
    def test_worker_runs_calls_on_dedicated_thread(self):
        worker = _UIAWorker()
        worker.start()
        names = []

        def probe():
            names.append(threading.current_thread().name)
            return 42

        assert worker.run(probe) == 42
        assert worker.run(probe) == 42
        assert names == ["jarvis-uia-mta", "jarvis-uia-mta"]
        worker.stop()

    def test_worker_propagates_call_exceptions(self):
        worker = _UIAWorker()
        worker.start()

        def boom():
            raise ValueError("boom")

        with pytest.raises(ValueError, match="boom"):
            worker.run(boom)
        worker.stop()

    def test_worker_restarts_after_stop(self):
        worker = _UIAWorker()
        worker.start()
        assert worker.run(lambda: 1) == 1
        worker.stop()
        worker.start()
        assert worker.run(lambda: 2) == 2
        worker.stop()


# --------------------------------------------------------------------------
# Deterministic gates and validation helpers.
# --------------------------------------------------------------------------

class TestDesktopGate:
    def test_desktop_gate_locked(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools._session_accepts_input", lambda: False)
        error = _desktop_gate()
        assert error is not None
        assert error["code"] == "SESSION_LOCKED"

    def test_desktop_gate_open(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools._session_accepts_input", lambda: True)
        assert _desktop_gate() is None

    def test_session_accepts_input_rejects_no_input_desktop(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.OpenInputDesktop", lambda *a, **k: 0)
        assert _session_accepts_input() is False

    def test_session_accepts_input_rejects_secure_desktop(self, monkeypatch):
        def fake_open(*a, **k):
            return 1
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.OpenInputDesktop", fake_open)

        def fake_getinfo(h, info, buf, size, needed):
            ctypes_string = ctypes.create_unicode_buffer("Winlogon")
            ctypes.memmove(buf, ctypes_string, (len(ctypes_string.value) + 1) * 2)
            return 1
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.GetUserObjectInformationW", fake_getinfo)
        assert _session_accepts_input() is False

    def test_session_accepts_input_accepts_default_desktop(self, monkeypatch):
        def fake_open(*a, **k):
            return 1
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.OpenInputDesktop", fake_open)

        def fake_getinfo(h, info, buf, size, needed):
            ctypes_string = ctypes.create_unicode_buffer("Default")
            ctypes.memmove(buf, ctypes_string, (len(ctypes_string.value) + 1) * 2)
            return 1
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.GetUserObjectInformationW", fake_getinfo)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.CloseDesktop", lambda h: 1)
        assert _session_accepts_input() is True


class TestTargetWindowSafety:
    def test_no_hwnd_is_window_not_found(self):
        assert _target_window_safety(0, "x")["code"] == "WINDOW_NOT_FOUND"

    def test_hidden_window_denied(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "Notepad")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "Untitled - Notepad")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: False)
        assert _target_window_safety(0xABC, "x")["code"] == "TARGET_NOT_VISIBLE"

    def test_minimized_window_denied(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "Notepad")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "Untitled - Notepad")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: False)
        assert _target_window_safety(0xABC, "x")["code"] == "TARGET_MINIMIZED"

    def test_secure_desktop_class_denied(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "Consent UI Class")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "Do you want to allow changes?")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: False)
        assert _target_window_safety(0xABC, "x")["code"] == "SECURITY_SURFACE_DENIED"

    def test_security_title_denied(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "ApplicationFrameWindow")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "User Account Control")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: False)
        assert _target_window_safety(0xABC, "x")["code"] == "SECURITY_SURFACE_DENIED"

    def test_elevated_process_denied(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "Notepad")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "admin.txt - Notepad")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: True)
        assert _target_window_safety(0xABC, "x")["code"] == "ELEVATED_TARGET_DENIED"

    def test_safe_window_allowed(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsWindowVisible", lambda h: 1)
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.IsIconic", lambda h: 0)
        monkeypatch.setattr("tools.gui_tools._window_class", lambda h: "Notepad")
        monkeypatch.setattr("tools.gui_tools._window_title", lambda h: "notes.txt - Notepad")
        monkeypatch.setattr("tools.gui_tools._window_pid", lambda h: 100)
        monkeypatch.setattr("tools.gui_tools._is_elevated", lambda pid: False)
        assert _target_window_safety(0xABC, "x") is None

    def test_is_elevated_uses_integrity_rid(self, monkeypatch):
        monkeypatch.setattr("tools.gui_tools._integrity_rid", lambda pid: 4096)
        assert _is_elevated(100) is False
        monkeypatch.setattr("tools.gui_tools._integrity_rid", lambda pid: 12288)
        assert _is_elevated(100) is True
        monkeypatch.setattr("tools.gui_tools._integrity_rid", lambda pid: None)
        assert _is_elevated(100) is False


class TestHotkeyValidation:
    def test_dangerous_hotkey_rejected(self):
        assert _hotkey_error(["ctrl", "alt", "delete"])["code"] == "DANGEROUS_HOTKEY_REJECTED"
        assert _hotkey_error(["ctrl", "shift", "esc"])["code"] == "DANGEROUS_HOTKEY_REJECTED"
        assert _hotkey_error(["win", "l"])["code"] == "DANGEROUS_HOTKEY_REJECTED"

    def test_empty_hotkey_rejected(self):
        assert _hotkey_error([])["code"] == "HOTKEY_REQUIRED"

    def test_invalid_key_rejected(self):
        assert _hotkey_error(["ctrl", "superkey"])["code"] == "INVALID_HOTKEY_KEY"

    def test_safe_hotkey_allowed(self):
        assert _hotkey_error(["ctrl", "c"]) is None
        assert _hotkey_error(["ctrl", "v"]) is None


# --------------------------------------------------------------------------
# Tool execute paths (fake driver on the real MTA worker).
# --------------------------------------------------------------------------

class TestGUIObserveTool:
    @pytest.mark.asyncio
    async def test_observe_success(self, gui_env):
        tool = GUIObserveTool()
        result = await tool.execute(GUIObserveInput(window_title="Notepad"))
        assert result.success is True
        assert result.data["window"]["title"] == "Notepad"
        assert result.data["bounded"] is True
        assert gui_env.calls[0][0] == "observe"

    @pytest.mark.asyncio
    async def test_observe_window_not_found(self, gui_env, monkeypatch):
        gui_env.observe = lambda *a: {"found": False}
        tool = GUIObserveTool()
        result = await tool.execute(GUIObserveInput(window_title="Missing"))
        assert result.success is False
        assert result.error["code"] == "WINDOW_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_observe_locked_session_gate(self, gui_env, monkeypatch):
        monkeypatch.setattr("tools.gui_tools._desktop_gate",
                            lambda: {"code": "SESSION_LOCKED", "message": "locked"})
        tool = GUIObserveTool()
        result = await tool.execute(GUIObserveInput(window_title="Notepad"))
        assert result.success is False
        assert result.error["code"] == "SESSION_LOCKED"

    @pytest.mark.asyncio
    async def test_observe_ocr_reports_unavailable_honestly(self, gui_env):
        tool = GUIObserveTool()
        result = await tool.execute(GUIObserveInput(window_title="Notepad", use_ocr=True))
        assert result.success is True
        assert result.data["ocr"]["available"] is False
        assert "not installed" in result.data["ocr"]["reason"]
        assert result.data["ocr"]["max_age_seconds"] > 0

    @pytest.mark.asyncio
    async def test_observe_verify(self, gui_env):
        tool = GUIObserveTool()
        assert await tool.verify(GUIObserveInput(window_title="x"), Mock(success=True, data={})) is True
        assert await tool.verify(GUIObserveInput(window_title="x"), Mock(success=False, data=None)) is False


class TestGUIFocusTool:
    @pytest.mark.asyncio
    async def test_focus_success(self, gui_env):
        tool = GUIFocusTool()
        result = await tool.execute(GUIFocusInput(window_title="Notepad"))
        assert result.success is True
        assert result.data["focused_window"] == "Notepad"
        assert gui_env.calls[0][0] == "focus"

    @pytest.mark.asyncio
    async def test_focus_window_not_found(self, gui_env, monkeypatch):
        gui_env.focus = lambda *a: {"ok": False, "code": "WINDOW_NOT_FOUND", "message": "nope"}
        tool = GUIFocusTool()
        result = await tool.execute(GUIFocusInput(window_title="Missing"))
        assert result.success is False
        assert result.error["code"] == "WINDOW_NOT_FOUND"


class TestGUIClickTool:
    @pytest.mark.asyncio
    async def test_click_success_is_single_input(self, gui_env):
        tool = GUIClickTool()
        result = await tool.execute(GUIClickInput(window_title="Notepad", x=10, y=20))
        assert result.success is True
        assert result.data["clicked_at"] == {"x": 10, "y": 20}
        assert result.data["inputs"] == 1
        assert result.data["reobserved"]["found"] is True
        kinds = [c[0] for c in gui_env.calls]
        assert "observe" in kinds and "single_click" in kinds

    @pytest.mark.asyncio
    async def test_click_foreground_required(self, gui_env, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.GetForegroundWindow", lambda: 0x9999)
        tool = GUIClickTool()
        result = await tool.execute(GUIClickInput(window_title="Notepad", x=10, y=20))
        assert result.success is False
        assert result.error["code"] == "FOREGROUND_REQUIRED"
        assert [c[0] for c in gui_env.calls] == ["observe"]

    @pytest.mark.asyncio
    async def test_click_policy_requirements(self, gui_env):
        requirements = GUIClickTool().get_policy_requirements()
        assert requirements["input_injection"] is True
        assert requirements["coordinate_bounds_check"] is True
        assert requirements["requires_confirmation"] is True


class TestGUITypeTool:
    @pytest.mark.asyncio
    async def test_type_success(self, gui_env):
        tool = GUITypeTool()
        result = await tool.execute(GUITypeInput(window_title="Notepad", text="Hello World"))
        assert result.success is True
        assert result.data["typed_text"] == "Hello World"
        assert result.data["length"] == 11
        assert result.data["inputs"] == 1

    @pytest.mark.asyncio
    async def test_type_foreground_required(self, gui_env, monkeypatch):
        monkeypatch.setattr("tools.gui_tools.ctypes.windll.user32.GetForegroundWindow", lambda: 0x9999)
        tool = GUITypeTool()
        result = await tool.execute(GUITypeInput(window_title="Notepad", text="x"))
        assert result.success is False
        assert result.error["code"] == "FOREGROUND_REQUIRED"


class TestGUIHotkeyTool:
    @pytest.mark.asyncio
    async def test_hotkey_success(self, gui_env):
        tool = GUIHotkeyTool()
        result = await tool.execute(GUIHotkeyInput(window_title="Notepad", keys=["ctrl", "c"]))
        assert result.success is True
        assert result.data["hotkey_executed"] == "ctrl+c"
        assert result.data["inputs"] == 1

    @pytest.mark.asyncio
    async def test_hotkey_dangerous_rejected_before_driver(self, gui_env):
        tool = GUIHotkeyTool()
        result = await tool.execute(GUIHotkeyInput(window_title="Notepad", keys=["ctrl", "alt", "delete"]))
        assert result.success is False
        assert result.error["code"] == "DANGEROUS_HOTKEY_REJECTED"
        assert gui_env.calls == []


class TestGUIInvokeTool:
    @pytest.mark.asyncio
    async def test_invoke_success(self, gui_env):
        tool = GUIInvokeTool()
        result = await tool.execute(
            GUIInvokeInput(window_title="Notepad", element_description="Save"))
        assert result.success is True
        assert result.data["method"] == "uia_invoke"
        assert gui_env.calls[0][0] == "invoke"

    @pytest.mark.asyncio
    async def test_invoke_element_not_found(self, gui_env, monkeypatch):
        gui_env.invoke = lambda *a: {"ok": False, "code": "ELEMENT_NOT_FOUND", "message": "nope"}
        tool = GUIInvokeTool()
        result = await tool.execute(
            GUIInvokeInput(window_title="Notepad", element_description="Nope"))
        assert result.success is False
        assert result.error["code"] == "ELEMENT_NOT_FOUND"

    @pytest.mark.asyncio
    async def test_invoke_policy_requirements(self, gui_env):
        requirements = GUIInvokeTool().get_policy_requirements()
        assert requirements["requires_ui_automation"] is True
        assert requirements["element_interaction"] is True


class TestGUISetValueTool:
    @pytest.mark.asyncio
    async def test_set_value_success(self, gui_env):
        tool = GUISetValueTool()
        result = await tool.execute(
            GUISetValueInput(window_title="Notepad", element_description="Filename", value="a.txt"))
        assert result.success is True
        assert result.data["value_set"] == "a.txt"
        assert result.data["method"] == "uia_value"

    @pytest.mark.asyncio
    async def test_set_value_no_pattern(self, gui_env, monkeypatch):
        gui_env.set_value = lambda *a: {"ok": False, "code": "NO_VALUE_PATTERN", "message": "no pattern"}
        tool = GUISetValueTool()
        result = await tool.execute(
            GUISetValueInput(window_title="Notepad", element_description="Field", value="x"))
        assert result.success is False
        assert result.error["code"] == "NO_VALUE_PATTERN"


# --------------------------------------------------------------------------
# Registration and policy surface.
# --------------------------------------------------------------------------

class TestGUIRegistration:
    @pytest.mark.asyncio
    async def test_all_gui_tools_registered(self, gui_env):
        tools = [
            GUIObserveTool(),
            GUIFocusTool(),
            GUIClickTool(),
            GUITypeTool(),
            GUIHotkeyTool(),
            GUIInvokeTool(),
            GUISetValueTool(),
        ]
        assert [t.name for t in tools] == [
            "gui_observe", "gui_focus", "gui_click", "gui_type",
            "gui_hotkey", "gui_invoke", "gui_set_value",
        ]
        for tool in tools:
            assert tool.input_schema is not None
            assert callable(tool.execute)
            assert callable(tool.verify)
            assert callable(tool.get_policy_requirements)
            assert callable(tool.validate_targets)

    @pytest.mark.asyncio
    async def test_policy_requirements_include_ui_automation(self, gui_env):
        for tool in [GUIObserveTool(), GUIFocusTool(), GUIClickTool(), GUITypeTool(),
                     GUIHotkeyTool(), GUIInvokeTool(), GUISetValueTool()]:
            reqs = tool.get_policy_requirements()
            assert reqs.get("requires_ui_automation") is True
            assert reqs.get("elevated_process_check") is True

    @pytest.mark.asyncio
    async def test_observe_is_not_confirmation_gated(self, gui_env):
        assert GUIObserveTool().get_policy_requirements()["requires_confirmation"] is False

    @pytest.mark.asyncio
    async def test_mutating_gui_tools_require_confirmation(self, gui_env):
        for tool in [GUIFocusTool(), GUIClickTool(), GUITypeTool(),
                     GUIHotkeyTool(), GUIInvokeTool(), GUISetValueTool()]:
            assert tool.get_policy_requirements()["requires_confirmation"] is True
