"""
Tests for the live file-root grant path.

Contract: approved roots are the union of environment defaults
(SYTHION_FILE_ROOTS) and roots granted at runtime through the settings API
(persisted in the DB). A grant takes effect immediately without a restart,
and a settings-store failure must never widen access.
"""

import pytest

from backend.core.db_manager import DatabaseManager
from backend.core import settings_store
from backend.models.database import set_db_manager


@pytest.fixture
def db_manager(tmp_path):
    db_mgr = DatabaseManager(str(tmp_path / "test_grants.db"))
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session
    set_db_manager(None)


@pytest.fixture
def empty_env_roots(monkeypatch):
    from backend.core.config import settings

    monkeypatch.setattr(settings, "file_roots", [])


def _grant(session, root):
    settings_store.update_settings(session, {"file_roots": [root]})


def test_grant_adds_access_live(db_manager, empty_env_roots, tmp_path):
    """A root granted via the settings API is honored immediately, no restart."""
    granted = tmp_path / "granted"
    granted.mkdir()
    target = granted / "note.txt"
    target.write_text("hi")

    _grant(db_manager, str(granted))

    from backend.tools.file_tools import _safe_resolve

    resolved = _safe_resolve(str(target))
    assert resolved == target.resolve()


def test_grant_unions_with_env_roots(db_manager, tmp_path, monkeypatch):
    from backend.core.config import settings

    env_root = tmp_path / "env"
    granted_root = tmp_path / "granted"
    env_root.mkdir()
    granted_root.mkdir()
    monkeypatch.setattr(settings, "file_roots", [str(env_root)])

    _grant(db_manager, str(granted_root))

    from backend.tools.file_tools import _approved_roots

    roots = [str(r).lower() for r in _approved_roots()]
    assert str(env_root.resolve()).lower() in roots
    assert str(granted_root.resolve()).lower() in roots


def test_ungranted_path_still_rejected(db_manager, empty_env_roots, tmp_path):
    """Outside the granted roots, containment is still enforced."""
    granted = tmp_path / "granted"
    outside = tmp_path / "outside"
    granted.mkdir()
    outside.mkdir()
    target = outside / "note.txt"
    target.write_text("hi")

    _grant(db_manager, str(granted))

    from backend.tools.file_tools import _safe_resolve, _TargetRejected

    with pytest.raises(_TargetRejected) as exc_info:
        _safe_resolve(str(target))
    assert exc_info.value.code == "TARGET_OUTSIDE_ROOTS"


def test_no_grant_and_no_env_falls_back_to_home_and_cwd(db_manager, empty_env_roots):
    """With nothing configured the safe home/cwd defaults still apply."""
    from backend.tools.file_tools import _approved_roots

    roots = _approved_roots()
    assert roots
    home = __import__("pathlib").Path.home().resolve()
    assert any(r == home for r in roots)


@pytest.mark.asyncio
@pytest.mark.parametrize("env_configured", [False, True])
async def test_db_failure_denies_actual_file_reads(db_manager, tmp_path, monkeypatch, env_configured):
    """A failed lookup must not activate home/cwd or implicit backend access."""
    from backend.core.config import settings
    from backend.models import database
    from backend.tools.file_tools import FileReadTool
    from backend.api.schemas import FileReadInput

    env_root = tmp_path / "env"
    env_root.mkdir()
    narrow = tmp_path / "narrow"
    narrow.mkdir()
    _grant(db_manager, str(narrow))
    monkeypatch.setattr(settings, "file_roots", [str(env_root)] if env_configured else [])
    monkeypatch.setattr("pathlib.Path.home", lambda: tmp_path)
    monkeypatch.chdir(tmp_path)
    target = tmp_path / "private.txt"
    target.write_text("isolated test data")
    monkeypatch.setattr(database, "get_db_manager", lambda: (_ for _ in ()).throw(OSError("unavailable")))
    output = await FileReadTool().execute(FileReadInput(path=str(target)))
    assert output.success is False
    assert output.error["code"] == "TARGET_OUTSIDE_ROOTS"
    from backend.tools.file_tools import _approved_roots
    assert _approved_roots() == []


def test_corrupt_persisted_roots_fail_closed(db_manager, empty_env_roots, tmp_path):
    from backend.tools.file_tools import _safe_resolve, _TargetRejected
    settings_store.set_setting(db_manager, "file_roots", "invalid json")
    db_manager.commit()
    with pytest.raises(_TargetRejected):
        _safe_resolve(str(tmp_path / "file"))


def test_grant_removed_after_settings_reset(db_manager, empty_env_roots, tmp_path):
    """Revoking a grant (clearing file_roots) removes access immediately."""
    granted = tmp_path / "granted"
    granted.mkdir()

    _grant(db_manager, str(granted))

    from backend.tools.file_tools import _approved_roots

    assert any(str(r).lower() == str(granted.resolve()).lower() for r in _approved_roots())

    from backend.tools.file_tools import _effective_roots
    settings_store.update_settings(db_manager, {"file_roots": []}, fallback_ack={
        "persisted_file_roots": [str(granted)],
        "fallback_file_roots": [str(p) for p in _effective_roots([])],
    })

    roots = [str(r).lower() for r in _approved_roots()]
    assert all(str(r).lower() != str(granted.resolve()).lower() for r in roots)
