"""
FastAPI application for JARVIS backend.
"""

from fastapi import FastAPI, Request, Depends
from fastapi.middleware.cors import CORSMiddleware
from contextlib import asynccontextmanager
import os
import sys
import uuid

# Ensure the backend directory is importable so bare `tools.*` and
# `jarvis_backend.*` imports inside tool/service modules resolve regardless
# of the current working directory.
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from .core.config import settings, generate_control_token
from .core.logging_config import configure_logging
from .core.db_manager import init_database, DatabaseManager
from .models.database import set_db_manager
from .core.auth import LocalControlBoundaryMiddleware, control_origins, create_auth_dependency
from .api.schemas import HealthResponse, ErrorResponse
from .api.routes import router as api_router
from .tools.executor import ToolExecutor
from .tools.system_tools import create_system_tools
from .services.skills_system import initialize_default_skills

import logging

logger = logging.getLogger(__name__)
configure_logging()


# Global instances
db_manager: DatabaseManager = None
tool_executor: ToolExecutor = None
control_token: str = None


@asynccontextmanager
async def lifespan(app: FastAPI):
    """Application lifespan manager."""
    global db_manager, tool_executor, control_token
    
    # Startup
    control_token = generate_control_token()
    logger.info(f"JARVIS starting with control token: {control_token[:8]}...")

    # Validate critical configuration early so misconfig is obvious at boot
    from .core.config import validate_startup_config
    for level, message in validate_startup_config():
        getattr(logger, level)(message)
    
    # Initialize database
    db_path = settings.database_path or os.path.join(
        os.path.expanduser("~"),
        ".jarvis",
        "jarvis.db"
    )
    db_manager = init_database(db_path)
    set_db_manager(db_manager)
    logger.info(f"Database initialized at: {db_path}")
    
    # Initialize tool executor
    tool_executor = ToolExecutor()
    
    # Register all tools
    from .tools.system_tools import create_system_tools
    from .tools.file_tools import create_file_tools
    from .tools.app_tools import create_app_tools
    from .tools.package_tools import create_package_tools
    
    for tool in create_system_tools():
        tool_executor.register_tool(tool)
    for tool in create_file_tools():
        tool_executor.register_tool(tool)
    for tool in create_app_tools():
        tool_executor.register_tool(tool)
    for tool in create_package_tools():
        tool_executor.register_tool(tool)
        
    logger.info(f"Registered {len(tool_executor.list_tools())} tools")
    
    # Initialize skills system
    initialize_default_skills()
    logger.info("Skills system initialized")
    
    yield
    
    # Shutdown
    logger.info("JARVIS shutting down...")


app = FastAPI(
    title="JARVIS Backend",
    description="Windows 10/11 Personal Desktop Assistant API",
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

# CORS middleware - only allow local origin
app.add_middleware(
    CORSMiddleware,
    allow_origins=list(control_origins(settings.development_mode)),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def get_auth():
    """Get authentication dependency."""
    return create_auth_dependency(control_token)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (legacy, moved to /api/v1/health)."""
    return HealthResponse(
        status="healthy",
        version="0.1.0",
        database_connected=db_manager is not None,
        cloud_configured=bool(settings.ollama_api_key)
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
