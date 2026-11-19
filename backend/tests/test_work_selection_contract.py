"""Single runtime-owned work-selection contract for all reasoning entry paths."""

import pytest

import backend.services.work_attention as work_attention
from backend.services.work_attention import (
    AttentionAction,
    WorkItem,
    WorkKind,
    WorkSelection,
    decide_background_attention,
    reasoning_signals_for,
    select_next_work,
)


def test_selection_is_deterministic_and_priority_driven():
    items = [
        WorkItem(WorkKind.GOAL_PLANNING, "goal-1", priority=1, arrival=0),
        WorkItem(WorkKind.TASK_DECISION, "task-1", priority=5, arrival=3),
        WorkItem(WorkKind.CONVERSATION, "chat-1", priority=5, arrival=1),
    ]
    first = select_next_work(items)
    assert first.selected is True
    assert first.kind is WorkKind.CONVERSATION
    assert first.work_id == "chat-1"
    assert select_next_work(list(reversed(items))) == first


def test_no_ready_work_is_an_explicit_idle_selection():
    selection = select_next_work([
        WorkItem(WorkKind.TASK_DECISION, "t", ready=False),
    ])
    assert selection == WorkSelection(selected=False, reason="no_ready_work")
    assert selection.kind is None and selection.work_id is None


def test_invalid_work_items_fail_closed():
    with pytest.raises(TypeError):
        select_next_work(["not-a-work-item"])


@pytest.mark.parametrize("kwargs", [
    {"work_id": ""},
    {"work_id": " padded "},
    {"priority": True},
    {"ready": "yes"},
    {"arrival": -1},
    {"kind": "conversation"},
])
def test_work_item_rejects_malformed_fields(kwargs):
    values = dict(kind=WorkKind.CONVERSATION, work_id="w")
    values.update(kwargs)
    with pytest.raises(ValueError):
        WorkItem(**values)


def test_signals_map_conversation_vs_background_work():
    chat = select_next_work([
        WorkItem(WorkKind.CONVERSATION, "chat", ready=True)])
    task = select_next_work([
        WorkItem(WorkKind.TASK_DECISION, "task", ready=True)])
    chat_signals = reasoning_signals_for(chat)
    task_signals = reasoning_signals_for(
        task, pending_mutating_action=True,
        budget_available=False, provider_available=False)
    assert chat_signals.has_new_input is True
    assert chat_signals.work_active is False
    assert task_signals.has_new_input is False
    assert task_signals.work_active is True
    assert task_signals.pending_mutating_action is True
    assert task_signals.budget_available is False
    assert task_signals.provider_available is False


def test_idle_selection_never_produces_dispatch_signals():
    signals = reasoning_signals_for(select_next_work([]))
    assert signals.has_new_input is False
    assert signals.work_active is False


def test_leased_wake_path_uses_the_shared_selection_contract(monkeypatch):
    class Selector:
        def pick_goal(self, *, respect_backoff=True):
            return {"goal_id": "goal-42", "priority": 3}

    seen = []
    original = work_attention.select_next_work

    def spy(items):
        seen.append(items)
        return original(items)

    monkeypatch.setattr(work_attention, "select_next_work", spy)
    decision = decide_background_attention(Selector())
    assert decision.action is AttentionAction.RUN_GOAL
    assert decision.goal_id == "goal-42"
    assert len(seen) == 1
    assert seen[0][0].kind is WorkKind.GOAL_PLANNING
    assert seen[0][0].work_id == "goal-42"
