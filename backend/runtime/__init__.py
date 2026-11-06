"""Protected runtime kernel: explicit interfaces for the Sythion runtime.

Dependency direction (see ):

    Interfaces / UI  ->  Intelligence  ->  Runtime Interfaces (this package)
        ->  Protected Runtime implementations  ->  Operating System

Intelligence components depend on the Protocols defined in
``runtime.interfaces``; they never touch module globals, raw DB sessions for
runtime writes, or adapter singletons.
"""

from backend.runtime.interfaces import (
    EventLedger,
    LeaseManager,
    ModelClient,
    TaskStateStore,
)
from backend.runtime.events import DbEventLedger
from backend.runtime.leases import LeaseManager as DbLeaseManager

__all__ = [
    "EventLedger",
    "TaskStateStore",
    "LeaseManager",
    "DbLeaseManager",
    "ModelClient",
    "DbEventLedger",
]
