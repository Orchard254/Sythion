"""
System read tools for Sythion (Synth).
Direct OS APIs for reading system state.
"""

import logging
import os
import platform
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional, Generic, TypeVar, Generic, TypeVar

logger = logging.getLogger(__name__)

# Import as a package when running as `backend.main`, or as bare modules
# when tools are loaded directly (tests / scripts run from backend/).
try:
    from backend.tools.executor import BaseTool, handle_tool_errors
    from backend.api.schemas import (
        ToolOutput,
        SystemTimeInput,
        SystemBatteryInput,
        SystemDiskInput,
        SystemProcessesInput,
        SystemActiveWindowInput,
        SystemFileRootsInput,
        SystemCapabilitiesInput,
        SystemVolumeInput,
        SystemPowerInput,
        SystemPowerControlInput,
        BrightnessControlInput,
        NetworkStatusInput,
        WindowListInput,
        BrowserTabsInput,
        BrowserSwitchTabInput,
        BrowserOpenTabInput,
        BrowserCloseTabInput,
        BrowserReadPageInput,
        BrowserInteractInput,
        BrowserScreenshotInput,
        ToolProposeInput,
        ClipboardReadInput,
    )


except ImportError:
    from tools.executor import BaseTool, handle_tool_errors
    from api.schemas import (
        ToolOutput,
        SystemTimeInput,
        SystemBatteryInput,
        SystemDiskInput,
        SystemProcessesInput,
        SystemActiveWindowInput,
        SystemFileRootsInput,
        SystemCapabilitiesInput,
        SystemVolumeInput,
        SystemPowerInput,
        SystemPowerControlInput,
        BrightnessControlInput,
        NetworkStatusInput,
        WindowListInput,
        BrowserTabsInput,
        BrowserSwitchTabInput,
        BrowserOpenTabInput,
        BrowserCloseTabInput,
        BrowserReadPageInput,
        BrowserInteractInput,
        BrowserScreenshotInput,
        ToolProposeInput,
        ClipboardReadInput,
    )




def _tool_domain(name: str) -> str:
    """Infer the capability domain from the tool-name prefix."""
    for prefix, domain in (
        ("system_", "system_read"),
        ("file_", "files"),
        ("app_", "applications"),
        ("package_", "packages"),
        ("gui_", "gui"),
        ("memory_", "memory"),
        ("web_", "web"),
    ):
        if name.startswith(prefix):
            return domain
    return "other"


def _model_visible(reqs: Dict[str, Any], autonomy: str) -> bool:
    """Mirror of model_engine._model_visible_tools: policy-filtered subset."""
    if reqs.get("requires_admin"):
        return False
    if autonomy != "autonomous":
        if (reqs.get("mutating") or reqs.get("destructive")) and not reqs.get("app_internal"):
            return False
        if reqs.get("requires_confirmation"):
            return False
        if reqs.get("risk_level") in {"medium", "high"}:
            return False
    # engine never exposes privacy-, screen-, or UI-automation-sensitive tools,
    # even in autonomous mode.
    if (
        reqs.get("privacy_sensitive")
        or reqs.get("requires_screen_access")
        or reqs.get("requires_ui_automation")
    ):
        return False
    return True


_KNOWN_GAPS = [
    "file_search: search for files by name/content within approved roots",
    "power control: lock/sleep/restart/shutdown (win+l and ctrl+alt+del are rejected hotkeys)",
    "local network status: wifi/ip/connectivity",
    "brightness / display control",
    "per-process CPU/memory usage (system_processes lists only)",
    "OCR observation: gui_observe(use_ocr=True) reports available:false (engine not bundled)",
]


class SystemTimeTool(BaseTool[SystemTimeInput]):
    """Get current system time and date."""
    
    name = "system_time"
    description = "Get the current system time and date"
    input_schema = SystemTimeInput
    
    async def execute(self, input_data: SystemTimeInput) -> ToolOutput:
        now = datetime.now()
        return ToolOutput(
            success=True,
            data={
                "datetime": now.isoformat(),
                "date": now.strftime("%Y-%m-%d"),
                "time": now.strftime("%H:%M:%S"),
                "timezone": datetime.now().astimezone().tzname(),
            },
            evidence={"source": "system_clock"}
        )
    
    async def verify(self, input_data: SystemTimeInput, output: ToolOutput) -> bool:
        return output.success and "datetime" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemBatteryTool(BaseTool[SystemBatteryInput]):
    """Get battery status information."""
    
    name = "system_battery"
    description = "Get battery status and charge level"
    input_schema = SystemBatteryInput
    
    @handle_tool_errors("BATTERY_ERROR")
    async def execute(self, input_data: SystemBatteryInput) -> ToolOutput:
        """Execute the battery status tool."""
        battery = psutil.sensors_battery()
        if battery is None:
            return ToolOutput(
                success=True,
                data={"available": False, "message": "No battery detected"},
                evidence={"source": "psutil"}
            )
        
        return ToolOutput(
            success=True,
            data={
                "available": True,
                "percent": battery.percent,
                "plugged_in": battery.power_plugged,
                "time_left": battery.secsleft if battery.secsleft != psutil.POWER_TIME_UNLIMITED else None,
            },
            evidence={"source": "psutil_sensors"}
        )
    
    async def verify(self, input_data: SystemBatteryInput, output: ToolOutput) -> bool:
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemDiskTool(BaseTool[SystemDiskInput]):
    """Get disk space information."""
    
    name = "system_disk"
    description = "Get disk space usage information"
    input_schema = SystemDiskInput
    
    @handle_tool_errors("DISK_READ_ERROR")
    async def execute(self, input_data: SystemDiskInput) -> ToolOutput:
        path = input_data.path or "/"
        usage = psutil.disk_usage(path)
        
        return ToolOutput(
            success=True,
            data={
                "path": path,
                "total_gb": round(usage.total / (1024**3), 2),
                "used_gb": round(usage.used / (1024**3), 2),
                "free_gb": round(usage.free / (1024**3), 2),
                "percent_used": usage.percent,
            },
            evidence={"source": "psutil_disk", "path": path}
        )
    
    async def verify(self, input_data: SystemDiskInput, output: ToolOutput) -> bool:
        return output.success and "total_gb" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemProcessesTool(BaseTool[SystemProcessesInput]):
    """Get list of running processes with optional CPU/memory stats."""
    
    name = "system_processes"
    description = "Get list of running processes with optional CPU/memory stats"
    input_schema = SystemProcessesInput
    
    @handle_tool_errors("PROCESS_LIST_ERROR")
    async def execute(self, input_data: SystemProcessesInput) -> ToolOutput:
        processes = []
        for proc in psutil.process_iter(['pid', 'name', 'cpu_percent', 'memory_info', 'num_threads', 'ppid']):
            try:
                info = proc.info
                proc_data = {
                    "pid": info['pid'],
                    "name": info['name'],
                }
                
                if input_data.include_cpu:
                    proc_data["cpu_percent"] = info.get('cpu_percent', 0.0) or 0.0
                
                if input_data.include_memory:
                    mem_info = info.get('memory_info')
                    if mem_info:
                        proc_data["memory_mb"] = round(mem_info.rss / (1024 * 1024), 1)
                    else:
                        proc_data["memory_mb"] = 0.0
                
                if input_data.include_children:
                    try:
                        children = proc.children(recursive=False)
                        proc_data["child_count"] = len(children)
                    except (psutil.NoSuchProcess, psutil.AccessDenied):
                        proc_data["child_count"] = 0
                
                processes.append(proc_data)
            except (psutil.NoSuchProcess, psutil.AccessDenied):
                continue
        
        # Sort processes
        sort_key = input_data.sort_by
        if sort_key == "cpu" and input_data.include_cpu:
            processes.sort(key=lambda x: x.get("cpu_percent", 0), reverse=True)
        elif sort_key == "memory" and input_data.include_memory:
            processes.sort(key=lambda x: x.get("memory_mb", 0), reverse=True)
        elif sort_key == "pid":
            processes.sort(key=lambda x: x.get("pid", 0))
        else:
            processes.sort(key=lambda x: x.get("name", "").lower())
        
        # Apply limit
        processes = processes[:input_data.limit]
        
        # Build summary
        names = sorted(list({p["name"] for p in processes if p.get("name")}))
        summary_parts = [f"{len(processes)} shown"]
        
        if input_data.include_cpu:
            total_cpu = sum(p.get("cpu_percent", 0) for p in processes)
            summary_parts.append(f"total CPU: {total_cpu:.1f}%")
        
        if input_data.include_memory:
            total_mem = sum(p.get("memory_mb", 0) for p in processes)
            summary_parts.append(f"total mem: {total_mem:.0f}MB")
        
        model_summary = f"Running processes ({', '.join(summary_parts)}): {', '.join(names[:60])}"
        
        return ToolOutput(
            success=True,
            data={
                "count": len(processes),
                "limit": input_data.limit,
                "include_cpu": input_data.include_cpu,
                "include_memory": input_data.include_memory,
                "processes": processes,
            },
            model_summary=model_summary,
            evidence={"source": "psutil_processes"}
        )
    
    async def verify(self, input_data: SystemProcessesInput, output: ToolOutput) -> bool:
        return output.success and "processes" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "redact_sensitive": True}


class SystemActiveWindowTool(BaseTool[SystemActiveWindowInput]):
    """Get active window information."""
    
    name = "system_active_window"
    description = "Get information about the currently active window"
    input_schema = SystemActiveWindowInput
    
    @handle_tool_errors("WINDOW_READ_ERROR")
    async def execute(self, input_data: SystemActiveWindowInput) -> ToolOutput:
        # Note: Full implementation requires Windows-specific APIs
        # This is a placeholder that will be enhanced with win32gui on Windows
        try:
            import win32gui
            hwnd = win32gui.GetForegroundWindow()
            title = win32gui.GetWindowText(hwnd)
            
            return ToolOutput(
                success=True,
                data={
                    "hwnd": hwnd,
                    "title": title,
                    "is_visible": win32gui.IsWindowVisible(hwnd),
                },
                evidence={"source": "win32gui"}
            )
        except ImportError:
            # Fallback for non-Windows (development/testing)
            return ToolOutput(
                success=True,
                data={
                    "hwnd": None,
                    "title": "N/A (not on Windows)",
                    "platform": platform.system(),
                },
                evidence={"source": "platform_fallback"}
            )
    
    async def verify(self, input_data: SystemActiveWindowInput, output: ToolOutput) -> bool:
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class WindowListTool(BaseTool[WindowListInput]):
    """List all open windows on the system."""

    name = "window_list"
    description = "List all open windows with their titles, handles, and visibility state"
    input_schema = WindowListInput

    @handle_tool_errors("WINDOW_LIST_ERROR")
    async def execute(self, input_data: WindowListInput) -> ToolOutput:
        try:
            import win32gui
            import win32process
        except ImportError:
            return ToolOutput(
                success=True,
                data={
                    "count": 0,
                    "windows": [],
                    "platform": platform.system(),
                    "note": "win32gui not available on this platform",
                },
                evidence={"source": "platform_fallback"},
            )

        windows = []

        def enum_callback(hwnd, _):
            if not win32gui.IsWindowVisible(hwnd):
                if input_data.visible_only:
                    return
                is_visible = False
            else:
                is_visible = True

            if win32gui.IsIconic(hwnd):
                if not input_data.include_minimized:
                    return
                is_minimized = True
            else:
                is_minimized = False

            title = win32gui.GetWindowText(hwnd)
            if not title:
                return

            _, pid = win32process.GetWindowThreadProcessId(hwnd)
            proc_name = None
            try:
                proc_name = psutil.Process(pid).name()
            except Exception:
                proc_name = "unknown"

            windows.append({
                "hwnd": hwnd,
                "title": title,
                "pid": pid,
                "process_name": proc_name,
                "is_visible": is_visible,
                "is_minimized": is_minimized,
            })

        win32gui.EnumWindows(enum_callback, None)

        # Build informative model summary
        top_titles = [f"{w['title']} ({w.get('process_name', 'app')})" for w in windows[:20]]
        summary_str = f"Found {len(windows)} open window(s): {', '.join(top_titles)}"
        if len(windows) > 20:
            summary_str += f" (+{len(windows) - 20} more)"
        summary_str += ". Note: In web browsers (Chrome, Edge), all background tabs inside the same window share that single window's title."

        return ToolOutput(
            success=True,
            data={
                "count": len(windows),
                "windows": windows,
            },
            model_summary=summary_str,
            evidence={"source": "win32gui"},
        )

    async def verify(self, input_data: WindowListInput, output: ToolOutput) -> bool:
        return output.success and "windows" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class BrowserTabsTool(BaseTool[BrowserTabsInput]):
    """List open browser tabs, their titles, and the active tab across Chromium (Chrome, Edge, Brave) and Firefox."""

    name = "browser_tabs"
    description = "List all open browser tabs with their titles, active status, and parent browser windows"
    input_schema = BrowserTabsInput

    async def execute(self, input_data: BrowserTabsInput) -> ToolOutput:
        # 1. If Sythion Browser Bridge extension is connected, query full high-fidelity tabs
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
            bridge = BrowserBridgeService.get_instance()
            if bridge.is_connected:
                ext_tabs = await bridge.get_tabs()
                if ext_tabs:
                    active_tab = next((t.get("title") for t in ext_tabs if t.get("active") or t.get("is_active")), None)
                    # Build a numbered full list — no truncation
                    tab_lines = "\n".join(
                        f"  {i+1}. [tab_id: {t.get('id')}] [{('ACTIVE' if t.get('active') or t.get('is_active') else 'bg')}] "
                        f"{t.get('title', 'Untitled')} | {t.get('url', '')}"
                        for i, t in enumerate(ext_tabs)
                    )

                    summary = (
                        f"Discovered {len(ext_tabs)} open Chrome tab(s) via Sythion Browser Extension.\n"
                        f"ALL TABS (complete list, do NOT truncate):\n{tab_lines}\n"
                        f"Active tab: '{active_tab}'."
                    )

                    return ToolOutput(
                        success=True,
                        data={
                            "count": len(ext_tabs),
                            "active_tab": active_tab,
                            "tabs": ext_tabs,
                            "source": "browser_extension",
                        },
                        model_summary=summary,
                        evidence={"source": "sythion_browser_extension"},
                    )
        except Exception:
            pass

        try:
            import win32gui
            import win32process
        except ImportError:
            return ToolOutput(
                success=True,
                data={"count": 0, "browsers": [], "tabs": [], "platform": platform.system()},
                model_summary="Browser tab discovery is available on Windows.",
                evidence={"source": "platform_fallback"},
            )


        try:
            from backend.tools.gui_tools import _UIAWorker, _UIADriver
        except ImportError:
            try:
                from tools.gui_tools import _UIAWorker, _UIADriver
            except ImportError:
                _UIAWorker = None
                _UIADriver = None

        target_browser = (input_data.browser or "all").lower().strip()

        # Enumerate browser windows
        browser_windows = []
        try:
            def enum_cb(hwnd, _):
                if not win32gui.IsWindowVisible(hwnd):
                    return
                title = win32gui.GetWindowText(hwnd)
                if not title:
                    return
                _, pid = win32process.GetWindowThreadProcessId(hwnd)
                try:
                    pname = psutil.Process(pid).name().lower()
                except Exception:
                    pname = ""

                is_target = False
                if target_browser == "all":
                    is_target = any(b in pname for b in ["chrome", "msedge", "edge", "brave", "firefox"])
                else:
                    is_target = target_browser in pname

                if is_target:
                    browser_windows.append({
                        "hwnd": hwnd,
                        "title": title,
                        "process_name": pname,
                        "pid": pid,
                    })

            win32gui.EnumWindows(enum_cb, None)
        except Exception:
            browser_windows = []

        if not browser_windows:
            return ToolOutput(
                success=True,
                data={"count": 0, "browsers": [], "tabs": []},
                model_summary=f"No active {target_browser} browser windows detected.",
                evidence={"source": "window_enumeration"},
            )

        if _UIAWorker is None:
            # Fallback when UIA worker is not available
            tabs = [{"index": i + 1, "title": w["title"], "is_active": True, "browser": w["process_name"]} for i, w in enumerate(browser_windows)]
            return ToolOutput(
                success=True,
                data={"count": len(tabs), "browsers": browser_windows, "tabs": tabs},
                model_summary=f"Found {len(tabs)} open browser window(s): {', '.join(t['title'] for t in tabs[:20])}",
                evidence={"source": "window_fallback"},
            )

        worker = _UIAWorker()
        worker.start()
        driver = _UIADriver()

        def _inspect_uia():
            import ctypes
            import comtypes
            from comtypes.gen import UIAutomationClient as _uia
            uia = driver._session()


            all_browser_results = []
            flat_tabs = []

            for win_info in browser_windows:
                hwnd = win_info["hwnd"]
                try:
                    # 1. Awake Chromium accessibility engine via MSAA AccessibleObjectFromWindow handshake
                    try:
                        IID_IAccessible = comtypes.GUID("{618736E0-3C3D-11CF-810C-00AA00389B71}")
                        pacc = ctypes.c_void_p()
                        ctypes.windll.oleacc.AccessibleObjectFromWindow(
                            hwnd, 0xFFFFFFFC, ctypes.byref(IID_IAccessible), ctypes.byref(pacc)  # OBJID_CLIENT
                        )
                        if pacc.value:
                            ctypes.windll.ole32.CoTaskMemFree(pacc)
                    except Exception:
                        pass

                    element = uia.ElementFromHandle(hwnd)
                    if not element:
                        continue

                    # 2. Search for TabItem controls AND all top-strip tab elements
                    win_rect = element.CurrentBoundingRectangle
                    win_top = int(win_rect.top)
                    win_bottom = int(win_rect.bottom)
                    tab_strip_bottom = win_top + 160  # Top tab band

                    candidate_elements = []

                    # A. Find all TabItem controls
                    tab_cond = uia.CreatePropertyCondition(
                        _uia.UIA_ControlTypePropertyId, _uia.UIA_TabItemControlTypeId
                    )
                    tab_elements = element.FindAll(2, tab_cond)  # TreeScope_Descendants
                    for t in range(tab_elements.Length):
                        candidate_elements.append(tab_elements.GetElement(t))

                    # B. Find all Custom / Button controls in the top tab strip band
                    for ctype_id in [_uia.UIA_CustomControlTypeId, _uia.UIA_ButtonControlTypeId]:
                        cond = uia.CreatePropertyCondition(_uia.UIA_ControlTypePropertyId, ctype_id)
                        matching = element.FindAll(2, cond)
                        for m in range(min(matching.Length, 100)):
                            elem = matching.GetElement(m)
                            try:
                                elem_rect = elem.CurrentBoundingRectangle
                                elem_name = (elem.CurrentName or "").strip()
                                elem_aid = (elem.CurrentAutomationId or "").strip()
                                # Check if located in top strip and has meaningful tab content
                                if int(elem_rect.top) <= tab_strip_bottom and int(elem_rect.right - elem_rect.left) >= 24:
                                    if elem_name and not any(btn in elem_name.lower() for btn in ["close", "minimize", "maximize", "restore", "search tabs", "new tab", "tab search"]):
                                        candidate_elements.append(elem)
                            except Exception:
                                pass

                    # C. Walk the top tab strip child hierarchy
                    walker = uia.ControlViewWalker
                    child = walker.GetFirstChildElement(element)
                    while child:
                        try:
                            crect = child.CurrentBoundingRectangle
                            if int(crect.top) <= tab_strip_bottom:
                                sub_child = walker.GetFirstChildElement(child)
                                while sub_child:
                                    srect = sub_child.CurrentBoundingRectangle
                                    sname = (sub_child.CurrentName or "").strip()
                                    if sname and int(srect.top) <= tab_strip_bottom and int(srect.right - srect.left) >= 24:
                                        if not any(btn in sname.lower() for btn in ["close", "minimize", "maximize", "restore", "search tabs", "new tab", "tab search"]):
                                            candidate_elements.append(sub_child)
                                    # Walk into sub-containers (e.g. Tab Groups)
                                    sub_sub = walker.GetFirstChildElement(sub_child)
                                    while sub_sub:
                                        ssname = (sub_sub.CurrentName or "").strip()
                                        ssrect = sub_sub.CurrentBoundingRectangle
                                        if ssname and int(ssrect.top) <= tab_strip_bottom and int(ssrect.right - ssrect.left) >= 24:
                                            if not any(btn in ssname.lower() for btn in ["close", "minimize", "maximize", "restore", "search tabs", "new tab", "tab search"]):
                                                candidate_elements.append(sub_sub)
                                        sub_sub = walker.GetNextSiblingElement(sub_sub)
                                    sub_child = walker.GetNextSiblingElement(sub_child)
                        except Exception:
                            pass
                        child = walker.GetNextSiblingElement(child)


                    tabs = []
                    seen_titles = set()
                    ignore_names = {
                        "close", "minimize", "maximize", "restore", "search tabs", "new tab",
                        "tab search", "back", "forward", "reload", "extensions", "side panel",
                        "customise and control google chrome", "customize and control google chrome",
                        "settings and more", "app menu", "downloads", "google chrome", "microsoft edge",
                        "brave", "firefox", "search", "bookmarks", "history", "reading list", "..."
                    }

                    for tab_elem in candidate_elements:
                        try:
                            tab_name = (tab_elem.CurrentName or "").strip()
                            if not tab_name:
                                continue
                            
                            # Clean up tab name (remove " - Google Chrome", " - Microsoft Edge")
                            clean_title = tab_name
                            for suffix in [" - Google Chrome", " - Microsoft Edge", " - Brave", " - Mozilla Firefox"]:
                                if clean_title.endswith(suffix):
                                    clean_title = clean_title[:-len(suffix)].strip()

                            if not clean_title or clean_title.lower() in ignore_names or clean_title in seen_titles:
                                continue
                            seen_titles.add(clean_title)


                            sel_pattern = driver._current_pattern(
                                uia, tab_elem, _uia.UIA_SelectionItemPatternId, _uia.IUIAutomationSelectionItemPattern
                            )
                            is_selected = bool(sel_pattern.CurrentIsSelected) if sel_pattern else False
                            tab_dict = {
                                "index": len(tabs) + 1,
                                "title": clean_title,
                                "raw_title": tab_name,
                                "is_active": is_selected,
                                "browser": win_info["process_name"],
                                "window_title": win_info["title"],
                            }
                            tabs.append(tab_dict)
                            flat_tabs.append(tab_dict)
                        except Exception:
                            continue


                    all_browser_results.append({
                        "browser": win_info["process_name"],
                        "window_title": win_info["title"],
                        "tab_count": len(tabs),
                        "tabs": tabs,
                    })
                except Exception as ex:
                    all_browser_results.append({
                        "browser": win_info["process_name"],
                        "window_title": win_info["title"],
                        "tab_count": 0,
                        "tabs": [],
                        "error": str(ex),
                    })

            return all_browser_results, flat_tabs


        try:
            browser_results, flat_tabs = worker.run(_inspect_uia, timeout=15.0)
        except Exception as e:
            browser_results = []
            flat_tabs = []
        finally:
            worker.stop()

        # If UIA did not extract inner tab items, fallback to top-level browser windows
        if not flat_tabs and browser_windows:
            flat_tabs = [
                {
                    "index": i + 1,
                    "title": w["title"],
                    "is_active": True,
                    "browser": w["process_name"],
                    "window_title": w["title"],
                }
                for i, w in enumerate(browser_windows)
            ]

        active_tab = next((t["title"] for t in flat_tabs if t.get("is_active")), None)
        tab_titles = [t["title"] for t in flat_tabs[:8]]
        summary = f"Discovered {len(flat_tabs)} open browser tab(s) across {len(browser_windows)} window(s)."
        if tab_titles:
            summary += f" Tabs: {', '.join(tab_titles)}"
            if len(flat_tabs) > 8:
                summary += f" (+{len(flat_tabs) - 8} more)"
        if active_tab:
            summary += f" Active tab: '{active_tab}'."

        return ToolOutput(
            success=True,
            data={
                "count": len(flat_tabs),
                "active_tab": active_tab,
                "browsers": browser_results or browser_windows,
                "tabs": flat_tabs,
            },
            model_summary=summary,
            evidence={"source": "uia_tab_inspection"},
        )


    async def verify(self, input_data: BrowserTabsInput, output: ToolOutput) -> bool:
        return output.success and "tabs" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}



class SystemFileRootsTool(BaseTool[SystemFileRootsInput]):
    """Report the approved file roots and current user context."""
    """Report the approved file roots and current user context."""

    name = "system_file_roots"
    description = ("Get the approved file root directories (the only paths file tools may access), "
                   "the current OS user, and the user's home directory")
    input_schema = SystemFileRootsInput

    async def execute(self, input_data: SystemFileRootsInput) -> ToolOutput:
        try:
            from backend.tools.file_tools import _approved_roots
        except ImportError:
            from tools.file_tools import _approved_roots
        roots = [str(r) for r in _approved_roots()]
        return ToolOutput(
            success=True,
            data={
                "approved_roots": roots,
                "user": os.environ.get("USERNAME") or "",
                "home": str(Path.home().resolve()),
            },
            model_summary=f"Approved file roots ({len(roots)}): {', '.join(roots)}",
            evidence={"source": "approved_roots_config"}
        )

    async def verify(self, input_data: SystemFileRootsInput, output: ToolOutput) -> bool:
        return output.success and "approved_roots" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemCapabilitiesTool(BaseTool[SystemCapabilitiesInput]):
    """Report the registered tool inventory, model-visible subsets per autonomy
    mode, and the known capability gaps — the system's self-reflection surface."""

    name = "system_capabilities"
    description = ("List the full registered tool inventory, which tools are model-visible "
                   "in the current autonomy mode, and the known capability gaps, so you can "
                   "reason about what you can do and what is missing")
    input_schema = SystemCapabilitiesInput

    async def execute(self, input_data: SystemCapabilitiesInput) -> ToolOutput:
        try:
            from backend.core.config import settings
            active_autonomy = str(os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level) or "").strip()
            from backend.tools.registry import create_all_tools
            tools = list(create_all_tools())

            # Database-stored ProposedTool implementations are NOT loaded or
            # executed here. The protected runtime contract forbids in-process
            # execution of model/database-authored Python; trusted tools come
            # only from create_all_tools() above.
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "CAPABILITIES_ERROR", "message": str(e)},
                duration_ms=0,
            )

        inventory = []
        by_domain: Dict[str, List[str]] = {}
        for tool in tools:
            reqs = tool.get_policy_requirements() or {}
            flags = sorted(k for k, v in reqs.items() if v and k != "risk_level")
            domain = _tool_domain(tool.name)
            by_domain.setdefault(domain, []).append(tool.name)
            inventory.append({
                "name": tool.name,
                "domain": domain,
                "description": tool.description[:300],
                "risk_level": reqs.get("risk_level", "-"),
                "flags": flags,
                "model_visible": {
                    "guarded": _model_visible(reqs, "guarded"),
                    "autonomous": _model_visible(reqs, "autonomous"),
                },
            })

        visible_guarded = [t["name"] for t in inventory if t["model_visible"]["guarded"]]
        visible_autonomous = [t["name"] for t in inventory if t["model_visible"]["autonomous"]]
        visible_guarded.sort()
        visible_autonomous.sort()

        domain_rows = [
            {"domain": d, "count": len(ns), "tools": sorted(ns)}
            for d, ns in sorted(by_domain.items())
        ]

        domain_brief = ", ".join(
            f"{d}({len(ns)})" for d, ns in sorted(by_domain.items())
        )
        model_summary = (
            f"Capabilities: {len(inventory)} tools registered; {len(visible_guarded)} "
            f"model-visible in guarded mode, {len(visible_autonomous)} in autonomous. "
            f"Domains: {domain_brief}. "
            f"Known gaps: {', '.join(g.split(':')[0] for g in _KNOWN_GAPS)}. "
            f"Call the tool again for the full inventory."
        )

        return ToolOutput(
            success=True,
            data={
                "autonomy_mode": active_autonomy,
                "registered_count": len(inventory),
                "model_visible_count": {
                    "guarded": len(visible_guarded),
                    "autonomous": len(visible_autonomous),
                },
                "model_visible_guarded": visible_guarded,
                "model_visible_autonomous": visible_autonomous,
                "by_domain": domain_rows,
                "tools": inventory,
                "known_gaps": _KNOWN_GAPS,
            },
            model_summary=model_summary,
            evidence={"source": "tool_registry_introspection"}
        )

    async def verify(self, input_data: SystemCapabilitiesInput, output: ToolOutput) -> bool:
        return output.success and "registered_count" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}



class BrowserSwitchTabTool(BaseTool[BrowserSwitchTabInput]):
    """Switch to a specific Chrome/Edge tab instantly by its numeric tab ID.

    Use the tab ID returned by browser_tabs. This directly activates the tab
    via the Sythion Browser Extension — no keyboard cycling needed.
    """

    name = "browser_switch_tab"
    description = (
        "Switch the active browser tab to a specific tab identified by its numeric ID. "
        "Get the tab ID from browser_tabs first, then call this tool with that ID."
    )
    input_schema = BrowserSwitchTabInput

    async def execute(self, input_data: BrowserSwitchTabInput) -> ToolOutput:
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error="Browser extension not connected. Load the Sythion extension in Chrome first.",
                model_summary="Cannot switch tab: Sythion Browser Extension is not connected.",
            )

        try:
            target_tab_id = input_data.tab_id
            try:
                ext_tabs = await bridge.get_tabs()
                if ext_tabs:
                    matching_tab = next((t for t in ext_tabs if t.get("id") == target_tab_id), None)
                    if not matching_tab and 1 <= target_tab_id <= len(ext_tabs):
                        target_tab_id = ext_tabs[target_tab_id - 1].get("id")
            except Exception as exc:
                logger.debug("BrowserSwitchTabTool: tab ID fallback lookup failed: %s: %s",
                             type(exc).__name__, exc)

            result = await bridge.switch_tab(target_tab_id)
            title = result.get("title", f"Tab {target_tab_id}")
            return ToolOutput(
                success=True,
                data={"tab_id": target_tab_id, "title": title},
                model_summary=f"Switched to tab '{title}' (ID: {target_tab_id}) successfully.",
                evidence={"source": "sythion_browser_extension"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error=str(e),
                model_summary=f"Failed to switch to tab {input_data.tab_id}: {e}",
            )

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "mutating": True, "requires_confirmation": False}


class BrowserOpenTabTool(BaseTool[BrowserOpenTabInput]):
    """Open a new Chrome/Edge browser tab at a given URL via the Sythion Browser Extension."""

    name = "browser_open_tab"
    description = "Open a new browser tab at a specified URL. Optionally focus it immediately."
    input_schema = BrowserOpenTabInput

    async def execute(self, input_data: BrowserOpenTabInput) -> ToolOutput:
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error="Browser extension not connected.",
                model_summary="Cannot open tab: Sythion Browser Extension is not connected.",
            )

        try:
            result = await bridge.open_tab(input_data.url)
            tab_id = result.get("tab_id")
            return ToolOutput(
                success=True,
                data={"tab_id": tab_id, "url": input_data.url},
                model_summary=f"Opened new tab at '{input_data.url}' (tab ID: {tab_id}).",
                evidence={"source": "sythion_browser_extension"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error=str(e),
                model_summary=f"Failed to open tab at '{input_data.url}': {e}",
            )

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "mutating": True, "requires_confirmation": False}


class BrowserCloseTabTool(BaseTool[BrowserCloseTabInput]):
    """Close a specific Chrome/Edge tab by its numeric ID via the Sythion Browser Extension."""

    name = "browser_close_tab"
    description = (
        "Close a browser tab identified by its numeric ID. "
        "Get the tab ID from browser_tabs first, then call this tool with that ID."
    )
    input_schema = BrowserCloseTabInput

    async def execute(self, input_data: BrowserCloseTabInput) -> ToolOutput:
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error="Browser extension not connected.",
                model_summary="Cannot close tab: Sythion Browser Extension is not connected.",
            )

        try:
            target_tab_id = input_data.tab_id
            try:
                ext_tabs = await bridge.get_tabs()
                if ext_tabs:
                    matching_tab = next((t for t in ext_tabs if t.get("id") == target_tab_id), None)
                    if not matching_tab and 1 <= target_tab_id <= len(ext_tabs):
                        target_tab_id = ext_tabs[target_tab_id - 1].get("id")
            except Exception as exc:
                logger.debug("BrowserCloseTabTool: tab ID fallback lookup failed: %s: %s",
                             type(exc).__name__, exc)

            await bridge.close_tab(target_tab_id)
            return ToolOutput(
                success=True,
                data={"tab_id": target_tab_id},
                model_summary=f"Closed tab {target_tab_id} successfully.",
                evidence={"source": "sythion_browser_extension"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "CLOSE_TAB_FAILED", "message": str(e)},
                model_summary=f"Failed to close tab {input_data.tab_id}: {e}",
            )


    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "medium", "mutating": True, "requires_confirmation": False}


class BrowserReadPageTool(BaseTool[BrowserReadPageInput]):
    """Read the structured content, headings, and interactive elements of an open browser tab."""

    name = "browser_read_page"
    description = (
        "Read the text content, title, headings, and interactive elements (search boxes, buttons) "
        "of the active or specified browser tab via the Sythion Browser Extension."
    )
    input_schema = BrowserReadPageInput

    async def execute(self, input_data: BrowserReadPageInput) -> ToolOutput:
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error={"code": "EXTENSION_DISCONNECTED", "message": "Browser extension not connected. Load the Sythion extension in Chrome first."},
                model_summary="Cannot read page: Sythion Browser Extension is not connected.",
            )

        try:
            result = await bridge.read_page(input_data.tab_id)
            title = result.get("title", "Untitled")
            url = result.get("url", "")
            headings = result.get("headings", [])
            text_snippet = result.get("text_snippet", "")
            interactive = result.get("interactive_elements", [])

            lines = [f"Page: {title}", f"URL: {url}"]
            if headings:
                heading_lines = "\n".join(f"  • {h.get('text', '')}" for h in headings[:20] if h.get("text"))
                if heading_lines:
                    lines.append(f"Headings:\n{heading_lines}")
            if text_snippet:
                lines.append(f"Content:\n{text_snippet[:8000]}")
            if interactive:
                elem_lines = "\n".join(
                    f"  - [{el.get('type')}] {el.get('label', '')} (selector: `{el.get('selector', '')}`)"
                    for el in interactive[:30]
                )
                lines.append(f"Interactive Elements:\n{elem_lines}")

            formatted_summary = "\n\n".join(lines)
            return ToolOutput(
                success=True,
                data={
                    "title": title,
                    "url": url,
                    "headings": headings,
                    "content": text_snippet,
                    "interactive_elements": interactive,
                },
                model_summary=formatted_summary,
                evidence={"source": "sythion_browser_extension"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "READ_PAGE_FAILED", "message": str(e)},
                model_summary=f"Failed to read page content: {e}",
            )

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class BrowserInteractTool(BaseTool[BrowserInteractInput]):
    """Interact with buttons, search inputs, keys, and scrolling on an open webpage."""

    name = "browser_interact"
    description = (
        "Click buttons, type text into search/input boxes, press keys (e.g. 'Enter'), or scroll "
        "on the active or specified browser tab via the Sythion Browser Extension."
    )
    input_schema = BrowserInteractInput

    async def execute(self, input_data: BrowserInteractInput) -> ToolOutput:
        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error={"code": "EXTENSION_DISCONNECTED", "message": "Browser extension not connected."},
                model_summary="Cannot interact with page: Sythion Browser Extension is not connected.",
            )

        try:
            result = await bridge.interact_page(
                action_type=input_data.action,
                selector=input_data.selector,
                text=input_data.text,
                key=input_data.key,
                tab_id=input_data.tab_id,
            )
            if not result.get("success", False):
                err = result.get("error", "Unknown interaction error")
                return ToolOutput(
                    success=False,
                    error={"code": "INTERACTION_FAILED", "message": err},
                    model_summary=f"Page interaction failed: {err}",
                )

            act = input_data.action
            if act == "type":
                summary = f"Typed '{input_data.text}' into element '{input_data.selector}'"
                if input_data.key:
                    summary += f" and pressed {input_data.key}"
            elif act == "click":
                summary = f"Clicked element '{input_data.selector}' ({result.get('text', '')})"
            elif act == "press_key":
                summary = f"Pressed key '{input_data.key}'"
            elif act == "scroll":
                summary = f"Scrolled page {result.get('direction', 'down')}"
            else:
                summary = f"Executed {act} on page"

            return ToolOutput(
                success=True,
                data=result,
                model_summary=summary,
                evidence={"source": "sythion_browser_extension"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "INTERACTION_ERROR", "message": str(e)},
                model_summary=f"Failed to interact with page: {e}",
            )

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "mutating": True, "requires_confirmation": False}


class BrowserScreenshotTool(BaseTool[BrowserScreenshotInput]):
    """Capture a visual PNG screenshot of an open browser tab."""

    name = "browser_screenshot"
    description = (
        "Capture a visual PNG screenshot of the active or specified browser tab. "
        "Saves the image to disk and returns the file path and image metadata."
    )
    input_schema = BrowserScreenshotInput

    async def execute(self, input_data: BrowserScreenshotInput) -> ToolOutput:
        import base64
        from pathlib import Path
        from datetime import datetime

        try:
            from backend.services.browser_bridge_service import BrowserBridgeService
        except ImportError:
            from services.browser_bridge_service import BrowserBridgeService

        bridge = BrowserBridgeService.get_instance()
        if not bridge.is_connected:
            return ToolOutput(
                success=False,
                error={"code": "EXTENSION_DISCONNECTED", "message": "Browser extension not connected. Load the Sythion extension in Chrome first."},
                model_summary="Cannot take screenshot: Sythion Browser Extension is not connected.",
            )

        try:
            result = await bridge.take_screenshot(tab_id=input_data.tab_id)
            if not result.get("success", False):
                err = result.get("error", "Unknown screenshot error")
                return ToolOutput(
                    success=False,
                    error={"code": "SCREENSHOT_FAILED", "message": err},
                    model_summary=f"Failed to capture browser screenshot: {err}",
                )

            image_base64 = result.get("image_base64", "")
            title = result.get("title", "Browser Screenshot")
            url = result.get("url", "")
            tab_id = result.get("tab_id")

            # Determine destination path
            if input_data.save_path:
                candidate = Path(input_data.save_path).resolve()
                _proto = False
                try:
                    from backend.tools.file_tools import _is_prototype_build as _ipb

                    _proto = bool(_ipb())
                except Exception:
                    _proto = os.environ.get("SYTHION_PROTECT_BUILD", "").strip().lower() in {
                        "1",
                        "true",
                        "yes",
                        "on",
                        "prototype",
                    }
                if _proto:
                    try:
                        from backend.tools.file_tools import _contained as _contained_path
                        from backend.core.storage import data_dir as _browser_data_dir
                    except ImportError:
                        from tools.file_tools import _contained as _contained_path
                        from core.storage import data_dir as _browser_data_dir
                    data_root = Path(_browser_data_dir()).resolve()
                    if not (
                        _contained_path(candidate)
                        or str(candidate).lower().startswith(str(data_root).lower() + os.sep)
                        or candidate == data_root
                    ):
                        return ToolOutput(
                            success=False,
                            error={
                                "code": "TARGET_OUTSIDE_ROOTS",
                                "message": f"Screenshot target is outside approved prototype roots: {candidate}",
                            },
                            model_summary="Screenshot save path rejected (prototype isolation).",
                        )
                target_path = candidate
                target_path.parent.mkdir(parents=True, exist_ok=True)
            else:
                try:
                    from backend.core.storage import data_dir as _sythion_data_dir
                except ImportError:
                    from core.storage import data_dir as _sythion_data_dir
                screenshots_dir = Path(_sythion_data_dir()) / "screenshots"
                screenshots_dir.mkdir(parents=True, exist_ok=True)
                timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
                safe_title = "".join(c for c in title if c.isalnum() or c in (" ", "_", "-")).strip()[:30]
                filename = f"screenshot_{timestamp}_{safe_title or 'tab'}.png"
                target_path = screenshots_dir / filename

            # Decode and save PNG bytes
            img_bytes = base64.b64decode(image_base64)
            target_path.write_bytes(img_bytes)
            size_kb = round(len(img_bytes) / 1024, 1)

            return ToolOutput(
                success=True,
                data={
                    "file_path": str(target_path),
                    "size_bytes": len(img_bytes),
                    "size_kb": size_kb,
                    "title": title,
                    "url": url,
                    "tab_id": tab_id,
                },
                model_summary=f"Captured screenshot of '{title}' ({size_kb} KB) and saved to: {target_path}",
                evidence={"source": "sythion_browser_extension", "file_path": str(target_path)},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "SCREENSHOT_ERROR", "message": str(e)},
                model_summary=f"Failed to capture screenshot: {e}",
            )

    def get_policy_requirements(self) -> Dict[str, Any]:
        # Capturing an observation may create a Sythion-owned artifact, but it
        # does not alter the browser, a user document, or external state. A
        # timeout must therefore never block an otherwise valid completion.
        return {
            "risk_level": "low",
            "mutating": False,
            "read_only": True,
            "requires_confirmation": False,
            "privacy_sensitive": True,
            "redact_sensitive": True,
        }


class SystemVolumeTool(BaseTool[SystemVolumeInput]):



    """Get or set the system volume level.

    Uses the Windows Core Audio API via pycaw to read/write the master volume.
    Read-only when no parameters are provided. Mutating when volume_percentage
    or is_mute is provided.
    """

    name = "system_volume"
    description = "Get or set system volume. Read-only by default; pass volume_percentage (0-100) or is_mute (True/False) to change."
    input_schema = SystemVolumeInput

    @handle_tool_errors("VOLUME_ERROR")
    async def execute(self, input_data: SystemVolumeInput) -> ToolOutput:
        from pycaw.pycaw import AudioUtilities, IAudioEndpointVolume
        from comtypes import CLSCTX_ALL
        from ctypes import POINTER, cast

        speakers = AudioUtilities.GetSpeakers()
        dev = speakers._dev
        iface = dev.Activate(IAudioEndpointVolume._iid_, CLSCTX_ALL, None)
        volume = cast(iface, POINTER(IAudioEndpointVolume))

        # Set volume if requested
        if input_data.volume_percentage is not None:
            level = max(0.0, min(1.0, input_data.volume_percentage / 100.0))
            volume.SetMasterVolumeLevelScalar(level, None)

        # Set mute if requested
        if input_data.is_mute is not None:
            volume.SetMute(int(input_data.is_mute), None)

        # Read current state
        level = volume.GetMasterVolumeLevelScalar()
        muted = volume.GetMute()

        return ToolOutput(
            success=True,
            data={
                "volume_percentage": round(level * 100, 1),
                "is_muted": bool(muted),
            },
            evidence={"source": "pycaw"},
        )

    async def verify(self, input_data: SystemVolumeInput, output: ToolOutput) -> bool:
        return output.success and "volume_percentage" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        # Mutating when setting volume or mute, read-only otherwise
        return {
            "risk_level": "low",
            "mutating": True,
            "requires_confirmation": False,
            "read_only": False,
        }


class ToolProposeTool(BaseTool[ToolProposeInput]):
    """Propose a new tool for validation, static checking, and owner approval.

    Use this when you encounter a capability gap that no existing tool can
    fill. The proposal is validated statically (no forbidden imports, no
    secrets, proper BaseTool interface) and stored for owner review.
    PROTECTED RUNTIME CONTRACT: proposal code is never executed in the core
    process; executable enablement requires a future isolated boundary.
    """

    name = "tool_propose"
    description = ("Propose a new tool by providing its name, description, input schema, "
                   "and Python implementation. The proposal is validated and stored for "
                   "owner review; it is never executed inside the core runtime")

    input_schema = ToolProposeInput

    @handle_tool_errors("TOOL_PROPOSE_ERROR")
    async def execute(self, input_data: ToolProposeInput) -> ToolOutput:
        from backend.services.tool_authoring import (
            ToolProposal,
            validate_tool_proposal,
            static_interface_check,
        )
        from backend.models.database import get_db_manager, ProposedTool

        proposal = ToolProposal(
            name=input_data.name,
            description=input_data.description,
            input_schema=input_data.input_schema,
            implementation=input_data.implementation,
        )

        # Validate
        validation = validate_tool_proposal(proposal)
        if not validation.valid:
            return ToolOutput(
                success=False,
                error={
                    "code": "TOOL_VALIDATION_FAILED",
                    "message": "; ".join(validation.errors),
                },
                duration_ms=0,
            )

        # Static interface check (never executes the implementation)
        check = static_interface_check(proposal)

        # Store
        db_mgr = get_db_manager()
        with db_mgr.get_session() as session:
            tool_proposal = ProposedTool(
                name=proposal.name,
                description=proposal.description,
                input_schema_json=__import__("json").dumps(proposal.input_schema),
                implementation=proposal.implementation,
                validation_errors=__import__("json").dumps([]),
                sandbox_result=__import__("json").dumps(check.model_dump()),
                status="sandbox_passed" if check.passed else "proposed",
            )
            session.add(tool_proposal)
            session.flush()
            proposal_id = tool_proposal.id
            proposal_status = tool_proposal.status
            session.commit()

        model_summary = (
            f"Tool proposal '{proposal.name}' submitted: "
            f"validation passed, static interface check {'passed' if check.passed else 'failed'}. "
            f"The implementation is stored as inert review data and is never executed "
            f"in the core runtime."
        )

        return ToolOutput(
            success=True,
            data={
                "proposal_id": proposal_id,
                "name": proposal.name,
                "status": proposal_status,
                "validation": validation.model_dump(),
                "static_check": check.model_dump(),
            },
            model_summary=model_summary,
            evidence={"source": "tool_proposal"},
        )

    async def verify(self, input_data: ToolProposeInput, output: ToolOutput) -> bool:
        return output.success and "proposal_id" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class SystemPowerTool(BaseTool[SystemPowerInput]):
    """Get power/battery status: percentage, plugged-in state, and time remaining.

    Uses psutil.sensors_battery() on Windows. Returns structured power info
    so the model can reason about power state without a system call.
    """

    name = "system_power"
    description = "Get battery percentage, plugged-in state, and estimated time remaining"
    input_schema = SystemPowerInput

    @handle_tool_errors("POWER_ERROR")
    async def execute(self, input_data: SystemPowerInput) -> ToolOutput:
        battery = psutil.sensors_battery()
        if battery is None:
            return ToolOutput(
                success=True,
                data={"battery_present": False, "message": "No battery detected (desktop system)"},
                model_summary="No battery detected — this is a desktop system.",
                evidence={"source": "psutil.sensors_battery"},
            )

        secs_left = battery.secsleft
        if secs_left < 0:
            time_remaining = None
            time_remaining_str = "unlimited (plugged in)"
        else:
            hours = secs_left // 3600
            minutes = (secs_left % 3600) // 60
            time_remaining = secs_left
            time_remaining_str = f"{hours}h {minutes}m"

        return ToolOutput(
            success=True,
            data={
                "battery_present": True,
                "percentage": battery.percent,
                "power_plugged": battery.power_plugged,
                "secs_left": secs_left if secs_left >= 0 else None,
                "time_remaining_str": time_remaining_str,
            },
            model_summary=(
                f"Battery {battery.percent}%, "
                f"{'plugged in' if battery.power_plugged else 'on battery'}, "
                f"time remaining: {time_remaining_str}."
            ),
            evidence={"source": "psutil.sensors_battery"},
        )

    async def verify(self, input_data: SystemPowerInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class SystemPowerControlTool(BaseTool[SystemPowerControlInput]):
    """Control system power state: lock, sleep, restart, or shutdown.

    Uses PowerShell commands to perform power operations.
    WARNING: restart and shutdown are destructive - ensure user confirms.
    """

    name = "system_power_control"
    description = "Lock workstation, sleep, restart, or shutdown the system"
    input_schema = SystemPowerControlInput

    _VALID_ACTIONS = {"lock", "sleep", "restart", "shutdown"}

    async def execute(self, input_data: SystemPowerControlInput) -> ToolOutput:
        import subprocess

        action = input_data.action.lower().strip()
        delay = input_data.delay_seconds

        if action not in self._VALID_ACTIONS:
            return ToolOutput(
                success=False,
                error={
                    "code": "INVALID_ACTION",
                    "message": f"Invalid action '{action}'. Must be one of: {', '.join(sorted(self._VALID_ACTIONS))}"
                },
                duration_ms=0,
            )

        try:
            if action == "lock":
                cmd = "rundll32.exe user32.dll,LockWorkStation"
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0 and result.stderr:
                    return ToolOutput(
                        success=False,
                        error={"code": "LOCK_FAILED", "message": result.stderr.strip()},
                        duration_ms=0,
                    )
                return ToolOutput(
                    success=True,
                    data={"action": "lock", "executed": True},
                    model_summary="Workstation locked.",
                    evidence={"command": cmd, "returncode": result.returncode},
                )

            elif action == "sleep":
                cmd = "rundll32.exe powrprof.dll,SetSuspendState 0,1,0"
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=10
                )
                if result.returncode != 0 and result.stderr:
                    return ToolOutput(
                        success=False,
                        error={"code": "SLEEP_FAILED", "message": result.stderr.strip()},
                        duration_ms=0,
                    )
                return ToolOutput(
                    success=True,
                    data={"action": "sleep", "executed": True},
                    model_summary="System entering sleep mode.",
                    evidence={"command": cmd, "returncode": result.returncode},
                )

            elif action == "restart":
                if delay > 0:
                    cmd = f"Start-Sleep -Seconds {delay}; Restart-Computer -Force"
                else:
                    cmd = "Restart-Computer -Force"
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0 and result.stderr:
                    return ToolOutput(
                        success=False,
                        error={"code": "RESTART_FAILED", "message": result.stderr.strip()},
                        duration_ms=0,
                    )
                delay_msg = f" in {delay} seconds" if delay > 0 else " immediately"
                return ToolOutput(
                    success=True,
                    data={"action": "restart", "executed": True, "delay_seconds": delay},
                    model_summary=f"System restarting{delay_msg}.",
                    evidence={"command": cmd, "returncode": result.returncode},
                )

            elif action == "shutdown":
                if delay > 0:
                    cmd = f"Start-Sleep -Seconds {delay}; Stop-Computer -Force"
                else:
                    cmd = "Stop-Computer -Force"
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0 and result.stderr:
                    return ToolOutput(
                        success=False,
                        error={"code": "SHUTDOWN_FAILED", "message": result.stderr.strip()},
                        duration_ms=0,
                    )
                delay_msg = f" in {delay} seconds" if delay > 0 else " immediately"
                return ToolOutput(
                    success=True,
                    data={"action": "shutdown", "executed": True, "delay_seconds": delay},
                    model_summary=f"System shutting down{delay_msg}.",
                    evidence={"command": cmd, "returncode": result.returncode},
                )

        except subprocess.TimeoutExpired:
            return ToolOutput(
                success=False,
                error={"code": "TIMEOUT", "message": f"Power command timed out for action: {action}"},
                duration_ms=0,
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "POWER_CONTROL_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: SystemPowerControlInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "high", "requires_confirmation": True, "read_only": False}


class BrightnessControlTool(BaseTool[BrightnessControlInput]):
    """Get or set display brightness.

    Uses WMI (WmiMonitorBrightness/WmiMonitorBrightnessMethods) via PowerShell.
    Read-only for 'get' action, mutating for 'set' action.
    Returns current brightness level and available levels.
    """

    name = "brightness_control"
    description = "Get or set display brightness (0-100)"
    input_schema = BrightnessControlInput

    async def execute(self, input_data: BrightnessControlInput) -> ToolOutput:
        import subprocess

        action = input_data.action.lower().strip()

        if action == "get":
            try:
                cmd = (
                    "(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightness).CurrentBrightness"
                )
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0:
                    error_msg = result.stderr.strip() if result.stderr else "Failed to get brightness"
                    return ToolOutput(
                        success=False,
                        error={"code": "BRIGHTNESS_GET_FAILED", "message": error_msg},
                        duration_ms=0,
                    )
                brightness_str = result.stdout.strip()
                if not brightness_str:
                    return ToolOutput(
                        success=False,
                        error={"code": "NO_BRIGHTNESS_DATA", "message": "No brightness data returned (monitor may not support WMI brightness)"},
                        duration_ms=0,
                    )
                brightness = int(brightness_str)
                return ToolOutput(
                    success=True,
                    data={"brightness": brightness, "action": "get"},
                    model_summary=f"Display brightness is {brightness}%.",
                    evidence={"source": "WmiMonitorBrightness", "raw_output": brightness_str},
                )
            except ValueError:
                return ToolOutput(
                    success=False,
                    error={"code": "INVALID_BRIGHTNESS", "message": f"Could not parse brightness value: {brightness_str}"},
                    duration_ms=0,
                )
            except Exception as e:
                return ToolOutput(
                    success=False,
                    error={"code": "BRIGHTNESS_ERROR", "message": str(e)},
                    duration_ms=0,
                )

        elif action == "set":
            if input_data.brightness is None:
                return ToolOutput(
                    success=False,
                    error={"code": "MISSING_BRIGHTNESS", "message": "brightness parameter is required when action='set'"},
                    duration_ms=0,
                )
            brightness = input_data.brightness
            try:
                cmd = (
                    f"(Get-WmiObject -Namespace root\\wmi -Class WmiMonitorBrightnessMethods)"
                    f".WmiSetBrightness(1, {brightness})"
                )
                result = subprocess.run(
                    ["powershell", "-Command", cmd],
                    capture_output=True, text=True, timeout=15
                )
                if result.returncode != 0:
                    error_msg = result.stderr.strip() if result.stderr else "Failed to set brightness"
                    return ToolOutput(
                        success=False,
                        error={"code": "BRIGHTNESS_SET_FAILED", "message": error_msg},
                        duration_ms=0,
                    )
                return ToolOutput(
                    success=True,
                    data={"brightness": brightness, "action": "set"},
                    model_summary=f"Display brightness set to {brightness}%.",
                    evidence={"source": "WmiMonitorBrightnessMethods", "brightness": brightness},
                )
            except Exception as e:
                return ToolOutput(
                    success=False,
                    error={"code": "BRIGHTNESS_ERROR", "message": str(e)},
                    duration_ms=0,
                )

        else:
            return ToolOutput(
                success=False,
                error={
                    "code": "INVALID_ACTION",
                    "message": f"Invalid action '{action}'. Must be 'get' or 'set'."
                },
                duration_ms=0,
            )

    async def verify(self, input_data: BrightnessControlInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "read_only": False, "mutating": True}


class NetworkStatusTool(BaseTool[NetworkStatusInput]):
    """Get network connection status: interfaces, IPs, and connectivity.

    Uses psutil to enumerate network interfaces and their stats.
    Read-only: returns interface names, addresses, and connection state.
    """

    name = "network_status"
    description = "Get network interface status, IP addresses, and connection state"
    input_schema = NetworkStatusInput

    @handle_tool_errors("NETWORK_ERROR")
    async def execute(self, input_data: NetworkStatusInput) -> ToolOutput:
        addrs = psutil.net_if_addrs()
        stats = psutil.net_if_stats()

        interfaces = []
        for name, addr_list in addrs.items():
            stat = stats.get(name)
            is_up = stat.isup if stat else False
            speed = stat.speed if stat else 0

            ipv4 = None
            ipv6 = None
            mac = None
            for addr in addr_list:
                if addr.family.name == "AF_INET":
                    ipv4 = addr.address
                elif addr.family.name == "AF_INET6":
                    ipv6 = addr.address
                elif addr.family.name == "AF_LINK":
                    mac = addr.address

            interfaces.append({
                "name": name,
                "is_up": is_up,
                "speed_mbps": speed,
                "ipv4": ipv4,
                "ipv6": ipv6,
                "mac": mac,
            })

        connected_count = sum(1 for i in interfaces if i["is_up"])
        primary = next((i for i in interfaces if i["is_up"] and i["ipv4"]), None)

        return ToolOutput(
            success=True,
            data={
                "interfaces": interfaces,
                "connected_count": connected_count,
                "total_count": len(interfaces),
                "primary_interface": primary["name"] if primary else None,
                "primary_ipv4": primary["ipv4"] if primary else None,
            },
            model_summary=(
                f"{connected_count}/{len(interfaces)} interfaces up. "
                f"Primary: {primary['name']} ({primary['ipv4']})" if primary
                else f"{connected_count}/{len(interfaces)} interfaces up, no IPv4 found."
            ),
            evidence={"source": "psutil.net_if_addrs"},
        )

    async def verify(self, input_data: NetworkStatusInput, output: ToolOutput) -> bool:
        return output.success and "interfaces" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class ClipboardReadTool(BaseTool[ClipboardReadInput]):
    """
    Read the current clipboard contents.

    Uses ctypes to access Windows clipboard via OpenClipboard/GetClipboardData.
    Returns text content if the clipboard contains CF_UNICODETEXT, otherwise
    reports the clipboard format type.
    """

    name = "clipboard_read"
    description = "Read the current text contents of the Windows clipboard"
    input_schema = ClipboardReadInput

    async def execute(self, input_data: ClipboardReadInput) -> ToolOutput:
        import ctypes

        CF_UNICODETEXT = 13

        user32 = ctypes.windll.user32
        kernel32 = ctypes.windll.kernel32

        # Set proper signatures so 64-bit pointers are not truncated to 32-bit.
        user32.OpenClipboard.argtypes = [ctypes.c_void_p]
        user32.OpenClipboard.restype = ctypes.c_int
        user32.IsClipboardFormatAvailable.argtypes = [ctypes.c_uint]
        user32.IsClipboardFormatAvailable.restype = ctypes.c_int
        user32.GetClipboardData.argtypes = [ctypes.c_uint]
        user32.GetClipboardData.restype = ctypes.c_void_p
        user32.CloseClipboard.restype = ctypes.c_int
        kernel32.GlobalLock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalLock.restype = ctypes.c_void_p
        kernel32.GlobalUnlock.argtypes = [ctypes.c_void_p]
        kernel32.GlobalUnlock.restype = ctypes.c_int

        try:
            if not user32.OpenClipboard(0):
                return ToolOutput(
                    success=False,
                    error={"code": "CLIPBOARD_ERROR", "message": "Failed to open clipboard"},
                )

            try:
                if not user32.IsClipboardFormatAvailable(CF_UNICODETEXT):
                    return ToolOutput(
                        success=True,
                        data={
                            "content": None,
                            "format": "non-text",
                            "message": "Clipboard does not contain text data",
                        },
                        model_summary="Clipboard contains non-text data or is empty",
                    )

                handle = user32.GetClipboardData(CF_UNICODETEXT)
                if not handle:
                    return ToolOutput(
                        success=False,
                        error={"code": "CLIPBOARD_ERROR", "message": "Failed to get clipboard data"},
                    )

                ptr = kernel32.GlobalLock(handle)
                if not ptr:
                    return ToolOutput(
                        success=False,
                        error={"code": "CLIPBOARD_ERROR", "message": "Failed to lock clipboard data"},
                    )

                try:
                    text = ctypes.c_wchar_p(ptr).value
                    if text is None:
                        text = ""
                finally:
                    kernel32.GlobalUnlock(handle)

                content_length = len(text)
                truncated = content_length > 10000
                display_text = text[:10000] + "..." if truncated else text

                return ToolOutput(
                    success=True,
                    data={
                        "content": display_text,
                        "length": content_length,
                        "truncated": truncated,
                        "format": "unicode_text",
                    },
                    model_summary=f"Clipboard: {content_length} chars" + (" (truncated)" if truncated else ""),
                    evidence={"source": "Win32 OpenClipboard/GetClipboardData"},
                )
            finally:
                user32.CloseClipboard()

        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "CLIPBOARD_ERROR", "message": str(e)},
            )

    async def verify(self, input_data: ClipboardReadInput, output: ToolOutput) -> bool:
        return output.success and output.data is not None

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": False,
            "privacy_sensitive": True,
        }


class DeviceStatusTool(BaseTool[SystemTimeInput]):
    """Read-only inventory of installed Windows devices and their status."""

    name = "device_status"
    description = "List installed Windows devices and their current status via PnP."
    input_schema = SystemTimeInput

    @handle_tool_errors("DEVICE_STATUS_ERROR")
    async def execute(self, input_data):
        if os.name != "nt":
            return ToolOutput(success=False, error={"code": "UNSUPPORTED_PLATFORM", "message": "Device status is Windows-only"})
        import json
        import subprocess

        try:
            completed = subprocess.run(
                ["powershell", "-NoProfile", "-Command", "Get-PnpDevice | Select-Object -First 25 Status,Class,FriendlyName | ConvertTo-Json -Compress"],
                capture_output=True,
                text=True,
                timeout=15,
                check=False,
            )
        except Exception as exc:  # noqa: BLE001
            return ToolOutput(success=False, error={"code": "DEVICE_STATUS_ERROR", "message": str(exc)})

        if completed.returncode != 0:
            return ToolOutput(success=False, error={"code": "DEVICE_STATUS_ERROR", "message": completed.stderr.strip() or "PowerShell failed"})

        try:
            parsed = json.loads(completed.stdout) if completed.stdout.strip() else []
        except json.JSONDecodeError:
            return ToolOutput(success=False, error={"code": "DEVICE_STATUS_PARSE_ERROR", "message": "Unexpected PowerShell output"})

        if isinstance(parsed, dict):
            parsed = [parsed]

        return ToolOutput(
            success=True,
            data={"devices": parsed, "count": len(parsed)},
            model_summary=f"Device status: {len(parsed)} device(s) returned",
            evidence={"source": "Get-PnpDevice"},
        )

    async def verify(self, input_data, output: ToolOutput) -> bool:
        return bool(output.success)

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "low",
            "requires_confirmation": False,
            "mutating": False,
            "requires_admin": False,
            "privacy_sensitive": False,
        }


# Register all system tools
def create_system_tools() -> list:
    """Create instances of all system read tools."""
    return [
        SystemTimeTool(),
        SystemBatteryTool(),
        SystemDiskTool(),
        SystemProcessesTool(),
        SystemActiveWindowTool(),
        WindowListTool(),
        BrowserTabsTool(),
        BrowserSwitchTabTool(),
        BrowserOpenTabTool(),
        BrowserCloseTabTool(),
        BrowserReadPageTool(),
        BrowserInteractTool(),
        BrowserScreenshotTool(),
        SystemVolumeTool(),

        SystemPowerTool(),
        SystemPowerControlTool(),
        BrightnessControlTool(),
        NetworkStatusTool(),
        SystemFileRootsTool(),
        SystemCapabilitiesTool(),
        ToolProposeTool(),
        ClipboardReadTool(),
        DeviceStatusTool(),
    ]


# ---------------------------------------------------------------------------
# Prototype-only fail-closed handling for host-data observation tools.
# ---------------------------------------------------------------------------

def _is_prototype_build() -> bool:
    try:
        from backend.core.secrets import is_prototype_build as _proto
    except Exception:
        try:
            from core.secrets import is_prototype_build as _proto
        except Exception:
            _proto = None
    if _proto is None:
        return True
    try:
        return bool(_proto())
    except Exception:
        return True


_PROTOTYPE_HOST_DATA_TOOL_CLASSES = (
    SystemProcessesTool,
    SystemActiveWindowTool,
    WindowListTool,
    BrowserTabsTool,
    BrowserReadPageTool,
    BrowserScreenshotTool,
    ClipboardReadTool,
)


def _prototype_host_data_guard(method):
    async def wrapped(self, input_data):
        if _is_prototype_build():
            return ToolOutput(
                success=False,
                error={
                    "code": "PROTOTYPE_HOST_DATA_DISABLED",
                    "message": "Host data observation is disabled in prototype builds.",
                },
                duration_ms=0,
            )
        return await method(self, input_data)
    wrapped.__name__ = getattr(method, "__name__", "execute")
    wrapped.__doc__ = getattr(method, "__doc__", None)
    return wrapped


for _cls in _PROTOTYPE_HOST_DATA_TOOL_CLASSES:
    if hasattr(_cls, "execute") and callable(getattr(_cls, "execute", None)):
        _cls.execute = _prototype_host_data_guard(_cls.execute)

    _orig_policy = getattr(_cls, "get_policy_requirements", None)
    if callable(_orig_policy):
        def _make_policy(orig):
            def _policy(self):
                reqs = orig(self)
                if isinstance(reqs, dict) and _is_prototype_build():
                    reqs = dict(reqs)
                    reqs["privacy_sensitive"] = True
                return reqs
            return _policy
        _cls.get_policy_requirements = _make_policy(_orig_policy)
