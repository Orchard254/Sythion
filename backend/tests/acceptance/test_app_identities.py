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
from backend.api.schemas import ToolOutput
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
    assert set(fields.keys()) == {"app_id", "new_instance"}


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
async def test_app_launch_new_instance_true_bypasses_focus(app_executor, monkeypatch, tmp_path):
    """new_instance=True skips the focus-existing check entirely and launches a
    fresh process, even when a matching window exists and would otherwise be
    focused."""
    exe = tmp_path / "fixture.exe"
    exe.write_text("", encoding="utf-8")
    registered = {"id": "ABC-123", "name": "Fixture App", "exe": str(exe)}
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: registered)
    monkeypatch.setattr("backend.tools.app_tools._new_window_argv", lambda exe: [])

    focus_calls = []
    async def _fake_focus_execute(self, input_data):
        focus_calls.append(input_data)
        return ToolOutput(success=True, data={"method": "focused_existing"})

    monkeypatch.setattr("backend.tools.app_tools.AppFocusTool.execute", _fake_focus_execute)

    launched = []
    def _popen(args, shell):
        launched.append((list(args), shell))

    monkeypatch.setattr("backend.tools.app_tools.subprocess.Popen", _popen)

    out = await _run(app_executor, "app_launch", {"app_id": "Fixture App", "new_instance": True})
    assert out.success
    assert out.data["method"] == "new_instance"
    assert focus_calls == []
    assert launched == [([str(exe)], False)]


@pytest.mark.asyncio
async def test_app_launch_new_instance_true_uses_new_window_flag_for_chrome(app_executor, monkeypatch, tmp_path):
    """Chrome/Edge are single-process apps: a bare Popen would attach to the
    running instance and reuse its window, so new_instance=True must pass the
    documented --new-window flag to get a genuinely separate window."""
    exe = tmp_path / "chrome.exe"
    exe.write_text("", encoding="utf-8")
    registered = {"id": "CHROME", "name": "Google Chrome", "exe": str(exe)}
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: registered)

    launched = []
    def _popen(args, shell):
        launched.append((list(args), shell))

    monkeypatch.setattr("backend.tools.app_tools.subprocess.Popen", _popen)

    out = await _run(app_executor, "app_launch", {"app_id": "Google Chrome", "new_instance": True})
    assert out.success
    assert out.data["method"] == "new_instance"
    assert launched == [([str(exe), "--new-window"], False)]


@pytest.mark.asyncio
async def test_app_launch_new_instance_default_preserves_focus_first(app_executor, monkeypatch, tmp_path):
    """Regression: new_instance=False (the default) keeps today's exact
    behavior — focus an existing open window first, and only fall back to a
    plain launch when focus finds nothing."""
    exe = tmp_path / "fixture.exe"
    exe.write_text("", encoding="utf-8")
    registered = {"id": "ABC-123", "name": "Fixture App", "exe": str(exe)}
    monkeypatch.setattr("backend.tools.app_tools._resolve_registered_app", lambda app_id: registered)
    monkeypatch.setattr("backend.tools.app_tools._new_window_argv", lambda exe: [])

    async def _fake_focus_execute(self, input_data):
        return ToolOutput(success=True, data={"method": "focused_existing"})

    monkeypatch.setattr("backend.tools.app_tools.AppFocusTool.execute", _fake_focus_execute)

    launched = []
    def _popen(args, shell):
        launched.append((list(args), shell))

    monkeypatch.setattr("backend.tools.app_tools.subprocess.Popen", _popen)

    out = await _run(app_executor, "app_launch", {"app_id": "Fixture App"})
    assert out.success
    assert out.data["method"] == "focused_existing"
    assert launched == []

    # And when focus finds nothing, the default falls back to a plain launch.
    async def _fake_focus_execute_fail(self, input_data):
        return ToolOutput(success=False, error={"code": "WINDOW_NOT_FOUND", "message": "none"})

    monkeypatch.setattr("backend.tools.app_tools.AppFocusTool.execute", _fake_focus_execute_fail)
    out2 = await _run(app_executor, "app_launch", {"app_id": "Fixture App"})
    assert out2.success
    assert out2.data["method"] == "registered_launch"
    assert launched == [([str(exe)], False)]


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

    def GetWindowThreadProcessId(self, hwnd):
        return (0, hwnd)

    def EnumWindows(self, callback, results):
        self.enum_calls += 1
        if self.enum_calls == 1 or self.window_still_open:
            results.append((7, 7))

    def PostMessage(self, hwnd, msg, w, l):
        self.posted_close.append((hwnd, msg))


def _patch_win32(monkeypatch, still_open, process_alive=None):
    fake = _FakeWin32(still_open)
    monkeypatch.setitem(sys.modules, "win32gui", fake)
    win32con = types.SimpleNamespace(WM_CLOSE=0x0010)
    monkeypatch.setitem(sys.modules, "win32con", win32con)
    monkeypatch.setattr("backend.tools.app_tools._UNSAVED_WORK_PROBE_MS", 0)
    if process_alive is not None:
        monkeypatch.setattr("backend.tools.app_tools._process_alive", lambda pid: process_alive)
    return fake


@pytest.mark.asyncio
async def test_app_close_graceful_does_not_answer_unsaved_prompt(app_executor, monkeypatch):
    """A still-open process after WM_CLOSE must be surfaced, never Save/Discarded."""
    fake = _patch_win32(monkeypatch, still_open=True, process_alive=True)
    out = await _run(app_executor, "app_close", {"app_id": "Fixture App", "force": False})
    assert not out.success
    assert out.error["code"] == "UNSAVED_WORK_PROMPT"
    # WM_CLOSE was posted once and no taskkill/force action was taken.
    assert fake.posted_close == [(7, 0x0010)]


@pytest.mark.asyncio
async def test_app_close_graceful_succeeds_when_window_gone(app_executor, monkeypatch):
    """A closed process whose window is gone reports success via WM_CLOSE."""
    fake = _patch_win32(monkeypatch, still_open=False, process_alive=False)
    out = await _run(app_executor, "app_close", {"app_id": "Fixture App", "force": False})
    assert out.success
    assert out.data["method"] == "wm_close"
    assert fake.posted_close == [(7, 0x0010)]
    assert fake.enum_calls == 2


@pytest.mark.asyncio
async def test_app_close_prompt_dialog_captured_by_process_liveness(app_executor, monkeypatch):
    """An unsaved-work prompt whose caption omits the app name is still caught
    because the process remains alive after WM_CLOSE."""
    fake = _patch_win32(monkeypatch, still_open=False, process_alive=True)
    out = await _run(app_executor, "app_close", {"app_id": "Fixture App", "force": False})
    assert not out.success
    assert out.error["code"] == "UNSAVED_WORK_PROMPT"
    assert fake.posted_close == [(7, 0x0010)]


@pytest.mark.asyncio
async def test_app_focus_resolves_registry_guid_to_display_name(app_executor, monkeypatch):
    """app_list returns registry GUIDs as `id`; app_focus must accept them and
    focus via UI Automation using the resolved display name."""
    guid = "{CCFFC2EC-F561-3EF1-8038-F3608B52F935}"
    monkeypatch.setattr("backend.tools.app_tools._display_name_for_identifier", lambda app_id: "Google Chrome")
    focused = {}

    def _fake_focus(title):
        focused["title"] = title
        return {"ok": True, "window": {"hwnd": 7, "title": "gemma4 - Google Chrome", "pid": 1234, "foreground": True}}

    monkeypatch.setattr("tools.gui_tools._uia_driver", types.SimpleNamespace(focus=_fake_focus))

    async def _patched_run(fn):
        return fn()

    monkeypatch.setattr("tools.gui_tools._run_on_worker", _patched_run)
    monkeypatch.setattr("tools.gui_tools._desktop_gate", lambda: None)
    monkeypatch.setattr("tools.gui_tools._target_window_safety", lambda hwnd, title: None)

    out = await _run(app_executor, "app_focus", {"app_id": guid})
    assert out.success
    assert focused["title"] == "Google Chrome"
    assert out.data["window_handle"] == 7
    assert out.data["title"] == "gemma4 - Google Chrome"
    assert out.data["app_id"] == "Google Chrome"


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


def test_derive_exe_app_paths_fallback_resolves_chrome(monkeypatch):
    """A Chrome-style entry (no DisplayIcon/InstallLocation) resolves via App Paths."""
    from backend.tools import app_tools as at
    exe = r"C:\Program Files\Google\Chrome\Application\chrome.exe"
    monkeypatch.setattr(at, "_registry_value", lambda key, name: None)
    monkeypatch.setattr(at, "_local_existing", lambda path: True)
    monkeypatch.setattr(at, "_iter_app_paths_entries", lambda: [("chrome", exe)])
    assert at._derive_exe(object(), "Google Chrome", "") == exe


def test_derive_exe_app_paths_skips_unrelated_stem(monkeypatch):
    from backend.tools import app_tools as at
    monkeypatch.setattr(at, "_registry_value", lambda key, name: None)
    monkeypatch.setattr(at, "_local_existing", lambda path: True)
    monkeypatch.setattr(at, "_iter_app_paths_entries", lambda: [("vlc", r"C:\x\vlc.exe")])
    assert at._derive_exe(object(), "Google Chrome", "") is None


def test_derive_exe_app_paths_skips_missing_file(monkeypatch):
    from backend.tools import app_tools as at
    monkeypatch.setattr(at, "_registry_value", lambda key, name: None)
    monkeypatch.setattr(at, "_local_existing", lambda path: False)
    monkeypatch.setattr(at, "_iter_app_paths_entries", lambda: [("chrome", r"C:\missing\chrome.exe")])
    assert at._derive_exe(object(), "Google Chrome", "") is None


def test_derive_exe_uninstall_icon_wins_over_app_paths(monkeypatch):
    from backend.tools import app_tools as at
    real = r"C:\real\app.exe"
    app_paths = r"C:\other\app.exe"
    monkeypatch.setattr(at, "_registry_value", lambda key, name: real if name == "DisplayIcon" else None)
    monkeypatch.setattr(at, "_local_existing", lambda path: True)
    monkeypatch.setattr(at, "_iter_app_paths_entries", lambda: [("app", app_paths)])
    assert at._derive_exe(object(), "App", "") == real
