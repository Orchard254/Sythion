"""Adversarial, privacy-safe checks for task reasoning history."""

import pytest

from backend.services.reasoning_receipt_integrity import audit_reasoning_receipts


def receipt(seq, phase, job="job-1", **overrides):
    return {
        "sequence_num": seq,
        "event_type": "reasoning." + phase,
        "event_data": {"job_id": job, "purpose": "task_decision", **overrides},
    }


def test_empty_history_is_clean():
    result = audit_reasoning_receipts([])
    assert result.valid
    assert result.unresolved_count == 0
    assert result.anomalies == ()


def test_valid_pair_with_interleaved_nonreasoning_and_sequence_gaps():
    rows = [receipt(1, "started"),
            {"sequence_num": 2, "event_type": "tool.read", "event_data": {}},
            receipt(11, "response_received")]
    result = audit_reasoning_receipts(rows)
    assert result.valid
    assert (result.starts, result.terminals, result.unresolved_count) == (1, 1, 0)


def test_multiple_interleaved_calls_with_one_unresolved():
    result = audit_reasoning_receipts([
        receipt(2, "started", "a"), receipt(3, "started", "b"),
        receipt(4, "failed", "a"), receipt(6, "started", "c"),
        receipt(7, "cancelled", "c"),
    ])
    assert result.valid
    assert result.unresolved_count == 1
    assert (result.starts, result.terminals) == (3, 2)


@pytest.mark.parametrize("terminal", [
    "response_received", "failed", "timed_out", "cancelled",
])
def test_each_terminal_kind_pairs_once(terminal):
    result = audit_reasoning_receipts([receipt(1, "started"), receipt(2, terminal)])
    assert result.valid
    assert result.unresolved_count == 0


def test_duplicate_start_is_corruption_even_if_one_terminal_exists():
    result = audit_reasoning_receipts([
        receipt(1, "started"), receipt(2, "started"), receipt(3, "response_received")
    ])
    assert not result.valid
    assert "duplicate_start" in result.anomalies


def test_reused_completed_job_id_is_not_new_clean_call():
    result = audit_reasoning_receipts([
        receipt(1, "started"), receipt(2, "failed"), receipt(3, "started"),
        receipt(4, "response_received"),
    ])
    assert "duplicate_start" in result.anomalies


def test_orphan_terminal_cannot_make_history_look_complete():
    result = audit_reasoning_receipts([receipt(1, "response_received")])
    assert not result.valid
    assert "terminal_without_start" in result.anomalies


def test_duplicate_terminal_is_corruption():
    result = audit_reasoning_receipts([
        receipt(1, "started"), receipt(2, "failed"), receipt(3, "response_received")
    ])
    assert "terminal_without_start" in result.anomalies


@pytest.mark.parametrize("sequence", [None, 0, -1, "1", True, 1.0])
def test_sequence_must_be_positive_integer(sequence):
    row = receipt(1, "started")
    row["sequence_num"] = sequence
    assert "invalid_sequence" in audit_reasoning_receipts([row]).anomalies


def test_duplicate_and_out_of_order_sequence_fail():
    for last in (1, 0):
        r = audit_reasoning_receipts([receipt(1, "started", "a"), receipt(last, "started", "b")])
        assert "invalid_sequence" in r.anomalies


@pytest.mark.parametrize("kind", ["reasoning.approved", "reasoning.replayed", "reasoning.unknown"])
def test_unknown_reasoning_kind_does_not_silently_disappear(kind):
    r = audit_reasoning_receipts([{"sequence_num": 1, "event_type": kind, "event_data": {}}])
    assert "unknown_reasoning_event" in r.anomalies


@pytest.mark.parametrize("data", [
    None, {}, [], {"job_id": "x"}, {"job_id": "x", "purpose": "conversation"},
    {"job_id": "  ", "purpose": "task_decision"},
    {"job_id": " x", "purpose": "task_decision"},
    {"job_id": "x", "purpose": "task_decision", "approved": True},
    {"job_id": 4, "purpose": "task_decision"},
    {"job_id": "x"*129, "purpose": "task_decision"},
])
def test_invalid_metadata_is_never_accepted(data):
    row = receipt(1, "started")
    row["event_data"] = data
    result = audit_reasoning_receipts([row])
    assert not result.valid
    assert "invalid_receipt_metadata" in result.anomalies


def test_corruption_result_contains_no_job_ids_or_raw_values():
    rows = [receipt(1, "started", "sensitive-private-job-id", secret="PRIVATE")]
    result = audit_reasoning_receipts(rows)
    assert "sensitive-private-job-id" not in repr(result)
    assert "PRIVATE" not in repr(result)


def test_nonreasoning_action_events_are_ignored_not_interpreted_as_calls():
    result = audit_reasoning_receipts([
        {"sequence_num": 1, "event_type": "action.started", "event_data": {"job_id":"x"}}
    ])
    assert result.valid
    assert result.unresolved_count == 0


def test_broken_event_object_fails_closed():
    assert "invalid_event" in audit_reasoning_receipts([False]).anomalies


def test_auditor_never_mutates_receipts():
    rows = [receipt(1, "started"), receipt(2, "response_received")]
    from copy import deepcopy
    before = deepcopy(rows)
    audit_reasoning_receipts(rows)
    assert rows == before


def test_non_iterable_raises():
    with pytest.raises(TypeError):
        audit_reasoning_receipts(None)
