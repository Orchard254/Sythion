"""Phase 9.75 — capability risk split and specialist role revalidation.

The former ``system_observation`` capability bundled pure read-only tools
with HIGH-risk state-mutating controls (``system_power_control`` — lock/
sleep/restart/shutdown, plus brightness/volume setters). This suite pins the
corrected taxonomy:

    SYSTEM_OBSERVATION != SYSTEM_CONTROL

and proves no specialist role inherits control capabilities merely because
they were previously bundled under observation.
"""

import asyncio

import pytest

from backend.agents.specialist import SpecialistContextPacket, SpecialistRuntime, resolve_allowed_tools
from backend.agents.state import ROLE_CAPABILITIES, SpecialistRole
from backend.services.capabilities import CapabilityRegistry, _specs


def _spec(capability_id):
    return next(s for s in _specs() if s.id == capability_id)


CONTROL_TOOLS = set(_spec("system_control").tool_names)


class TestObservationControlSplit:

    def test_both_capabilities_exist_and_are_distinct(self):
        obs = _spec("system_observation")
        ctrl = _spec("system_control")
        assert obs.id != ctrl.id
        assert not (set(obs.tool_names) & set(ctrl.tool_names))

    def test_observation_is_purely_read_only(self):
        # Pinned allowlist: any future mutation-capable tool must NOT be
        # quietly re-added to observation.
        READ_ONLY = {
            "system_time", "system_battery", "system_disk",
            "system_processes", "network_status", "system_active_window",
            "window_list", "system_file_roots", "system_capabilities",
            "system_power", "clipboard_read",
        }
        assert set(_spec("system_observation").tool_names) == READ_ONLY

    def test_control_capability_owns_mutating_system_tools(self):
        assert CONTROL_TOOLS == {
            "system_power_control", "brightness_control", "system_volume",
        }

    def test_control_capability_is_high_risk_permission_gated(self):
        spec = _spec("system_control")
        assert spec.risk_class == "HIGH"
        assert spec.requires_permission is True

    def test_taxonomy_audit_every_spec_classified(self, tool_executor):
        """Audit of ALL capability specs: tools registered, mutating tools
        never live in an *observation* capability, risk classes present."""
        MUTATING_TOOL_NAMES = {
            "system_power_control", "brightness_control", "system_volume",
            "file_create", "file_write", "file_delete", "file_move",
            "file_copy", "app_launch", "app_focus", "app_close",
            "package_install", "package_uninstall",
            "gui_click", "gui_type", "gui_hotkey", "gui_paste", "gui_focus",
            "gui_invoke", "gui_set_value", "gui_batch_form",
            "browser_open_tab", "browser_close_tab", "browser_interact",
            "memory_store", "memory_forget", "memory_link",
            "objective_create", "objective_add_step",
            "objective_complete_step", "objective_update_summary",
            "tool_propose",
        }
        registered = set(tool_executor.list_tools())
        for spec in _specs():
            if spec.id.endswith("_observation") or spec.id == "activity_recall":
                leaked = set(spec.tool_names) & MUTATING_TOOL_NAMES
                assert not leaked, f"{spec.id} bundles mutating tools: {leaked}"
            for tool in spec.tool_names:
                assert tool in registered, (
                    f"{spec.id} references unregistered tool {tool}")
            if spec.risk_class == "HIGH":
                assert spec.requires_permission or spec.id == "reasoning_model"
        reg = CapabilityRegistry(None, tool_executor=tool_executor)
        assert reg.validate() == []  # no MISSING_TOOL / DUPLICATE_ID drift

    def test_unavailable_control_capability(self):
        """An environment whose executor lacks the control tools reports the
        control capability UNAVAILABLE — observation stays available."""
        from unittest.mock import MagicMock

        obs_tools = list(_spec("system_observation").tool_names)
        partial = MagicMock()
        partial.list_tools.return_value = obs_tools
        reg = CapabilityRegistry(None, tool_executor=partial)
        assert reg.resolve_status(_spec("system_observation")) == "AVAILABLE"
        assert reg.resolve_status(_spec("system_control")) == "UNAVAILABLE"


class TestSpecialistRoleRevalidation:

    def test_no_role_binds_system_control(self, tool_executor):
        for role in SpecialistRole:
            caps = ROLE_CAPABILITIES[role]
            assert "system_control" not in caps
            tools = resolve_allowed_tools(list(caps), tool_executor)
            assert not (set(tools) & CONTROL_TOOLS), (
                f"role {role.value} resolves control tools: "
                f"{sorted(set(tools) & CONTROL_TOOLS)}")

    def test_research_does_not_inherit_control(self, tool_executor):
        tools = resolve_allowed_tools(
            list(ROLE_CAPABILITIES[SpecialistRole.RESEARCH]), tool_executor)
        assert "system_power_control" not in tools
        assert "brightness_control" not in tools
        assert "system_volume" not in tools
        # Observation tools ARE still available to RESEARCH.
        assert "system_time" in tools

    def test_control_tool_denied_at_kernel_boundary(self, test_db,
                                                    tool_executor):
        """Even if a model proposed it, a specialist bound to observation
        cannot reach a control tool through SpecialistRuntime."""
        packet = SpecialistContextPacket(
            objective="probe", specialist_agent_id="s1", role="RESEARCH",
            required_capabilities=list(
                ROLE_CAPABILITIES[SpecialistRole.RESEARCH]))
        tools = resolve_allowed_tools(packet.required_capabilities,
                                      tool_executor)
        runtime = SpecialistRuntime(
            db=test_db, agent_id="s1", delegation_id="d1",
            allowed_tools=tools, task_executor=tool_executor, task_id="t1")
        with pytest.raises(Exception):
            asyncio.run(runtime.request_action("system_power_control",
                                               {"action": "lock"}))

    def test_roles_still_have_valid_capabilities_deterministically(self):
        registry_ids = {s.id for s in _specs()}
        snapshots = []
        for role in SpecialistRole:
            caps = ROLE_CAPABILITIES[role]
            assert caps, f"role {role.value} lost all capabilities"
            unknown = set(caps) - registry_ids
            assert not unknown, f"{role.value}: unknown {unknown}"
            snapshots.append(tuple(sorted(caps)))
        # Deterministic: identical across repeated reads.
        again = [tuple(sorted(ROLE_CAPABILITIES[r])) for r in SpecialistRole]
        assert snapshots == again

    def test_seeded_definitions_match_split_taxonomy(self, test_db):
        from backend.agents.service import AgentService

        svc = AgentService(test_db)
        definitions = svc.ensure_definitions()
        import json

        def json_caps(d):
            return set(json.loads(d.capabilities_json))

        by_role = {d.role: json_caps(d) for d in definitions}

        for role in SpecialistRole:
            caps = by_role[role.value]
            assert caps == set(ROLE_CAPABILITIES[role])
            assert "system_control" not in caps
        issues = svc.validate_role_bindings()
        assert not [i for i in issues if i.get("code") == "UNKNOWN_CAPABILITY"]
