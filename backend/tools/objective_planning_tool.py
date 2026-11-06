"""
Objective planning tools for Sythion.

Gives the model the ability to proactively create, update, and track durable
multi-step goal plans.  The underlying Objective / PlanStep records persist in
``sythion.db`` and survive restarts.

Five operations are exposed as separate BaseTool subclasses so each appears as
a distinct entry in the model's tool list with its own description and schema:

    objective_create       – Decompose a broad goal into named steps
    objective_add_step     – Append a new step to the current/named objective
    objective_complete_step – Mark the current (or named) step as done
    objective_get_plan     – Read the full current objective + step list
    objective_update_summary – Write a fresh progress note to the objective
"""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, List, Optional, Generic, TypeVar

from pydantic import BaseModel, Field

try:
    from backend.api.schemas import ToolOutput
    from backend.models.database import Objective, PlanStep, get_db_manager
    from backend.tools.executor import BaseTool
except ImportError:
    from api.schemas import ToolOutput
    from models.database import Objective, PlanStep, get_db_manager
    from tools.executor import BaseTool
from sqlalchemy.orm import sessionmaker

# ---------------------------------------------------------------------------
# Shared helpers
# ---------------------------------------------------------------------------

# PHASE 3.5: canonical open-step set (superset of the legacy trio) so step
# selection/remaining counts understand plan-backed states.
OPEN_STEP_STATUSES = {"pending", "ready", "running", "in_progress", "blocked"}
ACTIVE_OBJECTIVE_STATUSES = {"active", "paused"}


def _trim(text: str | None, limit: int = 200) -> str:
    value = (text or "").strip()
    if len(value) <= limit:
        return value
    return value[: limit - 3].rstrip() + "..."


def _active_objective(session) -> Objective | None:
    """Return the most-recently-updated active/paused objective."""
    return (
        session.query(Objective)
        .filter(Objective.status.in_(tuple(ACTIVE_OBJECTIVE_STATUSES)))
        .order_by(Objective.updated_at.desc(), Objective.created_at.desc())
        .first()
    )


def _current_open_step(session, objective_id: str) -> PlanStep | None:
    steps = (
        session.query(PlanStep)
        .filter(PlanStep.objective_id == objective_id)
        .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
        .all()
    )
    for step in steps:
        if step.status in OPEN_STEP_STATUSES:
            return step
    return None


def _next_step_order(session, objective_id: str) -> int:
    latest = (
        session.query(PlanStep)
        .filter(PlanStep.objective_id == objective_id)
        .order_by(PlanStep.step_order.desc(), PlanStep.id.desc())
        .first()
    )
    return (latest.step_order + 1) if latest else 1


def _plan_step_evidence_error(session, step: PlanStep) -> dict[str, str] | None:
    """Deterministic evidence gate for plan-backed steps.

    Returns an error dict when runtime evidence does NOT permit completion,
    or None when the associated task completed cleanly.
    """
    from backend.api.schemas import TaskState
    from backend.models.database import Task
    from backend.runtime.events import DbEventLedger
    from backend.services.execution_components import CompletionVerifier

    # Exact evidence binding only; linked_task_id is projection-only.
    task_id = step.evidence_task_id
    if not task_id:
        return {
            "code": "STEP_EVIDENCE_REQUIRED",
            "message": (
                "This plan step has no execution task yet. Execute the step "
                "(a verified runtime task) before declaring it complete."
            ),
        }
    task = session.query(Task).filter_by(id=task_id).first()
    if task is None or task.state != TaskState.COMPLETED.value:
        return {
            "code": "STEP_EVIDENCE_REQUIRED",
            "message": (
                f"Step evidence task '{task_id}' is "
                f"{getattr(task, 'state', 'missing')}, not completed. "
                "Complete the work with tools first; the step then completes "
                "automatically from runtime evidence."
            ),
        }
    error = CompletionVerifier(session, DbEventLedger(session).event_history) \
        .precondition_error(task_id)
    if error:
        return {
            "code": "STEP_EVIDENCE_UNVERIFIED",
            "message": f"Runtime evidence does not permit completion: {error}",
        }
    return None


def _format_plan(objective: Objective, steps: list[PlanStep]) -> dict[str, Any]:
    return {
        "objective_id": objective.id,
        "title": objective.title,
        "purpose": objective.purpose,
        "status": objective.status,
        "priority": objective.priority,
        "current_summary": objective.current_summary,
        "steps": [
            {
                "step_order": s.step_order,
                "description": s.description,
                "status": s.status,
                "result_summary": s.result_summary,
                "blocker_reason": s.blocker_reason,
            }
            for s in steps
        ],
    }


# ---------------------------------------------------------------------------
# Input schemas
# ---------------------------------------------------------------------------

class ObjectiveCreateInput(BaseModel):
    """Input for creating a new durable objective with plan steps."""

    title: str = Field(
        description="Short, clear goal title, e.g. 'Find remote work opportunities in Kenya'"
    )
    purpose: Optional[str] = Field(
        default=None,
        description="One-sentence purpose / motivation for this goal",
    )
    steps: List[str] = Field(
        description=(
            "Ordered list of 2-8 concrete, action-oriented step descriptions. "
            "Each step should be specific enough to complete independently, "
            "e.g. 'Research DataAnnotation.tech eligibility for Kenya-based applicants'"
        ),
        min_length=1,
        max_length=8,
    )
    priority: int = Field(
        default=3,
        ge=1,
        le=5,
        description="Priority 1 (critical) to 5 (low). Defaults to 3 (normal).",
    )


class ObjectiveAddStepInput(BaseModel):
    """Input for appending a new step to an existing objective."""

    description: str = Field(
        description="Concrete step description to add to the plan"
    )
    objective_id: Optional[str] = Field(
        default=None,
        description=(
            "Objective ID to add the step to. "
            "If omitted, the most-recently-active objective is used."
        ),
    )


class ObjectiveCompleteStepInput(BaseModel):
    """Input for marking the current (or a named) plan step as completed."""

    result_summary: str = Field(
        description="What was achieved or discovered in this step (2–3 sentences max)"
    )
    objective_id: Optional[str] = Field(
        default=None,
        description="Objective ID. If omitted, the most-recently-active objective is used.",
    )
    step_order: Optional[int] = Field(
        default=None,
        description=(
            "Step number to complete. If omitted, the first open (pending/in_progress) step "
            "of the objective is completed."
        ),
    )


class ObjectiveGetPlanInput(BaseModel):
    """Input for reading the current objective and its full step list."""

    objective_id: Optional[str] = Field(
        default=None,
        description=(
            "Objective ID to read. "
            "If omitted, the most-recently-active objective is returned."
        ),
    )


class ObjectiveUpdateSummaryInput(BaseModel):
    """Input for writing a fresh progress note to the current objective."""

    summary: str = Field(
        description="Current progress note, e.g. 'Step 1 done — found 3 viable platforms. Moving to step 2.'"
    )
    objective_id: Optional[str] = Field(
        default=None,
        description="Objective ID to update. If omitted, the most-recently-active objective is used.",
    )


# ---------------------------------------------------------------------------
# Tool: objective_create
# ---------------------------------------------------------------------------

class ObjectiveCreateTool(BaseTool[ObjectiveCreateInput]):
    """
    Create a durable multi-step goal plan for a broad user objective.

    Call this when the user states a broad goal that will take multiple
    steps or sessions to complete (e.g. finding remote work, learning
    a skill, financial planning).  Breaking the goal into explicit named
    steps lets Sythion track which steps are done and which remain,
    even across restarts.
    """

    name = "objective_create"
    description = (
        "Create a durable goal plan with named, ordered steps. "
        "Call this for broad user goals that need multiple steps "
        "(e.g. job search, financial planning, learning a skill). "
        "Provide a clear title, optional purpose, and 2-8 concrete step descriptions."
    )
    input_schema = ObjectiveCreateInput

    async def execute(self, input_data: ObjectiveCreateInput) -> ToolOutput:
        try:
            # PHASE 3.5 SUNSET: model-created objectives now go through the
            # canonical GoalManager + Planner (plan-backed from birth). The
            # legacy raw-construction path is retired for creation; statuses
            # use the canonical vocabulary (ready/blocked).
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                from backend.services.goal_manager import GoalManager
                from backend.services.planner import CandidateStepModel, Planner

                manager = GoalManager(session)
                goal = manager.create_goal(
                    title=input_data.title.strip(),
                    purpose=input_data.purpose,
                    priority=input_data.priority,
                )

                descriptions = [s.strip() for s in input_data.steps]
                candidates = [
                    CandidateStepModel(
                        key=f"step_{idx}",
                        description=desc,
                        depends_on=[f"step_{idx - 1}"] if idx > 1 else [],
                    )
                    for idx, desc in enumerate(descriptions, start=1)
                ]
                plan = Planner(session).create_plan(
                    goal.id,
                    candidates,
                    source="model",
                    reason="created via objective_create tool",
                )
                steps_created = Planner(session).plan_steps(plan.id)

                session.refresh(goal)

                # Verify the objective and plan steps persisted
                try:
                    engine = session.bind
                    SessionLocal = sessionmaker(bind=engine)
                    with SessionLocal() as verify_session:
                        obj_verify = verify_session.query(Objective).filter_by(id=goal.id).first()
                        if obj_verify is None:
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: Objective not found for create")
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Objective not persisted"},
                                duration_ms=0,
                            )
                        steps_verify = verify_session.query(PlanStep).filter_by(objective_id=goal.id).all()
                        if not steps_verify:
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: No plan steps for objective %s", goal.id)
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Plan steps not persisted"},
                                duration_ms=0,
                            )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Verification of objective creation failed: %s", exc)
                    return ToolOutput(
                        success=False,
                        error={"code": "VERIFICATION_FAILED", "message": str(exc)},
                        duration_ms=0,
                    )

                plan_data = _format_plan(goal, steps_created)
                summary = (
                    f"Created objective '{goal.title}' "
                    f"({len(steps_created)} steps, canonical plan v{plan.version}). "
                    f"First eligible step: {steps_created[0].description}"
                )
                return ToolOutput(
                    success=True,
                    data=plan_data,
                    model_summary=summary,
                    evidence={"objective_id": goal.id, "plan_id": plan.id},
                )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error={"code": "OBJECTIVE_CREATE_ERROR", "message": str(exc)},
                duration_ms=0,
            )

    async def verify(self, input_data: ObjectiveCreateInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "mutating": True, "app_internal": True}


# ---------------------------------------------------------------------------
# Tool: objective_add_step
# ---------------------------------------------------------------------------

class ObjectiveAddStepTool(BaseTool[ObjectiveAddStepInput]):
    """Append a new plan step to the current (or specified) objective."""

    name = "objective_add_step"
    description = (
        "Append a new step to the current goal plan. "
        "Use when new work items emerge mid-task that belong to the active objective."
    )
    input_schema = ObjectiveAddStepInput

    async def execute(self, input_data: ObjectiveAddStepInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                if input_data.objective_id:
                    objective = session.query(Objective).filter_by(id=input_data.objective_id).first()
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "OBJECTIVE_NOT_FOUND", "message": f"Objective {input_data.objective_id} not found"},
                            duration_ms=0,
                        )
                else:
                    objective = _active_objective(session)
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "NO_ACTIVE_OBJECTIVE", "message": "No active objective found. Create one with objective_create first."},
                            duration_ms=0,
                        )

                now = datetime.utcnow()
                step = PlanStep(
                    objective_id=objective.id,
                    step_order=_next_step_order(session, objective.id),
                    description=_trim(input_data.description.strip(), 200),
                    status="pending",
                    created_at=now,
                    updated_at=now,
                )
                session.add(step)
                objective.updated_at = now
                session.commit()
                session.refresh(step)

                # Verify the step persisted
                try:
                    engine = session.bind
                    SessionLocal = sessionmaker(bind=engine)
                    with SessionLocal() as verify_session:
                        step_verify = verify_session.query(PlanStep).filter_by(id=step.id).first()
                        if step_verify is None:
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: PlanStep not found for add_step")
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Step not persisted"},
                                duration_ms=0,
                            )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Verification of step addition failed: %s", exc)
                    return ToolOutput(
                        success=False,
                        error={"code": "VERIFICATION_FAILED", "message": str(exc)},
                        duration_ms=0,
                    )

                summary = f"Added step {step.step_order} to '{objective.title}': {step.description}"
                return ToolOutput(
                    success=True,
                    data={"objective_id": objective.id, "step_order": step.step_order, "description": step.description},
                    model_summary=summary,
                    evidence={"step_id": step.id},
                )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error={"code": "ADD_STEP_ERROR", "message": str(exc)},
                duration_ms=0,
            )

    async def verify(self, input_data: ObjectiveAddStepInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "mutating": True, "app_internal": True}


# ---------------------------------------------------------------------------
# Tool: objective_complete_step
# ---------------------------------------------------------------------------

class ObjectiveCompleteStepTool(BaseTool[ObjectiveCompleteStepInput]):
    """Mark the current or named plan step as completed with a result summary."""

    name = "objective_complete_step"
    description = (
        "Mark a plan step as completed and record what was achieved. "
        "Call this after finishing each step so the plan stays current. "
        "The next pending step is automatically set to in_progress."
    )
    input_schema = ObjectiveCompleteStepInput

    async def execute(self, input_data: ObjectiveCompleteStepInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                if input_data.objective_id:
                    objective = session.query(Objective).filter_by(id=input_data.objective_id).first()
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "OBJECTIVE_NOT_FOUND", "message": f"Objective {input_data.objective_id} not found"},
                            duration_ms=0,
                        )
                else:
                    objective = _active_objective(session)
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "NO_ACTIVE_OBJECTIVE", "message": "No active objective found."},
                            duration_ms=0,
                        )

                now = datetime.utcnow()

                # Find the step to complete
                if input_data.step_order is not None:
                    step = (
                        session.query(PlanStep)
                        .filter(
                            PlanStep.objective_id == objective.id,
                            PlanStep.step_order == input_data.step_order,
                        )
                        .first()
                    )
                    if step is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "STEP_NOT_FOUND", "message": f"Step {input_data.step_order} not found in objective {objective.id}"},
                            duration_ms=0,
                        )
                else:
                    step = _current_open_step(session, objective.id)
                    if step is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "NO_OPEN_STEP", "message": "No open step found to complete."},
                            duration_ms=0,
                        )

                # PHASE 2 EVIDENCE GATE: a plan-backed step may only be
                # completed from runtime evidence. The model's claim alone is
                # never sufficient — the step's execution task must be
                # terminal-completed and free of completion precondition
                # errors (uncertain actions, unverified mutations).
                if getattr(step, "plan_id", None) is not None:
                    gate_error = _plan_step_evidence_error(session, step)
                    if gate_error:
                        return ToolOutput(
                            success=False,
                            error=gate_error,
                            duration_ms=0,
                        )

                step.status = "completed"
                step.result_summary = _trim(input_data.result_summary, 500)
                step.blocker_reason = None
                step.updated_at = now
                session.flush()  # ensure completed status is visible to the count query below

                # PHASE 3.5: advancement/completion for plan-backed steps goes
                # through the canonical Planner + GoalManager.
                if getattr(step, "plan_id", None) is not None:
                    from backend.services.planner import Planner as _Planner

                    planner = _Planner(session)
                    planner.refresh_readiness(step.plan_id)
                    next_step = planner.select_next_step(objective.id)

                    remaining_open = (
                        session.query(PlanStep)
                        .filter(
                            PlanStep.objective_id == objective.id,
                            PlanStep.status.in_(tuple(OPEN_STEP_STATUSES | {"failed"})),
                        )
                        .count()
                    )
                    objective_status = objective.status
                    if remaining_open == 0:
                        from backend.services.goal_manager import GoalManager

                        try:
                            manager = GoalManager(session)
                            manager.complete_goal(
                                objective.id,
                                note=f"All steps completed. Last result: {_trim(input_data.result_summary, 200)}",
                            )
                            objective_status = "completed"
                        except ValueError as exc:
                            # Criteria unverified or similar: goal stays as-is,
                            # deterministically ineligible.
                            objective_status = objective.status
                            objective.current_summary = f"All steps complete; awaiting eligibility: {_trim(str(exc), 150)}"
                    else:
                        objective.current_summary = (
                            f"Step {step.step_order} completed. "
                            + (f"Now working on step {next_step.step_order}: {next_step.description}" if next_step else "No further steps pending.")
                        )
                    objective.updated_at = now
                    session.commit()
                    next_order = next_step.step_order if next_step else None
                else:
                    # Legacy auto-advance the next pending step to in_progress
                    next_step = (
                        session.query(PlanStep)
                        .filter(
                            PlanStep.objective_id == objective.id,
                            PlanStep.status == "pending",
                        )
                        .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
                        .first()
                    )
                    if next_step:
                        next_step.status = "in_progress"
                        next_step.updated_at = now

                    # Check if all steps are done → mark objective completed
                    remaining_open = (
                        session.query(PlanStep)
                        .filter(
                            PlanStep.objective_id == objective.id,
                            PlanStep.status.in_(tuple(OPEN_STEP_STATUSES)),
                        )
                        .count()
                    )
                    if remaining_open == 0:
                        objective.status = "completed"
                        objective.current_summary = f"All steps completed. Last result: {_trim(input_data.result_summary, 200)}"
                    else:
                        objective.current_summary = (
                            f"Step {step.step_order} completed. "
                            + (f"Now working on step {next_step.step_order}: {next_step.description}" if next_step else "No further steps pending.")
                        )
                    objective.updated_at = now
                    session.commit()
                    next_order = next_step.step_order if next_step else None

                # Verify the step completion and objective status persisted
                try:
                    engine = session.bind
                    SessionLocal = sessionmaker(bind=engine)
                    with SessionLocal() as verify_session:
                        step_verify = verify_session.query(PlanStep).filter_by(id=step.id).first()
                        if step_verify is None or step_verify.status != "completed":
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: PlanStep completion not persisted for step %s", step.id)
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Step completion not persisted"},
                                duration_ms=0,
                            )
                        obj_verify = verify_session.query(Objective).filter_by(id=objective.id).first()
                        if obj_verify is None:
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: Objective not found for complete_step")
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Objective not persisted"},
                                duration_ms=0,
                            )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Verification of step completion failed: %s", exc)
                    return ToolOutput(
                        success=False,
                        error={"code": "VERIFICATION_FAILED", "message": str(exc)},
                        duration_ms=0,
                    )

                summary = (
                    f"Step {step.step_order} of '{objective.title}' marked completed. "
                    + (f"Next: step {next_step.step_order} — {next_step.description}" if next_step else "All steps done — objective completed!")
                )
                return ToolOutput(
                    success=True,
                    data={
                        "objective_id": objective.id,
                        "completed_step": step.step_order,
                        "next_step": next_order,
                        "objective_status": objective_status if getattr(step, "plan_id", None) is not None else objective.status,
                    },
                    model_summary=summary,
                )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error={"code": "COMPLETE_STEP_ERROR", "message": str(exc)},
                duration_ms=0,
            )

    async def verify(self, input_data: ObjectiveCompleteStepInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "mutating": True, "app_internal": True}


# ---------------------------------------------------------------------------
# Tool: objective_get_plan
# ---------------------------------------------------------------------------

class ObjectiveGetPlanTool(BaseTool[ObjectiveGetPlanInput]):
    """Read the current objective and its full step list."""

    name = "objective_get_plan"
    description = (
        "Read the current goal plan — objective title, status, and all steps with their status. "
        "Use this to check what's been done and what's still pending before starting work."
    )
    input_schema = ObjectiveGetPlanInput

    async def execute(self, input_data: ObjectiveGetPlanInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                if input_data.objective_id:
                    objective = session.query(Objective).filter_by(id=input_data.objective_id).first()
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "OBJECTIVE_NOT_FOUND", "message": f"Objective {input_data.objective_id} not found"},
                            duration_ms=0,
                        )
                else:
                    objective = _active_objective(session)
                    if objective is None:
                        return ToolOutput(
                            success=True,
                            data={"objective": None, "message": "No active objective. Use objective_create to start a plan."},
                            model_summary="No active objective found. Start one with objective_create.",
                        )

                steps = (
                    session.query(PlanStep)
                    .filter(PlanStep.objective_id == objective.id)
                    .order_by(PlanStep.step_order.asc(), PlanStep.id.asc())
                    .all()
                )

                plan = _format_plan(objective, steps)
                completed = sum(1 for s in steps if s.status == "completed")
                total = len(steps)
                current = _current_open_step(session, objective.id)
                summary = (
                    f"Objective: '{objective.title}' [{objective.status}] — "
                    f"{completed}/{total} steps done. "
                    + (f"Current: step {current.step_order} — {current.description}" if current else "No open steps remaining.")
                )
                return ToolOutput(
                    success=True,
                    data=plan,
                    model_summary=summary,
                )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error={"code": "GET_PLAN_ERROR", "message": str(exc)},
                duration_ms=0,
            )

    async def verify(self, input_data: ObjectiveGetPlanInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {"risk_level": "none", "requires_confirmation": False, "mutating": False}


# ---------------------------------------------------------------------------
# Tool: objective_update_summary
# ---------------------------------------------------------------------------

class ObjectiveUpdateSummaryTool(BaseTool[ObjectiveUpdateSummaryInput]):
    """Write a fresh progress note to the current objective."""

    name = "objective_update_summary"
    description = (
        "Update the progress summary of the current goal plan. "
        "Use this to record a meaningful milestone or status update mid-objective "
        "so Sythion remembers where things stand across restarts."
    )
    input_schema = ObjectiveUpdateSummaryInput

    async def execute(self, input_data: ObjectiveUpdateSummaryInput) -> ToolOutput:
        try:
            db_mgr = get_db_manager()
            with db_mgr.get_session() as session:
                if input_data.objective_id:
                    objective = session.query(Objective).filter_by(id=input_data.objective_id).first()
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "OBJECTIVE_NOT_FOUND", "message": f"Objective {input_data.objective_id} not found"},
                            duration_ms=0,
                        )
                else:
                    objective = _active_objective(session)
                    if objective is None:
                        return ToolOutput(
                            success=False,
                            error={"code": "NO_ACTIVE_OBJECTIVE", "message": "No active objective found."},
                            duration_ms=0,
                        )

                objective.current_summary = _trim(input_data.summary, 500)
                objective.updated_at = datetime.utcnow()
                session.commit()

                # Verify the summary update persisted
                try:
                    engine = session.bind
                    SessionLocal = sessionmaker(bind=engine)
                    with SessionLocal() as verify_session:
                        obj_verify = verify_session.query(Objective).filter_by(id=objective.id).first()
                        if obj_verify is None or obj_verify.current_summary != objective.current_summary:
                            import logging
                            logging.getLogger(__name__).warning("Verification failed: Objective summary update not persisted")
                            return ToolOutput(
                                success=False,
                                error={"code": "VERIFICATION_FAILED", "message": "Summary update not persisted"},
                                duration_ms=0,
                            )
                except Exception as exc:
                    import logging
                    logging.getLogger(__name__).warning("Verification of summary update failed: %s", exc)
                    return ToolOutput(
                        success=False,
                        error={"code": "VERIFICATION_FAILED", "message": str(exc)},
                        duration_ms=0,
                    )

                return ToolOutput(
                    success=True,
                    data={"objective_id": objective.id, "current_summary": objective.current_summary},
                    model_summary=f"Updated summary for '{objective.title}': {objective.current_summary}",
                )
        except Exception as exc:
            return ToolOutput(
                success=False,
                error={"code": "UPDATE_SUMMARY_ERROR", "message": str(exc)},
                duration_ms=0,
            )

    async def verify(self, input_data: ObjectiveUpdateSummaryInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> dict[str, Any]:
        return {"risk_level": "low", "requires_confirmation": False, "mutating": True, "app_internal": True}
