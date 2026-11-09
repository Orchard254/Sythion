"""Startup recovery does not silently authorize re-execution."""
from dataclasses import FrozenInstanceError
import pytest
from backend.services.goal_restart_policy import classify_goal_restart

@pytest.mark.parametrize('task_state', [None, 'interrupted', 'cancelled', 'created', 'running', 'waiting_for_user', 'paused', 'weird'])
def test_unresolved_task_state_never_authorizes_replay(task_state):
    result = classify_goal_restart(task_state)
    assert not result.reconcile_terminal
    assert result.requires_review
    assert not result.replay_allowed
    assert not result.authorization_issued
    assert result.outcome.startswith('deferred_')

@pytest.mark.parametrize('task_state', ['completed', 'failed'])
def test_terminal_task_requires_evidence_reconciliation_not_replay(task_state):
    result = classify_goal_restart(task_state)
    assert result.reconcile_terminal
    assert not result.requires_review
    assert not result.replay_allowed
    assert not result.authorization_issued


def test_same_input_yields_same_non_authorizing_verdict():
    assert classify_goal_restart('interrupted') == classify_goal_restart('interrupted')


def test_recovery_does_not_accept_caller_approval_flag():
    with pytest.raises(TypeError):
        classify_goal_restart('interrupted', approved=True)


def test_immutable_policy_result():
    result = classify_goal_restart('interrupted')
    with pytest.raises(FrozenInstanceError):
        result.replay_allowed = True


@pytest.mark.parametrize("change", [
    {"task_state": "completed"}, {"task_state": "interrupted"},
    {"task_state": "running"}, {"task_state": None},
    {"task_id": None}, {"evidence_task_id": None},
    {"linked_task_id": "task-b"}, {"task_goal_id": "other-goal"},
])
def test_failed_goal_retry_evidence_gate_rejects_unknown_or_mismatched(change):
    from backend.services.goal_restart_policy import failed_goal_task_has_matching_evidence
    valid = dict(
        task_state="failed", task_id="task-a", evidence_task_id="task-a",
        linked_task_id="task-a", task_goal_id="goal-a", step_goal_id="goal-a",
    )
    assert not failed_goal_task_has_matching_evidence(**(valid | change))


@pytest.mark.parametrize("owner", ["goal-a", None])
def test_failed_goal_retry_evidence_gate_allows_only_matching_failed_task(owner):
    from backend.services.goal_restart_policy import failed_goal_task_has_matching_evidence
    assert failed_goal_task_has_matching_evidence(
        task_state="failed", task_id="task-a", evidence_task_id="task-a",
        linked_task_id="task-a", task_goal_id=owner, step_goal_id="goal-a",
    )
