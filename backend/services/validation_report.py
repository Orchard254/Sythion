"""
Validation dump report (data only, no implementation details).

Produces a read-only snapshot for validating the memory system: per-store
table counts, memory-context injection stats aggregated from the model-engine
rollout log (available / relevant / connected / links over the last N runs),
and the relation vocabulary actually in use on memory edges. Nothing here
mutates state.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

from sqlalchemy.orm import Session

from backend.models.database import (
    ConversationTurn,
    MemoryContextLog,
    MemoryEdge,
    MemoryEntry,
    ProposedSkill,
    Skill,
    Task,
    TaskEvent,
)

logger = logging.getLogger(__name__)

REPORT_DEFAULT_LAST_N = 50
REPORT_MAX_LAST_N = 1000

_INJECTION_FIELDS = ("available", "relevant", "connected", "links")


def build_validation_report(
    db: Session,
    last_n: int = REPORT_DEFAULT_LAST_N,
) -> Dict[str, Any]:
    """Build the data-only validation dump snapshot.

    Args:
        db: A database session (read-only usage).
        last_n: Number of recent model-engine context builds to aggregate for
            the injection stats.
    """
    last_n = max(1, min(last_n, REPORT_MAX_LAST_N))
    counts = {
        "memory_entries": db.query(MemoryEntry).count(),
        "memory_edges": db.query(MemoryEdge).count(),
        "conversation_turns": db.query(ConversationTurn).count(),
        "tasks": db.query(Task).count(),
        "task_events": db.query(TaskEvent).count(),
        "skills": db.query(Skill).count(),
        "proposed_skills": db.query(ProposedSkill).count(),
        "proposed_skills_pending": (
            db.query(ProposedSkill).filter_by(status="proposed").count()
        ),
    }
    vocabulary = [
        r[0]
        for r in db.query(MemoryEdge.relation)
        .distinct()
        .order_by(MemoryEdge.relation)
        .all()
    ]
    return {
        "generated_at": datetime.utcnow().isoformat(),
        "last_n_runs": last_n,
        "counts": counts,
        "memory_context_injection": _injection_stats(db, last_n),
        "relation_vocabulary": {
            "distinct": len(vocabulary),
            "relations": vocabulary,
        },
    }


def _injection_stats(db: Session, last_n: int) -> Dict[str, Any]:
    """Aggregate the last `last_n` rollout-log rows (newest first)."""
    rows = (
        db.query(MemoryContextLog)
        .order_by(MemoryContextLog.id.desc())
        .limit(last_n)
        .all()
    )
    totals = {field: 0 for field in _INJECTION_FIELDS}
    per_run = []
    for r in rows:
        for field in _INJECTION_FIELDS:
            totals[field] += getattr(r, field) or 0
        per_run.append({
            "task_id": r.task_id,
            **{field: getattr(r, field) or 0 for field in _INJECTION_FIELDS},
            "created_at": r.created_at.isoformat() if r.created_at else None,
        })
    return {
        "runs": len(rows),
        "totals": totals,
        "per_run": per_run,
    }
