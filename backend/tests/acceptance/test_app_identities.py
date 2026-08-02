"""
Acceptance gate: Registered Windows application identities (Phase C #64/#65/#66).

App discovery reads the Windows uninstall hives; launch resolves strictly
against those registered identities. The model can never supply an executable
path or command-line arguments, and non-Windows execution paths are removed.
"""

import platform
import sys
import types

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.app_tools import create_app_tools


@pytest.fixture
def app_executor(monkeypatch):
    ex = ToolExecutor()
    for tool in create_app_tools():
        ex.register_tool(tool)
    return ex


def _run(app_executor, name, params):
    return app_executor.execute_tool(name, params, policy_context=PolicyContext(direct_user_instruction=True))


@pytest.mark.asyncio
async def test_executable_path_rejected_as_app_id(app_executor):
    """A model-supplied executable path must fail validation before execute."""
    for bad in ["C:\\Windows\\notepad.exe", "C:/Windows/system32/cmd.exe", "notepad.exe", "..\\..\\evil.exe"]:
        out = await _run(app_executor, "app_launch", {"app_id": bad})
        assert not out.success
        assert out.error["code"] in ("INVALID_APP_ID", "APP_NOT_REGISTERED")


@pytest.mark.asyncio
async def test_command_line_arguments_impossible(app_executor, monkeypatch):
    """app_launch input has no command-line argument field."""
    from backend.api import schemas as schemas
    fields = schemas.AppLaunchInput.__fields__
    assert set(fields.keys()) == {"app_id"}


@pytest.mark.asyncio
async def test_launch_unregistered_identity_refused(app_executor, monkeypatch):
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: None)
    called = {"popen": False}

    def _popen(*args, **kwargs):
        called["popen"] = True

    monkeypatch.setattr("backend.tools.app_tools.subprocess.Popen", _popen)

    out = await _run(app_executor, "app_launch", {"app_id": "DefinitelyNotInstalled App"})
    assert not out.success
    assert out.error["code"] == "APP_NOT_REGISTERED"
    assert not called["popen"]


@pytest.mark.asyncio
async def test_launch_registered_identity_exact_exe_no_args(app_executor, monkeypatch, tmp_path):
    exe = tmp_path / "fixture.exe"
    exe.write_text("", encoding="utf-8")
    registered = {
        "id": "ABC-123",
        "name": "Fixture App",
        "exe": str(exe),
    }
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: registered)
    launched = []

    def _popen(args, shell):
        launched.append((list(args), shell))

    monkeypatch.setattr("backend.tools.app_tools.subprocess.Popen", _popen)

    out = await _run(app_executor, "app_launch", {"app_id": "Fixture App"})
    assert out.success
    assert out.data["method"] == "registered_launch"
    assert launched == [([str(exe)], False)]


@pytest.mark.asyncio
async def test_launch_registered_identity_missing_exe(app_executor, monkeypatch):
    registered = {"id": "X", "name": "Broken App", "exe": None}
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: registered)
    out = await _run(app_executor, "app_launch", {"app_id": "Broken App"})
    assert not out.success
    assert out.error["code"] == "APP_EXE_UNAVAILABLE"


@pytest.mark.asyncio
async def test_app_list_non_windows_rejected(app_executor, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Linux")
    out = await _run(app_executor, "app_list", {})
    assert not out.success
    assert out.error["code"] == "NON_WINDOWS_UNSUPPORTED"


@pytest.mark.asyncio
async def test_app_launch_non_windows_rejected(app_executor, monkeypatch):
    monkeypatch.setattr(platform, "system", lambda: "Darwin")
    out = await _run(app_executor, "app_launch", {"app_id": "Some App"})
    assert not out.success
    assert out.error["code"] == "NON_WINDOWS_UNSUPPORTED"


@pytest.mark.asyncio
async def test_app_close_wildcard_rejected(app_executor):
    out = await _run(app_executor, "app_close", {"app_id": "*.exe", "force": True})
    assert not out.success
    assert out.error["code"] == "INVALID_APP_ID"


class _FakeWin32:
    """Minimal win32gui/win32con stand-in that simulates WM_CLOSE probing."""

    def __init__(self, window_still_open):
        self.window_still_open = window_still_open
        self.enum_calls = 0
        self.posted_close = []

    def IsWindowVisible(self, hwnd):
        return True

    def GetWindowText(self, hwnd):
        return "Fixture App Window"

    def EnumWindows(self, callback, results):
        self.enum_calls += 1
        if self.enum_calls == 1 or self.window_still_open:
            results.append(7)

    def PostMessage(self, hwnd, msg, w, l):
        self.posted_close.append((hwnd, msg))


def _patch_win32(monkeypatch, still_open):
    fake = _FakeWin32(still_open)
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    win32con = types.SimpleNamespace(WM_CLOSE=0x0010)
    monkeypatch.setitem(sys.modules, "win32con", win32con)
    monkeypatch.setattr("backend.tools.app_tools._UNSAVED_WORK_PROBE_MS", 0)
    return fake


@pytest.mark.asyncio
async def test_app_close_graceful_does_not_answer_unsaved_prompt(app_executor, monkeypatch):
    """A still-open window after WM_CLOSE must be surfaced, never Save/Discarded."""
    fake = _patch_win32(monkeypatch, still_open=True)
    out = await _run(app_executor, "app_close", {"app_id": "Fixture App", "force": False})
    assert not out.success
    assert out.error["code"] == "UNSAVED_WORK_PROMPT"
    # WM_CLOSE was posted once and no taskkill/force action was taken.
    assert fake.posted_close == [(7, 0x0010)]
    assert fake.enum_calls == 2


@pytest.mark.asyncio
async def test_app_close_graceful_succeeds_when_window_gone(app_executor, monkeypatch):
    fake = _patch_win32(monkeypatch, still_open=False)
    out = await _run(app_executor, "app_close", {"app_id": "Fixture App", "force": False})
    assert out.success
    assert out.data["method"] == "wm_close"
    assert fake.enum_calls == 2


@pytest.mark.asyncio
async def test_app_list_registry_discovery_returns_identities(app_executor, monkeypatch):
    """app_list returns the same registered identities launch resolves."""
    discovered = [
        {"id": "A", "name": "Alpha", "exe": "C:\\x\\alpha.exe", "publisher": None, "version": "1.0", "install_location": None},
        {"id": "B", "name": "Beta", "exe": None, "publisher": None, "version": None, "install_location": None},
    ]
    monkeypatch.setattr("backend.tools.app_tools._windows_registered_apps", lambda: discovered)
    out = await _run(app_executor, "app_list", {"search_term": "alp"})
    assert out.success
    assert out.data["applications"] == [discovered[0]]
