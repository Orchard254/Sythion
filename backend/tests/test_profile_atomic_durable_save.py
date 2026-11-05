"""Regression tests for backend/core/profile.py atomic durable saves.

These tests exercise only synthetic data under pytest tmp_path and never touch
the real user profile, credentials, or bundled mirror copies.
"""

import json
import os
from pathlib import Path

import pytest

try:
    from backend.core import profile as profile_module
except ImportError:  # pragma: no cover - standalone backend layout
    from core import profile as profile_module


@pytest.fixture
def isolated_profile_dir(tmp_path, monkeypatch):
    """Redirect profile storage at a disposable temp directory."""
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    for name in ("load_profile", "save_profile", "profile_path", "get_selected_model", "set_selected_model"):
        func = getattr(profile_module, name)
        func.__globals__["data_dir"] = lambda path=str(tmp_path): path
    monkeypatch.setattr(profile_module, "data_dir", lambda: str(tmp_path), raising=False)
    return tmp_path


def test_save_profile_no_lost_fields_on_merge(isolated_profile_dir, tmp_path):
    """A partial save must merge, not replace, all prior profile fields."""
    profile_module.save_profile(
        {
            "selected_model": "model-1",
            "custom_field": "keep-me",
            "nested": {"a": 1, "b": 2},
        }
    )
    profile_module.save_profile({"extra": "added"})

    stored = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert stored == {
        "selected_model": "model-1",
        "custom_field": "keep-me",
        "nested": {"a": 1, "b": 2},
        "extra": "added",
    }

    # Returned dict also retains prior fields.
    result = profile_module.save_profile({"selected_model": "model-2"})
    assert result["custom_field"] == "keep-me"
    assert result["nested"] == {"a": 1, "b": 2}
    assert result["extra"] == "added"
    assert result["selected_model"] == "model-2"


def test_save_profile_write_failure_leaves_old_profile_intact_and_cleans_temp(
    isolated_profile_dir, tmp_path, monkeypatch
):
    """A failed atomic rename must not partially overwrite profile.json."""
    old_profile = {"selected_model": "old-model", "keep": "value"}
    profile_module.save_profile(old_profile)
    profile_path = tmp_path / "profile.json"
    old_text = profile_path.read_text(encoding="utf-8")

    monkeypatch.setattr(profile_module.os, "replace", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("injected failure")))
    with pytest.raises(RuntimeError, match="injected failure"):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_path.read_text(encoding="utf-8") == old_text
    assert list(tmp_path.glob(".profile.*.tmp")) == []


def test_save_profile_serialization_failure_cleans_temp(
    isolated_profile_dir, tmp_path, monkeypatch
):
    """Failure while serializing must unlink its unique temp file."""
    profile_module.save_profile({"selected_model": "old-model"})
    profile_path = tmp_path / "profile.json"
    old_text = profile_path.read_text(encoding="utf-8")

    monkeypatch.setattr(
        profile_module.json, "dump", lambda *a, **k: (_ for _ in ()).throw(ValueError("boom"))
    )
    with pytest.raises(ValueError, match="boom"):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_path.read_text(encoding="utf-8") == old_text
    assert list(tmp_path.glob(".profile.*.tmp")) == []


def test_save_profile_uses_unique_temp_and_leaves_no_temp_after_success(
    isolated_profile_dir, tmp_path, monkeypatch
):
    """Successful saves should use unique temp files and remove them."""
    captured = []
    original_mkstemp = profile_module.tempfile.mkstemp

    def capturing_mkstemp(*args, **kwargs):
        fd, name = original_mkstemp(*args, **kwargs)
        captured.append(name)
        return fd, name

    monkeypatch.setattr(profile_module.tempfile, "mkstemp", capturing_mkstemp)

    profile_module.save_profile({"value": 1})
    profile_module.save_profile({"value": 2})

    assert len(captured) == 2
    assert captured[0] != captured[1]
    # Neither mkstemp path remains as a partial temp after os.replace.
    for name in captured:
        assert not os.path.exists(name)
    assert list(tmp_path.glob(".profile.*.tmp")) == []
    stored = json.loads((tmp_path / "profile.json").read_text(encoding="utf-8"))
    assert stored["value"] == 2
