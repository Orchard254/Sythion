"""
FastAPI application for Sythion (Synth) backend.
"""

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import sys
import uuid

# Ensure the backend directory is importable so bare `tools.*` and
# `sythion_backend.*` imports inside tool/service modules resolve regardless
# of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from .core.config import settings, generate_control_token
from .core.logging_config import configure_logging
from .core.db_manager import init_database, DatabaseManager
from .models.database import set_db_manager, get_db_manager
from .core.auth import LocalControlBoundaryMiddleware, control_origins
from .api.schemas import HealthResponse, ErrorResponse
from .api.routes import router as api_router
from .tools.executor import ToolExecutor
from .tools.system_tools import create_system_tools

import logging

logger = logging.getLogger(__name__)
configure_logging()


# Global instances
db_manager: DatabaseManager = None
tool_executor: ToolExecutor = None
control_token: str = None
profile_lock = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global db_manager, tool_executor, control_token, profile_lock

    # Enforce single-instance: another live sidecar means this launch should
    # exit cleanly rather than run two runners against one profile.
    from .core.profile_lock import acquire_profile_lock
    profile_lock = acquire_profile_lock()
    if profile_lock is None:
        raise RuntimeError(
            "Another Sythion (Synth) sidecar holds the profile lock; refusing to start a second instance"
        )
    
    # Startup: prefer the launcher-provided per-launch token (Phase A handshake),
    # falling back to a fresh random one for dev/test.
    from .core.handshake import read_launch_token, write_handshake
    handshake_token = read_launch_token()
    control_token = handshake_token or generate_control_token()
    logger.info(f"Sythion (Synth) starting with control token: {control_token[:8]}...")

    # Validate critical configuration early so misconfig is obvious at boot
    from .core.config import validate_startup_config
    for level, message in validate_startup_config():
        getattr(logger, level)(message)
    
    # Initialize database
    from .core.storage import database_path, data_dir, migrate_legacy_data_dir
    migrate_legacy_data_dir()
    db_path = settings.database_path or database_path()
    db_manager = init_database(db_path)
    set_db_manager(db_manager)
    logger.info(f"Database initialized at: {db_path}")

    # Phase 4.5: single authoritative startup ordering (task/lease recovery →
    # goal migration → agent init → wake recovery). The wake worker starts
    # only AFTER this sequence, so no wake ever executes against unrecovered
    # state. Failures are logged, never fatal to startup.
    try:
        from .services.bootstrap import run_startup_sequence

        boot = run_startup_sequence(db_manager)
        logger.info("Startup sequence order: %s", boot["order"])
    except Exception as exc:
        logger.warning(f"Startup sequence skipped due to error: {exc}")

    # Phase 4: persistent Main Agent identity + event-driven wake worker.
    # The worker is ALWAYS started (so policy toggles apply live), but it can
    # only execute background sessions when the operator explicitly enabled
    # them (settings_store: background_orchestration_enabled, default OFF).
    agent_service = None
    wake_worker = None
    try:
        from .services.main_agent import MainAgentService
        from .services.wake_worker import WakeWorker, set_wake_worker

        agent_session = db_manager.new_session()
        try:
            agent_service = MainAgentService(agent_session)
            main_agent = agent_service.get_or_create()
            logger.info("Main agent %s state=%s", main_agent.agent_id, main_agent.state)
        finally:
            agent_session.close()

        wake_worker = WakeWorker(db_manager)
        set_wake_worker(wake_worker)
        wake_worker.start()

        if agent_service.background_enabled():
            # Explicit-policy startup wake: once per process boot. Drain any
            # durable PENDING wakes recovered by the bootstrap sequence too.
            fresh = db_manager.new_session()
            try:
                svc = MainAgentService(fresh)
                wake_worker.submit_startup(svc.get().agent_id)
            finally:
                fresh.close()
            await wake_worker.drain_durable()
    except Exception as exc:
        logger.warning(f"Main agent initialization skipped due to error: {exc}")
    
    # Initialize tool executor
    tool_executor = ToolExecutor()
    
    # Register all tools through the single shared factory so the executor
    # used by the API/routes (and thus the model) always matches this one.
    from .tools.registry import register_all_tools
    register_all_tools(tool_executor)

    # PROTECTED RUNTIME CONTRACT (Phase 0): the core process never executes
    # Python stored in the database or authored by the model. Previously this
    # startup block ran enabled ProposedTool.implementation source with
    # restricted builtins; restricted builtins are not a security boundary,
    # so that path was removed entirely. Approved proposals remain inert
    # review artifacts; executable enablement of custom tools requires a
    # genuine out-of-process isolation boundary (future phase). See
    # docs/PROTECTED_RUNTIME_CONTRACT.md ("Process isolation").
    try:
        from .models.database import ProposedTool
        db_mgr = get_db_manager()
        with db_mgr.get_session() as session:
            enabled_count = (
                session.query(ProposedTool).filter_by(status="enabled").count()
            )
        if enabled_count:
            logger.warning(
                "%d approved tool proposal(s) exist but are NOT loaded or "
                "executed: in-process execution of database-stored tool code "
                "is disabled by the protected runtime contract.",
                enabled_count,
            )
    except Exception as e:
        logger.warning("Could not inspect proposed tools at startup: %s", e)
        
    logger.info(f"Registered {len(tool_executor.list_tools())} tools")

    # Expose the fully-loaded executor to the API layer through the explicit
    # dependency wiring (composition root) instead of function-attribute
    # injection. The model engine and skill engine share this same tool set.
    from .api import dependencies as api_dependencies
    api_dependencies.set_executor_provider(lambda: tool_executor)

    # Phase A handshake: expose the bound port + token to the launcher. The
    # launcher pre-selects a free port (`SYTHION_PORT` or --port) and reads this
    # file to learn where the sidecar is listening.
    from .core.handshake import write_handshake
    try:
        write_handshake(port=settings.port, token=control_token)
        logger.info(f"Handshake written for port {settings.port}")
    except Exception as e:  # pragma: no cover - filesystem dependent
        logger.warning(f"Could not write handshake file: {e}")

    yield

    # Shutdown
    logger.info("Sythion (Synth) shutting down...")

    # Phase 4: stop accepting new background work and persist agent shutdown.
    # Unfinished goals/tasks are NOT marked done — the existing lease/recovery
    # machinery remains authoritative for interrupted work.
    try:
        from .services.wake_worker import get_wake_worker, set_wake_worker
        from .services.main_agent import MainAgentService

        worker = get_wake_worker()
        if worker is not None:
            await worker.stop()
            set_wake_worker(None)
    except Exception as exc:
        logger.warning(f" Wake worker shutdown skipped: {exc}")
    try:
        if db_manager is not None:
            session = db_manager.new_session()
            try:
                MainAgentService(session).set_state(
                    "shutdown", reason="process_shutdown")
            finally:
                session.close()
    except Exception as exc:
        logger.warning(f"Main agent shutdown state not persisted: {exc}")

    if profile_lock is not None:
        profile_lock.release()
        profile_lock = None


app = FastAPI(
    title="Sythion (Synth) Backend",
    description="Trusted Windows 10/11 runtime core API for a larger intelligence system",
    version="0.1.0",
    lifespan=lifespan
)

app.add_middleware(
    LocalControlBoundaryMiddleware,
    token_provider=lambda: control_token,
    allowed_origins=control_origins(settings.development_mode),
)

# Include API routes
app.include_router(api_router, prefix="/api/v1")

from backend.api.browser_bridge_routes import router as browser_router
app.include_router(browser_router)

from backend.api.learning_routes import router as learning_router
app.include_router(learning_router, prefix="/api/v1")


# CORS middleware - only allow local origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(control_origins(settings.development_mode)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (legacy, moved to /api/v1/health)."""
    from .core.secrets import resolve_ollama_api_key
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        database_connected=db_manager is not None,
        cloud_configured=bool(resolve_ollama_api_key(settings.ollama_api_key))
    )


# Legacy endpoints - moved to /api/v1/*
@app.get("/tasks")
async def list_tasks_legacy():
    """Legacy endpoint - use /api/v1/tasks instead."""
    return {"message": "Use /api/v1/tasks"}


@app.get("/tools")
async def list_tools_legacy():
    """Legacy endpoint - use /api/v1/tools instead."""
    if tool_executor:
        return {"tools": tool_executor.list_tools()}
    return {"tools": []}


@app.exception_handler(Exception)
async def global_exception_handler(request: Request, exc: Exception):
    """Global exception handler."""
    from fastapi.responses import JSONResponse
    return JSONResponse(
        status_code=500,
        content=ErrorResponse(
            error="INTERNAL_ERROR",
            detail=str(exc),
            code="500"
        ).model_dump()
    )


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(
        "backend.main:app",
        host=settings.host,
        port=settings.port,
        reload=True
    )
