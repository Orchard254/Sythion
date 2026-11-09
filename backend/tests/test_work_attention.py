"""No-provider attention scheduling unit tests."""

from dataclasses import FrozenInstanceError

import pytest

from backend.services.work_attention import (
    AttentionAction,
    AttentionDecision,
    decide_background_attention,
)


class Selector:
    def __init__(self, choice):
        self.choice = choice
        self.calls = []

    def pick_goal(self, *, respect_backoff=True):
        self.calls.append(respect_backoff)
        return self.choice


def test_idle_does_not_invoke_model_or_run_tasks():
    selector = Selector(None)
    decision = decide_background_attention(selector)
    assert decision == AttentionDecision(AttentionAction.IDLE, "no_runnable_goal")
    assert decision.goal_id is None
    assert selector.calls == [True]


def test_runnable_goal_selected_once_with_backoff_enforced():
    selector = Selector({"goal_id": "goal-1", "priority": 1,
                         "next_step": {"id": 42}})
    decision = decide_background_attention(selector)
    assert decision.action == AttentionAction.RUN_GOAL
    assert decision.reason == "eligible_goal"
    assert decision.goal_id == "goal-1"
    assert selector.calls == [True]


def test_selected_goal_is_immutable():
    decision = decide_background_attention(Selector({"goal_id": "a"}))
    with pytest.raises(FrozenInstanceError):
        decision.goal_id = "b"


@pytest.mark.parametrize("choice", [False, 1, "goal-1", [], object()])
def test_reject_non_mapping_non_empty_selection(choice):
    with pytest.raises(TypeError, match="invalid selection"):
        decide_background_attention(Selector(choice))


@pytest.mark.parametrize("goal_id", [None, 0, False, "", "  ", " a "])
def test_reject_invalid_goal_identity(goal_id):
    with pytest.raises(ValueError, match="invalid goal reference"):
        decide_background_attention(Selector({"goal_id": goal_id}))


def test_empty_mapping_is_not_idle():
    with pytest.raises(ValueError, match="invalid goal reference"):
        decide_background_attention(Selector({}))


@pytest.mark.parametrize("action,reason,goal_id", [
    (AttentionAction.IDLE, "eligible_goal", None),
    (AttentionAction.IDLE, "no_runnable_goal", "g"),
    (AttentionAction.RUN_GOAL, "no_runnable_goal", "g"),
    (AttentionAction.RUN_GOAL, "eligible_goal", None),
    ("run_goal", "eligible_goal", "g"),
])
def test_invalid_attention_objects_fail_closed(action, reason, goal_id):
    with pytest.raises(ValueError):
        AttentionDecision(action, reason, goal_id)
