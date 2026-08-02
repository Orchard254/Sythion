"""
Application Tools for JARVIS
Implements application management: list, launch, focus, close
Platform-specific implementations for Windows with fallbacks for Linux/Mac
"""
import subprocess
import shutil
import os
import re
import platform
from typing import Dict, Any, List, Optional

from tools.executor import BaseTool
from api.schemas import (
    ToolOutput,
    AppListInput,
    AppLaunchInput,
    AppFocusInput,
    AppCloseInput,
)


# App identifiers are used as argument-list entries (never through a shell),
# but rejecting shell metacharacters defends against `os.startfile`/app-registry
# surprises and keeps the value a plain app name.
_APP_ID_PATTERN = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._+ ()-]*$")


def _validate_app_id(app_id: str) -> None:
    if not app_id or not _APP_ID_PATTERN.match(app_id) or any(c in app_id for c in "/\\"):
        raise ValueError(f"Invalid app_id: {app_id!r}")


class AppListTool(BaseTool):
    """List installed applications."""
    
    name = "app_list"
    description = "List installed applications on the system"
    input_schema = AppListInput
    
    async def execute(self, input_data: AppListInput) -> ToolOutput:
        """Execute the app list tool."""
        try:
            system = platform.system()
            apps = []
            
            if system == "Windows":
                # Windows: Query registry for installed programs
                try:
                    import winreg
                    
                    # Check both 32-bit and 64-bit registry views
                    registry_paths = [
                        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                        (winreg.HKEY_LOCAL_MACHINE, r"SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall"),
                        (winreg.HKEY_CURRENT_USER, r"SOFTWARE\Microsoft\Windows\CurrentVersion\Uninstall"),
                    ]
                    
                    seen_names = set()
                    for hkey, path in registry_paths:
                        try:
                            key = winreg.OpenKey(hkey, path)
                            i = 0
                            while True:
                                try:
                                    subkey_name = winreg.EnumKey(key, i)
                                    subkey = winreg.OpenKey(key, subkey_name)
                                except OSError:
                                    break

                                try:
                                    def _q(subkey_, name_):
                                        try:
                                            return winreg.QueryValueEx(subkey_, name_)[0]
                                        except (FileNotFoundError, OSError):
                                            return None

                                    display_name = _q(subkey, "DisplayName")
                                    publisher = _q(subkey, "Publisher")
                                    version = _q(subkey, "DisplayVersion")
                                    install_location = _q(subkey, "InstallLocation")

                                    if display_name is None:
                                        continue

                                    # Filter by search term if provided
                                    if input_data.search_term and input_data.search_term.lower() not in display_name.lower():
                                        continue

                                    if display_name not in seen_names:
                                        seen_names.add(display_name)
                                        apps.append({
                                            "name": display_name,
                                            "publisher": publisher,
                                            "version": version,
                                            "install_location": install_location,
                                            "platform": "windows"
                                        })
                                finally:
                                    winreg.CloseKey(subkey)
                                i += 1
                            winreg.CloseKey(key)
                        except OSError:
                            continue
                except ImportError:
                    return ToolOutput(
                        success=False,
                        error={"code": "WINDOWS_API_ERROR", "message": "winreg not available"},
                        duration_ms=0
                    )
            
            elif system == "Darwin":
                # macOS: List applications from /Applications
                try:
                    result = subprocess.run(
                        ["mdfind", "kMDItemContentTypeTree=com.apple.application"],
                        capture_output=True,
                        text=True,
                        timeout=30
                    )
                    if result.returncode == 0:
                        for app_path in result.stdout.strip().split('\n'):
                            if app_path and app_path.endswith('.app'):
                                app_name = app_path.split('/')[-1].replace('.app', '')
                                
                                if input_data.search_term and input_data.search_term.lower() not in app_name.lower():
                                    continue
                                
                                apps.append({
                                    "name": app_name,
                                    "path": app_path,
                                    "platform": "macos"
                                })
                except Exception:
                    pass
            
            else:
                # Linux: Try to list .desktop files
                try:
                    desktop_dirs = [
                        "/usr/share/applications",
                        "/usr/local/share/applications",
                        str(Path.home()) + "/.local/share/applications"
                    ]
                    
                    from pathlib import Path
                    for desktop_dir in desktop_dirs:
                        dir_path = Path(desktop_dir)
                        if dir_path.exists():
                            for desktop_file in dir_path.glob("*.desktop"):
                                try:
                                    content = desktop_file.read_text()
                                    name = None
                                    exec_cmd = None
                                    for line in content.split('\n'):
                                        if line.startswith("Name="):
                                            name = line[5:]
                                        elif line.startswith("Exec="):
                                            exec_cmd = line[5:]
                                    
                                    if name:
                                        if input_data.search_term and input_data.search_term.lower() not in name.lower():
                                            continue
                                        
                                        apps.append({
                                            "name": name,
                                            "exec": exec_cmd,
                                            "desktop_file": str(desktop_file),
                                            "platform": "linux"
                                        })
                                except Exception:
                                    continue
                except Exception:
                    pass
            
            # Sort by name
            apps.sort(key=lambda x: x['name'].lower())
            
            return ToolOutput(
                success=True,
                data={
                    "count": len(apps),
                    "search_term": input_data.search_term,
                    "platform": system,
                    "applications": apps[:100]  # Limit to 100 results
                },
                evidence={"source": "system_application_registry"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "APP_LIST_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: AppListInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success and "applications" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "none", "requires_confirmation": False}


class AppLaunchTool(BaseTool):
    """Launch an application."""
    
    name = "app_launch"
    description = "Launch an application by name or ID"
    input_schema = AppLaunchInput
    
    async def execute(self, input_data: AppLaunchInput) -> ToolOutput:
        """Execute the app launch tool."""
        try:
            system = platform.system()
            app_id = input_data.app_id
            _validate_app_id(app_id)

            if system == "Windows":
                # Windows: resolve the executable without a shell, then launch.
                # `shutil.which` covers PATH; os.startfile handles registered
                # app names / UWP aliases and never goes through cmd.exe.
                try:
                    import win32api
                    import win32con

                    exe_path = shutil.which(app_id)
                    if exe_path:
                        subprocess.Popen([exe_path], shell=False)
                        method = "resolved_executable"
                    else:
                        os.startfile(app_id)
                        method = "startfile"
                    return ToolOutput(
                        success=True,
                        data={
                            "app_id": app_id,
                            "platform": "windows",
                            "method": method
                        },
                        evidence={"source": "windows_launch"}
                    )
                except ImportError:
                    # Fallback without pywin32
                    exe_path = shutil.which(app_id)
                    if exe_path:
                        subprocess.Popen([exe_path], shell=False)
                        method = "resolved_executable"
                    else:
                        os.startfile(app_id)
                        method = "startfile"
                    return ToolOutput(
                        success=True,
                        data={
                            "app_id": app_id,
                            "platform": "windows",
                            "method": method
                        },
                        evidence={"source": "windows_launch"}
                    )
            
            elif system == "Darwin":
                # macOS: Use open command
                subprocess.Popen(["open", "-a", app_id])
                return ToolOutput(
                    success=True,
                    data={
                        "app_id": app_id,
                        "platform": "macos",
                        "method": "open_command"
                    },
                    evidence={"source": "macos_open"}
                )
            
            else:
                # Linux: Try to run the application
                try:
                    # First try as a command
                    subprocess.Popen([app_id])
                    method = "direct_execution"
                except FileNotFoundError:
                    # Try finding it in PATH
                    result = subprocess.run(
                        ["which", app_id],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        exe_path = result.stdout.strip()
                        subprocess.Popen([exe_path])
                        method = "path_execution"
                    else:
                        # Try via xdg-open for desktop files
                        subprocess.Popen(["xdg-open", app_id])
                        method = "xdg_open"
                
                return ToolOutput(
                    success=True,
                    data={
                        "app_id": app_id,
                        "platform": "linux",
                        "method": method
                    },
                    evidence={"source": "linux_execution"}
                )
        
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "APP_LAUNCH_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: AppLaunchInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "medium", "requires_confirmation": True}


class AppFocusTool(BaseTool):
    """Focus/bring an application window to front."""
    
    name = "app_focus"
    description = "Bring an application window to the foreground"
    input_schema = AppFocusInput
    
    async def execute(self, input_data: AppFocusInput) -> ToolOutput:
        """Execute the app focus tool."""
        try:
            system = platform.system()
            app_id = input_data.app_id
            _validate_app_id(app_id)
            
            if system == "Windows":
                # Windows: Use win32gui to find and focus window
                try:
                    import win32gui
                    import win32con
                    
                    def enum_windows(hwnd, results):
                        if win32gui.IsWindowVisible(hwnd):
                            title = win32gui.GetWindowText(hwnd)
                            if app_id.lower() in title.lower():
                                results.append(hwnd)
                        return True
                    
                    windows = []
                    win32gui.EnumWindows(enum_windows, windows)
                    
                    if windows:
                        # Focus the first matching window
                        hwnd = windows[0]
                        if win32gui.IsIconic(hwnd):
                            win32gui.ShowWindow(hwnd, win32con.SW_RESTORE)
                        win32gui.SetForegroundWindow(hwnd)
                        
                        return ToolOutput(
                            success=True,
                            data={
                                "app_id": app_id,
                                "window_handle": hwnd,
                                "platform": "windows"
                            },
                            evidence={"source": "win32gui"}
                        )
                    else:
                        return ToolOutput(
                            success=False,
                            error={"code": "WINDOW_NOT_FOUND", "message": f"No window found matching '{app_id}'"},
                            duration_ms=0
                        )
                except ImportError:
                    return ToolOutput(
                        success=False,
                        error={"code": "WINDOWS_API_ERROR", "message": "pywin32 not available"},
                        duration_ms=0
                    )
            
            elif system == "Darwin":
                # macOS: Use AppleScript to focus application
                try:
                    safe_id = app_id.replace('"', '\\"')
                    script = f'''
                    tell application "{safe_id}"
                        activate
                    end tell
                    '''
                    subprocess.run(["osascript", "-e", script], check=True)
                    return ToolOutput(
                        success=True,
                        data={
                            "app_id": app_id,
                            "platform": "macos"
                        },
                        evidence={"source": "apple_script"}
                    )
                except subprocess.CalledProcessError:
                    return ToolOutput(
                        success=False,
                        error={"code": "APPLESCRIPT_ERROR", "message": f"Failed to focus '{app_id}'"},
                        duration_ms=0
                    )
            
            else:
                # Linux: Use wmctrl or xdotool
                try:
                    result = subprocess.run(
                        ["wmctrl", "-x", "-a", app_id],
                        capture_output=True,
                        text=True
                    )
                    if result.returncode == 0:
                        return ToolOutput(
                            success=True,
                            data={
                                "app_id": app_id,
                                "platform": "linux",
                                "method": "wmctrl"
                            },
                            evidence={"source": "wmctrl"}
                        )
                    else:
                        return ToolOutput(
                            success=False,
                            error={"code": "WMCTRL_ERROR", "message": "wmctrl not available or failed"},
                            duration_ms=0
                        )
                except FileNotFoundError:
                    return ToolOutput(
                        success=False,
                        error={"code": "LINUX_FOCUS_ERROR", "message": "Neither wmctrl nor xdotool available"},
                        duration_ms=0
                    )
        
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "APP_FOCUS_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: AppFocusInput, output: ToolOutput) -> bool:
        """Verify the tool execution result."""
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        """Get policy requirements for this tool."""
        return {"risk_level": "low", "requires_confirmation": False}


class AppCloseTool(BaseTool):
    """Close an application."""
    
    name = "app_close"
    description = "Close an application window or process"
    input_schema = AppCloseInput
    
    async def execute(self, input_data: AppCloseInput) -> ToolOutput:
        """Execute the app close tool."""
        try:
            system = platform.system()
            app_id = input_data.app_id
            force = input_data.force
            _validate_app_id(app_id)
            if "*" in app_id:
                raise ValueError("Wildcard app ids are not allowed")

            if system == "Windows":
                # Windows: Use taskkill or WM_CLOSE
                try:
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
                        method = "taskkill_force"
                    else:
                        # Try graceful close first
                        import win32gui
                        import win32con
                        
                        def enum_windows(hwnd, results):
                            if win32gui.IsWindowVisible(hwnd):
                                title = win32gui.GetWindowText(hwnd)
                                if app_id.lower() in title.lower():
                                    results.append(hwnd)
                            return True
                        
                        windows = []
                        win32gui.EnumWindows(enum_windows, windows)

                        if not windows:
                            return ToolOutput(
                                success=False,
                                error={"code": "WINDOW_NOT_FOUND", "message": f"No window found matching '{app_id}'"},
                                duration_ms=0
                            )
                        for hwnd in windows:
                            win32gui.PostMessage(hwnd, win32con.WM_CLOSE, 0, 0)

                        method = "wm_close"
                    
                    return ToolOutput(
                        success=True,
                        data={
                            "app_id": app_id,
                            "force": force,
                            "platform": "windows",
                            "method": method
                        },
                        evidence={"source": "windows_process_management"}
                    )
                except ImportError:
                    # Fallback to taskkill
                    result = subprocess.run(
                        ["taskkill", "/IM", app_id, "/F" if force else ""],
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
                        data={
                            "app_id": app_id,
                            "force": force,
                            "platform": "windows",
                            "method": "taskkill_fallback"
                        },
                        evidence={"source": "windows_taskkill"}
                    )
            
            elif system == "Darwin":
                # macOS: Use osascript or kill
                if force:
                    subprocess.run(["killall", "-9", app_id], capture_output=True)
                    method = "kill_force"
                else:
                    safe_id = app_id.replace('"', '\\"')
                    script = f'''
                    tell application "{safe_id}"
                        quit
                    end tell
                    '''
                    subprocess.run(["osascript", "-e", script], capture_output=True)
                    method = "apple_script_quit"
                
                return ToolOutput(
                    success=True,
                    data={
                        "app_id": app_id,
                        "force": force,
                        "platform": "macos",
                        "method": method
                    },
                    evidence={"source": "macos_process_management"}
                )
            
            else:
                # Linux: Use pkill or killall
                if force:
                    subprocess.run(["pkill", "-9", "-f", app_id], capture_output=True)
                    method = "pkill_force"
                else:
                    subprocess.run(["pkill", "-f", app_id], capture_output=True)
                    method = "pkill"
                
                return ToolOutput(
                    success=True,
                    data={
                        "app_id": app_id,
                        "force": force,
                        "platform": "linux",
                        "method": method
                    },
                    evidence={"source": "linux_process_management"}
                )
        
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "APP_CLOSE_ERROR", "message": str(e)},
                duration_ms=0
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


def create_app_tools() -> List[BaseTool]:
    """Create instances of all application tools."""
    return [
        AppListTool(),
        AppLaunchTool(),
        AppFocusTool(),
        AppCloseTool(),
    ]
