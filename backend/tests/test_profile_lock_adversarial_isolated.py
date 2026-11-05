"""Adversarial isolated tests for robust profile locking.

Every test here uses a per-test SYTHION_DATA_DIR and in-process advisory
locks. No live user profile, running application, or alternate repository is
touched.
"""

import ctypes
import os

import pytest

try:
    from backend.core import profile_lock as pl
    from backend.core.profile_lock import ProfileLock
    from backend.core.storage import data_dir
except ImportError:  # pragma: no cover - standalone backend layout
    from core import profile_lock as pl
    from core.profile_lock import ProfileLock
    from core.storage import data_dir


@pytest.fixture
def isolated_data_dir(tmp_path, monkeypatch):
    monkeypatch.setenv("SYTHION_DATA_DIR", str(tmp_path))
    return str(tmp_path)


pytestmark = pytest.mark.concurrency


def test_unreadable_lock_does_not_reclaim_under_contention(
    isolated_data_dir, monkeypatch
):
    """An unreadable lock file must never be reclaimed while contended."""
    # Seed a contended advisory lock in this process.
    holder = ProfileLock()
    assert holder.acquire() is True

    calls = []

    def _no_remove(path):
        calls.append(path)
        raise AssertionError("profile lock path must never be deleted")

    monkeypatch.setattr(pl.os, "remove", _no_remove)
    # Force the contended branch even though our holder is the same process.
    monkeypatch.setattr(ProfileLock, "_try_lock", lambda self, fd: False)

    contender = ProfileLock()
    assert contender.acquire() is False
    assert contender._fd is None
    assert calls == []
    assert holder._fd is not None

    monkeypatch.undo()
    holder.release()


def test_is_stale_unreadable_is_fail_closed(isolated_data_dir, monkeypatch):
    path = os.path.join(data_dir(), "profile.lock")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("not a pid")

    lock = ProfileLock()
    # _pid_alive uncertainty and malformed content both fail closed.
    assert lock._is_stale() is False


def test_unknown_pid_liveness_fails_closed(monkeypatch):
    # Permission/unknown open errors must be treated as alive.
    monkeypatch.setattr(
        ctypes.windll.kernel32, "OpenProcess", lambda *a, **k: 0
    )
    # Force an access-denied style error for the pid.
    monkeypatch.setattr(
        ctypes.windll.kernel32, "GetLastError", lambda: 5
    )
    assert pl._pid_alive(4242) is True

    # A clearly missing pid is still dead.
    monkeypatch.setattr(
        ctypes.windll.kernel32, "GetLastError", lambda: 87
    )
    assert pl._pid_alive(999999999) is False

    # Exception during probe fails closed.
    monkeypatch.setattr(
        ctypes.windll.kernel32, "OpenProcess", lambda *a, **k: (_ for _ in ()).throw(RuntimeError("probe failed"))
    )
    assert pl._pid_alive(12345) is True

    # Unix-style: permission denied treated as alive.
    monkeypatch.undo()


def test_pid_alive_unix_permission_and_unknown_fail_closed(monkeypatch):
    if os.name == "nt":
        pytest.skip("Unix-only branch")
    import errno

    def raise_permission(pid, sig):
        raise PermissionError(errno.EPERM, "no permission")

    monkeypatch.setattr(pl.os, "kill", raise_permission)
    assert pl._pid_alive(123) is True

    def raise_unknown(pid, sig):
        raise OSError(errno.EINVAL, "unknown")

    monkeypatch.setattr(pl.os, "kill", raise_unknown)
    assert pl._pid_alive(123) is True

    def raise_missing(pid, sig):
        raise ProcessLookupError(errno.ESRCH, "no process")

    monkeypatch.setattr(pl.os, "kill", raise_missing)
    assert pl._pid_alive(123) is False


def test_write_failure_does_not_leak_descriptor(isolated_data_dir, monkeypatch):
    real_close = pl.os.close
    closed = []

    def tracking_close(fd):
        closed.append(fd)
        return real_close(fd)

    monkeypatch.setattr(pl.os, "close", tracking_close)

    def raise_write(self, fd):
        raise OSError("write failure")

    monkeypatch.setattr(ProfileLock, "_write_owner", raise_write)

    lock = ProfileLock()
    assert lock.acquire() is False
    assert lock._fd is None
    assert closed, "acquire() must close its descriptor on write failure"


def test_never_delete_even_if_stale_claimed(isolated_data_dir, monkeypatch):
    calls = []

    def no_remove(path):
        calls.append(path)
        raise AssertionError("profile lock path must never be deleted")

    monkeypatch.setattr(pl.os, "remove", no_remove)
    monkeypatch.setattr(ProfileLock, "_try_lock", lambda self, fd: False)
    monkeypatch.setattr(ProfileLock, "_is_stale", lambda self: True)

    lock = ProfileLock()
    assert lock.acquire() is False
    assert lock._fd is None
    assert calls == []


def test_stale_dead_owner_reclaimed_without_deletion(isolated_data_dir):
    path = os.path.join(data_dir(), "profile.lock")
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("999999999 1234567890")  # dead pid, old timestamp

    lock = ProfileLock()
    assert lock.acquire() is True
    assert lock._fd is not None
    lock.release()


def test_live_owner_still_exclusive(isolated_data_dir, monkeypatch):
    lock1 = ProfileLock()
    assert lock1.acquire() is True

    # A live pid keeps the lock exclusive.
    monkeypatch.setattr(pl, "_pid_alive", lambda pid: True)
    other = ProfileLock()
    assert other.acquire() is False
    assert other._fd is None

    lock1.release()
    lock3 = ProfileLock()
    assert lock3.acquire() is True
    lock3.release()


def test_contended_acquire_does_not_mutate_lock_contents(isolated_data_dir):
    path = os.path.join(data_dir(), "profile.lock")
    holder = ProfileLock()
    assert holder.acquire() is True
    original = b"12345 111"
    os.lseek(holder._fd, 0, os.SEEK_SET)
    os.write(holder._fd, original)
    os.ftruncate(holder._fd, len(original))
    os.fsync(holder._fd)

    contender = ProfileLock()
    assert contender.acquire() is False
    assert contender._fd is None

    os.lseek(holder._fd, 0, os.SEEK_SET)
    assert os.read(holder._fd, len(original) + 8) == original

    holder.release()


def test_byte_range_lock_succeeds_on_empty_file(isolated_data_dir):
    path = os.path.join(data_dir(), "profile.lock")
    fd = os.open(path, os.O_CREAT | os.O_RDWR | os.O_TRUNC)
    try:
        lock = ProfileLock()
        assert lock._try_lock(fd) is True
        # Unlock to avoid leaking the range.
        os.lseek(fd, 0, os.SEEK_SET)
        import msvcrt
        msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
    finally:
        os.close(fd)
