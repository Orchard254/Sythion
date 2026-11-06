"""Planner — owns plan creation, validation, step scheduling, and replanning.

Phase 2 planning layer. The Planner determines WHAT should happen; it never
executes anything. Tool execution, policy, verification, budgets and task
state remain owned by the Runtime Kernel (README.md).

Model boundary: candidate plans may come from an LLM through the Phase 1
``ModelClient`` interface only (see ``request_model_plan``). Raw model output
is never persisted: it must pass schema validation → dependency validation
→ persistence as a new immutable plan version.
"""

from __future__ import annotations

import json
from typing import Any, Dict, List, Optional

from pydantic import BaseModel, Field, ValidationError as PydanticValidationError
from sqlalchemy.orm import Session

from backend.models.database import GoalPlan, Objective, PlanStep
from backend.runtime.events import DbEventLedger
from backend.services.goal_manager import GoalManager


class PlanValidationError(ValueError):
    """A candidate plan failed structural/dependency validation.

    ``errors`` lists every deterministic problem found; nothing is persisted
    when validation fails.
    """

    def __init__(self, errors: List[str]):
        super().__init__("; ".join(errors) if errors else "invalid plan")
        self.errors = errors


# --- Step state machine --------------------------------------------------------

STEP_OPEN_STATES = {"pending", "ready", "running", "in_progress", "blocked"}
STEP_TERMINAL_STATES = {"completed", "failed", "cancelled", "skipped"}

STEP_TRANSITIONS: Dict[str, set] = {
    "pending": {"ready", "blocked", "cancelled", "running"},
    "ready": {"running", "blocked", "pending", "cancelled"},
    "running": {"completed", "failed", "cancelled"},
    "in_progress": {"completed", "failed", "cancelled"},  # legacy alias of running
    "blocked": {"ready", "pending", "cancelled"},
    "completed": set(),
    "failed": {"ready"},  # explicit retry resets the same step
    "cancelled": set(),
    "skipped": set(),
}


def can_transition_step(current: str, requested: str) -> bool:
    return requested == current or requested in STEP_TRANSITIONS.get(current, set())


class CandidateStepModel(BaseModel):
    """Schema for one step inside a candidate structured plan."""

    key: str = Field(
        pattern=r"^[a-z][a-z0-9_]{0,63}$",
        description="Stable snake_case identity within the plan",
    )
    description: str = Field(min_length=3, max_length=500)
    priority: int = Field(default=3, ge=1, le=5)  # 1 = highest
    depends_on: List[str] = Field(default_factory=list)


def parse_candidate_plan(raw: Any) -> List[CandidateStepModel]:
    """Validate a raw candidate plan payload against the strict schema.

    Accepts either a list of step dicts or a dict with a ``steps`` key.
    Raises :class:`PlanValidationError` on any schema violation — malformed
    model output never becomes plan state.
    """
    if isinstance(raw, str):
        try:
            raw = json.loads(raw)
        except json.JSONDecodeError as exc:
            raise PlanValidationError([f"candidate plan is not valid JSON: {exc}"])
    if isinstance(raw, dict):
        raw = raw.get("steps")
    if not isinstance(raw, list) or not raw:
        raise PlanValidationError(["candidate plan must be a non-empty list of steps"])
    try:
        return [CandidateStepModel.model_validate(item) for item in raw]
    except PydanticValidationError as exc:
        errors = [
            f"step {idx}: {err['msg']} (field {err.get('loc', ('?',))[-1]})"
            for idx, err in enumerate(exc.errors(), start=1)
        ]
        raise PlanValidationError(errors)


def validate_dependency_graph(steps: List[CandidateStepModel]) -> None:
    """Deterministic dependency validation.

    Rejects: duplicate keys, self dependencies, references to nonexistent
    steps, and cycles. Runs BEFORE any persistence.
    """
    errors: List[str] = []
    keys: List[str] = [s.key for s in steps]
    seen: set = set()
    duplicates: set = set()
    for key in keys:
        if key in seen:
            duplicates.add(key)
        seen.add(key)
    for key in sorted(duplicates):
        errors.append(f"duplicate step key: '{key}'")

    key_set = set(keys)
    for step in steps:
        for dep in step.depends_on:
            if dep == step.key:
                errors.append(f"step '{step.key}' depends on itself")
            elif dep not in key_set:
                errors.append(
                    f"step '{step.key}' depends on unknown step '{dep}'"
                )

    # Cycle detection (iterative DFS with colors).
    WHITE, GRAY, BLACK = 0, 1, 2
    color = {key: WHITE for key in keys}
    for start in keys:
        if color[start] != WHITE:
            continue
        stack = [(start, iter(steps[keys.index(start)].depends_on))]
        color[start] = GRAY
        while stack:
            node, deps_iter = stack[-1]
            advanced = False
            for dep in deps_iter:
                if dep in color and color[dep] == GRAY:
                    errors.append(f"dependency cycle detected involving '{dep}'")
                    raise PlanValidationError(_dedupe(errors))
                if dep in color and color[dep] == WHITE:
                    color[dep] = GRAY
                    idx = keys.index(dep)
                    stack.append((dep, iter(steps[idx].depends_on)))
                    advanced = True
                    break
            if not advanced:
                color[node] = BLACK
                stack.pop()

    if errors:
        raise PlanValidationError(_dedupe(errors))


def _dedupe(items: List[str]) -> List[str]:
    seen: set = set()
    out = []
    for item in items:
        if item not in seen:
            seen.add(item)
            out.append(item)
    return out


class Planner:
    """Owns plans, steps, readiness, selection, and replanning."""

    def __init__(self, session: Session):
        self.db = session
        self.ledger = DbEventLedger(session)

    # --- helpers -----------------------------------------------------------

    def _goal_scope(self, goal_id: str) -> str:
        return f"goal:{goal_id}"

    def active_plan(self, goal_id: str) -> Optional[GoalPlan]:
        return (
            self.db.query(GoalPlan)
            .filter_by(goal_id=goal_id, status="active")
            .order_by(GoalPlan.version.desc())
            .first()
        )

    def plan_steps(self, plan_id: int) -> List[PlanStep]:
        return (
            self.db.query(PlanStep)
            .filter_by(plan_id=plan_id)
            .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
            .all()
        )

    def goal_steps(self, goal_id: str) -> List[PlanStep]:
        return (
            self.db.query(PlanStep)
            .filter(PlanStep.objective_id == goal_id)
            .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
            .all()
        )

    def get_step(self, step_id: int) -> Optional[PlanStep]:
        return self.db.query(PlanStep).filter_by(id=step_id).first()

    # --- plan creation -------------------------------------------------------

    def create_plan(
        self,
        goal_id: str,
        candidates: List[CandidateStepModel],
        source: str = "user",
        reason: Optional[str] = None,
    ) -> GoalPlan:
        """Validate then persist a NEW plan version for the goal.

        Validation happens strictly before persistence; a failed validation
        leaves no partial state behind.
        """
        goal = self.db.query(Objective).filter_by(id=goal_id).first()
        if goal is None:
            raise ValueError(f"Goal {goal_id} not found")

        validate_dependency_graph(candidates)  # raises PlanValidationError

        previous = self.active_plan(goal_id)
        version = (previous.version + 1) if previous else 1
        now = __import__("datetime").datetime.utcnow()

        plan = GoalPlan(
            goal_id=goal_id,
            version=version,
            status="active",
            source=source,
            reason=reason,
            created_at=now,
        )
        self.db.add(plan)
        self.db.flush()

        for idx, cand in enumerate(candidates, start=1):
            deps_completed = not cand.depends_on  # nothing to unlock initially
            step = PlanStep(
                objective_id=goal_id,
                plan_id=plan.id,
                step_key=cand.key,
                step_order=idx,
                description=cand.description.strip(),
                status="ready" if deps_completed else "blocked",
                priority=cand.priority,
                depends_on=json.dumps(cand.depends_on),
                attempt_count=0,
                created_at=now,
                updated_at=now,
            )
            self.db.add(step)

        if previous is not None:
            previous.status = "superseded"

        self.ledger.append_event(self._goal_scope(goal_id), "plan.created", {
            "plan_id": plan.id,
            "version": version,
            "source": source,
            "steps": len(candidates),
            "superseded_plan_id": previous.id if previous else None,
            "reason": reason,
        })
        self.db.commit()
        self.db.refresh(plan)
        self.refresh_readiness(plan.id)
        # Phase 11.5: strategy-level replan evidence (§4) — distinguished from task retry
        try:
            if previous is not None:
                from backend.learning.capture import record_replan_outcome

                record_replan_outcome(
                    self.db,
                    goal_id=goal_id,
                    original_plan_id=previous.id,
                    new_plan_id=plan.id,
                    reason=reason or "replan",
                    evidence_refs=[f"goal:{goal_id}", f"plan:{previous.id}", f"plan:{plan.id}"],
                )
        except Exception as exc:
            from backend.learning.capture import log_capture_failure
            log_capture_failure("replan_outcome", exc, goal_id=goal_id, plan_id=plan.id)
        # Phase 12/12.5 audit: planning-level structured replan evidence (§5)
        # Audit fix (defect 2): plan creation alone is not execution evidence.
        # REAL_EXECUTION is only assigned after verified execution; plan-creation
        # replan records must be LEGACY so they never count toward REAL promotion.
        try:
            if previous is not None:
                from backend.learning.capture import record_replan_planning_outcome

                res = record_replan_planning_outcome(
                    self.db,
                    goal_id=goal_id,
                    original_plan_id=previous.id,
                    new_plan_id=plan.id,
                    reason=reason or "replan",
                    trigger=reason,
                    retry_attempts=0,
                    provenance="LEGACY",
                )
                from backend.learning.capture import report_capture_result
                report_capture_result("replan_planning_outcome", res, goal_id=goal_id, plan_id=plan.id)
        except Exception as exc:
            from backend.learning.capture import log_capture_failure
            log_capture_failure("replan_planning_outcome", exc, goal_id=goal_id, plan_id=plan.id)
        return plan

    # --- readiness & selection -------------------------------------------------

    def refresh_readiness(self, plan_id: int) -> int:
        """Unlock BLOCKED steps whose dependencies are all COMPLETED.

        Deterministic: B becomes ready exactly when A completed. Returns the
        number of newly ready steps.
        """
        steps = self.plan_steps(plan_id)
        status_by_key = {s.step_key: s.status for s in steps}
        unlocked = 0
        for step in steps:
            if step.status != "blocked":
                continue
            deps = json.loads(step.depends_on) if step.depends_on else []
            if all(status_by_key.get(d) == "completed" for d in deps):
                step.status = "ready"
                step.updated_at = __import__("datetime").datetime.utcnow()
                self.ledger.append_event(
                    self._goal_scope(step.objective_id),
                    "plan_step.ready",
                    {"step_id": step.id, "step_key": step.step_key},
                )
                unlocked += 1
        if unlocked:
            self.db.commit()
        return unlocked

    def select_next_step(self, goal_id: str) -> Optional[PlanStep]:
        """Deterministically choose the next eligible step, or None.

        Ordering policy (documented + tested):
          1. dependency eligibility (READY only; BLOCKED steps are unlocked
             first when their dependencies completed);
          2. priority ascending (1 = most important);
          3. stable tie-break by step_order then id.
        The LLM cannot influence this ordering beyond what was validated into
        the persisted plan.
        """
        plan = self.active_plan(goal_id)
        if plan is None:
            return None
        self.refresh_readiness(plan.id)
        candidates = (
            self.db.query(PlanStep)
            .filter(
                PlanStep.plan_id == plan.id,
                PlanStep.status.in_(("ready",)),
            )
            .order_by(PlanStep.priority.asc(), PlanStep.step_order.asc(), PlanStep.id.asc())
            .all()
        )
        return candidates[0] if candidates else None

    # --- execution-state updates (evidence-gated) ------------------------------

    def mark_step_started(self, step_id: int, task_id: str) -> PlanStep:
        """Transition READY (or PENDING legacy) → RUNNING bound to a task."""
        from datetime import datetime

        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if not can_transition_step(step.status, "running"):
            raise ValueError(
                f"Invalid step transition: {step.status} -> running "
                f"(step_key={step.step_key})"
            )
        step.status = "running"
        step.linked_task_id = task_id
        step.evidence_task_id = task_id
        step.attempt_count = (step.attempt_count or 0) + 1
        step.updated_at = datetime.utcnow()
        self.ledger.append_event(
            self._goal_scope(step.objective_id),
            "plan_step.started",
            {"step_id": step.id, "step_key": step.step_key, "task_id": task_id,
             "attempt": step.attempt_count},
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def complete_step_from_evidence(
        self,
        step_id: int,
        task_id: str,
        completion_precondition_error_fn=None,
    ) -> PlanStep:
        """Complete a step ONLY from acceptable runtime evidence.

        Required evidence: the associated task exists in terminal state
        'completed' AND the runtime CompletionVerifier finds no precondition
        error (no uncertain actions, no unverified mutating failure).
        A model claim can never substitute for this check.
        """
        from datetime import datetime

        from backend.api.schemas import TaskState
        from backend.models.database import Task

        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")

        task = self.db.query(Task).filter_by(id=task_id).first()
        if task is None or task.id != task_id:
            raise ValueError(f"Evidence task {task_id} not found")
        if task.state != TaskState.COMPLETED.value:
            raise ValueError(
                f"Cannot complete step {step.step_key}: evidence task is "
                f"'{task.state}', not completed"
            )
        if completion_precondition_error_fn is not None:
            error = completion_precondition_error_fn(task_id)
            if error:
                raise ValueError(
                    f"Cannot complete step {step.step_key}: runtime evidence "
                    f"unacceptable ({error})"
                )

        if not can_transition_step(step.status, "completed"):
            raise ValueError(
                f"Invalid step transition: {step.status} -> completed"
            )

        step.status = "completed"
        step.result_summary = (
            task.final_result or f"Task {task_id} completed."
        )[:2000]
        step.failure_reason = None
        step.blocker_reason = None
        step.evidence_task_id = task_id
        step.updated_at = datetime.utcnow()
        self.ledger.append_event(
            self._goal_scope(step.objective_id),
            "plan_step.completed",
            {"step_id": step.id, "step_key": step.step_key,
             "task_id": task_id, "result_summary": (step.result_summary or "")[:300]},
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    def fail_step(self, step_id: int, reason: str, task_id: Optional[str] = None) -> PlanStep:
        from datetime import datetime

        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        if not can_transition_step(step.status, "failed"):
            raise ValueError(f"Invalid step transition: {step.status} -> failed")
        step.status = "failed"
        step.failure_reason = (reason or "")[:1000]
        if task_id:
            step.evidence_task_id = task_id
        step.updated_at = datetime.utcnow()
        self.ledger.append_event(
            self._goal_scope(step.objective_id),
            "plan_step.failed",
            {"step_id": step.id, "step_key": step.step_key,
             "task_id": task_id, "failure_reason": step.failure_reason},
        )
        self.db.commit()
        self.db.refresh(step)
        return step

    # --- goal progress recalculation ---------------------------------------------

    def recompute_goal_after_step_terminal(self, goal_id: str) -> Dict[str, Any]:
        """Recalculate progress; auto-complete the goal ONLY deterministically."""
        manager = GoalManager(self.db)
        progress = manager.progress(goal_id)
        goal = manager.get_goal(goal_id)
        if goal is None or goal.status != "active":
            return progress

        steps = self.goal_steps(goal_id)
        open_states = STEP_OPEN_STATES | {"failed"}
        if any(s.status in open_states for s in steps):
            return progress
        required = [s for s in steps if s.status != "cancelled"]
        if required and all(s.status == "completed" for s in required):
            if not (goal.completion_criteria and not goal.criteria_verified):
                manager.transition_goal(
                    goal_id,
                    "completed",
                    event_data={"progress": progress},
                    note="All required steps completed with verified evidence.",
                )
        return progress

    # --- replanning -----------------------------------------------------------------

    def build_failure_context(self, step_id: int) -> Dict[str, Any]:
        """Structured failure context handed to the planner/model."""
        step = self.get_step(step_id)
        if step is None:
            raise ValueError(f"Step {step_id} not found")
        remaining = [
            {"key": s.step_key, "description": s.description, "status": s.status}
            for s in self.plan_steps(step.plan_id)
            if s.id != step_id and s.status in STEP_OPEN_STATES
        ]
        events = self.ledger.event_history(f"goal:{step.objective_id}")
        return {
            "failed_step": {
                "id": step.id,
                "key": step.step_key,
                "description": step.description,
                "attempt_count": step.attempt_count,
                "failure_reason": step.failure_reason,
            },
            "verification_result": "failed" if step.status == "failed" else step.status,
            "remaining_plan": remaining,
            "recent_events": [
                {"type": e["event_type"], "data": e["event_data"]}
                for e in events[-10:]
            ],
        }

    def replan(
        self,
        goal_id: str,
        reason: str,
        candidates: Optional[List[CandidateStepModel]] = None,
        source: str = "deterministic",
    ) -> GoalPlan:
        """Create a validated successor plan; history remains auditable.

        When no candidates are supplied, the open AND failed steps of the
        current plan are carried over unchanged (a failed step becomes a
        fresh PENDING retry candidate in the new version).
        """
        plan = self.active_plan(goal_id)
        if plan is None:
            raise ValueError(f"No active plan for goal {goal_id}")

        if candidates is None:
            carry_states = STEP_OPEN_STATES | {"failed"}
            candidates = [
                CandidateStepModel(
                    key=s.step_key,
                    description=s.description,
                    priority=s.priority,
                    depends_on=json.loads(s.depends_on) if s.depends_on else [],
                )
                for s in self.plan_steps(plan.id)
                if s.status in carry_states
            ]

        return self.create_plan(goal_id, candidates, source=source, reason=reason)

    # --- model boundary ---------------------------------------------------------------

    PLAN_PROPOSAL_SYSTEM_PROMPT = (
        "You are the planning module of Sythion. Produce a STRICT JSON plan "
        'for the given goal: {"steps": [{"key": "snake_case_key", '
        '"description": "...", "priority": 1-5, "depends_on": ["key", ...]}]}. '
        "Rules: 2-8 concrete executable steps; unique keys; dependencies must "
        "reference earlier-defined keys and form a DAG (no cycles); lower "
        "priority number = more important. Output JSON only, no prose."
    )

    def request_model_plan(
        self,
        goal: Objective,
        model_client,
        instruction: Optional[str] = None,
        max_steps: int = 8,
    ) -> List[CandidateStepModel]:
        """Ask the LLM (via ModelClient) for a CANDIDATE plan.

        The proposal is parsed and schema-validated here; malformed output
        raises PlanValidationError and is recorded by the caller. Nothing is
        persisted by this method itself.
        """
        user_message = (
            f"Goal title: {goal.title}\n"
            f"Purpose: {goal.purpose or '(none)'}\n"
            f"Completion criteria: {goal.completion_criteria or '(none)'}\n"
            f"{('Failure context / instructions: ' + instruction) if instruction else ''}\n"
            f"Produce at most {max_steps} steps as STRICT JSON."
        ).strip()
        response = asyncio_run(model_client.decide(
            messages=[
                {"role": "system", "content": self.PLAN_PROPOSAL_SYSTEM_PROMPT},
                {"role": "user", "content": user_message},
            ],
            tools=[],
            max_tokens=4000,
        ))
        content = ""
        message = response.get("message") or {}
        content = message.get("content") or ""
        if not content and response.get("choices"):
            content = (response["choices"][0].get("message") or {}).get("content", "")

        text = (content or "").strip()
        if text.startswith("```"):
            stripped = text.strip("`").strip()
            text = stripped[4:].strip() if stripped.lower().startswith("json") else stripped
        try:
            start, end = text.find("{"), text.rfind("}")
            raw = text[start:end + 1] if start != -1 and end > start else text
            data = json.loads(raw)
        except (json.JSONDecodeError, ValueError) as exc:
            raise PlanValidationError([f"model produced unparseable plan JSON: {exc}"])

        try:
            return parse_candidate_plan(data)
        except PlanValidationError as exc:
            raise PlanValidationError(
                [f"model plan rejected: {e}" for e in exc.errors]
            )


def asyncio_run(coro):
    """Small helper so the Planner stays importable without a running loop."""
    import asyncio

    try:
        loop = asyncio.get_running_loop()
    except RuntimeError:
        return asyncio.run(coro)
    # Already inside a loop (tool/route context): run in a fresh thread's loop.
    import concurrent.futures

    with concurrent.futures.ThreadPoolExecutor(max_workers=1) as pool:
        return pool.submit(asyncio.run, coro).result()
