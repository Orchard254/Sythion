"""
Profile single-instance lock.

One JARVIS sidecar may run per local profile. The lock is an exclusive,
advisory lock file in the private data dir. Because the file is only advisory,
we pair it with a PID owner check: if the previous holder's process is gone,
the lock is stale and may be reclaimed. This mirrors the task-runner lease
concept at the process level.
"""

import logging
import os
import sys
import time
from typing import Optional

from .storage import lock_path

logger = logging.getLogger(__name__)

STALE_GRACE_SECONDS = 30


class ProfileLock:
    """Holds the exclusive profile lock for the lifetime of the sidecar."""

    def __init__(self, path: Optional[str] = None):
        self.path = path or lock_path()
        self._fd: Optional[int] = None

    def acquire(self) -> bool:
        """Acquire the lock. Returns True if acquired, False if another live
        sidecar holds it. Stale locks (dead owner or past grace period) are
        reclaimed."""
        if self._fd is not None:
            return True

        # msvcrt.locking needs at least one byte present to lock a range.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        except OSError:
            return False

        try:
            os.write(fd, b"0")
        except OSError:
            os.close(fd)
            return False

        if self._try_lock(fd):
            self._write_owner(fd)
            self._fd = fd
            logger.info(f"Acquired profile lock at {self.path}")
            return True

        os.close(fd)

        # Lock is held. If the owner is gone (stale), reclaim it.
        if self._is_stale():
            try:
                os.remove(self.path)
            except OSError:
                return False
            return self.acquire()

        return False

    def _try_lock(self, fd: int) -> bool:
        """Attempt a non-blocking exclusive lock on the open fd."""
        os.lseek(fd, 0, os.SEEK_SET)
        try:
            if os.name == "nt":
                import msvcrt
                msvcrt.locking(fd, msvcrt.LK_NBLCK, 1)
                return True
            else:  # pragma: no cover - v1 targets Windows
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return True
        except OSError:
            return False

    def _write_owner(self, fd: int) -> None:
        """Write the current pid + timestamp into the locked file."""
        content = f"{os.getpid()} {int(time.time())}".encode("utf-8")
        os.lseek(fd, 0, os.SEEK_SET)
        os.write(fd, content)
        os.truncate(fd, len(content))
        os.fsync(fd)

    def _is_stale(self) -> bool:
        """Check whether an existing lock file's owner process is gone."""
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except OSError:
            return True
        if not raw:
            return True
        try:
            pid_str, ts_str = raw.split()
            pid, ts = int(pid_str), float(ts_str)
        except (ValueError, TypeError):
            return True
        if pid == os.getpid():
            return True
        if not _pid_alive(pid):
            return True
        if time.time() - ts > STALE_GRACE_SECONDS:
            logger.warning(f"Profile lock held by live pid {pid} past grace period")
            return True
        return False

    def release(self) -> None:
        """Release the lock."""
        if self._fd is None:
            return
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(self._fd, 0, os.SEEK_SET)
                msvcrt.locking(self._fd, msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl
                fcntl.flock(self._fd, fcntl.LOCK_UN)
        except OSError:
            pass
        try:
            os.close(self._fd)
        except OSError:
            pass
        self._fd = None

    def __enter__(self) -> "ProfileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _pid_alive(pid: int) -> bool:
    """Return True if a process with the given PID is running."""
    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            handle = ctypes.windll.kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, int(pid)
            )
            if not handle:
                return False
            ctypes.windll.kernel32.CloseHandle(handle)
            return True
        except Exception:
            return False
    else:  # pragma: no cover
        try:
            os.kill(pid, 0)
            return True
        except OSError:
            return False


def acquire_profile_lock() -> Optional[ProfileLock]:
    """Acquire the profile lock, warning (not crashing) on contention.

    Returns the lock handle on success, None if another live sidecar holds it.
    """
    lock = ProfileLock()
    if lock.acquire():
        return lock
    logger.warning("Another JARVIS sidecar holds the profile lock")
    return None
