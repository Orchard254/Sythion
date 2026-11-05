"""Fail-closed prototype identity resolution under import/helper errors.

Synthetic paths only; no real profiles, vault, or network.
"""

import os
import sys
from pathlib import Path

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, SettingsMetadata
from backend.core import settings_store, storage


@pytest.mark.asyncio
async def test_storage_is_prototype_on_helper_exception(monkeypatch):
    monkeypatch.setattr(
        "backend.core.secrets.is_prototype_build",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    assert storage._is_prototype_build() is True


def test_storage_is_prototype_on_import_error(monkeypatch):
    monkeypatch.setitem(sys.modules, "backend.core.secrets", None)
    monkeypatch.setitem(sys.modules, "core.secrets", None)
    assert storage._is_prototype_build() is True


def test_storage_migrate_does_not_copy_legacy_on_helper_exception(monkeypatch, tmp_path):
    legacy = tmp_path / "synthetic-legacy"
    legacy.mkdir()
    (legacy / "secret.txt").write_text("x")
    target = tmp_path / "synthetic-target"

    monkeypatch.setenv(storage.DATA_DIR_ENV, str(target))
    monkeypatch.delenv(storage.LEGACY_DATA_DIR_ENV, raising=False)
    monkeypatch.setattr(storage, "LEGACY_DATA_DIR", str(legacy))
    monkeypatch.setattr(
        "backend.core.secrets.is_prototype_build",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )

    assert storage.migrate_legacy_data_dir() is False
    assert not (target / "secret.txt").exists()


def test_resolved_file_roots_skips_ambient_on_helper_exception(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(
        "backend.core.secrets.is_prototype_build",
        lambda: (_ for _ in ()).throw(RuntimeError("boom")),
    )
    monkeypatch.setattr(settings, "file_roots", ["C:/AmbientRoot"])

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        roots = settings_store.resolved_file_roots(session)
        assert roots == []
    finally:
        session.close()


def test_resolved_file_roots_keeps_grants_on_import_error(monkeypatch, tmp_path):
    from backend.core.config import settings

    monkeypatch.setitem(sys.modules, "backend.core.secrets", None)
    monkeypatch.setitem(sys.modules, "core.secrets", None)
    monkeypatch.setattr(settings, "file_roots", ["C:/AmbientRoot"])

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        grant = str(tmp_path / "granted")
        os.makedirs(grant, exist_ok=True)
        settings_store.update_settings(session, {"file_roots": [grant]})
        roots = settings_store.resolved_file_roots(session)
        assert all("AmbientRoot" not in str(r) for r in roots)
        assert any(str(Path(grant)) in str(r) or grant in str(r) for r in roots)
    finally:
        session.close()


@pytest.mark.asyncio
async def test_main_lifespan_skips_migration_on_helper_exception(monkeypatch, tmp_path):
    import backend.core.secrets as sec

    monkeypatch.setattr(sec, "is_prototype_build", lambda: (_ for _ in ()).throw(RuntimeError("boom")))

    data_dir = tmp_path / "main-profile"
    monkeypatch.setenv("SYTHION_DATA_DIR", str(data_dir))
    monkeypatch.delenv("Sythion (Synth)_DATA_DIR", raising=False)
    monkeypatch.setenv("SYTHION_SECRET_BACKEND", "memory")

    import backend.core.storage as storage

    calls = []

    def _record():
        calls.append(1)
        return False

    monkeypatch.setattr(storage, "migrate_legacy_data_dir", _record)

    import backend.main as main_module

    async with main_module.lifespan(main_module.app):
        pass

    assert calls == []


@pytest.mark.parametrize(
    "env, ambient_allowed",
    [
        ({}, True),
        ({"SYTHION_PROTECT_BUILD": "1"}, False),
        ({"SYTHION_PROTECT_BUILD": "not-a-valid-marker"}, False),
        (
            {"SYTHION_PROTECT_BUILD": "1", "SYTHION_SECRET_NAMESPACE": "Sythion (Synth)"},
            False,
        ),
    ],
)
def test_resolved_file_roots_identity_matrix(monkeypatch, tmp_path, env, ambient_allowed):
    from backend.core.config import settings

    # Clear all ambient identity markers, then apply the matrix case.
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
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    settings.file_roots = ["C:/AmbientMatrixRoot"]

    # Synthetic persisted grant must survive in every identity state.
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        grant = str(tmp_path / "granted-matrix")
        os.makedirs(grant, exist_ok=True)
        settings_store.update_settings(session, {"file_roots": [grant]})
        roots = [str(r) for r in settings_store.resolved_file_roots(session)]
        assert any(grant in r for r in roots)
        assert ("C:/AmbientMatrixRoot" in roots) is ambient_allowed
    finally:
        session.close()


@pytest.mark.parametrize(
    "env, isolated",
    [
        ({}, False),
        ({"SYTHION_PROTECT_BUILD": "1"}, True),
        ({"SYTHION_PROTECT_BUILD": "not-a-valid-marker"}, True),
        (
            {"SYTHION_PROTECT_BUILD": "1", "SYTHION_SECRET_NAMESPACE": "Sythion (Synth)"},
            True,
        ),
    ],
)
def test_storage_identity_matrix(monkeypatch, env, isolated):
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_SECRET_NAMESPACE",
        "SYTHION_SECRET_TARGET_PREFIX",
        "SYTHION_SECRET_SERVICE",
        "SYTHION_DATA_DIR",
        "Sythion (Synth)_DATA_DIR",
    ):
        monkeypatch.delenv(var, raising=False)
    for key, value in env.items():
        monkeypatch.setenv(key, value)

    assert storage._is_prototype_build() is isolated
