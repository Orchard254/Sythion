"""Profile directory contract between the desktop shell and the sidecar.

The shell and the sidecar must resolve ONE profile directory. A split profile
is not cosmetic: the sidecar would write its handshake into one directory
while the shell looked for it in another, the bridge would never resolve, and
the application would sit on "Connecting to Sythion" until the handshake
timeout expired.

The contract:

1. ``SYTHION_DATA_DIR`` is the canonical explicit override.
2. ``Sythion (Synth)_DATA_DIR`` is honoured only when the canonical variable
   is absent.
3. Both set to different directories is an error, not a silent choice.
4. Neither set means the established default.
"""

import os

import pytest

from backend.core import storage
from backend.core.storage import (
    DATA_DIR_ENV,
    DEFAULT_DATA_DIR_NAME,
    LEGACY_DATA_DIR_ENV,
    data_dir,
    data_dir_key,
    default_data_dir,
    normalize_data_dir,
    resolve_data_dir,
)


def test_the_canonical_variable_selects_the_profile():
    resolved = resolve_data_dir(canonical=r"C:\profiles\sythion")
    assert resolved == os.path.normpath(os.path.abspath(r"C:\profiles\sythion"))


def test_the_legacy_variable_is_still_honoured():
    resolved = resolve_data_dir(legacy=r"C:\profiles\legacy")
    assert resolved == os.path.normpath(os.path.abspath(r"C:\profiles\legacy"))


def test_matching_overrides_are_accepted():
    resolved = resolve_data_dir(
        canonical=r"C:\profiles\sythion", legacy=r"C:\profiles\sythion"
    )
    assert resolved == os.path.normpath(os.path.abspath(r"C:\profiles\sythion"))


def test_equivalent_overrides_are_accepted():
    resolved = resolve_data_dir(
        canonical="C:\\profiles\\sythion", legacy="C:\\profiles\\sythion\\.\\"
    )
    assert resolved == os.path.normpath(os.path.abspath("C:\\profiles\\sythion"))


def test_conflicting_overrides_are_rejected():
    with pytest.raises(ValueError) as excinfo:
        resolve_data_dir(
            canonical=r"C:\profiles\one", legacy=r"C:\profiles\two"
        )
    message = str(excinfo.value)
    assert DATA_DIR_ENV in message
    assert LEGACY_DATA_DIR_ENV in message
    assert r"C:\profiles\one" in message
    assert r"C:\profiles\two" in message


def test_the_default_is_used_without_overrides():
    resolved = resolve_data_dir()
    assert resolved.endswith(DEFAULT_DATA_DIR_NAME)


def test_empty_overrides_count_as_absent():
    assert resolve_data_dir(canonical="", legacy="").endswith(DEFAULT_DATA_DIR_NAME)
    assert resolve_data_dir(canonical="   ", legacy="").endswith(DEFAULT_DATA_DIR_NAME)


def test_the_resolved_directory_is_absolute():
    resolved = resolve_data_dir(canonical=os.path.join("relative", "profile"))
    assert os.path.isabs(resolved)


def test_comparison_is_case_insensitive_on_windows():
    if os.name != "nt":
        pytest.skip("Windows paths are the case-insensitive ones")
    assert data_dir_key(r"C:\Profiles\Sythion") == data_dir_key(r"c:\profiles\sythion")


def test_normalization_is_lexical():
    assert normalize_data_dir(r"C:\a\b\..\c") == os.path.normpath(r"C:\a\c")
    assert normalize_data_dir(r"C:\a\.\b") == os.path.normpath(r"C:\a\b")


def test_data_dir_uses_the_environment(monkeypatch, tmp_path):
    """`data_dir()` reads the environment and creates the directory."""
    target = tmp_path / "profile"
    monkeypatch.setenv(DATA_DIR_ENV, str(target))
    monkeypatch.delenv(LEGACY_DATA_DIR_ENV, raising=False)

    resolved = data_dir()

    assert resolved == os.path.normpath(os.path.abspath(str(target)))
    assert os.path.isdir(resolved)


def test_data_dir_rejects_conflicting_environment(monkeypatch, tmp_path):
    monkeypatch.setenv(DATA_DIR_ENV, str(tmp_path / "one"))
    monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(tmp_path / "two"))

    with pytest.raises(ValueError):
        data_dir()


def test_data_dir_falls_back_to_the_legacy_variable(monkeypatch, tmp_path):
    target = tmp_path / "legacy-profile"
    monkeypatch.delenv(DATA_DIR_ENV, raising=False)
    monkeypatch.setenv(LEGACY_DATA_DIR_ENV, str(target))

    resolved = data_dir()

    assert resolved == os.path.normpath(os.path.abspath(str(target)))
    assert os.path.isdir(resolved)


def test_default_data_dir_is_the_established_location():
    assert default_data_dir().endswith(DEFAULT_DATA_DIR_NAME)


def test_every_profile_file_resolves_into_the_same_directory(monkeypatch, tmp_path):
    """The handshake, the logs and the lock must land in ONE directory."""
    target = tmp_path / "profile"
    monkeypatch.setenv(DATA_DIR_ENV, str(target))
    monkeypatch.delenv(LEGACY_DATA_DIR_ENV, raising=False)

    resolved = data_dir()

    assert storage.handshake_path() == os.path.join(resolved, "sidecar.json")
    assert storage.lock_path() == os.path.join(resolved, "profile.lock")
    assert storage.database_path() == os.path.join(resolved, "sythion.db")
    assert storage.log_path() == os.path.join(resolved, "logs", "sythion.log")
    assert storage.log_dir() == os.path.join(resolved, "logs")
