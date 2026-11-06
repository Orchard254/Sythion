"""Isolated truthfulness checks for the device integration capability."""

from types import SimpleNamespace

import pytest

import backend.services.capabilities as capabilities


@pytest.mark.parametrize("tools", [[], ["device_status"]])
def test_device_integration_description_and_status(monkeypatch, tools):
    spec = next(s for s in capabilities._specs() if s.id == "device_integration")
    monkeypatch.setattr(capabilities, "_specs", lambda: [spec])
    monkeypatch.setattr(capabilities, "os", SimpleNamespace(name="nt"))
    executor = SimpleNamespace(list_tools=lambda: tools)

    entry = capabilities.CapabilityRegistry(tool_executor=executor).get_capability(
        "device_integration"
    )

    assert entry["status"] == capabilities.UNAVAILABLE
    assert entry["description"] == (
        "Smart-home and remote-device integration is unavailable. "
        "A separate read-only Windows device status inventory tool is provided; "
        "inventory results depend on the system."
    )
    assert spec.tool_names == ("device_status",)
    assert entry["tools_registered"] == tools
    assert entry["requires_permission"] is True
    assert entry["requires_external_service"] is True
    assert entry["risk_class"] == "MEDIUM"
