"""
Task runner service with state machine implementation.
Manages single task execution with durable state.
"""

import asyncio
import logging
import uuid
from datetime import datetime, timedelta
from typing import Dict, Any, Optional, List
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.database import Task, TaskEvent, RunnerLease
from backend.api.schemas import TaskState

logger = logging.getLogger(__name__)


ALLOWED_TRANSITIONS = {
    TaskState.CREATED.value: {TaskState.RUNNING.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value},
    TaskState.RUNNING.value: {TaskState.WAITING_FOR_USER.value, TaskState.COMPLETED.value, TaskState.FAILED.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value, TaskState.PAUSED.value},
    TaskState.WAITING_FOR_USER.value: {TaskState.RUNNING.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value},
    TaskState.INTERRUPTED.value: {TaskState.RUNNING.value},
    TaskState.PAUSED.value: {TaskState.RUNNING.value, TaskState.CANCELLED.value},
    TaskState.COMPLETED.value: set(),
    TaskState.FAILED.value: set(),
    TaskState.CANCELLED.value: set(),
}


class TaskRunner:
    """Single task runner with lease management."""

    def __init__(self, db_session: Session):
        self.db = db_session
        self._lease_nonce: Optional[str] = None
        self._current_task_id: Optional[str] = None

    async def acquire_lease(self, task_id: Optional[str] = None) -> bool:
        """Acquire exclusive runner lease.

        Args:
            task_id: Optional task ID being resumed
            
        Returns:
            True if lease acquired, False if another runner holds it
        """
        nonce = str(uuid.uuid4())
        
        # Check for existing lease
        existing = self.db.query(RunnerLease).filter_by(id=1).first()
        
        if existing:
            # Check if lease is stale (expired heartbeat - 30 second timeout)
            if datetime.utcnow() > existing.expires_at:
                # Stale lease detected - mark any running task as interrupted
                if existing.last_task_id:
                    self._mark_task_interrupted(existing.last_task_id)
                
                # Take over the lease
                existing.owner_nonce = nonce
                existing.process_id = id(self)
                existing.heartbeat = datetime.utcnow()
                existing.expires_at = datetime.utcnow() + timedelta(seconds=30)
                existing.last_task_id = task_id
                self.db.commit()
                self._lease_nonce = nonce
                self._current_task_id = task_id
                return True
            else:
                # Active lease held by another runner
                return False
        else:
            # No lease exists, create new one
            lease = RunnerLease(
                id=1,
                owner_nonce=nonce,
                process_id=id(self),
                heartbeat=datetime.utcnow(),
                expires_at=datetime.utcnow() + timedelta(seconds=30),
                last_task_id=task_id,
            )
            self.db.add(lease)
            self.db.commit()
            self._lease_nonce = nonce
            self._current_task_id = task_id
            return True

    async def renew_lease(self, task_id: Optional[str] = None) -> bool:
        """Renew the runner lease heartbeat.

        Args:
            task_id: Optional task ID to update
            
        Returns:
            True if renewal successful, False if lease lost
        """
        if not self._lease_nonce:
            return False

        lease = self.db.query(RunnerLease).filter_by(id=1).first()
        if not lease or lease.owner_nonce != self._lease_nonce:
            self._lease_nonce = None
            return False

        lease.heartbeat = datetime.utcnow()
        lease.expires_at = datetime.utcnow() + timedelta(seconds=30)
        if task_id:
            lease.last_task_id = task_id
            self._current_task_id = task_id
        self.db.commit()
        return True

    async def release_lease(self):
        """Release the runner lease."""
        if self._lease_nonce:
            lease = self.db.query(RunnerLease).filter_by(id=1).first()
            if lease and lease.owner_nonce == self._lease_nonce:
                # Clear last_task_id on clean release
                lease.last_task_id = None
                self.db.delete(lease)
                self.db.commit()
            self._lease_nonce = None
            self._current_task_id = None

    def _mark_task_interrupted(self, task_id: str):
        """Mark a running task as interrupted due to crash/restart.
        
        This is called when we detect a stale lease and need to mark
        the previously running task as interrupted.
        
        Args:
            task_id: ID of the task that was running
        """
        task = self.db.query(Task).filter_by(id=task_id).first()
        if task and task.state in {TaskState.CREATED.value, TaskState.RUNNING.value, TaskState.WAITING_FOR_USER.value}:
            if task.active_action_id:
                self._append_event(
                    task_id,
                    "action.uncertain",
                    {
                        "action_id": task.active_action_id,
                        "reason": "runner_crash_or_restart",
                    },
                )
            # Mark task as interrupted
            task.state = TaskState.INTERRUPTED.value
            task.active_action_id = None
            task.version += 1
            task.updated_at = datetime.utcnow()
            
            self._append_event(task_id, "task.interrupted", {
                    "reason": "runner_crash_or_restart",
                    "detected_at": datetime.utcnow().isoformat(),
                    "note": "Task was running when runner lease expired. Resume will re-observe before continuing."
                })
            self.db.commit()
            logger.info(f"Marked task {task_id} as interrupted due to stale lease")

    def _append_event(self, task_id: str, event_type: str, event_data: Dict[str, Any]) -> TaskEvent:
        """Append the next immutable event while the caller owns the transaction."""
        import json

        last_event = self.db.query(TaskEvent).filter_by(task_id=task_id).order_by(
            TaskEvent.sequence_num.desc()
        ).first()
        event = TaskEvent(
            task_id=task_id,
            event_type=event_type,
            event_data=json.dumps(event_data),
            sequence_num=(last_event.sequence_num + 1) if last_event else 1,
            created_at=datetime.utcnow(),
        )
        self.db.add(event)
        return event

    def _verify_lease(self) -> bool:
        """Verify we still hold the lease."""
        if not self._lease_nonce:
            return False
        
        lease = self.db.query(RunnerLease).filter_by(id=1).first()
        return lease and lease.owner_nonce == self._lease_nonce

    async def record_event(self, task_id: str, event_type: str, event_data: Dict[str, Any]) -> None:
        """Append a durable event without changing task state (e.g. budget usage)."""
        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        self._append_event(task_id, event_type, event_data)
        self.db.commit()

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
        task.version += 1
        task.updated_at = datetime.utcnow()

        # Apply additional updates
        for key, value in updates.items():
            setattr(task, key, value)

        self._append_event(task_id, event_type, event_data)
        self.db.commit()
        self.db.refresh(task)

        return task

    async def record_action_started(
        self,
        task_id: str,
        action_id: str,
        tool_name: str,
        parameter_keys: List[str],
    ) -> None:
        """Durably record a mutating attempt before the operating-system call."""
        lease = self.db.query(RunnerLease).filter_by(id=1).first()
        if not lease or lease.owner_nonce != self._lease_nonce:
            raise RuntimeError("Runner lease not held")
        lease.last_task_id = task_id
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
