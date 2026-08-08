"""
Tests for SystemPowerTool, SystemPowerControlTool, BrightnessControlTool, and NetworkStatusTool.

Contract: system info/control tools using psutil and WMI.
"""

import pytest

from backend.tools.system_tools import SystemPowerTool, SystemPowerControlTool, BrightnessControlTool, NetworkStatusTool
from backend.api.schemas import SystemPowerInput, SystemPowerControlInput, BrightnessControlInput, NetworkStatusInput, ToolOutput


@pytest.fixture
def power_tool():
    return SystemPowerTool()


@pytest.fixture
def network_tool():
    return NetworkStatusTool()


class TestSystemPower:
    def test_returns_success(self, power_tool):
        """Should return success=True."""
        import asyncio
        output = asyncio.run(power_tool.execute(SystemPowerInput()))
        assert output.success is True

    def test_has_battery_fields(self, power_tool):
        """Should return battery_present, percentage, power_plugged."""
        import asyncio
        output = asyncio.run(power_tool.execute(SystemPowerInput()))
        data = output.data
        assert "battery_present" in data
        assert "percentage" in data
        assert "power_plugged" in data
        assert "time_remaining_str" in data

    def test_percentage_range(self, power_tool):
        """Percentage should be 0-100 if battery present."""
        import asyncio
        output = asyncio.run(power_tool.execute(SystemPowerInput()))
        if output.data["battery_present"]:
            assert 0 <= output.data["percentage"] <= 100

    def test_verify_returns_true(self, power_tool):
        """Verify should return True for successful output."""
        import asyncio
        output = asyncio.run(power_tool.execute(SystemPowerInput()))
        assert asyncio.run(power_tool.verify(SystemPowerInput(), output)) is True

    def test_policy_read_only(self, power_tool):
        """Should be read-only with no confirmation."""
        reqs = power_tool.get_policy_requirements()
        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False
        assert reqs["read_only"] is True


class TestNetworkStatus:
    def test_returns_success(self, network_tool):
        """Should return success=True."""
        import asyncio
        output = asyncio.run(network_tool.execute(NetworkStatusInput()))
        assert output.success is True

    def test_has_interfaces(self, network_tool):
        """Should return interfaces list and counts."""
        import asyncio
        output = asyncio.run(network_tool.execute(NetworkStatusInput()))
        data = output.data
        assert "interfaces" in data
        assert "connected_count" in data
        assert "total_count" in data
        assert isinstance(data["interfaces"], list)

    def test_interfaces_have_required_fields(self, network_tool):
        """Each interface should have name, is_up, ipv4."""
        import asyncio
        output = asyncio.run(network_tool.execute(NetworkStatusInput()))
        for iface in output.data["interfaces"]:
            assert "name" in iface
            assert "is_up" in iface
            assert "ipv4" in iface
            assert "ipv6" in iface
            assert "mac" in iface
            assert "speed_mbps" in iface

    def test_connected_count_consistent(self, network_tool):
        """connected_count should equal sum of is_up interfaces."""
        import asyncio
        output = asyncio.run(network_tool.execute(NetworkStatusInput()))
        data = output.data
        up_count = sum(1 for i in data["interfaces"] if i["is_up"])
        assert data["connected_count"] == up_count
        assert data["total_count"] == len(data["interfaces"])

    def test_verify_returns_true(self, network_tool):
        """Verify should return True for successful output."""
        import asyncio
        output = asyncio.run(network_tool.execute(NetworkStatusInput()))
        assert asyncio.run(network_tool.verify(NetworkStatusInput(), output)) is True

    def test_policy_read_only(self, network_tool):
        """Should be read-only with no confirmation."""
        reqs = network_tool.get_policy_requirements()
        assert reqs["risk_level"] == "none"
        assert reqs["requires_confirmation"] is False
        assert reqs["read_only"] is True


class TestSystemPowerControl:
    @pytest.fixture
    def power_control_tool(self):
        return SystemPowerControlTool()

    def test_invalid_action_rejected(self, power_control_tool):
        """Should reject invalid action names."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="invalid_action")
        ))
        assert output.success is False
        assert output.error["code"] == "INVALID_ACTION"
        assert "invalid_action" in output.error["message"]

    def test_lock_action_accepted(self, power_control_tool):
        """Lock action should be accepted (may fail in CI without display)."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="lock")
        ))
        # Lock may fail in headless CI, but the action should be recognized
        assert output.success is True or output.error.get("code") == "LOCK_FAILED"

    def test_sleep_action_accepted(self, power_control_tool):
        """Sleep action should be accepted (may fail in CI)."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="sleep")
        ))
        # Sleep may fail in headless CI, but the action should be recognized
        assert output.success is True or output.error.get("code") == "SLEEP_FAILED"

    def test_restart_action_accepted(self, power_control_tool):
        """Restart action should be accepted with delay."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="restart", delay_seconds=0)
        ))
        # In CI, this might actually restart or fail - we just test the schema works
        assert output.success is True or output.error.get("code") in ("RESTART_FAILED", "TIMEOUT")

    def test_shutdown_action_accepted(self, power_control_tool):
        """Shutdown action should be accepted with delay."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="shutdown", delay_seconds=0)
        ))
        # In CI, this might actually shutdown or fail - we just test the schema works
        assert output.success is True or output.error.get("code") in ("SHUTDOWN_FAILED", "TIMEOUT")

    def test_delay_seconds_validated(self, power_control_tool):
        """Delay should be validated (0-3600)."""
        import asyncio
        # Negative delay should fail validation
        with pytest.raises(Exception):
            power_control_tool.execute(SystemPowerControlInput(action="lock", delay_seconds=-1))

    def test_verify_returns_bool(self, power_control_tool):
        """Verify should return a boolean."""
        import asyncio
        output = asyncio.run(power_control_tool.execute(
            SystemPowerControlInput(action="lock")
        ))
        result = asyncio.run(power_control_tool.verify(
            SystemPowerControlInput(action="lock"), output
        ))
        assert isinstance(result, bool)

    def test_policy_requires_confirmation(self, power_control_tool):
        """Power control should require confirmation (high risk)."""
        reqs = power_control_tool.get_policy_requirements()
        assert reqs["risk_level"] == "high"
        assert reqs["requires_confirmation"] is True
        assert reqs["read_only"] is False

    def test_valid_actions_set(self, power_control_tool):
        """Should have exactly 4 valid actions."""
        assert power_control_tool._VALID_ACTIONS == {"lock", "sleep", "restart", "shutdown"}


class TestBrightnessControl:
    @pytest.fixture
    def brightness_tool(self):
        return BrightnessControlTool()

    def test_get_brightness_returns_output(self, brightness_tool):
        """Get action should return a ToolOutput."""
        import asyncio
        output = asyncio.run(brightness_tool.execute(
            BrightnessControlInput(action="get")
        ))
        # May succeed or fail depending on monitor support
        assert isinstance(output, ToolOutput)

    def test_invalid_action_rejected(self, brightness_tool):
        """Should reject invalid action names."""
        import asyncio
        output = asyncio.run(brightness_tool.execute(
            BrightnessControlInput(action="invalid_action")
        ))
        assert output.success is False
        assert output.error["code"] == "INVALID_ACTION"

    def test_set_requires_brightness(self, brightness_tool):
        """Set action should require brightness parameter."""
        import asyncio
        output = asyncio.run(brightness_tool.execute(
            BrightnessControlInput(action="set")
        ))
        assert output.success is False
        assert output.error["code"] == "MISSING_BRIGHTNESS"

    def test_brightness_range_validated(self, brightness_tool):
        """Brightness should be validated (0-100)."""
        import asyncio
        # Negative brightness should fail validation
        with pytest.raises(Exception):
            brightness_tool.execute(BrightnessControlInput(action="set", brightness=-1))

    def test_brightness_max_validated(self, brightness_tool):
        """Brightness > 100 should fail validation."""
        import asyncio
        with pytest.raises(Exception):
            brightness_tool.execute(BrightnessControlInput(action="set", brightness=101))

    def test_verify_returns_bool(self, brightness_tool):
        """Verify should return a boolean."""
        import asyncio
        output = asyncio.run(brightness_tool.execute(
            BrightnessControlInput(action="get")
        ))
        result = asyncio.run(brightness_tool.verify(
            BrightnessControlInput(action="get"), output
        ))
        assert isinstance(result, bool)

    def test_policy_low_risk(self, brightness_tool):
        """Brightness control should be low risk."""
        reqs = brightness_tool.get_policy_requirements()
        assert reqs["risk_level"] == "low"
        assert reqs["requires_confirmation"] is False
        assert reqs["read_only"] is False
