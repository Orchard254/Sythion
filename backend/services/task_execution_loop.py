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
from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError, DecisionType
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
        self.model_engine = ModelDecisionEngine(db_session, tool_executor)

        # Extracted components (one responsibility each).
        self.guard = ExecutionGuard()
        self.cancellation = CancellationController(task_runner)
        self.verifier = CompletionVerifier(db_session, self._get_event_history)
        self.post_task_pipeline = PostTaskPipeline(db_session, task_runner, tool_executor)

    async def execute_task(self, task_id: str) -> None:
        """
        Execute a single task from start to completion.

        Args:
            task_id: ID of the task to execute

        Raises:
            Exception: If task execution fails critically
        """
        logger.info(f"Starting execution of task: {task_id}")

        repair_attempts = 0
        refused_completions = 0  # consecutive complete_task calls refused by precondition guard
        budget = BudgetGuard(TaskBudget())

        # Start a lease heartbeat so long LLM calls don't let the lease
        # expire mid-task (expiry governed by settings.lease_timeout_seconds,
        # renewal interval by settings.lease_heartbeat_seconds). Without
        # this, a second request can steal the lease and we get spurious
        # 409s or double execution.
        heartbeat = asyncio.create_task(self._heartbeat_lease(task_id))

        try:
            # Transition to running state (no-op if the route already did)
            task = await self.runner.get_task(task_id)
            if task is None:
                raise RuntimeError(f"Task {task_id} not found")
            if task.state != TaskState.RUNNING.value:
                task = await self.runner.transition_state(
                    task_id=task_id,
                    new_state=TaskState.RUNNING,
                    event_type="task.execution_started",
                    event_data={"started_at": datetime.utcnow().isoformat()}
                )

            while True:
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
                    break

                # Check for pending action (approval/denial)
                if task.pending_action:
                    try:
                        pending = json.loads(task.pending_action)
                    except (json.JSONDecodeError, TypeError):
                        pending = {}
                    p_status = pending.get("status")
                    p_tool = pending.get("tool_name")
                    p_params = pending.get("params", {})
                    if p_status == "approved" and p_tool:
                        logger.info(f"Executing approved pending action: {p_tool}")
                        await self.runner.clear_pending_action(task_id)
                        budget.record_action()
                        await self._execute_tool(
                            task_id,
                            {"tool_name": p_tool, "params": p_params},
                            approved_by_user=True,
                        )
                        continue
                    elif p_status == "denied":
                        logger.info(f"Skipping denied pending action: {p_tool}")
                        await self.runner.clear_pending_action(task_id)
                        await self.runner.transition_state(
                            task_id=task_id,
                            new_state=TaskState.RUNNING,
                            event_type="action.denied",
                            event_data={"tool_name": p_tool, "params": p_params},
                        )
                        continue

                # Get event history for context
                events = self._get_event_history(task_id)

                # Request decision from model
                repair_context = None
                try:
                    decision_type, params = await self.model_engine.make_decision(
                        task_id=task.id,
                        original_request=task.original_request,
                        normalized_goal=task.normalized_goal,
                        event_history=events,
                        current_state=task.state,
                        pending_question=task.pending_question,
                        repair_context=repair_context,
                        budget=budget.budget,
                    )
                    repair_attempts = 0  # Reset on successful decision

                except BudgetExceededError as e:
                    # Budget exhaustion is terminal: fail honestly, never repair.
                    logger.warning(f"Task {task_id} exceeded budget {e.code}: {e.message}")
                    await self._fail_task(task_id, code=e.code, message=e.message)
                    break
                except ModelDecisionError as e:
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
                    if e.code == "MISSING_CONTROL_CALL":
                        repair_context = (
                            "CRITICAL ERROR: You outputted raw text instead of a tool call. "
                            "You MUST call the 'complete_task' tool with your answer inside 'result' "
                            "(e.g. complete_task(result=\"...\")). Do NOT output raw text."
                        )
                    else:
                        repair_context = (
                            f"Your previous decision was rejected: {e.message} "
                            f"(code {e.code}). Respond with a valid tool call."
                        )
                    continue


                # Re-check cancellation before executing: a cancel request may
                # have arrived during the (slow) model call. A cancelled task
                # must never trigger an OS action.
                task = await self.runner.get_task(task_id)
                if task.state == TaskState.CANCELLED.value:
                    logger.info(f"Task {task_id} was cancelled during decision; not executing")
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

                # Execute any pending tool calls from the same model response
                if success:
                    pending = self.model_engine.pop_pending_tool_calls()
                    for pending_call in pending:
                        p_type = pending_call["decision_type"]
                        p_args = pending_call["args"]
                        budget.record_action()
                        p_success = await self._execute_decision(
                            task_id=task_id,
                            decision_type=p_type,
                            params=p_args
                        )
                        if not p_success:
                            logger.warning(f"Pending tool call {p_type} failed; stopping sequential execution")
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
                    break

        except Exception as e:
            logger.error(f"Critical error in task execution: {e}", exc_info=True)
            await self._fail_task(
                task_id,
                code="EXECUTION_ERROR",
                message=str(e)
            )
            raise
        finally:
            heartbeat.cancel()
            try:
                await heartbeat
            except (asyncio.CancelledError, Exception):
                pass

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
                if not await self.runner.renew_lease(task_id):
                    logger.warning(
                        f"Lease lost during execution of task {task_id}; "
                        "another runner may have taken over"
                    )
                    return
                await asyncio.sleep(settings.lease_heartbeat_seconds)
        except asyncio.CancelledError:
            raise
    
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
                
        except Exception as e:
            logger.error(f"Decision execution failed: {e}", exc_info=True)
            return False
    
    async def _complete_task(
        self,
        task_id: str,
        params: Dict[str, Any]
    ) -> bool:
        """Mark a task completed only when durable evidence permits it."""
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
        
        await self.runner.transition_state(
            task_id=task_id,
            new_state=TaskState.WAITING_FOR_USER,
            event_type="task.waiting_for_user",
            event_data={
                "question": question,
                "required_fields": required_fields,
                "waiting_since": datetime.utcnow().isoformat()
            },
            pending_question=question
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

        task = await self.runner.get_task(task_id)
        if not task:
            raise RuntimeError(f"Task {task_id} not found")
        if task.state == TaskState.CANCELLED.value:
            logger.info(f"Task {task_id} cancelled before tool '{tool_name}'; not executing")
            return False

        from backend.core.config import settings
        from backend.core.importance_classifier import Importance, classify_action
        active_autonomy = os.environ.get("SYTHION_AUTONOMY_LEVEL", settings.autonomy_level)
        classification = classify_action(
            tool_name,
            policy,
            tool_params,
            autonomy_level=active_autonomy,
            direct_user_instruction=bool(task.original_request.strip()),
        )
        if classification == Importance.FORBIDDEN:
            logger.warning("Action %s blocked: forbidden by importance classifier", tool_name)
            return False
        needs_approval = (
            classification == Importance.CONSEQUENTIAL
            and not approved_by_user
            and active_autonomy != "autonomous"
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

            pending_data = json.dumps({
                "tool_name": tool_name,
                "params": tool_params,
                "status": "awaiting",
            })
            await self.runner.transition_state(
                task_id=task_id,
                new_state=TaskState.WAITING_FOR_USER,
                event_type="action.pending_approval",
                event_data={"tool_name": tool_name, "params": tool_params},
                pending_question=f"Do you approve executing tool '{tool_name}'?",
                pending_action=pending_data,
            )
            return False

        action_id = str(uuid.uuid4()) if mutating else None
        if action_id:
            await self.runner.record_action_started(
                task_id=task_id,
                action_id=action_id,
                tool_name=tool_name,
                parameter_keys=list(tool_params.keys()),
            )

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

        # Execute tool
        result: ToolOutput = await self.tool_executor.execute_tool(
            tool_name=tool_name,
            input_data=tool_params,
            policy_context=policy_context,
        )
        
        # Record event
        model_summary = result.model_summary
        if not model_summary and result.data is not None:
            model_summary = json.dumps(result.data, default=str)[:1500]
        if not model_summary and result.error is not None:
            model_summary = json.dumps(result.error, default=str)[:1500]
        await self.runner.transition_state(

            task_id=task_id,
            new_state=TaskState.RUNNING,  # Stay in running
            event_type="tool.executed",
            event_data={
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
            },
            active_action_id=None,
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
        # Acquire lease
        if not await runner.acquire_lease(task_id=task_id):
            raise RuntimeError("Another task runner is currently active")

        loop = TaskExecutionLoop(session, runner, tool_executor)
        await loop.execute_task(task_id)
    finally:
        try:
            await runner.release_lease()
        except Exception:
            pass
        session.close()
