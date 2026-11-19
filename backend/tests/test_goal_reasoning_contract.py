"""Goal planning uses the same admission/result contract as other entry paths."""

import json

import pytest

from backend.services.goal_manager import GoalManager
from backend.services.planner import Planner, PlanValidationError


class _Client:
    def __init__(self, response, identity="fake-planner"):
        self.response = response
        self.calls = 0
        self._identity = identity

    def provider_name(self):
        return self._identity

    async def decide(self, messages, tools, max_tokens=4000):
        self.calls += 1
        return self.response


def _plan_response():
    return {"message": {"content": json.dumps({"steps": [
        {"key": "a", "description": "first"},
    ]})}}


@pytest.fixture
def goal(test_db):
    return GoalManager(test_db).create_goal("Contract goal")


def test_valid_goal_plan_uses_the_boundary(test_db, goal):
    client = _Client(_plan_response())
    candidates = Planner(test_db).request_model_plan(goal, model_client=client)
    assert [c.key for c in candidates] == ["a"]
    assert client.calls == 1


@pytest.mark.parametrize("bad", ["not a mapping", ["list"], 42])
def test_invalid_result_shape_is_rejected_without_a_second_call(test_db, goal, bad):
    client = _Client(bad)
    with pytest.raises(PlanValidationError):
        Planner(test_db).request_model_plan(goal, model_client=client)
    assert client.calls == 1


def test_terminal_goal_admits_no_reasoning_call(test_db, goal):
    goal.status = "completed"
    client = _Client(_plan_response())
    with pytest.raises(PlanValidationError):
        Planner(test_db).request_model_plan(goal, model_client=client)
    assert client.calls == 0


def test_unidentified_processor_is_rejected_before_any_call(test_db, goal):
    client = _Client(_plan_response(), identity="")
    with pytest.raises(PlanValidationError):
        Planner(test_db).request_model_plan(goal, model_client=client)
    assert client.calls == 0
