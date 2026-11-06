"""Isolated fail-closed regression tests for backend/core/profile.py saves.

These tests touch only synthetic profile data under pytest tmp_path. They
never use the real user profile, credentials, or any bundled mirror copy.
"""

import json
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
    monkeypatch.setattr(profile_module, "data_dir", lambda: str(tmp_path), raising=False)
    return tmp_path


def _profile_file(tmp_path: Path) -> Path:
    return tmp_path / "profile.json"


def test_load_profile_retains_compatible_readonly_behavior(isolated_profile_dir, tmp_path, monkeypatch):
    """Read-only loading stays tolerant, as required for safe consumers."""
    profile_file = _profile_file(tmp_path)

    profile_file.write_text("{not json", encoding="utf-8")
    assert profile_module.load_profile() == {}

    profile_file.write_text("[1, 2, 3]", encoding="utf-8")
    assert profile_module.load_profile() == {}

    profile_file.write_bytes(b"\xff\xfe\x00")
    assert profile_module.load_profile() == {}

    # Simulate an unreadable existing profile without changing on-disk bytes.
    original_bytes = profile_file.read_bytes()
    real_open = open

    def raising_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        if str(path) == str(profile_file) and "r" in str(mode):
            raise OSError("injected read failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(profile_module, "open", raising_open, raising=False)
    assert profile_module.load_profile() == {}
    assert profile_file.read_bytes() == original_bytes


def test_save_profile_malformed_existing_preserves_bytes(isolated_profile_dir, tmp_path):
    profile_file = _profile_file(tmp_path)
    malformed = b'{this is not json'
    profile_file.write_bytes(malformed)

    with pytest.raises(json.JSONDecodeError):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_file.read_bytes() == malformed


def test_save_profile_non_object_existing_preserves_bytes(isolated_profile_dir, tmp_path):
    profile_file = _profile_file(tmp_path)
    original = b"[1, 2, 3]"
    profile_file.write_bytes(original)

    with pytest.raises(ValueError, match="JSON object"):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_file.read_bytes() == original


def test_save_profile_read_error_preserves_bytes(isolated_profile_dir, tmp_path, monkeypatch):
    profile_file = _profile_file(tmp_path)
    original = b'{"selected_model": "old-model"}'
    profile_file.write_bytes(original)

    real_open = open

    def raising_open(path, *args, **kwargs):
        mode = args[0] if args else kwargs.get("mode", "r")
        mode = "r" if mode is None else str(mode)
        if str(path) == str(profile_file) and "w" not in mode and "a" not in mode and "x" not in mode:
            raise OSError("injected read failure")
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr(profile_module, "open", raising_open, raising=False)

    with pytest.raises(OSError, match="injected read failure"):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_file.read_bytes() == original


def test_save_profile_missing_existing_creates_profile(isolated_profile_dir, tmp_path):
    profile_file = _profile_file(tmp_path)
    assert not profile_file.exists()

    result = profile_module.save_profile(
        {"selected_model": "created-model", "keep": True}
    )

    assert profile_file.exists()
    stored = json.loads(profile_file.read_text(encoding="utf-8"))
    assert stored == {"selected_model": "created-model", "keep": True}
    assert result == stored


def test_save_profile_normal_update_merges(isolated_profile_dir, tmp_path):
    profile_file = _profile_file(tmp_path)
    profile_file.write_text(
        json.dumps({"selected_model": "old-model", "keep": "value"}),
        encoding="utf-8",
    )

    result = profile_module.save_profile({"selected_model": "new-model", "extra": 42})

    stored = json.loads(profile_file.read_text(encoding="utf-8"))
    assert stored == {
        "selected_model": "new-model",
        "keep": "value",
        "extra": 42,
    }
    assert result == stored

    # No leftover atomic temp files.
    assert list(tmp_path.glob(".profile.*.tmp")) == []


def test_save_profile_invalid_utf8_existing_preserves_bytes(isolated_profile_dir, tmp_path):
    profile_file = _profile_file(tmp_path)
    invalid_utf8 = b"\xff\xfe\x00"
    profile_file.write_bytes(invalid_utf8)

    with pytest.raises(UnicodeError):
        profile_module.save_profile({"selected_model": "new-model"})

    assert profile_file.read_bytes() == invalid_utf8
