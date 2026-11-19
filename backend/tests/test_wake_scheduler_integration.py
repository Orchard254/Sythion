"""Shared schedulers wired into the real wake path (opt-in, non-authorizing)."""

import pytest

from backend.services.bounded_wake_scheduler import WakePolicy
from backend.services.reasoning_dispatch import (
    ReasoningEvent, ReasoningEventBus, get_reasoning_metrics,
)
from backend.services.reasoning_scheduler import ReasoningScheduler
from backend.services.wake_worker import WakeWorker, emit_wake_outcome


@pytest.fixture(autouse=True)
def clean_metrics():
    metrics = get_reasoning_metrics()
    metrics.reset()
    yield
    metrics.reset()


def test_wake_outcomes_are_observable_without_granting_authority():
    emit_wake_outcome("wake-1", {"executed": [], "stopped_reason": "no_runnable_goal"})
    emit_wake_outcome("wake-2", {"executed": [{"outcome": "completed"}],
                                 "stopped_reason": "step_cap"})
    events = get_reasoning_metrics().events.history()
    assert [e.event for e in events] == [
        ReasoningEvent.IDLE, ReasoningEvent.ADMITTED,
    ]
    assert [e.job_id for e in events] == ["wake-1", "wake-2"]
    assert all(e.provider_id == "runtime" for e in events)


@pytest.mark.asyncio
async def test_shared_scheduler_consumes_real_wake_events():
    bus = get_reasoning_metrics().events
    scheduler = ReasoningScheduler(bus)
    seen = []

    async def handler(event):
        seen.append(event.job_id)

    scheduler.register(ReasoningEvent.IDLE, handler)
    emit_wake_outcome("wake-3", {"executed": [], "stopped_reason": "no_runnable_goal"})
    await scheduler.process_until_idle()
    assert seen == ["wake-3"]


@pytest.mark.asyncio
async def test_opt_in_bounded_cycle_is_not_the_default_worker_loop():
    worker = WakeWorker(db_manager=object())
    assert worker.reasoning_scheduler is None
    bus = ReasoningEventBus()
    scheduler = ReasoningScheduler(bus)
    worker.install_reasoning_scheduler(scheduler)
    assert worker.reasoning_scheduler is scheduler

    class Selector:
        def pick_goal(self, *, respect_backoff=True):
            return None

    async def wake(goal_id):
        raise AssertionError("no work must not call the wake handler")

    report = await worker.run_bounded_wake_cycle(
        Selector(), wake,
        WakePolicy(enabled=True, interval_seconds=0.1, max_cycles=2),
    )
    assert report.idle_cycles == 2
    assert report.work_cycles == 0
