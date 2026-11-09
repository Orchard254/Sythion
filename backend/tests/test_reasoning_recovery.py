"""Pure recovery-preflight tests: deterministic, no provider and no DB writes."""

from datetime import datetime, timedelta, timezone

import pytest

from backend.services.reasoning_recovery import assess_reasoning_recovery

NOW = datetime(2026, 11, 9, 12, 0, 0)


def owner(status="ACTIVE", delta=10, **kwargs):
    return {"status": status, "expires_at": NOW + timedelta(seconds=delta), **kwargs}


def assess(state="running", calls=1, record=None):
    return assess_reasoning_recovery(state, calls, record, now=NOW)


def test_live_lease_never_claims_worker_is_certainly_alive():
    result = assess(record=owner())
    assert result["state"] == "possibly_running"
    assert result["owner_lease"] == "current"
    assert result["may_still_be_running"]
    assert not result["automatic_replay_allowed"]
    assert not result["review_required"]


def test_expired_lease_requires_review_and_never_replays():
    result = assess(record=owner(delta=-1))
    assert result["state"] == "stale_reasoning_review"
    assert result["owner_lease"] == "expired"
    assert result["review_required"]
    assert not result["automatic_replay_allowed"]


@pytest.mark.parametrize("status,expected", [
    ("RELEASED", "released"), ("RECOVERED", "recovered"),
    ("EXPIRED", "expired"), ("unexpected", "indeterminate"),
])
def test_unresolved_without_active_owner_needs_review(status, expected):
    result = assess(record=owner(status=status))
    assert result["owner_lease"] == expected
    assert result["state"] in {"unresolved_reasoning_review", "stale_reasoning_review"}
    assert result["review_required"]


def test_missing_owner_is_unknown_not_proof_of_no_execution():
    result = assess(record=None)
    assert result["state"] == "unresolved_reasoning_review"
    assert result["owner_lease"] == "missing"


def test_exact_expiration_boundary_is_stale():
    result = assess(record=owner(delta=0))
    assert result["owner_lease"] == "expired"


def test_no_expiration_or_bad_clock_is_indeterminate():
    assert assess(record={"status": "ACTIVE"})["owner_lease"] == "indeterminate"
    aware = {"status": "ACTIVE", "expires_at": datetime.now(timezone.utc)}
    assert assess(record=aware)["owner_lease"] == "indeterminate"


@pytest.mark.parametrize("state,expected", [
    ("completed", "historical_ambiguity_review"),
    ("failed", "historical_ambiguity_review"),
    ("cancelled", "historical_ambiguity_review"),
    ("paused", "unresolved_reasoning_review"),
    ("waiting_for_user", "unresolved_reasoning_review"),
    ("interrupted", "interrupted_review"),
])
def test_unresolved_does_not_override_task_state(state, expected):
    result = assess(state, record=owner())
    assert result["state"] == expected
    assert result["review_required"]


@pytest.mark.parametrize("state,expected", [
    ("completed", "terminal"),
    ("failed", "terminal"),
    ("cancelled", "terminal"),
    ("paused", "paused"),
    ("waiting_for_user", "waiting_for_user"),
    ("created", "not_started"),
    ("running", "unowned_running_review"),
    ("interrupted", "interrupted_review"),
])
def test_no_unresolved_receipts_still_respects_task_lifecycle(state, expected):
    assert assess(state, 0)["state"] == expected


def test_running_expired_even_without_unresolved_call_is_reviewed():
    assert assess("running", 0, owner(delta=-1))["state"] == "stale_task_review"


def test_running_current_without_unresolved_call_is_only_possibly_live():
    assert assess("running", 0, owner())["state"] == "possibly_running"


@pytest.mark.parametrize("flag", ["active_action", "uncertain_actions"])
def test_external_action_uncertainty_overrides_reasoning_status(flag):
    result = assess("completed", 0, owner(**{flag: True}))
    assert result["state"] == "external_effect_review"
    assert result["external_effect_uncertain"]
    assert result["review_required"]
    assert not result["automatic_replay_allowed"]


def test_unknown_task_state_fails_safe():
    assert assess("unknown", 0)["state"] == "unknown_task_state_review"


@pytest.mark.parametrize("kwargs", [
    {"task_state": "", "unresolved_calls": 0, "owner": None},
    {"task_state": "running", "unresolved_calls": -1, "owner": None},
    {"task_state": "running", "unresolved_calls": True, "owner": None},
    {"task_state": "running", "unresolved_calls": 0, "owner": "bad"},
    {"task_state": "running", "unresolved_calls": 0, "owner": {"active_action": "yes"}},
    {"task_state": "running", "unresolved_calls": 0, "owner": {"uncertain_actions": 1}},
])
def test_invalid_inputs_fail_closed(kwargs):
    with pytest.raises(ValueError):
        assess_reasoning_recovery(**kwargs, now=NOW)


def test_never_mutates_owner_snapshot():
    record = owner()
    original = dict(record)
    assess(record=record)
    assert record == original
