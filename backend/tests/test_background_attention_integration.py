"""Runtime integration regressions for leased deterministic attention.

These tests require Sythion's actual task orchestrator and configuration.
They do not call a model or use real tools.
"""

import pytest

from backend.services.task_orchestrator import TaskOrchestrator


class LeaseStore:
    def __init__(self, *, available=True):
        self.available = available
        self.acquired = 0
        self.released = 0

    async def acquire_lease(self):
        self.acquired += 1
        return self.available

    async def release_lease(self):
        self.released += 1


class Selector:
    def __init__(self, value):
        self.value = value
        self.calls = []

    def pick_goal(self, *, respect_backoff=True):
        self.calls.append(respect_backoff)
        return self.value


async def unexpected_tool_or_model_call(*_args):
    raise AssertionError("idle or ineligible work must not invoke task execution")


@pytest.mark.asyncio
async def test_no_runnable_goal_is_idle_and_releases_lease_exactly_once():
    store = LeaseStore()
    selector = Selector(None)
    runner = TaskOrchestrator(state_store=store, planner=None)
    result = await runner.run_background_cycle(
        selector, max_steps=4, run_task_fn=unexpected_tool_or_model_call
    )
    assert result == {"executed": [], "stopped_reason": "no_runnable_goal"}
    assert (store.acquired, store.released) == (1, 1)
    assert selector.calls == [True]


@pytest.mark.asyncio
async def test_goal_ineligible_between_selection_and_step_start_is_idle(monkeypatch):
    store = LeaseStore()
    selector = Selector({"goal_id": "still-a-valid-reference"})
    runner = TaskOrchestrator(state_store=store, planner=None)

    async def became_ineligible(_goal_id):
        return None

    monkeypatch.setattr(runner, "start_next_step", became_ineligible)
    result = await runner.run_background_cycle(
        selector, max_steps=3, run_task_fn=unexpected_tool_or_model_call
    )
    assert result == {"executed": [], "stopped_reason": "goal_became_ineligible"}
    assert (store.acquired, store.released) == (1, 1)
    assert selector.calls == [True]


@pytest.mark.asyncio
async def test_lease_refusal_does_not_read_goals_or_run_model():
    store = LeaseStore(available=False)
    selector = Selector({"goal_id": "g"})
    runner = TaskOrchestrator(state_store=store, planner=None)
    result = await runner.run_background_cycle(
        selector, max_steps=2, run_task_fn=unexpected_tool_or_model_call
    )
    assert result == {"executed": [], "stopped_reason": "lease_unavailable"}
    assert (store.acquired, store.released) == (1, 0)
    assert selector.calls == []


@pytest.mark.asyncio
async def test_invalid_goal_reference_releases_lease_and_fails_closed():
    store = LeaseStore()
    selector = Selector({"goal_id": "  "})
    runner = TaskOrchestrator(state_store=store, planner=None)
    with pytest.raises(ValueError, match="invalid goal reference"):
        await runner.run_background_cycle(
            selector, max_steps=2, run_task_fn=unexpected_tool_or_model_call
        )
    assert (store.acquired, store.released) == (1, 1)
    assert selector.calls == [True]
