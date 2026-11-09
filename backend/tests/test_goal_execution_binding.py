"""Standalone goal-task binding checks; no ORM or live executor needed."""
import pytest
from backend.services.goal_execution_binding import goal_step_task_binding_is_valid

GOOD = dict(
    requested_task_id="task-a", evidence_task_id="task-a",
    linked_task_id="task-a", step_goal_id="goal-a", task_goal_id="goal-a",
    plan_goal_id="goal-a", plan_status="active", matching_step_count=1,
)


def test_active_unique_goal_task_binding_is_eligible_for_verification():
    assert goal_step_task_binding_is_valid(**GOOD)


def test_legacy_unassigned_task_uses_exact_step_binding():
    assert goal_step_task_binding_is_valid(**(GOOD | {"task_goal_id": None}))


@pytest.mark.parametrize("changed", [
    {"requested_task_id": None}, {"requested_task_id": ""},
    {"requested_task_id": "task-b"}, {"evidence_task_id": None},
    {"evidence_task_id": "task-b"}, {"linked_task_id": None},
    {"linked_task_id": "task-b"}, {"step_goal_id": None},
    {"step_goal_id": ""}, {"task_goal_id": "foreign-goal"},
    {"plan_goal_id": "foreign-goal"}, {"plan_status": "superseded"},
    {"plan_status": "rejected"}, {"plan_status": None},
    {"matching_step_count": 0}, {"matching_step_count": 2},
    {"matching_step_count": True},
])
def test_unknown_foreign_superseded_or_duplicate_binding_never_verified(changed):
    assert not goal_step_task_binding_is_valid(**(GOOD | changed))
