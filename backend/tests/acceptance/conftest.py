"""
Shared fixtures for the Sythion (Synth) acceptance-test suite.

These are the runnable versions of the "planned" gates in
docs/acceptance-test-map.md. Each test file maps to one acceptance area and
must be deterministic: background model calls are stubbed so results depend
only on the code under test.
"""

import asyncio
import pytest
from unittest.mock import AsyncMock

from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, get_db
from backend.api.routes import router as api_router
from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.core import secrets as secrets_module
from backend.tools.executor import ToolExecutor
from backend.services.task_runner import TaskRunner


ACCEPTANCE_CONTROL_TOKEN = "acceptance-control-token"


# ---------------------------------------------------------------------------
# Product-credential write guard
# ---------------------------------------------------------------------------
# The acceptance suite must never write to, or delete, the PRODUCT's own
# Windows Credential Manager entry (``Sythion (Synth)/ollama_api_key``).
# ``test_secret_storage.py::test_credential_manager_round_trip`` still drives
# the real ``win32cred`` store, but under a test-scoped prefix.
#
# The production prefix is snapshotted HERE, at conftest IMPORT time. The
# round-trip test monkeypatches ``secrets_module._CM_TARGET_PREFIX`` to a
# test-only prefix, so a guard that read that attribute at CALL time would be
# disarmed by the very fix it is meant to police.

_PRODUCT_TARGET_PREFIX = secrets_module._CM_TARGET_PREFIX


class ProductCredentialWriteAttempt(AssertionError):
    """Raised when a test tries to write or delete the product credential.

    Deliberately NOT a ``pywintypes.error``: ``CredentialManagerStore.delete``
    swallows those (``backend/core/secrets.py``), which would make the delete
    path look toothless. An ``AssertionError`` subclass is reported by pytest
    as a failure.
    """


@pytest.fixture(autouse=True)
def _forbid_product_credential_writes(request, monkeypatch):
    """Fail any acceptance test that writes or deletes a product credential.

    The check happens BEFORE the original ``win32cred`` call is delegated to,
    so a blocked attempt never reaches the real vault. Reads (``CredRead``)
    are not intercepted: they are non-destructive, and blocking them would
    change what other acceptance tests observe.

    The guard never touches ``secrets_module._store`` - no other acceptance
    test sees a different secret store than before.
    """
    try:
        import win32cred
    except ImportError:
        # Not Windows (or pywin32 missing): there is no vault to protect.
        yield
        return

    def _reject(target, action):
        if isinstance(target, str) and target.startswith(_PRODUCT_TARGET_PREFIX):
            raise ProductCredentialWriteAttempt(
                f"acceptance test {request.node.nodeid} attempted to {action} the "
                f"product credential target {target!r}. Tests must never write to "
                f"or delete targets under {_PRODUCT_TARGET_PREFIX!r}; use a "
                f"test-scoped prefix instead."
            )

    original_write = win32cred.CredWrite
    original_delete = win32cred.CredDelete

    def guarded_write(cred, flags=0):
        target = cred.get("TargetName", "") if hasattr(cred, "get") else ""
        _reject(target, "write")
        return original_write(cred, flags)

    def guarded_delete(target, *args, **kwargs):
        _reject(target, "delete")
        return original_delete(target, *args, **kwargs)

    monkeypatch.setattr(win32cred, "CredWrite", guarded_write)
    monkeypatch.setattr(win32cred, "CredDelete", guarded_delete)
    yield


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
