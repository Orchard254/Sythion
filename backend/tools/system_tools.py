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
    "system_volume: set/get audio level (only volumeup/volumedown/mute media keys exist)",
    "power control: lock/sleep/restart/shutdown (win+l and ctrl+alt+del are rejected hotkeys)",
    "window enumeration: list open windows (only system_active_window exists)",
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
    """Get list of running processes (redacted)."""
    
    name = "system_processes"
    description = "Get list of running processes (limited, redacted)"
    input_schema = SystemProcessesInput
    
    async def execute(self, input_data: SystemProcessesInput) -> ToolOutput:
        try:
            processes = []
            for proc in psutil.process_iter(['pid', 'name']):
                try:
                    processes.append({
                        "pid": proc.info['pid'],
                        "name": proc.info['name'],
                    })
                except (psutil.NoSuchProcess, psutil.AccessDenied):
                    continue
            
            # Sort by name and limit
            processes.sort(key=lambda x: x['name'].lower())
            processes = processes[:input_data.limit]
            names = sorted(list({p["name"] for p in processes if p.get("name")}))
            model_summary = f"Running processes ({len(processes)} shown): {', '.join(names[:25])}"
            
            return ToolOutput(
                success=True,
                data={
                    "count": len(processes),
                    "limit": input_data.limit,
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


class SystemFileRootsTool(BaseTool):
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
                session.commit()
                session.refresh(tool_proposal)

            model_summary = (
                f"Tool proposal '{proposal.name}' submitted: "
                f"validation passed, sandbox {'passed' if sandbox.passed else 'failed'}. "
                f"Owner must approve via POST /tools/proposals/{tool_proposal.id}/approve "
                f"before it becomes a live tool."
            )

            return ToolOutput(
                success=True,
                data={
                    "proposal_id": tool_proposal.id,
                    "name": proposal.name,
                    "status": tool_proposal.status,
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


# Register all system tools
def create_system_tools() -> list:
    """Create instances of all system read tools."""
    return [
        SystemTimeTool(),
        SystemBatteryTool(),
        SystemDiskTool(),
        SystemProcessesTool(),
        SystemActiveWindowTool(),
        SystemFileRootsTool(),
        SystemCapabilitiesTool(),
        ToolProposeTool(),
    ]
