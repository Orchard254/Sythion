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
from typing import Any
from backend.api.schemas import TaskState
from backend.core.session import get_current_session_id
from backend.models.database import ConversationTurn, MemoryEdge, MemoryEntry, Task
from backend.services.memory_service import MemoryService
from sqlalchemy.orm import sessionmaker
logger = logging.getLogger(__name__)


def _caller_owns_transaction(db) -> bool:
    """Return True when committing now would risk caller-owned data.

    ``save_conversation_turn`` needs to persist conversation turns in an
    independent, verified transaction before optional memory extraction.
    If the caller already has a physical transaction or staged rows, an early
    commit would silently commit that caller work, so the function reports and
    stops instead of forcing the commit.
    """
    # Pending (including flushed-but-uncommitted) rows make an early commit
    # unsafe regardless of the physical transaction state.
    for collection_name in ("new", "dirty", "deleted"):
        try:
            if getattr(db, collection_name, None):
                return True
        except Exception:
            # Fail closed: if we cannot inspect pending rows, assume caller-owned.
            return True

    # Respect caller ownership for explicitly begun transactions and nested
    # savepoints as well as pending writes. SQLAlchemy records the reason a
    # session transaction exists; only its ordinary AUTOBEGIN (typically a
    # read-only Select) may be augmented. Anything explicit, or any real
    # physical transaction, means the caller owns it.
    try:
        transaction = db.get_transaction()
    except Exception:
        return True
    try:
        from sqlalchemy.orm.session import SessionTransactionOrigin

        origin = getattr(transaction, "origin", None)
        if origin is not None and origin is not SessionTransactionOrigin.AUTOBEGIN:
            return True
        if transaction is None:
            origin = None
    except Exception:
        return True
    if transaction is not None:
        origin = getattr(transaction, "origin", None)
        if origin is None:
            return True

    # Physical (dbapi) transaction state, for flushed/pending or explicit work.
    try:
        connection = db.connection()
        dbapi_connection = getattr(connection, "connection", None)
        dbapi_connection = getattr(
            dbapi_connection, "dbapi_connection", dbapi_connection
        )
        physical = getattr(dbapi_connection, "in_transaction", None)
        if callable(physical):
            physical = physical()
        if physical is None:
            # Cannot determine transaction state: fail closed.
            return True
        return bool(physical)
    except Exception:
        # Fail closed if physical transaction state cannot be determined.
        return True


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
    if _caller_owns_transaction(db):
        logger.warning(
            "Could not save conversation turn for task %s: caller-owned transaction "
            "makes an independent commit unsafe",
            task_id,
        )
        return

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

    try:
        # Persist conversation turns first, in their own committed transaction,
        # so optional memory extraction can never lose or half-persist them.
        db.add(turn_user)
        db.add(turn_assistant)
        db.commit()

        # Verify the conversation turns were persisted before optional extraction.
        try:
            engine = db.bind
            SessionLocal = sessionmaker(bind=engine)
            with SessionLocal() as verify_session:
                turns = verify_session.query(ConversationTurn).filter_by(task_id=task_id).all()
                if len(turns) < 2:
                    logger.warning(
                        "Verification failed: conversation turns not persisted for task %s",
                        task_id,
                    )
                    return
        except Exception as exc:
            logger.warning("Verification of conversation turn persistence failed: %s", exc)
            return

        # Optional, isolated memory extraction. Authority remains with
        # MemoryService; a failure in one extraction must not undo turns or other
        # committed extractions, and must leave no phantom uncommitted rows.
        req_text = task.original_request or ""
        patterns = [
            (r"(?:my name is|i'?m called)\s+([A-Za-z0-9_\-]{2,30})", "user_profile", "user_name", "FACT"),
            (
                r"(?:i prefer|my preferred text editor is|my favorite editor is)\s+([A-Za-z0-9_\-\s\.\+]{2,30})",
                "user_preference",
                "preferred_editor",
                "PREFERENCE",
            ),
            (r"(?:remember that|note that)\s+([A-Za-z0-9_\-\s\.\+:]{5,100})", "user_instruction", None, "INSTRUCTION"),
        ]
        memory_service = MemoryService(db)
        for pattern, cat, fixed_key, kind in patterns:
            m = re.search(pattern, req_text, re.IGNORECASE)
            if not m:
                continue
            val = m.group(1).strip()
            key = fixed_key or f"pref_{re.sub(r'[^a-zA-Z0-9_]', '_', val[:20]).lower()}"
            # Authority, duplicate, contradiction, scope, and lifecycle state
            # are owned solely by MemoryService using conservative
            # USER_CONVERSATION provenance. Auto-extracted facts remain UNVERIFIED.
            try:
                memory_service.create(
                    key=key,
                    value=val,
                    kind=kind,
                    scope="USER",
                    source_type="USER_CONVERSATION",
                    source_ref=task_id,
                    category=cat,
                    check_contradictions=True,
                )
            except Exception as exc:
                try:
                    db.rollback()
                except Exception:
                    pass
                logger.warning(
                    "Memory extraction failed for task %s (key=%s): %s",
                    task_id,
                    key,
                    exc,
                )

        try:
            from backend.services.conversation_state import update_state

            update_state(session_id, task.original_request or "", res_summary)
        except Exception as e:
            logger.warning("Could not update conversation state: %s", e)

        logger.info("Saved conversation turn for task %s", task_id)

    except Exception as e:
        # Roll back only the current, uncommitted transaction. Previously
        # committed conversation turns (and any committed extractions) remain.
        try:
            if db.in_transaction():
                db.rollback()
        except Exception:
            pass
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


async def diagnose_failed_task(db, task_id, runner=None, *, model_call_fn=None, **kwargs):
    """Compatibility wrapper for failed-task diagnostics."""
    try:
        from backend.services.failed_task_diagnostic import diagnose_failed_task as _diagnose

        return await _diagnose(db, task_id, runner, model_call_fn=model_call_fn, **kwargs)
    except Exception as exc:
        logger.warning("Failed-task diagnostic wrapper failed for task %s: %s", task_id, exc)
        return "Diagnostic unavailable: diagnostic investigation failed."


diagnose_failed_operation = diagnose_failed_task
run_failed_task_diagnostic = diagnose_failed_task
investigate_failed_task = diagnose_failed_task