"""Stage 2 device registry: identity, last-seen, capabilities, owner-only trust."""

from datetime import datetime

from backend.models.database import RegisteredDevice
from backend.services.device_registry import (
    TRUST_LEVELS,
    get_device,
    list_devices,
    local_capabilities,
    local_device_id,
    register_local_device,
    set_trust_level,
    trust_rank,
)

T1 = datetime(2026, 11, 20, 8, 0, 0)
T2 = datetime(2026, 11, 21, 8, 0, 0)


def test_device_id_is_stable_and_does_not_expose_hostname():
    device_id = local_device_id()
    assert local_device_id() == device_id
    assert device_id.startswith("dev_")
    assert len(device_id) == 24
    assert "hostname" not in device_id.lower()


def test_local_capabilities_are_deterministic_and_bounded():
    caps = local_capabilities()
    assert caps == local_capabilities()
    assert caps
    assert all(isinstance(c, str) and c for c in caps)
    assert len(caps) <= 20


def test_registration_is_idempotent_and_never_raises_trust(test_db):
    first = register_local_device(test_db, now=T1)
    test_db.commit()
    assert first["trust_level"] == "observed"
    assert first["first_seen"].startswith("2026-11-20")

    second = register_local_device(test_db, now=T2)
    test_db.commit()
    assert second["device_id"] == first["device_id"]
    assert second["trust_level"] == "observed"
    assert second["last_seen"].startswith("2026-11-21")
    assert second["first_seen"] == first["first_seen"]
    assert test_db.query(RegisteredDevice).count() == 1


def test_list_and_get_devices(test_db):
    device = register_local_device(test_db, now=T1)
    test_db.commit()
    assert [d["device_id"] for d in list_devices(test_db)] == [device["device_id"]]
    assert get_device(test_db, device["device_id"])["name"] == device["name"]
    assert get_device(test_db, "dev_missing") is None


def test_trust_levels_are_validated(test_db):
    assert list(TRUST_LEVELS) == ["observed", "trusted", "privileged"]
    assert trust_rank("observed") < trust_rank("trusted") < trust_rank("privileged")
    device = register_local_device(test_db, now=T1)
    test_db.commit()

    updated = set_trust_level(test_db, device["device_id"], "trusted")
    test_db.commit()
    assert updated["trust_level"] == "trusted"

    # Re-registration still cannot raise trust further.
    reregistered = register_local_device(test_db, now=T2)
    assert reregistered["trust_level"] == "trusted"


def test_trust_change_rejects_unknown_level_and_device(test_db):
    device = register_local_device(test_db, now=T1)
    test_db.commit()
    try:
        set_trust_level(test_db, device["device_id"], "omnipotent")
        raise AssertionError("unknown trust level accepted")
    except ValueError:
        pass
    try:
        set_trust_level(test_db, "dev_missing", "trusted")
        raise AssertionError("unknown device accepted")
    except LookupError:
        pass


def test_device_endpoints_are_owner_gated(test_client, test_db):
    response = test_client.get("/api/v1/devices")
    assert response.status_code == 200
    assert "devices" in response.json()

    # Local device is not registered in a fresh test DB until startup runs.
    response = test_client.get("/api/v1/devices/local")
    assert response.status_code == 404

    register_local_device(test_db, now=T1)
    test_db.commit()
    response = test_client.get("/api/v1/devices/local")
    assert response.status_code == 200
    body = response.json()
    assert body["device_id"] == local_device_id()
    assert body["trust_level"] == "observed"


def test_trust_endpoint_is_audited_and_persists(test_client, test_db):
    from backend.models.database import TaskEvent

    device = register_local_device(test_db, now=T1)
    test_db.commit()

    response = test_client.put(
        f"/api/v1/devices/{device['device_id']}/trust",
        json={"trust_level": "privileged"},
    )
    assert response.status_code == 200
    assert response.json()["trust_level"] == "privileged"
    assert get_device(test_db, device["device_id"])["trust_level"] == "privileged"

    events = test_db.query(TaskEvent).filter(
        TaskEvent.event_type == "device.trust_changed").all()
    assert len(events) == 1

    response = test_client.put(
        f"/api/v1/devices/{device['device_id']}/trust",
        json={"trust_level": "bogus"},
    )
    assert response.status_code == 400
