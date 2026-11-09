"""Minimal strict contract scaffold for Synth's continuity kernel.

This module is intentionally side-effect free.  It defines the typed kernel
contract and its disabled-by-default feature flag only.  It performs no model,
memory, database, routing, tool, or voice work and does not change runtime
behavior.  The flag defaults to ``False`` at import time, so every existing
code path remains unchanged unless a caller explicitly opts in.
"""

from __future__ import annotations

import enum
from dataclasses import asdict, dataclass, field, is_dataclass
from typing import Any, Mapping, Optional, Sequence, Tuple

__all__ = [
    "CONTINUITY_KERNEL_ENABLED",
    "CONTINUITY_KERNEL_FEATURE_FLAG",
    "continuity_kernel_enabled",
    "is_continuity_kernel_enabled",
    "contract_disabled_by_default",
    "ContinuityKernelFeatureFlag",
    "ContinuityKernelStatus",
    "ContinuityKernelMode",
    "ContinuityKernelPhase",
    "ContinuityKernelOutcome",
    "ContinuityKernelDecision",
    "ContinuityKernelConfig",
    "ContinuityKernelContext",
    "ContinuityKernelState",
    "ContinuityKernelSnapshot",
    "ContinuityKernelContract",
    "ContinuityKernelScaffold",
]

# Import-time feature flag.  Deliberately constant and disabled by default so
# that importing this module cannot activate any new behavior.
CONTINUITY_KERNEL_ENABLED: bool = False
CONTINUITY_KERNEL_FEATURE_FLAG: bool = CONTINUITY_KERNEL_ENABLED
CONTINUITY_KERNEL: bool = CONTINUITY_KERNEL_ENABLED
CONTINUITY_KERNEL_FLAG: bool = CONTINUITY_KERNEL_ENABLED
continuity_kernel: bool = CONTINUITY_KERNEL_ENABLED


def is_continuity_kernel_enabled() -> bool:
    """Return whether the continuity kernel contract is enabled."""
    return bool(CONTINUITY_KERNEL_ENABLED)


# Backwards/alternate callable alias requested by some callers.
continuity_kernel_enabled: bool = CONTINUITY_KERNEL_ENABLED


class ContinuityKernelFeatureFlag(enum.Enum):
    """Strict enumeration of continuity-kernel feature-gate states."""

    DISABLED = "disabled"
    ENABLED = "enabled"


class ContinuityKernelStatus(enum.Enum):
    """Kernel lifecycle states.  Strict enumeration (no implicit int coercion)."""

    DISABLED = "disabled"
    IDLE = "idle"
    ACTIVE = "active"
    PAUSED = "paused"
    DEGRADED = "degraded"
    STOPPED = "stopped"


class ContinuityKernelMode(enum.Enum):
    """How the kernel participates in a turn/cycle."""

    DISABLED = "disabled"
    OBSERVE = "observe"
    ADVISE = "advise"
    ACTIVE = "active"


class ContinuityKernelPhase(enum.Enum):
    """Coarse continuity phases used by the scaffold only."""

    INACTIVE = "inactive"
    INITIALIZE = "initialize"
    RESUME = "resume"
    MAINTAIN = "maintain"
    SUSPEND = "suspend"


class ContinuityKernelOutcome(enum.Enum):
    """Outcome classification for kernel-level decisions."""

    NO_OP = "no_op"
    CONTINUE = "continue"
    PAUSE = "pause"
    DEFER = "defer"
    STOP = "stop"


class ContinuityKernelDecision(enum.Enum):
    """Decision kinds produced by the (not-yet-wired) kernel."""

    NO_ACTION = "no_action"
    CONTINUE_WORK = "continue_work"
    PAUSE_WORK = "pause_work"
    REQUEST_INPUT = "request_input"
    STOP_WORK = "stop_work"


@dataclass(frozen=True, slots=True)
class ContinuityKernelConfig:
    """Configuration contract for the continuity kernel scaffold."""

    enabled: bool = CONTINUITY_KERNEL_ENABLED
    mode: ContinuityKernelMode = ContinuityKernelMode.DISABLED
    phase: ContinuityKernelPhase = ContinuityKernelPhase.INACTIVE
    version: str = "0"

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a strict bool")
        if not isinstance(self.mode, ContinuityKernelMode):
            raise TypeError("mode must be a ContinuityKernelMode")
        if not isinstance(self.phase, ContinuityKernelPhase):
            raise TypeError("phase must be a ContinuityKernelPhase")


@dataclass(frozen=True, slots=True)
class ContinuityKernelContext:
    """Read-only context contract carried by the scaffold."""

    session_id: Optional[str] = None
    task_id: Optional[str] = None
    goal_id: Optional[str] = None
    conversation_id: Optional[str] = None
    request: Optional[str] = None
    enabled: bool = CONTINUITY_KERNEL_ENABLED

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a strict bool")


@dataclass(frozen=True, slots=True)
class ContinuityKernelState:
    """State contract for the (disabled by default) kernel."""

    status: ContinuityKernelStatus = ContinuityKernelStatus.DISABLED
    mode: ContinuityKernelMode = ContinuityKernelMode.DISABLED
    active: bool = False
    session_id: Optional[str] = None
    task_id: Optional[str] = None

    def __post_init__(self) -> None:
        if type(self.active) is not bool:
            raise TypeError("active must be a strict bool")
        if not isinstance(self.status, ContinuityKernelStatus):
            raise TypeError("status must be a ContinuityKernelStatus")
        if not isinstance(self.mode, ContinuityKernelMode):
            raise TypeError("mode must be a ContinuityKernelMode")


@dataclass(frozen=True, slots=True)
class ContinuityKernelSnapshot:
    """Point-in-time snapshot contract."""

    enabled: bool = CONTINUITY_KERNEL_ENABLED
    status: ContinuityKernelStatus = ContinuityKernelStatus.DISABLED
    state: Optional[ContinuityKernelState] = None
    context: Optional[ContinuityKernelContext] = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a strict bool")
        if not isinstance(self.status, ContinuityKernelStatus):
            raise TypeError("status must be a ContinuityKernelStatus")
        if self.state is not None and not isinstance(self.state, ContinuityKernelState):
            raise TypeError("state must be ContinuityKernelState or None")
        if self.context is not None and not isinstance(self.context, ContinuityKernelContext):
            raise TypeError("context must be ContinuityKernelContext or None")


@dataclass(frozen=True, slots=True)
class ContinuityKernelContract:
    """Top-level contract object used as the scaffold's exported handle."""

    enabled: bool = CONTINUITY_KERNEL_ENABLED
    status: ContinuityKernelStatus = ContinuityKernelStatus.DISABLED
    config: Optional[ContinuityKernelConfig] = None
    context: Optional[ContinuityKernelContext] = None
    state: Optional[ContinuityKernelState] = None

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a strict bool")
        if not isinstance(self.status, ContinuityKernelStatus):
            raise TypeError("status must be a ContinuityKernelStatus")
        if self.config is not None and not isinstance(self.config, ContinuityKernelConfig):
            raise TypeError("config must be ContinuityKernelConfig or None")
        if self.context is not None and not isinstance(self.context, ContinuityKernelContext):
            raise TypeError("context must be ContinuityKernelContext or None")
        if self.state is not None and not isinstance(self.state, ContinuityKernelState):
            raise TypeError("state must be ContinuityKernelState or None")

    def to_dict(self) -> Mapping[str, Any]:
        def _convert(value: Any) -> Any:
            if isinstance(value, enum.Enum):
                return value.value
            if is_dataclass(value):
                return {key: _convert(getattr(value, key)) for key in value.__dataclass_fields__}
            if isinstance(value, (list, tuple)):
                return [_convert(item) for item in value]
            if isinstance(value, dict):
                return {key: _convert(item) for key, item in value.items()}
            return value

        return _convert(self)


@dataclass(frozen=True, slots=True)
class ContinuityKernelScaffold:
    """Compatibility/alias scaffold handle for the contract."""

    contract: Optional[ContinuityKernelContract] = None
    enabled: bool = CONTINUITY_KERNEL_ENABLED

    def __post_init__(self) -> None:
        if type(self.enabled) is not bool:
            raise TypeError("enabled must be a strict bool")
        if self.contract is not None and not isinstance(self.contract, ContinuityKernelContract):
            raise TypeError("contract must be ContinuityKernelContract or None")


# Compatibility aliases covering likely naming variants.
ContinuityKernel = ContinuityKernelContract
ContinuityKernelHandle = ContinuityKernelContract
ContinuityKernelEnvelope = ContinuityKernelContract
ContinuityKernelDefinition = ContinuityKernelContract
ContinuityKernelSpec = ContinuityKernelContract
ContinuityKernelDescriptor = ContinuityKernelContract
ContinuityContract = ContinuityKernelContract
ContinuityKernelModule = ContinuityKernelScaffold
KernelContract = ContinuityKernelContract
ContinuityKernelConfigAlias = ContinuityKernelConfig
ContinuityKernelStateAlias = ContinuityKernelState


def contract_disabled_by_default() -> bool:
    """Helper for callers/tests checking the import-time default."""
    return CONTINUITY_KERNEL_ENABLED is False
