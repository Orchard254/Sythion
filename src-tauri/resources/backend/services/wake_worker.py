"""Event-driven wake worker for the Persistent Main Agent (Phase 4).

NOT a scheduler: there is no timer and no periodic loop. The worker sleeps on
an in-process asyncio queue that is fed exclusively by explicit, auditable
triggers:

  - application.startup          (lifespan, once per process)
  - task.completed / task.failed (post-task pipeline hook)
  - goal.resumed                 (GoalManager resume path)
  - user.request                 (POST /agent/wake)

Every submitted wake carries a durable wake_key (UNIQUE in main_agent_wakes),
so duplicates can never produce duplicate execution sessions — even across
restarts. Before running anything the worker consults the single policy gate
(MainAgentService.may_execute_background); disallowed wakes are recorded as
auditable skips. Sessions themselves are bounded (step cap + budgets + lease)
and run entirely through the Protected Runtime Kernel.
"""

from __future__ import annotations

import asyncio
import logging
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from backend.models.database import AgentWake

logger = logging.getLogger(__name__)


class WakeWorker:
    """Single-consumer queue processing audited Main-Agent wake requests."""

    def __init__(self, db_manager):
        self._db_manager = db_manager
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._queued_keys: set = set()
        self._task: Optional[asyncio.Task] = None
        self._max_queue = 64

    # ---- lifecycle ---------------------------------------------------------

    def start(self) -> None:
        if self._task is None or self._task.done():
            self._queue = asyncio.Queue()
            self._queued_keys.clear()
            self._task = asyncio.create_task(
                self._run(), name="sythion-agent-wake-worker"
            )
            logger.info("Main-agent wake worker started")

    async def stop(self) -> None:
        if self._task is not None and not self._task.done():
            self._task.cancel()
            try:
                await self._task
            except (asyncio.CancelledError, Exception):
                pass
        self._task = None
        logger.info("Main-agent wake worker stopped")

    @property
    def running(self) -> bool:
        return self._task is not None and not self._task.done()

    # ---- submission (DURABLE FIRST) ------------------------------------------

    def submit(self, trigger_type: str, trigger_reference: Optional[str] = None,
               wake_key: Optional[str] = None) -> Optional[str]:
        """Durably persist the wake (PENDING), then enqueue as an optimization.

        The durable row is authoritative: if the queue is full, or the
        process dies before dequeue, the wake is recovered by
        ``drain_durable`` / startup recovery instead of being lost.
        Returns the wake_key, or None only when the wake was a duplicate.
        """
        key = wake_key or f"{trigger_type}:{trigger_reference or uuid.uuid4()}"
        if key in self._queued_keys:
            return key  # in-flight pre-filter; DB constraint is the real gate

        from backend.services.main_agent import MainAgentService

        session = self._db_manager.new_session()
        try:
            record = MainAgentService(session).submit_wake(
                trigger_type,
                trigger_reference=trigger_reference,
                wake_key=key,
            )
            deduplicated = record["deduplicated"]
        finally:
            session.close()

        self._queued_keys.add(key)
        if not deduplicated and self._queue.qsize() < self._max_queue:
            self._queue.put_nowait({"wake_key": key})
        # Duplicate or saturated: either way the PENDING row exists and will
        # be drained durably. Nothing is lost.
        return key

    def submit_task_terminal(self, task_id: str, state: str) -> Optional[str]:
        trigger = f"task.{state}"
        return self.submit(trigger, trigger_reference=task_id,
                           wake_key=f"{trigger}:{task_id}")

    def submit_startup(self, agent_id: str) -> Optional[str]:
        # Unique per process boot: every startup legitimately wakes once.
        return self.submit("application.startup", trigger_reference=agent_id)

    # ---- consumption ---------------------------------------------------------------

    async def _run(self) -> None:
        while True:
            item = await self._queue.get()
            try:
                await self._process(item)
                await self.drain_durable()
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # pragma: no cover - never kill the worker
                logger.exception("Wake processing failed: %s", exc)
            finally:
                self._queued_keys.discard(item.get("wake_key"))
                self._queue.task_done()

    async def drain_durable(self, limit: int = 10) -> int:
        """Process any durable PENDING wakes (queue drops, restarts, reclaim).

        This is what makes the DB — not the queue — the source of truth for
        pending work. Returns the number processed.
        """
        session = self._db_manager.new_session()
        try:
            from backend.services.main_agent import WAKE_PENDING, MainAgentService

            svc = MainAgentService(session)
            rows = (
                session.query(AgentWake)
                .filter(AgentWake.status == WAKE_PENDING)
                .order_by(AgentWake.id.asc())
                .limit(limit)
                .all()
            )
        finally:
            session.close()

        count = 0
        for row in rows:
            item = {"wake_key": row.wake_key}
            await self._process(item)
            count += 1
        return count

    async def _process(self, item: Dict[str, Any]) -> None:
        session = self._db_manager.new_session()
        try:
            from backend.core.config import settings
            from backend.services.goal_selector import RunnableGoalSelector
            from backend.services.main_agent import (
                MainAgentService,
                WAKE_SKIPPED,
            )
            from backend.services.planner import Planner
            from backend.services.task_runner import TaskRunner
            from backend.services.task_orchestrator import TaskOrchestrator

            agent_service = MainAgentService(session)

            allowed, reason = agent_service.may_execute_background()
            if not allowed:
                # Resolve the DURABLE row as SKIPPED (audited), not just log.
                agent_service.skip_wake(
                    item.get("trigger_type", "unknown"), reason,
                    wake_key=item["wake_key"],
                )
                return

            wake = agent_service.claim_wake(item["wake_key"])
            if wake is None:
                logger.info("Wake %s already claimed/resolved; skipping",
                            item["wake_key"])
                return
            absorbed = agent_service.absorb_coalescable(wake)
            if absorbed:
                logger.info("Coalesced %d pending wake(s) into %s",
                            absorbed, wake.wake_key)

            runner = TaskRunner(session)
            orchestrator = TaskOrchestrator(
                state_store=runner,
                planner=Planner(session),
            )
            selector = RunnableGoalSelector(session)

            result = await orchestrator.run_background_cycle(
                selector,
                max_steps=settings.max_steps_per_background_cycle,
            )

            outcome = (
                "completed"
                if any(e["outcome"] == "completed" for e in result["executed"])
                else ("executed" if result["executed"] else "no_work")
            )
            agent_service.finish_wake(
                wake,
                status="COMPLETED",
                outcome=outcome,
                stop_reason=result["stopped_reason"],
                steps_attempted=len(result["executed"]),
                steps_completed=sum(
                    1 for e in result["executed"] if e["outcome"] == "completed"
                ),
            )
            logger.info(
                "Background session finished: %d step(s), stop=%s%s",
                len(result["executed"]), result["stopped_reason"],
                f", coalesced={absorbed}" if absorbed else "",
            )
        finally:
            session.close()


# Process-wide registration (composition-root style, like api.dependencies).
_worker: Optional[WakeWorker] = None


def set_wake_worker(worker: Optional[WakeWorker]) -> None:
    global _worker
    _worker = worker


def get_wake_worker() -> Optional[WakeWorker]:
    return _worker


def request_task_terminal_wake(task_id: str, state: str) -> None:
    """Best-effort hook used by the post-task pipeline."""
    worker = get_wake_worker()
    if worker is None:
        return
    try:
        worker.submit_task_terminal(task_id, state)
    except Exception as exc:  # pragma: no cover - must never break tasks
        logger.warning("Could not submit task-terminal wake: %s", exc)


def format_ts(dt: Optional[datetime]) -> Optional[str]:
    return dt.isoformat() if dt else None