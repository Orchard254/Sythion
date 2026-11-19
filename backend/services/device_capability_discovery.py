"""Per-device capability discovery for the Synth OS.

Merges three independent truths into one read-only projection that the owner can
see and act on:

  1. global capability status from the canonical CapabilityRegistry (can this
     capability run at all here?);
  2. the device's owner grants (was it explicitly allowed?);
  3. the device's trust floor (is it trusted enough for that capability?).

Statuses are deliberately four-valued:

  unavailable  the capability cannot run here (or has no implementation);
  restricted   the device's trust level is below the capability's floor;
  available    trust is sufficient but the owner has not granted it yet;
  granted      trust is sufficient AND the owner granted it — effective access.

This is a *description*, never an authorization: `allowed` mirrors `granted`
but no code path grants permission from this module. Grants/trust are still
written only through the audited owner endpoints in `remote_access`.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy.orm import Session

from backend.services.capabilities import (
    DISABLED,
    REQUIRES_CONFIGURATION,
    UNAVAILABLE,
    CapabilityRegistry,
)
from backend.services.device_registry import get_device, trust_rank
from backend.services.remote_access import (
    CAPABILITY_MIN_TRUST,
    list_grants,
)

# Remote capability -> canonical capability id whose status determines whether
# the underlying capability can actually run. A None mapping means the remote
# capability is a Core service with no tool-backed global spec.
_CAPABILITY_GLOBAL_ID = {
    "agent.status": "system_observation",
    "devices.read": "device_integration",
    "conversation.write": "reasoning_model",
    "goals.submit": "goal_planning",
}

# Remote capabilities with no implementation on the Core yet. Reported as
# unavailable rather than silently appearing grantable.
_NOT_IMPLEMENTED = frozenset({"messages.read"})

STATUS_UNAVAILABLE = "unavailable"
STATUS_RESTRICTED = "restricted"
STATUS_AVAILABLE = "available"
STATUS_GRANTED = "granted"


def describe_device_capabilities(db: Session, device_id: str) -> dict[str, Any]:
    """Return the per-device capability projection. Raises LookupError if unknown."""
    device = get_device(db, device_id)
    if device is None:
        raise LookupError("unknown device")

    registry = CapabilityRegistry(db)
    global_status = {
        entry["id"]: entry["status"]
        for entry in registry.list_capabilities(include_details=False)
    }
    grants = set(list_grants(db, device_id))
    device_trust = device["trust_level"]
    device_rank = trust_rank(device_trust)

    described = []
    for capability, floor in CAPABILITY_MIN_TRUST.items():
        global_id = _CAPABILITY_GLOBAL_ID.get(capability)
        global_state = global_status.get(global_id) if global_id else None
        blocked_globally = (
            capability in _NOT_IMPLEMENTED
            or global_state in (UNAVAILABLE, DISABLED, REQUIRES_CONFIGURATION)
        )
        floor_rank = trust_rank(floor)

        if blocked_globally:
            status = STATUS_UNAVAILABLE
            reason = "capability_unavailable"
        elif device_rank < floor_rank:
            status = STATUS_RESTRICTED
            reason = "insufficient_trust"
        elif capability in grants:
            status = STATUS_GRANTED
            reason = "ok"
        else:
            status = STATUS_AVAILABLE
            reason = "no_grant"

        described.append({
            "capability": capability,
            "status": status,
            "granted": capability in grants,
            "min_trust": floor,
            "global_capability": global_id,
            "global_status": global_state,
            "allowed": status == STATUS_GRANTED,
            "reason": reason,
        })

    described.sort(key=lambda entry: entry["capability"])
    return {
        "device_id": device_id,
        "trust_level": device_trust,
        "capabilities": described,
        "note": (
            "Read-only discovery. 'granted' is the only state with effective "
            "access; grants and trust are changed only by audited owner actions."
        ),
    }
