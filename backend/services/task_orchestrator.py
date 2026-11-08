"""TaskOrchestrator — durable work orchestration above the Runtime Kernel.

Phase 2: bridged plan steps to runtime tasks through the TaskStateStore.
Phase 3: adds deterministic crash recovery, a bounded persisted retry policy,
failure taxonomy, runnable-goal queries, and explicitly initiated automatic
continuation ("goal execution session"). It owns orchestration DECISIONS,
never runtime safety: no tool execution, no direct task-state writes, no
forged verification.
"""

from __future__ import annotations

import logging
from datetime import datetime
from enum import Enum
from typing import Any, Dict, List, Optional

from backend.models.database import Objective

logger = logging.getLogger(__name__)


class FailureCategory(str, Enum):
    """Deterministic failure taxonomy for step outcomes."""

    EXECUTION_FAILURE = "execution_failure"
    VERIFICATION_FAILURE = "verification_failure"
    INTERRUPTION = "interruption"
    TIMEOUT = "timeout"
    INVALID_PLAN = "invalid_plan"
    RECOVERY_FAILURE = "recovery_failure"


# Only these categories are retried automatically (bounded by policy).
RETRYABLE_CATEGORIES = {
    FailureCategory.EXECUTION_FAILURE,
    FailureCategory.VERIFICATION_FAILURE,
}


def categorize_failure(
    task_state: Optional[str],
    terminal_reason: Optional[str],
    precondition_error: Optional[str],
    machine_code: Optional[str] = None,
) -> FailureCategory:
    """Deterministic mapping from runtime evidence to a failure category.

    Classification precedence:
      1. task state INTERRUPTION;
      2. machine-readable code from the kernel's own ``task.failed`` event
         (e.g. ``ELAPSED_TIME_EXCEEDED`` → TIMEOUT);
      3. verifier output → VERIFICATION_FAILURE;
      4. conservative default: EXECUTION_FAILURE.
    Human-readable terminal reasons are never parsed for classification.
    """
    if task_state == "interrupted":
        return FailureCategory.INTERRUPTION

    _TIMEOUT_CODES = {"ELAPSED_TIME_EXCEEDED"}
    if machine_code and machine_code in _TIMEOUT_CODES:
        return FailureCategory.TIMEOUT

    if precondition_error:
        return FailureCategory.VERIFICATION_FAILURE
    return FailureCategory.EXECUTION_FAILURE


def failure_code_for(db, task_id: str) -> Optional[str]:
    """Machine-readable code from the last ``task.failed`` ledger event."""
    try:
        from backend.runtime.events import DbEventLedger

        for event in reversed(DbEventLedger(db).event_history(task_id)):
            if event["event_type"] == "task.failed":
                data = event.get("event_data") or {}
                code = data.get("code")
                return str(code) if code else None
    except Exception:  # pragma: no cover - classification is best-effort
        return None
    return None


class RetryPolicy:
    """Bounded, persisted, auditable per-step retry policy.

    - attempts are incremented only when an execution attempt STARTS;
    - recovery never increments attempts;
    - retries are allowed only for RETRYABLE_CATEGORIES and only while
      ``attempt_count < limit``; the counter lives on the PlanStep row.
    """

    def __init__(self, max_attempts: int, retryable=RETRYABLE_CATEGORIES):
        self.max_attempts = max(1, int(max_attempts))
        self.retryable = set(retryable)

    def can_retry(self, attempt_count: int, category: FailureCategory) -> bool:
        return (
            category in self.retryable
            and attempt_count < self.max_attempts
        )


class TaskOrchestrator:
    def __init__(self, state_store, planner, retry_policy: Optional[RetryPolicy] = None):
        """Args mirror Phase 1 interfaces: state_store is a TaskStateStore
        (TaskRunner), planner is services.planner.Planner."""
        from backend.core.config import settings

        self.state_store = state_store
        self.planner = planner
        self.retry_policy = retry_policy or RetryPolicy(settings.step_max_attempts)

    # ------------------------------------------------------------------ #
    # Step → task binding                                                  #
    # ------------------------------------------------------------------ #

    async def start_next_step(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """Select and bind the next eligible step to a fresh runtime task.

        The caller must hold the runner lease (create_task verifies it).
        Returns {'step', 'task'} or None when no step is eligible. A paused /
        terminal goal is never started.
        """
        from backend.services.goal_manager import GoalManager

        manager = GoalManager(self.planner.db)
        goal = manager.get_goal(goal_id)
        if goal is None or goal.status != "active":
            return None  # paused / completed / failed / abandoned: no new work

        step = self.planner.select_next_step(goal_id)
        if step is None:
            return None

        idempotency_key = f"goalstep:{step.id}:{step.attempt_count or 0}"
        task = await self.state_store.create_task(
            original_request=step.description,
            idempotency_key=idempotency_key,
        )
        # Associate the execution task with the goal for projections/recall.
        self.state_store.set_task_objective(task.id, goal_id)
        self.planner.mark_step_started(step.id, task.id)
        return {"step": self.planner.get_step(step.id), "task": task}

    # ------------------------------------------------------------------ #
    # Outcome application (evidence-gated, with bounded retry)             #
    # ------------------------------------------------------------------ #

    async def apply_task_outcome(
        self,
        task_id: str,
        completion_precondition_error_fn=None,
    ) -> Optional[Dict[str, Any]]:
        """Deterministically update plan/goal state from terminal task evidence.

        Completion requires: task completed + no completion precondition
        error from the kernel's own verifier. Failures are categorised; a
        bounded RETRY may reset the step to READY (audited), otherwise the
        step FAILS and the goal is PAUSED pending owner replanning.

        Flow: bind ``task_id`` exactly via ``PlanStep.evidence_task_id``;
        ignore non-running steps; categorise evidence; complete or fail
        the step; recompute goal progress; append observational learning
        captures (never affecting the outcome). Returns a dict with the
        fresh step, ``outcome``, ``category`` and ``goal_progress``, or
        ``None`` for non-plan-backed tasks. Learning capture is delegated
        to :meth:`_capture_retry_outcome` and
        :meth:`_capture_planning_real_outcome`.
        """
        from backend.models.database import PlanStep

        planner = self.planner
        # Evidence binding is exact: only evidence_task_id establishes plan-step
        # binding. linked_task_id is display/projection only and must not be
        # treated as task outcome evidence.
        step = (
            planner.db.query(PlanStep)
            .filter(PlanStep.evidence_task_id == task_id)
            .first()
        )
        if step is None or step.plan_id is None:
            return None  # not a plan-backed execution; nothing to do

        if step.status != "running":
            logger.info(
                "Step %s (%s) already %s; ignoring outcome of task %s",
                step.id, step.step_key, step.status, task_id,
            )
            return {"step": step, "updated": False}

        verifier_error_fn = (
            completion_precondition_error_fn
            or _default_completion_precondition(planner.db, task_id)
        )

        # Prefer the kernel's own terminal reason when the evidence task
        # did not complete (richer than the generic gate message).
        from backend.models.database import Task

        evidence_task = planner.db.query(Task).filter_by(id=task_id).first()
        precondition_error = (
            verifier_error_fn(task_id)
            if callable(verifier_error_fn) else None
        )
        task_completed = (
            evidence_task is not None and evidence_task.state == "completed"
        )
        category: Optional[FailureCategory] = None
        if not task_completed:
            category = categorize_failure(
                getattr(evidence_task, "state", None),
                getattr(evidence_task, "terminal_reason", None),
                None,
                machine_code=failure_code_for(planner.db, task_id),
            )
        elif precondition_error:
            category = categorize_failure("completed", None, precondition_error)

        outcome: str
        try:
            if category is not None:
                raise ValueError(self._failure_message(evidence_task, precondition_error))
            planner.complete_step_from_evidence(
                step.id,
                task_id,
                completion_precondition_error_fn=verifier_error_fn,
            )
            outcome = "completed"
        except ValueError as exc:
            logger.warning("Step %s could not be completed from evidence: %s", step.id, exc)
            category = category or FailureCategory.EXECUTION_FAILURE
            outcome, category = self._handle_failure(step, str(exc), task_id, category)

        progress = planner.recompute_goal_after_step_terminal(step.objective_id)
        self._capture_retry_outcome(step, task_id, outcome, category)
        self._capture_planning_real_outcome(step, task_id, outcome, category, evidence_task)
        return {
            "step": self.planner.get_step(step.id),
            "outcome": outcome,
            "category": category.value if category else None,
            "goal_progress": progress,
            "updated": True,
        }

    def _capture_retry_outcome(
        self, step, task_id: str, outcome: str, category: Optional[FailureCategory]
    ) -> None:
        """Phase 11.5 per-attempt outcome capture preserving retry context (§3).

        Observational only: maps the orchestrator ``outcome`` to the
        learning taxonomy and records it; failures are logged via
        ``log_capture_failure`` and never propagate.
        """
        planner = self.planner
        try:
            from backend.learning.capture import log_capture_failure, record_retry_outcome

            # Map orchestrator outcome to learning taxonomy
            retry_outcome_label = (
                "SUCCESS_VERIFIED"
                if outcome == "completed"
                else (
                    "FAILED_VERIFICATION"
                    if category == FailureCategory.VERIFICATION_FAILURE
                    else "FAILED"
                )
            )
            record_retry_outcome(
                planner.db,
                task_id=task_id,
                step_id=step.id,
                attempt=step.attempt_count or 1,
                outcome=retry_outcome_label,
                goal_id=step.objective_id,
                cost=None,
                evidence_refs=[f"task:{task_id}", f"plan_step:{step.id}"],
            )
        except Exception as exc:
            from backend.learning.capture import log_capture_failure

            log_capture_failure(
                "retry_outcome", exc,
                task_id=task_id, step_id=step.id, goal_id=step.objective_id,
            )

    def _capture_planning_real_outcome(
        self,
        step,
        task_id: str,
        outcome: str,
        category: Optional[FailureCategory],
        evidence_task,
    ) -> None:
        """Phase 12.5 REAL_EXECUTION planning outcome — observational only.

        Authoritative boundary: after CompletionVerifier gate + step state
        transition + goal recomputation. Only verified terminal task
        evidence can justify REAL_EXECUTION provenance. Failures are
        logged and never propagate.
        """
        planner = self.planner
        try:
            import hashlib
            import json

            from backend.learning.capture import infer_planning_strategy
            from backend.learning.planning_store import PlanningOutcomeStore
            from backend.models.database import GoalPlan

            fresh_step = planner.get_step(step.id)
            effective_step = fresh_step or step
            plan = planner.active_plan(step.objective_id)
            if plan is None:
                try:
                    plan = planner.db.query(GoalPlan).filter(
                        GoalPlan.id == effective_step.plan_id
                    ).first()
                except Exception:
                    plan = None
            plan_steps = planner.plan_steps(plan.id) if plan and getattr(plan, "id", None) else []
            has_deps = False
            dep_complexity = None
            try:
                if plan_steps:
                    has_deps = any(
                        s.depends_on and s.depends_on not in (None, "", "[]")
                        for s in plan_steps
                    )
                    dep_complexity = sum(
                        len(json.loads(s.depends_on)) if s.depends_on else 0
                        for s in plan_steps
                    )
                else:
                    dep_complexity = None
            except Exception:
                dep_complexity = None
            strat = infer_planning_strategy(
                plan_steps=len(plan_steps) if plan_steps else None,
                has_dependencies=bool(has_deps),
            )
            retries_val = max(0, (effective_step.attempt_count or 1) - 1)
            replans_val = (plan.version - 1) if plan and getattr(plan, "version", None) else 0
            task_state_val = getattr(evidence_task, "state", None)
            if task_state_val == "cancelled":
                planning_outcome_label = "CANCELLED"
                verified_plan: bool | None = None
            else:
                if outcome == "completed":
                    planning_outcome_label = "SUCCESS_VERIFIED"
                    verified_plan = True
                elif category == FailureCategory.VERIFICATION_FAILURE:
                    planning_outcome_label = "FAILED_VERIFICATION"
                    verified_plan = False
                elif task_state_val == "timed_out":
                    planning_outcome_label = "TIMED_OUT"
                    verified_plan = False
                else:
                    planning_outcome_label = "FAILED"
                    verified_plan = False
            fingerprint_raw = (
                f"planning_real_task|{effective_step.objective_id}|"
                f"{effective_step.plan_id}|{task_id}|"
                f"{effective_step.attempt_count}|{planning_outcome_label}"
            )
            # 64-bit truncation is the repo-wide fingerprint convention (all
            # learning stores use hexdigest()[:16]). Collision risk at the
            # bounded evidence scale (TTL-capped rows, per-attempt identity)
            # is negligible; full-digest migration would break idempotency
            # against existing fingerprints and is deliberately NOT done.
            fingerprint = hashlib.sha256(fingerprint_raw.encode()).hexdigest()[:16]
            res = PlanningOutcomeStore(planner.db).record(
                planning_strategy=strat,
                task_class="goal",
                goal_id=effective_step.objective_id,
                plan_id=effective_step.plan_id,
                initial_plan_steps=len(plan_steps) if plan_steps else None,
                final_plan_steps=len(plan_steps) if plan_steps else None,
                retries=retries_val,
                replans=replans_val,
                verified_success=verified_plan,
                outcome=planning_outcome_label,
                evidence_refs=[
                    f"goal:{effective_step.objective_id}",
                    f"plan:{effective_step.plan_id}",
                    f"task:{task_id}",
                    f"plan_step:{effective_step.id}",
                ],
                evidence_fingerprint=fingerprint,
                provenance="REAL_EXECUTION",
                dependency_complexity=dep_complexity,
            )
            from backend.learning.capture import report_capture_result

            report_capture_result(
                "planning_real_outcome", res,
                task_id=task_id,
                step_id=effective_step.id,
                goal_id=effective_step.objective_id,
                outcome=planning_outcome_label,
            )
        except Exception as exc:
            from backend.learning.capture import log_capture_failure

            log_capture_failure(
                "planning_real_outcome", exc,
                task_id=task_id, step_id=step.id, goal_id=step.objective_id,
            )

    @staticmethod
    def _failure_message(evidence_task, precondition_error: Optional[str]) -> str:
        if evidence_task is None:
            return "evidence task missing"
        if evidence_task.state != "completed":
            return (
                evidence_task.terminal_reason
                or f"evidence task is '{evidence_task.state}', not completed"
            )
        return f"runtime evidence unacceptable ({precondition_error})"

    def _handle_failure(
        self, step, reason: str, task_id: str, category: FailureCategory
    ) -> tuple:
        """Fail the step deterministically; retry scheduling is separate and
        explicit (see ``schedule_retry_if_allowed``) so plain outcome
        application never mutates retry state implicitly."""
        planner = self.planner
        planner.fail_step(step.id, reason=reason, task_id=task_id)
        return "failed", category

    def schedule_retry_if_allowed(self, step_id: int) -> Optional[Dict[str, Any]]:
        """Deterministic bounded retry decision for a FAILED step.

        - allowed only for RETRYABLE_CATEGORIES while
          ``attempt_count < max_attempts``;
        - audited via ``plan_step.attempt.retry``;
        - on exhaustion emits ``plan_step.attempt.exhausted`` ONCE (tied to the
          one-time active→paused goal transition) and pauses the goal for
          owner review/replanning.
        Idempotent: READY steps and non-eligible categories/states are no-ops.
        """
        from backend.core.config import settings
        from backend.models.database import Task
        from backend.runtime.events import DbEventLedger

        planner = self.planner
        step = planner.get_step(step_id)
        if step is None or step.plan_id is None or step.status != "failed":
            return None

        # Re-derive the category deterministically from persisted evidence.
        task_id = step.evidence_task_id
        task = planner.db.query(Task).filter_by(id=task_id).first() if task_id else None
        error_fn = _default_completion_precondition(planner.db, task_id)
        precondition_error = (
            error_fn(task_id) if task is not None and task.state == "completed" else None
        )
        category = categorize_failure(
            getattr(task, "state", None),
            getattr(task, "terminal_reason", None),
            precondition_error,
            machine_code=failure_code_for(planner.db, task_id) if task_id else None,
        )

        scope = f"goal:{step.objective_id}"
        ledger = DbEventLedger(planner.db)

        if self.retry_policy.can_retry(step.attempt_count or 0, category):
            step.status = "ready"
            step.updated_at = datetime.utcnow()
            ledger.append_event(scope, "plan_step.attempt.retry", {
                "step_id": step.id,
                "step_key": step.step_key,
                "category": category.value,
                "attempt_count": step.attempt_count,
                "max_attempts": self.retry_policy.max_attempts,
                "reason": (step.failure_reason or "")[:300],
            })
            planner.db.commit()
            return {
                "step_id": step.id, "scheduled": True,
                "attempt_count": step.attempt_count,
                "max_attempts": self.retry_policy.max_attempts,
                "category": category.value,
            }

        # Exhausted: audit once by tying the event to pausing an ACTIVE goal.
        from backend.services.goal_manager import GoalManager

        manager = GoalManager(planner.db)
        goal = manager.get_goal(step.objective_id)
        if goal is not None and goal.status == "active":
            ledger.append_event(scope, "plan_step.attempt.exhausted", {
                "step_id": step.id,
                "step_key": step.step_key,
                "category": category.value,
                "attempt_count": step.attempt_count,
                "max_attempts": self.retry_policy.max_attempts,
                "reason": (step.failure_reason or "")[:300],
            })
            try:
                manager.transition_goal(
                    step.objective_id,
                    "paused",
                    event_data={
                        "reason": "step_retry_exhausted",
                        "step_key": step.step_key,
                        "category": category.value,
                    },
                    note=f"Step '{step.step_key}' exhausted "
                         f"{self.retry_policy.max_attempts} attempts.",
                )
            except Exception as exc:  # pragma: no cover - best-effort projection
                logger.warning("Could not pause goal after retry exhaustion: %s", exc)
        return {
            "step_id": step.id, "scheduled": False,
            "attempt_count": step.attempt_count,
            "max_attempts": self.retry_policy.max_attempts,
            "category": category.value,
        }

    # ------------------------------------------------------------------ #
    # Recovery (Phase 3)                                                   #
    # ------------------------------------------------------------------ #

    def recover_step(self, step_id: int) -> Dict[str, Any]:
        """Deterministically resolve one RUNNING plan step after a crash.

        Rules (see ):
          - evidence task terminal-completed + clean verifier → COMPLETED
          - evidence task failed                              → FAILED (+retry policy)
          - evidence task interrupted/cancelled               → READY (recoverable;
                                                                attempt NOT re-counted)
          - evidence task missing                             → PENDING + event
          - evidence task still RUNNING                       → deferred (stale-lease
                                                                takeover path owns it);
                                                                step stays RUNNING
        Idempotent: transitions fire only from RUNNING; repeated calls with no
        state change append nothing.
        """
        planner = self.planner
        step = planner.get_step(step_id)
        if step is None or step.plan_id is None:
            return {"step_id": step_id, "resolved": False, "outcome": "not_plan_backed"}
        if step.status != "running":
            return {"step_id": step_id, "resolved": False, "outcome": f"not_running({step.status})"}

        scope = f"goal:{step.objective_id}"
        ledger = planner.ledger
        task_id = step.evidence_task_id

        from backend.models.database import Task

        task = planner.db.query(Task).filter_by(id=task_id).first() if task_id else None

        if task is None:
            # Missing task: never forge success/failure — safe recoverable state.
            step.status = "pending"
            step.updated_at = datetime.utcnow()
            ledger.append_event(scope, "plan_step.recovery.resolved", {
                "step_id": step.id, "step_key": step.step_key,
                "outcome": "missing_task", "task_id": task_id,
            })
            planner.db.commit()
            return {"step_id": step.id, "resolved": True, "outcome": "missing_task"}

        error_fn = _default_completion_precondition(planner.db, task_id)
        precondition_error = error_fn(task_id) if task.state == "completed" else None

        if task.state == "completed" and precondition_error is None:
            planner.complete_step_from_evidence(
                step.id, task_id, completion_precondition_error_fn=error_fn,
            )
            # Phase 12.5 recovery REAL capture (observational, dedup via fingerprint)
            try:
                import hashlib as _hl2, json as _js2
                from backend.learning.capture import infer_planning_strategy as _inf2
                from backend.learning.planning_store import PlanningOutcomeStore as _POS2
                from backend.models.database import GoalPlan as _GPR
                fresh2 = planner.get_step(step.id)
                eff2 = fresh2 or step
                plan2 = planner.active_plan(eff2.objective_id)
                if plan2 is None:
                    try:
                        plan2 = planner.db.query(_GPR).filter(_GPR.id == eff2.plan_id).first()
                    except Exception:
                        plan2 = None
                psteps2 = planner.plan_steps(plan2.id) if plan2 and getattr(plan2, "id", None) else []
                has_deps2 = any(s.depends_on and s.depends_on not in (None, "", "[]") for s in psteps2) if psteps2 else False
                try:
                    dep2 = sum(len(_js2.loads(s.depends_on)) if s.depends_on else 0 for s in psteps2) if psteps2 else None
                except Exception:
                    dep2 = None
                strat2 = _inf2(plan_steps=len(psteps2) if psteps2 else None, has_dependencies=bool(has_deps2))
                retries2 = max(0, (eff2.attempt_count or 1) - 1)
                replans2 = (plan2.version - 1) if plan2 and getattr(plan2, "version", None) else 0
                fp2 = _hl2.sha256(f"planning_real_task|{eff2.objective_id}|{eff2.plan_id}|{task_id}|{eff2.attempt_count}|SUCCESS_VERIFIED".encode()).hexdigest()[:16]
                res2 = _POS2(planner.db).record(
                    planning_strategy=strat2, task_class="goal", goal_id=eff2.objective_id, plan_id=eff2.plan_id,
                    initial_plan_steps=len(psteps2) if psteps2 else None, final_plan_steps=len(psteps2) if psteps2 else None,
                    retries=retries2, replans=replans2, verified_success=True, outcome="SUCCESS_VERIFIED",
                    evidence_refs=[f"goal:{eff2.objective_id}", f"plan:{eff2.plan_id}", f"task:{task_id}", f"plan_step:{eff2.id}"],
                    evidence_fingerprint=fp2, provenance="REAL_EXECUTION", dependency_complexity=dep2,
                )
                from backend.learning.capture import report_capture_result as _rcr2
                _rcr2("planning_real_recovery", res2, task_id=task_id, step_id=eff2.id)
            except Exception as exc:
                from backend.learning.capture import log_capture_failure
                log_capture_failure("planning_real_recovery", exc, task_id=task_id, step_id=step.id)
            return {"step_id": step.id, "resolved": True, "outcome": "completed"}

        if task.state == "completed" and precondition_error is not None:
            # Terminal-completed but evidence unacceptable: an honest failure,
            # never a forged completion.
            planner.fail_step(
                step.id,
                reason=f"recovery: {precondition_error}",
                task_id=task_id,
            )
            try:
                import hashlib as _hl2b, json as _js2b
                from backend.learning.capture import infer_planning_strategy as _inf2b
                from backend.learning.planning_store import PlanningOutcomeStore as _POS2b
                from backend.models.database import GoalPlan as _GPRb
                fresh2b = planner.get_step(step.id)
                eff2b = fresh2b or step
                plan2b = planner.active_plan(eff2b.objective_id)
                if plan2b is None:
                    try:
                        plan2b = planner.db.query(_GPRb).filter(_GPRb.id == eff2b.plan_id).first()
                    except Exception:
                        plan2b = None
                psteps2b = planner.plan_steps(plan2b.id) if plan2b and getattr(plan2b, "id", None) else []
                has_deps2b = any(s.depends_on and s.depends_on not in (None, "", "[]") for s in psteps2b) if psteps2b else False
                try:
                    dep2b = sum(len(_js2b.loads(s.depends_on)) if s.depends_on else 0 for s in psteps2b) if psteps2b else None
                except Exception:
                    dep2b = None
                strat2b = _inf2b(plan_steps=len(psteps2b) if psteps2b else None, has_dependencies=bool(has_deps2b))
                retries2b = max(0, (eff2b.attempt_count or 1) - 1)
                replans2b = (plan2b.version - 1) if plan2b and getattr(plan2b, "version", None) else 0
                fp2b = _hl2b.sha256(f"planning_real_task|{eff2b.objective_id}|{eff2b.plan_id}|{task_id}|{eff2b.attempt_count}|FAILED_VERIFICATION".encode()).hexdigest()[:16]
                res2b = _POS2b(planner.db).record(
                    planning_strategy=strat2b, task_class="goal", goal_id=eff2b.objective_id, plan_id=eff2b.plan_id,
                    initial_plan_steps=len(psteps2b) if psteps2b else None, final_plan_steps=len(psteps2b) if psteps2b else None,
                    retries=retries2b, replans=replans2b, verified_success=False, outcome="FAILED_VERIFICATION",
                    evidence_refs=[f"goal:{eff2b.objective_id}", f"plan:{eff2b.plan_id}", f"task:{task_id}", f"plan_step:{eff2b.id}"],
                    evidence_fingerprint=fp2b, provenance="REAL_EXECUTION", dependency_complexity=dep2b,
                )
                from backend.learning.capture import report_capture_result as _rcr2b
                _rcr2b("planning_real_recovery_failed_verification", res2b, task_id=task_id, step_id=eff2b.id)
            except Exception as exc:
                from backend.learning.capture import log_capture_failure
                log_capture_failure("planning_real_recovery_failed_verification", exc, task_id=task_id, step_id=step.id)
            return {"step_id": step.id, "resolved": True, "outcome": "failed"}

        if task.state in ("failed", "cancelled", "interrupted"):
            if task.state == "failed":
                planner.fail_step(
                    step.id,
                    reason=(task.terminal_reason or "failed"),
                    task_id=task_id,
                )
                # Phase 12.5 recovery REAL capture for failed task (dedup)
                try:
                    import hashlib as _hl3, json as _js3
                    from backend.learning.capture import infer_planning_strategy as _inf3
                    from backend.learning.planning_store import PlanningOutcomeStore as _POS3
                    from backend.models.database import GoalPlan as _GPR3
                    fresh3 = planner.get_step(step.id)
                    eff3 = fresh3 or step
                    plan3 = planner.active_plan(eff3.objective_id)
                    if plan3 is None:
                        try:
                            plan3 = planner.db.query(_GPR3).filter(_GPR3.id == eff3.plan_id).first()
                        except Exception:
                            plan3 = None
                    psteps3 = planner.plan_steps(plan3.id) if plan3 and getattr(plan3, "id", None) else []
                    has_deps3 = any(s.depends_on and s.depends_on not in (None, "", "[]") for s in psteps3) if psteps3 else False
                    try:
                        dep3 = sum(len(_js3.loads(s.depends_on)) if s.depends_on else 0 for s in psteps3) if psteps3 else None
                    except Exception:
                        dep3 = None
                    strat3 = _inf3(plan_steps=len(psteps3) if psteps3 else None, has_dependencies=bool(has_deps3))
                    retries3 = max(0, (eff3.attempt_count or 1) - 1)
                    replans3 = (plan3.version - 1) if plan3 and getattr(plan3, "version", None) else 0
                    fp3 = _hl3.sha256(f"planning_real_task|{eff3.objective_id}|{eff3.plan_id}|{task_id}|{eff3.attempt_count}|FAILED".encode()).hexdigest()[:16]
                    res3 = _POS3(planner.db).record(
                        planning_strategy=strat3, task_class="goal", goal_id=eff3.objective_id, plan_id=eff3.plan_id,
                        initial_plan_steps=len(psteps3) if psteps3 else None, final_plan_steps=len(psteps3) if psteps3 else None,
                        retries=retries3, replans=replans3, verified_success=False, outcome="FAILED",
                        evidence_refs=[f"goal:{eff3.objective_id}", f"plan:{eff3.plan_id}", f"task:{task_id}", f"plan_step:{eff3.id}"],
                        evidence_fingerprint=fp3, provenance="REAL_EXECUTION", dependency_complexity=dep3,
                    )
                    from backend.learning.capture import report_capture_result as _rcr3
                    _rcr3("planning_real_recovery_failed", res3, task_id=task_id, step_id=eff3.id)
                except Exception as exc:
                    from backend.learning.capture import log_capture_failure
                    log_capture_failure("planning_real_recovery_failed", exc, task_id=task_id, step_id=step.id)
                outcome = "failed"
            else:
                # Interrupted / cancelled attempts are recoverable, not
                # failures: the step returns to READY without consuming an
                # extra attempt. Retry/execution decisions belong to the
                # explicit session/advance path.
                step.status = "ready"
                step.updated_at = datetime.utcnow()
                ledger.append_event(scope, "plan_step.recovery.resolved", {
                    "step_id": step.id, "step_key": step.step_key,
                    "outcome": f"recoverable_{task.state}", "task_id": task_id,
                })
                planner.db.commit()
                outcome = f"recoverable_{task.state}"
            return {"step_id": step.id, "resolved": True, "outcome": outcome}

        if task.state in ("running", "created"):
            # Kernel has not produced a terminal outcome (or never started):
            # ownership resolution belongs to the lease takeover / resume
            # path. Defer; the step stays RUNNING.
            ledger.append_event(scope, "plan_step.recovery.detected", {
                "step_id": step.id, "step_key": step.step_key,
                "task_id": task_id, "deferred": f"task_state_{task.state}",
            })
            planner.db.commit()
            return {"step_id": step.id, "resolved": False,
                    "outcome": f"deferred_{task.state}"}

        return {"step_id": step.id, "resolved": False, "outcome": f"unexpected_task_state({task.state})"}

    def recover_all(self) -> List[Dict[str, Any]]:
        """Idempotent startup recovery over every RUNNING plan-backed step."""
        from backend.models.database import PlanStep

        running_steps = (
            self.planner.db.query(PlanStep)
            .filter(PlanStep.plan_id.isnot(None))
            .filter(PlanStep.status == "running")
            .all()
        )
        results = []
        for step in running_steps:
            try:
                results.append(self.recover_step(step.id))
            except Exception as exc:  # pragma: no cover - best-effort recovery
                logger.exception("Recovery failed for step %s", step.id)
                results.append({"step_id": step.id, "resolved": False,
                                "outcome": "recovery_failure", "error": str(exc)})
        resolved = sum(1 for r in results if r.get("resolved"))
        if results:
            logger.info("Startup recovery inspected %d running step(s); resolved %d",
                        len(results), resolved)
        return results

    # ------------------------------------------------------------------ #
    # Runnable goals (scheduler boundary query — never executes)           #
    # ------------------------------------------------------------------ #

    def list_runnable_goals(self) -> List[Dict[str, Any]]:
        """Eligible goals in serving order (delegates to the Phase 4
        RunnableGoalSelector so HTTP and the agent share ONE implementation).
        Pure query; executes nothing."""
        from backend.services.goal_selector import RunnableGoalSelector

        return RunnableGoalSelector(self.planner.db).list_runnable_goals()

    async def execute_session(self, goal_id: str, run_task_fn=None) -> Dict[str, Any]:
        """Explicitly initiated goal execution with automatic continuation.

        Runs eligible steps back-to-back within THIS call while the caller's
        lease/session is valid, bounded by ``max_steps_per_goal_execution``
        and the existing budgets/lease. Stops on: no eligible steps, retry
        exhaustion (goal paused), budget bound, or lost lease. This is NOT a
        scheduler: nothing runs outside this awaited call.
        """
        from backend.api.schemas import TaskState
        from backend.core.config import settings
        from backend.services.task_execution_loop import run_task as default_run

        run_fn = run_task_fn or default_run
        executed = []
        stopped_reason = None

        for _ in range(settings.max_steps_per_goal_execution):
            if not await self.state_store.acquire_lease():
                stopped_reason = "lease_unavailable"
                break
            try:
                started = await self.start_next_step(goal_id)
                if started is None:
                    # Before giving up, apply the bounded retry policy to any
                    # FAILED step (audited); a scheduled retry becomes READY
                    # and the next loop iteration executes it.
                    retry_info = self._retry_one_failed_step(goal_id)
                    if retry_info and retry_info.get("scheduled"):
                        await self.state_store.release_lease()
                        continue
                    stopped_reason = stopped_reason or (
                        "step_retry_exhausted"
                        if retry_info else "no_eligible_step"
                    )
                    await self.state_store.release_lease()
                    break
                task = started["task"]
                await self.state_store.transition_state(
                    task_id=task.id,
                    new_state=TaskState.RUNNING,
                    event_type="task.execution_started",
                    event_data={"started_at": datetime.utcnow().isoformat(),
                                "goal_step_key": started["step"].step_key},
                )
            finally:
                await self.state_store.release_lease()

            await run_fn(None, task.id, None)  # kernel worker (own session+lease)
            outcome = await self.apply_task_outcome(task.id)
            # PostTaskPipeline.apply_plan_outcome may already have applied the
            # terminal outcome inside the kernel; apply_task_outcome then
            # short-circuits with {"updated": False} and no "outcome" key.
            outcome_label = (
                outcome.get("outcome") if isinstance(outcome, dict) else None
            ) or ("already_applied" if outcome else "unknown")
            executed.append({
                "task_id": task.id,
                "step_key": started["step"].step_key,
                "outcome": outcome_label,
            })
            if outcome_label == "failed":
                # Let the next iteration's retry check decide continuation;
                # if retries are exhausted the goal is paused and we stop.
                continue

        else:
            stopped_reason = "step_budget_reached"

        return {"executed": executed, "stopped_reason": stopped_reason}

    def _retry_one_failed_step(self, goal_id: str) -> Optional[Dict[str, Any]]:
        """Retry-policy decision for the goal's most recent FAILED step."""
        from backend.models.database import PlanStep

        failed = (
            self.planner.db.query(PlanStep)
            .filter(PlanStep.objective_id == goal_id)
            .filter(PlanStep.plan_id.isnot(None))
            .filter(PlanStep.status == "failed")
            .order_by(PlanStep.updated_at.desc())
            .first()
        )
        if failed is None:
            return None
        return self.schedule_retry_if_allowed(failed.id)

    # ------------------------------------------------------------------ #
    # Phase 4: bounded background cycle across MULTIPLE goals (fairness)   #
    # ------------------------------------------------------------------ #

    async def run_background_cycle(self, selector, max_steps: int,
                                   run_task_fn=None) -> Dict[str, Any]:
        """One bounded background session over eligible goals.

        Per iteration: pick a goal deterministically (selector fairness),
        execute ONE step through the kernel, apply the verified outcome,
        mark the goal served. Stops on step cap, no runnable goals, lost
        lease, or retry exhaustion. Never loops on its own.
        """
        from backend.api.schemas import TaskState

        if run_task_fn is None:
            from backend.services.task_execution_loop import run_task as run_task_fn

        executed = []
        stop_reason = None

        for _ in range(max(1, int(max_steps))):
            if not await self.state_store.acquire_lease():
                stop_reason = "lease_unavailable"
                break
            try:
                # Background selection respects persisted failure backoff;
                # explicit owner paths call with respect_backoff=False.
                chosen = selector.pick_goal(respect_backoff=True)
                if chosen is None:
                    stop_reason = "no_runnable_goal"
                    await self.state_store.release_lease()
                    break
                started = await self.start_next_step(chosen["goal_id"])
                if started is None:
                    # Became ineligible between pick and start (race with
                    # pause/cancel). Skip this slot rather than spin.
                    stop_reason = "goal_became_ineligible"
                    await self.state_store.release_lease()
                    break
                task = started["task"]
                goal_id = chosen["goal_id"]
                await self.state_store.transition_state(
                    task_id=task.id,
                    new_state=TaskState.RUNNING,
                    event_type="task.execution_started",
                    event_data={
                        "started_at": datetime.utcnow().isoformat(),
                        "goal_step_key": started["step"].step_key,
                    },
                )
            finally:
                await self.state_store.release_lease()

            await run_task_fn(None, task.id, None)
            outcome = await self.apply_task_outcome(task.id)
            # Same short-circuit tolerance as execute_session: the kernel's
            # PostTaskPipeline may have applied the outcome already.
            outcome_str = (
                outcome.get("outcome") if isinstance(outcome, dict) else None
            ) or ("already_applied" if outcome else "unknown")

            # SERVICE ACCOUNTING (Phase 4.5): "served" is recorded only when
            # an execution attempt actually STARTED a step — never merely for
            # selection. Failure accounting feeds the persisted backoff.
            selector.mark_served(goal_id)
            from backend.services.goal_manager import GoalManager as _GM

            _GM(self.planner.db).record_service_outcome(
                goal_id, step_failed=(outcome_str not in ("completed", "already_applied"))
            )

            executed.append({
                "goal_id": goal_id,
                "task_id": task.id,
                "step_key": started["step"].step_key,
                "outcome": outcome_str,
            })

            if outcome_str == "failed":
                # Deterministic retry decision; exhaustion already paused the
                # goal — stop this session so an owner can replan.
                retry_info = self.schedule_retry_if_allowed(started["step"].id)
                if not (retry_info and retry_info.get("scheduled")):
                    stop_reason = "step_retry_exhausted_or_permanent_failure"
                    break
                # Retry scheduled: the goal enters backoff-aware selection on
                # the next iteration; if it is the only goal and now in
                # backoff, the next pick returns None and we stop cleanly.

        if stop_reason is None:
            stop_reason = "step_budget_reached"

        return {"executed": executed, "stopped_reason": stop_reason}


def _default_completion_precondition(db, task_id: str):
    """Build the kernel's completion gate bound to this task's ledger."""
    from backend.runtime.events import DbEventLedger
    from backend.services.execution_components import CompletionVerifier

    ledger = DbEventLedger(db)

    def _error(tid: str) -> Optional[str]:
        return CompletionVerifier(db, ledger.event_history).precondition_error(tid)

    return _error
