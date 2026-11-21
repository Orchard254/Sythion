"""Bounded periodic maintenance for autonomous recovery.

ONE asyncio background task (started once from the app lifespan, in place of
the old pending-activation-only sweeper) runs a small, fixed set of isolated
sub-sweeps on every tick:

  * pending model-activation promotion — calls the existing
    :func:`backend.runtime.active_inference.sweep_pending_activation_once`
    unchanged (this module never edits that file);
  * stale RUNNING wake reclaim — re-queues reclaimed wakes through the live
    worker, but only when it can prove the wake is no longer in flight;
  * stale task-ownership reclaim — marks orphaned tasks INTERRUPTED (never
    resumes them).

Every sub-sweep gets its own short-lived session from the app DB manager, does
its synchronous SQL on a worker thread via ``asyncio.to_thread``, and closes the
session in ``finally``. Sub-sweep failures are contained independently; only
cancellation ends the loop. Nothing here calls ``may_execute_background``,
starts a model call, or grants authority — the only state changes are audited
recovery transitions.
"""

from __future__ import annotations

import asyncio
import logging
from datetime import datetime, timedelta
from typing import Any, Dict, List

from sqlalchemy import update

from backend.core.config import settings
from backend.runtime.active_inference import sweep_pending_activation_once
from backend.services.main_agent import (
    WAKE_FAILED,
    WAKE_PENDING,
    WAKE_RUNNING,
    _scope,
)

logger = logging.getLogger(__name__)

# Same low-frequency, bounded cadence as the pending-activation sweep it
# replaces. One cycle is a fixed amount of work; it never scales with traffic.
MAINTENANCE_SWEEP_INTERVAL_SECONDS = 5.0
# Mirrors the 5-attempt cap already enforced by MainAgentService boot recovery.
WAKE_RECLAIM_ATTEMPT_CAP = 5

_RECLAIMED_KEYS = "reclaimed_keys"


async def _in_fresh_session(db_manager: Any, fn: Any, *args: Any, **kwargs: Any) -> Any:
    """Short-lived session per sub-sweep; synchronous work off the loop thread."""
    session = await asyncio.to_thread(db_manager.new_session)
    try:
        return await asyncio.to_thread(fn, session, *args, **kwargs)
    finally:
        try:
            await asyncio.to_thread(session.close)
        except Exception:  # pragma: no cover - close must never kill the loop
            pass


# --- one-shot wake reclaim ---------------------------------------------------


def reclaim_stale_wakes_once(
    session: Any,
    wake_worker: Any,
    *,
    now: datetime | None = None,
    reclaim_seconds: float | None = None,
) -> Dict[str, Any]:
    """Reclaim durable RUNNING wakes whose owner is provably gone.

    A wake is stale only when it is still ``RUNNING`` and its ``claimed_at`` is
    older than ``now - reclaim_seconds`` (``settings.wake_reclaim_seconds`` by
    default). The CRITICAL double-execution guard is the live worker: a wake
    whose key is still in flight is never touched, and with no worker reference
    at all this sub-sweep fails closed and does nothing.

    Each transition is an atomic CAS (``status='RUNNING' AND claimed_at <
    cutoff``); only the thread that wins rowcount==1 writes a ledger event and
    asks the caller to re-queue. Reclaimed wakes go back to PENDING (attempts
    already incremented at claim time); after the 5-attempt cap they become
    FAILED with outcome ``reclaim_limit``. No wake is executed here.
    """
    if wake_worker is None:
        # Fail closed: without a worker there is no liveness authority at all.
        return {
            "skipped": "no_worker",
            "reclaimed": 0,
            "failed": 0,
            "skipped_live": 0,
            _RECLAIMED_KEYS: [],
        }

    ref_time = now or datetime.utcnow()
    seconds = (
        settings.wake_reclaim_seconds if reclaim_seconds is None
        else reclaim_seconds
    )
    cutoff = ref_time - timedelta(seconds=seconds)

    from backend.models.database import AgentWake
    from backend.runtime.events import DbEventLedger

    candidates = (
        session.query(AgentWake)
        .filter(AgentWake.status == WAKE_RUNNING)
        .filter(AgentWake.claimed_at < cutoff)
        .order_by(AgentWake.id.asc())
        .all()
    )

    reclaimed = 0
    failed = 0
    skipped_live = 0
    keys: List[str] = []
    for wake in candidates:
        # CRITICAL: never steal a wake this worker still owns. submit() records
        # the key as in-flight while queued or mid-flight; reclaiming such a
        # wake would run the same durable intent twice.
        if wake_worker.is_processing(wake.wake_key):
            skipped_live += 1
            continue

        attempts = wake.attempts or 0
        at_cap = attempts >= WAKE_RECLAIM_ATTEMPT_CAP
        values: Dict[str, Any] = {"status": WAKE_FAILED if at_cap else WAKE_PENDING}
        if at_cap:
            values.update({
                "outcome": "reclaim_limit",
                "stop_reason": "worker crashed repeatedly",
                "finished_at": ref_time,
            })

        updated = session.execute(
            update(AgentWake)
            .where(AgentWake.id == wake.id)
            .where(AgentWake.status == WAKE_RUNNING)
            .where(AgentWake.claimed_at < cutoff)
            .values(**values)
        )
        if updated.rowcount != 1:
            # Lost a race with the worker or another sweep: no event, no requeue.
            continue

        event_type = "agent.wake.failed" if at_cap else "agent.wake.reclaimed"
        DbEventLedger(session).append_event(
            _scope(wake.agent_id),
            event_type,
            {"wake_key": wake.wake_key, "attempts": attempts},
        )
        session.commit()

        if at_cap:
            failed += 1
        else:
            reclaimed += 1
            keys.append(wake.wake_key)

    return {
        "reclaimed": reclaimed,
        "failed": failed,
        "skipped_live": skipped_live,
        _RECLAIMED_KEYS: keys,
    }


# --- one-shot task reclaim ---------------------------------------------------


def reclaim_interrupted_tasks_once(
    session: Any,
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    """Mark tasks whose durable task ownership is lost as INTERRUPTED.

    Task ownership — NOT ``runner_lease`` — is the liveness authority: the
    heartbeat renews the ownership row, so an expired lease alongside a fresh
    ownership row means the task is still live. A task with no ownership row at
    all gives no evidence of loss and is left untouched (fail closed).

    INTERRUPTED is terminal for this sweep: it never resumes a task. The shared
    helper emits ``action.uncertain`` for an in-flight action before recording
    ``task.interrupted``.
    """
    ref_time = now or datetime.utcnow()

    from backend.api.schemas import TaskState
    from backend.models.database import Task
    from backend.runtime.leases import mark_task_interrupted
    from backend.runtime.task_ownership import EXPIRED, TaskOwnershipRow

    candidate_ids = [
        row[0]
        for row in (
            session.query(Task.id)
            .filter(Task.state.in_((
                TaskState.CREATED.value,
                TaskState.RUNNING.value,
                TaskState.WAITING_FOR_USER.value,
            )))
            .order_by(Task.id.asc())
            .all()
        )
    ]

    interrupted: List[str] = []
    for task_id in candidate_ids:
        ownership = (
            session.query(TaskOwnershipRow)
            .filter_by(task_id=task_id)
            .one_or_none()
        )
        if ownership is None:
            continue  # no ownership evidence: fail closed
        stale = (
            ownership.status == EXPIRED
            or (ownership.expires_at is not None and ref_time > ownership.expires_at)
        )
        if not stale:
            continue
        mark_task_interrupted(session, task_id)
        state = session.query(Task.state).filter_by(id=task_id).scalar()
        if state == TaskState.INTERRUPTED.value:
            interrupted.append(task_id)

    return {"interrupted": interrupted, "count": len(interrupted)}


# --- bounded, isolated sub-sweeps --------------------------------------------


async def _pending_activation_sub_sweep(db_manager: Any) -> Any:
    """Existing one-shot promotion; it owns (and closes) its own session."""
    return await sweep_pending_activation_once(db_manager)


async def _wake_reclaim_sub_sweep(
    db_manager: Any,
    wake_worker: Any,
    *,
    now: datetime | None = None,
    reclaim_seconds: float | None = None,
) -> Dict[str, Any]:
    if wake_worker is None:
        return {"skipped": "no_worker", _RECLAIMED_KEYS: []}
    report = await _in_fresh_session(
        db_manager, reclaim_stale_wakes_once, wake_worker,
        now=now, reclaim_seconds=reclaim_seconds,
    )
    # Re-queue on the event-loop thread: submit() would dedup the existing row
    # and never enqueue it, so the worker helper enqueues directly.
    for key in report.get(_RECLAIMED_KEYS, ()):
        wake_worker.requeue_reclaimed(key)
    return report


async def _task_reclaim_sub_sweep(
    db_manager: Any,
    *,
    now: datetime | None = None,
) -> Dict[str, Any]:
    return await _in_fresh_session(
        db_manager, reclaim_interrupted_tasks_once, now=now,
    )


async def _guarded_sub_sweep(name: str, awaitable: Any) -> Any:
    """One failing sub-sweep never prevents the others from running."""
    try:
        return await awaitable
    except asyncio.CancelledError:
        raise
    except Exception:
        logger.warning("Maintenance sub-sweep %s failed", name, exc_info=True)
        return None


async def maintenance_cycle(
    db_manager: Any,
    wake_worker: Any = None,
    *,
    now: datetime | None = None,
    reclaim_seconds: float | None = None,
) -> Dict[str, Any]:
    """One bounded cycle: all three sub-sweeps, independently contained."""
    return {
        "pending_activation": await _guarded_sub_sweep(
            "pending_activation", _pending_activation_sub_sweep(db_manager),
        ),
        "wake_reclaim": await _guarded_sub_sweep(
            "wake_reclaim",
            _wake_reclaim_sub_sweep(
                db_manager, wake_worker,
                now=now, reclaim_seconds=reclaim_seconds,
            ),
        ),
        "task_reclaim": await _guarded_sub_sweep(
            "task_reclaim", _task_reclaim_sub_sweep(db_manager, now=now),
        ),
    }


async def maintenance_sweeper(
    db_manager: Any,
    wake_worker: Any = None,
    *,
    interval_seconds: float = MAINTENANCE_SWEEP_INTERVAL_SECONDS,
) -> None:
    """Single bounded asyncio background task started once per app lifespan.

    Every failure is contained (logged, retried on the next tick); only
    cancellation ends the loop, so the lifespan can shut it down cleanly.
    """
    while True:
        try:
            await maintenance_cycle(db_manager, wake_worker)
        except asyncio.CancelledError:
            raise
        except Exception:
            logger.warning("Maintenance cycle failed", exc_info=True)
        try:
            await asyncio.sleep(interval_seconds)
        except asyncio.CancelledError:
            raise
