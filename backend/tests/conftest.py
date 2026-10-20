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

# Compatibility fix for httpx >= 0.28 with Starlette TestClient (Starlette passes app= which httpx 0.28 dropped)
import httpx
_orig_httpx_client_init = httpx.Client.__init__
def _compat_httpx_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_httpx_client_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_client_init


# ---- pytest markers ----
# Use: @pytest.mark.slow, @pytest.mark.flaky, @pytest.mark.integration
def pytest_configure(config):
    config.addinivalue_line("markers", "slow: marks tests as slow (deselect with '-m \"not slow\"')")
    config.addinivalue_line("markers", "flaky: marks tests as flaky (deselect with '-m \"not flaky\"')")
    config.addinivalue_line("markers", "integration: marks tests as integration tests (deselect with '-m \"not integration\"')")
    config.addinivalue_line("markers", "acceptance: marks acceptance tests (select with '-m acceptance')")
    config.addinivalue_line("markers", "gui: marks tests requiring GUI (deselect with '-m \"not gui\"')")
    config.addinivalue_line("markers", "network: marks tests requiring network (deselect with '-m \"not network\"')")
    config.addinivalue_line(
        "markers",
        "process_kill: real Windows process-kill integration tests "
        "(run explicitly with SYTHION_RUN_PROCESS_KILL=1)",
    )
    config.addinivalue_line(
        "markers",
        "provider_e2e: real provider-backed specialist E2E tests "
        "(run explicitly with SYTHION_RUN_PROVIDER_E2E=1 plus configured "
        "provider credentials/model; skipped otherwise)",
    )


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
        from backend.tools.registry import create_all_tools
    except ImportError:
        from tools.executor import ToolExecutor
        from tools.registry import create_all_tools

    ex = ToolExecutor()
    for tool in create_all_tools():
        ex.register_tool(tool)
    return ex


@pytest.fixture
def acceptance_control_token():
    """Standard token for acceptance tests."""
    return "acceptance-control-token"


# ---- Ambient ``backend/.env`` isolation -------------------------------------
#
# ``backend/core/config.py`` points pydantic-settings at ``<repo>/backend/.env``
# (``model_config["env_file"]``) and builds the ``settings`` singleton ONCE, at
# import time. ``backend/.env`` is gitignored, so it exists in a developer's
# working tree and in NO clean checkout and on NO CI runner. A test that
# inherits it is a test whose result depends on a file that is deliberately not
# in the repository - the suite would be RED on a fresh clone for a reason
# that has nothing to do with the code under test.
#
# Contract: ``_LOCAL_ENV_FIELDS`` names the fields a developer's ``.env`` can
# supply. This block is the single place where the suite stops inheriting a
# local working tree. To extend the isolation, add the field name to
# ``_LOCAL_ENV_FIELDS`` - the snapshot and the fixture below pick it up.
_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")

try:
    from backend.core.config import Settings
except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
    from core.config import Settings


def _clean_settings_snapshot():
    """The values ``_LOCAL_ENV_FIELDS`` have in a CLEAN checkout.

    Built with the env FILE disabled (``Settings(_env_file=None)``), so the
    developer's ``backend/.env`` is never read to obtain these values: the
    result is the process environment's ``SYTHION_*`` value when that variable
    is exported, otherwise the field default.

    A list value is copied out of ``clean``, so the snapshot does not alias
    ``clean``'s own list. That copy is NOT on its own enough to protect the
    snapshot: the snapshot is module-level and shared by every test in the
    session, so the fixture below hands EVERY TEST its own fresh copy of the
    list values. That per-test copy is what stops a test which mutates
    ``settings.file_roots`` IN PLACE (``.append(...)``) from corrupting the
    snapshot the NEXT test is isolated from.
    """
    clean = Settings(_env_file=None)
    snapshot = {}
    for name in _LOCAL_ENV_FIELDS:
        value = getattr(clean, name)
        snapshot[name] = list(value) if isinstance(value, list) else value
    return snapshot


_CLEAN_SETTINGS = _clean_settings_snapshot()


@pytest.fixture
def clean_settings_snapshot():
    """The shared CLEAN-checkout snapshot the isolation fixture assigns from.

    Exposed so a test can prove that the snapshot itself survives a test which
    mutates one of the isolated list values IN PLACE.
    """
    return _CLEAN_SETTINGS


@pytest.fixture(autouse=True)
def _isolate_local_env_settings(monkeypatch, request):
    """Stop the suite inheriting a developer's gitignored ``backend/.env``.

    The assignment is undone by ``monkeypatch`` at teardown, so it cannot leak
    into another test. Only CLEAN values are ever assigned - in a clean
    checkout ``""``, ``""`` and ``[]`` - so this fixture can never introduce a
    credential into the test process.
    """
    if request.node.get_closest_marker("provider_e2e") is not None:
        # Deliberate carve-out: the two provider-e2e modules gate themselves
        # with ``pytestmark ... skipif(not _provider_ready())``, which is
        # evaluated at IMPORT time - before any fixture runs. Neutralising the
        # key here would make that gate pass (it would see the developer's
        # key) and then fail the live provider call with no credential. That
        # run is explicitly opt-in (``SYTHION_RUN_PROVIDER_E2E=1``) and needs a
        # real credential, so those modules keep the ambient value.
        return
    for name, value in _CLEAN_SETTINGS.items():
        # A FRESH copy per test. The snapshot is shared by the whole session,
        # so handing out the snapshot's own list would let a test that mutates
        # ``settings.file_roots`` IN PLACE (``.append(...)``) corrupt the value
        # every LATER test is isolated from.
        monkeypatch.setattr(settings, name, list(value) if isinstance(value, list) else value)
