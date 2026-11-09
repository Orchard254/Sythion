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
