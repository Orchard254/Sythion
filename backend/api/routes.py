"""
API Routes for Sythion (Synth) Backend
Handles task management, tool execution, settings, and skills.
"""
import logging
import asyncio
import json
import os
import uuid
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

from backend.models.database import get_db, Objective, PlanStep, Skill, Task
from backend.services.task_runner import get_runner, TaskRunner
from backend.services.task_execution_loop import run_task
from backend.adapters.ollama_adapter import get_adapter, format_tools_for_ollama
from backend.tools.executor import ToolExecutor
from backend.api.schemas import (
    TaskCreate, TaskResponse, TaskListResponse, TaskReply,
    TaskState, ObjectiveCreate, ObjectiveListResponse, ObjectiveResponse,
    ObjectiveStatus, ObjectiveUpdate, PlanStepCreate, PlanStepResponse,
    PlanStepStatus, PlanStepUpdate, HealthResponse, SettingsResponse,
    ToolExecutionRequest, ToolExecutionResponse,
    VoiceStartResponse, VoiceAudioInput, VoiceAudioResponse, VoiceAssetsResponse,
    VoiceSegmentResponse,
    SecretWriteRequest, SecretStatusResponse, SecretsListResponse,
    ModelSelectRequest, ModelSelectResponse, OnboardingStatusResponse,
    SkillSubmitRequest, SkillStateRequest,
    PurgeRequest, PurgeReceiptResponse, RetentionRunResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_tool_executor() -> ToolExecutor:
    """Get or create tool executor instance."""
    if not hasattr(get_tool_executor, '_executor'):
        get_tool_executor._executor = ToolExecutor()
        from backend.tools.registry import register_all_tools
        register_all_tools(get_tool_executor._executor)

    return get_tool_executor._executor


def _plan_step_to_response(step: PlanStep) -> PlanStepResponse:
    return PlanStepResponse(
        id=step.id,
        objective_id=step.objective_id,
        step_order=step.step_order,
        description=step.description,
        status=PlanStepStatus(step.status),
        linked_task_id=step.linked_task_id,
        result_summary=step.result_summary,
        blocker_reason=step.blocker_reason,
        created_at=step.created_at,
        updated_at=step.updated_at,
    )


def _objective_to_response(objective: Objective) -> ObjectiveResponse:
    steps = sorted(list(objective.steps or []), key=lambda step: (step.step_order, step.id))
    sub_objs = sorted(list(objective.sub_objectives or []), key=lambda o: (o.created_at or datetime.min, o.id))
    return ObjectiveResponse(
        id=objective.id,
        title=objective.title,
        purpose=objective.purpose,
        status=ObjectiveStatus(objective.status),
        priority=objective.priority,
        current_summary=objective.current_summary,
        last_task_id=objective.last_task_id,
        completion_criteria=objective.completion_criteria,
        criteria_verified=objective.criteria_verified,
        parent_objective_id=objective.parent_objective_id,
        created_at=objective.created_at,
        updated_at=objective.updated_at,
        steps=[_plan_step_to_response(step) for step in steps],
        sub_objectives=[_objective_to_response(sub) for sub in sub_objs],
    )


@router.get("/health", response_model=HealthResponse)
async def health_check(db: Session = Depends(get_db)):
    """Check system health status."""
    from core.config import resolve_selected_model

    # Check Ollama Cloud connectivity
    adapter = get_adapter()
    cloud_ok = await adapter.check_health() if adapter else False
    
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        database_connected=True,
        cloud_configured=cloud_ok,
        selected_model=resolve_selected_model() or "",
    )


@router.get("/version")
async def version_check():
    """Return the sidecar version for the packaged-app update path."""
    from backend import __version__
    from core.config import settings

    return {
        "version": __version__,
        "api_version": "v1",
        "platform": "windows" if os.name == "nt" else os.name,
        "runtime": "uvicorn",
    }


@router.post("/tasks", response_model=TaskResponse)
async def create_task(
    task_data: TaskCreate,
    background_tasks: BackgroundTasks,
    db: Session = Depends(get_db),
    tool_executor: ToolExecutor = Depends(get_tool_executor)
):
    """Create a new task from user request and start execution."""
    runner = get_runner(db)
    
    # Acquire lease
    if not await runner.acquire_lease():
        raise HTTPException(
            status_code=409,
            detail="Another task runner is currently active"
        )
    
    try:
        # Create task
        task = await runner.create_task(
            original_request=task_data.original_request,
            idempotency_key=task_data.idempotency_key
        )

        try:
            from backend.services.work_state import ensure_task_objective
        except ImportError:
            from services.work_state import ensure_task_objective

        objective = ensure_task_objective(db, task)

        # Idempotent replay: an existing task was already accepted and executed.
        # Never re-transition or re-run it (completed/failed/cancelled states are
        # terminal and must not be started again).
        if task.state != TaskState.CREATED.value:
            logger.info(f"Idempotent replay returned existing task {task.id} (state={task.state})")
            return TaskResponse(
                id=task.id,
                original_request=task.original_request,
                state=TaskState(task.state),
                created_at=task.created_at,
                updated_at=task.updated_at,
                version=task.version,
                objective_id=task.objective_id,
                final_result=task.final_result,
                terminal_reason=task.terminal_reason
            )

        # Count enabled skills for the execution-start event (no prototype
        # registry; skills are validated, versioned declarative workflows).
        enabled_skills = db.query(Skill).filter_by(state="enabled").count()

        # Get available tools for LLM
        tool_metadata = []
        for name in tool_executor.list_tools():
            tool = tool_executor.get_tool(name)
            if tool:
                tool_metadata.append({
                    "name": tool.name,
                    "description": tool.description,
                    "input_schema": tool.input_schema,
                })
        formatted_tools = format_tools_for_ollama(tool_metadata)
        
        # Transition to running state (skip planning, go straight to execution)
        await runner.transition_state(
            task_id=task.id,
            new_state=TaskState.RUNNING,
            event_type="task.execution_started",
            event_data={
                "tools_available": len(tool_metadata),
                "skills_available": enabled_skills,
                "started_at": task.created_at.isoformat()
            }
        )
        
        # Start async task execution in background
        background_tasks.add_task(run_task, db, task.id, tool_executor)
        
        logger.info(f"Task {task.id} created and execution started in background")
        
        return TaskResponse(
            id=task.id,
            original_request=task.original_request,
            state=TaskState.RUNNING,
            created_at=task.created_at,
            updated_at=task.updated_at,
            version=task.version,
            objective_id=objective.id
        )
        
    finally:
        # Release lease after task creation (execution has its own lease)
        await runner.release_lease()


@router.get("/tasks", response_model=TaskListResponse)
async def list_tasks(
    limit: int = 50,
    state: Optional[TaskState] = None,
    db: Session = Depends(get_db)
):
    """List tasks with optional filtering."""
    runner = get_runner(db)
    
    if state:
        # Filter by state - would need to add filtering to runner
        tasks = await runner.list_tasks(limit=limit)
        tasks = [t for t in tasks if t.state == state.value]
    else:
        tasks = await runner.list_tasks(limit=limit)
    
    return TaskListResponse(
        tasks=[
            TaskResponse(
                id=t.id,
                original_request=t.original_request,
                state=TaskState(t.state),
                created_at=t.created_at,
                updated_at=t.updated_at,
                version=t.version,
                objective_id=t.objective_id,
                final_result=t.final_result,
                terminal_reason=t.terminal_reason
            )
            for t in tasks
        ],
        total=len(tasks)
    )


@router.get("/tasks/{task_id}", response_model=TaskResponse)
async def get_task(task_id: str, db: Session = Depends(get_db)):
    """Get a specific task by ID."""
    runner = get_runner(db)
    task = await runner.get_task(task_id)
    
    if not task:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    
    return TaskResponse(
        id=task.id,
        original_request=task.original_request,
        state=TaskState(task.state),
        created_at=task.created_at,
        updated_at=task.updated_at,
        version=task.version,
        objective_id=task.objective_id,
        final_result=task.final_result,
        terminal_reason=task.terminal_reason
    )


@router.get("/tasks/{task_id}/objective", response_model=ObjectiveResponse)
async def get_task_objective(task_id: str, db: Session = Depends(get_db)):
    """Get the durable objective currently linked to a task."""
    task = db.query(Task).filter_by(id=task_id).first()
    if task is None:
        raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
    if not task.objective_id:
        raise HTTPException(status_code=404, detail=f"Task {task_id} has no linked objective")

    objective = db.query(Objective).filter_by(id=task.objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {task.objective_id} not found")
    return _objective_to_response(objective)


@router.post("/tasks/{task_id}/cancel", response_model=TaskResponse)
async def cancel_task(task_id: str, db: Session = Depends(get_db)):
    """Cancel a running task."""
    runner = get_runner(db)
    
    # Acquire lease
    if not await runner.acquire_lease():
        raise HTTPException(
            status_code=409,
            detail="Cannot cancel task while another runner is active"
        )
    
    try:
        task = await runner.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        # Check if task can be cancelled
        if task.state in [TaskState.COMPLETED.value, TaskState.FAILED.value, TaskState.CANCELLED.value]:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot cancel task in state {task.state}"
            )
        
        updated_task = await runner.cancel_task(task_id)
        
        return TaskResponse(
            id=updated_task.id,
            original_request=updated_task.original_request,
            state=TaskState(updated_task.state),
            created_at=updated_task.created_at,
            updated_at=updated_task.updated_at,
            version=updated_task.version,
            objective_id=updated_task.objective_id
        )
        
    finally:
        await runner.release_lease()


@router.post("/stop", response_model=TaskResponse)
async def stop_active_task(db: Session = Depends(get_db)):
    """Stop the most recently active task.

    Global-stop entrypoint used by the desktop shell's global shortcut (and
    available to the voice pipeline). Cancels the newest task that is still
    in a non-terminal state so a stop always has something to act on without
    requiring a caller to know the task id.

    Unlike per-task cancel, this does not need to own the runner lease: the
    actively-running executor renews the lease itself, so requiring it here
    would make the shortcut a no-op exactly when it is most needed.
    """
    runner = get_runner(db)
    active_states = [
        TaskState.CREATED.value,
        TaskState.RUNNING.value,
        TaskState.WAITING_FOR_USER.value,
        TaskState.INTERRUPTED.value,
        TaskState.PAUSED.value,
    ]
    task = (
        db.query(Task)
        .filter(Task.state.in_(active_states))
        .order_by(Task.updated_at.desc())
        .first()
    )
    if not task:
        raise HTTPException(status_code=404, detail="No active task to stop")

    updated_task = await runner.force_cancel(task.id)
    logger.info(f"Global stop cancelled task {task.id}")

    return TaskResponse(
        id=updated_task.id,
        original_request=updated_task.original_request,
        state=TaskState(updated_task.state),
        created_at=updated_task.created_at,
        updated_at=updated_task.updated_at,
        version=updated_task.version,
        objective_id=updated_task.objective_id
    )


@router.post("/tasks/{task_id}/resume", response_model=TaskResponse)
async def resume_task(task_id: str, db: Session = Depends(get_db)):
    """Resume an interrupted task.
    
    This endpoint allows resuming a task that was interrupted due to
    a crash or restart. The task will re-observe state before continuing
    any mutating operations.
    """
    runner = get_runner(db)
    
    # Acquire lease with task ID for tracking
    if not await runner.acquire_lease(task_id=task_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot resume task while another runner is active"
        )
    
    try:
        task = await runner.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")
        
        # Only interrupted or paused tasks can be resumed
        if task.state not in (TaskState.INTERRUPTED.value, TaskState.PAUSED.value):
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume task in state {task.state}. Only interrupted or paused tasks can be resumed."
            )

        note = (
            "Task resumed after interruption. Will re-observe before continuing."
            if task.state == TaskState.INTERRUPTED.value
            else "Task resumed after pause. Skill re-verified before its next step."
        )
        
        # Transition back to running
        updated_task = await runner.transition_state(
            task_id=task_id,
            new_state=TaskState.RUNNING,
            event_type="task.resumed",
            event_data={
                "resumed_at": datetime.utcnow().isoformat(),
                "note": note
            }
        )
        
        # Start execution in background
        from backend.tools.executor import ToolExecutor
        tool_executor = ToolExecutor()
        from backend.api.routes import get_tool_executor
        tool_executor = get_tool_executor()
        
        from backend.services.task_execution_loop import run_task
        import asyncio
        asyncio.create_task(run_task(db, task_id, tool_executor))
        
        logger.info(f"Task {task_id} resumed and execution started")
        
        return TaskResponse(
            id=updated_task.id,
            original_request=updated_task.original_request,
            state=TaskState(updated_task.state),
            created_at=updated_task.created_at,
            updated_at=updated_task.updated_at,
            version=updated_task.version,
            objective_id=updated_task.objective_id,
            final_result=updated_task.final_result,
            terminal_reason=updated_task.terminal_reason
        )
        
    finally:
        await runner.release_lease()


@router.post("/tasks/{task_id}/reply", response_model=TaskResponse)
async def reply_to_task(task_id: str, reply_data: TaskReply, db: Session = Depends(get_db)):
    """Submit a clarification reply to a waiting_for_user task.

    The reply is recorded as a durable `user.replied` event on the SAME task
    (never a new task), and execution resumes on that task.
    """
    runner = get_runner(db)

    if not await runner.acquire_lease(task_id=task_id):
        raise HTTPException(
            status_code=409,
            detail="Cannot reply while another runner is active"
        )

    try:
        task = await runner.get_task(task_id)
        if not task:
            raise HTTPException(status_code=404, detail=f"Task {task_id} not found")

        if task.state != TaskState.WAITING_FOR_USER.value:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot reply to task in state {task.state}. Only waiting_for_user tasks can receive replies."
            )

        # Record the reply as an immutable event and return to running.
        updated_task = await runner.transition_state(
            task_id=task_id,
            new_state=TaskState.RUNNING,
            event_type="user.replied",
            event_data={
                "answer": reply_data.answer,
                "replied_at": datetime.utcnow().isoformat(),
            },
            pending_question=None,
        )

        # Resume background execution for this same task.
        from backend.services.task_execution_loop import run_task
        tool_executor = get_tool_executor()
        asyncio.create_task(run_task(db, task_id, tool_executor))

        logger.info(f"Task {task_id} resumed after user reply")

        return TaskResponse(
            id=updated_task.id,
            original_request=updated_task.original_request,
            state=TaskState(updated_task.state),
            created_at=updated_task.created_at,
            updated_at=updated_task.updated_at,
            version=updated_task.version,
            objective_id=updated_task.objective_id,
            final_result=updated_task.final_result,
            terminal_reason=updated_task.terminal_reason
        )

    finally:
        await runner.release_lease()


@router.get("/tasks/{task_id}/events")
async def get_task_events(task_id: str, after_sequence: int = 0, db: Session = Depends(get_db)):
    """Get immutable event history for a task, replayable from `after_sequence`.

    Events are returned in strict sequence order. Passing `after_sequence=N`
    returns only events with sequence_num > N, reconstructing exactly the
    committed timeline from that point (used by slow/disconnected clients).
    """
    from backend.models.database import TaskEvent

    query = db.query(TaskEvent).filter_by(task_id=task_id)
    if after_sequence:
        query = query.filter(TaskEvent.sequence_num > after_sequence)
    events = query.order_by(TaskEvent.sequence_num.asc()).all()

    return {
        "task_id": task_id,
        "after_sequence": after_sequence,
        "events": [
            {
                "event_type": e.event_type,
                "event_data": _decode_event_data(e.event_data),
                "sequence_num": e.sequence_num,
                "created_at": e.created_at
            }
            for e in events
        ]
    }


def _decode_event_data(raw: Any) -> Dict[str, Any]:
    """Decode stored event JSON, tolerating legacy malformed payloads."""
    if raw is None:
        return {}
    if isinstance(raw, dict):
        return raw
    try:
        return json.loads(raw)
    except (json.JSONDecodeError, TypeError):
        try:
            return json.loads(str(raw).replace("\\", "\\\\"))
        except (json.JSONDecodeError, TypeError):
            return {"raw": str(raw)}


@router.get("/objectives", response_model=ObjectiveListResponse)
async def list_objectives(
    status: Optional[ObjectiveStatus] = None,
    limit: int = 50,
    db: Session = Depends(get_db),
):
    """List durable objectives and their current plan steps."""
    query = db.query(Objective)
    if status is not None:
        query = query.filter(Objective.status == status.value)
    objectives = query.order_by(Objective.updated_at.desc(), Objective.created_at.desc()).limit(limit).all()
    return ObjectiveListResponse(
        objectives=[_objective_to_response(objective) for objective in objectives],
        total=len(objectives),
    )


@router.post("/objectives", response_model=ObjectiveResponse)
async def create_objective(payload: ObjectiveCreate, db: Session = Depends(get_db)):
    """Create a durable objective with optional initial plan steps.

    If plan_steps is empty but purpose/title contains a complex request,
    decompose_request() is used to auto-split into ordered steps.
    Explicit plan_steps are used as-is.
    """
    try:
        from backend.services.work_state import decompose_request
    except ImportError:
        from services.work_state import decompose_request

    objective = Objective(
        id=str(uuid.uuid4()),
        title=payload.title.strip(),
        purpose=payload.purpose.strip() if payload.purpose else None,
        status=ObjectiveStatus.ACTIVE.value,
        priority=payload.priority,
        current_summary="Objective created manually.",
        completion_criteria=payload.completion_criteria.strip() if payload.completion_criteria else None,
        parent_objective_id=payload.parent_objective_id.strip() if payload.parent_objective_id else None,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(objective)
    db.flush()

    # Determine plan steps and their initial statuses
    if payload.plan_steps:
        # Explicit plan_steps: all pending (user controls the plan)
        steps = payload.plan_steps
        statuses = [PlanStepStatus.PENDING.value] * len(steps)
    elif payload.purpose:
        decomposed = decompose_request(payload.purpose)
        if decomposed:
            # Auto-decomposed: first step in_progress, rest pending
            steps = decomposed
            statuses = [PlanStepStatus.IN_PROGRESS.value] + [PlanStepStatus.PENDING.value] * (len(decomposed) - 1)
        else:
            steps = [payload.purpose.strip()]
            statuses = [PlanStepStatus.IN_PROGRESS.value]
    else:
        # No purpose, no plan_steps: no auto-created steps
        steps = []
        statuses = []

    for idx, (description, status) in enumerate(zip(steps, statuses), start=1):
        db.add(PlanStep(
            objective_id=objective.id,
            step_order=idx,
            description=description,
            status=status,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        ))
    db.commit()
    db.refresh(objective)
    return _objective_to_response(objective)


@router.get("/objectives/{objective_id}", response_model=ObjectiveResponse)
async def get_objective(objective_id: str, db: Session = Depends(get_db)):
    """Get one durable objective and its plan."""
    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")
    return _objective_to_response(objective)


@router.patch("/objectives/{objective_id}", response_model=ObjectiveResponse)
async def update_objective(objective_id: str, payload: ObjectiveUpdate, db: Session = Depends(get_db)):
    """Update mutable durable-objective fields."""
    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if hasattr(value, "value"):
            value = value.value
        setattr(objective, key, value)
    objective.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(objective)
    return _objective_to_response(objective)


@router.post("/objectives/{objective_id}/verify-criteria", response_model=ObjectiveResponse)
async def verify_objective_criteria(objective_id: str, db: Session = Depends(get_db)):
    """Mark objective completion criteria as verified.

    This allows the objective to complete when all steps are done.
    """
    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")
    if not objective.completion_criteria:
        raise HTTPException(status_code=400, detail="Objective has no completion criteria to verify")
    objective.criteria_verified = True
    objective.updated_at = datetime.utcnow()
    # If all steps are already complete, auto-complete the objective
    remaining = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective.id, PlanStep.status.in_(tuple({"pending", "in_progress", "blocked"} | {"failed"})))
        .count()
    )
    if remaining == 0:
        objective.status = "completed"
    db.commit()
    db.refresh(objective)
    return _objective_to_response(objective)


@router.post("/objectives/{objective_id}/steps", response_model=PlanStepResponse)
async def create_plan_step(objective_id: str, payload: PlanStepCreate, db: Session = Depends(get_db)):
    """Append a plan step to a durable objective."""
    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")

    latest = (
        db.query(PlanStep)
        .filter(PlanStep.objective_id == objective_id)
        .order_by(PlanStep.step_order.desc(), PlanStep.id.desc())
        .first()
    )
    step = PlanStep(
        objective_id=objective_id,
        step_order=(latest.step_order + 1) if latest else 1,
        description=payload.description,
        status=payload.status.value,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(step)
    objective.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(step)
    return _plan_step_to_response(step)


@router.patch("/objectives/steps/{step_id}", response_model=PlanStepResponse)
async def update_plan_step(step_id: int, payload: PlanStepUpdate, db: Session = Depends(get_db)):
    """Update a durable plan step."""
    step = db.query(PlanStep).filter_by(id=step_id).first()
    if step is None:
        raise HTTPException(status_code=404, detail=f"Plan step {step_id} not found")

    data = payload.model_dump(exclude_unset=True)
    for key, value in data.items():
        if hasattr(value, "value"):
            value = value.value
        setattr(step, key, value)
    step.updated_at = datetime.utcnow()
    objective = db.query(Objective).filter_by(id=step.objective_id).first()
    if objective is not None:
        objective.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(step)
    return _plan_step_to_response(step)


@router.get("/tools", response_model=Dict[str, List[Dict[str, Any]]])
async def list_tools(tool_executor: ToolExecutor = Depends(get_tool_executor)):
    """List all available tools."""
    tools = []
    for name in tool_executor.list_tools():
        tool = tool_executor.get_tool(name)
        if tool:
            tools.append({
                "name": tool.name,
                "description": tool.description,
            })
    return {"tools": tools}


@router.get("/skills", response_model=List[Dict[str, Any]])
async def list_skills(db: Session = Depends(get_db)):
    """List all skills with their current validated version summary."""
    from backend.services.skill_engine import list_skill_summaries
    return list_skill_summaries(db)


@router.post("/skills", response_model=Dict[str, Any])
async def submit_skill(
    request: SkillSubmitRequest,
    db: Session = Depends(get_db),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """Submit a declarative skill for review.

    Submissions are validated and stored as proposals. Every skill requires
    explicit owner review and enablement (`POST /skills/proposals/{id}/approve`)
    before it can run; proposed skills are never executed.
    """
    from backend.services.skill_engine import (
        SkillDefinition,
        ValidationError,
        allowed_tool_names,
        definition_to_json,
        validate_definition,
        get_skill,
        ProposedSkill,
    )

    try:
        definition = SkillDefinition.model_validate(request.definition)
    except ValidationError as exc:
        raise HTTPException(status_code=422, detail=str(exc))

    errors = validate_definition(definition, allowed_tool_names(tool_executor))
    if errors:
        raise HTTPException(status_code=422, detail=errors)

    if get_skill(db, request.skill_id):
        raise HTTPException(
            status_code=409,
            detail=f"Skill {request.skill_id} already exists (update via a new version on the existing skill)",
        )

    proposal = ProposedSkill(
        skill_id=request.skill_id,
        definition=definition_to_json(definition),
        trace_ids="[]",
        status="proposed",
        validation_errors="[]",
    )
    db.add(proposal)
    db.commit()
    db.refresh(proposal)
    return {
        "proposal_id": proposal.id,
        "skill_id": request.skill_id,
        "status": "proposed",
        "detail": "Submitted for review. Approve via POST /skills/proposals/{id}/approve.",
    }


@router.get("/skills/proposals")
async def list_proposals(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List proposed skills awaiting explicit owner review."""
    from backend.services.skill_engine import list_proposals as engine_list
    return engine_list(db, status=status)


@router.post("/skills/proposals/{proposal_id}/approve")
async def approve_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """Explicitly review and enable a proposed skill as a live, versioned skill."""
    from backend.services.skill_engine import (
        approve_proposal as engine_approve,
        get_proposal,
    )

    proposal = get_proposal(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    try:
        skill = engine_approve(db, proposal, tool_executor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "skill_id": skill.id,
        "state": skill.state,
        "detail": "Skill enabled with a validated, versioned definition.",
    }


@router.post("/skills/proposals/{proposal_id}/reject")
async def reject_proposal(
    proposal_id: int,
    db: Session = Depends(get_db),
):
    """Explicitly reject a proposed skill (never executed)."""
    from backend.services.skill_engine import get_proposal, reject_proposal as engine_reject

    proposal = get_proposal(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    try:
        updated = engine_reject(db, proposal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {"proposal_id": updated.id, "status": updated.status}


@router.get("/memory/edges")
async def list_memory_edges(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List memory edges (default: all), optionally filtered by lifecycle status.

    Edges decay in weight when unused and surface as `stale` for explicit
    owner review -- they are never auto-deleted. `removed` edges are excluded
    from recall walks and context injection but kept on record.
    """
    from backend.services.memory_consolidation import list_edges as engine_list
    return engine_list(db, status=status)


@router.post("/memory/edges/{edge_id}/reinforce")
async def reinforce_memory_edge(edge_id: int, db: Session = Depends(get_db)):
    """Owner review decision: reinforce a stale edge back to active (weight 1.0)."""
    from backend.services.memory_consolidation import reinforce_edge as engine_reinforce

    try:
        edge = engine_reinforce(db, edge_id)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 422, detail=str(exc))
    return edge


@router.post("/memory/edges/{edge_id}/remove")
async def remove_memory_edge(edge_id: int, db: Session = Depends(get_db)):
    """Owner review decision: mark a stale edge removed (reviewed; never auto-applied)."""
    from backend.services.memory_consolidation import remove_edge as engine_remove

    try:
        edge = engine_remove(db, edge_id)
    except ValueError as exc:
        raise HTTPException(status_code=404 if "not found" in str(exc) else 422, detail=str(exc))
    return edge


@router.get("/memory/report")
async def memory_validation_report(
    last_n: int = 50,
    db: Session = Depends(get_db),
):
    """Data-only validation dump of the memory system.

    Per-store table counts, memory-context injection stats aggregated from the
    last `last_n` model-engine rollout-log runs, and the relation vocabulary
    in use on memory edges. Read-only; contains no implementation details.
    """
    from backend.services.validation_report import build_validation_report
    return build_validation_report(db, last_n=last_n)


@router.post("/skills/generate")
async def generate_skill_proposals(
    db: Session = Depends(get_db),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """Scan verified successful traces and propose skills for >=2 materially
    similar runs. Proposals grow evidence per similar run and are auto-enabled
    once the trace count reaches the configured threshold (default 5); nothing
    is executed automatically."""
    from backend.services.skill_engine import (
        Task,
        maybe_generate_proposal,
    )

    completed = (
        db.query(Task)
        .filter_by(state="completed")
        .order_by(Task.created_at.asc())
        .all()
    )
    created = []
    for task in completed:
        proposal = maybe_generate_proposal(db, task.id, tool_executor)
        if proposal is not None:
            created.append(proposal.id)
    return {"generated": created}


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str, db: Session = Depends(get_db)):
    """Get a skill's detail and all validated versions."""
    from backend.services.skill_engine import get_skill_detail

    detail = get_skill_detail(db, skill_id)
    if not detail:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return detail


@router.post("/skills/{skill_id}/state", response_model=Dict[str, Any])
async def set_skill_state(
    skill_id: str,
    request: SkillStateRequest,
    db: Session = Depends(get_db),
):
    """Enable or disable a live skill.

    Disabling a skill before a task's next unstarted step pauses that task.
    """
    from backend.services.skill_engine import set_skill_state as engine_set

    skill = engine_set(db, skill_id, request.state)
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    return {"skill_id": skill.id, "state": skill.state}


@router.post("/retention/purge", response_model=RetentionRunResponse)
async def purge_records(request: PurgeRequest, db: Session = Depends(get_db)):
    """Purge complete task records (task + full audit events + conversation turns).

    Either `days` (retention window, defaulting to the configured
    `retention_days`) or an exact `task_id` is required. Every purge writes a
    non-sensitive receipt; task content is never retained. The windowed run
    also sweeps expired/orphaned `conversation_turns` (`purged_turns`).
    """
    from backend.services.retention import (
        get_retention_days,
        purge_expired,
        purge_task,
        purge_turns,
    )

    if request.task_id is not None:
        receipt = purge_task(db, request.task_id, reason=request.reason)
        if receipt is None:
            raise HTTPException(status_code=404, detail=f"Task {request.task_id} not found")
        receipts = [receipt]
        purged_turns = 0
    else:
        days = request.days or get_retention_days(db)
        if days < 1:
            raise HTTPException(status_code=400, detail="days must be >= 1")
        receipts = purge_expired(db, days=days)
        purged_turns = purge_turns(db, days=days)

    return RetentionRunResponse(
        receipts=[PurgeReceiptResponse(**r) for r in receipts],
        purged_count=len(receipts),
        purged_turns=purged_turns,
    )


@router.get("/retention/receipts", response_model=List[PurgeReceiptResponse])
async def list_purge_receipts(db: Session = Depends(get_db)):
    """List non-sensitive purge receipts (identity + timing only)."""
    from backend.services.retention import list_purge_receipts as engine_receipts

    return [PurgeReceiptResponse(**r) for r in engine_receipts(db)]


@router.get("/settings", response_model=SettingsResponse)
async def get_settings(db: Session = Depends(get_db)):
    """Get current application settings (non-secret values only)."""
    from backend.core.config import settings
    from backend.core.settings_store import (
        get_settings as load_persisted,
        resolved_file_roots,
    )

    persisted = load_persisted(db)
    file_roots = resolved_file_roots(db)

    return SettingsResponse(
        host=settings.host,
        port=settings.port,
        ollama_cloud_base_url=settings.ollama_cloud_base_url,
        selected_model=settings.selected_model,
        file_roots=file_roots,
        max_actions_per_task=settings.max_actions_per_task,
        max_cloud_requests_per_task=settings.max_cloud_requests_per_task,
        voice_enabled=bool(persisted.get("voice_enabled", settings.voice_enabled)),
        voice_recording_enabled=bool(persisted.get("voice_recording_enabled", False)),
        voice_tts_voice=str(persisted.get("voice_tts_voice", "") or ""),
        retention_days=int(persisted.get("retention_days", 90) or 90),
    )


@router.get("/secrets", response_model=SecretsListResponse)
async def list_secret_status():
    """List which known secrets are configured. Never returns secret values."""
    from backend.core.secrets import list_secret_names
    return SecretsListResponse(configured=list_secret_names())


@router.put("/secrets/{name}", response_model=SecretStatusResponse)
async def set_secret(name: str, body: SecretWriteRequest):
    """Store a secret (write-only). The value is never returned."""
    from backend.core.secrets import set_secret as store_secret, KNOWN_SECRETS
    if name not in KNOWN_SECRETS:
        raise HTTPException(status_code=404, detail=f"Unknown secret name: {name}")
    store_secret(name, body.value)
    return SecretStatusResponse(configured=True, name=name)


@router.delete("/secrets/{name}", response_model=SecretStatusResponse)
async def delete_secret(name: str):
    """Delete a stored secret."""
    from backend.core.secrets import delete_secret as remove_secret, KNOWN_SECRETS
    if name not in KNOWN_SECRETS:
        raise HTTPException(status_code=404, detail=f"Unknown secret name: {name}")
    remove_secret(name)
    return SecretStatusResponse(configured=False, name=name)


@router.put("/settings")
async def update_settings(request: Request, db: Session = Depends(get_db)):
    """Update non-secret settings.

    Only whitelisted keys are accepted; secrets never travel through this
    endpoint (use the write-only secret store under /secrets instead).
    """
    from backend.core.settings_store import update_settings as persist_settings

    data = await request.json()
    if not isinstance(data, dict) or not data:
        raise HTTPException(status_code=400, detail="A non-empty settings object is required")

    try:
        updated = persist_settings(db, data)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))

    return {
        "message": "Settings updated",
        "settings": updated,
    }


@router.get("/models")
async def list_models():
    """List available Ollama Cloud models."""
    adapter = get_adapter()
    
    try:
        models = await adapter.list_models()
        return {"models": models}
    except Exception as e:
        logger.error(f"Failed to list models: {e}")
        raise HTTPException(status_code=503, detail=f"Failed to fetch models: {str(e)}")


@router.get("/onboarding", response_model=OnboardingStatusResponse)
async def onboarding_status():
    """Return onboarding status: key configured, selected model, onboarded flag."""
    from backend.core.secrets import has_secret, OLLAMA_API_KEY
    from backend.core.config import resolve_selected_model, settings

    selected = resolve_selected_model()
    api_key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)
    return OnboardingStatusResponse(
        api_key_configured=api_key_ok,
        selected_model=selected,
        onboarded=bool(selected),
    )


@router.post("/onboarding/models", response_model=ModelSelectResponse)
async def select_model(body: ModelSelectRequest):
    """Select one exact authenticated Cloud model ID and persist it.

    The model must be present in the authenticated model list; a stale,
    retyped, or arbitrary ID is rejected so the sidecar never runs against a
    model it cannot use. Only the non-secret model ID is persisted.
    """
    from backend.core.profile import set_selected_model
    from backend.adapters.ollama_adapter import get_adapter

    adapter = get_adapter()
    try:
        models = await adapter.list_models()
    except Exception as e:
        raise HTTPException(status_code=503, detail=f"Failed to fetch models: {str(e)}")

    model_ids = {
        model_id
        for model in models
        for model_id in (model.get("name"), model.get("model"))
        if isinstance(model_id, str)
    }
    if body.model_id not in model_ids:
        raise HTTPException(
            status_code=400,
            detail=f"Model '{body.model_id}' is not in the authenticated model list",
        )

    set_selected_model(body.model_id)
    # Refresh the live adapter so capability checks use the new selection.
    adapter.selected_model = body.model_id
    adapter._capability_profile = None
    return ModelSelectResponse(selected_model=body.model_id, persisted=True)


@router.post("/chat/completions")
async def chat_completion(request: Request):
    """Send a chat completion request to Ollama Cloud."""
    adapter = get_adapter()
    data = await request.json()
    
    messages = data.get("messages", [])
    tools = data.get("tools", [])
    
    if not messages:
        raise HTTPException(status_code=400, detail="Messages are required")
    
    try:
        # Format tools if provided
        formatted_tools = format_tools_for_ollama(tools) if tools else []
        
        response = await adapter.execute_tool_call(
            messages=messages,
            tools=formatted_tools,
            max_tokens=data.get("max_tokens", 4000)
        )
        
        return response
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=502, detail=f"Ollama Cloud request failed: {str(e)}")


# Voice API Endpoints

@router.get("/voice/assets", response_model=VoiceAssetsResponse)
async def voice_assets_status():
    """Report pinned voice-asset status. Never downloads anything."""
    from backend.core.voice_assets import asset_status

    return VoiceAssetsResponse(**asset_status())


@router.post("/voice/assets/install", response_model=VoiceAssetsResponse)
async def voice_assets_install():
    """Explicitly install the pinned voice assets (owner-initiated only).

    This is the single explicit install path; nothing downloads at import or
    startup. Runs synchronously and verifies the pinned SHA-256 before use.
    """
    from backend.core.voice_assets import install_voice_assets

    try:
        status = install_voice_assets()
    except Exception as e:
        logger.error("Voice asset installation failed: %s", e)
        raise HTTPException(status_code=502, detail=f"Voice asset install failed: {str(e)}")
    return VoiceAssetsResponse(**status)


@router.post("/voice/start", response_model=VoiceStartResponse)
async def start_voice_session(db: Session = Depends(get_db)):
    """Start a new voice session (local VAD + STT, no audio captured by sidecar)."""
    from backend.services.voice_pipeline import get_voice_manager
    from backend.core.settings_store import get_setting

    recording_enabled = get_setting(db, "voice_recording_enabled") == "true"
    manager = get_voice_manager()
    session_id = manager.start_session(recording_enabled=recording_enabled)

    return VoiceStartResponse(
        session_id=session_id,
        status="listening",
        stt_available=manager.stt_available(),
        recording_enabled=recording_enabled,
    )


@router.post("/voice/{session_id}/audio", response_model=VoiceAudioResponse)
async def process_voice_audio(session_id: str, audio_input: VoiceAudioInput):
    """Feed a short PCM frame; return any completed segments (local processing)."""
    from backend.services.voice_pipeline import get_voice_manager
    import base64

    if audio_input.session_id != session_id:
        raise HTTPException(status_code=400, detail="Session ID mismatch")

    try:
        audio_data = base64.b64decode(audio_input.audio_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid audio data: {str(e)}")

    if audio_input.channels != 1:
        raise HTTPException(status_code=400, detail="Only mono (1 channel) audio is supported")

    manager = get_voice_manager()
    completed = manager.feed_pcm(session_id, audio_data)
    session = manager.get_session(session_id)
    is_speaking = bool(session and session.speaking)

    return VoiceAudioResponse(
        session_id=session_id,
        segments=[_segment_to_schema(s) for s in completed],
        is_speaking=is_speaking,
        stt_available=manager.stt_available(),
    )


@router.post("/voice/{session_id}/end")
async def end_voice_session(session_id: str):
    """End a voice session; flush the final segment and delete raw audio."""
    from backend.services.voice_pipeline import get_voice_manager

    manager = get_voice_manager()
    results = manager.end_session(session_id)

    return {
        "session_id": session_id,
        "status": "ended",
        "segments": [_segment_to_schema(s) for s in results],
    }


@router.post("/voice/speak")
async def speak_text(request: Request):
    """Convert text to speech (local/offline TTS only)."""
    from backend.services.voice_pipeline import get_voice_manager
    import json

    data = await request.json()
    text = (data.get("text") or "").strip()

    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    if len(text) > 1000:
        raise HTTPException(status_code=400, detail="Text is too long for TTS")

    manager = get_voice_manager()
    success = manager.speak(text)

    return {
        "success": success,
        "text": text[:100] + "..." if len(text) > 100 else text,
    }


def _segment_to_schema(segment) -> VoiceSegmentResponse:
    return VoiceSegmentResponse(
        segment_id=segment.segment_id,
        text=segment.text,
        stop_detected=segment.stop_detected,
        audio_deleted=segment.audio_bytes_deleted,
        stt_available=segment.stt_available,
    )


# --- Tool Authoring (L3) --------------------------------------------------

@router.post("/tools/propose")
async def propose_tool(request: Request, db: Session = Depends(get_db)):
    """Submit a model-authored tool proposal for validation and approval.

    The proposal is validated, sandbox-tested, and stored. Owner approval
    is required before the tool becomes live.
    """
    from backend.services.tool_authoring import (
        ToolProposal,
        validate_tool_proposal,
        sandbox_test,
        ProposedTool,
    )

    data = await request.json()
    try:
        proposal = ToolProposal(**data)
    except Exception as e:
        raise HTTPException(status_code=422, detail=str(e))

    # Validate
    validation = validate_tool_proposal(proposal)
    if not validation.valid:
        raise HTTPException(status_code=422, detail=validation.errors)

    # Sandbox test
    sandbox = sandbox_test(proposal)

    # Store
    tool_proposal = ProposedTool(
        name=proposal.name,
        description=proposal.description,
        input_schema_json=json.dumps(proposal.input_schema),
        implementation=proposal.implementation,
        validation_errors=json.dumps([]),
        sandbox_result=json.dumps(sandbox.model_dump()),
        status="sandbox_passed" if sandbox.passed else "proposed",
    )
    db.add(tool_proposal)
    db.commit()
    db.refresh(tool_proposal)

    return {
        "proposal_id": tool_proposal.id,
        "name": proposal.name,
        "status": tool_proposal.status,
        "validation": validation.model_dump(),
        "sandbox": sandbox.model_dump(),
        "detail": "Tool proposal submitted. Approve via POST /tools/proposals/{id}/approve.",
    }


@router.get("/tools/proposals")
async def list_tool_proposals(
    status: Optional[str] = None,
    db: Session = Depends(get_db),
):
    """List tool proposals, optionally filtered by status."""
    from backend.services.tool_authoring import list_proposed_tools
    return list_proposed_tools(db, status=status)


@router.post("/tools/proposals/{proposal_id}/approve")
async def approve_tool(
    proposal_id: int,
    db: Session = Depends(get_db),
    tool_executor: ToolExecutor = Depends(get_tool_executor),
):
    """Approve a tool proposal and enable it in guarded mode."""
    from backend.services.tool_authoring import (
        get_proposed_tool,
        approve_tool_proposal,
    )

    proposal = get_proposed_tool(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    try:
        approved = approve_tool_proposal(db, proposal, tool_executor)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "proposal_id": approved.id,
        "name": approved.name,
        "status": approved.status,
        "enabled_mode": approved.enabled_mode,
        "detail": "Tool enabled in guarded mode.",
    }


@router.post("/tools/proposals/{proposal_id}/reject")
async def reject_tool(
    proposal_id: int,
    db: Session = Depends(get_db),
):
    """Reject a tool proposal."""
    from backend.services.tool_authoring import (
        get_proposed_tool,
        reject_tool_proposal,
    )

    proposal = get_proposed_tool(db, proposal_id)
    if not proposal:
        raise HTTPException(status_code=404, detail=f"Proposal {proposal_id} not found")
    try:
        rejected = reject_tool_proposal(db, proposal)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc))
    return {
        "proposal_id": rejected.id,
        "name": rejected.name,
        "status": rejected.status,
    }
