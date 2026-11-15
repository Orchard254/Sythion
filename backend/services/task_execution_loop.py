"""
Core Task Execution Loop for Sythion (Synth).
Integrates model decisions, tool execution, and state management.
"""

import asyncio
import json
import logging
import os
import uuid
from typing import Dict, Any, Optional, List
from datetime import datetime
from sqlalchemy.orm import Session

from backend.services.task_runner import TaskRunner
from backend.services.task_instruction_revision import TaskInstructionRevision
from backend.runtime.action_guard import OSActionGuard
from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError, DecisionType
from backend.runtime.model_client import DeferredActiveModelClient
from backend.services.reasoning_journal import ReasoningJournal
from backend.services.execution_components import (
    BudgetGuard,
    CancellationController,
    CompletionVerifier,
    ExecutionGuard,
    GuardDecision,
    PostTaskPipeline,
)
from backend.services.skill_engine import execute_skill
from backend.tools.executor import PolicyContext, ToolExecutor, ToolOutput
from backend.models.database import Task, TaskEvent
from backend.api.schemas import TaskState
from backend.core.budgets import TaskBudget, BudgetExceededError
from backend.core.config import settings

logger = logging.getLogger(__name__)

# Cancellation-safe guard for actual external mutating tool actions.
_OS_ACTION_GUARD = OSActionGuard()


class UnresolvedActionEvidenceError(RuntimeError):
    """Staging a new permission is blocked by unresolved prior external-action
    evidence. This is an honest terminal condition, not a retryable action
    failure or a replay grant."""


class TaskExecutionLoop:
    """
    Main execution loop for processing tasks — now an ORCHESTRATOR.

    Phase 1 kernel extraction: the loop delegates narrow responsibilities to
    explicit components (see services/execution_components.py):

        TaskExecutionLoop
            ├── BudgetGuard           (budget enforcement points)
            ├── CancellationController (task-scoped cancellation contexts)
            ├── ExecutionGuard        (repeat-failure / spin guards)
            ├── ActionExecutor role   (_execute_decision dispatch, in-loop)
            ├── CompletionVerifier    (evidence-gated completion)
            └── PostTaskPipeline      (post-terminal best-effort hooks)

    State transitions and events remain owned by TaskRunner; the model is
    never the source of truth for any runtime concern.
    """

    MAX_REPAIR_ATTEMPTS = 2

    # Provider-side failures (rate limit, cloud outage, retired/unavailable
    # model) are not repairable: no different model decision can fix them, so
    # retrying only amplifies the outage and delays honest failure.
    _NON_REPAIRABLE_CODES = frozenset({
        "CLOUD_ERROR",
        "RATE_LIMITED",
        "MODEL_UNAVAILABLE",
        "REASONING_JOURNAL_ERROR",
    })

    def __init__(
        self,
        db_session: Session,
        task_runner: TaskRunner,
        tool_executor: ToolExecutor
    ):
        self.db = db_session
        self.runner = task_runner
        self.tool_executor = tool_executor
        # Do not read model credentials/active profile just to create or
        # inspect a task executor. The first actual decision resolves a
        # single provider and retains that identity for this loop instance.
        self.model_engine = ModelDecisionEngine(
            db_session, tool_executor, model_client=DeferredActiveModelClient(),
        )
        # Ownership-checked receipts: provider-call lifecycle is durable task
        # evidence, not a tool authorization or verified completion result.
        self.model_engine.reasoning_journal = ReasoningJournal(
            task_runner.record_owned_reasoning_event
        )
        self.instruction_revision = TaskInstructionRevision(task_runner)

        # Extracted components (one responsibility each).
        self.guard = ExecutionGuard()
        self.cancellation = CancellationController(task_runner)
        self.verifier = CompletionVerifier(db_session, self._get_event_history)
        self.post_task_pipeline = PostTaskPipeline(db_session, task_runner, tool_executor)

    @staticmethod
    def _build_repair_context(code: str, message: str) -> str:
        """Build actionable repair guidance for a model decision error.

        Pure function: no I/O, no state. The returned strings are the
        exact guidance previously constructed inline in ``execute_task``.
        """
        if code == "MISSING_CONTROL_CALL":
            return (
                "CRITICAL ERROR: You outputted raw text instead of a tool call. "
                "You MUST call the 'complete_task' tool with your answer inside 'result' "
                "(e.g. complete_task(result=\"...\")). Do NOT output raw text."
            )
        elif code == "INVALID_DECISION_TYPE":
            return (
                "Your previous tool name is not callable in the current autonomy "
                "mode (it is registered but gated/hidden). Do NOT retry gui_* or any other tool not "
                "listed in AVAILABLE TOOLS (app_launch only when listed). Consult "
                "system_capabilities for the model-visible subset, choose one of the "
                "listed read-only tools, or use tool_propose when a capability is "
                "genuinely missing. Do not assume an interactive approval or consent "
                "boundary exists for gated tools."
            )
        else:
            return (
                f"Your previous decision was rejected: {message} "
                f"(code {code}). Respond with a valid tool call."
            )

    async def execute_task(self, task_id: str) -> None:
        """
        Execute a single task from start to completion.

        Sections (in execution order):
          1. Own the task and start the lease heartbeat.
          2. Establish the task is runnable (terminal tasks are refused).
          3. Per iteration: enforce budgets, drain reviewed follow-ups at a
             safe point, handle an approved/denied pending action, request a
             model decision (with bounded repair guidance on retriable
             errors), fence the decision against instruction revisions, apply
             repeat/spin guards, dispatch the decision and any pending calls
             from the same response, and stop on a terminal state.
          4. On exit: cancel the heartbeat and release task ownership.

        Args:
            task_id: ID of the task to execute

        Raises:
            Exception: If task execution fails critically
        """
        logger.info(f"Starting execution of task: {task_id}")

        self._ownership_lost = False
        repair_attempts = 0
        refused_completions = 0  # consecutive complete_task calls refused by precondition guard
        budget = BudgetGuard(TaskBudget())

        worker_id = f"pid-{os.getpid()}"
        ownership_acquired = False
        if self.runner is not None:
            ownership_result = await self.runner.acquire_task_ownership(
                task_id=task_id, worker_id=worker_id
            )
            if not getattr(ownership_result, "acquired", False):
                raise RuntimeError(
                    f"Task ownership not acquired: {getattr(ownership_result, 'reason', '')}"
                )
            ownership_acquired = True

        # Start a lease/ownership heartbeat so long LLM calls don't let the
        # task ownership expire mid-task.
        heartbeat = asyncio.create_task(self._heartbeat_lease(task_id))

        terminal_retry = False
        primary_error = None
        recording_failure = False
        repair_context = None
        try:
            # Transition to running state (no-op if the route already did)
            task = await self.runner.get_task(task_id)
            if task is None:
                raise RuntimeError(f"Task {task_id} not found")
            # A queued/retried worker may arrive after STOP or another worker
            # has settled the task. Terminal state is not a restart request;
            # preserve its action evidence and do not infer or replay effects.
            if task.state in (
                TaskState.CANCELLED.value,
                TaskState.COMPLETED.value,
                TaskState.FAILED.value,
            ):
                terminal_retry = True
                if task.state == TaskState.CANCELLED.value:
                    return
                raise RuntimeError(f"Cannot execute terminal task {task_id}: {task.state}")
            if task.state != TaskState.RUNNING.value:
                task = await self.runner.transition_state(
                    task_id=task_id,
                    new_state=TaskState.RUNNING,
                    event_type="task.execution_started",
                    event_data={"started_at": datetime.utcnow().isoformat()}
                )

            # Durable scheduling evidence is recorded only after ownership is
            # acquired and the task is established as runnable/running.  This
            # proves a historical worker start for this task/attempt; it does
            # not prove current execution, completion, clearance, or ongoing
            # ownership.
            if ownership_acquired and self.runner is not None:
                try:
                    task_row = self.db.query(Task).filter_by(id=task_id).first()
                    self.runner.ledger.append_event(
                        task_id,
                        "task.worker_started",
                        {
                            "task_id": task_id,
                            "task_version": getattr(task_row, "version", None),
                            "task_idempotency_key": getattr(task_row, "idempotency_key", None),
                            "worker_id": worker_id,
                            "process_id": os.getpid(),
                            "historical": True,
                            "current_execution_claimed": False,
                            "cleared": False,
                            "terminal": False,
                        },
                    )
                    self.db.commit()
                except Exception:
                    try:
                        self.db.rollback()
                    except Exception:
                        pass

            while True:

                if getattr(self, "_ownership_lost", False):
                    recording_failure = True
                    await self._fail_task(
                        task_id,
                        code="OWNERSHIP_LOST",
                        message="Task ownership lost; further decisions and actions stopped",
                    )
                    recording_failure = False
                    await self._mark_reviewed_followups_not_applied(task_id, "ownership_lost")
                    break

                # Enforce budgets before each iteration: a task must fail
                # honestly (never retry, mutate, or fall back locally) once any
                # per-task budget is exhausted.
                try:
                    budget.ensure_time_left()
                except BudgetExceededError as e:
                    await self._fail_task(task_id, code=e.code, message=e.message)
                    break

                # Check action limit (single authority: TaskBudget.max_actions
                # via the guard)
                if not budget.can_execute_action():
                    await self._fail_task(
                        task_id,
                        code="ACTION_LIMIT_EXCEEDED",
                        message=f"Exceeded maximum {budget.max_actions} actions"
                    )
                    break

                # Get current task state
                task = await self.runner.get_task(task_id)
                if not task:
                    raise RuntimeError(f"Task {task_id} not found")

                # Check for cancellation
                if task.state == TaskState.CANCELLED.value:
                    logger.info(f"Task {task_id} was cancelled")
                    await self._mark_reviewed_followups_not_applied(task_id, "cancelled")
                    break

                # All admitted work from the prior response has now settled.
                # Open first: an absent/executing window cannot accept revisions.
                snapshot = self.instruction_revision.open(task_id)
                if snapshot is None:
                    await self._mark_reviewed_followups_not_applied(task_id, "stopped_before_safe_point")
                    break
                if not await self._apply_reviewed_followups(task_id):
                    # Fail closed without losing or superseding the instruction.
                    # Pending permission/uncertain-action evidence stays intact;
                    # even an approved proposal cannot run past a failed drain.
                    self.runner._ensure_transaction()
                    task = self.db.query(Task).filter_by(id=task_id).populate_existing().one()
                    if task.state == TaskState.RUNNING.value:
                        await self.runner.transition_state(
                            task_id=task_id, new_state=TaskState.PAUSED,
                            event_type="task.followup_blocked",
                            event_data={"reason": "Queued instruction could not be safely applied; retained for review."},
                        )
                    else:
                        self.db.rollback()
                        await self._mark_reviewed_followups_not_applied(task_id, task.state)
                    break

                # Check for pending action (approval/denial)
                if task.pending_action:
                    try:
                        pending = json.loads(task.pending_action)
                    except (json.JSONDecodeError, TypeError):
                        pending = {}
                    pending_status = pending.get("status")
                    pending_tool_name = pending.get("tool_name")
                    pending_params = pending.get("params", {})
                    if pending_status == "approved" and pending_tool_name:
                        # The approved handoff is another admission boundary:
                        # enqueue may have raced the preceding drain/read.
                        if not self.instruction_revision.claim(task_id, snapshot.revision):
                            continue
                        approval_binding = None
                        if isinstance(pending, dict):
                            approval_binding = pending.get("action_id") or pending.get(
                                "binding_token"
                            )
                        logger.info(f"Executing approved pending action: {pending_tool_name}")
                        budget.record_action()
                        result = await self._execute_tool(
                            task_id,
                            {"tool_name": pending_tool_name, "params": pending_params},
                            approved_by_user=True,
                            action_id=str(approval_binding) if approval_binding else None,
                        )
                        # Internal approved evidence is not independently
                        # authenticated consent. Stop after the truthful diagnostic
                        # rather than looping over the pending event.
                        break
                    elif pending_status == "denied":
                        logger.info(f"Skipping denied pending action: {pending_tool_name}")
                        await self.runner.clear_pending_action(task_id)
                        await self.runner.transition_state(
                            task_id=task_id,
                            new_state=TaskState.RUNNING,
                            event_type="action.denied",
                            event_data={"tool_name": pending_tool_name, "params": pending_params},
                        )
                        continue

                # Refresh after the drain so inference sees every committed edit.
                snapshot = self.instruction_revision.open(task_id)
                if snapshot is None:
                    await self._mark_reviewed_followups_not_applied(task_id, "stopped_after_safe_point")
                    break
                events = self._get_event_history(task_id)

                # Request decision from model
                try:
                    decision_type, params = await self.model_engine.make_decision(
                        task_id=task.id,
                        original_request=snapshot.original_request,
                        normalized_goal=snapshot.normalized_goal,
                        event_history=events,
                        current_state=task.state,
                        pending_question=task.pending_question,
                        repair_context=repair_context,
                        budget=budget.budget,
                    )
                    repair_attempts = 0  # Reset on successful decision
                    repair_context = None

                except BudgetExceededError as e:
                    if not self.instruction_revision.claim(task_id, snapshot.revision):
                        self.model_engine.pop_pending_tool_calls()
                        continue
                    # Budget exhaustion is terminal: fail honestly, never repair.
                    logger.warning(f"Task {task_id} exceeded budget {e.code}: {e.message}")
                    await self._fail_task(task_id, code=e.code, message=e.message)
                    break
                except ModelDecisionError as e:
                    if not self.instruction_revision.claim(task_id, snapshot.revision):
                        self.model_engine.pop_pending_tool_calls()
                        continue
                    if e.code in self._NON_REPAIRABLE_CODES:
                        # Provider-side (rate limit, cloud outage, retired
                        # model): fail honestly on the first attempt instead of
                        # burning repair attempts against a dead provider.
                        logger.warning(
                            f"Non-repairable model failure {e.code}: {e.message}"
                        )
                        await self._fail_task(task_id, code=e.code, message=e.message)
                        break
                    repair_attempts += 1
                    budget.record_repair()
                    logger.warning(f"Model decision error (attempt {repair_attempts}): {e.message}")

                    if repair_attempts > self.MAX_REPAIR_ATTEMPTS:
                        await self._fail_task(
                            task_id,
                            code=e.code,
                            message=f"Model failed after {repair_attempts} repair attempts: {e.message}"
                        )
                        break

                    # Try again, telling the model what went wrong with actionable instructions
                    repair_context = self._build_repair_context(e.code, e.message)
                    continue


                # Linearize response admission against instruction append. This
                # fences every decision type and the entire sequential batch,
                # not just mutating tools or a check before an awaited action.
                if not self.instruction_revision.claim(task_id, snapshot.revision):
                    self.model_engine.pop_pending_tool_calls()
                    continue

                # Re-check cancellation before executing: a cancel request may
                # have arrived during the (slow) model call. A cancelled task
                # must never trigger an OS action.
                task = await self.runner.get_task(task_id)
                if task.state == TaskState.CANCELLED.value:
                    logger.info(f"Task {task_id} was cancelled during decision; not executing")
                    await self._mark_reviewed_followups_not_applied(task_id, "cancelled")
                    break

                # Repeat-failure / spin guards (ExecutionGuard): identical
                # failing proposals are blocked then fail the task; identical
                # successful-but-noop proposals are likewise refused.
                fingerprint = self.guard.note_proposal(
                    decision_type == DecisionType.EXECUTE_TOOL, params
                )

                failure_decision = self.guard.evaluate_failure_guard(fingerprint)
                if failure_decision == GuardDecision.FAIL_REPEAT:
                    attempts = self.guard.attempts_for(fingerprint)
                    await self._fail_task(
                        task_id,
                        code="REPEATED_ACTION_FAILURE",
                        message=(
                            f"Action {ExecutionGuard.label(params)} was proposed "
                            f"{attempts} times without progress; aborting instead of "
                            "repeating the same failing approach"
                        ),
                    )
                    break
                if failure_decision == GuardDecision.BLOCK_REPEAT:
                    await self.runner.transition_state(
                        task_id=task_id,
                        new_state=TaskState.RUNNING,
                        event_type="action.repeat_blocked",
                        event_data=self.guard.blocked_repeat_event(params, fingerprint),
                    )
                    logger.warning(
                        f"Blocked repeated failing action for task {task_id}: "
                        f"{ExecutionGuard.label(params)}"
                    )
                    continue

                spin_decision = self.guard.evaluate_spin_guard()
                if spin_decision == GuardDecision.FAIL_SPIN:
                    await self._fail_task(
                        task_id,
                        code="SPINNING_ACTION",
                        message=(
                            f"Action {ExecutionGuard.label(params)} was proposed "
                            f"{self.guard._run_len} consecutive times successfully without "
                            "changing the result; aborting instead of spinning"
                        ),
                    )
                    break
                if spin_decision == GuardDecision.BLOCK_SPIN:
                    await self.runner.transition_state(
                        task_id=task_id,
                        new_state=TaskState.RUNNING,
                        event_type="action.spin_blocked",
                        event_data=self.guard.blocked_spin_event(params),
                    )
                    logger.warning(
                        f"Blocked repeated spinning action for task {task_id}: "
                        f"{ExecutionGuard.label(params)}"
                    )
                    continue

                if decision_type != DecisionType.EXECUTE_TOOL:
                    self.guard.reset_run()

                # Execute the decision
                budget.record_action()
                success = await self._execute_decision(
                    task_id=task_id,
                    decision_type=decision_type,
                    params=params
                )

                # Execute any pending tool calls from the same model response.
                # A task that is waiting for user input must not be driven to a
                # terminal completion by trailing calls from the same response.
                if success:
                    task = await self.runner.get_task(task_id)
                    if task is not None and task.state == TaskState.WAITING_FOR_USER.value:
                        self.model_engine.pop_pending_tool_calls()
                        pending = []
                    else:
                        pending = self.model_engine.pop_pending_tool_calls()
                    for pending_call in pending:
                        pending_type = pending_call["decision_type"]
                        pending_args = pending_call["args"]
                        budget.record_action()
                        pending_success = await self._execute_decision(
                            task_id=task_id,
                            decision_type=pending_type,
                            params=pending_args
                        )
                        if not pending_success:
                            logger.warning(f"Pending tool call {pending_type} failed; stopping sequential execution")
                            success = False
                            break

                self.guard.record_outcome(fingerprint, success)

                if not success:
                    if decision_type == DecisionType.COMPLETE_TASK:
                        refused_completions += 1
                        if refused_completions >= 3:
                            await self._fail_task(
                                task_id,
                                code="COMPLETION_BLOCKED",
                                message=(
                                    "complete_task was refused 3 consecutive times because the "
                                    "last mutating action failed with no resolution. "
                                    "Use fail_task to report an honest failure instead."
                                ),
                            )
                            break
                    else:
                        refused_completions = 0
                    # Action failed - let model decide next step
                    logger.warning(f"Action failed for task {task_id}, continuing loop")
                else:
                    refused_completions = 0


                # Check if task reached terminal state
                task = await self.runner.get_task(task_id)
                if task.state in [
                    TaskState.COMPLETED.value,
                    TaskState.FAILED.value,
                    TaskState.CANCELLED.value,
                    TaskState.WAITING_FOR_USER.value,
                    TaskState.PAUSED.value,
                ]:
                    logger.info(f"Task {task_id} reached state: {task.state}")
                    # The task is terminal: stop renewing the lease now so the
                    # next request can start without waiting on the best-effort
                    # post-completion steps below (proposal generation, memory
                    # consolidation can take tens of seconds).
                    if not heartbeat.done():
                        heartbeat.cancel()
                        try:
                            await heartbeat
                        except (asyncio.CancelledError, Exception):
                            pass
                    await self.post_task_pipeline.record_budget_snapshot(task_id, budget)
                    # Phase 2: plan/goal state updates deterministically from
                    # terminal task evidence (never from model claims).
                    await self.post_task_pipeline.apply_plan_outcome(task_id)
                    if task.state == TaskState.COMPLETED.value:
                        await self.post_task_pipeline.propose_skill_from_trace(task_id)
                        await self.post_task_pipeline.save_conversation_turn(task_id)
                        await self.post_task_pipeline.consolidate_memory_from_trace(task_id)
                    elif task.state == TaskState.FAILED.value:
                        await self.post_task_pipeline.save_conversation_turn(task_id)
                    await self._mark_reviewed_followups_not_applied(task_id, task.state)
                    break

        except BaseException as e:
            primary_error = e
            if terminal_retry or not isinstance(e, Exception):
                # Preserve the existing refusal contract for completed/failed
                # retries without trying to fail an already-settled task again.
                raise
            logger.error(f"Critical error in task execution: {e}", exc_info=True)
            if not recording_failure:
                try:
                    await self._fail_task(
                        task_id,
                        code="EXECUTION_ERROR",
                        message=str(e)
                    )
                except Exception as failure_error:
                    e.add_note(f"Task failure recording also failed: {failure_error!r}")
                    logger.error("Task failure recording failed for task %s", task_id, exc_info=True)
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except (asyncio.CancelledError, Exception):
                pass
            if ownership_acquired and self.runner is not None:
                try:
                    released = await self.runner.release_task_ownership(
                        task_id=task_id, outcome="execute_task_finished"
                    )
                    if released is not True:
                        raise RuntimeError(f"Task ownership release refused for task {task_id}")
                except Exception as release_error:
                    logger.error("Task ownership release failed for task %s", task_id, exc_info=True)
                    if primary_error is None:
                        raise
                    primary_error.add_note(f"Task ownership release also failed: {release_error!r}")
    async def _propose_skill_from_trace(self, task_id: str) -> None:
        """Delegate post-task skill proposal growth to the PostTaskPipeline."""
        await self.post_task_pipeline.propose_skill_from_trace(task_id)

    async def _consolidate_memory_from_trace(self, task_id: str) -> None:
        """Delegate post-task memory consolidation to the PostTaskPipeline."""
        await self.post_task_pipeline.consolidate_memory_from_trace(task_id)

    async def _record_budget_snapshot(self, task_id: str, budget) -> None:
        """Record an evidence-safe summary of per-task budget usage."""
        await self.post_task_pipeline.record_budget_snapshot(task_id, budget)

    async def _heartbeat_lease(self, task_id: str) -> None:
        """Renew the runner lease on the configured heartbeat interval.

        Governing contract (backend/core/config.py): the heartbeat interval is
        settings.lease_heartbeat_seconds and must stay strictly below
        settings.lease_timeout_seconds, so a live runner never lets its lease
        expire while long LLM calls are in flight.
        """
        try:
            while True:
                if self.runner is not None and not await self.runner.renew_task_ownership(task_id=task_id):
                    logger.warning(
                        "Task ownership lost during execution of task %s; "
                        "further decisions and actions stopped",
                        task_id,
                    )
                    self._ownership_lost = True
                    return
                await asyncio.sleep(settings.lease_heartbeat_seconds)
        except asyncio.CancelledError:
            raise
    
    async def _apply_reviewed_followups(self, task_id: str) -> bool:
        """Drain at a worker safe point; refuse progress if anything remains.

        Only requests that pass the same safe-point, ownership, permission, and
        uncertain-action gates are applied.  A busy/failing application is left
        pending for a later safe point instead of losing the request.
        """
        try:
            from backend.services.conversation_runtime import ConversationRuntime
            from backend.runtime.followup_instruction_ledger import (
                FollowupInstructionLedgerManager,
            )

            ledger = FollowupInstructionLedgerManager(db=self.db, runner=self.runner)
            pending = ledger.pending(task_id=task_id)
            if not pending:
                return True
            runtime = ConversationRuntime(self.db)
            await self.instruction_revision.apply_pending_followups(
                task_id=task_id,
                runner=self.runner,
                runtime=runtime,
                commit=True,
            )
            return not ledger.pending(task_id=task_id)
        except Exception:
            self.db.rollback()
            logger.warning("Reviewed follow-up application blocked for task %s", task_id, exc_info=True)
            return False

    async def _mark_reviewed_followups_not_applied(self, task_id: str, reason: str) -> None:
        # Cleanup is still a runner mutation. Do not commit/roll back an
        # unrelated caller's raw SQL, autoflush, or live savepoint transaction.
        self.runner._refuse_caller_owned_pending_transaction()
        owned = False
        try:
            from backend.runtime.followup_instruction_ledger import (
                FollowupInstructionLedgerManager,
            )

            self.runner._ensure_transaction()
            owned = self.runner._runner_owns_db_transaction()
            if not owned:
                raise RuntimeError("Follow-up cleanup requires a runner-owned transaction")
            ledger = FollowupInstructionLedgerManager(db=self.db, runner=self.runner)
            ledger.on_task_terminal(task_id, reason=reason)
            self.db.commit()
        except BaseException as error:
            if owned:
                # Ownership was proven before the write. Unlike a post-error
                # physical-state probe, this also covers failed ORM flushes.
                try:
                    self.db.rollback()
                except Exception as rollback_error:
                    error.add_note(f"Follow-up cleanup rollback also failed: {rollback_error!r}")
                    logger.error("Follow-up cleanup rollback failed for task %s", task_id, exc_info=True)
            logger.error(
                "Could not mark reviewed follow-ups not applied for task %s",
                task_id,
                exc_info=True,
            )
            raise
        finally:
            if owned:
                self.runner._clear_runner_transaction_ownership()

    def _get_event_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get event history for a task via the runtime EventLedger."""
        events = self.runner.ledger.event_history(task_id)
        return [
            {
                "event_type": e["event_type"],
                "event_data": e["event_data"],
                "created_at": (
                    e["created_at"].isoformat()
                    if isinstance(e.get("created_at"), datetime)
                    else (e.get("created_at") or "")
                ),
            }
            for e in events
        ]

    @staticmethod
    def _call_fingerprint(params: Dict[str, Any]) -> str:
        """Canonical identity of an EXECUTE_TOOL decision (ExecutionGuard)."""
        return ExecutionGuard.fingerprint(params)

    @classmethod
    def _call_label(cls, params: Dict[str, Any]) -> str:
        """Human-readable, length-bounded label for logs and failure messages."""
        return ExecutionGuard.label(params)

    async def _execute_decision(
        self,
        task_id: str,
        decision_type: str,
        params: Dict[str, Any]
    ) -> bool:
        """
        Execute a model decision.
        
        Args:
            task_id: Task ID
            decision_type: Type of decision
            params: Decision parameters
            
        Returns:
            True if execution was successful
        """
        logger.info(f"Executing decision: {decision_type} with params: {params}")
        
        try:
            if decision_type == DecisionType.COMPLETE_TASK:
                return await self._complete_task(task_id, params)
                
            elif decision_type == DecisionType.FAIL_TASK:
                return await self._fail_task_from_model(task_id, params)
                
            elif decision_type == DecisionType.REQUEST_USER_INPUT:
                return await self._request_user_input(task_id, params)
                
            elif decision_type == DecisionType.RUN_SKILL:
                return await self._run_skill(task_id, params)
                
            elif decision_type == DecisionType.EXECUTE_TOOL:
                return await self._execute_tool(task_id, params)
                
            else:
                logger.error(f"Unknown decision type: {decision_type}")
                return False
                
        except UnresolvedActionEvidenceError as e:
            # Prior unresolved external-action evidence blocks staging any new
            # permission. Fail honestly and preserve that evidence; do not
            # treat this as an ordinary retryable action failure.
            logger.warning(
                "Task %s cannot stage a new permission: unresolved prior "
                "external-action evidence: %s",
                task_id,
                e,
            )
            await self._fail_task(
                task_id,
                code="UNRESOLVED_ACTION_EVIDENCE",
                message=str(e),
            )
            return False
        except Exception as e:
            logger.error(f"Decision execution failed: {e}", exc_info=True)
            return False
    
    async def _complete_task(
        self,
        task_id: str,
        params: Dict[str, Any]
    ) -> bool:
        """Mark a task completed only when durable evidence permits it."""
        task = await self.runner.get_task(task_id)
        if task is not None and task.state == TaskState.WAITING_FOR_USER.value:
            logger.warning("Refusing completion for task %s: task is waiting for user input", task_id)
            return False
        completion_error = self._completion_precondition_error(task_id)
        if completion_error:
            logger.warning("Refusing completion for task %s: %s", task_id, completion_error)
            return False

        summary = params.get('result') or params.get('summary', 'Task completed successfully')
        warnings = params.get('warnings', [])

        # Validate the model's answer against actual tool output data.
        # If the model under-reported (truncated a list, missed items), substitute
        # the full tool result so the user always sees the complete answer.
        task = self.db.query(Task).filter_by(id=task_id).first()
        original_request = task.original_request if task else ""
        summary = self._validate_and_enrich_result(task_id, original_request, summary)

        await self.runner.transition_state(
            task_id=task_id,
            new_state=TaskState.COMPLETED,
            event_type="task.completed",
            event_data={
                "summary": summary,
                "warnings": warnings,
                "completed_at": datetime.utcnow().isoformat()
            },
            final_result=summary
        )

        logger.info(f"Task {task_id} completed: {summary}")
        return True

    def _validate_and_enrich_result(
        self,
        task_id: str,
        original_request: str,
        model_result: str,
    ) -> str:
        """Delegate result validation to the CompletionVerifier component."""
        return self.verifier.validate_and_enrich_result(
            task_id, original_request, model_result
        )


    def _completion_precondition_error(self, task_id: str) -> Optional[str]:
        """Delegate completion gating to the CompletionVerifier component."""
        return self.verifier.precondition_error(task_id)
    
    async def _fail_task_from_model(
        self,
        task_id: str,
        params: Dict[str, Any]
    ) -> bool:
        """Fail task based on model decision."""
        code = params.get('code', 'UNKNOWN_ERROR')
        user_message = params.get('reason') or params.get('user_message', 'Task failed')
        
        await self._fail_task(task_id, code, user_message)
        return True
    
    async def _fail_task(
        self,
        task_id: str,
        code: str,
        message: str
    ) -> bool:
        """Internal method to fail a task."""
        await self.runner.transition_state(
            task_id=task_id,
            new_state=TaskState.FAILED,
            event_type="task.failed",
            event_data={
                "code": code,
                "message": message,
                "failed_at": datetime.utcnow().isoformat()
            },
            terminal_reason=message
        )
        
        logger.warning(f"Task {task_id} failed: {code} - {message}")
        return False
    
    async def _request_user_input(
        self,
        task_id: str,
        params: Dict[str, Any]
    ) -> bool:
        """Request input from user."""
        question = params.get('question', 'I need more information')
        required_fields = params.get('required_fields', [])
        if not isinstance(required_fields, list):
            required_fields = []
        await self.runner.transition_state(
            task_id=task_id,
            new_state=TaskState.WAITING_FOR_USER,
            event_type="task.waiting_for_user",
            event_data={
                "question": question,
                "required_fields": required_fields,
                "waiting_since": datetime.utcnow().isoformat()
            },
            pending_question=question,
        )
        
        logger.info(f"Task {task_id} waiting for user: {question}")
        return True
    
    async def _run_skill(
        self,
        task_id: str,
        params: Dict[str, Any]
    ) -> bool:
        """Execute a declarative skill workflow.

        The enabled validated skill version is pinned to the task. If the skill
        is disabled before its next unstarted step the task is paused instead
        of running against a stale definition.
        """
        skill_id = params.get('skill_id')
        inputs = params.get('inputs', {})
        
        if not skill_id:
            logger.error("run_skill called without skill_id")
            return False
        if isinstance(inputs, str):
            try:
                inputs = json.loads(inputs)
            except json.JSONDecodeError:
                inputs = {}
        if not isinstance(inputs, dict):
            inputs = {}

        outcome = await execute_skill(
            db=self.db,
            runner=self.runner,
            task_id=task_id,
            skill_id=skill_id,
            inputs=inputs,
            tool_executor=self.tool_executor,
        )

        if outcome.paused:
            logger.info(
                f"Task {task_id} paused: skill {skill_id} disabled before its next step"
            )
            return False

        if outcome.success:
            logger.info(f"Skill {skill_id} executed successfully (version {outcome.version})")
            return True
        else:
            logger.warning(f"Skill {skill_id} failed: {outcome.error}")
            return False
    
    async def _execute_tool(
        self,
        task_id: str,
        params: Dict[str, Any],
        approved_by_user: bool = False,
        action_id: Optional[str] = None,
    ) -> bool:
        """Execute a tool."""
        tool_name = params.get('tool_name')
        tool_params = params.get('params', {})
        
        if isinstance(tool_params, str):
            try:
                tool_params = json.loads(tool_params)
            except json.JSONDecodeError:
                tool_params = {}
        
        if not tool_name:
            logger.error("execute_tool called without tool_name")
            return False

        tool = self.tool_executor.get_tool(tool_name)
        policy = tool.get_policy_requirements() if tool else {}
        mutating = bool(policy.get("mutating") or policy.get("destructive") or
                        policy.get("risk_level") in {"medium", "high"})
        if approved_by_user and not mutating:
            raise RuntimeError("Approved action policy is missing or changed; pending evidence preserved")

        task = await self.runner.get_task(task_id)
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        if task.state == TaskState.CANCELLED.value:
            logger.info(f"Task {task_id} cancelled before tool '{tool_name}'; not executing")
            return False

        # Non-authorizing guard: internally approved pending/evidence status is
        # not authenticated human consent. Diagnose it truthfully first, then
        # block before admission, runner handoff, permission consumption, or any
        # external dispatch. This keeps pending rows recoverable.
        if approved_by_user:
            diagnostics_action_id = str(action_id).strip() if isinstance(action_id, str) and action_id.strip() else None
            evidence_error = self.verifier.action_evidence_error(
                task_id,
                mutation=True,
                include_pending=False,
                consuming_action_id=diagnostics_action_id if diagnostics_action_id else None,
            )
            if evidence_error:
                raise UnresolvedActionEvidenceError(evidence_error)
            logger.warning(
                " refusing dispatch for task %s tool '%s': no independently authenticated human consent",
                task_id,
                tool_name,
            )
            return False

        from backend.core.config import settings
        from backend.core.importance_classifier import Importance, classify_action
        # Persisted/runtime config (including env overrides) is not authenticated
        # human consent; fail closed on consequential actions regardless.
        active_autonomy = settings.autonomy_level
        classification = classify_action(
            tool_name,
            policy,
            tool_params,
            autonomy_level=active_autonomy,
            direct_user_instruction=False,
        )
        if classification == Importance.FORBIDDEN:
            logger.warning("Action %s blocked: forbidden by importance classifier", tool_name)
            return False
        # Consequential actions require approval even in autonomous mode; an
        # environmental/config flag is not trusted human consent.
        needs_approval = (
            classification == Importance.CONSEQUENTIAL
            and not approved_by_user
        )
        if needs_approval:
            events = self._get_event_history(task_id)
            was_denied = any(
                e.get("event_type") == "action.denied"
                and e.get("event_data", {}).get("tool_name") == tool_name
                for e in events
            )
            if was_denied:
                logger.info(f"Skipping execution of tool '{tool_name}' because it was previously denied by user")
                return False

            permission_action_id = str(uuid.uuid4())
            # This is execution linkage for the proposed action, not a
            # cryptographic or user-consent binding to the exact parameters or
            # task version. It gives the approved handoff one stable action ID;
            # the runner also checks the durable proposal payload before use.
            pending_data = json.dumps({
                "kind": "permission",
                "tool_name": tool_name,
                "params": tool_params,
                "status": "awaiting",
                "action_id": permission_action_id,
            })
            # Staging another permission must not overwrite evidence from an
            # unresolved action. Recheck under the transition's writer lock.
            self.runner._refuse_caller_owned_pending_transaction()
            started_transaction = self.runner._ensure_transaction()
            try:
                error = self.verifier.action_evidence_error(task_id, mutation=True)
                if error:
                    # Do not stage another permission question over unresolved
                    # prior external-action evidence. Fail honestly on the first
                    # blocked proposal instead of burning retries into a generic
                    # repeated failure. Existing evidence is left untouched.
                    raise UnresolvedActionEvidenceError(error)
                await self.runner.transition_state(
                    task_id=task_id,
                    new_state=TaskState.WAITING_FOR_USER,
                    event_type="action.pending_approval",
                    event_data={"tool_name": tool_name, "params": tool_params, "action_id": permission_action_id},
                    pending_question=f"Do you approve executing tool '{tool_name}'?",
                    pending_action=pending_data,
                )
            finally:
                if started_transaction:
                    self.runner._rollback_owned_transaction()
            return False

        if mutating:
            if action_id is None:
                action_id = str(uuid.uuid4())
            else:
                action_id = str(action_id)
        else:
            action_id = None

        if action_id:
            # Task-scoped ownership gate: an expired or stolen ownership row
            # must not start an external mutation.
            if not self.runner.verify_task_ownership(task_id=task_id):
                logger.warning(
                    "Refusing mutating tool '%s' for task %s: task ownership not held",
                    tool_name,
                    task_id,
                )
                return False
            await self.runner.start_mutating_action(
                task_id, action_id, tool_name, tool_params, approved=approved_by_user,
            )

        if action_id:
            try:
                if not await self.runner.begin_task_action(task_id=task_id, action_id=action_id):
                    return False
            except Exception:
                return False

        # Pre-action governance gate: check for prior failures on same target
        gate_warning = await self._pre_action_gate(task_id, tool_name, tool_params)
        if gate_warning:
            # Log the warning as an event for audit trail
            await self.runner.transition_state(
                task_id=task_id,
                new_state=TaskState.RUNNING,
                event_type="pre_action.warning",
                event_data=gate_warning,
            )
            # The warning will be included in the tool output so the model sees it

        # Execution-scoped context: task identity + cancellation bound to
        # THIS task only (CancellationController), installed by the executor.
        policy_context = await self.cancellation.policy_context(task_id)

        # Conservative action-bound serialization: only the actual external
        # tool execution (and its immediate record) may overlap across tasks.
        if mutating:
            async def _guarded_operation():
                if self._ownership_lost:
                    raise RuntimeError("task ownership lost")
                fresh_task = await self.runner.get_task(task_id)
                if fresh_task is None or fresh_task.state == TaskState.CANCELLED.value:
                    raise RuntimeError("task cancelled")
                # Revalidate exact fenced handoff and all uncertainty under a
                # writer transaction after waiting for the process-local guard.
                # This does not start another action or permit replay.
                if not await self.runner.begin_task_action(task_id=task_id, action_id=action_id):
                    raise RuntimeError("task action handoff no longer valid")
                return await self.tool_executor.execute_tool(
                    tool_name=tool_name,
                    input_data=tool_params,
                    policy_context=policy_context,
                )

            try:
                result: ToolOutput = await _OS_ACTION_GUARD.run(_guarded_operation)
            except RuntimeError as exc:
                logger.warning(
                    "Refusing mutating tool '%s' for task %s: %s",
                    tool_name,
                    task_id,
                    exc,
                )
                return False
        else:
            result: ToolOutput = await self.tool_executor.execute_tool(
                tool_name=tool_name,
                input_data=tool_params,
                policy_context=policy_context,
            )
        # Only commit the operation as runtime progress if ownership and task
        # state are still valid after the external operation has completed.
        # A cancelled or ownership-lost in-flight action is left uncertain and
        # is not reported as successful.
        if self._ownership_lost:
            logger.warning(
                "Not committing mutating tool '%s' for task %s: task ownership lost",
                tool_name,
                task_id,
            )
            self.runner._rollback_owned_transaction()
            return False
        # Serialize the final authorization decision under the runner's transaction
        # so a concurrent stop cannot win between reading task state and
        # completing the action.
        self.runner._ensure_transaction()
        post_task = await self.runner.get_task(task_id)
        if post_task is None or post_task.state != TaskState.RUNNING.value:
            logger.warning(
                "Not committing mutating tool '%s' for task %s: task state changed to %s",
                tool_name,
                task_id,
                getattr(post_task, "state", None),
            )
            self.runner._rollback_owned_transaction()
            return False
        if action_id and not self.runner.verify_task_ownership(task_id=task_id, action_id=action_id):
            logger.warning(
                "Not committing mutating tool '%s' for task %s: task ownership not held",
                tool_name,
                task_id,
            )
            self.runner._rollback_owned_transaction()
            return False
        
        # Record event
        model_summary = result.model_summary
        if not model_summary and result.data is not None:
            model_summary = json.dumps(result.data, default=str)[:1500]
        if not model_summary and result.error is not None:
            model_summary = json.dumps(result.error, default=str)[:1500]
        event_data = {
                "tool_name": tool_name,
                "params": tool_params,
                "success": result.success,
                "output": result.data or result.output if hasattr(result, 'output') else result.data,
                "model_summary": model_summary,
                "error": result.error,
                "evidence": result.evidence,
                "duration_ms": result.duration_ms,
                "mutating": mutating,
                "verification": "passed" if result.success else "failed",
            }
        if action_id:
            await self.runner.finish_mutating_action(task_id, action_id, event_data)
        else:
            await self.runner.transition_state(
                task_id=task_id, new_state=TaskState.RUNNING,
                event_type="tool.executed", event_data=event_data,
            )

        if result.success:
            logger.info(f"Tool {tool_name} executed successfully")
            return True
        else:
            logger.warning(f"Tool {tool_name} failed: {result.error}")
            return False

    async def _pre_action_gate(
        self,
        task_id: str,
        tool_name: str,
        tool_params: Dict[str, Any],
    ) -> Optional[Dict[str, Any]]:
        """Check for prior failures on the same target before executing.

        Queries TaskEvent for:
        - action.started with same tool_name and matching target path (file tools)
        - tool.executed with success=false or verification=failed on same target

        Returns a structured warning dict if prior failures found, else None.
        The caller logs the warning as an event and surfaces it to the model.
        """
        # Only apply to mutating file tools that have a path parameter
        target_path = None
        for key in ("path", "source_path", "destination_path", "target_path", "directory", "file_path"):
            if key in tool_params and isinstance(tool_params[key], str):
                target_path = tool_params[key]
                break

        if not target_path:
            return None

        # Normalize path for comparison
        try:
            from pathlib import Path
            target_path = str(Path(target_path).expanduser().resolve())
        except Exception:
            pass

        # Query for prior failures on this target within this task
        events = (
            self.db.query(TaskEvent)
            .filter(TaskEvent.task_id == task_id)
            .order_by(TaskEvent.sequence_num.desc())
            .limit(50)
            .all()
        )

        prior_failures = []
        for event in events:
            if event.event_type == "action.started":
                try:
                    ed = json.loads(event.event_data)
                    if ed.get("tool_name") == tool_name:
                        # Check if this action was on the same target
                        param_keys = ed.get("parameter_keys", [])
                        for pk in param_keys:
                            if "path" in pk.lower() or pk in tool_params:
                                prior_failures.append({
                                    "action_id": ed.get("action_id"),
                                    "tool_name": tool_name,
                                    "sequence": event.sequence_num,
                                    "created_at": event.created_at.isoformat() if event.created_at else None,
                                })
                                break
                except Exception:
                    pass
            elif event.event_type == "tool.executed":
                try:
                    ed = json.loads(event.event_data)
                    if ed.get("tool_name") == tool_name:
                        # Check if this was on the same target
                        params = ed.get("params", {})
                        for pk in ("path", "source_path", "destination_path", "target_path", "directory", "file_path"):
                            if pk in params and params[pk] == target_path:
                                success = ed.get("success", True)
                                verification = ed.get("verification", "passed")
                                if not success or verification == "failed":
                                    prior_failures.append({
                                        "action_id": ed.get("action_id"),
                                        "tool_name": tool_name,
                                        "sequence": event.sequence_num,
                                        "created_at": event.created_at.isoformat() if event.created_at else None,
                                        "error": ed.get("error"),
                                        "verification": verification,
                                    })
                                break
                except Exception:
                    pass

        if not prior_failures:
            return None

        # Return structured warning
        return {
            "code": "PRIOR_FAILURE_DETECTED",
            "message": f"This target has {len(prior_failures)} prior failure(s) in this task. Consider reviewing before retrying.",
            "prior_failures": prior_failures,
            "target_path": target_path,
            "tool_name": tool_name,
        }

    async def _save_conversation_turn(self, task_id: str, task: Task) -> None:
        """Delegate episodic conversation persistence to the intelligence hook layer."""
        from backend.services.post_task_intelligence import save_conversation_turn

        await save_conversation_turn(self.db, task_id, task)


async def run_task(db_session: Session, task_id: str, tool_executor: ToolExecutor) -> None:
    """
    Run a single task in the background.

    Creates an independent DB session because the request-scoped session
    passed in is closed once the HTTP response is sent. This guarantees the
    long-running execution loop has a live session for its whole duration.

    Args:
        db_session: Original request session (unused; kept for compat)
        task_id: Task ID to execute
        tool_executor: Tool executor instance
    """
    from backend.models.database import get_db_manager

    db_manager = get_db_manager()
    session = db_manager.new_session()
    runner = TaskRunner(session)

    try:
        # Task scope is owned by TaskExecutionLoop itself; no whole-task global
        # runner lease is held here, allowing independent background work.
        loop = TaskExecutionLoop(session, runner, tool_executor)
        await loop.execute_task(task_id)
    finally:
        session.close()
