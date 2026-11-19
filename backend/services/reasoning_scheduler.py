"""Bounded event-driven scheduler for reasoning lifecycle events.

The scheduler owns a queue and a single worker; it never calls a model, runs a
tool, grants permission or mutates task state. Handlers supplied by the trusted
runtime decide what an event means. Capacity and sequential processing prevent
busy loops and overlapping model work for the same reasoning stream.
"""

from __future__ import annotations

import asyncio
from collections.abc import Awaitable, Callable
from typing import Dict, List

from backend.services.reasoning_dispatch import (
    DispatchEvent,
    ReasoningAdmissionError,
    ReasoningEventBus,
)

Handler = Callable[[DispatchEvent], Awaitable[None]]


class ReasoningScheduler:
    """One bounded queue, one sequential worker, fail-closed admission."""

    def __init__(self, bus: ReasoningEventBus, *, capacity: int = 256) -> None:
        if not isinstance(bus, ReasoningEventBus):
            raise TypeError("a ReasoningEventBus is required")
        if isinstance(capacity, bool) or not isinstance(capacity, int) or capacity <= 0:
            raise ValueError("capacity must be a positive integer")
        self.bus = bus
        self._handlers: Dict[object, List[Handler]] = {}
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=capacity)
        self._worker: asyncio.Task | None = None
        self._lock = asyncio.Lock()
        self._processed: List[DispatchEvent] = []
        bus.subscribe(self.submit)

    def register(self, event: object, handler: Handler) -> None:
        if not callable(handler):
            raise TypeError("handler must be callable")
        self._handlers.setdefault(event, []).append(handler)

    def submit(self, event: DispatchEvent) -> None:
        """Synchronous sink for the event bus; never blocks on a model."""
        if not isinstance(event, DispatchEvent):
            raise TypeError("event must be a DispatchEvent")
        try:
            self._queue.put_nowait(event)
        except asyncio.QueueFull:
            raise ReasoningAdmissionError("reasoning scheduler queue is full") from None

    async def _handle(self, event: DispatchEvent) -> None:
        async with self._lock:
            for handler in tuple(self._handlers.get(event.event, ())):
                await handler(event)
            for handler in tuple(self._handlers.get(None, ())):
                await handler(event)
            self._processed.append(event)

    async def process_until_idle(self) -> None:
        """Drain the queue deterministically (used by tests and shutdown)."""
        while not self._queue.empty():
            await self._handle(self._queue.get_nowait())

    async def start(self) -> None:
        if self._worker is not None and not self._worker.done():
            return
        self._worker = asyncio.create_task(self._run())

    async def _run(self) -> None:
        while True:
            event = await self._queue.get()
            try:
                await self._handle(event)
            finally:
                self._queue.task_done()

    async def stop(self) -> None:
        if self._worker is None:
            return
        self._worker.cancel()
        try:
            await self._worker
        except asyncio.CancelledError:
            pass
        self._worker = None

    def processed(self) -> tuple:
        return tuple(self._processed)
