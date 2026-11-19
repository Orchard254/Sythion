"""Per-device capability discovery: global status x grants x trust floor."""

from datetime import datetime

from backend.models.database import DeviceGrant
from backend.services.device_capability_discovery import describe_device_capabilities
from backend.services.device_registry import register_local_device, set_trust_level
from backend.services.remote_access import grant_capability

NOW = datetime(2026, 11, 20, 8, 0, 0)


def _device(test_db, trust="observed"):
    device = register_local_device(test_db, now=NOW)
    device_id = device["device_id"]
    if trust != "observed":
        set_trust_level(test_db, device_id, trust)
    test_db.commit()
    return device_id


def _by_name(result, capability):
    return next(c for c in result["capabilities"] if c["capability"] == capability)


def test_unknown_device_raises(test_db):
    try:
        describe_device_capabilities(test_db, "dev_missing")
        raise AssertionError("unknown device accepted")
    except LookupError:
        pass


def test_default_projection_is_four_state_and_read_only(test_db):
    device_id = _device(test_db)
    result = describe_device_capabilities(test_db, device_id)

    assert result["device_id"] == device_id
    assert result["trust_level"] == "observed"
    statuses = {c["status"] for c in result["capabilities"]}
    assert statuses <= {"unavailable", "restricted", "available", "granted"}
    assert len(result["capabilities"]) == 6  # all remote capabilities reported
    assert all(c["allowed"] == (c["status"] == "granted") for c in result["capabilities"])
    assert "Read-only discovery" in result["note"]
    assert test_db.query(DeviceGrant).count() == 0  # discovery writes nothing


def test_trust_gate_produces_restricted(test_db):
    device_id = _device(test_db)
    result = describe_device_capabilities(test_db, device_id)
    # goals.submit has a privileged floor but the device is only observed.
    submit = _by_name(result, "goals.submit")
    assert submit["status"] == "restricted"
    assert submit["reason"] == "insufficient_trust"
    assert submit["min_trust"] == "privileged"
    assert submit["granted"] is False
    assert submit["allowed"] is False


def test_global_unavailable_wins_even_when_granted(test_db):
    device_id = _device(test_db, trust="privileged")
    grant_capability(test_db, device_id, "messages.read")
    grant_capability(test_db, device_id, "devices.read")
    test_db.commit()

    result = describe_device_capabilities(test_db, device_id)
    messages = _by_name(result, "messages.read")
    devices = _by_name(result, "devices.read")
    # messages.read has no implementation; devices.read maps to the
    # CapabilityRegistry's hard-coded UNAVAILABLE device_integration spec.
    assert messages["status"] == "unavailable"
    assert messages["reason"] == "capability_unavailable"
    assert devices["status"] == "unavailable"
    assert devices["global_capability"] == "device_integration"
    assert devices["allowed"] is False
    assert messages["granted"] is True and messages["allowed"] is False


def test_grant_and_trust_produce_granted(test_db):
    device_id = _device(test_db, trust="privileged")
    result = describe_device_capabilities(test_db, device_id)
    before = _by_name(result, "goals.submit")
    assert before["status"] == "available"
    assert before["reason"] == "no_grant"

    grant_capability(test_db, device_id, "goals.submit")
    test_db.commit()
    after = _by_name(describe_device_capabilities(test_db, device_id),
                     "goals.submit")
    assert after["status"] == "granted"
    assert after["granted"] is True
    assert after["allowed"] is True
    assert after["reason"] == "ok"


def test_endpoint_returns_projection(test_client, test_db):
    device_id = _device(test_db)
    response = test_client.get(f"/api/v1/devices/{device_id}/capabilities")
    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == device_id
    assert all("status" in c and "granted" in c for c in body["capabilities"])


def test_endpoint_unknown_device_returns_404(test_client, test_db):
    response = test_client.get("/api/v1/devices/dev_missing/capabilities")
    assert response.status_code == 404
