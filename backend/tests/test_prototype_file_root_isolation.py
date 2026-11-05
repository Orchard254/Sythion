"""Focused tests for prototype-specific file-root isolation."""

import os
from pathlib import Path

import pytest

from backend.core import settings_store
from backend.core import secrets as secrets_module
from backend.tools import file_tools


@pytest.fixture(autouse=True)
def _prototype_env(monkeypatch):
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_IS_PROTECT",
        "SYTHION_IS_PROTOTYPE",
        "SYTHION_PROTECT",
        "SYTHION_PROTOTYPE",
        "SYTHION_BUILD_IDENTITY",
        "SYTHION_PRODUCT_NAME",
        "SYTHION_APP_IDENTITY",
        "SYTHION_IDENTIFIER",
        "SYTHION_PRODUCT_IDENTIFIER",
        "SYTHION_SECRET_NAMESPACE",
        "SYTHION_SECRET_TARGET_PREFIX",
        "SYTHION_SECRET_SERVICE",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    secrets_module.set_secret_store_for_testing(None)
    yield


@pytest.fixture
def db_manager(tmp_path):
    from backend.core.db_manager import DatabaseManager
    from backend.models.database import set_db_manager

    db_mgr = DatabaseManager(str(tmp_path / "proto.db"))
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session
    set_db_manager(None)


def test_prototype_empty_roots_do_not_fall_back_to_home_or_cwd(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "file_roots", [])
    monkeypatch.setattr("pathlib.Path.home", lambda: Path("C:/proto-home"))
    monkeypatch.chdir("C:/")
    roots = file_tools._effective_roots([])
    assert roots == []


def test_prototype_explicit_grants_do_not_add_repo_root(db_manager, tmp_path, monkeypatch):
    from backend.core.config import settings

    env_root = tmp_path / "env"
    granted = tmp_path / "granted"
    env_root.mkdir()
    granted.mkdir()
    monkeypatch.setattr(settings, "file_roots", [str(env_root)])
    settings_store.update_settings(db_manager, {"file_roots": [str(granted)]})
    roots = [str(r).lower() for r in file_tools._approved_roots()]
    # Inherited env roots must not be honoured in a prototype build; only
    # deliberate DB grants survive.
    assert str(env_root.resolve()).lower() not in roots
    assert str(granted.resolve()).lower() in roots
    repo_root = str(Path(file_tools.__file__).resolve().parent.parent).lower()
    assert repo_root not in roots


def test_prototype_fresh_profile_denies_all_file_access(db_manager, tmp_path, monkeypatch):
    from backend.core.config import settings
    from backend.tools.file_tools import _safe_resolve, _TargetRejected

    monkeypatch.setattr(settings, "file_roots", [])
    monkeypatch.setattr("backend.models.database.get_db_manager", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    target = tmp_path / "touchme.txt"
    target.write_text("x")
    with pytest.raises(_TargetRejected):
        _safe_resolve(str(target))
