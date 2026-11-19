"""Opt-in morning-brief scheduler: default off, local-hour gated, idempotent."""

import asyncio
from datetime import datetime

import pytest

from backend.models.database import MorningBrief
from backend.services.morning_brief_scheduler import (
    MorningBriefCycleReport,
    MorningBriefPolicy,
    MorningBriefScheduler,
)


MORNING = datetime(2026, 11, 20, 9, 0, 0)
NIGHT = datetime(2026, 11, 20, 3, 0, 0)


def test_policy_is_bounded_and_disabled_by_default():
    policy = MorningBriefPolicy()
    assert policy.enabled is False
    with pytest.raises(ValueError):
        MorningBriefPolicy(local_hour=24)
    with pytest.raises(ValueError):
        MorningBriefPolicy(check_interval_seconds=0)


@pytest.mark.asyncio
async def test_disabled_scheduler_makes_no_brief(test_db):
    scheduler = MorningBriefScheduler()
    created = await scheduler.run_once(test_db, MorningBriefPolicy(), now=MORNING)
    assert created is False
    assert test_db.query(MorningBrief).count() == 0


@pytest.mark.asyncio
async def test_before_configured_hour_creates_nothing(test_db):
    scheduler = MorningBriefScheduler()
    created = await scheduler.run_once(
        test_db, MorningBriefPolicy(enabled=True, local_hour=8), now=NIGHT)
    assert created is False
    assert test_db.query(MorningBrief).count() == 0


@pytest.mark.asyncio
async def test_at_hour_creates_one_brief_then_idempotent(test_db):
    scheduler = MorningBriefScheduler()
    policy = MorningBriefPolicy(enabled=True, local_hour=8)
    assert await scheduler.run_once(test_db, policy, now=MORNING) is True
    assert await scheduler.run_once(test_db, policy, now=MORNING) is False
    assert test_db.query(MorningBrief).count() == 1


@pytest.mark.asyncio
async def test_stop_ends_the_loop_early(test_db):
    recorded = []

    async def fake_sleep(seconds):
        recorded.append(seconds)

    policy = MorningBriefPolicy(
        enabled=True, local_hour=8, check_interval_seconds=5)
    scheduler = MorningBriefScheduler(sleep=fake_sleep)
    scheduler.stop()
    report = await scheduler.run(lambda: test_db, policy)
    assert report == MorningBriefCycleReport(0, 0, True)
    assert recorded == []


@pytest.mark.asyncio
async def test_registry_lifecycle_is_inert_when_disabled():
    from backend.services.morning_brief_scheduler import (
        get_morning_brief_scheduler, set_morning_brief_scheduler,
        start_morning_brief_scheduler, stop_morning_brief_scheduler,
        task_is_running,
    )

    assert start_morning_brief_scheduler(object(), MorningBriefPolicy()) is False
    assert task_is_running() is False
    assert get_morning_brief_scheduler() is None
    set_morning_brief_scheduler(MorningBriefScheduler())
    assert get_morning_brief_scheduler() is not None
    await stop_morning_brief_scheduler()
    assert get_morning_brief_scheduler() is None
