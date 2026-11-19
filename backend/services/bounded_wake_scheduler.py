"""Time-based bounded wake scheduler (explicitly opt-in).

This is a runtime-owned loop that periodically asks the existing deterministic
attention policy whether work is runnable. It never invokes a model, executes a
tool, grants permission or changes task state itself: those remain in the
trusted wake handler supplied by the caller. It is DISABLED by default, has a
hard cycle cap, and treats idle as the normal outcome (no busy model calls).
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from dataclasses import dataclass

from backend.services.work_attention import AttentionAction, decide_background_attention


@dataclass(frozen=True, slots=True)
class WakePolicy:
    enabled: bool = False
    interval_seconds: float = 5.0
    max_cycles: int = 12

    def __post_init__(self) -> None:
        if not isinstance(self.enabled, bool):
            raise ValueError("enabled must be a boolean")
        if (isinstance(self.interval_seconds, bool)
                or not isinstance(self.interval_seconds, (int, float))
                or not 0.1 <= self.interval_seconds <= 3600):
            raise ValueError("interval_seconds is out of bounds")
        if (isinstance(self.max_cycles, bool)
                or not isinstance(self.max_cycles, int)
                or not 1 <= self.max_cycles <= 1000):
            raise ValueError("max_cycles is out of bounds")


@dataclass(frozen=True, slots=True)
class WakeCycleReport:
    cycles: int
    idle_cycles: int
    work_cycles: int
    failed_cycles: int
    stopped: bool


class BoundedWakeScheduler:
    """Bounded time-driven loop around the shared attention decision."""

    def __init__(self, *, sleep: Callable[[float], Awaitable[None]] = asyncio.sleep):
        if not callable(sleep):
            raise ValueError("a sleep function is required")
        self._sleep = sleep
        self._stop = asyncio.Event()
        self._cycles = 0
        self._idle = 0
        self._work = 0
        self._failed = 0

    def stop(self) -> None:
        self._stop.set()

    async def run(
        self,
        selector,
        wake_handler: Callable[[str], Awaitable[None]],
        policy: WakePolicy,
    ) -> WakeCycleReport:
        if not isinstance(policy, WakePolicy):
            raise TypeError("policy must be a WakePolicy")
        if not callable(wake_handler):
            raise TypeError("wake_handler must be callable")
        if not policy.enabled:
            return WakeCycleReport(0, 0, 0, 0, stopped=True)

        for _ in range(policy.max_cycles):
            if self._stop.is_set():
                break
            self._cycles += 1
            try:
                attention = decide_background_attention(selector)
                if attention.action is AttentionAction.IDLE:
                    self._idle += 1
                else:
                    await wake_handler(attention.goal_id)
                    self._work += 1
            except asyncio.CancelledError:
                raise
            except Exception:
                # A failed handler is not a successful cycle and never a
                # reason to retry automatically inside this loop.
                self._failed += 1
            if self._stop.is_set():
                break
            await self._sleep(policy.interval_seconds)

        return WakeCycleReport(
            cycles=self._cycles, idle_cycles=self._idle,
            work_cycles=self._work, failed_cycles=self._failed,
            stopped=self._stop.is_set(),
        )
