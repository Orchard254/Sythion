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

from .core.config import settings, generate_control_token, effective_development_mode
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
# Captured result of the Phase 4.5 startup sequence so consumers (health /
# readiness surfaces) can report partial startup truthfully instead of
# treating it as fully successful. None until the lifespan actually runs.
startup_boot: dict = None
startup_sequence_status: str = "unknown"


# ---------------------------------------------------------------------------
# Vault-write-violation marker contract
# ---------------------------------------------------------------------------
# An exception is a "vault-write violation" exactly when it exposes a TRUTHY
# attribute of this name. The global exception handler below answers such an
# exception with a distinct 409 instead of the generic 500, so a BLOCKED
# product-credential write is never indistinguishable from an unrelated
# crash.
#
# The producer of the marker is ``ProductCredentialWriteAttempt`` in
# ``backend/tests/conftest.py`` (the test-suite guard that refuses to write to
# or delete the product's own Windows Credential Manager entry). That is a
# documented cross-file agreement on an attribute NAME, not an import in
# either direction: production code must never import from ``backend/tests/``.
VAULT_WRITE_VIOLATION_ATTR = "sythion_vault_write_violation"
VAULT_WRITE_VIOLATION_STATUS = 409
VAULT_WRITE_VIOLATION_ERROR = "VAULT_WRITE_BLOCKED"


def _provision_native_attestation():
    """Consume the host-provisioned verifier key once, then delete the file.

    A missing or malformed key leaves the boundary unconfigured (fail closed).
    The key is never logged or exposed through any API.
    """
    from pathlib import Path

    from .core.storage import data_dir

    path = Path(data_dir()) / "native_attestation.key"
    if not path.is_file():
        return None
    try:
        from .services.recovery_native_challenge import TrustedHostAttestationBoundary

        key = bytes.fromhex(path.read_text(encoding="ascii").strip())
        boundary = TrustedHostAttestationBoundary(lambda: key)
        path.unlink(missing_ok=True)
        return boundary
    except Exception:
        path.unlink(missing_ok=True)
        return None


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
    
    _owned_profile_lock = profile_lock
    try:
        # Startup: prefer the launcher-provided per-launch token (Phase A handshake),
        # falling back to a fresh random one for dev/test.
        from .core.handshake import read_launch_token, write_handshake
        handshake_token = read_launch_token()
        control_token = handshake_token or generate_control_token()
        logger.info("Sythion (Synth) starting with per-launch control token initialized")
        app.state.native_attestation = _provision_native_attestation()

        # Validate critical configuration early so misconfig is obvious at boot
        from .core.config import validate_startup_config
        for level, message in validate_startup_config():
            getattr(logger, level)(message)

        # Initialize database
        from .core.storage import database_path, data_dir, migrate_legacy_data_dir
        # Prototype builds must not run the legacy ~/.sythion migration, not even
        # to probe whether the real legacy path exists.
        try:
            from .core.secrets import (
                is_prototype_build as _is_proto_build,
                secret_fail_closed as _secret_fail_closed,
            )

            # A fail-closed (ambiguous/conflicting) identity is isolated too; never
            # treat ambiguity as production for legacy migration.
            _is_prototype = bool(_is_proto_build()) or bool(_secret_fail_closed())
        except Exception:
            # Fail closed: uncertain prototype identity must never trigger legacy
            # profile migration or access to ambient/production profile data.
            _is_prototype = True
        if not _is_prototype:
            migrate_legacy_data_dir()
        db_path = settings.database_path or database_path()
        db_manager = init_database(db_path, profile_lock=profile_lock)
        set_db_manager(db_manager)
        logger.info(f"Database initialized at: {db_path}")

        # Phase 4.5: single authoritative startup ordering (task/lease recovery →
        # goal migration → agent init → wake recovery). The wake worker starts
        # only AFTER this sequence, so no wake ever executes against unrecovered
        # state. Failures are logged, never fatal to startup. The returned
        # aggregate is captured (not discarded) so health/readiness surfaces can
        # report partial startup truthfully.
        global startup_boot, startup_sequence_status
        boot = None
        try:
            from .services.bootstrap import run_startup_sequence

            boot = run_startup_sequence(db_manager)
            logger.info("Startup sequence order: %s", boot["order"])
        except Exception as exc:
            logger.warning(f"Startup sequence skipped due to error: {exc}")
            boot = {
                "order": [],
                "stage_statuses": {},
                "stages": [],
                "startup_sequence_status": "failed",
                "status": "failed",
                "overall_status": "failed",
                "aggregate_status": "failed",
                "error": str(exc),
            }

        startup_boot = boot
        try:
            app.state.startup_boot = boot
        except Exception:
            pass
        from .services.bootstrap import startup_status_projection

        _startup_projection = startup_status_projection(boot)
        startup_sequence_status = _startup_projection["startup_sequence_status"]
        # Alias expected by older/alternate consumers.
        try:
            app.state.startup_sequence_status = startup_sequence_status
        except Exception:
            pass

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

                    # Owner-gated, bounded TIME-DRIVEN goal wake. The shared
                    # helper creates a timer only when BOTH owner switches are
                    # on (background_orchestration_enabled AND
                    # bounded_wake_enabled). It only submits ordinary durable
                    # wakes; the single policy gate and protected runtime still
                    # execute them.
                    wake_worker.apply_owner_policy(fresh)
                finally:
                    fresh.close()
                await wake_worker.drain_durable()
        except Exception as exc:
            logger.warning(f"Main agent initialization skipped due to error: {exc}")

        # Stage 1 of SYNTH_OS_DIRECTION.md: opt-in daily morning brief. No timer
        # and no persisted brief exist unless the owner enabled it. The scheduler
        # only assembles a read-only brief from local state; it never calls a
        # model, grants permission, or changes task state.
        if settings.morning_brief_enabled:
            try:
                from .services.morning_brief_scheduler import (
                    MorningBriefPolicy, start_morning_brief_scheduler,
                )
                start_morning_brief_scheduler(db_manager, MorningBriefPolicy(
                    enabled=True,
                    local_hour=settings.morning_brief_local_hour,
                ))
            except Exception as exc:
                logger.warning(f"Morning brief scheduler start skipped: {exc}")

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
        #  ("Process isolation").
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

        # Phase A handshake: honor the launcher-provided port if present.
        _port_env = os.environ.get("SYTHION_PORT")
        if _port_env:
            try:
                settings.port = int(_port_env)
            except ValueError:
                pass

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
            from .services.morning_brief_scheduler import stop_morning_brief_scheduler
            await stop_morning_brief_scheduler()
        except Exception as exc:
            logger.warning(f" Morning brief scheduler shutdown skipped: {exc}")
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

    finally:
        if _owned_profile_lock is not None:
            _owned_profile_lock.release()
            if profile_lock is _owned_profile_lock:
                profile_lock = None


app = FastAPI(
    title="Sythion (Synth) Backend",
    description="Trusted Windows 10/11 runtime core API for a larger intelligence system",
    version="0.1.0",
    lifespan=lifespan
)

# Both security layers use the same frozen origin allowlist; never derive them
# independently from ambient environment at different points during import.
_trusted_origins = control_origins(effective_development_mode(settings.development_mode))

app.add_middleware(
    LocalControlBoundaryMiddleware,
    token_provider=lambda: control_token,
    allowed_origins=_trusted_origins,
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
    allow_origins=sorted(_trusted_origins),
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
    expose_headers=["X-Sythion-Dispatch-Verdict"],
)


@app.get("/health", response_model=HealthResponse)
async def health_check():
    """Health check endpoint (legacy, moved to /api/v1/health)."""
    from .core.secrets import resolve_ollama_api_key
    import backend.main as _self
    boot = getattr(_self, "startup_boot", None)
    if not isinstance(boot, dict):
        boot = {}
    from .services.bootstrap import startup_status_projection

    _startup_projection = startup_status_projection(boot)
    startup_ss = _startup_projection["startup_sequence_status"]
    startup_stages = _startup_projection["startup_stage_statuses"]
    # The top-level liveness status must not mask a degraded startup: a process
    # that is serving but did not complete its startup sequence is degraded,
    # not healthy. Unknown startup state (e.g. before the lifespan runs) keeps
    # the conservative "healthy" liveness answer so probes are not falsely
    # failed.
    _database_connected = False
    if db_manager is not None:
        try:
            from sqlalchemy import text as _text

            with db_manager.engine.connect() as _conn:
                _conn.execute(_text("SELECT 1"))
            _database_connected = True
        except Exception:
            _database_connected = False

    if startup_ss in ("failed", "degraded", "error") or not _database_connected:
        health_status = "degraded"
    else:
        health_status = "healthy"
    return HealthResponse(
        status=health_status,
        version="0.1.0",
        database_connected=_database_connected,
        cloud_configured=bool(resolve_ollama_api_key(settings.ollama_api_key)),
        startup_sequence_status=startup_ss,
        startup_status=startup_ss,
        startup_overall_status=_startup_projection["startup_overall_status"],
        startup_aggregate_status=_startup_projection["startup_aggregate_status"],
        startup_stage_statuses=startup_stages,
        startup_boot=boot,
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
    # Vault-write-violation marker contract (see the constants above): an
    # exception that declares itself a blocked product-credential write is
    # answered with a documented 409. The check is a POSITIVE marker test, so
    # an ordinary exception cannot be diverted by accident, and the generic
    # 500 branch below stays byte for byte what it was.
    if getattr(exc, VAULT_WRITE_VIOLATION_ATTR, False):
        return JSONResponse(
            status_code=VAULT_WRITE_VIOLATION_STATUS,
            content=ErrorResponse(
                error=VAULT_WRITE_VIOLATION_ERROR,
                detail=str(exc),
                code=str(VAULT_WRITE_VIOLATION_STATUS)
            ).model_dump()
        )
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
