"""
Owner-configurable task/audit retention and complete-record purge.

Retention: tasks (and their full audit event records) older than the
owner-configured `retention_days` setting (default 90) can be purged. The
episodic conversation transcript (`conversation_turns`) is bounded by the
same window through an explicit, separate sweep (`purge_conversation_history`);
every expired turn -- including orphans whose task was already purged -- is
swept so the table cannot grow forever. Task purge does NOT delete retained
conversation turns, memberships or contexts; it only detaches them from the
deleted task. Durable memory (`memory_entries`, `memory_edges`) is exempt:
it is not a transcript.

Purge: a complete-record purge deletes the task row and every event row for
that task, plus its task-specific artifacts (decision windows,
task/exchange associations). Conversation turns, memberships and contexts
survive and are detached from the deleted task. Then a non-sensitive purge
receipt (task ID, reason, purge time, event count) is written. Receipts
never contain task content, request text, or other sensitive material.
"""

from __future__ import annotations

import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List, Optional

from sqlalchemy import or_
from sqlalchemy.orm import Session

from backend.models.database import (
    ConversationTurn, ConversationExchange, ConversationTaskAssociation, TaskDecisionWindow,
    DiscussionContextMembership,
    PlanStep,
    ProposedSkill,
    PurgeReceipt,
    Task,
    TaskEvent,
)
try:
    from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerRow
except Exception:  # pragma: no cover - alternate invocation layout
    try:
        from runtime.followup_instruction_ledger import FollowupInstructionLedgerRow  # type: ignore
    except Exception:
        FollowupInstructionLedgerRow = None  # type: ignore

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
    """Delete a task and its full audit event record, preserving conversation
    turns, memberships and contexts (detaching task identity).

    Committed atomically so a crash mid-purge cannot leave partial records.
    """
    try:
        event_count = _count_events(db, task.id)
        db.query(TaskDecisionWindow).filter_by(task_id=task.id).delete(synchronize_session=False)
        # User context survives task deletion: retain follow-up instruction
        # ledger rows with their original task identity and null only the
        # mutable task foreign key.  Do not swallow storage errors here.
        if FollowupInstructionLedgerRow is not None:
            (db.query(FollowupInstructionLedgerRow)
             .filter_by(task_id=task.id)
             .update({"task_id": None}, synchronize_session=False))
        db.query(ConversationTaskAssociation).filter_by(task_id=task.id).delete(synchronize_session=False)
        # Episodic conversation transcript rows survive task deletion:
        # they are discussion history, not task records. Clear the deleted
        # task identity to avoid a dangling link, but keep row content and
        # all discussion-context memberships/contexts intact.
        db.query(ConversationTurn).filter_by(task_id=task.id).update(
            {"task_id": None}, synchronize_session=False
        )
        # Plan steps can point at this task as the step that executed it and/or
        # the evidence task proving an outcome. Null only the matching column
        # (a step may reference two different tasks). Leaving these dangling
        # would make goal execution binding, restart policy, work preview and
        # reasoning continuity read a purged task as still present.
        db.query(PlanStep).filter_by(linked_task_id=task.id).update(
            {"linked_task_id": None}, synchronize_session=False
        )
        db.query(PlanStep).filter_by(evidence_task_id=task.id).update(
            {"evidence_task_id": None}, synchronize_session=False
        )
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
    except BaseException:
        db.rollback()
        raise
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
    """Purge expired conversation data, returning the number of summary rows.

    The episodic transcript is bounded by the same window as tasks. Any turn
    older than `days` is deleted -- including orphans whose task row was
    already purged -- so `conversation_turns` cannot grow forever. Durable
    memory (`memory_entries` / `memory_edges`) is exempt.

    Returns the number of turn rows deleted.
    """
    return purge_conversation_history(db, days=days, now=now)[0]


def purge_conversation_history(
    db: Session,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> tuple[int, int]:
    """Purge expired summaries and full exchanges, returning their separate counts."""
    try:
        days = days or get_retention_days(db)
        cutoff = (now or datetime.utcnow()) - timedelta(days=days)
        expired_turns = db.query(ConversationTurn.id).filter(ConversationTurn.created_at < cutoff)
        expired_exchanges = db.query(ConversationExchange.id).filter(ConversationExchange.created_at < cutoff)
        from backend.services.discussion_contexts import prune_empty_contexts
        expired_members = db.query(DiscussionContextMembership).filter(or_(
            DiscussionContextMembership.exchange_id.in_(expired_exchanges),
            DiscussionContextMembership.user_turn_id.in_(expired_turns),
            DiscussionContextMembership.assistant_turn_id.in_(expired_turns),
        ))
        context_ids = [row.context_id for row in expired_members.all()]
        expired_members.delete(synchronize_session=False)
        prune_empty_contexts(db, context_ids)
        # Explicit cleanup also works on SQLite connections without FK enforcement.
        db.query(ConversationTaskAssociation).filter(or_(
            ConversationTaskAssociation.exchange_id.in_(expired_exchanges),
            ConversationTaskAssociation.user_turn_id.in_(expired_turns),
            ConversationTaskAssociation.assistant_turn_id.in_(expired_turns),
        )).delete(synchronize_session=False)
        purged = (
            db.query(ConversationTurn)
            .filter(ConversationTurn.created_at < cutoff)
            .delete(synchronize_session=False)
        )
        exchanges = db.query(ConversationExchange).filter(
            ConversationExchange.created_at < cutoff
        ).delete(synchronize_session=False)
        db.commit()
    except BaseException:
        db.rollback()
        raise
    if purged:
        logger.info("Purged %d conversation turns older than %s", purged, cutoff.isoformat())
    if exchanges:
        logger.info("Purged %d conversation exchanges older than %s", exchanges, cutoff.isoformat())
    return purged, exchanges


def purge_expired(
    db: Session,
    days: Optional[int] = None,
    now: Optional[datetime] = None,
) -> List[Dict[str, Any]]:
    """Purge complete records older than the retention window.

    Returns a list of non-sensitive purge receipts. Nothing is purged before
    the retention window, and terminal-state tasks at any age within the
    window are kept for the full window. Task purge does not remove
    conversation turns; retained history is swept separately by
    purge_turns()/purge_conversation_history().
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
