"""Focused tests for prototype secret-storage isolation (in-memory only)."""

import os

import pytest

from backend.core import secrets as secrets_module
from backend.core.secrets import MemoryStore


@pytest.fixture(autouse=True)
def _clean_secret_env(monkeypatch):
    """Remove ambient (possibly prototype) markers so each test is explicit."""
    for var in (
        "SYTHION_PROTECT_BUILD",
        "SYTHION_PROTOTYPE_BUILD",
        "SYTHION_IS_PROTOTYPE",
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
    monkeypatch.setenv("SYTHION_SECRET_BACKEND", "memory")
    secrets_module.set_secret_store_for_testing(None)


def test_production_default_namespace():
    ctx = secrets_module.resolve_secret_context()
    assert ctx[0] == "Sythion (Synth)"
    assert ctx[1] is False
    assert ctx[2] is False
    assert secrets_module._resolve_target_prefix() == "Sythion (Synth)/"


def test_prototype_marker_changes_namespace(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    ctx = secrets_module.resolve_secret_context()
    assert ctx[0] == "Sythion (Synth) Prototype"
    assert ctx[1] is True
    assert ctx[2] is False
    assert secrets_module.is_prototype_build() is True
    assert secrets_module._resolve_target_prefix() == "Sythion (Synth) Prototype/"


def test_prototype_build_identity_env(monkeypatch):
    monkeypatch.setenv("SYTHION_BUILD_IDENTITY", "Sythion (Synth) Prototype")
    assert secrets_module.secret_namespace() == "Sythion (Synth) Prototype"
    assert secrets_module._resolve_target_prefix() == "Sythion (Synth) Prototype/"


def test_conflicting_markers_fail_closed(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    monkeypatch.setenv("SYTHION_SECRET_NAMESPACE", "Sythion (Synth)")
    ctx = secrets_module.resolve_secret_context()
    assert ctx[0] is None
    assert ctx[2] is True
    assert secrets_module._resolve_target_prefix() == ""


def test_conflicting_explicit_namespaces_fail_closed(monkeypatch):
    monkeypatch.setenv("SYTHION_SECRET_NAMESPACE", "Sythion (Synth)")
    monkeypatch.setenv("SYTHION_SECRET_TARGET_PREFIX", "Sythion (Synth) Prototype/")
    ctx = secrets_module.resolve_secret_context()
    assert ctx[0] is None
    assert ctx[2] is True


def test_fail_closed_uses_memory_store(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    monkeypatch.setenv("SYTHION_SECRET_NAMESPACE", "Sythion (Synth)")
    store = secrets_module._default_store()
    assert isinstance(store, MemoryStore)


def test_default_store_prototype_persists_distinct_target(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    store = secrets_module._default_store()
    # Memory backend is selected by env in tests; credential manager store is
    # never instantiated. The resolved prefix is still prototype-scoped.
    assert secrets_module._resolve_target_prefix() == "Sythion (Synth) Prototype/"


def test_multiple_prototype_aliases_agree(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "true")
    monkeypatch.setenv("SYTHION_PROTOTYPE_BUILD", "1")
    monkeypatch.setenv("SYTHION_DATA_DIR", r"C:\profiles\Sythion (Synth) Prototype")
    ctx = secrets_module.resolve_secret_context()
    assert ctx[1] is True
    assert ctx[2] is False


def test_custom_namespace_is_accepted(monkeypatch):
    monkeypatch.setenv("SYTHION_SECRET_NAMESPACE", "MyIsolatedLab")
    ctx = secrets_module.resolve_secret_context()
    assert ctx[0] == "MyIsolatedLab"
    assert ctx[2] is False


def test_cm_target_ignores_mutable_prefix(monkeypatch):
    monkeypatch.setattr(secrets_module, "_CM_TARGET_PREFIX", "INJECTED-PREFIX/")
    assert secrets_module._cm_target("ollama_api_key") == "Sythion (Synth)/ollama_api_key"


def test_invalid_marker_fail_closed_never_production(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "not-a-valid-marker")
    context = secrets_module.resolve_secret_context()
    assert context[0] is None
    assert context[2] is True
    assert secrets_module._resolve_target_prefix() == ""


def test_concurrent_immutable_resolution():
    import threading

    results = []

    def worker():
        results.append(secrets_module._resolve_target_prefix())

    threads = [threading.Thread(target=worker) for _ in range(32)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()
    assert all(result == "Sythion (Synth)/" for result in results)


def test_resolve_ollama_api_key_ignores_passed_key_in_prototype(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    value = secrets_module.resolve_ollama_api_key("sk-production-looking-key")
    assert value != "sk-production-looking-key"


def test_resolve_ollama_api_key_uses_passed_key_in_production(monkeypatch):
    value = secrets_module.resolve_ollama_api_key("sk-dev-override")
    assert value == "sk-dev-override"


def test_prototype_persist_and_retrieve_specific_key(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    store = secrets_module.MemoryStore()
    secrets_module.set_secret_store_for_testing(store)
    synthetic_key = "sk-prototype-only-synthetic"
    secrets_module.set_secret(secrets_module.OLLAMA_API_KEY, synthetic_key)
    assert secrets_module.has_secret(secrets_module.OLLAMA_API_KEY) is True
    assert store.get(secrets_module.OLLAMA_API_KEY) == synthetic_key
    # Override passed from settings/env must not replace stored prototype key.
    assert secrets_module.resolve_ollama_api_key("sk-must-not-leak") == synthetic_key
    secrets_module.delete_secret(secrets_module.OLLAMA_API_KEY)
    assert secrets_module.has_secret(secrets_module.OLLAMA_API_KEY) is False


def test_prototype_does_not_open_production_env(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    import builtins
    import os
    import importlib

    opened = []
    original_open = builtins.open

    def wrapped_open(file, *args, **kwargs):
        opened.append(str(file))
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", wrapped_open)
    import backend.core.secrets as secrets_mod
    import backend.core.config as config_mod

    importlib.reload(secrets_mod)
    importlib.reload(config_mod)

    env_path = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(config_mod.__file__))), ".env")
    )
    assert not any(os.path.abspath(path) == env_path for path in opened), opened
    assert config_mod.settings.ollama_api_key == ""


def test_production_control_still_opens_env(monkeypatch):
    import builtins
    import os
    import importlib

    opened = []
    original_open = builtins.open

    def wrapped_open(file, *args, **kwargs):
        opened.append(str(file))
        return original_open(file, *args, **kwargs)

    monkeypatch.setattr(builtins, "open", wrapped_open)
    import backend.core.secrets as secrets_mod
    import backend.core.config as config_mod

    importlib.reload(secrets_mod)
    importlib.reload(config_mod)

    env_path = os.path.abspath(
        os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(config_mod.__file__))), ".env")
    )
    # Only meaningful when the file exists; it does in the developer checkout.
    if os.path.exists(env_path):
        assert any(os.path.abspath(path) == env_path for path in opened), opened


def test_prototype_does_not_inherit_synthetic_api_key(monkeypatch):
    monkeypatch.setenv("SYTHION_PROTECT_BUILD", "1")
    monkeypatch.setenv("SYTHION_OLLAMA_API_KEY", "sk-synthetic-test-value")
    import importlib
    import backend.core.secrets as secrets_mod
    import backend.core.config as config_mod

    importlib.reload(secrets_mod)
    importlib.reload(config_mod)
    assert config_mod.settings.ollama_api_key == ""