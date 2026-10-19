"""
Task runner service with state machine implementation.
Manages single task execution with durable state.

PHASE 1 KERNEL OWNERSHIP:
- TaskRunner is the authoritative writer of task lifecycle state
  (docs/PROTECTED_RUNTIME_CONTRACT.md, "State ownership").
- Lease mechanics are delegated to runtime.leases.LeaseManager.
- Event appends/reads are delegated to runtime.events.DbEventLedger.
  The ledger participates in transition transactions so state change + event
  stay atomic; reads are side-effect free.
"""

import json
import logging
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.database import Task
from backend.api.schemas import TaskState
from backend.runtime.events import DbEventLedger
from backend.runtime.leases import LeaseManager, ensure_lease_contract  # noqa: F401

logger = logging.getLogger(__name__)


ALLOWED_TRANSITIONS = {
    # Phase 8.5: a task can be BORN needing user input — the cognitive gate
    # may park an ambiguous request directly into WAITING_FOR_USER.
    TaskState.CREATED.value: {TaskState.RUNNING.value, TaskState.WAITING_FOR_USER.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value},
    TaskState.RUNNING.value: {TaskState.WAITING_FOR_USER.value, TaskState.COMPLETED.value, TaskState.FAILED.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value, TaskState.PAUSED.value},
    TaskState.WAITING_FOR_USER.value: {TaskState.RUNNING.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value},
    TaskState.INTERRUPTED.value: {TaskState.RUNNING.value},
    TaskState.PAUSED.value: {TaskState.RUNNING.value, TaskState.CANCELLED.value},
    TaskState.COMPLETED.value: set(),
    TaskState.FAILED.value: set(),
    TaskState.CANCELLED.value: set(),
}


class TaskRunner:
    """Single task runner: authoritative owner of task lifecycle state."""

    def __init__(self, db_session: Session):
        self.db = db_session
        self._leases = LeaseManager(db_session)
        self.ledger = DbEventLedger(db_session)
        # Ownership mirror for callers/tests; source of truth is _leases.nonce.
        self._lease_nonce: Optional[str] = None
        self._current_task_id: Optional[str] = None

    # --- lease (delegated to LeaseManager) -----------------------------------

    async def acquire_lease(self, task_id: Optional[str] = None) -> bool:
        acquired = await self._leases.acquire(task_id=task_id)
        self._lease_nonce = self._leases.nonce
        self._current_task_id = self._leases.current_task_id
        return acquired

    async def renew_lease(self, task_id: Optional[str] = None) -> bool:
        renewed = await self._leases.renew(task_id=task_id)
        self._lease_nonce = self._leases.nonce
        self._current_task_id = self._leases.current_task_id
        return renewed

    async def release_lease(self):
        await self._leases.release()
        self._lease_nonce = None
        self._current_task_id = None

    def _mark_task_interrupted(self, task_id: str):
        """Deprecated internal hook; interruption lives in LeaseManager now."""
        return self._leases._mark_task_interrupted(task_id)

    # --- event ledger (delegated to DbEventLedger) ----------------------------

    def _append_event(self, task_id: str, event_type: str, event_data: Dict[str, Any]) -> Any:
        """Append the next immutable event while the caller owns the transaction."""
        return self.ledger.append_event(task_id, event_type, event_data)

    def _verify_lease(self) -> bool:
        """Verify we still hold the lease."""
        if not self._leases.nonce:
            return False
        from backend.models.database import RunnerLease

        lease = self.db.query(RunnerLease).filter_by(id=1).first()
        return lease and lease.owner_nonce == self._leases.nonce

    async def record_event(self, task_id: str, event_type: str, event_data: Dict[str, Any]) -> None:
        """Append a durable event without changing task state (e.g. budget usage)."""
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        self._append_event(task_id, event_type, event_data)
        self.db.commit()

    # --- task state ownership --------------------------------------------------

    async def create_task(self, original_request: str, idempotency_key: Optional[str] = None) -> Task:
        """Create a new task.

        Args:
            original_request: User's natural language request
            idempotency_key: Optional key for deduplication

        Returns:
            Created Task object

        Raises:
            RuntimeError: If lease not held
        """
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")

        # Check idempotency
        if idempotency_key:
            existing = self.db.query(Task).filter_by(idempotency_key=idempotency_key).first()
            if existing:
                if existing.original_request != original_request:
                    raise ValueError("Idempotency key is already bound to a different request")
                return existing

        import uuid

        task_id = str(uuid.uuid4())
        task = Task(
            id=task_id,
            original_request=original_request,
            idempotency_key=idempotency_key,
            state=TaskState.CREATED.value,
            version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        self.db.add(task)

        # Append creation event
        self._append_event(task_id, "task.created", {"original_request": original_request})
        try:
            self.db.commit()
        except IntegrityError:
            self.db.rollback()
            if not idempotency_key:
                raise
            existing = self.db.query(Task).filter_by(idempotency_key=idempotency_key).first()
            if existing and existing.original_request == original_request:
                return existing
            raise
        self.db.refresh(task)

        return task

    async def clear_pending_action(self, task_id: str) -> None:
        """Authoritative removal of a resolved pending-action request.

        Replaces direct ``task.pending_action = None`` writes by the
        execution loop (Phase 1 ownership boundary).
        """
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        task.pending_action = None
        task.updated_at = datetime.utcnow()
        self.db.commit()

    def set_task_objective(self, task_id: str, objective_id: str) -> Task:
        """Associate a task with its goal/objective (orchestration link).

        Phase 2: the Task Orchestrator links plan-step execution tasks to
        their goal through this authoritative method instead of writing the
        column directly.
        """
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        task.objective_id = objective_id
        task.updated_at = datetime.utcnow()
        self.db.commit()
        self.db.refresh(task)
        return task

    async def transition_state(
        self,
        task_id: str,
        new_state: TaskState,
        event_type: str,
        event_data: Dict[str, Any],
        **updates
    ) -> Task:
        """Transition a task to a new state.

        Args:
            task_id: Task ID
            new_state: New state
            event_type: Event type for audit log
            event_data: Event data dictionary
            **updates: Additional field updates

        Returns:
            Updated Task object

        Raises:
            RuntimeError: If lease not held or task not found
        """
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")

        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")

        old_state = task.state
        if new_state.value != old_state and new_state.value not in ALLOWED_TRANSITIONS.get(old_state, set()):
            raise RuntimeError(f"Invalid task state transition: {old_state} -> {new_state.value}")

        # Update state
        task.state = new_state.value
        task.version += 1  # Audit counter only — concurrency is guaranteed by the lease, not by version checking.
        task.updated_at = datetime.utcnow()

        # Apply additional updates
        for key, value in updates.items():
            setattr(task, key, value)

        self._append_event(task_id, event_type, event_data)
        self.db.commit()
        self.db.refresh(task)

        try:
            from backend.services.work_state import sync_objective_state_for_task
        except ImportError:
            from services.work_state import sync_objective_state_for_task

        try:
            sync_objective_state_for_task(
                self.db,
                task,
                event_type=event_type,
                event_data=event_data,
            )
        except Exception as exc:
            logger.warning(
                "Could not sync objective state for task %s transition %s: %s",
                task_id,
                event_type,
                exc,
            )

        return task

    async def record_action_started(
        self,
        task_id: str,
        action_id: str,
        tool_name: str,
        parameter_keys: List[str],
    ) -> None:
        """Durably record a mutating attempt before the operating-system call."""
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        self._leases.touch_task(task_id)
        self._current_task_id = task_id
        await self.transition_state(
            task_id=task_id,
            new_state=TaskState.RUNNING,
            event_type="action.started",
            event_data={
                "action_id": action_id,
                "tool_name": tool_name,
                "parameter_keys": sorted(parameter_keys),
            },
            active_action_id=action_id,
        )

    async def set_pending_action_status(self, task_id: str, status: str) -> None:
        """Set the status field inside task.pending_action JSON."""
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task or not task.pending_action:
            return
        try:
            data = json.loads(task.pending_action)
        except (json.JSONDecodeError, TypeError):
            data = {}
        data["status"] = status
        task.pending_action = json.dumps(data)
        task.updated_at = datetime.utcnow()
        self.db.commit()

    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self.db.query(Task).filter_by(id=task_id).first()

    async def list_tasks(self, limit: int = 50) -> List[Task]:
        """List tasks ordered by creation date."""
        return self.db.query(Task).order_by(
            Task.created_at.desc()
        ).limit(limit).all()

    async def cancel_task(self, task_id: str) -> Task:
        """Cancel a running task.

        Args:
            task_id: Task ID

        Returns:
            Updated Task object
        """
        return await self.transition_state(
            task_id=task_id,
            new_state=TaskState.CANCELLED,
            event_type="task.cancelled",
            event_data={"reason": "user_requested"},
            terminal_reason="cancelled_by_user",
        )

    async def force_cancel(self, task_id: str) -> Task:
        """Cancel a task without owning the runner lease.

        Used by the global-stop path (`POST /api/v1/stop`). The actively
        running executor holds and renews the lease itself, so requiring it
        here would make a stop a no-op exactly when it is most needed. The
        cancelled state is terminal and observed by the executor before its
        next OS action, so forcing it is safe.
        """
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")

        old_state = task.state
        if TaskState.CANCELLED.value not in ALLOWED_TRANSITIONS.get(old_state, set()):
            raise RuntimeError(f"Invalid task state transition: {old_state} -> cancelled")

        task.state = TaskState.CANCELLED.value
        task.version += 1
        task.updated_at = datetime.utcnow()
        task.terminal_reason = "cancelled_by_user"
        self._append_event(task_id, "task.cancelled", {
            "reason": "user_requested",
            "via": "global_stop",
        })
        self.db.commit()
        self.db.refresh(task)

        try:
            from backend.services.work_state import sync_objective_state_for_task
        except ImportError:
            from services.work_state import sync_objective_state_for_task

        try:
            sync_objective_state_for_task(
                self.db,
                task,
                event_type="task.cancelled",
                event_data={
                    "reason": "user_requested",
                    "via": "global_stop",
                },
            )
        except Exception as exc:
            logger.warning("Could not sync objective state for force-cancelled task %s: %s", task_id, exc)
        return task


# NOTE: TaskRunner instances are short-lived per-request objects. They
# hold a reference to a DB session, which must NOT be cached across
# requests (sessions are request-scoped and closed when the request ends).
# Always create a fresh TaskRunner with the current session.

def get_runner(db_session: Session) -> TaskRunner:
    """Create a TaskRunner bound to the given session.

    A fresh runner is created per call so it never holds a stale session
    from a previous request.
    """
    return TaskRunner(db_session)
