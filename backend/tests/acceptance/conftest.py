"""
Shared fixtures for the Sythion (Synth) acceptance-test suite.

These are the runnable versions of the "planned" gates in
. Each test file maps to one acceptance area and
must be deterministic: background model calls are stubbed so results depend
only on the code under test.
"""

import asyncio
import httpx
import pytest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.adapters.ollama_adapter import get_adapter
from backend.core import profile as profile_module
from backend.models.database import Base, get_db
from backend.api.routes import router as api_router
from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.tools.executor import ToolExecutor
from backend.services.task_runner import TaskRunner


ACCEPTANCE_CONTROL_TOKEN = "acceptance-control-token"


@pytest.fixture
def db_session():
    """Isolated in-memory database session."""
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def task_runner(db_session):
    """Task runner bound to the test session."""
    return TaskRunner(db_session)


@pytest.fixture
def tool_executor():
    """Tool executor with no tools registered (gates never touch OS state)."""
    return ToolExecutor()


@pytest.fixture
def app(db_session):
    """FastAPI app with the real router + auth middleware, test token, stubbed background work."""
    import backend.api.routes as routes

    test_app = FastAPI()
    test_app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: ACCEPTANCE_CONTROL_TOKEN,
        allowed_origins=control_origins(False),
    )
    test_app.include_router(api_router, prefix="/api/v1")

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db

    # Background execution must never hit the live model during acceptance tests.
    async def _noop_run_task(db, task_id, tool_executor_):
        return None

    import backend.services.task_execution_loop as loop_module
    loop_module.run_task = _noop_run_task
    routes.run_task = _noop_run_task

    return test_app


@pytest.fixture
def client(app):
    """TestClient against the acceptance app (sends no Origin by default)."""
    with TestClient(app) as c:
        yield c


def _headers():
    return {"X-Control-Token": ACCEPTANCE_CONTROL_TOKEN}


def _models_transport():
    async def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/api/tags":
            return httpx.Response(200, json={
                "models": [
                    {"name": "minimax-m3"},
                    {"name": "deepseek-r2"},
                ]
            })
        return httpx.Response(404)

    return httpx.MockTransport(handler)


@pytest.fixture
def isolated_profile(tmp_path, monkeypatch):
    """Point the profile file at a temp path so tests never touch the real one."""
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    # Restore the module global at teardown. Mutating __globals__ directly
    # leaked a bound active_inference_selection into later non-acceptance
    # tests, which then failed with "Active provider credential unavailable".
    monkeypatch.setattr(profile_module, "data_dir", lambda: str(tmp_path))
    return tmp_path


@pytest.fixture
def stubbed_models_adapter(isolated_profile):
    """Adapter that resolves an authenticated model list without the network."""
    adapter = get_adapter()
    adapter.api_key = "test-key"
    adapter.selected_model = ""
    adapter._client = httpx.AsyncClient(
        base_url="https://ollama.com",
        headers={"Authorization": "Bearer test-key"},
        transport=_models_transport(),
    )
    # _client_loop will be set on first async call to _get_client
    yield adapter
    try:
        asyncio.get_event_loop().run_until_complete(adapter.close())
    except Exception:
        pass
