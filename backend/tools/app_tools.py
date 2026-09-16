"""
Application Tools for Sythion (Synth)
Implements application management: list, launch, focus, close.

v1 targets Windows only. App discovery reads the registered-uninstall registry
hives, and launch resolves strictly against those registered identities -- the
model can never supply an executable path or command-line arguments.
"""
import asyncio
import logging
import os
import platform
import re
import subprocess
from typing import Any, Dict, Generic, List, Optional, TypeVar

logger = logging.getLogger(__name__)

try:
    from backend.tools.executor import BaseTool, handle_tool_errors
    from backend.api.schemas import (
        ToolOutput,
        AppListInput,
        AppLaunchInput,
        AppFocusInput,
        AppCloseInput,
    )
except ImportError:
    from tools.executor import BaseTool, handle_tool_errors
    from api.schemas import (
        ToolOutput,
        AppListInput,
        AppLaunchInput,
        AppFocusInput,
        AppCloseInput,
    )


# App identifiers are used as argument-list entries (never through a shell).
# Rejecting shell metacharacters, path separators, and Windows drive/UNC forms
# keeps the value a plain registered display name and blocks model-supplied
# executable paths (#66).
_APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]*$")

# How long to wait after a graceful WM_CLOSE before deciding the app is not
# closing. A still-open window is treated as an unsaved-work prompt that must
# be answered by the user, never auto-answered as Save or Discard (#67).
_UNSAVED_WORK_PROBE_MS = 1500

# Apps that use a single-process model (Chrome, Edge) where a bare `Popen` of
# the exe attaches to the running instance and focuses/reuses its existing
# window instead of opening a genuinely separate one. For these we pass the
# documented "new window" flag to guarantee a fresh window when the model
# requests `new_instance=True`. Only registry-derived, known single-instance
# browsers are special-cased; everything else launches plainly.
_NEW_WINDOW_FLAG_BY_EXE_MARKER = {
    "chrome.exe": "--new-window",
    "msedge.exe": "--new-window",
}


def _new_window_argv(exe: str) -> List[str]:
    """Return the argv tail (flag or empty) when a bare Popen would otherwise
    reuse an existing single-instance window instead of opening a new one."""
    if not exe:
        return []
    marker = os.path.basename(exe).lower()
    flag = _NEW_WINDOW_FLAG_BY_EXE_MARKER.get(marker)
    return [flag] if flag else []


def _process_alive(pid: int) -> bool:
    """Return True if the process is still running.

    A graceful WM_CLOSE can leave the process alive behind an unsaved-work
    prompt whose dialog caption does not contain the app name (e.g. Win11
    Notepad titles it after the document), so title-based re-probes can miss
    it. Process liveness is the source of truth; if the PID cannot be
    inspected (e.g. ACCESS_DENIED on an elevated process) it is treated as
    alive rather than risk reporting an unverified close.
    """
    if not pid:
        return False
    try:
        import ctypes
        from ctypes import wintypes

        open_process = ctypes.windll.kernel32.OpenProcess
        open_process.restype = wintypes.HANDLE
        open_process.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]

        PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
        handle = open_process(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
        if not handle:
            # ERROR_INVALID_PARAMETER (87) => no such process.
            return ctypes.windll.kernel32.GetLastError() != 87
        try:
            code = ctypes.c_ulong()
            ctypes.windll.kernel32.GetExitCodeProcess(handle, ctypes.byref(code))
            return code.value == 259  # STILL_ACTIVE
        finally:
            ctypes.windll.kernel32.CloseHandle(handle)
    except Exception:
        return True


def _validate_app_id(app_id: str) -> None:
    if not app_id or not _APP_ID_PATTERN.match(app_id) or any(c in app_id for c in "/\\:"):
        raise ValueError(f"Invalid app_id: {app_id!r}")


def _app_target_error(app_id: str) -> Optional[Dict[str, str]]:
    """Deterministic, side-effect-free validation of an app target."""
    try:
        _validate_app_id(_display_name_for_identifier(app_id))
    except ValueError as e:
        return {"code": "INVALID_APP_ID", "message": str(e)}
    return None


def _registry_value(key, name):
    import winreg
    try:
        return winreg.QueryValueEx(key, name)[0]
    except (FileNotFoundError, OSError):
        return None


def _local_existing(path: str) -> bool:
    """True when `path` is a local file that exists.

    Remote/UNC paths are skipped entirely: they are not trusted local
    identities, and probing them can block for minutes on dead shares.
    """
    if not path:
        return False
    if path.startswith("\\\\") or ":" not in path:
        return False
    try:
        return os.path.isfile(path)
    except (OSError, ValueError):
        return False


def _derive_exe(subkey, display_name: str, install_location: str) -> Optional[str]:
    """Derive the launchable executable from registry data only."""
    icon = _registry_value(subkey, "DisplayIcon")
    if icon:
        candidate = str(icon).strip().strip('"')
        # DisplayIcon is often `"C:\\...\\app.exe",0`; keep only the file.
        candidate = candidate.split(",", 1)[0]
        if candidate.lower().endswith((".exe", ".com", ".bat", ".cmd")) and _local_existing(candidate):
            return candidate
    if install_location and display_name:
        candidate = os.path.join(install_location, display_name + ".exe")
        if _local_existing(candidate):
            return candidate
    return _app_paths_exe(display_name)


def _iter_app_paths_entries():
    """Yield (stem, default_value) pairs from the App Paths hive.

    App Paths (`name.exe` keys whose default value is the full executable
    path) is a trusted, registry-only source: Chrome and several store apps
    omit DisplayIcon/InstallLocation in the uninstall hive. Like the uninstall
    hives it is written by installers, never by the model (#66).
    """
    import winreg
    hives = [
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths"),
    ]
    for hkey, path in hives:
        try:
            key = winreg.OpenKey(hkey, path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                except OSError:
                    continue
                try:
                    default = _registry_value(subkey, "")
                finally:
                    winreg.CloseKey(subkey)
                if default:
                    yield os.path.splitext(subkey_name)[0].lower(), str(default)
        finally:
            winreg.CloseKey(key)


def _app_paths_exe(display_name: str) -> Optional[str]:
    """Resolve an executable via App Paths when the uninstall entry has none.

    The candidate is matched when the App Paths stem (e.g. "chrome" from
    "chrome.exe") appears as a token of the registered display name (e.g.
    "Google Chrome"), then verified to exist on disk before it is returned.
    """
    if not display_name:
        return None
    tokens = set(re.findall(r"[a-z0-9]+", str(display_name).lower()))
    if not tokens:
        return None
    for stem, raw in _iter_app_paths_entries():
        if stem not in tokens:
            continue
        candidate = str(raw).strip().strip('"')
        if candidate.lower().endswith((".exe", ".com", ".bat", ".cmd")) and _local_existing(candidate):
            return candidate
    return None


def _builtin_windows_apps() -> List[Dict[str, Any]]:
    """Return built-in Windows System32 utilities (Notepad, Calculator, Paint, etc.)."""
    sys_root = os.environ.get("SystemRoot", r"C:\Windows")
    sys32 = os.path.join(sys_root, "System32")
    sys_root_parent = os.environ.get("SystemRoot", r"C:\Windows")
    candidates = [
        ("Notepad", "notepad.exe"),
        ("Calculator", "calc.exe"),
        ("Paint", "mspaint.exe"),
        ("Task Manager", "taskmgr.exe"),
        ("Snipping Tool", "SnippingTool.exe"),
        ("PowerShell", "WindowsPowerShell\\v1.0\\powershell.exe"),
        ("CMD", "cmd.exe"),
        ("Windows Terminal", "wt.exe"),
    ]
    builtins = []
    for name, filename in candidates:
        exe = os.path.join(sys32, filename)
        if _local_existing(exe):
            builtins.append({
                "id": name,
                "name": name,
                "publisher": "Microsoft Corporation",
                "version": "OS Built-in",
                "install_location": sys32,
                "exe": exe,
            })
    return builtins


def _windows_registered_apps() -> List[Dict[str, Any]]:
    """Enumerate applications registered in the Windows uninstall hives, OS built-ins, and UWP/Store apps."""
    import winreg
    registry_paths = [
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
    ]
    apps = _builtin_windows_apps()
    seen_ids = {a["name"] for a in apps}
    
    # Add UWP/Store apps
    try:
        import subprocess
        result = subprocess.run(
            ["powershell", "-Command", "Get-AppxPackage | Select-Object Name, PackageFullName, InstallLocation | ConvertTo-Json"],
            capture_output=True, text=True, timeout=30
        )
        if result.returncode == 0 and result.stdout.strip():
            import json
            uwp_apps = json.loads(result.stdout)
            if isinstance(uwp_apps, dict):
                uwp_apps = [uwp_apps]
            for pkg in uwp_apps:
                name = pkg.get("Name", "")
                if name and name not in seen_ids:
                    install_loc = pkg.get("InstallLocation", "")
                    exe = None
                    if install_loc:
                        # Try to find the main executable
                        try:
                            import glob
                            # Try full name first
                            pattern = os.path.join(install_loc, f"{name}.exe")
                            if os.path.isfile(pattern):
                                exe = pattern
                            else:
                                # Try short name (e.g., "Boom3D" from "GlobalDelightTechnologies.Boom3D")
                                short_name = name.split(".")[-1] if "." in name else name
                                pattern = os.path.join(install_loc, "**", f"{short_name}.exe")
                                matches = glob.glob(pattern, recursive=True)
                                if matches:
                                    exe = matches[0]
                                else:
                                    # Fallback: any .exe in root
                                    pattern = os.path.join(install_loc, "*.exe")
                                    exes = glob.glob(pattern)
                                    if exes:
                                        exe = exes[0]
                        except Exception:
                            pass
                    apps.append({
                        "id": name,
                        "name": name,
                        "publisher": "Microsoft Store",
                        "version": "UWP App",
                        "install_location": install_loc,
                        "exe": exe,
                        "is_uwp": True,
                    })
                    seen_ids.add(name)
    except Exception:
        pass  # UWP enumeration is best-effort
    
    for hkey, path in registry_paths:
        try:
            key = winreg.OpenKey(hkey, path)
        except OSError:
            continue
        try:
            i = 0
            while True:
                try:
                    subkey_name = winreg.EnumKey(key, i)
                except OSError:
                    break
                i += 1
                try:
                    subkey = winreg.OpenKey(key, subkey_name)
                except OSError:
                    continue
                try:
                    display_name = _registry_value(subkey, "DisplayName")
                    if display_name is None or display_name in seen_ids:
                        continue
                    publisher = _registry_value(subkey, "Publisher")
                    version = _registry_value(subkey, "DisplayVersion")
                    install_location = _registry_value(subkey, "InstallLocation")
                    if install_location:
                        install_location = str(install_location)
                    exe = _derive_exe(subkey, str(display_name), install_location or "")
                    seen_ids.add(display_name)
                    apps.append({
                        "id": str(subkey_name),
                        "name": str(display_name),
                        "publisher": str(publisher) if publisher else None,
                        "version": str(version) if version else None,
                        "install_location": install_location,
                        "exe": exe,
                    })
                finally:
                    winreg.CloseKey(subkey)
        finally:
            winreg.CloseKey(key)
    return apps


def _resolve_registered_app(app_id: str) -> Optional[Dict[str, Any]]:
    """Resolve `app_id` to a registered Windows identity by display name."""
    needle = app_id.lower()
    for app in _windows_registered_apps():
        if (app.get("name") or "").lower() == needle:
            return app
    return None


def _display_name_for_identifier(app_id: str) -> str:
    """Resolve an app_list `id` (registry GUID, e.g. `{...}`) to its display
    name so it can be matched against window titles. Non-GUID identifiers are
    returned unchanged."""
    if not app_id.startswith("{"):
        return app_id
    for app in _windows_registered_apps():
        if app.get("id") == app_id and app.get("name"):
            return str(app["name"])
    return app_id


class AppListTool(BaseTool[AppListInput]):
    """List installed applications."""

    name = "app_list"
    description = "List installed applications on the system"
    input_schema = AppListInput

    @handle_tool_errors("APP_LIST_ERROR")
    async def execute(self, input_data: AppListInput) -> ToolOutput:
        """Execute the app list tool."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "app tools are Windows-only in v1"},
                duration_ms=0
            )

        apps = _windows_registered_apps()
        if input_data.search_term:
            needle = input_data.search_term.lower()
            apps = [a for a in apps if needle in (a["name"] or "").lower()]

        apps.sort(key=lambda x: (x["name"] or "").lower())
        names = [a["name"] for a in apps[:30] if a.get("name")]
        model_summary = f"Found {len(apps)} installed applications: {', '.join(names)}"

        return ToolOutput(
            success=True,
            data={
                "count": len(apps),
                "search_term": input_data.search_term,
                "platform": "windows",
                "applications": apps[:100],
            },
            model_summary=model_summary,
            evidence={"source": "system_application_registry"}
        )

    async def verify(self, input_data: AppListInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "applications" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class AppLaunchTool(BaseTool[AppLaunchInput]):
    """Launch a registered Windows application."""

    name = "app_launch"
    description = "Launch an application by its registered Windows display name (reuses an open window by default; new_instance=True opens a fresh window)"
    input_schema = AppLaunchInput

    @handle_tool_errors("APP_LAUNCH_ERROR")
    async def execute(self, input_data: AppLaunchInput) -> ToolOutput:
        """Execute the app launch tool.

        Resolves the id against the registered-app enumeration only. The
        resolved executable path comes from the registry, never from the model,
        and is launched with no arguments and no shell.
        """
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "app tools are Windows-only in v1"},
                duration_ms=0
            )

        app_id = input_data.app_id
        _validate_app_id(app_id)

        app = _resolve_registered_app(app_id)
        if app is None:
            return ToolOutput(
                success=False,
                error={"code": "APP_NOT_REGISTERED",
                       "message": f"No registered Windows application matches '{app_id}'"},
                duration_ms=0
            )

        exe = app.get("exe")
        if not exe or not os.path.isfile(exe):
            return ToolOutput(
                success=False,
                error={"code": "APP_EXE_UNAVAILABLE",
                       "message": f"Registered application '{app_id}' has no resolvable executable"},
                duration_ms=0
            )

        # Focus-First Check: by default (new_instance=False) an open window
        # matching this application is brought to front instead of spawning
        # a duplicate process. new_instance=True skips this entirely.
        if not input_data.new_instance:
            try:
                focus_tool = AppFocusTool()
                focus_res = await focus_tool.execute(AppFocusInput(app_id=app_id))
                if focus_res.success:
                    return ToolOutput(
                        success=True,
                        data={
                            "app_id": app_id,
                            "platform": "windows",
                            "method": "focused_existing",
                            "exe": exe,
                        },
                        model_summary=f"Focused existing open window for '{app_id}'",
                        evidence={"source": "windows_focus_existing"}
                    )
            except Exception:
                pass

        if input_data.new_instance:
            # Genuinely separate window/instance: for single-process apps
            # (Chrome, Edge) pass the new-window flag so Popen does not just
            # attach to the running instance and reuse its window.
            argv = [exe] + _new_window_argv(exe)
            subprocess.Popen(argv, shell=False)
            return ToolOutput(
                success=True,
                data={
                    "app_id": app_id,
                    "platform": "windows",
                    "method": "new_instance",
                    "exe": exe,
                },
                model_summary=f"Launched a new instance of '{app_id}'",
                evidence={"source": "windows_registered_launch"}
            )

        subprocess.Popen([exe], shell=False)
        return ToolOutput(
            success=True,
            data={
                "app_id": app_id,
                "platform": "windows",
                "method": "registered_launch",
                "exe": exe,
            },
            evidence={"source": "windows_registered_launch"}
        )

    async def verify(self, input_data: AppLaunchInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True}

    def validate_targets(self, input_data: AppLaunchInput) -> Optional[Dict[str, str]]:
        return _app_target_error(input_data.app_id)


class AppFocusTool(BaseTool[AppFocusInput]):
    """Focus/bring an application window to front."""

    name = "app_focus"
    description = "Bring an application window to the foreground"
    input_schema = AppFocusInput

    @handle_tool_errors("APP_FOCUS_ERROR")
    async def execute(self, input_data: AppFocusInput) -> ToolOutput:
        """Execute the app focus tool."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "app tools are Windows-only in v1"},
                duration_ms=0
            )

        app_id = input_data.app_id
        # app_list returns registry GUIDs as `id`; resolve to the display
        # name so it can be validated and matched against window titles.
        app_id = _display_name_for_identifier(app_id)
        _validate_app_id(app_id)

        # Raw win32gui.SetForegroundWindow fails under the Windows
        # foreground-lock restriction, so focus goes through the same
        # UI Automation mechanism gui_focus uses (which reliably brings
        # the window forward), with the same desktop/safety gates.
        from backend.tools.gui_tools import (
            _desktop_gate,
            _run_on_worker,
            _target_window_safety,
            _uia_driver,
        )
        gate = _desktop_gate()
        if gate:
            return ToolOutput(success=False, error=gate, duration_ms=0)

        try:
            result = await _run_on_worker(lambda: _uia_driver.focus(app_id))
        except Exception as exc:
            logger.warning("AppFocusTool UIA focus call failed for '%s': %s: %s", app_id, type(exc).__name__, exc, exc_info=True)
            return ToolOutput(
                success=False,
                error={"code": "UIA_UNAVAILABLE", "message": "UI Automation unavailable"},
                duration_ms=0
            )
        if not result.get("ok"):
            return ToolOutput(
                success=False,
                error={"code": result.get("code", "FOCUS_ERROR"), "message": result.get("message", "")},
                duration_ms=0
            )
        window = result.get("window", {})
        error = _target_window_safety(int(window.get("hwnd") or 0), window.get("title") or "")
        if error:
            return ToolOutput(success=False, error=error, duration_ms=0)

        return ToolOutput(
            success=True,
            data={
                "app_id": app_id,
                "window_handle": window.get("hwnd"),
                "title": window.get("title"),
                "pid": window.get("pid"),
                "foreground": window.get("foreground"),
                "platform": "windows",
            },
            evidence={"source": "uia_mta_worker"}
        )

    async def verify(self, input_data: AppFocusInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "low", "requires_confirmation": False}

    def validate_targets(self, input_data: AppFocusInput) -> Optional[Dict[str, str]]:
        return _app_target_error(input_data.app_id)


class AppCloseTool(BaseTool[AppCloseInput]):
    """Close an application."""

    name = "app_close"
    description = "Close an application window or process"
    input_schema = AppCloseInput

    @handle_tool_errors("APP_CLOSE_ERROR")
    async def execute(self, input_data: AppCloseInput) -> ToolOutput:
        """Execute the app close tool."""
        if platform.system() != "Windows":
            return ToolOutput(
                success=False,
                error={"code": "NON_WINDOWS_UNSUPPORTED", "message": "app tools are Windows-only in v1"},
                duration_ms=0
            )

        app_id = input_data.app_id
        force = input_data.force
        # app_list returns registry GUIDs as `id`; resolve to the display
        # name so it can be validated and matched against window titles.
        app_id = _display_name_for_identifier(app_id)
        _validate_app_id(app_id)
        if "*" in app_id:
            raise ValueError("Wildcard app ids are not allowed")

        if force:
            result = subprocess.run(
                ["taskkill", "/IM", app_id, "/F"],
                capture_output=True,
                text=True
            )
            if result.returncode != 0:
                return ToolOutput(
                    success=False,
                    error={"code": "KILL_ERROR", "message": (result.stderr or result.stdout).strip()},
                    duration_ms=0
                )
            return ToolOutput(
                success=True,
                data={"app_id": app_id, "force": force, "platform": "windows", "method": "taskkill_force"},
                evidence={"source": "windows_process_management"}
            )

        # Graceful close via WM_CLOSE.
        try:
            import win32gui
            import win32con
        except ImportError:
            return ToolOutput(
                success=False,
                error={"code": "WINDOWS_API_ERROR", "message": "pywin32 not available"},
                duration_ms=0
            )

        title_needle = app_id.lower()

        def enum_windows(hwnd, results):
            if win32gui.IsWindowVisible(hwnd):
                title = win32gui.GetWindowText(hwnd)
                if title_needle in title.lower():
                    _, pid = win32gui.GetWindowThreadProcessId(hwnd)
                    results.append((hwnd, pid))
            return True

        windows = []
        win32gui.EnumWindows(enum_windows, windows)

        if not windows:
            return ToolOutput(
                success=False,
                error={"code": "WINDOW_NOT_FOUND", "message": f"No window found matching '{app_id}'"},
                duration_ms=0
            )
        for hwnd, _ in windows:
            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

        # Probe for an unsaved-work prompt. If the process is still present
        # we do NOT force-close or answer Save/Discard: the user must decide.
        # Process liveness is checked first because an unsaved-work prompt is
        # a dialog whose caption may not contain the app name; the title
        # re-probe below is a second line of defense for windowed apps that
        # do not map to a process we captured.
        await asyncio.sleep(_UNSAVED_WORK_PROBE_MS / 1000.0)

        closed_pids = {pid for _, pid in windows if pid}
        alive = [pid for pid in closed_pids if _process_alive(pid)]
        if alive:
            return ToolOutput(
                success=False,
                error={"code": "UNSAVED_WORK_PROMPT",
                       "message": f"'{app_id}' is still running (PID {alive}) and may be showing an unsaved-work prompt; ask the user how to proceed."},
                duration_ms=0
            )

        remaining = []
        win32gui.EnumWindows(enum_windows, remaining)
        if remaining:
            return ToolOutput(
                success=False,
                error={"code": "UNSAVED_WORK_PROMPT",
                       "message": f"'{app_id}' is still open and may be showing an unsaved-work prompt; ask the user how to proceed."},
                duration_ms=0
            )

        return ToolOutput(
            success=True,
            data={"app_id": app_id, "force": force, "platform": "windows", "method": "wm_close"},
            evidence={"source": "windows_process_management"}
        )

    async def verify(self, input_data: AppCloseInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool.

        Policy metadata is static (never input-dependent): closing an
        application is a destructive, confirmation-required action.
        """
        return {
            "risk_level": "high",
            "requires_confirmation": True,
            "destructive": True,
        }

    def validate_targets(self, input_data: AppCloseInput) -> Optional[Dict[str, str]]:
        error = _app_target_error(input_data.app_id)
        if error:
            return error
        if "*" in input_data.app_id:
            return {"code": "INVALID_APP_ID", "message": "Wildcard app ids are not allowed"}
        return None


def create_app_tools() -> List[BaseTool]:
    """Create instances of all application tools."""
    return [
        AppListTool(),
        AppLaunchTool(),
        AppFocusTool(),
        AppCloseTool(),
    ]
