"""
Task runner service with state machine implementation.
Manages single task execution with durable state.

PHASE 1 KERNEL OWNERSHIP:
- TaskRunner is the authoritative writer of task lifecycle state
  (, "State ownership").
- Lease mechanics are delegated to runtime.leases.LeaseManager.
- Event appends/reads are delegated to runtime.events.DbEventLedger.
  The ledger participates in transition transactions so state change + event
  stay atomic; reads are side-effect free.
"""

import json
import logging
import inspect
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from backend.models.database import Task
from backend.api.schemas import TaskState
try:
    from backend.runtime.task_ownership import TaskOwnershipManager
except ImportError:  # pragma: no cover
    from runtime.task_ownership import TaskOwnershipManager
from backend.runtime.events import DbEventLedger
from backend.runtime.leases import LeaseManager, ensure_lease_contract  # noqa: F401

logger = logging.getLogger(__name__)

# Transaction-bound runner ownership.  Keys are the underlying SQLite DBAPI
# connection identity; values are the runner instance that started the active
# physical transaction.  This mapping is private and is cleared as soon as the
# physical transaction ends, so it cannot be forged through session.info or
# persist from one transaction to the next.
_RUNNER_TRANSACTION_OWNERS: dict = {}


ALLOWED_TRANSITIONS = {
    # Phase 8.5: a task can be BORN needing user input — the cognitive gate
    # may park an ambiguous request directly into WAITING_FOR_USER.
    TaskState.CREATED.value: {TaskState.RUNNING.value, TaskState.WAITING_FOR_USER.value, TaskState.CANCELLED.value, TaskState.INTERRUPTED.value, TaskState.FAILED.value},
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

    @property
    def _task_ownership_task_id(self) -> Optional[str]:
        return self._current_task_id

    @_task_ownership_task_id.setter
    def _task_ownership_task_id(self, value: Optional[str]) -> None:
        self._current_task_id = value

    def __init__(self, db_session: Session):
        self.db = db_session
        self._leases = LeaseManager(db_session)
        self.ledger = DbEventLedger(db_session)
        # Compatibility shim for the task-ownership primitive expected by
        # execution loop/revision/tests. This mirrors the newer integration
        # without removing the existing lease implementation.
        self.task_ownership = TaskOwnershipManager(db_session)
        self._task_ownership_nonce: Optional[str] = None
        self._task_ownership_fencing_token: Optional[int] = None
        self._task_ownership_fence: Optional[int] = None
        self._current_task_id: Optional[str] = None
        self._lease_nonce: Optional[str] = None
        self._runner_mutation_depth: int = 0
        self._runner_context_depth: int = 0

    @property
    def task_ownership_nonce(self) -> Optional[str]:
        return self._task_ownership_nonce

    async def acquire_task_ownership(self, task_id: str, worker_id: str = None, **kwargs) -> Any:
        self._ensure_transaction()
        try:
            result = self.task_ownership.acquire(
                task_id=task_id,
                worker_id=worker_id if worker_id is not None else "",
                ttl_seconds=kwargs.get("ttl_seconds"),
                process_id=kwargs.get("process_id"),
            )
            if getattr(result, "acquired", False):
                self._task_ownership_nonce = getattr(result, "nonce", None)
                self._task_ownership_fencing_token = getattr(result, "fencing_token", None)
                self._task_ownership_fence = self._task_ownership_fencing_token
                self._current_task_id = task_id
                try:
                    await self.acquire_lease(task_id=task_id)
                except Exception:
                    pass
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
            else:
                self.db.rollback()
            return result
        except Exception:
            self.db.rollback()
            raise
        finally:
            self._clear_runner_transaction_ownership()
    async def release_task_ownership(self, task_id: str, **kwargs) -> Any:
        nonce = kwargs.get("nonce", self._task_ownership_nonce)
        outcome = kwargs.get("outcome", kwargs.get("release_outcome"))
        self._ensure_transaction()
        try:
            result = self.task_ownership.release(
                task_id=task_id,
                nonce=nonce,
                outcome=outcome,
            )
            if result:
                self._task_ownership_nonce = None
                self._task_ownership_fencing_token = None
                self._task_ownership_fence = None
                self._current_task_id = None
                try:
                    await self.release_lease()
                except Exception:
                    pass
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
            else:
                self.db.rollback()
            return result
        except Exception:
            self.db.rollback()
            raise
        finally:
            self._clear_runner_transaction_ownership()
    async def renew_task_ownership(self, task_id: str = None, **kwargs) -> bool:
        task_id = task_id or self._current_task_id
        nonce = kwargs.get("nonce", self._task_ownership_nonce)
        self._ensure_transaction()
        try:
            result = self.task_ownership.renew(
                task_id=task_id,
                nonce=nonce,
                ttl_seconds=kwargs.get("ttl_seconds"),
            )
            if result:
                try:
                    self.db.commit()
                except Exception:
                    self.db.rollback()
                    raise
            else:
                self.db.rollback()
            return result
        except Exception:
            self.db.rollback()
            raise
        finally:
            self._clear_runner_transaction_ownership()
    def verify_task_ownership(self, task_id: str = None, **kwargs) -> bool:
        task_id = task_id or self._current_task_id
        nonce = kwargs.get("nonce", self._task_ownership_nonce)
        return bool(
            self.task_ownership.verify(
                task_id=task_id,
                nonce=nonce,
                fencing_token=kwargs.get("fencing_token"),
                action_id=kwargs.get("action_id"),
            )
        )

    def _transaction_key(self):
        try:
            connection = self.db.connection()
        except Exception:
            return None
        dbapi_connection = getattr(connection, "connection", None)
        dbapi_connection = getattr(dbapi_connection, "dbapi_connection", dbapi_connection)
        if dbapi_connection is None:
            return None
        return id(dbapi_connection)

    def _runner_owns_db_transaction(self) -> bool:
        if not self._physical_transaction_active():
            return False
        return _RUNNER_TRANSACTION_OWNERS.get(self._transaction_key()) is self

    def _physical_transaction_active(self) -> bool:
        try:
            connection = self.db.connection()
        except Exception:
            return False
        dbapi_connection = getattr(connection, "connection", None)
        dbapi_connection = getattr(dbapi_connection, "dbapi_connection", dbapi_connection)
        return bool(getattr(dbapi_connection, "in_transaction", False))

    def _mark_runner_transaction_owned(self) -> None:
        key = self._transaction_key()
        if key is not None:
            _RUNNER_TRANSACTION_OWNERS[key] = self

    def _clear_runner_transaction_ownership(self) -> None:
        key = self._transaction_key()
        if key is None:
            return
        current = _RUNNER_TRANSACTION_OWNERS.get(key)
        if current is self:
            _RUNNER_TRANSACTION_OWNERS.pop(key, None)


    def _release_runner_transaction_ownership(self) -> None:
        # Ownership follows the physical transaction; do not clear the marker
        # for a different runner. It expires naturally when the transaction ends.
        return None

    def _refuse_caller_owned_pending_transaction(self) -> None:
        # Fail closed: a runner mutation must start from committed baseline
        # unless this exact runner owns the active transaction. Public mutation
        # depth is NOT proof of transaction ownership, so do not use it as a
        # bypass.
        import sys
        pending = False
        for collection_name in ("new", "dirty", "deleted"):
            collection = getattr(self.db, collection_name, None)
            try:
                if collection:
                    pending = True
            except TypeError:
                continue

        physical = self._physical_transaction_active()
        key = self._transaction_key()
        owner = _RUNNER_TRANSACTION_OWNERS.get(key)

        if owner is self or self._runner_owns_db_transaction():
            return
        if not (pending or physical):
            return

        if pending and physical:
            raise RuntimeError(
                "Runner refused to mutate a caller-owned transaction with pending work"
            )
        if pending:
            raise RuntimeError(
                "Runner refused to mutate a caller-owned transaction with pending work"
            )
        if physical:
            raise RuntimeError(
                "Runner refused to mutate an active caller-owned transaction"
            )

    def _ensure_transaction(self) -> bool:
        started = self.task_ownership._ensure_transaction()
        if started:
            self._mark_runner_transaction_owned()
        return started

    def _rollback_owned_transaction(self) -> None:
        # Roll back only when this runner actually started/owns the physical
        # transaction. Otherwise leave caller-owned transactions and rows alone.
        if not self._physical_transaction_active():
            self._clear_runner_transaction_ownership()
            return
        key = self._transaction_key()
        owner = _RUNNER_TRANSACTION_OWNERS.get(key)
        # No proven ownership means no rollback and no caller data loss.
        if owner is not self:
            return

        try:
            self.db.rollback()
        except Exception:
            pass
        self._clear_runner_transaction_ownership()

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
        self._refuse_caller_owned_pending_transaction()

        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        self._append_event(task_id, event_type, event_data)
        self.db.commit()

    # --- task state ownership --------------------------------------------------

    async def create_task(self, original_request: str, idempotency_key: Optional[str] = None) -> Task:
        self._refuse_caller_owned_pending_transaction()

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
        self._refuse_caller_owned_pending_transaction()

        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        task.pending_action = None
        task.updated_at = datetime.utcnow()
        self.db.commit()

    async def start_mutating_action(self, task_id: str, action_id: str, tool_name: str, tool_params: Dict[str, Any], approved: bool = False, **kwargs) -> None:
        self._refuse_caller_owned_pending_transaction()
 
        # approved=True requires an exact staged pending permission below; no
        # missing-pending fallback is allowed.
        # Preserve caller transactions...
        self._ensure_transaction()
        task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
        if not task:
            self.db.rollback()
            raise RuntimeError(f"Task {task_id} not found")
        if task.state != TaskState.RUNNING.value:
            self.db.rollback()
            raise RuntimeError("Task ownership not held for action start")

        # Atomically consume an approved, exact pending permission payload before
        # marking an active action. A changed, missing, or mismatched staged
        # payload fails closed without mutating task or ownership state. Internal
        # "approved" status is durable evidence only; it is not by itself user
        # authorization to execute.
        pending_raw = getattr(task, "pending_action", None)
        if approved and pending_raw in (None, "", b""):
            self.db.rollback()
            raise RuntimeError(
                "Approved action requires an exact durable pending permission"
            )
        if pending_raw not in (None, "", b""):
            try:
                pending = json.loads(pending_raw)
            except (TypeError, ValueError):
                self.db.rollback()
                raise RuntimeError("Pending action evidence is malformed")
            if not isinstance(pending, dict):
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            if pending.get("kind") != "permission":
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            if approved and pending.get("status") != "approved":
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            if (not approved) and pending.get("status") == "approved":
                self.db.rollback()
                raise RuntimeError("Approved pending permission requires approved=True")
            if pending.get("action_id") != action_id:
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            if pending.get("tool_name") != tool_name:
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            if pending.get("params") != tool_params:
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action start")
            # The durable proposal is immutable: detect a pending payload that
            # was mutated after it was staged.
            if approved and pending.get("status") == "approved":
                events = self.ledger.event_history(task_id)
                proposals = [
                    e for e in events
                    if e.get("event_type") == "action.pending_approval"
                    and (e.get("event_data") or {}).get("action_id") == action_id
                ]
                approvals = [
                    e for e in events
                    if e.get("event_type") == "action.approved"
                    and (e.get("event_data") or {}).get("action_id") == action_id
                ]
                if not proposals or not approvals:
                    self.db.rollback()
                    raise RuntimeError("Pending action durable proposal is missing")
                last_proposal = proposals[-1].get("event_data") or {}
                if last_proposal.get("tool_name") != pending.get("tool_name") or last_proposal.get("params") != pending.get("params"):
                    self.db.rollback()
                    raise RuntimeError("Pending action durable proposal mismatch")

        # Read every action-evidence source under the SAME writer transaction,
        # before consuming permission or changing either active-action marker.
        from backend.services.execution_components import CompletionVerifier
        evidence_error = CompletionVerifier(self.db, self.ledger.event_history).action_evidence_error(
            task_id, include_pending=False, consuming_action_id=action_id if approved else None,
            proposed_action_id=action_id, mutation=True,
        )
        if evidence_error:
            raise RuntimeError(evidence_error)

        fence = self._task_ownership_fence if self._task_ownership_fence is not None else self._task_ownership_fencing_token
        started = self.task_ownership.begin_action(
            task_id,
            self._task_ownership_nonce,
            action_id,
            fencing_token=fence,
        )
        if not started:
            self.db.rollback()
            raise RuntimeError("Task ownership not held for action start")
        if pending_raw not in (None, "", b""):
            task.pending_action = None
        task.active_action_id = action_id
        task.updated_at = datetime.utcnow()
        self._append_event(task_id, "action.started", {
            "action_id": action_id,
            "tool_name": tool_name,
            "approved": approved,
            "owner_nonce": self._task_ownership_nonce,
            "fencing_token": fence,
        })
        self.db.commit()

    async def finish_mutating_action(self, task_id: str, action_id: str, event_data: Dict[str, Any] = None) -> None:
        self._refuse_caller_owned_pending_transaction()

        # Preserve caller transactions; begin a physical transaction only if
        # one is not already active.
        self._ensure_transaction()
        try:
            task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            if not task:
                self.db.rollback()
                raise RuntimeError(f"Task {task_id} not found")
            if task.state != TaskState.RUNNING.value:
                self.db.rollback()
                raise RuntimeError(f"Task {task_id} is not running")
            owner = self.task_ownership.get(task_id)
            if owner is not None and getattr(owner, "status", None) != "ACTIVE":
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action completion")
            fence = self._task_ownership_fence if self._task_ownership_fence is not None else self._task_ownership_fencing_token
            completed = self.task_ownership.complete_action(
                task_id,
                self._task_ownership_nonce,
                action_id,
                fencing_token=fence,
            )
            if not completed:
                self.db.rollback()
                raise RuntimeError("Task ownership not held for action completion")
            # Only clear the durable marker when the fence succeeds.
            if task.active_action_id == action_id:
                task.active_action_id = None
            task.updated_at = datetime.utcnow()
            # Exact action-linked evidence: tool identity, result, and verification.
            result_data = dict(event_data or {})
            if result_data.get("action_id") in (None, ""):
                result_data["action_id"] = action_id
            self._append_event(task_id, "tool.executed", result_data)
            self.db.commit()
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
            raise

    async def begin_task_action(self, task_id: str = None, action_id: str = None, **kwargs) -> bool:
        """Begin/commit a task-scoped action ownership fence.

        Returns True only when the ownership update is durable. Failures and
        commit rollbacks return False and do not discard unrelated caller work.
        Actions already preserved as uncertain are not replayed.
        """
        try:
            self._refuse_caller_owned_pending_transaction()
        except RuntimeError:
            return False
        task_id = task_id or self._current_task_id
        if not task_id or not action_id or not self._task_ownership_nonce:
            return False
        started_transaction = False
        try:
            started_transaction = self._ensure_transaction()
            fence = self._task_ownership_fence if self._task_ownership_fence is not None else self._task_ownership_fencing_token
            if fence is None or kwargs.get("fencing_token", fence) != fence:
                return False
            if not self.task_ownership.verify(task_id, self._task_ownership_nonce, fencing_token=fence):
                return False
            task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            owner = self.task_ownership.get(task_id)
            if task is None or task.state != TaskState.RUNNING.value or owner.uncertain_action_ids:
                return False
            in_progress = str(action_id) if task.active_action_id == str(action_id) else None
            if in_progress and owner.active_action_id is None:
                # Compatibility for record_action_started -> begin_action only
                # when that start was durably bound to this exact owner/fence.
                # An orphan/legacy task marker alone cannot establish handoff.
                starts = [e.get("event_data") for e in self.ledger.event_history(task_id)
                          if e.get("event_type") == "action.started"
                          and isinstance(e.get("event_data"), dict)
                          and e["event_data"].get("action_id") == in_progress]
                if (len(starts) != 1 or starts[0].get("owner_nonce") != self._task_ownership_nonce
                        or starts[0].get("fencing_token") != fence):
                    return False
            from backend.services.execution_components import CompletionVerifier
            error = CompletionVerifier(self.db, self.ledger.event_history).action_evidence_error(
                task_id, in_progress_action_id=in_progress, mutation=True,
            )
            if error:
                return False
            if in_progress and owner.active_action_id == in_progress:
                # Pure validation of an exact durable handoff. No event or
                # commit and no new execution permission; start rejects reuse.
                return True
            if not self.task_ownership.begin_action(
                str(task_id), self._task_ownership_nonce, str(action_id), fencing_token=fence,
            ):
                return False
            self.db.commit()
            return True
        except Exception:
            return False
        finally:
            if started_transaction:
                self._rollback_owned_transaction()

    def set_task_objective(self, task_id: str, objective_id: str) -> Task:
        self._refuse_caller_owned_pending_transaction()

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

    async def reply_to_waiting(self, task_id: str, answer: Any, expected_version: Any) -> Task:
        """Resume an explicitly typed clarification wait as the same task."""
        self._refuse_caller_owned_pending_transaction()

        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")

        started_transaction = self._ensure_transaction()
        try:
            from sqlalchemy import update as _sa_update

            task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            if not task:
                raise RuntimeError("Task %s not found" % task_id)
            if task.version != expected_version:
                raise RuntimeError("question changed")
            if task.state != TaskState.WAITING_FOR_USER.value:
                raise RuntimeError("Task %s is not waiting for user" % task_id)

            try:
                metadata = json.loads(task.pending_action or "{}")
            except (TypeError, ValueError):
                metadata = None
            if not (isinstance(metadata, dict) and metadata.get("kind") == "clarification"):
                raise RuntimeError(
                    "This wait is not an explicitly typed clarification and cannot be resumed automatically"
                )

            base_goal = task.normalized_goal if task.normalized_goal is not None else task.original_request
            merged_goal = "%s\n[clarification] %s" % (base_goal, answer)
            if len(merged_goal) > 24000:
                raise RuntimeError("Combined instruction length exceeds 24000 characters")

            old_pending_action = task.pending_action
            now = datetime.utcnow()

            result = self.db.execute(
                _sa_update(Task).where(
                    Task.id == task_id,
                    Task.state == TaskState.WAITING_FOR_USER.value,
                    Task.version == expected_version,
                    Task.pending_action == old_pending_action,
                ).values(
                    state=TaskState.RUNNING.value,
                    version=expected_version + 1,
                    pending_question=None,
                    pending_action=old_pending_action,
                    normalized_goal=merged_goal,
                    active_action_id=None,
                    updated_at=now,
                )
            )
            if result.rowcount != 1:
                fresh = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
                if fresh is None:
                    raise RuntimeError("Task %s not found" % task_id)
                if fresh.version != expected_version:
                    raise RuntimeError("question changed")
                try:
                    fresh_metadata = json.loads(fresh.pending_action or "{}")
                except (TypeError, ValueError):
                    fresh_metadata = None
                if not (
                    isinstance(fresh_metadata, dict)
                    and fresh_metadata.get("kind") == "clarification"
                ):
                    raise RuntimeError(
                        "This wait is not an explicitly typed clarification and cannot be resumed automatically"
                    )
                raise RuntimeError("question changed")

            self._append_event(task_id, "user.replied", {"answer": answer})
            self.db.commit()

            refreshed = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            return refreshed
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                pass
            raise
        finally:
            self._rollback_owned_transaction()

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
        self._refuse_caller_owned_pending_transaction()

        # Hold the writer lock before examining or changing task state so
        # concurrent handoff/completion attempts are serialized instead of both
        # committing. Does not roll back unrelated caller work.
        self._ensure_transaction()

        if new_state == TaskState.COMPLETED:
            # Completion is evidence-gated: no approval/status/event alone
            # authorizes success. Re-check after acquiring the lock.
            from backend.services.execution_components import CompletionVerifier
            completion_error = CompletionVerifier(
                self.db, self.ledger.event_history
            ).precondition_error(task_id)
            if completion_error:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                raise RuntimeError(completion_error)

        if new_state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            try:
                from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as _FollowupLedger
                _FollowupLedger(self.db)._ensure_transaction()
            except Exception:
                pass
        owner = self.task_ownership.get(task_id)
        has_task_owner = owner is not None and getattr(owner, "status", None) == "ACTIVE"

        if has_task_owner:
            if not self._task_ownership_nonce or owner.owner_nonce != self._task_ownership_nonce:
                try:
                    self.db.rollback()
                except Exception:
                    pass
                raise RuntimeError("Runner is not the current owner of this task")
        elif not self._verify_lease():
            try:
                self.db.rollback()
            except Exception:
                pass
            raise RuntimeError("Runner lease not held")

        stale_task = next(
            (
                obj for obj in getattr(self.db, "identity_map", {}).values()
                if isinstance(obj, Task) and getattr(obj, "id", None) == task_id
            ),
            None,
        )
        task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")

        old_state = task.state

        # Under the writer lock, a durable cancellation (or other committed
        # change) that has replaced the expected running state must not be
        # silently overwritten by a stale progress write.
        if (
            old_state == TaskState.CANCELLED.value
            and new_state.value == TaskState.RUNNING.value
            and event_type == "tool.executed"
        ):
            raise RuntimeError("Task changed during state transition")

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
        if new_state in (TaskState.COMPLETED, TaskState.FAILED, TaskState.CANCELLED):
            terminal_reason = updates.get("terminal_reason") if isinstance(updates, dict) else None
            if terminal_reason is None:
                terminal_reason = new_state.value
            try:
                from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as _FollowupLedger
                _FollowupLedger(self.db).on_task_terminal(task_id, reason=terminal_reason)
            except BaseException:
                self.db.rollback()
                raise
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
        self._refuse_caller_owned_pending_transaction()

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
                "owner_nonce": self._task_ownership_nonce,
                "fencing_token": self._task_ownership_fence,
            },
            active_action_id=action_id,
        )

    async def set_pending_action_status(
        self,
        task_id: str,
        status: str,
        binding: Optional[Any] = None,
        **kwargs: Any,
    ) -> None:
        """Set the status field inside task.pending_action JSON.

        This is a narrow durable-state mutation only. It must not create an
        approval: an ``approved`` transition is fail-closed. Matching identifiers
        or binding tokens in staged JSON are execution linkage, not independently
        authenticated human consent, and self-supplied claims of authentication
        do not constitute proof. Approval is rejected unless an actual,
        independently verified receipt/authentication integration exists.
        """
        self._refuse_caller_owned_pending_transaction()

        # The ``binding`` argument and any authentication-claiming keyword are
        # intentionally ignored: no self-supplied identifier or claim is trusted
        # as human consent.  Keeping the signature preserves callers, but it
        # cannot influence the fail-closed result.
        _ = binding, kwargs

        if not self._verify_lease():
            raise RuntimeError("Runner lease not held")

        started_transaction = self._ensure_transaction()
        try:
            task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            if not task:
                raise RuntimeError(f"Task {task_id} not found")
            if not task.pending_action:
                return

            try:
                data = json.loads(task.pending_action)
            except (json.JSONDecodeError, TypeError, ValueError):
                raise RuntimeError("Pending action evidence is malformed")
            if not isinstance(data, dict):
                raise RuntimeError("Pending action evidence is malformed")

            # Fail closed on manufacturing approval. A bare status flip has no
            # authorization effect. Matching identifiers (action_id, binding
            # tokens, and similar staged fields) are execution linkage only:
            # they are self-supplied/unauthenticated evidence and do NOT
            # constitute human consent. Self-supplied claims of authentication
            # (binding, authenticated_binding, etc.) are likewise unverified.
            # Until an independently verified receipt/authentication integration
            # exists, every approval transition is rejected, regardless of
            # matching identifiers.
            if status == "approved":
                raise RuntimeError(
                    "Pending approval requires independently authenticated binding "
                    "proof; approval transition fail-closed without authenticated "
                    "human consent"
                )

            data["status"] = status
            task.pending_action = json.dumps(data)
            task.updated_at = datetime.utcnow()
            self.db.commit()
        except Exception:
            if started_transaction:
                self._rollback_owned_transaction()
            raise
        finally:
            if started_transaction and not self._physical_transaction_active():
                self._clear_runner_transaction_ownership()
    async def get_task(self, task_id: str) -> Optional[Task]:
        """Get a task by ID."""
        return self.db.query(Task).filter_by(id=task_id).populate_existing().first()

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

    async def force_cancel(self, task_id: str, via: str = "global_stop") -> Task:
        self._refuse_caller_owned_pending_transaction()

        """Cancel a task without owning the runner lease.

        Used by the global-stop path (`POST /api/v1/stop`). The actively
        running executor holds and renews the lease itself, so requiring it
        here would make a stop a no-op exactly when it is most needed. The
        cancelled state is terminal and observed by the executor before its
        next OS action, so forcing it is safe.
        """
        try:
            from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as _FollowupLedger
            self._ensure_transaction()
            _FollowupLedger(self.db)._ensure_transaction()
        except Exception:
            pass
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")

        old_state = task.state
        if old_state in (
            TaskState.COMPLETED.value,
            TaskState.FAILED.value,
            TaskState.CANCELLED.value,
        ):
            raise RuntimeError(f"Invalid task state transition: {old_state} -> cancelled")

        expected_version = task.version
        now = datetime.utcnow()

        from sqlalchemy import update as _sa_update
        result = self.db.execute(
            _sa_update(Task).where(
                Task.id == task_id,
                Task.state == old_state,
                Task.version == expected_version,
            ).values(
                state=TaskState.CANCELLED.value,
                version=expected_version + 1,
                updated_at=now,
                terminal_reason="cancelled_by_user",
            )
        )
        if getattr(result, "rowcount", 1) != 1:
            fresh = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
            if fresh is None:
                raise RuntimeError(f"Task {task_id} not found")
            if fresh.state in (
                TaskState.COMPLETED.value,
                TaskState.FAILED.value,
                TaskState.CANCELLED.value,
            ):
                raise RuntimeError(f"Invalid task state transition: {fresh.state} -> cancelled")
            raise RuntimeError("Task changed while stopping; try again.")

        task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
        if not task:
            raise RuntimeError(f"Task {task_id} not found")

        self._append_event(task_id, "task.cancelled", {
            "reason": "user_requested",
            "via": via,
        })
        try:
            from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as _FollowupLedger
            _FollowupLedger(self.db).on_task_terminal(task_id, reason="cancelled")
        except BaseException:
            self.db.rollback()
            raise
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
                    "via": via,
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


_RUNNER_MUTATION_METHODS = (
    "acquire_task_ownership",
    "release_task_ownership",
    "renew_task_ownership",
    "acquire_lease",
    "renew_lease",
    "release_lease",
    "create_task",
    "clear_pending_action",
    "start_mutating_action",
    "finish_mutating_action",
    "set_task_objective",
    "transition_state",
    "record_event",
    "cancel_task",
    "force_cancel",
    "record_action_started",
    "set_pending_action_status",
    "begin_task_action",
    "reply_to_waiting",
)


def _runner_mutation_guarded(_method):
    _is_coro = inspect.iscoroutinefunction(_method)

    if _is_coro:
        async def _wrapped(self, *args, **kwargs):
            # Fail-closed refusal runs only for an outermost runner call. It is
            # based on actual session transaction ownership, never on depth.
            if getattr(self, "_runner_context_depth", 0) == 0:
                self._refuse_caller_owned_pending_transaction()
            getattr(self, "_runner_mutation_depth", 0)
            self._runner_context_depth = getattr(self, "_runner_context_depth", 0) + 1
            self._runner_mutation_depth = getattr(self, "_runner_mutation_depth", 0) + 1
            try:
                return await _method(self, *args, **kwargs)
            except Exception:
                self._rollback_owned_transaction()
                raise
            finally:
                if getattr(self, "_runner_context_depth", 0) > 0:
                    self._runner_context_depth -= 1
                if getattr(self, "_runner_mutation_depth", 0) > 0:
                    self._runner_mutation_depth -= 1
                # Transaction-bound ownership cannot survive beyond a physical
                # transaction. Clear it whenever the runner is not in an active
                # transaction.
                if not self._physical_transaction_active():
                    self._clear_runner_transaction_ownership()
        return _wrapped

    def _wrapped(self, *args, **kwargs):
        if getattr(self, "_runner_context_depth", 0) == 0:
            self._refuse_caller_owned_pending_transaction()
        self._runner_context_depth = getattr(self, "_runner_context_depth", 0) + 1
        self._runner_mutation_depth = getattr(self, "_runner_mutation_depth", 0) + 1
        try:
            return _method(self, *args, **kwargs)
        except Exception:
            self._rollback_owned_transaction()
            raise
        finally:
            if getattr(self, "_runner_context_depth", 0) > 0:
                self._runner_context_depth -= 1
            if getattr(self, "_runner_mutation_depth", 0) > 0:
                self._runner_mutation_depth -= 1
            if not self._physical_transaction_active():
                self._clear_runner_transaction_ownership()
    return _wrapped


for _method_name in _RUNNER_MUTATION_METHODS:
    _original_method = getattr(TaskRunner, _method_name, None)
    if inspect.isfunction(_original_method):
        setattr(TaskRunner, _method_name, _runner_mutation_guarded(_original_method))
