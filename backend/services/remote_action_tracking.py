"""Shared lifecycle tracking for durable remote-device actions.

Every tracked action is persisted before the operation runs, so a failure can
never leave an untracked effect. Callers commit the started action BEFORE the
operation; on failure they roll back the operation's partial writes, record a
truthful terminal state, and commit again. No helper here grants authority or
retries an uncertain outcome.
"""

from __future__ import annotations

import uuid
from typing import Any, Optional

from sqlalchemy.orm import Session

from backend.runtime.remote_action_lifecycle import (
    EXECUTING,
    FAILED,
    PLANNED,
    UNCERTAIN,
    UNDERSTANDING,
    VERIFYING,
    COMPLETED,
    RemoteActionManager,
)


def request_idempotency_key(data: Any) -> str:
    """Stable key from the request when supplied, otherwise a fresh one."""
    if isinstance(data, dict):
        for name in ("idempotency_key", "client_request_id", "request_id"):
            value = data.get(name)
            if isinstance(value, str) and value.strip():
                return value.strip()[:128]
    return f"auto-{uuid.uuid4().hex}"


def start_remote_action(
    db: Session,
    device_id: str,
    idempotency_key: str,
    *,
    task_id: Optional[str] = None,
) -> dict[str, Any]:
    """Create an action and advance it to EXECUTING (caller commits after)."""
    manager = RemoteActionManager(db)
    action = manager.create(
        idempotency_key=idempotency_key,
        device_id=device_id,
        task_id=task_id,
    )
    if action.get("deduplicated") and action.get("terminal"):
        # A reused key on a resolved action is a replay request, never a new
        # execution. Callers must refuse it rather than re-running an effect.
        result = dict(action)
        result["terminal_replay"] = True
        return result
    action_id = action["action_id"]
    manager.transition(action_id, UNDERSTANDING)
    manager.transition(action_id, PLANNED)
    return manager.transition(action_id, EXECUTING)


def complete_remote_action(
    db: Session, action_id: str, evidence_ref: str,
) -> dict[str, Any]:
    """Verify with durable evidence, then complete. Never assumes success."""
    if not isinstance(evidence_ref, str) or not evidence_ref.strip():
        raise ValueError("evidence_ref is required")
    manager = RemoteActionManager(db)
    manager.transition(action_id, VERIFYING)
    return manager.transition(action_id, COMPLETED, evidence_ref=evidence_ref.strip())


def fail_remote_action(
    db: Session, action_id: str, reason: str, *, uncertain: bool,
) -> dict[str, Any]:
    """Record a failed or uncertain terminal state without replaying."""
    manager = RemoteActionManager(db)
    safe_reason = (reason or "operation_failed")[:200]
    current = manager.get(action_id)
    if current is None:
        return {}
    if uncertain:
        try:
            return manager.transition(action_id, UNCERTAIN, reason=safe_reason)
        except Exception:
            return current  # already terminal; never revive it
    try:
        return manager.transition(action_id, FAILED, reason=safe_reason)
    except Exception:
        return current
