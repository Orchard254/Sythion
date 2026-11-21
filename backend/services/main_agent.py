"""Persistent Main Agent (Phase 4).

The Main Agent is the durable logical identity of Sythion as the user's
personal assistant. It is NOT an LLM: changing the model never changes this
identity, and the agent exists even with zero running tasks, zero active
goals, or no model provider available.

State domains are strictly separate:
  - Main Agent state  (this module): active/paused/degraded/shutdown
  - Goal state        (GoalManager)
  - Task state        (TaskRunner)

Responsibilities in THIS phase (continuity only):
  - persistent identity + lifecycle state
  - background-orchestration policy gate
  - wake recording/deduplication
  - deterministic self-model snapshot

It remains fully under Runtime Kernel authority: nothing here executes tools.
"""

from __future__ import annotations

import logging
import uuid
from datetime import datetime
from typing import Any, Dict, List, Optional

from backend.core.config import settings
from backend.core.settings_store import get_setting, set_setting
from backend.models.database import AgentWake, MainAgent, Objective
from backend.runtime.events import DbEventLedger

logger = logging.getLogger(__name__)

# Process boot marker: SHUTDOWN is sticky WITHIN a process (a runtime policy
# check must never resurrect it), but a NEW process boot deterministically
# transitions shutdown → active.
_BOOT_TIME = datetime.utcnow()


def _is_new_boot(row: MainAgent) -> bool:
    """True when the row's last update predates THIS process boot."""
    updated = row.updated_at or row.created_at or _BOOT_TIME
    return updated < _BOOT_TIME

AGENT_STATES = {"active", "paused", "degraded", "shutdown"}

# Phase 4.5 wake state machine (authoritative machine states, stored in
# AgentWake.status). `outcome` keeps a lowercase human summary for compat.
WAKE_PENDING = "PENDING"
WAKE_RUNNING = "RUNNING"
WAKE_COMPLETED = "COMPLETED"
WAKE_SKIPPED = "SKIPPED"
WAKE_DEFERRED = "DEFERRED"
WAKE_FAILED = "FAILED"
WAKE_STATES = {WAKE_PENDING, WAKE_RUNNING, WAKE_COMPLETED, WAKE_SKIPPED,
               WAKE_DEFERRED, WAKE_FAILED}

# Coalescing policy: background-capability triggers collapse into one
# 'background_work' slot because every session re-evaluates ALL runnable
# goals at execution time — no distinct state transition can be lost.
# Explicit user.request and application.startup are never coalesced.
_COALESCABLE_TRIGGERS = {
    "task.completed": "background_work",
    "task.failed": "background_work",
    "goal.resumed": "background_work",
    "goal.updated": "background_work",
}

# Bound the human-readable "last wake" summary so it can never carry an
# unbounded string into persisted state or the UI/morning brief.
_LAST_WAKE_REASON_CAP = 120


def coalesce_key_for(trigger_type: str) -> str:
    return _COALESCABLE_TRIGGERS.get(trigger_type, trigger_type)


def _scope(agent_id: str) -> str:
    return f"agent:{agent_id}"


class MainAgentService:
    """Owns the persistent main-agent row and its lifecycle decisions."""

    def __init__(self, session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # --- identity -------------------------------------------------------------

    def get_or_create(self) -> MainAgent:
        """Return the singleton agent row, minting a stable identity once.

        SHUTDOWN is not a sticky state: a fresh process always transitions a
        previously-shutdown agent back to ACTIVE deterministically at boot.
        """
        row = self.db.query(MainAgent).filter_by(id=1).first()
        if row is None:
            row = MainAgent(
                id=1,
                agent_id=str(uuid.uuid4()),
                name="Sythion",
                state="active",
                created_at=datetime.utcnow(),
                updated_at=datetime.utcnow(),
            )
            self.db.add(row)
            self.db.flush()
            self.ledger.append_event(_scope(row.agent_id), "agent.started", {
                "agent_id": row.agent_id,
            })
            self.db.commit()
            logger.info("Main agent identity minted: %s", row.agent_id)
            return row

        if row.state == "shutdown" and _is_new_boot(row):
            # A NEW process boot resets shutdown → active. Within the SAME
            # process, shutdown stays sticky (runtime policy checks must not
            # resurrect it).
            row.state = "active"
            row.updated_at = datetime.utcnow()
            self.ledger.append_event(_scope(row.agent_id), "agent.started", {
                "agent_id": row.agent_id, "previous": "shutdown",
            })
            self.db.commit()
        return row

    def get(self) -> Optional[MainAgent]:
        return self.db.query(MainAgent).filter_by(id=1).first()

    def set_state(self, new_state: str, reason: Optional[str] = None) -> MainAgent:
        """Transition the agent lifecycle state (validated)."""
        assert new_state in AGENT_STATES, f"unknown agent state {new_state}"
        row = self.get_or_create()
        if row.state == new_state:
            return row
        previous = row.state
        row.state = new_state
        row.updated_at = datetime.utcnow()
        event = {
            "paused": "agent.paused",
            "active": "agent.resumed",
            "shutdown": "agent.stopped",
        }.get(new_state, f"agent.{new_state}")
        self.ledger.append_event(_scope(row.agent_id), event, {
            "previous": previous,
            "reason": reason,
        })
        self.db.commit()
        self.db.refresh(row)
        return row

    # --- policy ------------------------------------------------------------------

    def background_enabled(self) -> bool:
        """Explicit operator switch: persisted override wins over the default."""
        persisted = get_setting(self.db, "background_orchestration_enabled")
        if persisted is None:
            return bool(settings.background_orchestration_enabled)
        return persisted.strip().lower() == "true"

    def set_background_enabled(self, enabled: bool) -> bool:
        set_setting(self.db, "background_orchestration_enabled",
                    "true" if enabled else "false")
        self.db.commit()
        return enabled

    def bounded_wake_enabled(self) -> bool:
        """Owner switch for the bounded time-driven wake loop (default OFF).

        Independent of the event-driven background switch: both must be on
        before a timer is created. The persisted override wins over the config
        default, exactly like ``background_enabled``.
        """
        persisted = get_setting(self.db, "bounded_wake_enabled")
        if persisted is None:
            return bool(settings.bounded_wake_enabled)
        return persisted.strip().lower() == "true"

    def set_bounded_wake_enabled(self, enabled: bool) -> bool:
        set_setting(self.db, "bounded_wake_enabled",
                    "true" if enabled else "false")
        self.db.commit()
        return enabled

    def may_execute_background(self) -> tuple:
        """(allowed, reason) — the single policy gate for background sessions."""
        row = self.get_or_create()
        if row.state == "paused":
            return False, "agent_paused"
        if row.state == "shutdown":
            return False, "agent_shutdown"
        if not self.background_enabled():
            return False, "background_disabled"
        return True, "ok"

    # --- wakes (Phase 4.5 durable state machine) ------------------------------

    def submit_wake(self, trigger_type: str, trigger_reference: Optional[str] = None,
                    wake_key: Optional[str] = None) -> Dict[str, Any]:
        """Durably persist a wake request as PENDING — BEFORE any queuing.

        This is the authoritative record: queue saturation, worker crashes,
        or process death can no longer lose the wake. Deduplication is the
        UNIQUE constraint on wake_key. Returns {'wake', 'deduplicated'}.
        """
        row = self.get_or_create()
        key = wake_key or f"{trigger_type}:{trigger_reference or ''}"
        existing = self.db.query(AgentWake).filter_by(wake_key=key).first()
        if existing is not None:
            return {"wake": existing, "deduplicated": True}

        try:
            wake = AgentWake(
                agent_id=row.agent_id,
                wake_key=key,
                trigger_type=trigger_type,
                trigger_reference=trigger_reference,
                status=WAKE_PENDING,
                coalesce_key=coalesce_key_for(trigger_type),
                started_at=datetime.utcnow(),
            )
            self.db.add(wake)
            self.db.commit()
        except Exception:
            self.db.rollback()
            existing = self.db.query(AgentWake).filter_by(wake_key=key).first()
            return {"wake": existing, "deduplicated": True}

        self.ledger.append_event(_scope(row.agent_id), "agent.wake.requested", {
            "wake_id": wake.id, "wake_key": key,
            "trigger_type": trigger_type,
            "trigger_reference": trigger_reference,
        })
        return {"wake": wake, "deduplicated": False}

    def claim_wake(self, wake_key: str) -> Optional[AgentWake]:
        """Atomically PENDING → RUNNING. Loser of the race gets None, which
        guarantees exactly one logical execution per wake_key."""
        from sqlalchemy import update

        now = datetime.utcnow()
        updated = self.db.execute(
            update(AgentWake)
            .where(AgentWake.wake_key == wake_key)
            .where(AgentWake.status == WAKE_PENDING)
            .values(status=WAKE_RUNNING, claimed_at=now,
                    attempts=AgentWake.attempts + 1)
        )
        if updated.rowcount != 1:
            self.db.rollback()
            return None
        agent = self.get_or_create()
        self.ledger.append_event(_scope(agent.agent_id), "agent.execution.started", {
            "wake_key": wake_key,
        })
        self.db.commit()
        return self.db.query(AgentWake).filter_by(wake_key=wake_key).first()

    def _caller_pending_orm_state(self) -> bool:
        pending = False
        for collection_name in ("new", "dirty", "deleted"):
            collection = getattr(self.db, collection_name, None)
            try:
                if collection:
                    pending = True
            except TypeError:
                continue
            except Exception as exc:
                raise RuntimeError(
                    "MainAgentService could not determine caller ORM transaction state"
                ) from exc
        return pending

    def _physical_transaction_active(self) -> bool:
        try:
            connection = self.db.connection()
            dbapi_connection = getattr(connection, "connection", None)
            dbapi_connection = getattr(
                dbapi_connection, "dbapi_connection", dbapi_connection
            )
            if dbapi_connection is None:
                raise RuntimeError(
                    "MainAgentService could not determine caller physical transaction state"
                )
            transaction_state = dbapi_connection.in_transaction
        except Exception as exc:
            raise RuntimeError(
                "MainAgentService could not determine caller physical transaction state"
            ) from exc
        if type(transaction_state) is not bool or callable(transaction_state):
            raise RuntimeError(
                "MainAgentService could not determine caller physical transaction state"
            )
        return transaction_state

    def _refuse_caller_owned_pending_transaction(self) -> None:
        pending = self._caller_pending_orm_state()
        if pending:
            raise RuntimeError(
                "MainAgentService refused to mutate a caller-owned transaction with pending work"
            )
        if self._physical_transaction_active():
            raise RuntimeError(
                "MainAgentService refused to mutate an active caller-owned transaction"
            )

    def absorb_coalescable(self, wake: AgentWake) -> int:
        """Absorb other PENDING wakes with the same coalesce_key.

        Safe because a session re-evaluates all runnable goals at execution
        time; absorbing loses nothing. Absorbed rows become COMPLETED with
        outcome 'coalesced' + merged_into pointer (fully auditable).
        """
        # Ownership guard first: before autoflush/expired attr reads/UPDATE.
        self._refuse_caller_owned_pending_transaction()

        from sqlalchemy import update

        absorbed = self.db.execute(
            update(AgentWake)
            .where(AgentWake.coalesce_key == wake.coalesce_key)
            .where(AgentWake.status == WAKE_PENDING)
            .where(AgentWake.id != wake.id)
            .values(status=WAKE_COMPLETED, outcome="coalesced",
                    merged_into=wake.id, finished_at=datetime.utcnow())
        )
        count = absorbed.rowcount
        if count:
            agent = self.get_or_create()
            self.ledger.append_event(_scope(agent.agent_id), "agent.wake.coalesced", {
                "into_wake_id": wake.id, "absorbed": count,
                "coalesce_key": wake.coalesce_key,
            })
        self.db.commit()
        return count

    def finish_wake(
        self,
        wake: AgentWake,
        outcome: str = "completed",
        stop_reason: Optional[str] = None,
        steps_attempted: int = 0,
        steps_completed: int = 0,
        status: Optional[str] = None,
    ) -> AgentWake:
        """Resolve a wake to a terminal machine state.

        ``status`` wins when given; otherwise it is derived from ``outcome``
        (case-insensitive) defaulting to COMPLETED. ``outcome`` keeps a
        lowercase human summary for compatibility.
        """
        resolved_status = (status or outcome).upper()
        assert resolved_status in WAKE_STATES, f"invalid wake state {resolved_status}"
        wake.status = resolved_status
        wake.outcome = (status or outcome).lower()
        wake.stop_reason = stop_reason
        wake.steps_attempted = steps_attempted
        wake.steps_completed = steps_completed
        wake.finished_at = datetime.utcnow()
        self.ledger.append_event(_scope(wake.agent_id), "agent.wake.completed", {
            "wake_id": wake.id,
            "status": resolved_status,
            "stop_reason": stop_reason,
            "steps_attempted": steps_attempted,
            "steps_completed": steps_completed,
        })
        agent = self.get_or_create()
        agent.last_session_outcome = f"{resolved_status}:{stop_reason}" if stop_reason else resolved_status
        # Truthful "last wake" surface read by the UI and morning brief.
        # Derived only from controlled fields (trigger_type + stop_reason or
        # outcome) — never prompt text or secrets — and length-bounded.
        reason = f"{wake.trigger_type or 'wake'}:{stop_reason or outcome}"
        agent.last_wake_reason = reason[:_LAST_WAKE_REASON_CAP]
        agent.last_wake_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(wake)
        return wake

    def skip_wake(self, trigger_type: str, reason: str,
                  trigger_reference: Optional[str] = None,
                  wake_key: Optional[str] = None) -> Dict[str, Any]:
        """Record why a wake did NOT run (policy/state) — auditable no-op.

        Deliberately does NOT update ``last_wake_at``/``last_wake_reason``:
        those fields are reserved for wakes that actually executed (see
        :meth:`finish_wake`), so the UI's "Last wake" never reports a no-op as
        though work happened. Skips remain fully auditable in AgentWake + ledger.
        """
        row = self.get_or_create()
        key = wake_key or f"{trigger_type}:{trigger_reference or ''}"
        existing = self.db.query(AgentWake).filter_by(wake_key=key).first()
        if existing is not None and existing.status not in (WAKE_PENDING,):
            return {"wake": existing, "deduplicated": True}
        wake = existing or AgentWake(
            agent_id=row.agent_id, wake_key=key,
            trigger_type=trigger_type, trigger_reference=trigger_reference,
            coalesce_key=coalesce_key_for(trigger_type),
            started_at=datetime.utcnow(),
        )
        wake.status = WAKE_SKIPPED
        wake.outcome = "skipped"
        wake.stop_reason = reason
        wake.finished_at = datetime.utcnow()
        if wake.id is None:
            self.db.add(wake)
        self.ledger.append_event(_scope(row.agent_id), "agent.wake.skipped", {
            "wake_key": key, "trigger_type": trigger_type, "reason": reason,
        })
        self.db.commit()
        return {"wake": wake, "deduplicated": False}

    def begin_wake(self, trigger_type: str, trigger_reference: Optional[str] = None,
                   wake_key: Optional[str] = None) -> Dict[str, Any]:
        """Compatibility wrapper: durably submit (dedup by unique key).

        Callers that execute synchronously then claim via :meth:`claim_wake`
        and resolve via :meth:`finish_wake`.
        """
        return self.submit_wake(trigger_type, trigger_reference, wake_key)

    def recover_pending_wakes(self, boot_time: datetime) -> Dict[str, int]:
        """Startup recovery for durable wakes (idempotent).

        - PENDING rows stay PENDING (the worker drains them);
        - RUNNING rows orphaned by a dead worker (claimed before this boot)
          are reclaimed back to PENDING up to 5 times, then FAILED;
        - terminal rows untouched.
        Reuses the lease/recovery philosophy: stale ownership is detected and
        reclaimed; nothing is executed during recovery itself.
        """
        from sqlalchemy import update

        # Fail-closed: do not mutate through a caller-owned transaction.
        self._refuse_caller_owned_pending_transaction()

        reclaimed = 0
        failed_reclaims = 0
        reconciled_terminal_wakes = 0
        # A crash can occur between a task's durable terminal commit and the
        # best-effort terminal-wake hook. Reconcile recent terminal tasks on
        # boot so those outcomes are not permanently invisible to the worker.
        try:
            from datetime import timedelta

            from backend.models.database import Task

            ref_time = boot_time or datetime.utcnow()
            recent_cutoff = ref_time - timedelta(days=7)
            terminal_tasks = [
                task for task in self.db.query(Task).filter(
                    Task.state.in_(("completed", "failed", "cancelled")),
                ).all()
                if (task.updated_at or task.created_at) is None
                or (task.updated_at or task.created_at) >= recent_cutoff
            ]
            terminal_tasks.sort(
                key=lambda task: task.updated_at or task.created_at or recent_cutoff
            )
            terminal_tasks = terminal_tasks[:100]
            for task in terminal_tasks:
                try:
                    trigger = f"task.{task.state}"
                    key = f"{trigger}:{task.id}"
                    existing = self.db.query(AgentWake).filter_by(wake_key=key).first()
                    if existing is None:
                        self.submit_wake(
                            trigger, trigger_reference=task.id, wake_key=key
                        )
                        reconciled_terminal_wakes += 1
                except Exception:
                    logger.warning(
                        "Task-terminal wake reconciliation skipped for task %s",
                        getattr(task, "id", None),
                        exc_info=True,
                    )
        except Exception:
            logger.warning("Task-terminal wake reconciliation skipped", exc_info=True)
        orphans = (
            self.db.query(AgentWake)
            .filter(AgentWake.status == WAKE_RUNNING)
            .all()
        )
        for wake in orphans:
            claimed_at = wake.claimed_at or wake.started_at or boot_time
            if claimed_at >= boot_time:
                continue  # belongs to THIS process; still live
            did_reclaim = False
            if (wake.attempts or 0) >= 5:
                wake.status = WAKE_FAILED
                wake.outcome = "reclaim_limit"
                wake.stop_reason = "worker crashed repeatedly"
                wake.finished_at = datetime.utcnow()
                failed_reclaims += 1
            else:
                wake.status = WAKE_PENDING
                reclaimed += 1
                did_reclaim = True
            self.ledger.append_event(_scope(wake.agent_id),
                                     "agent.wake.reclaimed" if did_reclaim else "agent.wake.failed",
                                     {"wake_key": wake.wake_key,
                                      "attempts": wake.attempts})
        self.db.commit()
        pending = (
            self.db.query(AgentWake)
            .filter(AgentWake.status == WAKE_PENDING)
            .count()
        )
        return {"reclaimed": reclaimed, "failed_reclaims": failed_reclaims,
                "reconciled_terminal_wakes": reconciled_terminal_wakes,
                "pending": pending}

    # --- activity / self-model -------------------------------------------------------

    def recent_wakes(self, limit: int = 20) -> List[Dict[str, Any]]:
        rows = (
            self.db.query(AgentWake)
            .order_by(AgentWake.id.desc())
            .limit(limit)
            .all()
        )
        return [
            {
                "id": w.id, "wake_key": w.wake_key,
                "trigger_type": w.trigger_type,
                "trigger_reference": w.trigger_reference,
                "started_at": w.started_at, "finished_at": w.finished_at,
                "outcome": w.outcome, "stop_reason": w.stop_reason,
                "steps_attempted": w.steps_attempted,
                "steps_completed": w.steps_completed,
            }
            for w in rows
        ]

    def self_model(self) -> Dict[str, Any]:
        """Deterministic capability/state snapshot (no LLM involvement)."""
        from backend.api.dependencies import get_tool_executor

        agent = self.get_or_create()
        tool_names = []
        try:
            tool_names = get_tool_executor().list_tools()
        except Exception:  # pragma: no cover - diagnostics must not raise
            tool_names = []

        active_goals = (
            self.db.query(Objective)
            .filter(Objective.status == "active")
            .count()
        )
        return {
            "agent_id": agent.agent_id,
            "state": agent.state,
            "model_selected": settings.selected_model or None,
            "tools_installed": len(tool_names),
            "goals_active": active_goals,
            "policies": {
                "background_orchestration_enabled": self.background_enabled(),
                "autonomy_level": settings.autonomy_level,
                "step_max_attempts": settings.step_max_attempts,
                "max_steps_per_background_cycle": settings.max_steps_per_background_cycle,
            },
            "last_wake_reason": agent.last_wake_reason,
            "last_session_outcome": agent.last_session_outcome,
        }