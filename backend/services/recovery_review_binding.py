"""Non-authorizing, read-only revision binding for recovery review snapshots.

This is a *change detector*, not a signed receipt, authenticated identity,
verified external result, or permission to resume/replay. The revision is
computed from server-read task, event and ownership metadata; callers cannot
supply an authority-bearing revision. A reviewer must obtain a fresh snapshot.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Mapping
from datetime import datetime
from typing import Any

_SCHEMA = "sythion.recovery_review_snapshot.v1"


def _datetime(value: Any) -> str | None:
    if value is None:
        return None
    if not isinstance(value, datetime):
        raise ValueError("invalid snapshot time")
    return value.isoformat(timespec="microseconds")


def calculate_recovery_revision(
    *,
    task_id: str,
    task_version: int,
    task_state: str,
    task_updated_at: datetime,
    last_event_sequence: int,
    owner_version: int | None,
    owner_fencing_token: int | None,
    owner_status: str | None,
    owner_expires_at: datetime | None,
    pending_action: bool,
    active_action: bool,
    preflight: Mapping[str, Any],
) -> str:
    """Stamp strictly validated *observation* metadata, not raw event content.

    No action IDs, owner nonce, prompts, source text, user claims, or secrets
    are hashed/returned. The stamp can detect changed metadata, but it cannot
    authenticate a human or detect edits that leave this metadata unchanged.
    """
    if not isinstance(task_id, str) or not task_id or task_id != task_id.strip():
        raise ValueError("task_id must be a nonblank identifier")
    if type(task_version) is not int or task_version < 1:
        raise ValueError("invalid task version")
    if not isinstance(task_state, str) or not task_state:
        raise ValueError("invalid task state")
    if not isinstance(task_updated_at, datetime):
        raise ValueError("task update time is required")
    if type(last_event_sequence) is not int or last_event_sequence < 0:
        raise ValueError("invalid event sequence")
    if (owner_version is None) != (owner_fencing_token is None):
        raise ValueError("incomplete ownership revision")
    if owner_version is not None and (type(owner_version) is not int or owner_version < 1):
        raise ValueError("invalid owner version")
    if owner_fencing_token is not None and (type(owner_fencing_token) is not int or owner_fencing_token < 1):
        raise ValueError("invalid ownership fence")
    if owner_version is None:
        if owner_status is not None or owner_expires_at is not None:
            raise ValueError("ownership metadata without an owner revision")
    elif not isinstance(owner_status, str) or not owner_status:
        raise ValueError("owner status is required")
    if type(pending_action) is not bool or type(active_action) is not bool:
        raise ValueError("action flags must be boolean")
    if not isinstance(preflight, Mapping):
        raise ValueError("trusted preflight is required")
    reason = preflight.get("reason")
    lease = preflight.get("owner_lease")
    unresolved = preflight.get("unresolved_reasoning_calls")
    clear = preflight.get("preflight_clear")
    if (not isinstance(reason, str) or not reason
            or not isinstance(lease, str) or not lease
            or type(unresolved) is not int or unresolved < 0
            or type(clear) is not bool
            or (clear and reason != "clear_for_explicit_resume")):
        raise ValueError("invalid recovery preflight")
    if preflight.get("task_state") != task_state:
        raise ValueError("stale task state in preflight")

    state = {
        "schema": _SCHEMA,
        "task": [task_id, task_version, task_state, _datetime(task_updated_at)],
        "event_watermark": last_event_sequence,
        "ownership": [owner_version, owner_fencing_token, owner_status, _datetime(owner_expires_at)],
        "effect_presence": [pending_action, active_action],
        "preflight": [reason, lease, unresolved, clear],
    }
    encoded = json.dumps(state, sort_keys=True, separators=(",", ":"), ensure_ascii=True).encode("utf-8")
    return "sha256:" + hashlib.sha256(encoded).hexdigest()


def read_recovery_review_binding(db: Any, task_id: str, preflight: Mapping[str, Any]) -> dict[str, Any]:
    """Read server-side evidence watermarks and bind them to a preflight.

    Caller must have obtained the preflight in the same request. This API is
    *diagnostic*, not transactionally authoritative: a concurrent writer can
    change state immediately. Real authorization must recompute and reverify
    under a task writer lock with a separate authenticated human attestor.
    """
    from sqlalchemy import func
    from backend.models.database import Task, TaskEvent
    from backend.runtime.task_ownership import TaskOwnershipRow

    # A snapshot must not trigger an unexpected autoflush of a caller's work.
    # The normal GET route uses a clean read-only session. Refuse a session
    # carrying pending writes instead of claiming those writes were committed.
    if db.new or db.dirty or db.deleted:
        raise RuntimeError("recovery snapshot requires a clean session")
    with db.no_autoflush:
        task = db.query(Task).filter_by(id=task_id).populate_existing().first()
        if task is None:
            raise LookupError("task not found")
        owner = db.query(TaskOwnershipRow).filter_by(task_id=task_id).populate_existing().first()
        watermark = db.query(func.max(TaskEvent.sequence_num)).filter(TaskEvent.task_id == task_id).scalar()
    revision = calculate_recovery_revision(
        task_id=task_id,
        task_version=task.version,
        task_state=task.state,
        task_updated_at=task.updated_at,
        last_event_sequence=watermark if watermark is not None else 0,
        owner_version=owner.version if owner else None,
        owner_fencing_token=owner.fencing_token if owner else None,
        owner_status=owner.status if owner else None,
        owner_expires_at=owner.expires_at if owner else None,
        pending_action=bool(task.pending_action),
        active_action=bool(task.active_action_id or (owner and owner.active_action_id)),
        preflight=preflight,
    )
    return {
        "schema": _SCHEMA,
        "revision": revision,
        "task_version": task.version,
        "last_event_sequence": watermark if watermark is not None else 0,
        "binding_status": "read_only_observation",
        "authenticated_human": False,
        "authorization_issued": False,
        "uncertainty_cleared": False,
        "automatic_replay_allowed": False,
        "note": "Snapshot revision is not an authenticated decision or a grant. Recheck under the writer lock.",
    }
