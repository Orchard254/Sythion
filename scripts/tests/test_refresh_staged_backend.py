"""Disposable-tree tests for opt-in Tauri backend mirror refresh."""
import pytest
from scripts import refresh_staged_backend as refresh


@pytest.fixture
def sandbox(tmp_path, monkeypatch):
    source = tmp_path / "backend"
    source.mkdir()
    (source / "main.py").write_text("print('new')\n", encoding="utf-8")
    resources = tmp_path / "src-tauri" / "resources"
    resources.mkdir(parents=True)
    mirror = resources / "backend"
    backup_parent = tmp_path / "build" / "staging"
    monkeypatch.setattr(refresh, "SOURCE", source)
    monkeypatch.setattr(refresh, "RESOURCES", resources)
    monkeypatch.setattr(refresh, "MIRROR", mirror)
    monkeypatch.setattr(refresh, "BACKUP_PARENT", backup_parent)
    return source, resources, mirror, backup_parent


def test_default_check_fails_without_changing_old_mirror(sandbox):
    _, _, mirror, backup_parent = sandbox
    mirror.mkdir()
    (mirror / "main.py").write_text("old", encoding="utf-8")
    assert refresh.main([]) == 1
    assert (mirror / "main.py").read_text(encoding="utf-8") == "old"
    assert not backup_parent.exists()


def test_explicit_promotion_keeps_previous_mirror_as_backup(sandbox):
    source, resources, mirror, backup_parent = sandbox
    mirror.mkdir()
    (mirror / "main.py").write_text("old", encoding="utf-8")
    runtime = resources / "python-runtime"
    runtime.mkdir()
    (runtime / "keep.txt").write_text("untouched", encoding="utf-8")
    old = refresh.apply()
    assert old is not None
    assert (old / "main.py").read_text(encoding="utf-8") == "old"
    assert (mirror / "main.py").read_bytes() == (source / "main.py").read_bytes()
    assert (runtime / "keep.txt").read_text(encoding="utf-8") == "untouched"
    assert refresh.check() == (True, 0)
    assert list(backup_parent.glob("clean-backend-backup-*/main.py"))


def test_explicit_promotion_creates_missing_mirror_without_backup(sandbox):
    source, _, mirror, backup_parent = sandbox
    assert refresh.apply() is None
    assert (mirror / "main.py").read_bytes() == (source / "main.py").read_bytes()
    assert not list(backup_parent.glob("clean-backend-backup-*"))


def test_prohibited_local_state_blocks_refresh(sandbox):
    _, _, mirror, backup_parent = sandbox
    mirror.mkdir()
    (mirror / "main.py").write_text("old", encoding="utf-8")
    (mirror / ".env").write_text("PRIVATE_VALUE_THAT_MUST_NOT_LEAK", encoding="utf-8")
    with pytest.raises(ValueError, match="prohibited release artifacts"):
        refresh.apply()
    assert (mirror / "main.py").read_text(encoding="utf-8") == "old"
    assert not backup_parent.exists()


def test_after_promotion_verification_failure_rolls_back(sandbox, monkeypatch):
    _, _, mirror, backup_parent = sandbox
    mirror.mkdir()
    (mirror / "main.py").write_text("old", encoding="utf-8")
    actual = refresh.backend_mirror_findings
    calls = 0

    def post_promotion_failure(source, candidate):
        nonlocal calls
        calls += 1
        if calls == 2:
            return ["simulated mismatch after staging"]
        return actual(source, candidate)

    monkeypatch.setattr(refresh, "backend_mirror_findings", post_promotion_failure)
    with pytest.raises(RuntimeError, match="promoted mirror failed"):
        refresh.apply()
    assert (mirror / "main.py").read_text(encoding="utf-8") == "old"
    assert not list(backup_parent.glob("clean-backend-backup-*"))
