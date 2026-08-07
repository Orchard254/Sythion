"""
Owner-configurable task/audit retention and complete-record purge.

Retention: tasks (and their full audit event records) older than the
owner-configured `retention_days` setting (default 90) can be purged. The
episodic conversation transcript (`conversation_turns`) is bounded by the same
window: every expired turn -- including orphans whose task was already purged --
is swept so the table cannot grow forever. Durable memory (`memory_entries`,
`memory_edges`) is exempt: it is not a transcript.

Purge: a complete-record purge deletes the task row, every event row for that
task, and that task's conversation turns, then writes a non-sensitive purge
receipt (task ID, reason, purge time, event count). Receipts never contain
task content, request text, or other sensitive material.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy.orm import Session

from backend.models.database import (
    ConversationTurn,
    ProposedSkill,
    PurgeReceipt,
    Task,
    TaskEvent,
)

logger = logging.getLogger(__name__)

DEFAULT_RETENTION_DAYS = 90


def get_retention_days(db: Session) -> int:
    """Return the configured retention window (default 90 days)."""
    from backend.core.settings_store import get_setting

    raw = get_setting(db, "retention_days")
    try:
        days = int(raw) if raw is not None else DEFAULT_RETENTION_DAYS
    except (TypeError, ValueError):
        days = DEFAULT_RETENTION_DAYS
    return days if days >= 1 else DEFAULT_RETENTION_DAYS


def _count_events(db: Session, task_id: str) -> int:
    return (
        db.query(TaskEvent)
        .filter_by(task_id=task_id)
        .count()
    )


def _delete_complete_record(db: Session, task: Task, reason: str) -> Dict[str, Any]:
    """Delete a task, its full audit event record, and its conversation turns.

    Committed atomically so a crash mid-purge cannot leave partial records.
    """
    event_count = _count_events(db, task.id)
    db.query(ConversationTurn).filter_by(task_id=task.id).delete(synchronize_session=False)
    db.query(TaskEvent).filter_by(task_id=task.id).delete(synchronize_session=False)
    db.delete(task)
    receipt = PurgeReceipt(
        task_id=task.id,
        reason=reason,
        purged_at=datetime.utcnow(),
        event_count=event_count,
    )
    db.add(receipt)
    db.commit()
    logger.info(
        "Purged complete record for task %s (%d events), reason=%s",
        task.id, event_count, reason,
    )
    return {
        "task_id": task.id,
        "reason": reason,
        "event_count": event_count,
        "purged_at": receipt.purged_at.isoformat(),
    }


def purge_task(db: Session, task_id: str, reason: str = "owner_requested") -> Optional[Dict[str, Any]]:
    """Purge one complete task record, returning a non-sensitive receipt dict."""
    task = db.query(Task).filter_by(id=task_id).first()
    if not task:
        return None
    return _delete_complete_record(db, task, reason)


def purge_turns(
    db: Session,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> int:
    """Purge conversation_turns older than the retention window.

    The episodic transcript is bounded by the same window as tasks. Any turn
    older than `days` is deleted -- including orphans whose task row was
    already purged -- so `conversation_turns` cannot grow forever. Durable
    memory (`memory_entries` / `memory_edges`) is exempt.

    Returns the number of turn rows deleted.
    """
    days = days or get_retention_days(db)
    cutoff = (now or datetime.utcnow()) - timedelta(days=days)
    purged = (
        db.query(ConversationTurn)
        .filter(ConversationTurn.created_at < cutoff)
        .delete(synchronize_session=False)
    )
    db.commit()
    if purged:
        logger.info("Purged %d conversation turns older than %s", purged, cutoff.isoformat())
    return purged


def purge_expired(
    db: Session,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Purge complete records older than the retention window.

    Returns a list of non-sensitive purge receipts. Nothing is purged before
    the retention window, and terminal-state tasks at any age within the
    window are kept for the full window. Each purged task also takes its
    conversation turns with it (complete-record semantics); expired/orphaned
    turns are swept separately by purge_turns().
    """
    days = days or get_retention_days(db)
    cutoff = (now or datetime.utcnow()) - timedelta(days=days)
    stale = (
        db.query(Task)
        .filter(Task.created_at < cutoff)
        .order_by(Task.created_at.asc())
        .all()
    )
    receipts: List[Dict[str, Any]] = []
    for task in stale:
        try:
            receipts.append(_delete_complete_record(db, task, "retention_expired"))
        except Exception as exc:  # pragma: no cover - defensive
            logger.error("Failed to purge task %s: %s", task.id, exc)
            db.rollback()
    return receipts


def list_purge_receipts(db: Session, limit: int = 100) -> List[Dict[str, Any]]:
    rows = (
        db.query(PurgeReceipt)
        .order_by(PurgeReceipt.purged_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "task_id": r.task_id,
            "reason": r.reason,
            "purged_at": r.purged_at.isoformat() if r.purged_at else None,
            "event_count": r.event_count,
        }
        for r in rows
    ]
