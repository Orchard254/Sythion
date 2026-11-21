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
import time
import uuid
from datetime import datetime
from typing import Any, Dict, Optional

from backend.models.database import AgentWake
from backend.services.reasoning_dispatch import (
    DispatchEvent, ReasoningEvent, ReasoningPurpose, get_reasoning_metrics,
)
from backend.services.bounded_wake_scheduler import (
    BoundedWakeScheduler, WakePolicy, WakeCycleReport,
)

logger = logging.getLogger(__name__)


def emit_wake_outcome(wake_key: str, result: Dict[str, Any]) -> None:
    """Record a wake outcome as a shared-bus event; grants no authority."""
    get_reasoning_metrics().events.emit(DispatchEvent(
        ReasoningEvent.IDLE if not result.get("executed")
        else ReasoningEvent.ADMITTED,
        wake_key, ReasoningPurpose.GOAL_PLANNING, "runtime",
        time.monotonic(),
        result.get("stopped_reason") or "wake_completed",
    ))


class WakeWorker:
    """Single-consumer queue processing audited Main-Agent wake requests."""

    def __init__(self, db_manager, *, reasoning_scheduler=None):
        self._db_manager = db_manager
        self._queue: "asyncio.Queue[Dict[str, Any]]" = asyncio.Queue()
        self._queued_keys: set = set()
        self._task: Optional[asyncio.Task] = None
        self._max_queue = 64
        # Boot-unique token for application.startup wake keys. agent_id is
        # durable and AgentWake rows are never deleted, so without this token
        # the UNIQUE(wake_key) dedup would drop every startup wake after boot #1.
        self._boot_id = uuid.uuid4().hex
        # Optional owner-gated time-driven loop. The worker stays purely
        # event-driven unless start_bounded_loop() is explicitly called.
        self._bounded_task: Optional[asyncio.Task] = None
        self._bounded_scheduler: Optional[BoundedWakeScheduler] = None
        self._bounded_session = None
        # The bounded loop may span many fresh BoundedWakeScheduler windows, so
        # its stop signal and last fatal error live on the worker, not on a
        # single (replaced) scheduler instance.
        self._stop: Optional[asyncio.Event] = None
        self._last_fatal_error: Optional[str] = None
        # Opt-in observability sink. The worker remains event-driven by default;
        # a scheduler supplied here is already subscribed to the shared bus.
        self.reasoning_scheduler = reasoning_scheduler

    def install_reasoning_scheduler(self, scheduler) -> None:
        """Attach the shared bounded event scheduler for wake observability."""
        self.reasoning_scheduler = scheduler

    async def run_bounded_wake_cycle(self, selector, wake_handler, policy: WakePolicy) -> WakeCycleReport:
        """Opt-in time-bounded cycle. Never used by the default worker loop."""
        return await BoundedWakeScheduler().run(selector, wake_handler, policy)

    # ---- owner-gated bounded time-driven loop (default OFF) ---------------

    def bounded_loop_running(self) -> bool:
        return self._bounded_task is not None and not self._bounded_task.done()

    def last_fatal_error(self) -> Optional[str]:
        """Last fatal error of the bounded loop, or None when none occurred."""
        return self._last_fatal_error

    def _bounded_owner_enabled(self) -> bool:
        """Re-read owner switches AND agent lifecycle state before rearm.

        Mirrors ``apply_owner_policy``: the time switch, the background switch
        AND a non-paused/non-shutdown agent are all required. ``may_execute_background``
        subsumes the background switch and the paused/shutdown state, so pausing
        stops the timer instead of letting it submit wakes that would only be
        SKIPped. Never grants execution authority itself — the single
        ``may_execute_background`` gate still runs in ``_process``.
        """
        from backend.services.main_agent import MainAgentService

        session = self._db_manager.new_session()
        try:
            svc = MainAgentService(session)
            allowed, _ = svc.may_execute_background()
            return bool(allowed and svc.bounded_wake_enabled())
        finally:
            session.close()

    def start_bounded_loop(self, policy: WakePolicy) -> bool:
        """Start the bounded loop only for an explicitly enabled policy.

        Returns True when a loop was started, False when the policy is disabled
        (or already running). The owner gate is enforced twice: here, so a
        disabled policy creates no timer at all, and again inside every wake via
        ``may_execute_background`` in ``_process``. The loop cannot itself run a
        model, grant authority or change task state — it only submits ordinary
        durable wakes that flow through the existing gate and protected runtime.
        """
        if not isinstance(policy, WakePolicy):
            raise TypeError("policy must be a WakePolicy")
        if not policy.enabled or self.bounded_loop_running():
            return False
        # Fresh signal + error state for each loop lifetime. Selector and wake
        # handler are built inside the background task so the DB session is
        # created and used on the same event-loop thread.
        self._stop = asyncio.Event()
        self._last_fatal_error = None
        self._bounded_scheduler = BoundedWakeScheduler()
        self._bounded_task = asyncio.create_task(
            self._run_bounded_loop(policy), name="sythion-bounded-wake-loop"
        )
        logger.info(
            "Owner-gated bounded wake loop started (interval=%.1fs, max_cycles=%d)",
            policy.interval_seconds, policy.max_cycles,
        )
        return True

    async def _run_bounded_loop(self, policy: WakePolicy) -> None:
        from backend.services.goal_selector import RunnableGoalSelector

        session = self._db_manager.new_session()
        self._bounded_session = session
        selector = RunnableGoalSelector(session)

        # A fatal failure raised while submitting a wake is recorded here so
        # the rearm loop can stop. The exception is ALSO re-raised below: the
        # bounded scheduler counts a re-raised handler exception as a failed
        # cycle (never successful work), keeping the window metrics truthful.
        fatal: list = []

        async def wake_handler(goal_id: str) -> None:
            # Submits a NORMAL durable wake. Execution still goes through
            # _process -> may_execute_background -> protected runtime.
            # Each cycle is a distinct wake intent. A constant key would be
            # deduplicated after cycle 1 (submit_wake checks existence,
            # regardless of the row's status), so cycles 2..N would create no
            # PENDING row and the goal would never be re-woken. The UUID suffix
            # is unique across process restarts too, since the DB is durable.
            try:
                self.submit(
                    "goal.wake",
                    trigger_reference=goal_id,
                    wake_key=f"goal.wake:{goal_id}:{uuid.uuid4().hex}",
                )
            except asyncio.CancelledError:
                raise
            except Exception as exc:
                fatal.append(str(exc) or repr(exc))
                # End this window promptly; the rearm loop sees ``fatal`` and
                # will not open another window. Re-raise so the scheduler
                # records this as a failed cycle rather than successful work.
                sched = self._bounded_scheduler
                if sched is not None:
                    sched.stop()
                raise

        total_cycles = 0
        rearm_count = 0
        try:
            while True:
                if self._stop is not None and self._stop.is_set():
                    break

                # A fresh scheduler gives a fresh, still-bounded cycle window.
                # Per-window cap (WakePolicy.max_cycles) is never raised here.
                scheduler = BoundedWakeScheduler()
                self._bounded_scheduler = scheduler
                report = await scheduler.run(selector, wake_handler, policy)
                total_cycles += report.cycles

                logger.info(
                    "Bounded wake window complete: window_cycles=%d idle=%d "
                    "work=%d failed=%d total_cycles=%d rearm_count=%d",
                    report.cycles, report.idle_cycles, report.work_cycles,
                    report.failed_cycles, total_cycles, rearm_count,
                )

                if fatal:
                    self._last_fatal_error = fatal[0]
                    logger.error(
                        "Bounded wake fatal error; stopping rearm: %s", fatal[0]
                    )
                    break
                if (self._stop is not None and self._stop.is_set()) or report.stopped:
                    break

                # Normal budget exhaustion: re-arm only while the policy and
                # BOTH real owner switches still say "on". This is a new window
                # with the same bounded cap — never an unbounded loop.
                if not policy.enabled or not self._bounded_owner_enabled():
                    logger.info(
                        "Bounded wake loop stopping: owner switches disabled "
                        "(total_cycles=%d)", total_cycles,
                    )
                    break
                rearm_count += 1
                logger.info(
                    "Re-arming bounded wake window #%d (total_cycles=%d)",
                    rearm_count, total_cycles,
                )
        except asyncio.CancelledError:
            raise
        except Exception as exc:  # pragma: no cover - never kill the process
            self._last_fatal_error = str(exc) or repr(exc)
            logger.exception("Bounded wake loop failed: %s", exc)
        finally:
            session.close()
            self._bounded_session = None

    def stop_bounded_loop(self) -> None:
        # Set the persistent signal so the rearm loop stops between windows,
        # and stop the current scheduler so the inner window breaks promptly.
        if self._stop is not None:
            self._stop.set()
        if self._bounded_scheduler is not None:
            self._bounded_scheduler.stop()
        if self._bounded_task is not None and not self._bounded_task.done():
            self._bounded_task.cancel()
        self._bounded_task = None
        self._bounded_scheduler = None
        logger.info("Owner-gated bounded wake loop stopped")

    def apply_owner_policy(self, db) -> bool:
        """Reconcile the bounded loop with the owner switches AND agent state.

        Idempotent and safe to call after any policy/settings change or at
        startup. Creates no timer unless ``background_orchestration_enabled``,
        ``bounded_wake_enabled`` AND a non-paused/non-shutdown agent all hold;
        stops a running loop the moment any no longer does (including after
        POST /agent/pause or shutdown). Returns whether the loop is running.
        """
        from backend.core.config import settings
        from backend.services.main_agent import MainAgentService

        svc = MainAgentService(db)
        allowed, _ = svc.may_execute_background()
        if not (allowed and svc.bounded_wake_enabled()):
            self.stop_bounded_loop()
            return False
        if self.bounded_loop_running():
            return True
        return self.start_bounded_loop(WakePolicy(
            enabled=True,
            interval_seconds=settings.bounded_wake_interval_seconds,
            max_cycles=settings.bounded_wake_max_cycles,
        ))

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
        self.stop_bounded_loop()
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

        if deduplicated:
            # An existing row already represents this wake (it may even be
            # terminal). Tracking the key here would leave a permanent ghost
            # in _queued_keys that short-circuits every later submit() for it.
            return key
        self._queued_keys.add(key)
        if self._queue.qsize() < self._max_queue:
            self._queue.put_nowait({"wake_key": key})
        # Enqueued (optimal) or saturated: either way the PENDING row is the
        # source of truth and is drained durably if the queue drops it. The
        # key is cleared in _process's finally when the wake is resolved.
        return key

    def submit_task_terminal(self, task_id: str, state: str) -> Optional[str]:
        trigger = f"task.{state}"
        return self.submit(trigger, trigger_reference=task_id,
                           wake_key=f"{trigger}:{task_id}")

    @property
    def boot_id(self) -> str:
        """Token unique to this worker's process boot (for startup wake keys).

        Normally generated in ``__init__``. The lazy fallback keeps the token
        per-worker even for workers assembled with ``__new__`` in tests.
        """
        token = getattr(self, "_boot_id", None)
        if token is None:
            token = uuid.uuid4().hex
            self._boot_id = token
        return token

    def submit_startup(self, agent_id: str) -> Optional[str]:
        # Unique per process boot, not per agent: agent_id is durable and wake
        # rows are never deleted, so a constant key would silently dedup every
        # startup wake after boot #1. The boot token keeps repeats within this
        # process deduped while a fresh boot always wakes exactly once.
        return self.submit(
            "application.startup",
            trigger_reference=agent_id,
            wake_key=f"application.startup:{agent_id}:{self.boot_id}",
        )

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

    def is_processing(self, wake_key: str) -> bool:
        """Read-only liveness view over in-flight wake keys.

        Used as the CRITICAL guard by the periodic maintenance sweep before it
        reclaims a stale RUNNING wake. A key present here means this worker
        still owns the in-process execution (queued or mid-flight), so the
        sweep must not reclaim it — that would double-execute the wake.
        """
        return wake_key in self._queued_keys

    def requeue_reclaimed(self, wake_key: str) -> None:
        """Re-enqueue a wake that maintenance reclaimed to PENDING.

        Must be called on the event-loop thread, after the reclaim's atomic CAS
        has committed. ``submit()`` cannot be used here: it deduplicates on
        wake_key and the durable row still exists, so it would never enqueue.
        As with ``submit()``, the PENDING row — not the queue — is authoritative:
        if the bounded queue is saturated, ``drain_durable`` picks the wake up
        on the next processed item.
        """
        if wake_key in self._queued_keys:
            return  # already queued/in-flight; never enqueue twice
        self._queued_keys.add(wake_key)
        if self._queue.qsize() < self._max_queue:
            self._queue.put_nowait({"wake_key": wake_key})

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

            # Observability only: wake outcomes become events on the shared
            # reasoning bus. No event grants consent, starts a model call or
            # changes task state.
            emit_wake_outcome(wake.wake_key, result)

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
            # The wake is resolved (claimed/completed/skipped, or already gone).
            # Clear the in-flight key on BOTH paths — queue-driven _run and
            # drain_durable — otherwise a saturated wake leaves a permanent
            # ghost key that blocks every later submit() with the same key.
            self._queued_keys.discard(item.get("wake_key"))


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