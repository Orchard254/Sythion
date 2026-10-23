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
from starlette.concurrency import run_in_threadpool
from sqlalchemy.orm import Session

from backend.models.database import get_db, Objective, PlanStep, Skill, Task
from backend.services.task_runner import get_runner, TaskRunner
from backend.services.task_execution_loop import run_task
from backend.adapters.ollama_adapter import format_tools_for_ollama
from backend.api.dependencies import get_tool_executor as _get_shared_executor
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
    GoalCreateRequest, GoalReplanRequest,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_tool_executor() -> ToolExecutor:
    """Get the shared runtime ToolExecutor via the explicit dependency wiring.

    Phase 1: the application lifespan installs the single shared executor
    through ``api.dependencies.set_executor_provider``; there is no
    function-attribute injection anymore. Without a provider (tests/dev),
    an identically-registered executor is built on demand.
    """
    return _get_shared_executor()


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
    from backend.core.config import resolve_selected_model

    # Provider health via the neutral facade (Phase 5)
    from backend.runtime.model_provider import get_default_provider, ModelHealth as _MH
    _h = get_default_provider().health()
    cloud_ok = _h.status == _MH.HEALTHY
    
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
    from backend.core.config import settings

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
    
    # Idempotent replay: if a task with this idempotency key already exists, return it immediately
    if task_data.idempotency_key:
        existing_task = db.query(Task).filter_by(idempotency_key=task_data.idempotency_key).first()
        if existing_task:
            return TaskResponse(
                id=existing_task.id,
                original_request=existing_task.original_request,
                state=TaskState(existing_task.state),
                created_at=existing_task.created_at,
                updated_at=existing_task.updated_at,
                version=existing_task.version,
                objective_id=existing_task.objective_id,
                final_result=existing_task.final_result,
                terminal_reason=existing_task.terminal_reason,
            )

    # Phase 13C: Extract conversation context from recent turns if available
    conv_context: Dict[str, Any] = {}
    try:
        from backend.models.database import ConversationTurn
        from backend.core.session import get_current_session_id
        session_id = get_current_session_id()
        recent_turns = (
            db.query(ConversationTurn)
            .filter_by(session_id=session_id)
            .order_by(ConversationTurn.created_at.desc())
            .limit(4)
            .all()
        )
        if recent_turns:
            conv_context["in_conversation"] = True
            conv_context["recent_turns"] = [
                {"role": t.role, "summary": t.summary} for t in reversed(recent_turns)
            ]
            all_text = " ".join((t.summary or "") for t in recent_turns).lower()
            if any(w in all_text for w in ("laptop", "battery", "pc", "computer", "system", "file", "folder")):
                conv_context["has_target_entity"] = True
    except Exception as exc:
        logger.debug("Could not load conversation context for intent interpretation: %s", exc)

    from backend.cognitive.intent import IntentInterpreter
    from backend.cognitive.state import InteractionType

    interpreter = IntentInterpreter()
    intent = interpreter.interpret(task_data.original_request, context=conv_context)
    interaction_type = interpreter.determine_interaction(task_data.original_request, intent=intent, context=conv_context)
    
    # Phase 13B: CONVERSATION does NOT create a Task
    if interaction_type == InteractionType.CONVERSATION:
        logger.info("Routing to ConversationRuntime (no task creation)")
        from backend.services.conversation_runtime import ConversationRuntime, ConversationModelError
        
        runtime = ConversationRuntime(db)
        try:
            result = await runtime.handle_conversation(task_data.original_request)
        except ConversationModelError as exc:
            logger.error(f"Conversation model provider failure: {exc}")
            raise HTTPException(
                status_code=503,
                detail=f"Conversation model provider unavailable: {str(exc)}"
            )
        except Exception as exc:
            logger.error(f"Conversation runtime failure: {exc}", exc_info=True)
            raise HTTPException(
                status_code=500,
                detail=f"Conversation runtime failure: {str(exc)}"
            )
        
        # Return conversation response with explicit interaction contract
        conv_id = f"conv-{uuid.uuid4()}"
        return TaskResponse(
            id=conv_id,
            conversation_id=conv_id,
            interaction_type="CONVERSATION",
            original_request=task_data.original_request,
            state=TaskState.COMPLETED,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
            version=1,
            objective_id=None,
            final_result=result["response"],
            response=result["response"],
            terminal_reason=None,
        )
    
    # For non-conversation requests, proceed with normal task execution
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

        # PHASE 8.75 / Phase 13A: full cognitive gate for FRESHLY created tasks.
        # Determines interaction boundary (CONVERSATION vs INFORMATIONAL vs ACTION vs GOAL)
        # before any goal/task materialization.
        # Note: CONVERSATION is already handled above, so this won't see it.
        routing = None
        if task.state == TaskState.CREATED.value:
            from backend.services.cognitive_runtime import CognitiveDecisionAdapter
            from backend.cognitive.state import DecisionType

            adapter = CognitiveDecisionAdapter(runner, db)
            routing = await adapter.route_new_input(task.id, task.original_request)

            # clarify/park: the cognitive decision handed control back to the
            # user (ambiguity, permission, or no-viable-action). The task is
            # already parked durably in WAITING_FOR_USER by the adapter via
            # the TaskRunner lifecycle; return without starting execution.
            if routing.mode in ("clarify", "park"):
                logger.info("Task %s held by cognitive decision (mode=%s, "
                            "decision=%s, interaction=%s)",
                            task.id, routing.mode,
                            getattr(routing.decision, "decision_type", None),
                            getattr(routing, "interaction_type", None))
                return TaskResponse(
                    id=task.id,
                    original_request=task.original_request,
                    state=TaskState(task.state),
                    created_at=task.created_at,
                    updated_at=task.updated_at,
                    version=task.version,
                    objective_id=task.objective_id,
                )

            # PHASE 9: bounded specialist delegation. The cognitive layer
            # decided DELEGATE; the adapter runs propose→validate→assign→
            # run(kernel)→verify→integrate and completes the parent task ONLY
            # on an ACCEPTED verified result.
            if routing.mode == "delegate":
                logger.info("Task %s delegated to a bounded specialist",
                            task.id)
                await adapter.run_delegate_decision(
                    task.id, task.original_request)
                completed = await runner.get_task(task.id)
                return TaskResponse(
                    id=task.id,
                    original_request=task.original_request,
                    state=TaskState(completed.state),
                    created_at=completed.created_at,
                    updated_at=completed.updated_at,
                    version=completed.version,
                    objective_id=completed.objective_id,
                    final_result=getattr(completed, "final_result", None),
                    terminal_reason=getattr(completed, "terminal_reason", None),
                )

            # Phase 13A/13B: Attach objective ONLY if this is a genuine GOAL request.
            # Ordinary conversation, informational questions, and direct single actions
            # must NOT create fake Goal/Plan/PlanStep projections.
            # Note: CONVERSATION is handled separately and doesn't create tasks, so won't reach here.
            from backend.cognitive.state import InteractionType as IT_Check
            if routing and (routing.interaction_type == IT_Check.GOAL or (routing.decision and getattr(routing.decision, "decision_type", None) in (DecisionType.CREATE_GOAL, DecisionType.UPDATE_GOAL))):
                try:
                    from backend.services.work_state import ensure_task_objective
                    ensure_task_objective(db, task)
                except Exception as exc:
                    logger.warning("Goal objective attachment failed: %s", exc)

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
        
        logger.info(f"Task {task.id} created and execution started in background (objective={task.objective_id})")
        
        return TaskResponse(
            id=task.id,
            original_request=task.original_request,
            state=TaskState.RUNNING,
            created_at=task.created_at,
            updated_at=task.updated_at,
            version=task.version,
            objective_id=task.objective_id,
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

        # PHASE 8.5: merge the clarification answer into the task's goal so
        # resumed execution continues the ORIGINAL intent (multi-turn
        # continuity) instead of starting an unrelated task.
        from backend.services.cognitive_runtime import CognitiveDecisionAdapter

        await CognitiveDecisionAdapter(runner, db).apply_clarification_answer(
            task_id, reply_data.answer)

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

    Phase 1: reads flow through the runtime EventLedger interface.
    """
    from backend.runtime.events import DbEventLedger

    events = DbEventLedger(db).events_after(task_id, after_sequence)

    return {
        "task_id": task_id,
        "after_sequence": after_sequence,
        "events": [
            {
                "event_type": e["event_type"],
                "event_data": e["event_data"],
                "sequence_num": e["sequence_num"],
                "created_at": e["created_at"],
            }
            for e in events
        ]
    }


def _decode_event_data(raw: Any) -> Dict[str, Any]:
    """Deprecated shim kept for import compatibility (see runtime.events)."""
    from backend.runtime.events import decode_event_data

    return decode_event_data(raw)


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

    PHASE 3.5 SUNSET ADAPTER: creation now flows through the canonical
    GoalManager (+ Planner when steps exist) so API-created objectives are
    plan-backed from birth. Response shape is preserved. Status vocabulary
    for steps is the canonical one (pending/ready/blocked/...).
    """
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import CandidateStepModel, Planner
    from backend.services.work_state import decompose_request

    manager = GoalManager(db)
    goal = manager.create_goal(
        title=payload.title.strip(),
        purpose=payload.purpose.strip() if payload.purpose else None,
        priority=payload.priority,
        parent_objective_id=(
            payload.parent_objective_id.strip()
            if payload.parent_objective_id else None
        ),
    )

    # Determine step descriptions (explicit plan_steps win; else deterministic
    # decomposition of purpose; else none).
    if payload.plan_steps:
        descriptions = [s.description if hasattr(s, "description") else str(s)
                        for s in payload.plan_steps]
    elif payload.purpose:
        descriptions = decompose_request(payload.purpose) or [payload.purpose.strip()]
    else:
        descriptions = []

    if descriptions:
        candidates = [
            CandidateStepModel(key=f"step_{idx}", description=desc)
            for idx, desc in enumerate(descriptions, start=1)
        ]
        Planner(db).create_plan(goal.id, candidates, source="user",
                                reason="created via legacy-compatible /objectives API")

    db.refresh(goal)
    return _objective_to_response(goal)


@router.get("/objectives/{objective_id}", response_model=ObjectiveResponse)
async def get_objective(objective_id: str, db: Session = Depends(get_db)):
    """Get one durable objective and its plan."""
    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")
    return _objective_to_response(objective)


@router.patch("/objectives/{objective_id}", response_model=ObjectiveResponse)
async def update_objective(objective_id: str, payload: ObjectiveUpdate, db: Session = Depends(get_db)):
    """Update mutable durable-objective fields.

    Phase 2: ``status`` is no longer writable directly â€” lifecycle changes
    are validated by the GoalManager state machine (invalid transitions like
    completed â†’ active are rejected deterministically).
    """
    from backend.services.goal_manager import GoalManager, InvalidGoalTransitionError

    objective = db.query(Objective).filter_by(id=objective_id).first()
    if objective is None:
        raise HTTPException(status_code=404, detail=f"Objective {objective_id} not found")

    data = payload.model_dump(exclude_unset=True)
    requested_status_raw = data.pop("status", None)
    requested_status = (
        requested_status_raw.value
        if hasattr(requested_status_raw, "value")
        else requested_status_raw
    )
    for key, value in data.items():
        if hasattr(value, "value"):
            value = value.value
        setattr(objective, key, value)
    objective.updated_at = datetime.utcnow()
    db.commit()
    db.refresh(objective)

    if requested_status is not None:
        manager = GoalManager(db)
        try:
            objective = _apply_goal_status_change(
                manager, objective_id, str(requested_status)
            )
        except InvalidGoalTransitionError as exc:
            raise HTTPException(status_code=409, detail=str(exc))
        except ValueError as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    return _objective_to_response(objective)


def _apply_goal_status_change(manager, goal_id: str, requested: str):
    """Map a requested goal status onto the GoalManager state machine.

    'completed' is only accepted through complete_goal's deterministic
    eligibility rules; API callers can never forge it.
    """
    if requested == "paused":
        return manager.pause_goal(goal_id)
    if requested == "active":
        return manager.resume_goal(goal_id)
    if requested == "abandoned":
        return manager.abandon_goal(goal_id)
    if requested == "failed":
        return manager.fail_goal(goal_id, reason="requested_via_api")
    if requested == "completed":
        return manager.complete_goal(goal_id)
    raise ValueError(f"Unknown goal status: {requested}")


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


# --- Goals & Planning (Phase 2) -----------------------------------------------


def _goal_detail(db: Session, goal_id: str) -> Dict[str, Any]:
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import Planner

    manager = GoalManager(db)
    goal = manager.get_goal(goal_id)
    if goal is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    planner = Planner(db)
    plan = planner.active_plan(goal_id)
    steps = planner.plan_steps(plan.id) if plan else []
    return {
        "goal": {
            "id": goal.id,
            "title": goal.title,
            "purpose": goal.purpose,
            "status": goal.status,
            "priority": goal.priority,
            "current_summary": goal.current_summary,
            "completion_criteria": goal.completion_criteria,
            "criteria_verified": goal.criteria_verified,
        },
        "progress": manager.progress(goal_id),
        "active_plan": {
            "id": plan.id,
            "version": plan.version,
            "status": plan.status,
            "source": plan.source,
            "reason": plan.reason,
            "steps": [
                {
                    "id": s.id,
                    "step_key": s.step_key,
                    "step_order": s.step_order,
                    "description": s.description,
                    "status": s.status,
                    "priority": s.priority,
                    "depends_on": json.loads(s.depends_on) if s.depends_on else [],
                    "attempt_count": s.attempt_count,
                    "linked_task_id": s.linked_task_id,
                    "evidence_task_id": s.evidence_task_id,
                    "result_summary": s.result_summary,
                    "failure_reason": s.failure_reason,
                }
                for s in steps
            ],
        } if plan else None,
    }


@router.post("/goals")
async def create_goal(payload: GoalCreateRequest, db: Session = Depends(get_db)):
    """Create a durable goal with an optional validated plan.

    Candidate steps are schema-validated AND dependency-validated before any
    persistence; malformed plans are rejected without partial state.
    """
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import CandidateStepModel, PlanValidationError, Planner

    manager = GoalManager(db)
    try:
        goal = manager.create_goal(
            title=payload.title,
            purpose=payload.purpose,
            priority=payload.priority,
            completion_criteria=payload.completion_criteria,
        )
        if payload.steps:
            candidates = [
                CandidateStepModel(
                    key=s.key, description=s.description,
                    priority=s.priority, depends_on=s.depends_on,
                )
                for s in payload.steps
            ]
            Planner(db).create_plan(goal.id, candidates, source="user",
                                    reason="initial plan from goal creation")
    except PlanValidationError as exc:
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return _goal_detail(db, goal.id)


@router.get("/goals")
async def list_goals(status: Optional[str] = None, limit: int = 50,
                     db: Session = Depends(get_db)):
    """List goals with deterministic progress."""
    from backend.services.goal_manager import GoalManager

    manager = GoalManager(db)
    return {
        "goals": [
            {
                "id": g.id,
                "title": g.title,
                "status": g.status,
                "priority": g.priority,
                "progress": manager.progress(g.id),
            }
            for g in manager.list_goals(status=status, limit=limit)
        ],
        "total": len(manager.list_goals(status=status, limit=limit)),
    }


@router.get("/goals/runnable")
async def list_runnable_goals(db: Session = Depends(get_db)):
    """ACTIVE goals with an eligible step â€” scheduler-boundary query only.

    Executes nothing; future schedulers may consume this listing.
    """
    from backend.services.planner import Planner
    from backend.services.task_orchestrator import TaskOrchestrator

    orchestrator = TaskOrchestrator(state_store=None, planner=Planner(db))
    return {"runnable": orchestrator.list_runnable_goals()}


# --- Model evaluation / selection (Phase 6) -----------------------------------


@router.post("/models/registry/{model_id}/evaluate")
async def evaluate_model(model_id: str, request: Request):
    """Owner-triggered deterministic evaluation of ONE model.

    Runs only SAFE fixed-prompt task classes (no tools/files/credentials).
    Unknown or not-yet-implemented task classes are reported honestly.
    """
    from backend.services.model_selection import (
        IMPLEMENTED_TASK_CLASSES,
        NOT_IMPLEMENTED_TASK_CLASSES,
        ModelEvaluator,
    )

    data = await request.json() if request.headers.get("content-length") else {}
    requested = (data or {}).get("task_classes") or sorted(IMPLEMENTED_TASK_CLASSES)
    unknown = [t for t in requested
               if t not in IMPLEMENTED_TASK_CLASSES
               and t not in NOT_IMPLEMENTED_TASK_CLASSES]
    if unknown:
        raise HTTPException(status_code=400,
                            detail={"unknown_task_classes": unknown})

    from backend.models.database import set_db_manager

    session = set_db_manager.__self__ if False else None  # placeholder guard
    from backend.core.db_manager import get_db_manager
    from backend.models.database import set_db_manager

    session = get_db_manager().new_session()
    try:
        evaluator = ModelEvaluator(session)
        result = await evaluator.run(model_id, task_classes=requested)
        skipped_real = [t for t in requested
                        if t in NOT_IMPLEMENTED_TASK_CLASSES]
        result["skipped_not_implemented"] = sorted(
            set(result.get("skipped_not_implemented", [])) | set(skipped_real))
        return result
    finally:
        session.close()


@router.get("/models/registry/{model_id}/evaluations")
async def model_evaluation_history(model_id: str, db: Session = Depends(get_db)):
    from backend.models.database import ModelEvaluation

    rows = (
        db.query(ModelEvaluation)
        .filter_by(model_id=model_id)
        .order_by(ModelEvaluation.id.desc())
        .limit(100)
        .all()
    )
    return {
        "evaluations": [
            {
                "task_class": r.task_class,
                "evaluation_version": r.evaluation_version,
                "score": r.score,
                "passed": r.passed,
                "latency_ms": r.latency_ms,
                "usage_tokens": r.usage_tokens,
                "created_at": r.created_at,
            }
            for r in rows
        ],
        "total": len(rows),
    }


@router.post("/models/select-preview")
async def model_select_preview(request: Request, db: Session = Depends(get_db)):
    """Deterministic selection EXPLANATION without executing anything."""
    from backend.services.model_selection import (
        ModelRequirements,
        ModelSelector,
    )

    data = await request.json() if request.headers.get("content-length") else {}
    requirements = ModelRequirements.from_dict(data or {})
    selector = ModelSelector(db)
    result = selector.select(requirements)
    return result.to_dict()


# --- Main Agent (Phase 4) -----------------------------------------------------


@router.get("/agent")
async def get_agent(db: Session = Depends(get_db)):
    """Persistent Main Agent identity, state, and deterministic self-model."""
    from backend.services.main_agent import MainAgentService

    svc = MainAgentService(db)
    agent = svc.get_or_create()
    model = svc.self_model()
    return {
        "agent_id": agent.agent_id,
        "name": agent.name,
        "state": agent.state,
        **model,
        "last_wake_at": agent.last_wake_at,
        "last_session_outcome": agent.last_session_outcome,
    }


@router.post("/agent/pause")
async def pause_agent(db: Session = Depends(get_db)):
    """Pause the Main Agent: no new background work until resumed.

    Already-running tasks are not destroyed; their outcomes still apply.
    """
    from backend.services.main_agent import MainAgentService

    agent = MainAgentService(db).set_state("paused", reason="owner_requested")
    return {"agent_id": agent.agent_id, "state": agent.state}


@router.post("/agent/resume")
async def resume_agent(db: Session = Depends(get_db)):
    from backend.services.main_agent import MainAgentService

    agent = MainAgentService(db).set_state("active", reason="owner_requested")
    return {"agent_id": agent.agent_id, "state": agent.state}


@router.get("/agent/background")
async def get_background_policy(db: Session = Depends(get_db)):
    """Whether event-driven background orchestration is currently enabled."""
    from backend.core.config import settings
    from backend.services.main_agent import MainAgentService

    svc = MainAgentService(db)
    return {"enabled": svc.background_enabled(),
            "default": bool(settings.background_orchestration_enabled)}


@router.put("/agent/background")
async def set_background_policy(request: Request, db: Session = Depends(get_db)):
    """Operator switch for background orchestration (persisted, audited)."""
    from backend.services.main_agent import MainAgentService
    from backend.runtime.events import DbEventLedger

    data = await request.json()
    enabled = bool(data.get("enabled"))
    svc = MainAgentService(db)
    svc.set_background_enabled(enabled)
    agent = svc.get_or_create()
    DbEventLedger(db).append_event(
        f"agent:{agent.agent_id}",
        "agent.background_policy_changed",
        {"enabled": enabled},
    )
    db.commit()
    return {"enabled": svc.background_enabled()}


@router.get("/agent/activity")
async def agent_activity(limit: int = 20, db: Session = Depends(get_db)):
    """Recent wakes + recent agent events (answers 'what is Sythion doing?')."""
    from backend.services.main_agent import MainAgentService

    svc = MainAgentService(db)
    agent = svc.get_or_create()
    events = list(reversed(svc.ledger.event_history(f"agent:{agent.agent_id}")[-limit:]))
    return {
        "wakes": svc.recent_wakes(limit=limit),
        "events": [
            {
                "event_type": e["event_type"],
                "event_data": e["event_data"],
                "sequence_num": e["sequence_num"],
                "created_at": e["created_at"],
            }
            for e in events
        ],
    }


@router.post("/agent/wake")
async def wake_agent(request: Request, db: Session = Depends(get_db)):
    """Explicit owner-triggered wake (trigger_type='user.request').

    Deduplication and policy apply exactly as for automatic triggers. The
    bounded session runs synchronously in this call when allowed; otherwise
    the skip is recorded and returned.
    """
    import uuid as _uuid

    from backend.core.config import settings
    from backend.services.goal_selector import RunnableGoalSelector
    from backend.services.planner import Planner
    from backend.services.task_orchestrator import TaskOrchestrator
    from backend.services.main_agent import MainAgentService

    data = await request.json() if request.headers.get("content-length") else {}
    trigger_reference = (data or {}).get("reference")
    idempotency_key = (data or {}).get("wake_key")

    svc = MainAgentService(db)
    allowed, reason = svc.may_execute_background()
    wake_key = idempotency_key or f"user.request:{_uuid.uuid4()}"
    if not allowed:
        skipped = svc.skip_wake(
            "user.request", reason,
            trigger_reference=trigger_reference,
            wake_key=wake_key,
        )
        return {"woke": False, "reason": reason,
                "wake_id": skipped["wake"].id}

    # Durable submit + atomic claim (duplicate keys can never re-execute).
    submitted = svc.submit_wake(
        "user.request",
        trigger_reference=trigger_reference,
        wake_key=wake_key,
    )
    if submitted["deduplicated"]:
        return {"woke": False, "reason": "duplicate_wake_key",
                "wake_id": submitted["wake"].id}

    wake = svc.claim_wake(wake_key)
    if wake is None:
        return {"woke": False, "reason": "duplicate_wake_key"}

    absorbed = svc.absorb_coalescable(wake)

    runner = get_runner(db)
    orchestrator = TaskOrchestrator(
        state_store=runner,
        planner=Planner(db),
    )
    selector = RunnableGoalSelector(db)
    result = await orchestrator.run_background_cycle(
        selector, max_steps=settings.max_steps_per_background_cycle)

    outcome = ("completed" if any(e["outcome"] == "completed"
                                  for e in result["executed"])
               else ("executed" if result["executed"] else "no_work"))
    svc.finish_wake(
        wake, status="COMPLETED", outcome=outcome,
        stop_reason=result["stopped_reason"],
        steps_attempted=len(result["executed"]),
        steps_completed=sum(1 for e in result["executed"]
                            if e["outcome"] == "completed"),
    )
    resp = {"woke": True, **result, "wake_id": wake.id}
    if absorbed:
        resp["coalesced"] = absorbed
    return resp


@router.post("/goals/{goal_id}/execute")
async def execute_goal_session(goal_id: str, db: Session = Depends(get_db)):
    """Explicitly initiated goal execution session with automatic continuation.

    Runs eligible steps back-to-back inside this call (bounded by
    ``max_steps_per_goal_execution``), each through the protected runtime
    worker. Stops on: no eligible step, retry exhaustion (goal paused),
    budget bound, or lost lease. This is NOT a scheduler.
    """
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import Planner
    from backend.services.task_orchestrator import TaskOrchestrator

    if GoalManager(db).get_goal(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")

    runner = get_runner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    session_result = await orchestrator.execute_session(goal_id)
    return {**session_result, **_goal_detail(db, goal_id)}


@router.get("/goals/{goal_id}")
async def get_goal(goal_id: str, db: Session = Depends(get_db)):
    """Inspect a goal: state, active plan version, steps, progress."""
    return _goal_detail(db, goal_id)


@router.get("/goals/{goal_id}/plans")
async def list_goal_plans(goal_id: str, db: Session = Depends(get_db)):
    """Plan version history (replanning audit trail; nothing is erased)."""
    from backend.models.database import GoalPlan
    from backend.services.goal_manager import GoalManager

    if GoalManager(db).get_goal(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    plans = (
        db.query(GoalPlan)
        .filter_by(goal_id=goal_id)
        .order_by(GoalPlan.version.desc())
        .all()
    )
    return {
        "plans": [
            {"id": p.id, "version": p.version, "status": p.status,
             "source": p.source, "reason": p.reason, "created_at": p.created_at}
            for p in plans
        ]
    }


@router.post("/goals/{goal_id}/advance")
async def advance_goal(goal_id: str, background_tasks: BackgroundTasks,
                       db: Session = Depends(get_db)):
    """Select the next eligible step deterministically and execute its task.

    Owner-triggered only â€” there is no autonomous scheduler. The task runs
    through the existing protected runtime worker (`run_task`); plan/goal
    state updates afterwards come exclusively from kernel evidence.
    """
    runner = get_runner(db)
    if not await runner.acquire_lease():
        raise HTTPException(status_code=409, detail="Another task runner is currently active")

    try:
        from backend.services.planner import Planner
        from backend.services.task_orchestrator import TaskOrchestrator

        orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
        started = await orchestrator.start_next_step(goal_id)
        if started is None:
            return {
                "advanced": False,
                "detail": "No eligible step (all done, blocked, or no plan).",
                **_goal_detail(db, goal_id),
            }

        task = started["task"]
        await runner.transition_state(
            task_id=task.id,
            new_state=TaskState.RUNNING,
            event_type="task.execution_started",
            event_data={"started_at": datetime.utcnow().isoformat(),
                        "goal_step_key": started["step"].step_key},
        )
    finally:
        await runner.release_lease()

    tool_executor = get_tool_executor()
    background_tasks.add_task(run_task, db, task.id, tool_executor)
    return {"advanced": True, "task_id": task.id, "step_key": started["step"].step_key}


@router.post("/goals/{goal_id}/replan")
async def replan_goal(goal_id: str, payload: GoalReplanRequest,
                      db: Session = Depends(get_db)):
    """Create a NEW validated plan version (owner-triggered).

    Either explicit ``steps`` or ``use_model=true`` (structured reasoning via
    the Phase 1 ModelClient boundary). Malformed proposals are rejected with
    no partial state; previous versions remain fully auditable.
    """
    from backend.runtime.model_client import get_default_model_client
    from backend.services.goal_manager import GoalManager
    from backend.services.planner import CandidateStepModel, PlanValidationError, Planner

    manager = GoalManager(db)
    if manager.get_goal(goal_id) is None:
        raise HTTPException(status_code=404, detail=f"Goal {goal_id} not found")
    planner = Planner(db)

    failure_context = None
    failed_step = next(
        (s for s in planner.goal_steps(goal_id) if s.status == "failed"), None
    )
    if failed_step is not None:
        failure_context = planner.build_failure_context(failed_step.id)

    try:
        if payload.use_model:
            candidates = planner.request_model_plan(
                manager.get_goal(goal_id),
                model_client=get_default_model_client(),
                instruction=payload.instruction or (
                    json.dumps(failure_context)[:1500] if failure_context else None
                ),
            )
            source = "model"
        else:
            candidates = [
                CandidateStepModel(
                    key=s.key, description=s.description,
                    priority=s.priority, depends_on=s.depends_on,
                )
                for s in (payload.steps or [])
            ]
            source = "user"
        plan = planner.replan(
            goal_id,
            reason=payload.reason or ("replanned after failure" if failure_context else "manual replan"),
            candidates=candidates or None,
            source=source,
        )
    except PlanValidationError as exc:
        # Record the rejection for audit; persist NOTHING.
        from backend.runtime.events import DbEventLedger

        DbEventLedger(db).append_event(
            f"goal:{goal_id}", "plan.proposal.rejected", {"errors": exc.errors[:10]}
        )
        db.commit()
        raise HTTPException(status_code=422, detail={"errors": exc.errors})
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return _goal_detail(db, goal_id)


# NOTE: the generic /{action} lifecycle route is registered LAST so it
# can never shadow the specific POST routes above (/advance, /replan).

@router.post("/goals/{goal_id}/{action}")
async def goal_lifecycle_action(goal_id: str, action: str, db: Session = Depends(get_db)):
    """Explicit lifecycle actions: pause | resume | abandon."""
    from backend.services.goal_manager import GoalManager, InvalidGoalTransitionError

    actions = {"pause", "resume", "abandon"}
    if action not in actions:
        raise HTTPException(status_code=404, detail=f"Unknown action '{action}'")

    manager = GoalManager(db)
    try:
        if action == "pause":
            manager.pause_goal(goal_id)
        elif action == "resume":
            manager.resume_goal(goal_id)
        elif action == "abandon":
            manager.abandon_goal(goal_id)
    except InvalidGoalTransitionError as exc:
        raise HTTPException(status_code=409, detail=str(exc))
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc))
    return _goal_detail(db, goal_id)


# --- Capability / Model / Self-Model / Health (Phase 5) -----------------------


@router.get("/capabilities")
async def list_capabilities(include_details: bool = False,
                            db: Session = Depends(get_db)):
    """Deterministic capability registry. Metadata only — never authorization."""
    from backend.services.capabilities import CapabilityRegistry

    registry = CapabilityRegistry(db)
    return {
        "capabilities": registry.list_capabilities(include_details=include_details),
        "consistency_issues": registry.validate(),
        "total": len(registry.list_capabilities()),
    }


@router.get("/capabilities/{capability_id}")
async def get_capability(capability_id: str, db: Session = Depends(get_db)):
    from backend.services.capabilities import CapabilityRegistry

    cap = CapabilityRegistry(db).get_capability(capability_id)
    if cap is None:
        raise HTTPException(status_code=404, detail=f"Unknown capability '{capability_id}'")
    return cap


@router.get("/models/registry")
async def model_registry_listing(probe: bool = False, db: Session = Depends(get_db)):
    """Structured model records with explicit truthfulness metadata."""
    from backend.services.system_state import ModelRegistry

    registry = ModelRegistry(db)
    records = registry.list_models(probe=probe)
    return {
        "models": [
            {
                "model_id": r.model_id,
                "provider_id": r.provider_id,
                "selected": r.selected,
                "available_now": r.available_now,
                "tool_calling_supported": r.tool_calling_supported,
                "evaluated": r.evaluated,
                "health_status": r.health_status,
            }
            for r in records
        ],
        "total": len(records),
        "note": "evaluated=false everywhere: no evaluation system exists yet; "
                "no quality claims are made.",
    }


@router.get("/models/registry/{model_id}")
async def model_registry_detail(model_id: str):
    from backend.services.system_state import ModelRegistry

    record = ModelRegistry(None).get_model(model_id)
    if record is None:
        raise HTTPException(status_code=404,
                            detail=f"Model '{model_id}' not registered")
    return {
        **record.__dict__,
        "capabilities": ModelRegistry(None).model_capabilities(model_id),
    }


@router.get("/agent/self-model")
async def agent_self_model(db: Session = Depends(get_db)):
    """Full deterministic SelfModel (identity/runtime/capabilities/models/
    resources/health). Derived only from authoritative state; no LLM writes."""
    from backend.api.dependencies import get_tool_executor
    from backend.services.self_model import build_self_model

    return build_self_model(db, tool_executor=get_tool_executor())


@router.get("/agent/health")
async def agent_health(include_provider: bool = False,
                       db: Session = Depends(get_db)):
    """Aggregated health checks (remote provider probe is opt-in)."""
    from backend.services.system_state import run_health_checks

    return run_health_checks(include_provider=include_provider, db_session=db)


@router.get("/agents")
async def list_agents(include_expired: bool = False,
                      db: Session = Depends(get_db)):
    """Phase 9 agent registry (Main Agent + specialist instances)."""
    from backend.agents.service import AgentService

    service = AgentService(db)
    return {
        "main": agents_main_payload(service),
        "specialists": service.list_agents(include_expired=include_expired),
        "definitions": [
            {"id": d.id, "role": d.role, "name": d.name, "enabled": d.enabled,
             "capabilities": _safe_json_loads(d.capabilities_json, []),
             "model_requirements": _safe_json_loads(
                 d.model_requirements_json, {})}
            for d in service.ensure_definitions()
        ],
    }


@router.get("/agents/delegations")
async def list_delegations(limit: int = 50, db: Session = Depends(get_db)):
    """Durable delegation records with their lifecycle status."""
    from backend.agents.delegation import DelegationService

    return {"delegations": DelegationService(db).list_delegations(limit)}


@router.get("/agents/delegations/{delegation_id}")
async def get_delegation(delegation_id: str, db: Session = Depends(get_db)):
    """One delegation record incl. structured result and cost accounting."""
    from backend.agents.delegation import DelegationService

    delegation = DelegationService(db).get_delegation(delegation_id)
    if delegation is None:
        raise HTTPException(status_code=404, detail="delegation not found")
    return {
        "id": delegation.id,
        "status": delegation.status,
        "role": delegation.specialist_role,
        "objective": delegation.objective,
        "attempt": delegation.attempt,
        "parent_agent_id": delegation.parent_agent_id,
        "specialist_agent_id": delegation.specialist_agent_id,
        "specialist_task_id": delegation.specialist_task_id,
        "goal_id": delegation.goal_id,
        "task_id": delegation.task_id,
        "result": _safe_json_loads(delegation.result_json, None),
        "integration_outcome": delegation.integration_outcome,
        "failure_reason": delegation.failure_reason,
        "budget": _safe_json_loads(delegation.budget_json, {}),
    }


def agents_main_payload(service) -> Dict[str, Any]:
    row = service.get_or_create_main()
    return {"agent_id": row.id, "type": row.type, "state": row.state}


def _safe_json_loads(raw, default):
    try:
        import json as _json

        return _json.loads(raw) if raw else default
    except Exception:
        return default


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
    from backend.runtime.model_provider import get_default_provider
    provider = get_default_provider()

    try:
        models = await run_in_threadpool(provider.list_models)
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
async def select_model(body: ModelSelectRequest, db=Depends(get_db)):
    """Select one exact authenticated Cloud model ID and persist it.

    The model must be present in the authenticated model list; a stale,
    retyped, or arbitrary ID is rejected so the sidecar never runs against a
    model it cannot use. Only the non-secret model ID is persisted.
    """
    from backend.core.profile import set_selected_model
    from backend.runtime.model_provider import get_default_provider

    from backend.runtime.model_provider import get_default_provider

    provider = get_default_provider()
    # The sync facade bridges into the adapter (provider-specific plumbing
    # stays inside the implementation).
    try:
        models = provider.list_models()
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
    try:
        from backend.core.settings_store import set_setting
        set_setting(db, "ollama_inference_verified_model", "")
        db.commit()
    except Exception:
        pass
    # Refresh the live provider so capability checks use the new selection
    # (provider-specific cache invalidation stays inside the adapter).
    from backend.runtime.model_provider import get_default_provider

    _adapter_obj = get_default_provider()._adapter
    _adapter_obj.selected_model = body.model_id
    _adapter_obj._capability_profile = None
    return ModelSelectResponse(selected_model=body.model_id, persisted=True)


@router.post("/chat/completions")
async def chat_completion(request: Request):
    """Send a chat completion request to the configured provider."""
    from backend.runtime.model_provider import get_default_provider
    from backend.adapters.ollama_adapter import format_tools_for_ollama

    provider = get_default_provider()
    data = await request.json()

    messages = data.get("messages", [])
    tools = data.get("tools", [])

    if not messages:
        raise HTTPException(status_code=400, detail="Messages are required")

    try:
        # Format tools if provided
        formatted_tools = format_tools_for_ollama(tools) if tools else []

        response = await provider.chat(
            messages=messages,
            max_tokens=data.get("max_tokens", 4000),
        )

        return response
    except Exception as e:
        logger.error(f"Chat completion failed: {e}")
        raise HTTPException(status_code=502, detail=f"Ollama Cloud request failed: {str(e)}")


# Voice API Endpoints


@router.post("/onboarding/credentials/test")
async def test_onboarding_credentials(request: Request, db=Depends(get_db)):
    """Validate a candidate Ollama Cloud key without persisting it on failure.

    A known-good key already stored is preserved if validation fails.
    """
    from backend.core.secrets import set_secret, has_secret, OLLAMA_API_KEY, resolve_ollama_api_key
    from backend.core.config import settings
    from backend.adapters.ollama_adapter import get_adapter
    import json

    body = await request.json()
    candidate = (body.get("value") or "").strip() if isinstance(body, dict) else ""
    if not candidate:
        raise HTTPException(status_code=400, detail="API key is required")

    previous = None
    try:
        from backend.core.secrets import _get_store
        previous = _get_store().get(OLLAMA_API_KEY)
    except Exception:
        previous = None

    adapter = None
    try:
        from backend.adapters.ollama_adapter import get_adapter as _ga
        adapter = _ga()
    except Exception:
        adapter = None
    try:
        set_secret(OLLAMA_API_KEY, candidate)
        if adapter is not None:
            adapter.api_key = resolve_ollama_api_key(settings.ollama_api_key)
            adapter._client = None
            adapter._client_loop = None
        # Use sync facade via provider-neutral entry point
        from backend.runtime.model_provider import get_default_provider
        provider = get_default_provider()
        models = provider.list_models()
        if not models:
            raise HTTPException(status_code=503, detail="Authenticated model discovery returned no models")
        # Credential changed: prior inference evidence no longer applies.
        try:
            from backend.core.settings_store import set_setting
            set_setting(db, "ollama_inference_verified_model", "")
            db.commit()
        except Exception:
            pass
        return {"valid": True, "model_count": len(models), "models": [m.get("name") or m.get("model") for m in models if isinstance(m, dict)]}
    except HTTPException:
        # Restore previous credential on validation failure
        _restore_secret(previous)
        raise
    except Exception as exc:
        _restore_secret(previous)
        msg = str(exc)
        if "401" in msg or "Invalid" in msg:
            raise HTTPException(status_code=401, detail="Invalid Ollama Cloud API key")
        if "timed out" in msg.lower() or "network" in msg.lower() or "connection" in msg.lower():
            raise HTTPException(status_code=503, detail=f"Network failure during credential validation: {msg}")
        raise HTTPException(status_code=502, detail=f"Credential validation failed: {msg}")


def _restore_secret(previous):
    try:
        from backend.core.secrets import set_secret, delete_secret, OLLAMA_API_KEY
        if previous:
            set_secret(OLLAMA_API_KEY, previous)
        else:
            try:
                delete_secret(OLLAMA_API_KEY)
            except Exception:
                pass
    except Exception:
        pass
    try:
        from backend.core.config import settings
        from backend.adapters.ollama_adapter import get_adapter
        from backend.core.secrets import resolve_ollama_api_key
        adapter = get_adapter()
        if adapter is not None:
            adapter.api_key = resolve_ollama_api_key(settings.ollama_api_key)
            adapter._client = None
            adapter._client_loop = None
    except Exception:
        pass


def _extract_inference_text(response: Any) -> str:
    """Return the model's text from a provider response, or '' if there is none.

    Order (the FIRST candidate that yields a non-blank string wins):
      1. native Ollama /api/chat      -> response["message"]["content"]
      2. OpenAI-compatible chat       -> response["choices"][0]["message"]["content"]
      3. OpenAI-compatible legacy     -> response["choices"][0]["text"]
      4. legacy Ollama /api/generate  -> response["response"]

    The order matches backend/agents/reasoner.py:310-318 and
    backend/cognitive/reasoning.py:187, which read ``message.content`` first.
    Every access is type-guarded, so a malformed body yields '' instead of
    an exception.
    """

    def _candidate(value: Any) -> str:
        """Return the stripped string, or '' for anything that is not text."""
        if isinstance(value, str):
            return value.strip()
        return ""

    if not isinstance(response, dict):
        return ""

    # 1. native Ollama /api/chat
    message = response.get("message")
    if isinstance(message, dict):
        native = _candidate(message.get("content"))
        if native:
            return native

    # 2 + 3. the OpenAI-compatible shapes
    choices = response.get("choices")
    if isinstance(choices, list) and choices and isinstance(choices[0], dict):
        choice = choices[0]
        choice_message = choice.get("message")
        if isinstance(choice_message, dict):
            chat = _candidate(choice_message.get("content"))
            if chat:
                return chat
        legacy_choice = _candidate(choice.get("text"))
        if legacy_choice:
            return legacy_choice

    # 4. the legacy Ollama /api/generate shape
    return _candidate(response.get("response"))


@router.post("/onboarding/inference")
async def onboarding_inference(request: Request, db=Depends(get_db)):
    """Run a small real inference request against a selected model."""
    from backend.core.config import resolve_selected_model
    from backend.runtime.model_provider import get_default_provider
    import json

    body = {}
    try:
        body = await request.json()
    except Exception:
        body = {}
    model_id = resolve_selected_model()
    if body.get("model_id"):
        model_id = body.get("model_id")
    if not model_id:
        raise HTTPException(status_code=400, detail="No model selected")

    provider = get_default_provider()
    try:
        # Validate model availability
        models = provider.list_models()
        ids = {m.get("name") or m.get("model") for m in models if isinstance(m, dict)}
        if model_id not in ids:
            raise HTTPException(status_code=400, detail=f"Model '{model_id}' is not available")
        resp = await provider.chat(
            messages=[{"role": "user", "content": "Reply with OK"}],
            max_tokens=16,
        )
        text = _extract_inference_text(resp)
        if not str(text).strip():
            raise HTTPException(status_code=502, detail="Inference returned an empty response")
        try:
            from backend.core.settings_store import set_setting
            set_setting(db, "ollama_inference_verified_model", model_id)
            db.commit()
        except Exception:
            pass
        return {"success": True, "model": model_id, "response_preview": str(text).strip()[:80]}
    except HTTPException:
        raise
    except Exception as exc:
        msg = str(exc)
        if "401" in msg or "Invalid" in msg:
            raise HTTPException(status_code=401, detail="Invalid authentication during inference")
        if "timed out" in msg.lower() or "network" in msg.lower() or "connection" in msg.lower():
            raise HTTPException(status_code=503, detail=f"Network failure during inference: {msg}")
        raise HTTPException(status_code=502, detail=f"Inference failed: {msg}")



def _effective_autonomy_is_guarded() -> bool:
    """Return whether runtime-visible effective autonomy is exactly guarded.

    Runtime consumers use ``os.environ.get("SYTHION_AUTONOMY_LEVEL",
    settings.autonomy_level)``; validate that exact effective value here. A DB
    marker or persisted settings row cannot override it. Does not mutate env or
    settings.
    """
    import os

    from backend.core import config as core_config

    value = os.environ.get("SYTHION_AUTONOMY_LEVEL", core_config.settings.autonomy_level)
    return value == "guarded"


@router.post("/setup/safe-settings")
async def setup_safe_settings(db=Depends(get_db)):
    if not _effective_autonomy_is_guarded():
        raise HTTPException(
            status_code=409,
            detail="Runtime autonomy is not guarded; safe setup cannot be applied.",
        )

    from backend.core.settings_store import set_setting

    try:
        set_setting(db, "retention_days", "90")
        set_setting(db, "setup_safe_initial_config_applied", "true")
        db.commit()
    except Exception:
        db.rollback()
        raise HTTPException(
            status_code=500,
            detail="Safe setup could not be saved. Please try again.",
        )
    return {"ok": True}


@router.get("/setup/readiness")
@router.get("/onboarding/readiness")
@router.get("/first-run/readiness")
async def setup_readiness(db=Depends(get_db)):
    """Authoritative first-run readiness based on backend checks and assets."""
    from backend.core.secrets import has_secret, OLLAMA_API_KEY
    from backend.core.config import resolve_selected_model, settings
    from backend.core.voice_assets import asset_status, stt_model_installed, _stt_checksum_verified
    from backend.runtime.model_provider import get_default_provider, ModelHealth

    health = get_default_provider().health()
    key_ok = has_secret(OLLAMA_API_KEY) or bool(settings.ollama_api_key)
    model = resolve_selected_model()
    stt_ready = stt_model_installed() and _stt_checksum_verified()
    tts_voice = asset_status().get("tts_voice") or ""
    safe_settings_ok = False
    try:
        from backend.core.settings_store import get_setting

        safe_settings_ok = (
            get_setting(db, "setup_safe_initial_config_applied") == "true"
            and get_setting(db, "retention_days") == "90"
            and _effective_autonomy_is_guarded()
        )
    except Exception:
        safe_settings_ok = False
    verified_inference_model = None
    try:
        from backend.core.settings_store import get_setting
        verified_inference_model = get_setting(db, "ollama_inference_verified_model")
    except Exception:
        verified_inference_model = None
    inference_ok = bool(model) and verified_inference_model == model
    checks = {
        "resources_ok": True,
        "database_ok": True,
        "sidecar_ok": True,
        "ollama_api_key_ok": key_ok,
        "ollama_health_ok": health.status == ModelHealth.HEALTHY,
        "selected_model_ok": bool(model),
        "inference_verified_ok": inference_ok,
        "stt_model_installed": stt_ready,
        "tts_voice_ok": bool(tts_voice),
        "safe_settings_ok": safe_settings_ok,
    }
    full_ready = all(checks.values())
    return {
        "full_ready": full_ready,
        "text_first_available": True,
        "checks": checks,
        "selected_model": model or "",
        "inference_verified_model": verified_inference_model or "",
        "tts_voice": tts_voice,
    }

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
    """Submit a model-authored tool proposal for validation and review.

    PROTECTED RUNTIME CONTRACT: the proposal is validated with static
    analysis only â€” its implementation is never compiled or executed in this
    process. The stored proposal is an inert review artifact.
    """
    from backend.services.tool_authoring import (
        ToolProposal,
        validate_tool_proposal,
        static_interface_check,
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

    # Static interface check (no execution of proposal code)
    check = static_interface_check(proposal)

    # Store
    tool_proposal = ProposedTool(
        name=proposal.name,
        description=proposal.description,
        input_schema_json=json.dumps(proposal.input_schema),
        implementation=proposal.implementation,
        validation_errors=json.dumps([]),
        sandbox_result=json.dumps(check.model_dump()),
        status="sandbox_passed" if check.passed else "proposed",
    )
    db.add(tool_proposal)
    db.commit()
    db.refresh(tool_proposal)

    return {
        "proposal_id": tool_proposal.id,
        "name": proposal.name,
        "status": tool_proposal.status,
        "validation": validation.model_dump(),
        "static_check": check.model_dump(),
        "detail": "Proposal stored for owner review; implementation is never executed in-process. Approve via POST /tools/proposals/{id}/approve.",
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
    """Record an explicit owner review decision on a tool proposal.

    PROTECTED RUNTIME CONTRACT: approval marks the proposal as reviewed but
    never loads or executes its implementation in this process.
    """
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


