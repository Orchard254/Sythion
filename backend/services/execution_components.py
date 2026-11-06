"""Narrow components extracted around the TaskExecutionLoop (Phase 1).

The loop remains the orchestrator; each class below owns exactly one
responsibility and contains logic moved verbatim from
``task_execution_loop.py`` so externally observable behavior is unchanged:

- BudgetGuard          — per-task budget enforcement points
- CancellationController — task-scoped cancellation checks / policy contexts
- ExecutionGuard       — repeat-failure and spin guards (no-progress detection)
- ActionExecutor       — decision dispatch + pending tool-call sequencing
- CompletionVerifier   — evidence-gated completion + result validation
- PostTaskPipeline     — best-effort post-terminal hooks (skills, memory,
                         conversation persistence, budget snapshot)

These components depend on runtime interfaces (TaskStateStore, EventLedger)
and injected callables — never on module globals.
"""

from __future__ import annotations

import json
import logging
import re
import uuid
from datetime import datetime
from typing import Any, Callable, Dict, List, Optional

from backend.core.budgets import BudgetExceededError, TaskBudget
from backend.runtime.events import DbEventLedger
from backend.tools.executor import PolicyContext

logger = logging.getLogger(__name__)


# --------------------------------------------------------------------------- #
# BudgetGuard                                                                  #
# --------------------------------------------------------------------------- #


class BudgetGuard:
    """Single enforcement point for per-task budgets inside the loop.

    Wraps a ``TaskBudget`` (which resolves limits from settings). The loop
    consults this guard instead of checking raw counters.
    """

    def __init__(self, budget: TaskBudget):
        self.budget = budget

    def ensure_time_left(self) -> None:
        """Raise BudgetExceededError when the elapsed-time budget is spent."""
        self.budget.check_elapsed()

    def actions_remaining(self) -> int:
        return self.budget.max_actions - self.budget.actions

    def can_execute_action(self) -> bool:
        """False when executing one more action would exceed the limit."""
        return self.actions_remaining() >= 1

    @property
    def max_actions(self) -> int:
        return self.budget.max_actions

    def record_action(self) -> None:
        self.budget.record_action()

    def record_cloud_request(self, input_tokens: int = 0, output_tokens: int = 0) -> None:
        self.budget.record_cloud_request(input_tokens=input_tokens, output_tokens=output_tokens)

    def record_repair(self) -> None:
        self.budget.record_repair()

    def snapshot(self) -> Dict[str, Any]:
        return self.budget.snapshot()


# --------------------------------------------------------------------------- #
# CancellationController                                                       #
# --------------------------------------------------------------------------- #


class CancellationController:
    """Task-scoped cancellation checks against authoritative task state."""

    def __init__(self, state_store):
        self._state_store = state_store

    async def is_cancelled(self, task_id: str) -> bool:
        task = await self._state_store.get_task(task_id)
        return task is not None and task.state == "cancelled"

    async def policy_context(self, task_id: str) -> PolicyContext:
        """Build an execution-scoped PolicyContext bound to this task only."""

        async def _is_cancelled() -> bool:
            current = await self._state_store.get_task(task_id)
            return current is not None and current.state == "cancelled"

        task = await self._state_store.get_task(task_id)
        # Internal/stored task text is never by itself authenticated human consent
        # to execute a confirmation-gated tool directly.
        direct_instruction = False
        return PolicyContext(
            direct_user_instruction=direct_instruction,
            task_id=task_id,
            cancelled_check=_is_cancelled,
        )


# --------------------------------------------------------------------------- #
# ExecutionGuard (repeat-failure + spin guards)                                #
# --------------------------------------------------------------------------- #


class GuardDecision:
    ALLOW = "allow"
    BLOCK_REPEAT = "block_repeat"
    BLOCK_SPIN = "block_spin"
    FAIL_REPEAT = "fail_repeat"
    FAIL_SPIN = "fail_spin"


class ExecutionGuard:
    """No-progress detection: refuses repeated failing calls and spinning
    identical successful calls, escalating to honest failure."""

    # Repeated identical failing calls are a no-progress signal: after a call
    # has failed once, an identical (tool, params) proposal is refused and the
    # model is told to change approach; after REPEAT_HARD_FAIL_LIMIT identical
    # proposals the task fails instead of burning more model tokens.
    REPEAT_BLOCK_AFTER_FAILURES = 1
    REPEAT_HARD_FAIL_LIMIT = 3

    # Repeated identical *successful* calls are also no-progress: the action
    # works but changes nothing. After REPEAT_SPIN_BLOCK_LIMIT consecutive
    # identical proposals the call is refused; after REPEAT_SPIN_FAIL_LIMIT
    # the task fails.
    REPEAT_SPIN_BLOCK_LIMIT = 3
    REPEAT_SPIN_FAIL_LIMIT = 5

    def __init__(self):
        # fingerprint -> number of failed/refused identical proposals
        self._call_attempts: Dict[str, int] = {}
        # spin tracking: consecutive identical EXECUTE_TOOL proposals
        self._last_call_fp: Optional[str] = None
        self._run_len: int = 0

    @staticmethod
    def fingerprint(params: Dict[str, Any]) -> str:
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
    def label(cls, params: Dict[str, Any]) -> str:
        """Human-readable, length-bounded label for logs/failures."""
        fp = cls.fingerprint(params)
        return fp if len(fp) <= 160 else fp[:157] + "..."

    def note_proposal(self, is_tool_decision: bool, params: Dict[str, Any]) -> Optional[str]:
        """Register a proposal; returns its fingerprint for tool decisions."""
        if not is_tool_decision:
            fp = None
        else:
            fp = self.fingerprint(params)
            if self._last_call_fp != fp:
                self._last_call_fp = fp
                self._run_len = 0
            self._run_len += 1
        return fp

    def reset_run(self) -> None:
        """A non-tool decision breaks any consecutive identical run."""
        self._last_call_fp = None
        self._run_len = 0

    def attempts_for(self, fingerprint: Optional[str]) -> int:
        """Recorded failed/refused attempts for a proposal fingerprint."""
        if fingerprint is None:
            return 0
        return self._call_attempts.get(fingerprint, 0)

    def evaluate_failure_guard(self, fingerprint: Optional[str]) -> str:
        """GuardDecision for a previously-seen (possibly failing) proposal."""
        if fingerprint is None:
            return GuardDecision.ALLOW
        attempts = self._call_attempts.get(fingerprint, 0)
        if attempts >= self.REPEAT_HARD_FAIL_LIMIT:
            return GuardDecision.FAIL_REPEAT
        if attempts >= self.REPEAT_BLOCK_AFTER_FAILURES:
            return GuardDecision.BLOCK_REPEAT
        return GuardDecision.ALLOW

    def evaluate_spin_guard(self) -> str:
        """GuardDecision for consecutive identical *successful* proposals."""
        if self._run_len >= self.REPEAT_SPIN_FAIL_LIMIT:
            return GuardDecision.FAIL_SPIN
        if self._run_len >= self.REPEAT_SPIN_BLOCK_LIMIT:
            return GuardDecision.BLOCK_SPIN
        return GuardDecision.ALLOW

    def record_outcome(self, fingerprint: Optional[str], success: bool) -> None:
        """Record execution outcome for repeat-failure accounting."""
        if fingerprint is None:
            return
        if success:
            self._call_attempts.pop(fingerprint, None)
        else:
            self._call_attempts[fingerprint] = self._call_attempts.get(fingerprint, 0) + 1

    def blocked_repeat_event(self, params: Dict[str, Any], fingerprint: str) -> Dict[str, Any]:
        attempts = self._call_attempts.get(fingerprint, 0)
        self._call_attempts[fingerprint] = attempts + 1
        return {
            "tool_name": params.get("tool_name"),
            "params": params.get("params", {}),
            "attempts": attempts,
            "reason": (
                f"This exact action was already attempted {attempts} "
                "time(s) without success and is now blocked. Propose a "
                "different action (e.g. use gui_screenshot for visual inspection, "
                "coordinate clicks gui_click, or alternative tools/window titles), "
                "or complete/fail the task."
            ),
        }

    def blocked_spin_event(self, params: Dict[str, Any]) -> Dict[str, Any]:
        return {
            "tool_name": params.get("tool_name"),
            "params": params.get("params", {}),
            "run_length": self._run_len,
            "reason": (
                f"This exact action has already succeeded "
                f"{self._run_len - 1} consecutive time(s) with no "
                "change. It is blocked. Proceed to the next step, "
                "try a different action, or complete/fail the task."
            ),
        }


# --------------------------------------------------------------------------- #
# CompletionVerifier                                                           #
# --------------------------------------------------------------------------- #


class CompletionVerifier:
    """Evidence-gated completion: the model cannot declare success without
    recorded verification, and under-reported results are enriched from the
    actual tool output."""

    _TRUNCATION_MARKERS = (
        "[...",
        "(truncated",
        "remaining items",
        "remaining tabs",
        "more items",
        "more tabs",
        "partial list",
        "truncated by system",
        "truncated list",
    )

    def __init__(self, db_session, event_history_fn: Callable[[str], List[Dict[str, Any]]]):
        self.db = db_session
        self._event_history = event_history_fn

    def precondition_error(self, task_id: str) -> Optional[str]:
        """Return why evidence does not permit a successful completion.

        Only *mutating* tool failures block completion. Read-only observation
        failures are normal exploration steps and never prevent completion.
        """
        from backend.api.schemas import TaskState
        from backend.models.database import Task

        task = self.db.query(Task).filter_by(id=task_id).first()
        if not task:
            return "Task no longer exists"
        if task.state == TaskState.CANCELLED.value:
            return "Task cancellation was accepted"

        error = self.action_evidence_error(task_id)
        if error:
            return error

        events = self._event_history(task_id)

        for event in events:
            event_type = event["event_type"]
            if event_type in {"action.uncertain", "task.interrupted"}:
                return "An action outcome is uncertain"

        mutating_events = [
            e["event_data"]
            for e in events
            if e.get("event_type") == "tool.executed" and e.get("event_data", {}).get("mutating")
        ]
        if mutating_events:
            last_mutating = mutating_events[-1]
            if not last_mutating.get("success", False):
                return "The final mutating tool action failed and has no recorded resolution"
            if last_mutating.get("verification") != "passed":
                return "The final mutating action has no confirmed verification"

        return None

    def action_evidence_error(self, task_id, *, include_pending=True, consuming_action_id=None,
                              in_progress_action_id=None, proposed_action_id=None, mutation=False):
        """Exact-action obligations, not counts or unrelated later successes.

        Legacy approvals lacking linkage remain unresolved. This is evidence
        gating only: no event here authorizes replay or proves external effects.
        Mutation callers must hold the writer transaction. Only their exact
        validated proposal/current fenced handoff may receive an exemption.
        """
        from backend.models.database import Task
        from backend.runtime.task_ownership import TaskOwnershipManager

        task = self.db.query(Task).filter_by(id=task_id).populate_existing().first()
        if task is None:
            return "Task no longer exists"
        if task.active_action_id is not None and task.active_action_id != in_progress_action_id:
            return "An action outcome is unresolved"
        if in_progress_action_id is not None and task.active_action_id != in_progress_action_id:
            return "The exact in-progress action marker is missing"
        # Deliberately do not swallow reads: unavailable evidence fails closed.
        owner = TaskOwnershipManager(self.db).get(task_id)
        if owner and (owner.uncertain_action_ids or
                      (owner.active_action_id is not None and owner.active_action_id != in_progress_action_id)):
            return "An owned action outcome is uncertain"
        if include_pending and task.pending_action:
            try:
                pending = json.loads(task.pending_action)
            except (ValueError, TypeError):
                return "Pending action evidence is malformed"
            if not isinstance(pending, dict) or pending.get("status") != "denied":
                return "A pending action is unresolved"
        unresolved = {}
        proposals = {}
        proposal = None
        started = {}
        executed = set()
        handoffs = set()
        action_kinds = {
            "action.pending_approval", "action.approved", "action.denied", "action.started",
            "action.handoff_unresolved", "action.uncertain", "task.interrupted", "tool.executed",
        }
        for index, event in enumerate(self._event_history(task_id)):
            kind = event.get("event_type")
            data = event.get("event_data")
            if not mutation:
                data = data or {}
            if mutation and kind not in action_kinds:
                continue
            if mutation:
                if not isinstance(data, dict) or "raw" in data:
                    return "Malformed action evidence is unresolved"
                if kind in {"action.uncertain", "task.interrupted"}:
                    return "An action outcome is uncertain"
                if kind in {"action.started", "tool.executed"} and (
                    not isinstance(data.get("tool_name"), str) or not data["tool_name"].strip()
                ):
                    return "Malformed action tool identity is unresolved"
                if kind == "tool.executed" and (
                    not isinstance(data.get("success"), bool) or not isinstance(data.get("mutating"), bool)
                ):
                    return "Malformed action result is unresolved"
            action_id = data.get("action_id")
            if mutation and action_id is not None and (not isinstance(action_id, str) or not action_id.strip()):
                return "Malformed action identity is unresolved"
            if (proposed_action_id is not None and action_id == proposed_action_id
                    and kind in {"action.started", "action.handoff_unresolved", "tool.executed"}):
                return "This action was already admitted; refusing replay"
            if kind == "action.pending_approval":
                proposal = action_id
                if action_id:
                    proposals[action_id] = data
            elif kind == "action.approved":
                key = action_id or proposal or ("legacy-approval", index)
                unresolved[key] = proposals.get(key, data)
                proposal = None
            elif kind == "action.denied":
                proposal = None
            elif kind in {"action.started", "action.handoff_unresolved"}:
                key = action_id or ("unlinked-action", index)
                unresolved[key] = proposals.get(key, data)
                if kind == "action.started":
                    if mutation and key in started:
                        return "Repeated action start evidence is unresolved"
                    started[key] = started.get(key, 0) + 1
                else:
                    handoffs.add(key)
            elif kind == "tool.executed":
                if mutation and action_id and data.get("mutating") and action_id in executed:
                    return "Repeated action result evidence is unresolved"
                executed.add(action_id)
                if mutation and data.get("mutating") and (
                    not action_id or data.get("success") is not True or data.get("verification") != "passed"
                ):
                    unresolved[action_id or ("unlinked-result", index)] = data
                elif action_id and data.get("mutating") and data.get("success") is True and data.get("verification") == "passed":
                    expected = unresolved.get(action_id)
                    if expected and expected.get("tool_name") == data.get("tool_name") and ("params" not in expected or expected["params"] == data.get("params")):
                        unresolved.pop(action_id)
        # Only a current, separately validated proposal's approval may be
        # consumed. An already-started/handoff/result obligation is never an
        # approval exemption, even when it shares the requested action ID.
        if (consuming_action_id and consuming_action_id not in started
                and consuming_action_id not in handoffs and consuming_action_id not in executed):
            unresolved.pop(consuming_action_id, None)
        if (in_progress_action_id and started.get(in_progress_action_id) == 1
                and in_progress_action_id not in handoffs and in_progress_action_id not in executed):
            # Caller must prove current nonce/fence/expiry before requesting
            # this internal handoff exemption. It is not dispatch authority.
            unresolved.pop(in_progress_action_id, None)
        if unresolved:
            return "An approved or started action has no exact verified result"
        return None


    def validate_and_enrich_result(
        self,
        task_id: str,
        original_request: str,
        model_result: str,
    ) -> str:
        """Validate the model's completion result against actual tool output.

        1. Truncation-marker check — substitute the full tool model_summary.
        2. Count-mismatch check for enumerable listing tools.
        3. Pass-through otherwise.
        """
        from backend.models.database import Task

        events = self._event_history(task_id)

        successful_tool_events = [
            e for e in reversed(events)
            if e.get("event_type") == "tool.executed"
            and e.get("event_data", {}).get("success")
        ]

        if not successful_tool_events:
            return model_result

        last_event_data = successful_tool_events[0].get("event_data", {})
        full_model_summary = last_event_data.get("model_summary", "")
        tool_output = last_event_data.get("output") or {}

        lower_result = model_result.lower()
        if any(marker.lower() in lower_result for marker in self._TRUNCATION_MARKERS):
            if full_model_summary:
                logger.info(
                    "Completion result for task %s contains truncation marker — "
                    "substituting full tool model_summary.",
                    task_id,
                )
                return full_model_summary
            return model_result

        last_tool_name = last_event_data.get("tool_name", "")
        if last_tool_name in ("browser_tabs", "window_list") and isinstance(tool_output, dict):
            reported_count = tool_output.get("count")
            if isinstance(reported_count, int) and reported_count > 1:
                list_lines = re.findall(r"^\s*(?:\d+\.|[-*•])", model_result, re.MULTILINE)
                model_item_count = len(list_lines)
                if (
                    model_item_count < (reported_count // 2)
                    and len(model_result) < 300
                    and full_model_summary
                ):
                    logger.info(
                        "Completion result for task %s mentions only %d item(s) on %s "
                        "(tool returned %d) — substituting full tool model_summary.",
                        task_id,
                        model_item_count,
                        last_tool_name,
                        reported_count,
                    )
                    return full_model_summary

        return model_result


# --------------------------------------------------------------------------- #
# PostTaskPipeline                                                             #
# --------------------------------------------------------------------------- #


class PostTaskPipeline:
    """Best-effort post-terminal hooks; failures must never rewrite
    execution truth or block completion reporting."""

    def __init__(self, db_session, runner, tool_executor, model_call_fn=None):
        self.db = db_session
        self.runner = runner
        self.tool_executor = tool_executor
        self.model_call_fn = model_call_fn

    async def record_budget_snapshot(self, task_id: str, guard: BudgetGuard) -> None:
        try:
            await self.runner.record_event(
                task_id=task_id,
                event_type="task.budget",
                event_data=guard.snapshot(),
            )
        except Exception as e:  # pragma: no cover - evidence is best-effort
            logger.warning(f"Could not record budget snapshot for task {task_id}: {e}")

    async def propose_skill_from_trace(self, task_id: str) -> None:
        try:
            from backend.services.post_task_intelligence import propose_skill_from_trace

            await propose_skill_from_trace(self.db, task_id, self.tool_executor)
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("Proposal generation failed for task %s: %s", task_id, exc)

    async def save_conversation_turn(self, task_id: str) -> None:
        try:
            from backend.services.post_task_intelligence import save_conversation_turn as _save

            task = await self.runner.get_task(task_id)
            if task is None:
                return
            await _save(self.db, task_id, task)
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("Could not save conversation turn for task %s: %s", task_id, exc)

    async def consolidate_memory_from_trace(self, task_id: str) -> None:
        try:
            from backend.services.post_task_intelligence import consolidate_memory_from_trace as _cons

            await _cons(self.db, task_id)
        except Exception as exc:  # pragma: no cover - best-effort
            logger.warning("Memory consolidation failed for task %s: %s", task_id, exc)

    async def diagnose_failed_task(self, task_id: str, model_call_fn: Any = None) -> Any:
        """Best-effort bounded diagnosis for a failed task."""
        try:
            from backend.services.failed_task_diagnostic import diagnose_failed_task as _diagnose

            fn = model_call_fn if model_call_fn is not None else self.model_call_fn
            return await _diagnose(self.db, task_id, self.runner, model_call_fn=fn)
        except Exception as exc:
            logger.warning("Failed-task diagnostic failed for task %s: %s", task_id, exc)
            return "Diagnostic unavailable: diagnostic investigation failed."

    async def investigate_failed_task(self, task_id: str, model_call_fn: Any = None) -> Any:
        return await self.diagnose_failed_task(task_id, model_call_fn=model_call_fn)

    async def run_diagnostic(self, task_id: str, model_call_fn: Any = None) -> Any:
        return await self.diagnose_failed_task(task_id, model_call_fn=model_call_fn)

    def set_model_call_fn(self, fn: Any) -> None:
        self.model_call_fn = fn

    async def schedule_failed_task_diagnostic(self, task_id: str, model_call_fn: Any = None) -> Any:
        """Non-blocking: schedule one conversation-associated failed-task diagnosis."""
        try:
            from backend.services.failed_task_diagnostic import schedule_failed_task_diagnostic as _schedule

            fn = model_call_fn if model_call_fn is not None else self.model_call_fn
            return await _schedule(task_id, model_call_fn=fn)
        except Exception as exc:
            logger.warning("Could not schedule failed-task diagnostic for task %s: %s", task_id, exc)
            return None

    async def drain_pending_diagnostics(self) -> None:
        """Await scheduled diagnostics (test/coordination helper)."""
        try:
            from backend.services.failed_task_diagnostic import await_pending_failed_task_diagnostics

            await await_pending_failed_task_diagnostics()
        except Exception as exc:
            logger.warning("Draining pending diagnostics failed: %s", exc)
    async def apply_plan_outcome(self, task_id: str) -> None:
        """Deterministic goal/plan state update from terminal task evidence.

        Phase 2: plan steps complete ONLY through this evidence path. The
        model cannot mark plan-backed steps completed any other way.
        """
        # Do not block finalization: schedule the diagnostic in an independent
        # session. It remains idempotent and conversation-association-gated.
        try:
            initial_task = await self.runner.get_task(task_id)
            if initial_task is not None and getattr(initial_task, "state", None) == "failed":
                await self.schedule_failed_task_diagnostic(
                    task_id, model_call_fn=self.model_call_fn
                )
        except Exception as exc:
            logger.warning("Failed-task diagnostic scheduling failed for task %s: %s", task_id, exc)

        try:
            from backend.services.planner import Planner
            from backend.services.task_orchestrator import TaskOrchestrator

            orchestrator = TaskOrchestrator(
                state_store=self.runner,
                planner=Planner(self.db),
            )
            await orchestrator.apply_task_outcome(task_id)
        except Exception as exc:  # pragma: no cover - best-effort projection
            logger.warning("Plan outcome application failed for task %s: %s", task_id, exc)

        # Phase 4 event-driven wake: a terminal task is an auditable trigger
        # for the Main Agent. The worker itself enforces policy + dedup, and
        # this hook must never break the task pipeline.
        try:
            from backend.services.wake_worker import request_task_terminal_wake

            task_row = await self.runner.get_task(task_id)
            request_task_terminal_wake(
                task_id, task_row.state if task_row else "unknown"
            )
        except Exception as exc:  # pragma: no cover
            logger.warning("Agent wake submission failed for task %s: %s", task_id, exc)

        # Phase 11: capture terminal outcome as learning evidence (best-effort,
        # never influences runtime invariants).
        try:
            await self.record_learning_outcome(task_id)
        except Exception as exc:  # pragma: no cover
            from backend.learning.capture import log_capture_failure
            log_capture_failure("post_task_learning", exc, task_id=task_id)

    async def record_learning_outcome(self, task_id: str) -> None:
        """Translate terminal task evidence into a durable StrategyOutcome."""
        from backend.learning.capture import record_task_outcome

        task_row = await self.runner.get_task(task_id)
        if task_row is None:
            return
        # Determine verification from completion verifier
        try:
            verification = None
            if task_row.state == "completed":
                err = CompletionVerifier(self.db, lambda tid: self.runner.ledger.event_history(tid) if hasattr(self.runner, "ledger") else []).precondition_error(task_id)
                verification = "failed" if err else "verified"
            elif task_row.state == "failed":
                verification = "failed"
            events = self.runner.ledger.event_history(task_id) if hasattr(self.runner, "ledger") else []
            # Cost/latency from budget snapshot event if present
            cost = None
            latency_ms = None
            attempts = 1
            for ev in reversed(events):
                if ev.get("event_type") == "task.budget":
                    cost = ev.get("event_data")
                    break
            # Evidence refs: require at least task id itself if completed
            evidence_refs = [f"task:{task_id}"] if task_row.state in ("completed", "failed") else []
            # Infer strategy from cognitive decision event
            decision_type = None
            intent_cat = None
            for ev in events:
                if ev.get("event_type") == "cognitive.decision.made":
                    decision_type = ev.get("event_data", {}).get("decision_type")
                    intent_cat = ev.get("event_data", {}).get("intent_category")
                    break
            record_task_outcome(
                self.db,
                task_id=task_id,
                intent_category=intent_cat,
                decision_type=decision_type,
                task_state=task_row.state,
                verification=verification,
                model_id=None,
                cost=cost if isinstance(cost, dict) else None,
                latency_ms=latency_ms,
                attempts=attempts,
                evidence_refs=evidence_refs,
                goal_id=getattr(task_row, "objective_id", None),
            )
        except Exception as exc:
            from backend.learning.capture import log_capture_failure
            log_capture_failure("task_learning_outcome", exc, task_id=task_id)
