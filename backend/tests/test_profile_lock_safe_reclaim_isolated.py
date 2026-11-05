"""Isolated concurrency tests for ProfileLock stale-reclaim safety.

These exercise the reclaim decision without touching a live user profile or a
running application: all state lives under a per-test SYTHION_DATA_DIR and uses
in-process advisory locks.
"""

import os

import pytest

try:
    from backend.core.profile_lock import ProfileLock
    from backend.core.storage import data_dir
except ImportError:  # pragma: no cover - standalone backend layout
    from core.profile_lock import ProfileLock
    from core.storage import data_dir


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    return str(tmp_path)


pytestmark = pytest.mark.concurrency


def test_live_owner_is_not_reclaimed_after_grace(isolated_data_dir):
    """A lock held by a live process stays exclusive even with an old timestamp."""
    lock1 = ProfileLock()
    assert lock1.acquire() is True
    # Tamper the stored timestamp to be far in the past while the owner lives,
    # writing through the already-held descriptor (Windows locks the range, so
    # reopening the path for write would be denied).
    payload = f"{os.getpid()} 0".encode("utf-8")
    os.lseek(lock1._fd, 0, os.SEEK_SET)
    os.write(lock1._fd, payload)
    os.ftruncate(lock1._fd, len(payload))
    os.fsync(lock1._fd)

    lock2 = ProfileLock()
    assert lock2.acquire() is False
    assert lock2._fd is None

    # The original holder is still intact.
    assert lock1._fd is not None
    lock1.release()


def test_same_pid_live_lock_is_not_reclaimed(isolated_data_dir):
    """Two ProfileLock instances in one process must not reclaim each other."""
    lock1 = ProfileLock()
    assert lock1.acquire() is True

    lock2 = ProfileLock()
    assert lock2.acquire() is False
    assert lock2._fd is None
    assert lock1._fd is not None

    lock1.release()
    # After a real release, acquisition works again.
    lock3 = ProfileLock()
    assert lock3.acquire() is True
    lock3.release()


def test_unlockable_live_owner_with_patched_pid(isolated_data_dir, monkeypatch):
    """Even if the underlying OS lock is contended and _pid_alive says live,
    an old timestamp must not trigger reclaim."""
    from backend.core import profile_lock as pl

    lock1 = ProfileLock()
    assert lock1.acquire() is True
    path = os.path.join(data_dir(), "profile.lock")

    # Pretend some other live pid owns the lock and that it is ancient, writing
    # through the already-held descriptor.
    other_pid = 4242
    payload = f"{other_pid} 0".encode("utf-8")
    os.lseek(lock1._fd, 0, os.SEEK_SET)
    os.write(lock1._fd, payload)
    os.ftruncate(lock1._fd, len(payload))
    os.fsync(lock1._fd)

    monkeypatch.setattr(pl, "_pid_alive", lambda pid: True)

    # Force the contend-and-check-stale branch.
    monkeypatch.setattr(ProfileLock, "_try_lock", lambda self, fd: False)

    lock2 = ProfileLock()
    assert lock2.acquire() is False
    assert lock2._fd is None

    monkeypatch.undo()
    lock1.release()


def test_dead_owner_still_reclaimed(isolated_data_dir):
    """Dead owners keep the stale-recovery contract."""
    path = os.path.join(data_dir(), "profile.lock")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("999999999 0")

    lock = ProfileLock()
    assert lock.acquire() is True
    lock.release()
