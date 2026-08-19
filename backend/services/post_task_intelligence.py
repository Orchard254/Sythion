"""
Post-task intelligence hooks.

These hooks run after the runtime execution loop reaches a terminal task state.
They intentionally live outside the core loop so conversation persistence,
memory consolidation, and proposal growth can evolve without deepening the
runtime/intelligence coupling inside `task_execution_loop.py`.

They are best-effort: failures here must never rewrite execution truth or block
normal task completion.
"""

from __future__ import annotations

import logging
import re
from datetime import datetime
from typing import Any

from backend.api.schemas import TaskState
from backend.core.session import get_current_session_id
from backend.models.database import ConversationTurn, MemoryEdge, MemoryEntry, Task

logger = logging.getLogger(__name__)


async def propose_skill_from_trace(db, task_id: str, tool_executor: Any) -> None:
    """Best-effort proposal generation after a verified successful task."""
    try:
        from backend.services.skill_engine import maybe_generate_proposal

        proposal = maybe_generate_proposal(db, task_id, tool_executor)
        if proposal is not None:
            logger.info(
                "Generated proposed skill %s from verified trace of task %s",
                proposal.id,
                task_id,
            )
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("Proposal generation failed for task %s: %s", task_id, exc)


async def save_conversation_turn(db, task_id: str, task: Task) -> None:
    """Save a compressed user/assistant turn for episodic continuity."""
    try:
        session_id = get_current_session_id()

        req_summary = (task.original_request or "").strip()
        if len(req_summary) > 500:
            req_summary = req_summary[:497] + "..."

        res_summary = (task.final_result or task.terminal_reason or "").strip()
        if not res_summary:
            res_summary = (
                "Task completed"
                if task.state == TaskState.COMPLETED.value
                else "Task failed"
            )
        if len(res_summary) > 2000:
            res_summary = res_summary[:1997] + "..."

        turn_user = ConversationTurn(
            session_id=session_id,
            task_id=task_id,
            role="user",
            summary=req_summary,
        )
        turn_assistant = ConversationTurn(
            session_id=session_id,
            task_id=task_id,
            role="assistant",
            summary=f"[{task.state.upper()}] {res_summary}",
        )

        db.add(turn_user)
        db.add(turn_assistant)

        req_text = task.original_request or ""
        patterns = [
            (r"(?:my name is|i'?m called)\s+([A-Za-z0-9_\-]{2,30})", "user_profile", "user_name"),
            (
                r"(?:i prefer|my preferred text editor is|my favorite editor is)\s+([A-Za-z0-9_\-\s\.\+]{2,30})",
                "user_preference",
                "preferred_editor",
            ),
            (r"(?:remember that|note that)\s+([A-Za-z0-9_\-\s\.\+:]{5,100})", "user_instruction", None),
        ]
        for pattern, cat, fixed_key in patterns:
            m = re.search(pattern, req_text, re.IGNORECASE)
            if m:
                val = m.group(1).strip()
                key = fixed_key or f"pref_{re.sub(r'[^a-zA-Z0-9_]', '_', val[:20]).lower()}"
                existing = db.query(MemoryEntry).filter_by(key=key).first()
                if existing:
                    existing.value = val
                    existing.category = cat
                    existing.updated_at = datetime.utcnow()
                else:
                    db.add(MemoryEntry(
                        key=key,
                        value=val,
                        category=cat,
                        source_task_id=task_id,
                    ))

        db.commit()
        logger.info("Saved conversation turn for task %s", task_id)

        try:
            from backend.services.conversation_state import update_state

            update_state(session_id, task.original_request or "", res_summary)
        except Exception as e:
            logger.warning("Could not update conversation state: %s", e)

    except Exception as e:
        logger.warning("Could not save conversation turn for task %s: %s", task_id, e)


async def consolidate_memory_from_trace(db, task_id: str) -> None:
    """Best-effort memory consolidation after a saved conversation turn."""
    try:
        from backend.core.config import settings
        from backend.services.memory_consolidation import (
            apply_edge_decay,
            consolidate_memory,
            prune_stale_memories,
        )

        if not settings.memory_consolidation_enabled:
            return

        apply_edge_decay(db)
        prune_stale_memories(db)

        session_id = get_current_session_id()
        turns = (
            db.query(ConversationTurn)
            .filter_by(session_id=session_id)
            .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
            .limit(20)
            .all()
        )
        if len(turns) < 2:
            return
        memories = db.query(MemoryEntry).order_by(MemoryEntry.updated_at.desc()).all()
        edges = db.query(MemoryEdge).order_by(MemoryEdge.updated_at.desc()).all()
        applied = await consolidate_memory(
            db,
            list(reversed(turns)),
            memories,
            memory_edges=edges,
        )
        if applied:
            logger.info(
                "Memory consolidation applied %d ops (task %s)",
                len(applied),
                task_id,
            )
    except Exception as exc:  # pragma: no cover - best-effort
        logger.warning("Memory consolidation failed for task %s: %s", task_id, exc)
