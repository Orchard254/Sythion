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

try:
    from backend.core import secrets as secrets_module
except ImportError:
    from core import secrets as secrets_module

# Compatibility fix for httpx >= 0.28 with Starlette TestClient (Starlette passes app= which httpx 0.28 dropped)
import httpx
_orig_httpx_client_init = httpx.Client.__init__
def _compat_httpx_client_init(self, *args, **kwargs):
    kwargs.pop("app", None)
    return _orig_httpx_client_init(self, *args, **kwargs)
httpx.Client.__init__ = _compat_httpx_client_init


# ---- pytest markers ----
# The root pytest.ini is the single source of truth for the marker
# vocabulary: the marker names, the description of what each marker selects,
# and the tree the default (no-argument) invocation collects. The
# descriptions live there and only there, so that a description cannot
# drift away from the code it describes.
#
# The root pytest.ini is REQUIRED. A full-tree collection without it is
# aborted by the selection-contract guard below (rule 5) before any test
# runs, because the point of the guard is that the repository has exactly one
# place where the selection contract is written down.
#
# The declarations below are a declaration-only fallback, and they are
# exactly as much as they look:
#   * they carry the marker NAMES only - the descriptions are not duplicated
#     here, so there is no second copy to drift;
#   * they take effect only once this conftest is loaded, i.e. once pytest
#     collects something under backend/tests. That means:
#       - a PARTIAL run inside backend/tests (one file, one directory) gets
#         the marker vocabulary without pytest.ini, and therefore keeps its
#         marker selections and its unknown-mark warnings correct;
#       - `pytest --markers` does NOT show these markers when the test tree
#         has not been collected, because --markers never loads this
#         conftest;
#       - a FULL-TREE collection without pytest.ini aborts with exit code 4
#         (rule 5). The fallback does not, and cannot, make the suite
#         runnable without the root configuration.
CANONICAL_MARKERS = (
    "slow",
    "flaky",
    "integration",
    "acceptance",
    "gui",
    "network",
    "process_kill",
    "provider_e2e",
    "concurrency",
    "learning_soak",
)

# Modules that need a live interactive Windows desktop and the real UI
# Automation stack. The `gui` marker means exactly that, so the two have to
# agree in both directions:
#   * a module listed here must carry `pytestmark = pytest.mark.gui`;
#   * a module whose tests carry the `gui` marker must be listed here.
# The guard checks both directions and names the module in the message, so
# adding a second live-GUI module is a two-line, reviewable change - the
# marker in the module, the entry here - instead of a suite-wide abort with
# a misleading message.
LIVE_GUI_MODULES = (
    "acceptance/test_gui_fixture.py",
)

# Directory layout used by the marker classification rules and by the
# selection-contract guard below.
_TESTS_ROOT = Path(__file__).resolve().parent
_ACCEPTANCE_DIR = _TESTS_ROOT / "acceptance"
_REPO_ROOT = _TESTS_ROOT.parent.parent

# pytest's built-in marks are never listed in the `markers` ini option.
_BUILTIN_MARKS = frozenset(
    {"skip", "skipif", "xfail", "usefixtures", "filterwarnings", "parametrize"}
)


def _norm(path) -> str:
    """Case- and separator-normalised real path, for comparisons.

    ``realpath`` on both sides of every comparison, so that a checkout
    reached through a symlinked path cannot make two spellings of the same
    file look like two different files (which would abort the suite with a
    false violation).
    """
    key = str(path)
    cached = _NORM_CACHE.get(key)
    if cached is None:
        cached = os.path.normcase(os.path.realpath(os.path.abspath(key)))
        _NORM_CACHE[key] = cached
    return cached


_NORM_CACHE = {}
_NORM_TESTS_ROOT = _norm(_TESTS_ROOT)
_NORM_ACCEPTANCE_DIR = _norm(_ACCEPTANCE_DIR)
_NORM_REPO_ROOT = _norm(_REPO_ROOT)


def _rel(path: str) -> str:
    """Repository-relative path (forward slashes), for messages."""
    prefix = _NORM_REPO_ROOT + os.sep
    if path.startswith(prefix):
        return path[len(prefix):].replace(os.sep, "/")
    return path


def _declared_marker_names(config) -> set:
    """The marker names pytest currently knows about."""
    names = set()
    for line in config.getini("markers"):
        # "name: description" or "name" -> "name"
        name = line.split(":")[0].split(" ")[0].strip()
        if name:
            names.add(name)
    return names


def pytest_configure(config):
    """Declare the canonical marker names the root configuration did not."""
    already = _declared_marker_names(config)
    added = set()
    for name in CANONICAL_MARKERS:
        if name not in already:
            # Name only: the descriptions live in pytest.ini and only there.
            config.addinivalue_line("markers", name)
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
    # Exclusion signals: if any part of the tree is deliberately dropped
    # from collection (--ignore / --ignore-glob / a conftest's collect_ignore
    # or collect_ignore_glob), the suite actually collected is only a subset
    # of backend/tests, so the selection-contract guard must NOT run on it.
    # config.getoption raises ValueError for unregistered options, and the
    # collect-ignore helpers may be absent on older pytest, so every probe
    # is guarded.
    try:
        if config.getoption("ignore", default=None):
            return False
    except (ValueError, AttributeError, TypeError):
        pass
    try:
        if config.getoption("ignore_glob", default=None):
            return False
    except (ValueError, AttributeError, TypeError):
        pass
    get_pathlist = getattr(config, "_getconftest_pathlist", None)
    if callable(get_pathlist):
        for ignore_name in ("collect_ignore", "collect_ignore_glob"):
            try:
                if get_pathlist(ignore_name, path=_TESTS_ROOT):
                    return False
            except Exception:
                pass

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


def _gui_population_violations(items) -> list:
    """Check the ``gui`` marker against the live-GUI module registry.

    Both directions are reported, and each message states what is actually
    wrong:
      * a module whose tests carry the ``gui`` marker but that is not
        registered as a live-GUI module;
      * a registered live-GUI module whose tests do not all carry the
        marker;
      * a registered live-GUI module from which no test was collected.
    """
    violations = []
    registered = {}
    for relative in LIVE_GUI_MODULES:
        registered[_norm(_TESTS_ROOT / relative)] = relative

    by_file = {}
    for item in items:
        by_file.setdefault(_item_path(item), []).append(item)

    for path, file_items in sorted(by_file.items()):
        marked = [i for i in file_items if i.get_closest_marker("gui") is not None]
        if marked and path not in registered:
            violations.append(
                "module '{}' has {} test(s) carrying the `gui` marker but is not "
                "registered as a live-GUI module - add it to LIVE_GUI_MODULES "
                "in backend/tests/conftest.py".format(_rel(path), len(marked))
            )

    for path, relative in sorted(registered.items()):
        file_items = by_file.get(path)
        if not file_items:
            violations.append(
                "module '{}' is registered as a live-GUI module but no test was "
                "collected from it".format(relative)
            )
            continue
        unmarked = [i for i in file_items if i.get_closest_marker("gui") is None]
        if unmarked:
            violations.append(
                "module '{}' is registered as a live-GUI module but {} of its {} "
                "collected test(s) do not carry the `gui` marker, e.g. {}".format(
                    relative, len(unmarked), len(file_items), unmarked[0].nodeid
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
    extra = sorted(acceptance - expected_acceptance)
    if extra:
        violations.append(
            "marker 'acceptance' is applied to {} test(s) outside "
            "backend/tests/acceptance/, e.g. {}".format(len(extra), extra[0])
        )
    missing = sorted(expected_acceptance - acceptance)
    if missing:
        violations.append(
            "marker 'acceptance' is missing from {} test(s) under "
            "backend/tests/acceptance/, e.g. {}".format(len(missing), missing[0])
        )

    # 3. `gui` is exactly the registered live-GUI modules
    violations.extend(_gui_population_violations(items))

    # 4. `network` is exactly the provider_e2e tests
    network = {i.nodeid for i in items if i.get_closest_marker("network") is not None}
    expected_network = {
        i.nodeid for i in items if i.get_closest_marker("provider_e2e") is not None
    }
    extra = sorted(network - expected_network)
    if extra:
        violations.append(
            "marker 'network' is applied to {} test(s) that are not provider_e2e "
            "tests, e.g. {}".format(len(extra), extra[0])
        )
    missing = sorted(expected_network - network)
    if missing:
        violations.append(
            "marker 'network' is missing from {} provider_e2e test(s), e.g. {}".format(
                len(missing), missing[0]
            )
        )

    # 5. the configuration in use is the repository-root pytest.ini and
    #    declares every canonical marker
    inipath = config.inipath
    if inipath is None:
        violations.append(
            "no pytest configuration file was used; the repository-root pytest.ini "
            "is required - it defines the marker vocabulary and the test tree"
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
            "the configuration in use does not declare the marker(s): {}".format(
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
#
# Note: ``file_roots`` is included above, but an empty value (``[]``) is NOT
# "no file access". ``backend/tools/file_tools.py::_approved_roots()``
# treats ``[]`` as "unset" and approves the user's home directory plus the
# process working directory (dropping the repository ``backend`` directory that
# a non-empty list always adds). The fixture swaps approved directories; it
# never makes the suite file-access-free.
_LOCAL_ENV_FIELDS = ("ollama_api_key", "selected_model", "file_roots")

try:
    from backend.core.config import Settings
except ImportError:  # pragma: no cover - the standalone ``backend/`` layout
    from core.config import Settings


def _clean_settings_snapshot():
    """The values ``_LOCAL_ENV_FIELDS`` have in a CLEAN checkout.

    Built with the env FILE disabled (``Settings(_env_file=None)``) AND with
    the process environment's ``SYTHION_*`` variables temporarily removed, so
    neither a developer's gitignored ``backend/.env`` nor an exported
    ``SYTHION_OLLAMA_API_KEY`` (or any other ``SYTHION_*`` value) can leak
    into the "clean" snapshot.  The result is always the field default for
    these three fields, regardless of the caller's working tree.

    Neutralising the process environment here is only the *snapshot* half of
    the contract: the per-test fixture below also clears the same variables
    from ``os.environ`` for every non-``provider_e2e`` test, so code that
    reads them directly (and any fresh ``Settings()`` instance constructed
    inside a test) observes the clean values too.

    A list value is copied out of ``clean``, so the snapshot does not alias
    ``clean``'s own list. That copy is NOT on its own enough to protect the
    snapshot: the snapshot is module-level and shared by every test in the
    session, so the fixture below hands EVERY TEST its own fresh copy of the
    list values. That per-test copy is what stops a test which mutates
    ``settings.file_roots`` IN PLACE (``.append(...)``) from corrupting the
    snapshot the NEXT test is isolated from.
    """
    # Temporarily remove every ``SYTHION_*`` process variable so pydantic-settings
    # cannot read them while building the clean snapshot.  They are restored
    # afterwards (see ``finally``), so the process environment seen by the rest
    # of the session - and by the ``provider_e2e`` carve-out - is unchanged.
    removed = {}
    for key in list(os.environ):
        if key.upper().startswith("SYTHION_"):
            removed[key] = os.environ.pop(key)
    try:
        clean = Settings(_env_file=None)
        snapshot = {}
        for name in _LOCAL_ENV_FIELDS:
            value = getattr(clean, name)
            snapshot[name] = list(value) if isinstance(value, list) else value
        return snapshot
    finally:
        os.environ.update(removed)


_CLEAN_SETTINGS = _clean_settings_snapshot()


@pytest.fixture
def clean_settings_snapshot():
    """The shared snapshot of the CLEAN values the isolation fixture uses.

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

    Note: ``[]`` for ``file_roots`` is NOT "no file access" --
    ``backend/tools/file_tools.py::_approved_roots()`` falls back to the
    user's home directory plus the current working directory in that case (and
    drops the repository ``backend`` directory a non-empty list always adds).
    The isolation swaps approved directories; it is not file-access-free.
    See item 006, finding F8.
    """
    if request.node.get_closest_marker("provider_e2e") is not None:
        # Deliberate carve-out: the two provider-e2e modules gate themselves
        # with ``pytestmark ... skipif(not _provider_ready())``, which is
        # evaluated at IMPORT time - before any fixture runs. Neutralising the
        # key here would make that gate pass (it would see the developer's
        # key) and then fail the live provider call with no credential. That
        # run is explicitly opt-in (``SYTHION_RUN_PROVIDER_E2E=1``) and needs a
        # real credential, so those modules keep the ambient value (including
        # the process environment).
        return
    # Neutralise the PROCESS ENVIRONMENT too, not just the settings singleton.
    # ``os.environ`` is the other half of the leak: pydantic-settings re-reads
    # ``SYTHION_*`` vars whenever a fresh ``Settings()`` is constructed inside
    # a test, and production code that calls ``os.environ``/``os.getenv``
    # directly would otherwise still see the caller's ambient values.  Clear
    # every ``SYTHION_*`` variable for the duration of this test; monkeypatch
    # restores them at teardown.  ``provider_e2e`` tests are carved out above
    # and keep the ambient environment.
    for key in list(os.environ):
        if key.upper().startswith("SYTHION_"):
            monkeypatch.delenv(key, raising=False)
    for name, value in _CLEAN_SETTINGS.items():
        # A FRESH copy per test. The snapshot is shared by the whole session,
        # so handing out the snapshot's own list would let a test that mutates
        # ``settings.file_roots`` IN PLACE (``.append(...)``) corrupt the value
        # every LATER test is isolated from.
        monkeypatch.setattr(settings, name, list(value) if isinstance(value, list) else value)



# ---------------------------------------------------------------------------
# Product-credential write guard
# ---------------------------------------------------------------------------
# The whole test suite must never write to, or delete, the PRODUCT's own
# Windows Credential Manager entry (``Sythion (Synth)/ollama_api_key``).
# ``test_secret_storage.py::test_credential_manager_round_trip`` still drives
# the real ``win32cred`` store, but under a test-scoped prefix.
#
# The production prefix is snapshotted HERE, at conftest IMPORT time. The
# round-trip test monkeypatches ``secrets_module._CM_TARGET_PREFIX`` to a
# test-only prefix, so a guard that read that attribute at CALL time would be
# disarmed by the very fix it is meant to police.

_PRODUCT_TARGET_PREFIX = secrets_module._CM_TARGET_PREFIX

# The prefix comparison is CASE-INSENSITIVE, and it has to be: Windows
# Credential Manager target names are case-insensitive, so
# "sythion (synth)/ollama_api_key" and "Sythion (Synth)/ollama_api_key"
# address the SAME credential slot (measured: writing the second spelling
# does not produce a second, separately visible credential). A case-sensitive
# comparison would let the lower-cased spelling through and land the write
# in the product's own credential.
#
# DO NOT "fix" this back to a case-sensitive comparison. The test-scoped
# prefix ("Sythion (Synth) [TEST]/") is unaffected: it differs from the
# product prefix at the character after "Sythion (Synth)" - a space, not a
# "/" - so it is still allowed in either spelling.
_PRODUCT_TARGET_PREFIX_LOWER = _PRODUCT_TARGET_PREFIX.lower()


class ProductCredentialWriteAttempt(AssertionError):
    """Raised when a test tries to write or delete the product credential.

    Deliberately NOT a ``pywintypes.error``: ``CredentialManagerStore.delete``
    swallows those (``backend/core/secrets.py``), which would make the delete
    path look toothless. An ``AssertionError`` subclass is reported by pytest
    as a failure.
    """


@pytest.fixture(autouse=True)
def _forbid_product_credential_writes(request, monkeypatch):
    """Fail any test that writes or deletes a product credential.

    The check happens BEFORE the original ``win32cred`` call is delegated to,
    so a blocked attempt never reaches the real vault. Reads (``CredRead``)
    are not intercepted: they are non-destructive, and blocking them would
    change what other tests observe.

    The guard never touches ``secrets_module._store`` - no other test sees a
    different secret store than before.

    The guard's rule is "reject unless you can PROVE the target is not a
    product target", which has two consequences:

    * The prefix comparison is CASE-INSENSITIVE. Windows credential target
      names are case-insensitive, so "sythion (synth)/ollama_api_key" is the
      same slot as "Sythion (Synth)/ollama_api_key" and is rejected too.
    * The guard FAILS CLOSED. A credential whose target name cannot be read
      is REJECTED, never delegated to the real vault.

    Known constraint: ``backend/main.py`` installs a global
    ``@app.exception_handler(Exception)``. A future test that drove
    a vault write through ``backend.main.app`` would get a 500 response
    instead of this exception - the write would still be blocked, but the
    test would not go red. No test uses ``main.app`` today.
    """
    try:
        import win32cred
    except ImportError:
        # Not Windows (or pywin32 missing): there is no vault to protect.
        yield
        return

    def _reject(target, action):
        """Raise unless the target is PROVABLY not a product target.

        Fails CLOSED: an unreadable target is rejected, not delegated.
        """
        if not isinstance(target, str):
            raise ProductCredentialWriteAttempt(
                f"test {request.node.nodeid} attempted to {action} a "
                f"credential whose target name could not be read. The guard "
                f"fails closed: the target must be a readable string that is "
                f"not under {_PRODUCT_TARGET_PREFIX!r}."
            )
        if target.lower().startswith(_PRODUCT_TARGET_PREFIX_LOWER):
            raise ProductCredentialWriteAttempt(
                f"test {request.node.nodeid} attempted to {action} the "
                f"product credential target {target!r}. Tests must never write to "
                f"or delete targets under {_PRODUCT_TARGET_PREFIX!r} (the "
                f"comparison is case-insensitive, because Windows credential "
                f"target names are); use a test-scoped prefix instead."
            )

    def _target_of(cred):
        """The target name of a credential, or None if it cannot be read."""
        getter = getattr(cred, "get", None)
        if getter is None:
            return None
        try:
            target = getter("TargetName", None)
        except Exception:
            # Anything unreadable is treated as "unknown target" -> rejected.
            return None
        return target if isinstance(target, str) else None

    original_write = win32cred.CredWrite
    original_delete = win32cred.CredDelete

    def guarded_write(cred, flags=0):
        _reject(_target_of(cred), "write")
        return original_write(cred, flags)

    def guarded_delete(target, *args, **kwargs):
        _reject(target, "delete")
        return original_delete(target, *args, **kwargs)

    monkeypatch.setattr(win32cred, "CredWrite", guarded_write)
    monkeypatch.setattr(win32cred, "CredDelete", guarded_delete)
    yield
