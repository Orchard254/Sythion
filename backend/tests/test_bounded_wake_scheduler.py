"""Time-based bounded scheduler: opt-in, capped, idle-safe, no implicit model call."""

import pytest

from backend.services.bounded_wake_scheduler import (
    BoundedWakeScheduler,
    WakeCycleReport,
    WakePolicy,
)


class IdleSelector:
    def pick_goal(self, *, respect_backoff=True):
        return None


class RunnableSelector:
    def pick_goal(self, *, respect_backoff=True):
        return {"goal_id": "goal-1", "priority": 1}


class BrokenSelector:
    def pick_goal(self, *, respect_backoff=True):
        raise RuntimeError("selector unavailable")


@pytest.fixture
def sleeps():
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    return recorded, fake_sleep


def test_policy_is_bounded_and_disabled_by_default():
    policy = WakePolicy()
    assert policy.enabled is False
    with pytest.raises(ValueError):
        WakePolicy(interval_seconds=0)
    with pytest.raises(ValueError):
        WakePolicy(max_cycles=0)


@pytest.mark.asyncio
async def test_disabled_scheduler_makes_no_calls(sleeps):
    _, fake_sleep = sleeps
    scheduler = BoundedWakeScheduler(sleep=fake_sleep)
    called = []

    async def wake(goal_id):
        called.append(goal_id)

    report = await scheduler.run(RunnableSelector(), wake, WakePolicy())
    assert report == WakeCycleReport(0, 0, 0, 0, stopped=True)
    assert called == []


@pytest.mark.asyncio
async def test_idle_cycles_never_invoke_the_wake_handler(sleeps):
    intervals, fake_sleep = sleeps
    scheduler = BoundedWakeScheduler(sleep=fake_sleep)
    called = []

    async def wake(goal_id):
        called.append(goal_id)

    report = await scheduler.run(
        IdleSelector(), wake,
        WakePolicy(enabled=True, interval_seconds=2.0, max_cycles=3))
    assert report.cycles == 3
    assert report.idle_cycles == 3
    assert report.work_cycles == 0
    assert called == []
    assert intervals == [2.0, 2.0, 2.0]


@pytest.mark.asyncio
async def test_runnable_work_is_bounded_by_max_cycles(sleeps):
    _, fake_sleep = sleeps
    scheduler = BoundedWakeScheduler(sleep=fake_sleep)
    called = []

    async def wake(goal_id):
        called.append(goal_id)

    report = await scheduler.run(
        RunnableSelector(), wake,
        WakePolicy(enabled=True, interval_seconds=1.0, max_cycles=4))
    assert called == ["goal-1"] * 4
    assert report.work_cycles == 4
    assert report.failed_cycles == 0


@pytest.mark.asyncio
async def test_stop_ends_the_loop_early(sleeps):
    _, fake_sleep = sleeps
    scheduler = BoundedWakeScheduler(sleep=fake_sleep)
    called = []

    async def wake(goal_id):
        called.append(goal_id)
        scheduler.stop()

    report = await scheduler.run(
        RunnableSelector(), wake,
        WakePolicy(enabled=True, interval_seconds=1.0, max_cycles=10))
    assert called == ["goal-1"]
    assert report.stopped is True
    assert report.cycles == 1


@pytest.mark.asyncio
async def test_selector_failure_is_recorded_not_retried(sleeps):
    _, fake_sleep = sleeps
    scheduler = BoundedWakeScheduler(sleep=fake_sleep)

    async def wake(goal_id):
        raise AssertionError("must not be called")

    report = await scheduler.run(
        BrokenSelector(), wake,
        WakePolicy(enabled=True, interval_seconds=1.0, max_cycles=2))
    assert report.failed_cycles == 2
    assert report.work_cycles == 0
