"""
System read tools for Sythion (Synth).
Direct OS APIs for reading system state.
"""

import os
import platform
import psutil
from datetime import datetime
from pathlib import Path
from typing import Dict, Any, Optional

# Import as a package when running as `backend.main`, or as bare modules
# when tools are loaded directly (tests / scripts run from backend/).
try:
    from backend.tools.executor import BaseTool
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
        ToolProposeInput,
    )
except ImportError:
    from tools.executor import BaseTool
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
        ToolProposeInput,
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
    "local network status: wifi/ip/connectivity (only web_search/web_fetch exist)",
    "brightness / display control",
    "per-process CPU/memory usage (system_processes lists only)",
    "OCR observation: gui_observe(use_ocr=True) reports available:false (engine not bundled)",
]


class SystemTimeTool(BaseTool):
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


class SystemBatteryTool(BaseTool):
    """Get battery status information."""
    
    name = "system_battery"
    description = "Get battery status and charge level"
    input_schema = SystemBatteryInput
    
    async def execute(self, input_data: SystemBatteryInput) -> ToolOutput:
        """Execute the battery status tool."""
        try:
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
        except Exception as e:
            # No battery sensor is normal on desktops; treat as success only
            # for that case. Other psutil errors are real failures.
            if "No such device" in str(e) or "not available" in str(e).lower():
                return ToolOutput(
                    success=True,
                    data={"available": False, "message": "No battery detected"},
                    evidence={"source": "psutil"}
                )
            return ToolOutput(
                success=False,
                error={"code": "BATTERY_ERROR", "message": str(e)},
                duration_ms=0
            )
    
    async def verify(self, input_data: SystemBatteryInput, output: ToolOutput) -> bool:
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemDiskTool(BaseTool):
    """Get disk space information."""
    
    name = "system_disk"
    description = "Get disk space usage information"
    input_schema = SystemDiskInput
    
    async def execute(self, input_data: SystemDiskInput) -> ToolOutput:
        try:
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "DISK_READ_ERROR", "message": str(e)}
            )
    
    async def verify(self, input_data: SystemDiskInput, output: ToolOutput) -> bool:
        return output.success and "total_gb" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class SystemProcessesTool(BaseTool):
    """Get list of running processes with optional CPU/memory stats."""
    
    name = "system_processes"
    description = "Get list of running processes with optional CPU/memory stats"
    input_schema = SystemProcessesInput
    
    async def execute(self, input_data: SystemProcessesInput) -> ToolOutput:
        try:
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
            
            model_summary = f"Running processes ({', '.join(summary_parts)}): {', '.join(names[:25])}"
            
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "PROCESS_LIST_ERROR", "message": str(e)}
            )
    
    async def verify(self, input_data: SystemProcessesInput, output: ToolOutput) -> bool:
        return output.success and "processes" in (output.data or {})
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "redact_sensitive": True}


class SystemActiveWindowTool(BaseTool):
    """Get active window information."""
    
    name = "system_active_window"
    description = "Get information about the currently active window"
    input_schema = SystemActiveWindowInput
    
    async def execute(self, input_data: SystemActiveWindowInput) -> ToolOutput:
        try:
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "WINDOW_READ_ERROR", "message": str(e)}
            )
    
    async def verify(self, input_data: SystemActiveWindowInput, output: ToolOutput) -> bool:
        return output.success
    
    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False}


class WindowListTool(BaseTool):
    """List all open windows on the system."""

    name = "window_list"
    description = "List all open windows with their titles, handles, and visibility state"
    input_schema = WindowListInput

    async def execute(self, input_data: WindowListInput) -> ToolOutput:
        try:
            import win32gui
            import win32process

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
                windows.append({
                    "hwnd": hwnd,
                    "title": title,
                    "pid": pid,
                    "is_visible": is_visible,
                    "is_minimized": is_minimized,
                })

            win32gui.EnumWindows(enum_callback, None)

            return ToolOutput(
                success=True,
                data={
                    "count": len(windows),
                    "windows": windows,
                },
                evidence={"source": "win32gui"},
            )
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "WINDOW_LIST_ERROR", "message": str(e)},
            )

    async def verify(self, input_data: WindowListInput, output: ToolOutput) -> bool:
        return output.success and "windows" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class SystemFileRootsTool(BaseTool):
    """Report the approved file roots and current user context."""
    """Report the approved file roots and current user context."""

    name = "system_file_roots"
    description = ("Get the approved file root directories (the only paths file tools may access), "
                   "the current OS user, and the user's home directory")
    input_schema = SystemFileRootsInput

    async def execute(self, input_data: SystemFileRootsInput) -> ToolOutput:
        try:
            from tools.file_tools import _approved_roots
        except ImportError:
            from backend.tools.file_tools import _approved_roots
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


class SystemCapabilitiesTool(BaseTool):
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
            active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
            from backend.tools.registry import create_all_tools
            tools = list(create_all_tools())

            # Also include dynamically loaded tools from the database
            try:
                from backend.models.database import ProposedTool, get_db_manager
                db_mgr = get_db_manager()
                with db_mgr.get_session() as session:
                    approved = session.query(ProposedTool).filter_by(status="enabled").all()
                    existing_names = {t.name for t in tools}
                    for proposal in approved:
                        if proposal.name not in existing_names:
                            namespace = {"__builtins__": __builtins__}
                            exec(proposal.implementation, namespace)
                            for obj in namespace.values():
                                if (
                                    isinstance(obj, type)
                                    and hasattr(obj, "execute")
                                    and not getattr(obj, "__abstractmethods__", None)
                                    and hasattr(obj, "name")
                                ):
                                    tools.append(obj())
                                    break
            except Exception:
                pass
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
                "description": tool.description[:140],
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


class SystemVolumeTool(BaseTool):
    """Get or set the system volume level.

    Uses the Windows Core Audio API via pycaw to read/write the master volume.
    Read-only when no parameters are provided. Mutating when volume_percentage
    or is_mute is provided.
    """

    name = "system_volume"
    description = "Get or set system volume. Read-only by default; pass volume_percentage (0-100) or is_mute (True/False) to change."
    input_schema = SystemVolumeInput

    async def execute(self, input_data: SystemVolumeInput) -> ToolOutput:
        try:
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
        except ImportError:
            return ToolOutput(
                success=False,
                error={"code": "PYCAW_MISSING", "message": "pycaw package not installed"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "VOLUME_ERROR", "message": str(e)},
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


class ToolProposeTool(BaseTool):
    """Propose a new tool for validation, sandbox testing, and owner approval.

    Use this when you encounter a capability gap that no existing tool can
    fill. The proposal will be validated (no forbidden imports, no secrets,
    proper BaseTool interface), sandbox-tested, and require owner approval
    before becoming a live tool.
    """

    name = "tool_propose"
    description = ("Propose a new tool by providing its name, description, input schema, "
                   "and Python implementation. The proposal is validated, sandbox-tested, "
                   "and requires owner approval before becoming live")
    input_schema = ToolProposeInput

    async def execute(self, input_data: ToolProposeInput) -> ToolOutput:
        try:
            from backend.services.tool_authoring import (
                ToolProposal,
                validate_tool_proposal,
                sandbox_test,
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

            # Sandbox test
            sandbox = sandbox_test(proposal)

            # Store
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                tool_proposal = ProposedTool(
                    name=proposal.name,
                    description=proposal.description,
                    input_schema_json=__import__("json").dumps(proposal.input_schema),
                    implementation=proposal.implementation,
                    validation_errors=__import__("json").dumps([]),
                    sandbox_result=__import__("json").dumps(sandbox.model_dump()),
                    status="sandbox_passed" if sandbox.passed else "proposed",
                )
                session.add(tool_proposal)
                session.flush()
                proposal_id = tool_proposal.id
                proposal_status = tool_proposal.status
                session.commit()

            model_summary = (
                f"Tool proposal '{proposal.name}' submitted: "
                f"validation passed, sandbox {'passed' if sandbox.passed else 'failed'}. "
                f"Owner must approve via POST /tools/proposals/{proposal_id}/approve "
                f"before it becomes a live tool."
            )

            return ToolOutput(
                success=True,
                data={
                    "proposal_id": proposal_id,
                    "name": proposal.name,
                    "status": proposal_status,
                    "validation": validation.model_dump(),
                    "sandbox": sandbox.model_dump(),
                },
                model_summary=model_summary,
                evidence={"source": "tool_proposal"},
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "TOOL_PROPOSE_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: ToolProposeInput, output: ToolOutput) -> bool:
        return output.success and "proposal_id" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class SystemPowerTool(BaseTool):
    """Get power/battery status: percentage, plugged-in state, and time remaining.

    Uses psutil.sensors_battery() on Windows. Returns structured power info
    so the model can reason about power state without a system call.
    """

    name = "system_power"
    description = "Get battery percentage, plugged-in state, and estimated time remaining"
    input_schema = SystemPowerInput

    async def execute(self, input_data: SystemPowerInput) -> ToolOutput:
        try:
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "POWER_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: SystemPowerInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


class SystemPowerControlTool(BaseTool):
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


class BrightnessControlTool(BaseTool):
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
        return {"risk_level": "low", "requires_confirmation": False, "read_only": False}


class NetworkStatusTool(BaseTool):
    """Get network connection status: interfaces, IPs, and connectivity.

    Uses psutil to enumerate network interfaces and their stats.
    Read-only: returns interface names, addresses, and connection state.
    """

    name = "network_status"
    description = "Get network interface status, IP addresses, and connection state"
    input_schema = NetworkStatusInput

    async def execute(self, input_data: NetworkStatusInput) -> ToolOutput:
        try:
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
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "NETWORK_ERROR", "message": str(e)},
                duration_ms=0,
            )

    async def verify(self, input_data: NetworkStatusInput, output: ToolOutput) -> bool:
        return output.success and "interfaces" in (output.data or {})

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "read_only": True}


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
        SystemVolumeTool(),
        SystemPowerTool(),
        SystemPowerControlTool(),
        BrightnessControlTool(),
        NetworkStatusTool(),
        SystemFileRootsTool(),
        SystemCapabilitiesTool(),
        ToolProposeTool(),
    ]
