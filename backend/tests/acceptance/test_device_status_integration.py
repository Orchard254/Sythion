"""Acceptance gate: real local device status integration."""

import pytest


@pytest.mark.asyncio
async def test_device_status_reads_real_device_inventory():
    from backend.tools.system_tools import DeviceStatusTool

    result = await DeviceStatusTool().execute({})
    assert result.success is True
    assert isinstance(result.data["devices"], list)
    assert result.data["count"] > 0
    assert all("FriendlyName" in device for device in result.data["devices"])
