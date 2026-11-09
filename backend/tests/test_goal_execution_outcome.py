"""Pure post-worker outcome classification regressions."""

import pytest

from backend.services.goal_execution_outcome import classify_step_outcome


def observed(**updates):
    values = dict(requested_task_id='work-task', evidence_task_id='work-task',
                  step_status='completed', task_state='completed', completion_error=None)
    values.update(updates)
    return classify_step_outcome(**values)


def test_verified_step_success():
    assert observed() == 'completed'


def test_post_pipeline_applied_success_is_still_success():
    assert observed(step_status='completed') == 'completed'


def test_post_pipeline_applied_failure_is_not_success():
    assert observed(step_status='failed', task_state='failed') == 'failed'


def test_completed_task_with_rejected_step_is_failure():
    assert observed(step_status='failed', completion_error='mutating output unverified') == 'failed'


@pytest.mark.parametrize('status', ['created', 'running', 'interrupted', 'cancelled', 'waiting_for_user', 'paused', None])
def test_nonterminal_or_interrupted_task_not_success(status):
    assert observed(task_state=status) == 'unverified'


@pytest.mark.parametrize('step', ['running', 'ready', 'blocked', 'pending', 'cancelled', 'skipped', None])
def test_step_not_finished_cannot_claim_success(step):
    assert observed(step_status=step) == 'unverified'


def test_previous_task_link_not_current_execution():
    assert observed(evidence_task_id='older-task') == 'unverified'


def test_missing_evidence_link():
    assert observed(evidence_task_id=None) == 'unverified'


def test_verifier_error_overrides_completed_status():
    assert observed(completion_error='uncertain side effect') == 'unverified'


def test_failed_step_cannot_be_claimed_on_nonterminal_task():
    assert observed(step_status='failed', task_state='running') == 'unverified'


def test_worker_claim_and_status_not_inputs():
    assert 'worker_claim' not in classify_step_outcome.__code__.co_varnames


def test_empty_request_id_rejected():
    with pytest.raises(ValueError):
        observed(requested_task_id='  ')
