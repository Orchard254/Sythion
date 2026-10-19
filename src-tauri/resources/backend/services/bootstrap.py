"""Startup bootstrap sequence (Phase 4.5).

Single authoritative ordering for everything that must happen before the
API starts serving and before ANY background wake may execute:

    1. task/lease recovery        (interrupted work resolved)
    2. goal migration             (legacy objectives → plan-backed)
    3. main-agent initialization  (identity restored / minted)
    4. wake recovery              (durable PENDING/stale wakes reclaimed)

``main.py`` then starts the wake worker (which drains recovered PENDING
wakes) — never before this sequence completes. The returned ``order`` list
makes the ordering regression-testable.
"""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any, Dict

logger = logging.getLogger(__name__)


def run_startup_sequence(db_manager) -> Dict[str, Any]:
    """Execute ordered startup steps; failures are logged, never fatal.

    Returns {'order': [...], 'recovery': ..., 'migration': ...,
             'agent_id': ..., 'wake_recovery': ...}.
    """
    order: list[str] = []
    result: Dict[str, Any] = {"order": order}

    # 1. Task/lease recovery ---------------------------------------------------
    order.append("task_recovery")
    try:
        from backend.services.task_orchestrator import TaskOrchestrator
        from backend.services.planner import Planner

        session = db_manager.new_session()
        try:
            orchestrator = TaskOrchestrator(
                state_store=None, planner=Planner(session)
            )
            result["recovery"] = orchestrator.recover_all()
            if result["recovery"]:
                logger.info("Startup goal recovery: %s", result["recovery"])
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"Startup goal recovery skipped due to error: {exc}")
        result["recovery"] = []

    # 2. Goal migration (legacy sunset) ----------------------------------------
    order.append("goal_migration")
    try:
        from backend.services.goal_manager import migrate_legacy_objectives

        session = db_manager.new_session()
        try:
            report = migrate_legacy_objectives(session)
            result["migration"] = report
            if report["migrated"] or report["skipped"]:
                logger.info("Legacy objective migration: %s", report)
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"Legacy objective migration skipped due to error: {exc}")
        result["migration"] = {"migrated": [], "skipped": []}

    # 3. Main-agent initialization ----------------------------------------------
    order.append("agent_init")
    agent_id = None
    try:
        from backend.services.main_agent import MainAgentService

        session = db_manager.new_session()
        try:
            agent = MainAgentService(session).get_or_create()
            agent_id = agent.agent_id
            logger.info("Main agent %s state=%s", agent_id, agent.state)
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"Main agent initialization skipped due to error: {exc}")
    result["agent_id"] = agent_id

    # 4. Wake recovery (BEFORE worker start; nothing executes here) --------------
    order.append("wake_recovery")
    if agent_id is not None:
        try:
            from datetime import datetime

            from backend.services.main_agent import MainAgentService

            boot_time = datetime.utcnow()
            session = db_manager.new_session()
            try:
                result["wake_recovery"] = (
                    MainAgentService(session).recover_pending_wakes(boot_time)
                )
                if result["wake_recovery"]["pending"]:
                    logger.info("Durable wakes pending after recovery: %s",
                                result["wake_recovery"])
            finally:
                session.close()
        except Exception as exc:
            logger.warning(f"Wake recovery skipped due to error: {exc}")
            result["wake_recovery"] = {"reclaimed": 0, "failed_reclaims": 0,
                                       "pending": 0}
    else:
        result["wake_recovery"] = {"reclaimed": 0, "failed_reclaims": 0,
                                   "pending": 0}

    return result