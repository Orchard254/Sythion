"""Isolated active-plan finalization gate checks."""
import pytest
from backend.services.goal_completion_policy import may_complete_active_goal

GOOD = {
    "goal_status": "active", "active_plan_count": 1,
    "step_statuses": ["completed", "completed"],
    "criteria_required": False, "criteria_verified": False,
}


def test_one_active_plan_all_verified_steps_can_finish():
    assert may_complete_active_goal(**GOOD)


def test_cancelled_not_required_when_at_least_one_completed():
    assert may_complete_active_goal(**(GOOD | {"step_statuses": ["completed", "cancelled"]}))


def test_verified_explicit_completion_criteria():
    assert may_complete_active_goal(**(GOOD | {"criteria_required": True, "criteria_verified": True}))


@pytest.mark.parametrize("change", [
    {"goal_status": "paused"}, {"goal_status": "completed"},
    {"goal_status": "failed"}, {"active_plan_count": 0},
    {"active_plan_count": 2}, {"step_statuses": []},
    {"step_statuses": ["cancelled", "cancelled"]},
    {"step_statuses": ["completed", "failed"]},
    {"step_statuses": ["completed", "skipped"]},
    {"step_statuses": ["completed", "blocked"]},
    {"step_statuses": ["completed", "running"]},
    {"step_statuses": ["completed", "ready"]},
    {"criteria_required": True},
])
def test_cannot_finalize_unknown_open_or_unverified_current_plan(change):
    assert not may_complete_active_goal(**(GOOD | change))


@pytest.mark.parametrize("change", [
    {"goal_status": None}, {"active_plan_count": True},
    {"active_plan_count": -1}, {"step_statuses": "completed"},
    {"step_statuses": ["completed", None]},
    {"criteria_required": 1}, {"criteria_verified": None},
])
def test_invalid_completion_projection_refused(change):
    with pytest.raises(ValueError):
        may_complete_active_goal(**(GOOD | change))
