"""Event-driven scheduler acceptance: bounded, sequential, no implicit model call."""

import asyncio

import pytest

from backend.services.reasoning_dispatch import (
    DispatchEvent,
    ReasoningAdmissionError,
    ReasoningEvent,
    ReasoningEventBus,
    ReasoningPurpose,
)
from backend.services.reasoning_scheduler import ReasoningScheduler


def _event(kind=ReasoningEvent.ADMITTED, job="j1"):
    return DispatchEvent(
        kind, job, ReasoningPurpose.TASK_DECISION, "nvidia", 1.0)


@pytest.mark.asyncio
async def test_events_are_processed_sequentially_without_model_calls():
    bus = ReasoningEventBus()
    scheduler = ReasoningScheduler(bus)
    order = []

    async def handler(event):
        order.append(("start", event.job_id))
        await asyncio.sleep(0)
        order.append(("end", event.job_id))

    scheduler.register(ReasoningEvent.ADMITTED, handler)
    scheduler.submit(_event(job="a"))
    scheduler.submit(_event(job="b"))
    await scheduler.process_until_idle()
    assert order == [
        ("start", "a"), ("end", "a"), ("start", "b"), ("end", "b"),
    ]
    assert [e.job_id for e in scheduler.processed()] == ["a", "b"]


@pytest.mark.asyncio
async def test_bus_subscription_feeds_the_scheduler():
    bus = ReasoningEventBus()
    scheduler = ReasoningScheduler(bus)
    seen = []

    async def handler(event):
        seen.append(event.job_id)

    scheduler.register(None, handler)
    bus.emit(_event(job="from-bus"))
    await scheduler.process_until_idle()
    assert seen == ["from-bus"]


@pytest.mark.asyncio
async def test_scheduler_queue_is_bounded_and_fails_closed():
    bus = ReasoningEventBus()
    scheduler = ReasoningScheduler(bus, capacity=1)
    scheduler.submit(_event(job="first"))
    with pytest.raises(ReasoningAdmissionError, match="queue is full"):
        scheduler.submit(_event(job="second"))
    await scheduler.process_until_idle()


@pytest.mark.asyncio
async def test_background_worker_stops_cleanly():
    bus = ReasoningEventBus()
    scheduler = ReasoningScheduler(bus)
    seen = []

    async def handler(event):
        seen.append(event.job_id)

    scheduler.register(ReasoningEvent.IDLE, handler)
    await scheduler.start()
    bus.emit(_event(ReasoningEvent.IDLE, job="idle"))
    await asyncio.sleep(0)
    await asyncio.sleep(0)
    await scheduler.stop()
    assert seen == ["idle"]
