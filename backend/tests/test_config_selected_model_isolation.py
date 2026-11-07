"""Regression tests for backend/core/config.py isolated selected-model behavior.

These tests use disposable temp directories, synthetic profile content, and no
real credentials.
"""

from pathlib import Path

import pytest

try:
    from backend.core import config as config_module
    from backend.core import profile as profile_module
    from backend.core import secrets as secrets_module
except ImportError:  # pragma: no cover - standalone backend layout
    from core import config as config_module
    from core import profile as profile_module
    from core import secrets as secrets_module


def _redirect_profile(path: Path, monkeypatch) -> None:
    monkeypatch.setattr(profile_module, "data_dir", lambda p=str(path): p, raising=False)


@pytest.fixture
def production_profile_dir(tmp_path, monkeypatch):
    path = tmp_path / "production-data"
    path.mkdir()
    _redirect_profile(path, monkeypatch)
    monkeypatch.delenv("SYTHION_PROTECT_BUILD", raising=False)
    monkeypatch.delenv("SYTHION_PROTOTYPE_BUILD", raising=False)
    monkeypatch.delenv("SYTHION_PROTOTYPE", raising=False)
    monkeypatch.delenv("SYTHION_IS_PROTOTYPE", raising=False)
    monkeypatch.delenv("SYTHION_BUILD_IDENTITY", raising=False)
    monkeypatch.delenv("SYTHION_SECRET_NAMESPACE", raising=False)
    # Keep profile data out of a path whose name might signal prototype.
    return path


@pytest.fixture
def prototype_profile_dir(tmp_path, monkeypatch):
    path = tmp_path / "isolated-prototype-data"
    path.mkdir()
    _redirect_profile(path, monkeypatch)
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    return path


def test_resolve_selected_model_uses_persisted_profile_without_env_in_production(
    production_profile_dir, monkeypatch
):
    profile_module.save_profile({"selected_model": "persisted-model"})
    monkeypatch.setattr(config_module.settings, "selected_model", "")

    assert config_module.resolve_selected_model() == "persisted-model"


def test_resolve_selected_model_production_env_override_allowed(production_profile_dir, monkeypatch):
    profile_module.save_profile({"selected_model": "persisted-model"})
    monkeypatch.setattr(config_module.settings, "selected_model", "env-model")

    namespace, is_prototype, fail_closed, _ = secrets_module.resolve_secret_context()
    assert namespace == secrets_module._PRODUCTION_SECRET_NAMESPACE
    assert not is_prototype
    assert not fail_closed
    assert config_module.resolve_selected_model() == "env-model"


def test_resolve_selected_model_strips_whitespace_env_override(
    production_profile_dir, monkeypatch
):
    profile_module.save_profile({"selected_model": "persisted-model"})
    monkeypatch.setattr(config_module.settings, "selected_model", "settings-model")
    monkeypatch.setenv("SYTHION_SELECTED_MODEL", "  env-model\t")

    assert config_module.resolve_selected_model() == "env-model"


def test_resolve_selected_model_whitespace_only_env_falls_through(
    production_profile_dir, monkeypatch
):
    profile_module.save_profile({"selected_model": "persisted-model"})
    monkeypatch.setattr(config_module.settings, "selected_model", "settings-model")
    monkeypatch.setenv("SYTHION_SELECTED_MODEL", "   ")

    assert config_module.resolve_selected_model() == "settings-model"


def test_resolve_selected_model_whitespace_env_ignored_when_fail_closed(
    production_profile_dir, monkeypatch
):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    monkeypatch.setenv("SYTHION_BUILD_IDENTITY", "production")
    monkeypatch.setenv("SYTHION_SELECTED_MODEL", "  leaked-model  ")
    monkeypatch.setattr(config_module.settings, "selected_model", "env-model")
    profile_module.save_profile({"selected_model": "safe-model"})

    namespace, is_prototype, fail_closed, _ = secrets_module.resolve_secret_context()
    assert fail_closed
    assert config_module.resolve_selected_model() == "safe-model"


def test_secret_backend_strips_whitespace_memory(production_profile_dir, monkeypatch):
    monkeypatch.setenv("SYTHION_SECRET_BACKEND", "  memory  ")

    assert isinstance(secrets_module._default_store(), secrets_module.MemoryStore)


def test_resolve_selected_model_prototype_ignores_ambient_env_and_dotenv_model(
    prototype_profile_dir, tmp_path, monkeypatch
):
    # Pretend a production-looking selected model leaked through settings.
    monkeypatch.setattr(config_module.settings, "selected_model", "beam-production-model")
    profile_module.save_profile({"selected_model": "prototype-selected-model"})

    namespace, is_prototype, fail_closed, _ = secrets_module.resolve_secret_context()
    assert is_prototype
    assert not fail_closed

    assert config_module.resolve_selected_model() == "prototype-selected-model"


def test_resolve_selected_model_prototype_does_not_read_production_profile(
    tmp_path, monkeypatch
):
    production_dir = tmp_path / "production-data"
    prototype_dir = tmp_path / "isolated-prototype-data"
    production_dir.mkdir()
    prototype_dir.mkdir()

    # Persist a production model under the production data directory.
    _redirect_profile(production_dir, monkeypatch)
    monkeypatch.delenv("SYTHION_PROTECT_BUILD", raising=False)
    profile_module.save_profile({"selected_model": "production-model"})

    # Switch to an isolated prototype profile and model leak attempt.
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    _redirect_profile(prototype_dir, monkeypatch)
    monkeypatch.setattr(config_module.settings, "selected_model", "env-model")
    profile_module.save_profile({"selected_model": "prototype-model"})

    assert config_module.resolve_selected_model() == "prototype-model"


def test_resolve_selected_model_fail_closed_ignores_ambient_env(production_profile_dir, monkeypatch):
    # Ambiguous identity markers: must fail closed, not act like production.
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    monkeypatch.setenv("SYTHION_BUILD_IDENTITY", "production")
    monkeypatch.setattr(config_module.settings, "selected_model", "env-model")
    profile_module.save_profile({"selected_model": "safe-model"})

    namespace, is_prototype, fail_closed, reason = secrets_module.resolve_secret_context()
    assert namespace is None
    assert fail_closed

    assert config_module.resolve_selected_model() == "safe-model"
