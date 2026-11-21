"""LeaseManager implementation over the ``runner_lease`` row.

Extracted verbatim from TaskRunner (Phase 1 kernel extraction). Governing
timing contract lives in ``backend/core/config.py``:
``lease_timeout_seconds`` (expiry / stale-takeover threshold) and
``lease_heartbeat_seconds`` (renewal interval, must be strictly smaller).
"""

from __future__ import annotations

import os
import uuid
from datetime import datetime, timedelta
from typing import Optional

from sqlalchemy.orm import Session

from backend.core.config import settings
from backend.models.database import Task, RunnerLease


def ensure_lease_contract() -> None:
    """Validate the heartbeat/expiry relationship; raise on misconfiguration."""
    if settings.lease_heartbeat_seconds >= settings.lease_timeout_seconds:
        raise RuntimeError(
            "Invalid lease configuration: lease_heartbeat_seconds must be "
            "strictly smaller than lease_timeout_seconds"
        )


ensure_lease_contract()


def mark_task_interrupted(session: Session, task_id: str) -> None:
    """Mark a non-terminal task INTERRUPTED when its owner is lost.

    Shared by the stale-lease takeover path in :class:`LeaseManager` and by the
    periodic maintenance sweep (which detects lost task ownership independently
    of the runner lease — the heartbeat renews task ownership, not the lease).
    Idempotent: a task no longer in a live state is left untouched. An active
    action is recorded as ``action.uncertain`` before the task is interrupted;
    the sweep never resumes a task — INTERRUPTED is terminal for it.
    """
    from backend.api.schemas import TaskState

    task = session.query(Task).filter_by(id=task_id).first()
    if not (task and task.state in {
        TaskState.CREATED.value,
        TaskState.RUNNING.value,
        TaskState.WAITING_FOR_USER.value,
    }):
        return

    from backend.runtime.events import DbEventLedger

    ledger = DbEventLedger(session)
    if task.active_action_id:
        ledger.append_event(
            task_id,
            "action.uncertain",
            {"action_id": task.active_action_id, "reason": "runner_crash_or_restart"},
        )
    task.state = TaskState.INTERRUPTED.value
    task.active_action_id = None
    task.version += 1
    task.updated_at = datetime.utcnow()
    ledger.append_event(
        task_id,
        "task.interrupted",
        {
            "reason": "runner_crash_or_restart",
            "detected_at": datetime.utcnow().isoformat(),
            "note": "Task was running when runner lease expired. Resume will re-observe before continuing.",
        },
    )
    session.commit()
    try:
        from backend.services.work_state import sync_objective_state_for_task
    except ImportError:
        from services.work_state import sync_objective_state_for_task

    try:
        sync_objective_state_for_task(
            session,
            task,
            event_type="task.interrupted",
            event_data={
                "reason": "runner_crash_or_restart",
                "note": "Task was running when runner lease expired. Resume will re-observe before continuing.",
            },
        )
    except Exception as exc:  # pragma: no cover - projection is best-effort
        import logging

        logging.getLogger(__name__).warning(
            "Could not sync objective state for interrupted task %s: %s", task_id, exc
        )


class LeaseManager:
    """Exclusive single-runner lease over the profile's execution."""

    def __init__(self, session: Session):
        self._session = session
        self._nonce: Optional[str] = None
        self._current_task_id: Optional[str] = None

    @property
    def nonce(self) -> Optional[str]:
        return self._nonce

    @property
    def current_task_id(self) -> Optional[str]:
        return self._current_task_id

    def _mark_task_interrupted(self, task_id: str) -> None:
        """Stale-lease takeover hook; delegates to the shared recovery helper."""
        mark_task_interrupted(self._session, task_id)

    async def acquire(self, task_id: Optional[str] = None) -> bool:
        nonce = str(uuid.uuid4())
        now = datetime.utcnow()
        expires = now + timedelta(seconds=settings.lease_timeout_seconds)

        existing = self._session.query(RunnerLease).filter_by(id=1).first()
        if existing:
            # Stale if expired OR owning process is dead (process-kill recovery without waiting for timeout)
            is_expired = now > existing.expires_at
            is_dead = False
            if not is_expired and existing.process_id:
                try:
                    import psutil
                    is_dead = not psutil.pid_exists(existing.process_id)
                except Exception:
                    # Fallback: on Windows, use OpenProcess check
                    try:
                        import ctypes
                        from ctypes import wintypes
                        handle = ctypes.windll.kernel32.OpenProcess(0x0400, False, existing.process_id)
                        if not handle:
                            is_dead = ctypes.windll.kernel32.GetLastError() == 87  # invalid pid
                        else:
                            ctypes.windll.kernel32.CloseHandle(handle)
                    except Exception:
                        is_dead = False
            if is_expired or is_dead:
                # Stale lease per the governing expiry threshold or dead owner.
                if existing.last_task_id:
                    self._mark_task_interrupted(existing.last_task_id)
                existing.owner_nonce = nonce
                existing.process_id = os.getpid()
                existing.heartbeat = now
                existing.expires_at = expires
                existing.last_task_id = task_id
                self._session.commit()
                self._nonce = nonce
                self._current_task_id = task_id
                return True
            return False

        lease = RunnerLease(
            id=1,
            owner_nonce=nonce,
            process_id=os.getpid(),
            heartbeat=now,
            expires_at=expires,
            last_task_id=task_id,
        )
        self._session.add(lease)
        self._session.commit()
        self._nonce = nonce
        self._current_task_id = task_id
        return True

    def touch_task(self, task_id: str) -> None:
        """Record the task a mutation is about to start on (crash recovery)."""
        if not self._nonce:
            return
        lease = self._session.query(RunnerLease).filter_by(id=1).first()
        if lease and lease.owner_nonce == self._nonce:
            lease.last_task_id = task_id
            self._current_task_id = task_id
            self._session.commit()

    async def renew(self, task_id: Optional[str] = None) -> bool:
        if not self._nonce:
            return False
        lease = self._session.query(RunnerLease).filter_by(id=1).first()
        if not lease or lease.owner_nonce != self._nonce:
            self._nonce = None
            return False
        now = datetime.utcnow()
        lease.heartbeat = now
        lease.expires_at = now + timedelta(seconds=settings.lease_timeout_seconds)
        if task_id:
            lease.last_task_id = task_id
            self._current_task_id = task_id
        self._session.commit()
        return True

    async def release(self) -> None:
        if not self._nonce:
            return
        lease = self._session.query(RunnerLease).filter_by(id=1).first()
        if lease and lease.owner_nonce == self._nonce:
            lease.last_task_id = None
            self._session.delete(lease)
            self._session.commit()
        self._nonce = None
        self._current_task_id = None
