"""Sidecar half of the trusted-host key handoff: consume once, fail closed."""

import pytest

from backend import main as backend_main
from backend.services.recovery_native_challenge import TrustedHostAttestationBoundary


@pytest.fixture
def profile_dir(tmp_path, monkeypatch):
    monkeypatch.setattr("backend.core.storage.data_dir", lambda: str(tmp_path))
    return tmp_path


def test_host_key_is_consumed_once_and_file_removed(profile_dir):
    key_file = profile_dir / "native_attestation.key"
    key_file.write_text("ab" * 32, encoding="ascii")
    boundary = backend_main._provision_native_attestation()
    assert isinstance(boundary, TrustedHostAttestationBoundary)
    assert boundary.configured is True
    assert not key_file.exists()
    assert backend_main._provision_native_attestation() is None


def test_missing_key_leaves_boundary_unconfigured(profile_dir):
    assert backend_main._provision_native_attestation() is None


@pytest.mark.parametrize("content", ["", "zz", "ab", "ab" * 10])
def test_malformed_key_fails_closed_and_is_deleted(profile_dir, content):
    key_file = profile_dir / "native_attestation.key"
    key_file.write_text(content, encoding="ascii")
    assert backend_main._provision_native_attestation() is None
    assert not key_file.exists()
