"""Standalone fail-closed tests for explicit resume admission."""
from datetime import datetime, timedelta
import pytest

from backend.services.resume_admission import evaluate_resume_admission

NOW = datetime(2026, 11, 9, 12)


def owner(status, delta=30, **kwargs):
    return {"status": status, "expires_at": NOW + timedelta(seconds=delta), **kwargs}


def decision(state="interrupted", count=0, owner_snapshot=None, **kwargs):
    return evaluate_resume_admission(
        task_state=state, unresolved_calls=count, owner=owner_snapshot,
        has_pending_action=kwargs.get("has_pending_action", False),
        has_task_action=kwargs.get("has_task_action", False),
        has_uncertain_event=kwargs.get("has_uncertain_event", False), now=NOW,
    )


@pytest.mark.parametrize("state", ["interrupted", "paused"])
@pytest.mark.parametrize("ownership", [None, owner("RELEASED")])
def test_clean_explicit_resume_preflight_is_not_auto_execution(state, ownership):
    result = decision(state, owner_snapshot=ownership)
    assert result["preflight_clear"] is True
    assert result["reason"] == "clear_for_explicit_resume"
    assert result["automatic_replay_allowed"] is False


@pytest.mark.parametrize("state", ["created", "running", "waiting_for_user", "completed", "failed", "cancelled"])
def test_unresumable_states_refused(state):
    assert decision(state)["reason"] == "task_not_resumable"


@pytest.mark.parametrize("ownership", [owner("ACTIVE"), owner("ACTIVE", delta=-4), owner("RECOVERED"), owner("EXPIRED"), owner("MYSTERY")])
def test_live_expired_or_unreconciled_ownership_is_not_stolen(ownership):
    result = decision(owner_snapshot=ownership)
    assert result["reason"] == "ownership_recovery_required"
    assert not result["preflight_clear"]


@pytest.mark.parametrize("extra", [
    {"has_task_action": True}, {"has_uncertain_event": True},
    {"owner_snapshot": owner("RELEASED", uncertain_actions=True)},
    {"owner_snapshot": owner("RELEASED", active_action=True)},
])
def test_external_effect_has_priority_and_must_be_reviewed(extra):
    assert decision(**extra)["reason"] == "external_effect_review_required"


def test_unresolved_llm_result_does_not_authorize_replay():
    result = decision(count=1)
    assert result["reason"] == "unresolved_reasoning_review_required"
    assert result["unresolved_reasoning_calls"] == 1
    assert result["automatic_replay_allowed"] is False


def test_approval_state_is_not_execution_authority():
    assert decision(has_pending_action=True)["reason"] == "pending_action_review_required"


def test_external_action_has_priority_over_unresolved_llm():
    assert decision(count=1, has_uncertain_event=True)["reason"] == "external_effect_review_required"


@pytest.mark.parametrize("invalid", [
    {"task_state": ""}, {"unresolved_calls": -1}, {"unresolved_calls": True},
    {"owner": "not an owner"}, {"has_pending_action": 1},
    {"has_task_action": "yes"}, {"has_uncertain_event": None}, {"now": "tomorrow"},
])
def test_invalid_input_rejected(invalid):
    kwargs=dict(task_state="interrupted",unresolved_calls=0,owner=None,
                has_pending_action=False,has_task_action=False,
                has_uncertain_event=False,now=NOW)
    kwargs.update(invalid)
    with pytest.raises(ValueError):
        evaluate_resume_admission(**kwargs)


def test_prior_mutation_blocks_even_if_result_was_reported_successful():
    result = evaluate_resume_admission(
        task_state="paused", unresolved_calls=0, owner=None,
        has_pending_action=False, has_task_action=False, has_uncertain_event=False,
        has_prior_mutation=True, now=NOW,
    )
    assert result["reason"] == "prior_mutating_effect_review_required"
    assert not result["preflight_clear"]
