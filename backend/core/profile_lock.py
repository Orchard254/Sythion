"""
Profile single-instance lock.

One Sythion (Synth) sidecar may run per local profile. The lock is an exclusive,
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
        sidecar holds it.

        Advisory locking is authoritative: a dead owner releases the OS lock
        automatically, so a stale lock file can be reclaimed by simply locking
        it. We never unlink or recreate the path, which avoids racing with
        another process that may hold or create that path. When the advisory
        lock is contended, ownership cannot be verified (including for an
        unreadable/malformed file or an unknown PID), so acquisition fails
        closed rather than risking displacement of a live holder.
        """
        if self._fd is not None:
            return True

        # Advisory byte-range locks are attempted directly, even on an empty
        # file. Verified on Windows: msvcrt.locking LK_NBLCK succeeds for a
        # one-byte range at EOF+ (creating no written byte), so a contended
        # acquire never mutates the incumbent's owner record.
        try:
            fd = os.open(self.path, os.O_CREAT | os.O_RDWR)
        except OSError:
            return False

        if self._try_lock(fd):
            try:
                self._write_owner(fd)
            except OSError:
                self._unlock_fd(fd)
                self._close_fd(fd)
                return False
            self._fd = fd
            logger.info(f"Acquired profile lock at {self.path}")
            return True

        # Lock is held by someone else: close our descriptor and fail closed.
        # The path is never deleted or recreated here (see class docstring).
        self._close_fd(fd)
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
        """Check whether an existing lock file's owner process is gone.

        Fail closed: an unreadable, malformed, or otherwise unverifiable lock
        is never reported as stale, because its owner liveness cannot be
        established. Only a successfully parsed PID that is definitively gone
        makes the lock stale.
        """
        try:
            with open(self.path, "r", encoding="utf-8") as fh:
                raw = fh.read().strip()
        except (OSError, UnicodeError, UnicodeDecodeError):
            return False
        if not raw:
            return False
        try:
            pid_str, ts_str = raw.split()
            pid, ts = int(pid_str), float(ts_str)
        except (ValueError, TypeError, AttributeError):
            return False
        try:
            # Only a definitively dead owner is stale. Any uncertainty in
            # _pid_alive is treated as "alive" (fail closed).
            alive = _pid_alive(pid)
        except Exception:
            alive = True
        if not alive:
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
        self._close_fd(self._fd)
        self._fd = None

    def _close_fd(self, fd: int) -> None:
        try:
            os.close(fd)
        except OSError:
            pass

    def _unlock_fd(self, fd: int) -> None:
        try:
            if os.name == "nt":
                import msvcrt
                os.lseek(fd, 0, os.SEEK_SET)
                msvcrt.locking(fd, msvcrt.LK_UNLCK, 1)
            else:  # pragma: no cover
                import fcntl
                fcntl.flock(fd, fcntl.LOCK_UN)
        except OSError:
            pass

    def __enter__(self) -> "ProfileLock":
        self.acquire()
        return self

    def __exit__(self, *exc) -> None:
        self.release()


def _pid_alive(pid) -> bool:
    """Return True if a process with the given PID is running.

    Fail closed: whenever liveness cannot be established definitively, report
    the process as alive so a lock is never reclaimed based on uncertainty.
    """
    try:
        pid_int = int(pid)
    except (ValueError, TypeError):
        return True
    if pid_int <= 0:
        return True

    if os.name == "nt":
        try:
            import ctypes

            PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
            kernel32 = ctypes.windll.kernel32
            handle = kernel32.OpenProcess(
                PROCESS_QUERY_LIMITED_INFORMATION, False, pid_int
            )
            if handle:
                kernel32.CloseHandle(handle)
                return True
            err = kernel32.GetLastError()
            # ERROR_INVALID_PARAMETER (87): the pid does not correspond to a
            # running process. Anything else (access denied, unknown) is
            # treated as live (fail closed).
            if err == 87:
                return False
            return True
        except Exception:
            return True
    else:  # pragma: no cover - v1 targets Windows
        try:
            os.kill(pid_int, 0)
            return True
        except ProcessLookupError:
            return False
        except PermissionError:
            return True
        except OSError:
            return True


def acquire_profile_lock() -> Optional[ProfileLock]:
    """Acquire the profile lock, warning (not crashing) on contention.

    Returns the lock handle on success, None if another live sidecar holds it.
    """
    lock = ProfileLock()
    if lock.acquire():
        return lock
    logger.warning("Another Sythion (Synth) sidecar holds the profile lock")
    return None
