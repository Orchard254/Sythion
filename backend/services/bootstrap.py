"""Startup bootstrap sequence (Phase 4.5).

Single authoritative ordering for everything that must happen before the
API starts serving and before ANY background wake may execute:

    1. task/lease recovery        (interrupted work resolved: plan steps,
                                   delegations, plans, specialist TTLs)
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

STATUS_COMPLETED = "completed"
STATUS_FAILED = "failed"
STATUS_SKIPPED = "skipped"


def _stage_result(name: str, status: str, detail: Any = None,
                  error: str = None) -> Dict[str, Any]:
    entry: Dict[str, Any] = {"stage": name, "status": status}
    if detail is not None:
        entry["detail"] = detail
    if error is not None:
        entry["error"] = error
    return entry


def _best_effort_recovery(db_manager, label: str, runner):
    """Run one startup recovery on a fresh session; never fatal.

    Mirrors ``TaskOrchestrator.recover_all``'s logging pattern: an exception is
    logged as a warning and returned as an error string so the caller can record
    it without aborting the ordered startup sequence. A dedicated session per
    recovery keeps one failure from poisoning the others' transaction state.
    """
    session = db_manager.new_session()
    try:
        return runner(session), None
    except Exception as exc:
        logger.warning(f"Startup {label} skipped due to error: {exc}")
        return None, str(exc)
    finally:
        session.close()


def run_startup_sequence(db_manager) -> Dict[str, Any]:
    """Execute ordered startup steps; failures are logged, never fatal.

    Returns {'order': [...], 'recovery': ..., 'migration': ...,
             'agent_id': ..., 'wake_recovery': ..., 'stage_statuses': {...},
             'stages': [...], 'startup_sequence_status': ...}.
    """
    order: list[str] = []
    result: Dict[str, Any] = {"order": order}
    stage_statuses: Dict[str, str] = {}
    stages: list[Dict[str, Any]] = []

    def _record(stage: str, status: str, detail: Any = None,
                error: str = None) -> None:
        stage_statuses[stage] = status
        stages.append(_stage_result(stage, status, detail=detail, error=error))

    # 1. Task/lease recovery ---------------------------------------------------
    order.append("task_recovery")
    recovery_errors: list[str] = []

    def _goal_recovery(session):
        from backend.services.task_orchestrator import TaskOrchestrator
        from backend.services.planner import Planner

        orchestrator = TaskOrchestrator(state_store=None, planner=Planner(session))
        return orchestrator.recover_all()

    result["recovery"], error = _best_effort_recovery(
        db_manager, "goal recovery", _goal_recovery
    )
    if error is not None:
        recovery_errors.append(error)
        result["recovery"] = []
    elif result["recovery"]:
        logger.info("Startup goal recovery: %s", result["recovery"])

    # The recoveries below close the gap left by recover_all(), which scans plan
    # steps only and never touches Delegation / DelegationPlan / AgentRecord rows.
    # Each takes only the DB session, so they run here: after the DB is up and
    # before agent_init / wake_recovery. Order matters — resolve delegation and
    # plan state from task evidence FIRST, then expire specialists still RUNNING
    # past their TTL (expiry must not pre-empt evidence-based recovery). No new
    # background loops: this is one-shot startup recovery only.
    def _delegation_recovery(session):
        from backend.agents.delegation import DelegationService

        return DelegationService(session).recover_delegations()

    result["delegation_recovery"], error = _best_effort_recovery(
        db_manager, "delegation recovery", _delegation_recovery
    )
    if error is not None:
        recovery_errors.append(error)
        result["delegation_recovery"] = []
    elif result["delegation_recovery"]:
        logger.info("Startup delegation recovery: %s",
                    result["delegation_recovery"])

    def _plan_recovery(session):
        from backend.agents.coordination import DelegationPlanService

        return DelegationPlanService(session).recover_plans()

    result["plan_recovery"], error = _best_effort_recovery(
        db_manager, "plan recovery", _plan_recovery
    )
    if error is not None:
        recovery_errors.append(error)
        result["plan_recovery"] = {"recovered": 0}
    elif result["plan_recovery"] and result["plan_recovery"].get("recovered"):
        logger.info("Startup plan recovery: %s", result["plan_recovery"])

    def _specialist_expiry(session):
        from backend.agents.service import AgentService

        return AgentService(session).expire_due()

    result["specialist_expiry"], error = _best_effort_recovery(
        db_manager, "specialist TTL expiry", _specialist_expiry
    )
    if error is not None:
        recovery_errors.append(error)
        result["specialist_expiry"] = 0
    elif result["specialist_expiry"]:
        logger.info("Startup specialist TTL expiry: %s",
                    result["specialist_expiry"])

    if recovery_errors:
        _record("task_recovery", STATUS_FAILED,
                detail=result["recovery"], error="; ".join(recovery_errors))
    else:
        _record("task_recovery", STATUS_COMPLETED, detail=result["recovery"])

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
            _record("goal_migration", STATUS_COMPLETED, detail=report)
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"Legacy objective migration skipped due to error: {exc}")
        result["migration"] = {"migrated": [], "skipped": []}
        _record("goal_migration", STATUS_FAILED, error=str(exc))

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
            _record("agent_init", STATUS_COMPLETED, detail=agent_id)
        finally:
            session.close()
    except Exception as exc:
        logger.warning(f"Main agent initialization skipped due to error: {exc}")
        _record("agent_init", STATUS_FAILED, error=str(exc))
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
                _record("wake_recovery", STATUS_COMPLETED,
                        detail=result["wake_recovery"])
            finally:
                session.close()
        except Exception as exc:
            logger.warning(f"Wake recovery skipped due to error: {exc}")
            result["wake_recovery"] = {"reclaimed": 0, "failed_reclaims": 0,
                                       "pending": 0}
            _record("wake_recovery", STATUS_FAILED, error=str(exc))
    else:
        result["wake_recovery"] = {"reclaimed": 0, "failed_reclaims": 0,
                                   "pending": 0}
        # Agent identity was never established, so wake recovery could not run.
        prior = stage_statuses.get("agent_init")
        if prior == STATUS_FAILED:
            _record("wake_recovery", STATUS_SKIPPED,
                    error="main agent unavailable")
        else:
            _record("wake_recovery", STATUS_SKIPPED,
                    error="main agent unavailable")

    # Aggregate status is truthful: any non-completed stage means the startup
    # sequence did not fully succeed, even though individual failures are
    # non-fatal and never abort the ordered sequence.
    if all(status == STATUS_COMPLETED for status in stage_statuses.values()):
        aggregate = STATUS_COMPLETED
    else:
        aggregate = "failed"

    result["stage_statuses"] = stage_statuses
    result["stages"] = stages
    result["startup_sequence_status"] = aggregate
    # Common aliases for callers / health surfaces.
    result["status"] = aggregate
    result["overall_status"] = aggregate
    result["aggregate_status"] = aggregate

    return result


_STARTUP_STATUS_KEYS = (
    "startup_sequence_status",
    "startup_status",
    "status",
    "overall_status",
    "startup_overall_status",
    "aggregate_status",
    "startup_aggregate_status",
)


def _startup_alias_value(boot: Dict[str, Any], *keys: str) -> Any:
    """Return the first present, non-empty value for any of ``keys``."""
    for key in keys:
        if key in boot:
            value = boot.get(key)
            if value is not None and value != "":
                return value
    return None


def _canonical_startup_status(boot: Any) -> str:
    """Resolve the canonical startup aggregate/status from a boot payload.

    Single fallback precedence shared by every consumer of the startup boot
    result: ``startup_sequence_status`` -> ``startup_status`` -> ``status`` ->
    ``overall_status`` -> ``startup_overall_status`` -> ``aggregate_status`` ->
    ``startup_aggregate_status`` -> ``"unknown"``.  Missing/empty values never
    fabricate ``completed``/``failed``; they stay ``"unknown"``.
    """
    if not isinstance(boot, dict):
        return "unknown"
    for key in _STARTUP_STATUS_KEYS:
        value = boot.get(key)
        if value is not None and value != "":
            return str(value)
    return "unknown"


def startup_status_projection(boot: Any = None) -> Dict[str, Any]:
    """Project a raw startup boot payload into canonical + compatibility fields.

    The projection is pure: it performs no database probing and changes no
    input.  Consumers keep their own database connectivity checks.  Compatibility
    alias fields are preserved (falling back to the canonical value when absent)
    so external schemas do not change.
    """
    if not isinstance(boot, dict):
        boot = {}
    canonical = _canonical_startup_status(boot)

    overall_value = _startup_alias_value(
        boot, "overall_status", "startup_overall_status"
    )
    aggregate_value = _startup_alias_value(
        boot, "aggregate_status", "startup_aggregate_status"
    )
    overall = str(overall_value) if overall_value is not None else canonical
    aggregate = str(aggregate_value) if aggregate_value is not None else canonical

    stages = boot.get("stage_statuses")
    if not isinstance(stages, dict):
        stages = boot.get("startup_stage_statuses")
    if not isinstance(stages, dict):
        stages = {}

    return {
        # Canonical aggregate/status.
        "startup_sequence_status": canonical,
        "startup_status": canonical,
        "status": canonical,
        "canonical_status": canonical,
        # Compatibility aliases (preserved, never silently dropped).
        "overall_status": overall,
        "startup_overall_status": overall,
        "aggregate_status": aggregate,
        "startup_aggregate_status": aggregate,
        # Stage detail (read-only projection).
        "stage_statuses": stages,
        "startup_stage_statuses": stages,
    }


