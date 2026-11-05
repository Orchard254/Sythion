"""Offline tests for the shared canonical startup-status projection."""

import pytest

from backend.services.bootstrap import startup_status_projection


def test_missing_boot_is_unknown_not_failed():
    for boot in (None, {}, "garbage", 42, []):
        proj = startup_status_projection(boot)
        assert proj["startup_sequence_status"] == "unknown"
        assert proj["startup_status"] == "unknown"
        assert proj["status"] == "unknown"
        assert proj["canonical_status"] == "unknown"
        assert proj["stage_statuses"] == {}
        assert proj["startup_stage_statuses"] == {}


def test_failed_boot_stays_failed():
    boot = {
        "startup_sequence_status": "failed",
        "status": "failed",
        "overall_status": "failed",
        "aggregate_status": "failed",
        "stage_statuses": {"task_recovery": "failed"},
    }
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "failed"
    assert proj["startup_status"] == "failed"
    assert proj["stage_statuses"] == {"task_recovery": "failed"}
    assert proj["startup_stage_statuses"] == {"task_recovery": "failed"}


def test_inconsistent_alias_precedence_startup_sequence_wins():
    boot = {
        "startup_sequence_status": "failed",
        "status": "completed",
        "overall_status": "completed",
        "aggregate_status": "completed",
    }
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "failed"
    assert proj["startup_status"] == "failed"


def test_inconsistent_alias_precedence_status_over_overall():
    boot = {"status": "completed", "overall_status": "failed", "aggregate_status": "failed"}
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "completed"


def test_inconsistent_alias_precedence_overall_over_aggregate():
    boot = {"overall_status": "completed", "aggregate_status": "failed"}
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "completed"


def test_inconsistent_alias_precedence_aggregate_last_resort():
    boot = {"aggregate_status": "failed"}
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "failed"


def test_compatibility_fields_preserved_when_present():
    boot = {
        "startup_sequence_status": "completed",
        "status": "completed",
        "overall_status": "degraded",
        "aggregate_status": "error",
        "stage_statuses": {"a": "completed"},
    }
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "completed"
    assert proj["startup_status"] == "completed"
    assert proj["overall_status"] == "degraded"
    assert proj["startup_overall_status"] == "degraded"
    assert proj["aggregate_status"] == "error"
    assert proj["startup_aggregate_status"] == "error"
    assert proj["stage_statuses"] == {"a": "completed"}
    assert proj["startup_stage_statuses"] == {"a": "completed"}


def test_compatibility_fields_fall_back_to_canonical_when_absent():
    boot = {"startup_sequence_status": "completed"}
    proj = startup_status_projection(boot)
    assert proj["overall_status"] == "completed"
    assert proj["startup_overall_status"] == "completed"
    assert proj["aggregate_status"] == "completed"
    assert proj["startup_aggregate_status"] == "completed"


def test_empty_string_alias_is_skipped():
    boot = {"startup_sequence_status": "", "status": "failed"}
    proj = startup_status_projection(boot)
    assert proj["startup_sequence_status"] == "failed"


def test_does_not_probe_database():
    # Projection must remain pure and accept only boot payloads.
    import inspect

    sig = inspect.signature(startup_status_projection)
    assert list(sig.parameters) == ["boot"]