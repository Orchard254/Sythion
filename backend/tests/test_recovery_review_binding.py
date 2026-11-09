"""Pure tests for the recovery metadata revision (no secrets or side effects)."""
from datetime import datetime, timedelta
import pytest

from backend.services.recovery_review_binding import calculate_recovery_revision

NOW = datetime(2026, 11, 9, 12, 30, 1)


def stamp(**changes):
    fields = dict(
        task_id="task-abc", task_version=2, task_state="interrupted",
        task_updated_at=NOW, last_event_sequence=14,
        owner_version=3, owner_fencing_token=5, owner_status="RELEASED",
        owner_expires_at=NOW - timedelta(minutes=5),
        pending_action=False, active_action=False,
        preflight={"reason":"clear_for_explicit_resume","owner_lease":"released",
                   "task_state":"interrupted","unresolved_reasoning_calls":0,"preflight_clear":True},
    )
    fields.update(changes)
    return calculate_recovery_revision(**fields)


def test_stable_for_identical_snapshot():
    assert stamp() == stamp()
    assert stamp().startswith("sha256:") and len(stamp()) == 71


@pytest.mark.parametrize("change", [
    {"task_id": "other"}, {"task_version": 3}, {"task_state": "paused"},
    {"task_updated_at": NOW + timedelta(seconds=1)},
    {"last_event_sequence": 15}, {"owner_version": 4},
    {"owner_fencing_token": 6}, {"owner_status": "RECOVERED"},
    {"owner_expires_at": NOW + timedelta(seconds=1)},
    {"pending_action": True}, {"active_action": True},
])
def test_change_invalidates_revision(change):
    if change.get("task_state"):
        change = {**change, "preflight": {
            "reason":"clear_for_explicit_resume","owner_lease":"released",
            "task_state":"paused","unresolved_reasoning_calls":0,"preflight_clear":True}}
    assert stamp(**change) != stamp()


def test_reason_or_lease_change_invalidates_revision():
    clear = dict(reason="clear_for_explicit_resume", owner_lease="released",
                 task_state="interrupted", unresolved_reasoning_calls=0, preflight_clear=True)
    assert stamp(preflight={**clear, "reason":"ownership_recovery_required","preflight_clear":False}) != stamp()
    assert stamp(preflight={**clear, "owner_lease":"expired","preflight_clear":False}) != stamp()
    assert stamp(preflight={**clear, "unresolved_reasoning_calls":1,"preflight_clear":False}) != stamp()


def test_missing_ownership_supported_and_stable():
    a = stamp(owner_version=None, owner_fencing_token=None, owner_status=None, owner_expires_at=None)
    assert a == stamp(owner_version=None, owner_fencing_token=None, owner_status=None, owner_expires_at=None)
    assert a != stamp()


@pytest.mark.parametrize("change", [
    {"task_id": " "}, {"task_version": 0}, {"task_version": True},
    {"task_updated_at": None}, {"last_event_sequence": -1},
    {"owner_version": None}, {"owner_fencing_token": None},
    {"owner_version": 0}, {"owner_fencing_token": 0},
    {"owner_expires_at": "not a time"}, {"pending_action": 1},
    {"active_action": "true"}, {"preflight": None},
    {"preflight": {"reason":"clear_for_explicit_resume","owner_lease":"released",
                   "task_state":"running","unresolved_reasoning_calls":0,"preflight_clear":True}},
])
def test_invalid_or_inconsistent_input_fails_closed(change):
    with pytest.raises(ValueError):
        stamp(**change)


def test_reasoning_prompt_credentials_and_owner_nonce_are_not_inputs():
    assert "prompt" not in calculate_recovery_revision.__code__.co_varnames
    assert "owner_nonce" not in calculate_recovery_revision.__code__.co_varnames
    assert "credential" not in calculate_recovery_revision.__code__.co_varnames
