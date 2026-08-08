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
from backend.services.skill_engine import execute_skill
from backend.tools.executor import PolicyContext, ToolExecutor, ToolOutput
from backend.models.database import Task, TaskEvent
from backend.api.schemas import TaskState
from backend.core.budgets import TaskBudget, BudgetExceededError

logger = logging.getLogger(__name__)


class TaskExecutionLoop:
    """
    Main execution loop for processing tasks.
    
    This is the core intelligence loop that:
    1. Observes current task state
    2. Requests decision from LLM
    3. Executes the decided action (tool/skill)
    4. Verifies results
    5. Updates task state
    6. Repeats until completion or failure
    """
    
    MAX_ACTIONS_PER_TASK = 50
    MAX_REPAIR_ATTEMPTS = 2

    # Repeated identical failing calls are a no-progress signal: after a call
    # has failed once, an identical (tool, params) proposal is refused and the
    # model is told to change approach; after REPEAT_HARD_FAIL_LIMIT identical
    # proposals the task fails instead of burning more model tokens.
    REPEAT_BLOCK_AFTER_FAILURES = 1
    REPEAT_HARD_FAIL_LIMIT = 3

    # Repeated identical *successful* calls are also no-progress: the action
    # works but changes nothing, so the model is just spinning. After
    # REPEAT_SPIN_BLOCK_LIMIT consecutive identical proposals the call is
    # refused; after REPEAT_SPIN_FAIL_LIMIT the task fails instead of burning
    # the whole action budget on unchanged re-executions.
    REPEAT_SPIN_BLOCK_LIMIT = 3
    REPEAT_SPIN_FAIL_LIMIT = 5

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
        # fingerprint -> number of times the model has proposed this exact
        # (tool, params) call (failed executions + refused repeats).
        self._call_attempts: Dict[str, int] = {}
        # Consecutive-run tracking for the spin guard: the fingerprint of the
        # last proposed EXECUTE_TOOL call and how many times it was proposed
        # in a row (resets whenever a different call is proposed).
        self._last_call_fp: Optional[str] = None
        self._run_len: int = 0
        
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
        action_count = 0
        budget = TaskBudget()
        
        # Start a lease heartbeat so long LLM calls don't let the lease
        # expire mid-task (30s expiry). Without this, a second request can
        # steal the lease and we get spurious 409s or double execution.
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
                    budget.check_elapsed()
                except BudgetExceededError as e:
                    await self._fail_task(task_id, code=e.code, message=e.message)
                    break

                # Check action limit
                if action_count >= self.MAX_ACTIONS_PER_TASK:
                    await self._fail_task(
                        task_id,
                        code="ACTION_LIMIT_EXCEEDED",
                        message=f"Exceeded maximum {self.MAX_ACTIONS_PER_TASK} actions"
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
                        task.pending_action = None
                        self.db.commit()
                        action_count += 1
                        budget.record_action()
                        await self._execute_tool(
                            task_id,
                            {"tool_name": p_tool, "params": p_params},
                            approved_by_user=True,
                        )
                        continue
                    elif p_status == "denied":
                        logger.info(f"Skipping denied pending action: {p_tool}")
                        task.pending_action = None
                        self.db.commit()
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
                        budget=budget,
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
                    
                    # Try again, telling the model what went wrong
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

                # Repeat-failure guard: an identical call that already failed
                # is never re-run unchanged. Refusing the repeat (instead of
                # executing and re-asking) stops the model from burning tokens
                # on a dead-end; if it keeps proposing the same call the task
                # fails honestly and quickly.
                fingerprint = None
                if decision_type == DecisionType.EXECUTE_TOOL:
                    fingerprint = self._call_fingerprint(params)
                    attempts = self._call_attempts.get(fingerprint, 0)
                    # Track consecutive identical proposals for the spin guard;
                    # any different call resets the run.
                    if self._last_call_fp != fingerprint:
                        self._last_call_fp = fingerprint
                        self._run_len = 0
                    self._run_len += 1
                    if attempts >= self.REPEAT_HARD_FAIL_LIMIT:
                        await self._fail_task(
                            task_id,
                            code="REPEATED_ACTION_FAILURE",
                            message=(
                                f"Action {self._call_label(params)} was proposed "
                                f"{attempts} times without progress; aborting instead of "
                                "repeating the same failing approach"
                            ),
                        )
                        break
                    if attempts >= self.REPEAT_BLOCK_AFTER_FAILURES:
                        await self.runner.transition_state(
                            task_id=task_id,
                            new_state=TaskState.RUNNING,
                            event_type="action.repeat_blocked",
                            event_data={
                                "tool_name": params.get("tool_name"),
                                "params": params.get("params", {}),
                                "attempts": attempts,
                                "reason": (
                                    f"This exact action was already attempted {attempts} "
                                    "time(s) without success and is now blocked. Propose a "
                                    "different action, or complete/fail the task."
                                ),
                            },
                        )
                        self._call_attempts[fingerprint] = attempts + 1
                        logger.warning(
                            f"Blocked repeated failing action for task {task_id}: "
                            f"{self._call_label(params)}"
                        )
                        continue
                    # Spin guard: identical calls that keep succeeding but change
                    # nothing are refused, then fail the task, so the model can't
                    # burn the whole budget re-confirming the same state.
                    if self._run_len >= self.REPEAT_SPIN_FAIL_LIMIT:
                        await self._fail_task(
                            task_id,
                            code="SPINNING_ACTION",
                            message=(
                                f"Action {self._call_label(params)} was proposed "
                                f"{self._run_len} consecutive times successfully without "
                                "changing the result; aborting instead of spinning"
                            ),
                        )
                        break
                    if self._run_len >= self.REPEAT_SPIN_BLOCK_LIMIT:
                        await self.runner.transition_state(
                            task_id=task_id,
                            new_state=TaskState.RUNNING,
                            event_type="action.spin_blocked",
                            event_data={
                                "tool_name": params.get("tool_name"),
                                "params": params.get("params", {}),
                                "run_length": self._run_len,
                                "reason": (
                                    f"This exact action has already succeeded "
                                    f"{self._run_len - 1} consecutive time(s) with no "
                                    "change. It is blocked. Proceed to the next step, "
                                    "try a different action, or complete/fail the task."
                                ),
                            },
                        )
                        logger.warning(
                            f"Blocked repeated spinning action for task {task_id}: "
                            f"{self._call_label(params)}"
                        )
                        continue
                else:
                    self._last_call_fp = None
                    self._run_len = 0

                # Execute the decision
                action_count += 1
                budget.record_action()
                success = await self._execute_decision(
                    task_id=task_id,
                    decision_type=decision_type,
                    params=params
                )

                if fingerprint:
                    if success:
                        self._call_attempts.pop(fingerprint, None)
                    else:
                        self._call_attempts[fingerprint] = \
                            self._call_attempts.get(fingerprint, 0) + 1

                if not success:
                    # Action failed - let model decide next step
                    logger.warning(f"Action failed for task {task_id}, continuing loop")
                
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
                    await self._record_budget_snapshot(task_id, budget)
                    if task.state == TaskState.COMPLETED.value:
                        await self._propose_skill_from_trace(task_id)
                        await self._save_conversation_turn(task_id, task)
                        await self._consolidate_memory_from_trace(task_id)
                    elif task.state == TaskState.FAILED.value:
                        await self._save_conversation_turn(task_id, task)
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
        """Delegate post-task skill proposal growth to the intelligence hook layer."""
        from backend.services.post_task_intelligence import propose_skill_from_trace

        await propose_skill_from_trace(self.db, task_id, self.tool_executor)

    async def _consolidate_memory_from_trace(self, task_id: str) -> None:
        """Delegate post-task memory consolidation to the intelligence hook layer."""
        from backend.services.post_task_intelligence import consolidate_memory_from_trace

        await consolidate_memory_from_trace(self.db, task_id)

    async def _record_budget_snapshot(self, task_id: str, budget: TaskBudget) -> None:
        """Record an evidence-safe summary of per-task budget usage."""
        try:
            await self.runner.record_event(
                task_id=task_id,
                event_type="task.budget",
                event_data=budget.snapshot(),
            )
        except Exception as e:  # pragma: no cover - evidence is best-effort
            logger.warning(f"Could not record budget snapshot for task {task_id}: {e}")

    async def _heartbeat_lease(self, task_id: str) -> None:
        """Renew the runner lease every few seconds during execution."""
        try:
            while True:
                if not await self.runner.renew_lease(task_id):
                    logger.warning(
                        f"Lease lost during execution of task {task_id}; "
                        "another runner may have taken over"
                    )
                    return
                await asyncio.sleep(10)
        except asyncio.CancelledError:
            raise
    
    def _get_event_history(self, task_id: str) -> List[Dict[str, Any]]:
        """Get event history for a task."""
        events = self.db.query(TaskEvent).filter_by(task_id=task_id).order_by(
            TaskEvent.sequence_num.asc()
        ).all()
        
        def _safe_load(data):
            if not data:
                return {}
            try:
                return json.loads(data)
            except (json.JSONDecodeError, TypeError):
                # Fall back for malformed stored JSON (e.g. Windows paths)
                try:
                    return json.loads(data.replace("\\", "\\\\"))
                except (json.JSONDecodeError, TypeError):
                    return {"raw": str(data)}
        
        return [
            {
                "event_type": e.event_type,
                "event_data": _safe_load(e.event_data),
                "created_at": e.created_at.isoformat() if e.created_at else ""
            }
            for e in events
        ]
    
    @staticmethod
    def _call_fingerprint(params: Dict[str, Any]) -> str:
        """Canonical identity of an EXECUTE_TOOL decision: tool + sorted params."""
        if not isinstance(params, dict):
            return ""
        tool_name = params.get("tool_name")
        tool_params = params.get("params", {})
        if isinstance(tool_params, str):
            try:
                tool_params = json.loads(tool_params)
            except (json.JSONDecodeError, TypeError):
                tool_params = {}
        if not isinstance(tool_params, dict):
            tool_params = {}
        return f"{tool_name}|{json.dumps(tool_params, sort_keys=True, default=str, ensure_ascii=False)}"

    @classmethod
    def _call_label(cls, params: Dict[str, Any]) -> str:
        """Human-readable, length-bounded label for logs and failure messages."""
        label = cls._call_fingerprint(params)
        return label if len(label) <= 160 else label[:157] + "..."

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

    def _completion_precondition_error(self, task_id: str) -> Optional[str]:
        """Return why evidence does not permit a successful completion.

        Only *mutating* tool failures block completion.  Read-only observation
        failures (e.g. app_focus returning WINDOW_NOT_FOUND, or app_list
        finding no matches) are normal exploration steps and must never
        prevent a task from completing after a subsequent success.
        """
        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            return "Task no longer exists"
        if task.state == TaskState.CANCELLED.value:
            return "Task cancellation was accepted"

        for event in self._get_event_history(task_id):
            event_type = event["event_type"]
            if event_type in {"action.uncertain", "task.interrupted"}:
                return "An action outcome is uncertain"

        mutating_events = [
            e["event_data"]
            for e in self._get_event_history(task_id)
            if e.get("event_type") == "tool.executed" and e.get("event_data", {}).get("mutating")
        ]
        if mutating_events:
            last_mutating = mutating_events[-1]
            if not last_mutating.get("success", False):
                return "The final mutating tool action failed and has no recorded resolution"
            if last_mutating.get("verification") != "passed":
                return "The final mutating action has no confirmed verification"

        return None
    
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

        async def _is_cancelled() -> bool:
            current = await self.runner.get_task(task_id)
            return current is not None and current.state == TaskState.CANCELLED.value

        # Execute tool
        result: ToolOutput = await self.tool_executor.execute_tool(
            tool_name=tool_name,
            input_data=tool_params,
            policy_context=PolicyContext(
                direct_user_instruction=bool(task.original_request.strip()),
                cancelled_check=_is_cancelled,
            ),
        )
        
        # Record event
        model_summary = result.model_summary
        if not model_summary and result.data is not None:
            model_summary = json.dumps(result.data, default=str)[:200]
        if not model_summary and result.error is not None:
            model_summary = json.dumps(result.error, default=str)[:200]
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
