"""
Shared fixtures for the Sythion (Synth) backend test suite.

Guarded autonomy is the default contract (backend/core/config.py). The config
singleton reads SYTHION_AUTONOMY_LEVEL at import time, so an ambient env var
from the dev shell would leak into every test (mutating tools become
model-visible and destructive actions skip approval). Pin the guarded default
so tests are deterministic: a test opts into autonomous mode explicitly via
monkeypatch instead of inheriting the shell environment.
"""

import pytest

try:
    from backend.core.config import settings
except ImportError:
    from core.config import settings


# ---- pytest markers ----
# Use: @pytest.mark.slow, @pytest.mark.flaky, @pytest.mark.integration
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "flaky: marks tests as flaky (deselect with '-m \"not flaky\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests (deselect with '-m \"not integration\"')")
    config.addinivalue_line("markers", "acceptance: marks acceptance tests (select with '-m acceptance')")
    config.addinivalue_line("markers", "gui: marks tests requiring GUI (deselect with '-m \"not gui\"')")
    config.addinivalue_line("markers", "network: marks tests requiring network (deselect with '-m \"not network\"')")


@pytest.fixture(autouse=True)
def _guarded_autonomy_default(monkeypatch):
    """Force the guarded autonomy contract for the whole suite."""
    monkeypatch.delenv("SYTHION_AUTONOMY_LEVEL", raising=False)
    monkeypatch.setattr(settings, "autonomy_level", "guarded")
    # Background memory consolidation calls the real model API; keep it off
    # so completing a task in a test never issues a network/model request.
    monkeypatch.setattr(settings, "memory_consolidation_enabled", False)


# ---- Reusable fixtures ----
@pytest.fixture
def test_db():
    """Provide a fresh in-memory SQLite database session for a single test."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool
    try:
        from backend.models.database import Base
    except ImportError:
        from models.database import Base

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


@pytest.fixture
def test_client(test_db):
    """Provide a FastAPI TestClient with the test DB."""
    from fastapi.testclient import TestClient
    try:
        from backend.main import app
        from backend.models.database import get_db
    except ImportError:
        from main import app
        from models.database import get_db

    def override_get_db():
        yield test_db

    app.dependency_overrides[get_db] = override_get_db
    try:
        import backend.main as backend_main
    except ImportError:
        import main as backend_main
    previous_token = backend_main.control_token
    backend_main.control_token = "test-token"
    try:
        yield TestClient(
            app,
            headers={"Origin": "tauri://localhost", "X-Control-Token": "test-token"},
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


@pytest.fixture
def task_runner(test_db):
    """Provide a TaskRunner with a fresh test DB and lease acquired."""
    try:
        from backend.services.task_runner import TaskRunner
    except ImportError:
        from services.task_runner import TaskRunner

    runner = TaskRunner(test_db)
    import asyncio
    asyncio.run(runner.acquire_lease())
    try:
        yield runner
    finally:
        asyncio.run(runner.release_lease())


@pytest.fixture
def tool_executor():
    """Provide a ToolExecutor with all registered tools."""
    try:
        from backend.tools.executor import ToolExecutor
        from backend.tools import create_all_tools
    except ImportError:
        from tools.executor import ToolExecutor
        from tools import create_all_tools

    ex = ToolExecutor()
    for tool in create_all_tools():
        ex.register_tool(tool)
    return ex


@pytest.fixture
def acceptance_control_token():
    """Standard token for acceptance tests."""
    return "acceptance-control-token"