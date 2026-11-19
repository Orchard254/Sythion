"""Opt-in daily morning-brief scheduler (default OFF).

The Core checks periodically whether the local hour has been reached and, if
so, ensures exactly one durable brief exists for the local date. It performs no
model call, grants no authority, and changes no task state: it only persists a
read-only brief assembled from local state.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from datetime import datetime
from typing import Any, Optional

from backend.services.morning_brief import ensure_daily_brief


@dataclass(frozen=True, slots=True)
class MorningBriefPolicy:
    enabled: bool = False
    local_hour: int = 8
    check_interval_seconds: float = 60.0

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if (isinstance(self.local_hour, bool) or not isinstance(self.local_hour, int)
                or not 0 <= self.local_hour <= 23):
            raise ValueError("local_hour is out of bounds")
        if (isinstance(self.check_interval_seconds, bool)
                or not isinstance(self.check_interval_seconds, (int, float))
                or not 1.0 <= self.check_interval_seconds <= 86400):
            raise ValueError("check_interval_seconds is out of bounds")


@dataclass(frozen=True, slots=True)
class MorningBriefCycleReport:
    created: int
    failed: int
    stopped: bool


class MorningBriefScheduler:
    def __init__(self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not callable(sleep):
            raise ValueError("a sleep function is required")
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._created = 0
        self._failed = 0

    def stop(self) -> None:
        self._stop.set()

    async def run_once(self, db, policy: MorningBriefPolicy, *, now: datetime | None = None) -> bool:
        """Create today's brief if the local hour is reached. Returns created flag."""
        if not isinstance(policy, MorningBriefPolicy):
            raise TypeError("policy must be a MorningBriefPolicy")
        if not policy.enabled:
            return False
        now = now or datetime.now()
        if now.hour < policy.local_hour:
            return False
        _, created = ensure_daily_brief(db, now=now)
        if created:
            db.commit()
        return created

    async def run(
        self,
        db_factory: Callable[[], Any],
        policy: MorningBriefPolicy,
    ) -> MorningBriefCycleReport:
        if not callable(db_factory):
            raise TypeError("db_factory must be callable")
        if not isinstance(policy, MorningBriefPolicy):
            raise TypeError("policy must be a MorningBriefPolicy")
        if not policy.enabled:
            return MorningBriefCycleReport(0, 0, True)

        while not self._stop.is_set():
            session = db_factory()
            try:
                if await self.run_once(session, policy):
                    self._created += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                self._failed += 1
            finally:
                session.close()
            if self._stop.is_set():
                break
            await self._sleep(policy.check_interval_seconds)

        return MorningBriefCycleReport(
            created=self._created, failed=self._failed,
            stopped=self._stop.is_set(),
        )


# Process-wide registration (composition-root style, like the wake worker).
_scheduler: Optional[MorningBriefScheduler] = None
_task: Optional[asyncio.Task] = None


def set_morning_brief_scheduler(scheduler: Optional[MorningBriefScheduler]) -> None:
    global _scheduler
    _scheduler = scheduler


def get_morning_brief_scheduler() -> Optional[MorningBriefScheduler]:
    return _scheduler


def task_is_running() -> bool:
    return _task is not None and not _task.done()


def start_morning_brief_scheduler(db_manager, policy: MorningBriefPolicy) -> bool:
    if not isinstance(policy, MorningBriefPolicy) or not policy.enabled:
        return False
    if task_is_running():
        return False
    global _scheduler, _task
    _scheduler = MorningBriefScheduler()
    set_morning_brief_scheduler(_scheduler)
    _task = asyncio.create_task(
        _scheduler.run(db_manager.new_session, policy),
        name="sythion-morning-brief-scheduler",
    )
    return True


async def stop_morning_brief_scheduler() -> None:
    global _scheduler, _task
    if _scheduler is not None:
        _scheduler.stop()
    if _task is not None and not _task.done():
        _task.cancel()
        try:
            await _task
        except (asyncio.CancelledError, Exception):
            pass
    _task = None
    _scheduler = None
