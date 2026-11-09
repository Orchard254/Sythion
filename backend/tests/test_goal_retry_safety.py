"""Pure retry-evidence gate checks; do not require a database."""
import pytest
from backend.services.goal_retry_safety import evaluate_failed_goal_retry_safety

VALID = dict(
    task_state="failed", owner_state="released", has_pending_action=False,
    has_task_action=False, has_owner_action=False, has_owner_uncertainty=False,
    has_uncertain_event=False, has_prior_mutation=False, unresolved_calls=0,
    has_corrupt_receipt=False,
)


def test_clean_failed_task_is_only_a_retry_policy_candidate():
    result = evaluate_failed_goal_retry_safety(**VALID)
    assert result == {"reason": "clear_for_retry_policy", "safe_to_consider_retry": True,
                      "authorization_issued": False, "execution_started": False}


@pytest.mark.parametrize("change", [
    {"task_state": "completed"}, {"task_state": "interrupted"},
    {"task_state": "running"}, {"has_pending_action": True},
    {"has_task_action": True}, {"has_owner_action": True},
    {"has_owner_uncertainty": True}, {"has_uncertain_event": True},
    {"has_prior_mutation": True}, {"unresolved_calls": 1},
    {"has_corrupt_receipt": True}, {"owner_state": "current"},
    {"owner_state": "expired"}, {"owner_state": "recovered"},
    {"owner_state": "indeterminate"}, {"owner_state": "EXPIRED"},
])
def test_unsafe_retry_evidence_requires_review(change):
    result = evaluate_failed_goal_retry_safety(**(VALID | change))
    assert not result["safe_to_consider_retry"]
    assert "required" in result["reason"]
    assert result["authorization_issued"] is False
    assert result["execution_started"] is False


def test_no_owner_is_permitted_for_legacy_failed_read_only_task():
    result = evaluate_failed_goal_retry_safety(**(VALID | {"owner_state": "missing"}))
    assert result["safe_to_consider_retry"]


@pytest.mark.parametrize("bad", [
    {"unresolved_calls": -1}, {"unresolved_calls": True},
    {"has_prior_mutation": 1}, {"has_owner_action": "false"},
    {"task_state": None}, {"owner_state": None},
])
def test_invalid_retry_evidence_refused(bad):
    with pytest.raises(ValueError):
        evaluate_failed_goal_retry_safety(**(VALID | bad))
