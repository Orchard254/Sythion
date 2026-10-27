"""Canonical Capability Registry (Phase 5).

A **Capability** is what Sythion can accomplish; a **Tool** is one concrete
mechanism for it. The registry references the existing ToolExecutor and
provider layers — it NEVER authorizes anything:

    CapabilityRegistry answers "what would be required?"
    The Protected Runtime answers "is this action allowed now?"

Statuses are derived deterministically from runtime sources (registered
tools, OS platform, bridge connectivity, configuration). The LLM can never
invent availability.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

# Canonical status model.
AVAILABLE = "AVAILABLE"
UNAVAILABLE = "UNAVAILABLE"
DISABLED = "DISABLED"
DEGRADED = "DEGRADED"
REQUIRES_PERMISSION = "REQUIRES_PERMISSION"
REQUIRES_CONFIGURATION = "REQUIRES_CONFIGURATION"


@dataclass(frozen=True)
class CapabilitySpec:
    """Static description + deterministic status resolver binding."""

    id: str
    name: str
    category: str
    description: str
    tool_names: tuple
    risk_class: str = "LOW"           # informational only; runtime authorizes
    requires_permission: bool = False  # informational: gated by policy at exec
    requires_external_service: bool = False
    requires_configuration: bool = False
    environments: tuple = ("windows",)
    deterministic: bool = True
    stateful: bool = False


def _specs() -> List[CapabilitySpec]:
    windows_only = ("windows",)
    return [
        # Phase 9.75 risk split: pure observation ONLY. State-mutating
        # system controls live in the separate `system_control` capability
        # so a specialist bound to observation can never reach them.
        CapabilitySpec(
            id="system_observation", name="System Observation", category="system",
            description="Read-only query of live device state: time, battery, disk, "
                        "processes, network, windows, clipboard text, and system metadata.",
            tool_names=("system_time", "system_battery", "system_disk",
                        "system_processes", "network_status",
                        "system_active_window", "window_list",
                        "system_file_roots", "system_capabilities",
                        "system_power", "clipboard_read"),
        ),
        CapabilitySpec(
            id="system_control", name="System Control", category="system",
            description="Mutating OS state control: power state (lock/sleep/restart/"
                        "shutdown), display brightness, and master volume.",
            tool_names=("system_power_control", "brightness_control",
                        "system_volume"),
            risk_class="HIGH", requires_permission=True,
            environments=windows_only,
        ),
        CapabilitySpec(
            id="file_operations", name="File Operations", category="filesystem",
            description="Read/write/move/delete files within approved roots, with verification.",
            tool_names=("file_list", "file_read", "file_create", "file_write",
                        "file_delete", "file_move", "file_copy", "file_search",
                        "file_hash"),
            risk_class="MEDIUM", requires_permission=True,
        ),
        CapabilitySpec(
            id="app_control", name="Application Control", category="desktop",
            description="Launch/focus/close registered Windows applications by identity.",
            tool_names=("app_list", "app_launch", "app_focus", "app_close"),
            risk_class="MEDIUM", environments=windows_only,
        ),
        CapabilitySpec(
            id="package_management", name="Package Management", category="system",
            description="Install/search packages via fixed-argument winget only.",
            tool_names=("package_search", "package_list", "package_install",
                        "package_uninstall"),
            risk_class="HIGH", requires_permission=True,
            environments=windows_only,
        ),
        CapabilitySpec(
            id="gui_automation", name="GUI Automation", category="desktop",
            description="UI-Automation-based window observation and input on visible desktops.",
            tool_names=("gui_observe", "gui_click", "gui_type", "gui_hotkey",
                        "gui_paste", "gui_focus", "gui_invoke", "gui_set_value",
                        "gui_screenshot", "gui_batch_form"),
            risk_class="HIGH", requires_permission=True,
            environments=windows_only, stateful=False,
        ),
        CapabilitySpec(
            id="browser_control", name="Browser Control", category="web",
            description="Tab discovery, page reading and interaction via the local browser extension bridge.",
            tool_names=("browser_tabs", "browser_switch_tab", "browser_open_tab",
                        "browser_close_tab", "browser_read_page",
                        "browser_interact", "browser_screenshot"),
            risk_class="MEDIUM", requires_permission=True,
            requires_external_service=True,
        ),
        # Device/Home integration is not implemented in this version; the UI
        # renders demonstration data and the registry must not claim availability.
        CapabilitySpec(
            id="device_integration", name="Device Integration", category="devices",
            description="Device and home management integration available as device status inventory (not demo data).",
            tool_names=("device_status",),
            risk_class="MEDIUM", requires_permission=True,
            requires_external_service=True,
        ),
        CapabilitySpec(
            id="persistent_memory", name="Persistent Memory", category="memory",
            description="Store/recall/link durable user facts with decay and pruning.",
            tool_names=("memory_store", "memory_recall", "memory_forget",
                        "memory_link", "memory_recall_related", "memory_stats",
                        "memory_export"),
        ),
        CapabilitySpec(
            id="activity_recall", name="Activity Recall", category="memory",
            description="Recall Sythion's own past actions from the immutable ledger.",
            tool_names=("activity_recall",),
        ),
        CapabilitySpec(
            id="goal_planning", name="Goal Planning & Tracking", category="planning",
            description="Create durable goals, validated dependency-aware plans, track evidence-gated progress.",
            tool_names=("objective_create", "objective_add_step",
                        "objective_complete_step", "objective_get_plan",
                        "objective_update_summary"),
        ),
        CapabilitySpec(
            id="voice", name="Voice Interaction", category="interaction",
            description="Local VAD + STT transcription and SAPI5 TTS narration.",
            tool_names=(),
            requires_configuration=True, environments=windows_only,
        ),
        CapabilitySpec(
            id="reasoning_model", name="Reasoning Model", category="model",
            description="LLM reasoning through the configured provider.",
            tool_names=(),
            requires_external_service=True, requires_configuration=True,
        ),
        CapabilitySpec(
            id="self_authoring_tools", name="Tool Authoring Proposals", category="meta",
            description="Propose new tools as inert review artifacts (never executed in-process).",
            tool_names=("tool_propose",),
        ),
    ]


class CapabilityRegistry:
    """Deterministic capability catalog over authoritative runtime sources."""

    def __init__(self, session=None, tool_executor=None):
        self.db = session
        self._executor = tool_executor

    # -- helpers -------------------------------------------------------------

    def _executor_or_default(self):
        if self._executor is not None:
            return self._executor
        try:
            from backend.api.dependencies import get_tool_executor

            return get_tool_executor()
        except Exception:
            return None

    def _registered_tools(self) -> set:
        executor = self._executor_or_default()
        try:
            return set(executor.list_tools())
        except Exception:
            return set()

    def resolve_status(self, spec: CapabilitySpec) -> str:
        """Deterministic status from runtime facts (never LLM-derived)."""
        registered = self._registered_tools()

        missing = [t for t in spec.tool_names if t not in registered]
        if spec.tool_names and len(missing) == len(spec.tool_names):
            return UNAVAILABLE  # implementation entirely absent

        if os.name != "nt" and "windows" in spec.environments:
            return UNAVAILABLE

        if spec.id == "device_integration":
            return UNAVAILABLE  # not implemented; UI renders demonstration data only

        if spec.id == "browser_control":
            try:
                from backend.services.browser_bridge_service import (
                    BrowserBridgeService,
                )

                if not BrowserBridgeService.get_instance().is_connected:
                    return UNAVAILABLE  # extension disconnected
                return AVAILABLE
            except Exception:
                return UNAVAILABLE

        if spec.id == "voice":
            try:
                from backend.core.settings_store import get_setting

                enabled = self.db and get_setting(self.db, "voice_enabled")
                if enabled is not None and enabled != "true":
                    return DISABLED
                stt_path = self.db and get_setting(self.db, "stt_model_path")
                if not stt_path:
                    return REQUIRES_CONFIGURATION
                return AVAILABLE
            except Exception:
                return REQUIRES_CONFIGURATION

        if spec.id == "reasoning_model":
            from backend.core.config import resolve_selected_model
            from backend.core.secrets import has_secret, OLLAMA_API_KEY

            if not (has_secret(OLLAMA_API_KEY) or True):
                return REQUIRES_CONFIGURATION
            if not resolve_selected_model():
                return REQUIRES_CONFIGURATION
            return AVAILABLE  # reachability is Health's job, not availability

        # Default for tool-backed capabilities present on this platform.
        if spec.risk_class == "HIGH":
            return AVAILABLE  # available; execution is permission-gated at runtime
        return AVAILABLE

    # -- public interface ------------------------------------------------------

    def list_capabilities(self, include_details: bool = False) -> List[Dict[str, Any]]:
        out = []
        registered = self._registered_tools()
        for spec in _specs():
            status = self.resolve_status(spec)
            entry = {
                "id": spec.id,
                "name": spec.name,
                "category": spec.category,
                "status": status,
                "risk_class": spec.risk_class,
                "requires_permission": spec.requires_permission,
                "requires_external_service": spec.requires_external_service,
            }
            if include_details:
                entry.update({
                    "description": spec.description,
                    "environments": list(spec.environments),
                    "deterministic": spec.deterministic,
                    "stateful": spec.stateful,
                    "tools_registered": [
                        t for t in spec.tool_names if t in registered
                    ],
                    "tools_missing": [
                        t for t in spec.tool_names if t not in registered
                    ],
                })
            out.append(entry)
        return out

    def get_capability(self, capability_id: str) -> Optional[Dict[str, Any]]:
        for entry in self.list_capabilities(include_details=True):
            if entry["id"] == capability_id:
                return entry
        return None

    def capability_available(self, capability_id: str) -> bool:
        cap = self.get_capability(capability_id)
        return bool(cap and cap["status"] == AVAILABLE)

    def validate(self) -> List[Dict[str, str]]:
        """Consistency checks — reported, never silently repaired."""
        issues: List[Dict[str, str]] = []
        registered = self._registered_tools()
        seen_ids = set()
        for spec in _specs():
            if spec.id in seen_ids:
                issues.append({"capability": spec.id, "code": "DUPLICATE_ID"})
            seen_ids.add(spec.id)
            for tool in spec.tool_names:
                if tool not in registered:
                    issues.append({
                        "capability": spec.id,
                        "code": "MISSING_TOOL",
                        "tool": tool,
                    })
        return issues