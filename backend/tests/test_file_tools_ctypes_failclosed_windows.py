"""Isolated adversarial tests for fail-closed Windows native-handle checks.

These tests exercise backend.tools.file_tools internal reparse-point and
canonical-containment helpers when the ctypes/native layer is unavailable or
fails at runtime. They are intentionally temp-dir only and do not perform any
network, device, or repository mutation.
"""

import os
from pathlib import Path

import pytest

import backend.tools.file_tools as ft


@pytest.fixture
def tmp_file(tmp_path: Path) -> Path:
    target = tmp_path / "ordinary.txt"
    target.write_text("hello", encoding="utf-8")
    return target


def test_ctypes_unready_windows_reparse_point_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", False)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    assert ft._is_reparse_point(tmp_file) is True


def test_ctypes_unready_windows_canonical_contained_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", False)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    assert ft._canonical_contained(tmp_file) is False


def test_ctypes_unready_non_windows_behavior_unchanged(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", False)
    monkeypatch.setattr(ft.os, "name", "posix")
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    assert ft._is_reparse_point(tmp_file) is False
    assert ft._canonical_contained(tmp_file) is True


def test_mocked_native_handle_unavailable_reparse_point_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_open_no_follow", lambda path: None)
    assert ft._is_reparse_point(tmp_file) is True


def test_mocked_native_open_raises_reparse_point_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")

    def _raise(path):
        raise OSError("native open unavailable")

    monkeypatch.setattr(ft, "_open_no_follow", _raise)
    assert ft._is_reparse_point(tmp_file) is True


def test_mocked_native_attributes_missing_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_open_no_follow", lambda path: 123)
    monkeypatch.setattr(ft, "_file_attributes", lambda handle: None)
    # Ensure close does not mask the result.
    monkeypatch.setattr(ft, "_kernel32", type("K", (), {"CloseHandle": staticmethod(lambda h: False)})())
    assert ft._is_reparse_point(tmp_file) is True


def test_native_attributes_no_reparse_bit_safe(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_open_no_follow", lambda path: 123)
    monkeypatch.setattr(ft, "_file_attributes", lambda handle: 0)
    monkeypatch.setattr(ft, "_kernel32", type("K", (), {"CloseHandle": staticmethod(lambda h: True)})())
    assert ft._is_reparse_point(tmp_file) is False


def test_mocked_native_attributes_raises_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")

    def _raise(handle):
        raise OSError("attribute query failed")

    monkeypatch.setattr(ft, "_open_no_follow", lambda path: 123)
    monkeypatch.setattr(ft, "_file_attributes", _raise)
    monkeypatch.setattr(ft, "_kernel32", type("K", (), {"CloseHandle": staticmethod(lambda h: True)})())
    assert ft._is_reparse_point(tmp_file) is True


def test_mocked_native_canonical_handle_unavailable_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    monkeypatch.setattr(ft, "_open_follow", lambda path: None)
    assert ft._canonical_contained(tmp_file) is False


def test_mocked_final_path_failure_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    monkeypatch.setattr(ft, "_open_follow", lambda path: 123)
    monkeypatch.setattr(ft, "_final_path_by_handle", lambda handle: None)
    monkeypatch.setattr(ft, "_kernel32", type("K", (), {"CloseHandle": staticmethod(lambda h: True)})())
    assert ft._canonical_contained(tmp_file) is False


def test_mocked_canonical_of_existing_raises_fail_closed(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", True)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")

    def _raise(path):
        raise OSError("canonical query failed")

    monkeypatch.setattr(ft, "_canonical_of_existing", _raise)
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    assert ft._canonical_contained(tmp_file) is False


def test_native_unready_safe_resolve_rejects_windows(monkeypatch, tmp_file):
    monkeypatch.setattr(ft, "_CTYPES_READY", False)
    if os.name != "nt":
        monkeypatch.setattr(ft.os, "name", "nt")
    monkeypatch.setattr(ft, "_approved_roots", lambda: [tmp_file.parent])
    with pytest.raises(ft._TargetRejected) as exc_info:
        ft._safe_resolve(str(tmp_file))
    assert exc_info.value.code == "TARGET_REPARSE_POINT_REJECTED"
