"""Deprecated shim - import session helpers from :mod:`backend.core.session` instead.

Kept for backward-compatible ``from backend.core.session_history import ...``
imports. New code should import from ``backend.core.session`` directly.
"""

from backend.core.session import (
    SYSTEM_SESSION_PROVENANCE,
    SessionLineageEntry,
    clear_current_session_id,
    get_current_session_id,
    get_session_id,
    get_session_lineage,
    get_session_lineage_ids,
    get_session_provenance,
    get_trusted_session_ids,
    get_trusted_session_lineage,
    is_override_active,
    is_system_generated_session_id,
    is_trusted_session_id,
    list_session_lineage,
    list_trusted_session_ids,
    reset_session_id,
    set_current_session_id,
)

__all__ = [
    "SYSTEM_SESSION_PROVENANCE",
    "SessionLineageEntry",
    "clear_current_session_id",
    "get_current_session_id",
    "get_session_id",
    "get_session_lineage",
    "get_session_lineage_ids",
    "get_session_provenance",
    "get_trusted_session_ids",
    "get_trusted_session_lineage",
    "is_override_active",
    "is_system_generated_session_id",
    "is_trusted_session_id",
    "list_session_lineage",
    "list_trusted_session_ids",
    "reset_session_id",
    "set_current_session_id",
]
