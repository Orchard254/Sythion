"""
Shared fixtures for the Sythion (Synth) backend test suite.

Guarded autonomy is the default contract (backend/core/config.py). The config
singleton reads SYTHION_AUTONOMY_LEVEL at import time, so an ambient env var
from the dev shell would leak into every test (mutating tools become
model-visible and destructive actions skip approval). Pin the guarded default
so tests are deterministic: a test opts into autonomous mode explicitly via
monkeypatch instead of inheriting the shell environment.
"""

import os
from pathlib import Path

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
# The root pytest.ini is the single source of truth for the marker
# vocabulary: which markers exist and what each one selects. The declarations
# below are only a fallback, so the suite still runs - and still fails loudly
# on an unknown marker - when it is run without that file.
#
# `concurrency` and `gated_soak` are applied to real tests and used to be
# undeclared, which produced one PytestUnknownMarkWarning per use.
CANONICAL_MARKERS = {
    "slow": "a slow test (deselect with '-m \"not slow\"')",
    "flaky": "declared for future use; not currently applied to any test",
    "integration": "declared for future use; not currently applied to any test",
    "acceptance": "the acceptance sub-suite under backend/tests/acceptance/",
    "gui": (
        "requires a live interactive Windows desktop and the real UI "
        "Automation stack (currently backend/tests/acceptance/test_gui_fixture.py)"
    ),
    "network": (
        "requires an outbound network connection "
        "(currently every provider_e2e test)"
    ),
    "process_kill": (
        "real Windows process-kill integration tests "
        "(run explicitly with SYTHION_RUN_PROCESS_KILL=1)"
    ),
    "provider_e2e": (
        "real provider-backed specialist E2E tests "
        "(run explicitly with SYTHION_RUN_PROVIDER_E2E=1 plus a configured "
        "provider credential/model; skipped otherwise)"
    ),
    "concurrency": "tests that exercise the shared runtime under concurrent load",
    "gated_soak": (
        "long-running soak tests, gated behind an explicit environment flag"
    ),
}

# Directory layout used by the marker classification rules and by the
# selection-contract guard below.
_TESTS_ROOT = Path(__file__).resolve().parent
_ACCEPTANCE_DIR = _TESTS_ROOT / "acceptance"
_GUI_MODULE = _ACCEPTANCE_DIR / "test_gui_fixture.py"
_REPO_ROOT = _TESTS_ROOT.parent.parent

# pytest's built-in marks are never listed in the `markers` ini option.
_BUILTIN_MARKS = frozenset(
    {"skip", "skipif", "xfail", "usefixtures", "filterwarnings", "parametrize"}
)


def _norm(path) -> str:
    """Case- and separator-normalised absolute path, for comparisons."""
    return os.path.normcase(os.path.abspath(str(path)))


_NORM_TESTS_ROOT = _norm(_TESTS_ROOT)
_NORM_ACCEPTANCE_DIR = _norm(_ACCEPTANCE_DIR)
_NORM_GUI_MODULE = _norm(_GUI_MODULE)
_NORM_REPO_ROOT = _norm(_REPO_ROOT)


def _declared_marker_names(config) -> set:
    """The marker names pytest currently knows about."""
    names = set()
    for line in config.getini("markers"):
        # "name: description" -> "name"
        name = line.split(":")[0].split(" ")[0].strip()
        if name:
            names.add(name)
    return names


def pytest_configure(config):
    """Declare the canonical markers the root configuration did not declare."""
    already = _declared_marker_names(config)
    added = set()
    for name, description in CANONICAL_MARKERS.items():
        if name not in already:
            config.addinivalue_line("markers", "{}: {}".format(name, description))
            added.add(name)
    # Remember what came from this fallback, so the guard below can tell
    # "declared by the root configuration" from "declared here".
    config._sythion_fallback_markers = added


# ---- marker classification and the selection-contract guard ----
def _item_path(item) -> str:
    """Normalised absolute path of the file that defines the item."""
    path = item.path
    if not path.is_absolute():
        path = Path(str(item.config.rootpath)) / path
    return _norm(path)


def _apply_marker_rules(item) -> None:
    """Apply the two rule-based marker classifications.

    This has to happen in ``pytest_itemcollected`` and NOT in
    ``pytest_collection_modifyitems``: pytest's own marker deselection
    (``MarkPlugin.deselect_by_mark``) is registered ``tryfirst`` and runs
    before any other ``pytest_collection_modifyitems`` implementation, so a
    marker added there is invisible to ``-m``.
    """
    if _item_path(item).startswith(_NORM_ACCEPTANCE_DIR + os.sep):
        item.add_marker("acceptance")
    if item.get_closest_marker("provider_e2e") is not None:
        item.add_marker("network")


def pytest_itemcollected(item):
    """Classify the item and record it for the selection-contract guard."""
    _apply_marker_rules(item)
    recorded = getattr(item.config, "_sythion_selection_items", None)
    if recorded is None:
        recorded = []
        item.config._sythion_selection_items = recorded
    recorded.append(item)


def _is_full_tree_collection(config) -> bool:
    """True when this invocation collected the whole backend/tests tree."""
    base = Path(str(config.invocation_params.dir))
    args = [str(a) for a in config.args]
    if not args:
        args = [str(p) for p in config.getini("testpaths")]
    for arg in args:
        candidate = Path(arg)
        if not candidate.is_absolute():
            candidate = base / candidate
        if _norm(candidate) == _NORM_TESTS_ROOT:
            return True
    return False


def _compare_populations(marker: str, actual: set, expected: set) -> list:
    """Describe how two marker populations differ."""
    if actual == expected:
        return []
    violations = []
    extra = sorted(actual - expected)
    missing = sorted(expected - actual)
    if extra:
        violations.append(
            "marker '{}' is applied to {} test(s) it does not describe, e.g. {}".format(
                marker, len(extra), extra[0]
            )
        )
    if missing:
        violations.append(
            "marker '{}' is missing from {} test(s) it must describe, e.g. {}".format(
                marker, len(missing), missing[0]
            )
        )
    return violations


def _selection_contract_violations(config, items) -> list:
    """Check every marker population against the contract."""
    violations = []
    declared = _declared_marker_names(config)
    fallback = getattr(config, "_sythion_fallback_markers", set())
    from_root = declared - fallback

    # 1. every marker carried by a collected item is a declared marker
    unknown = {}
    for item in items:
        for mark in item.iter_markers():
            if mark.name in declared or mark.name in _BUILTIN_MARKS:
                continue
            unknown.setdefault(mark.name, []).append(item.nodeid)
    for name, nodeids in sorted(unknown.items()):
        violations.append(
            "undeclared marker 'pytest.mark.{}' is applied to {} test(s), e.g. {}".format(
                name, len(nodeids), nodeids[0]
            )
        )

    # 2. `acceptance` is exactly the acceptance sub-suite
    acceptance = {i.nodeid for i in items if i.get_closest_marker("acceptance") is not None}
    expected_acceptance = {
        i.nodeid for i in items
        if _item_path(i).startswith(_NORM_ACCEPTANCE_DIR + os.sep)
    }
    violations.extend(_compare_populations("acceptance", acceptance, expected_acceptance))

    # 3. `gui` is exactly the live-desktop GUI module
    gui = {i.nodeid for i in items if i.get_closest_marker("gui") is not None}
    expected_gui = {i.nodeid for i in items if _item_path(i) == _NORM_GUI_MODULE}
    violations.extend(_compare_populations("gui", gui, expected_gui))

    # 4. `network` is exactly the provider_e2e tests
    network = {i.nodeid for i in items if i.get_closest_marker("network") is not None}
    expected_network = {
        i.nodeid for i in items if i.get_closest_marker("provider_e2e") is not None
    }
    violations.extend(_compare_populations("network", network, expected_network))

    # 5. the root configuration is the repository-root pytest.ini and declares
    #    every canonical marker
    inipath = config.inipath
    if inipath is None:
        violations.append(
            "no pytest configuration file was used; expected pytest.ini at the repository root"
        )
    elif _norm(Path(inipath).parent) != _NORM_REPO_ROOT:
        violations.append(
            "the configuration file in use is '{}', not the repository-root pytest.ini".format(
                inipath
            )
        )
    missing = sorted(set(CANONICAL_MARKERS) - from_root)
    if missing:
        violations.append(
            "the root configuration does not declare the marker(s): {}".format(
                ", ".join(missing)
            )
        )
    return violations


def pytest_collection_finish(session):
    """Enforce the selection contract on every full-tree collection.

    A partial run (a single file, a single directory) is left alone: the
    populations of a partial run are not comparable with the contract.
    """
    config = session.config
    items = getattr(config, "_sythion_selection_items", [])
    if not items:
        return
    if not _is_full_tree_collection(config):
        return
    violations = _selection_contract_violations(config, items)
    if violations:
        raise pytest.UsageError(
            "selection-contract violation in the backend test tree:\n  - "
            + "\n  - ".join(violations)
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
