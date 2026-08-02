"""
API Routes for JARVIS Backend
Handles task management, tool execution, settings, and skills.
"""
import logging
import asyncio
import json
import os
from datetime import datetime
from typing import List, Optional, Dict, Any
from fastapi import APIRouter, Depends, HTTPException, Request, BackgroundTasks
from sqlalchemy.orm import Session

from backend.models.database import get_db
from backend.services.task_runner import get_runner, TaskRunner
from backend.services.skills_system import get_registry, initialize_default_skills
from backend.services.task_execution_loop import run_task
from backend.adapters.ollama_adapter import get_adapter, format_tools_for_ollama
from backend.tools.executor import ToolExecutor
from backend.api.schemas import (
    TaskCreate, TaskResponse, TaskListResponse, TaskReply,
    TaskState, HealthResponse, SettingsResponse,
    ToolExecutionRequest, ToolExecutionResponse,
    VoiceStartResponse, VoiceAudioInput,
    SecretWriteRequest, SecretStatusResponse, SecretsListResponse,
    ModelSelectRequest, ModelSelectResponse, OnboardingStatusResponse,
)

logger = logging.getLogger(__name__)

router = APIRouter()


def get_tool_executor() -> ToolExecutor:
    """Get or create tool executor instance."""
    if not hasattr(get_tool_executor, '_executor'):
        get_tool_executor._executor = ToolExecutor()
        # Register all tools
        from backend.tools.system_tools import create_system_tools
        from backend.tools.file_tools import create_file_tools
        from backend.tools.app_tools import create_app_tools
        from backend.tools.package_tools import create_package_tools
        
        for tool in create_system_tools():
            get_tool_executor._executor.register_tool(tool)
        for tool in create_file_tools():
            get_tool_executor._executor.register_tool(tool)
        for tool in create_app_tools():
            get_tool_executor._executor.register_tool(tool)
        for tool in create_package_tools():
            get_tool_executor._executor.register_tool(tool)
            
    return get_tool_executor._executor


@router.get("/health", response_model=HealthResponse)
async def health_check(db: Session = Depends(get_db)):
    """Check system health status."""
    from core.config import settings
    
    # Check Ollama Cloud connectivity
    adapter = get_adapter()
    cloud_ok = await adapter.check_health() if adapter else False
    
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        database_connected=True,
        cloud_configured=cloud_ok
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
                final_result=task.final_result,
                terminal_reason=task.terminal_reason
            )

        # Initialize skills registry if not done
        registry = get_registry()
        if len(registry.list_skills()) == 0:
            initialize_default_skills()
        
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
                "skills_available": len(registry.list_skills()),
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
            version=task.version
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
        final_result=task.final_result,
        terminal_reason=task.terminal_reason
    )


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
            version=updated_task.version
        )
        
    finally:
        await runner.release_lease()


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
        
        # Only interrupted tasks can be resumed
        if task.state != TaskState.INTERRUPTED.value:
            raise HTTPException(
                status_code=400,
                detail=f"Cannot resume task in state {task.state}. Only interrupted tasks can be resumed."
            )
        
        # Transition back to running
        updated_task = await runner.transition_state(
            task_id=task_id,
            new_state=TaskState.RUNNING,
            event_type="task.resumed",
            event_data={
                "resumed_at": datetime.utcnow().isoformat(),
                "note": "Task resumed after interruption. Will re-observe before continuing."
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
async def list_skills(category: Optional[str] = None):
    """List all available skills."""
    registry = get_registry()
    
    # Initialize if empty
    if len(registry.list_skills()) == 0:
        initialize_default_skills()
    
    if category:
        from backend.services.skills_system import SkillCategory
        try:
            cat = SkillCategory(category)
            skills = registry.list_skills(category=cat)
        except ValueError:
            raise HTTPException(status_code=400, detail=f"Invalid category: {category}")
    else:
        skills = registry.list_skills()
    
    return [
        {
            "id": s.id,
            "name": s.name,
            "description": s.description,
            "category": s.category.value,
            "parameters": [
                {
                    "name": p.name,
                    "type": p.type,
                    "description": p.description,
                    "required": p.required,
                    "default": p.default
                }
                for p in s.parameters
            ],
            "risk_level": s.risk_level,
            "requires_confirmation": s.requires_confirmation
        }
        for s in skills
    ]


@router.get("/skills/{skill_id}")
async def get_skill(skill_id: str):
    """Get a specific skill definition."""
    registry = get_registry()
    skill = registry.get(skill_id)
    
    if not skill:
        raise HTTPException(status_code=404, detail=f"Skill {skill_id} not found")
    
    d = skill.definition
    return {
        "id": d.id,
        "name": d.name,
        "description": d.description,
        "category": d.category.value,
        "parameters": [
            {
                "name": p.name,
                "type": p.type,
                "description": p.description,
                "required": p.required,
                "default": p.default,
                "enum": p.enum
            }
            for p in d.parameters
        ],
        "returns": d.returns,
        "examples": d.examples,
        "risk_level": d.risk_level,
        "requires_confirmation": d.requires_confirmation
    }


@router.get("/settings", response_model=SettingsResponse)
async def get_settings():
    """Get current application settings."""
    from backend.core.config import settings
    
    return SettingsResponse(
        host=settings.host,
        port=settings.port,
        ollama_cloud_base_url=settings.ollama_cloud_base_url,
        selected_model=settings.selected_model,
        file_roots=settings.file_roots,
        max_actions_per_task=settings.max_actions_per_task,
        max_cloud_requests_per_task=settings.max_cloud_requests_per_task,
        voice_enabled=settings.voice_enabled
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
async def update_settings(request: Request):
    """Update application settings."""
    # Note: In production, settings should be persisted to a config file
    # For now, this is a stub that acknowledges the request
    data = await request.json()
    
    # Validate and update settings would go here
    # For security, most settings should only be configurable via Tauri frontend
    
    return {
        "message": "Settings update acknowledged",
        "updated_keys": list(data.keys()),
        "note": "Some settings require application restart"
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

@router.post("/voice/start", response_model=VoiceStartResponse)
async def start_voice_session():
    """Start a new voice processing session."""
    from backend.jarvis_backend.services.voice_service import get_voice_processor, VoiceConfig
    
    config = VoiceConfig(
        sample_rate=16000,
        channels=1,
        vad_sensitivity=0.5,
        silence_duration_ms=1000
    )
    
    processor = get_voice_processor(config, use_windows_audio=False)
    session_id = await processor.start_voice_session()
    
    return VoiceStartResponse(
        session_id=session_id,
        status="listening"
    )


@router.post("/voice/{session_id}/audio")
async def process_voice_audio(session_id: str, audio_input: VoiceAudioInput):
    """Process audio frame from voice session."""
    from backend.jarvis_backend.services.voice_service import get_voice_processor, VoiceConfig
    import base64
    
    # In production, maintain session state in memory/redis
    config = VoiceConfig(
        sample_rate=audio_input.sample_rate,
        channels=audio_input.channels
    )
    
    processor = get_voice_processor(config, use_windows_audio=False)
    
    # Decode base64 audio data
    try:
        audio_data = base64.b64decode(audio_input.audio_data)
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Invalid audio data: {str(e)}")
    
    # Process audio and get transcription
    text = await processor.process_voice_input(audio_data)
    
    return {
        "session_id": session_id,
        "vad_state": processor.get_vad_state(),
        "transcription": text or "",
        "is_speaking": processor.get_vad_state() == "speaking"
    }


@router.post("/voice/{session_id}/end")
async def end_voice_session(session_id: str):
    """End a voice session."""
    from backend.jarvis_backend.services.voice_service import get_voice_processor, VoiceConfig
    
    config = VoiceConfig()
    processor = get_voice_processor(config, use_windows_audio=False)
    await processor.end_voice_session(session_id)
    
    return {"session_id": session_id, "status": "ended"}


@router.post("/voice/speak")
async def speak_text(request: Request):
    """Convert text to speech."""
    from backend.jarvis_backend.services.voice_service import get_voice_processor, VoiceConfig
    import json
    
    data = await request.json()
    text = data.get("text", "")
    
    if not text:
        raise HTTPException(status_code=400, detail="Text is required")
    
    config = VoiceConfig()
    processor = get_voice_processor(config, use_windows_audio=False)
    
    success = await processor.speak_text(text)
    
    return {
        "success": success,
        "text": text[:100] + "..." if len(text) > 100 else text
    }
