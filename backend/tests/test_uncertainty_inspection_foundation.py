"""Adversarial foundation tests for fail-closed uncertainty inspection."""

import copy

import pytest

from backend.models.database import Task, TaskEvent
from backend.services.uncertainty_inspection import (
    DeepFrozenSnapshot,
    TaskUncertaintyInspectionService,
    inspect_task_uncertainty,
)


def test_no_data_returned_and_uncertainty_unchanged(test_db):
    test_db.add(Task(id="task-a", original_request="secret", state="running", version=1))
    test_db.commit()
    snapshot = inspect_task_uncertainty(test_db, task_id="task-a")
    assert isinstance(snapshot, DeepFrozenSnapshot)
    assert snapshot["data_returned"] is False
    assert snapshot["inspection_allowed"] is False
    assert snapshot["uncertainty_changed"] is False
    assert snapshot["uncertainty"] == "unknown"
    assert snapshot["uncertain_action_ids"] == ()
    assert snapshot["task"]["present"] is False
    assert snapshot["ownership"]["present"] is False
    assert snapshot["task_events"]["returned"] == 0
    assert snapshot["fail_closed"] is True
    assert snapshot["authorization"] == "none"
    assert snapshot["blocker"] == "profile_task_boundary_unenforceable"


def test_pending_staged_work_is_neither_read_nor_mutated(test_db):
    test_db.add(Task(id="task-a", original_request="request", state="running", version=3))
    test_db.commit()
    pending = Task(id="staged", original_request="pending", state="created", version=1)
    test_db.add(pending)
    assert pending in test_db.new

    snapshot = inspect_task_uncertainty(test_db, task_id="task-a", session_id="s", profile="p")
    assert snapshot["data_returned"] is False
    assert pending in test_db.new

    test_db.rollback()
    assert test_db.query(Task).filter_by(id="staged").first() is None


def test_cross_profile_and_session_do_not_disclose_data(test_db):
    test_db.add(Task(id="task-a", original_request="secret-a", state="running", version=1))
    test_db.add(Task(id="task-b", original_request="secret-b", state="running", version=1))
    test_db.commit()

    first = inspect_task_uncertainty(test_db, task_id="task-a", session_id="session-a", profile="profile-a")
    second = inspect_task_uncertainty(test_db, task_id="task-b", session_id="session-b", profile="profile-b")
    mismatched = inspect_task_uncertainty(test_db, task_id="task-a", session_id="session-b", profile="profile-b")

    for snapshot in (first, second, mismatched):
        assert snapshot["data_returned"] is False
        assert snapshot["task"]["present"] is False
        assert "secret-a" not in str(snapshot.to_dict())
        assert "secret-b" not in str(snapshot.to_dict())


def test_no_autoflush_contract_is_required(test_db, monkeypatch):
    class NoAutoflushSession:
        pass

    with pytest.raises(ValueError, match="no_autoflush"):
        inspect_task_uncertainty(NoAutoflushSession(), task_id="task-a")

    class RaisingAutoflush:
        @property
        def no_autoflush(self):
            raise RuntimeError("contract boom")

    with pytest.raises(RuntimeError, match="contract boom"):
        inspect_task_uncertainty(RaisingAutoflush(), task_id="task-a")


def test_nested_output_is_deeply_immutable(test_db):
    snapshot = inspect_task_uncertainty(test_db, task_id="missing")
    nested = snapshot["privacy"]
    with pytest.raises((TypeError, AttributeError)):
        nested["raw_event_data_exposed"] = True
    with pytest.raises((TypeError, AttributeError)):
        snapshot.privacy = "changed"
    assert snapshot["uncertainty_changed"] is False

    # to_dict is a defensive deep copy.
    exported = snapshot.to_dict()
    exported["privacy"]["raw_event_data_exposed"] = True
    assert snapshot["privacy"]["raw_event_data_exposed"] is False


def test_exception_propagation_from_contract(test_db, monkeypatch):
    monkeypatch.setattr(test_db, "no_autoflush", None)
    with pytest.raises(ValueError, match="no_autoflush"):
        inspect_task_uncertainty(test_db, task_id="any-task")


def test_class_wrapper_and_aliases(test_db):
    service = TaskUncertaintyInspectionService(test_db)
    snapshot = service.inspect(task_id="missing", session_id="session-1", profile="p")
    assert snapshot["data_returned"] is False
    assert snapshot["scope_requested"]["session_id"] is True

    direct = service.inspect_task_uncertainty(task_id="missing")
    assert direct["uncertainty_changed"] is False


def test_no_raw_sensitive_values_or_internal_ids_are_exposed(test_db):
    test_db.add(Task(id="secret-task", original_request="TOP SECRET REQUEST", state="running",
                     version=1, pending_action='{"secret_param": "SHOULD-NOT-LEAK"}'))
    test_db.add(TaskEvent(task_id="secret-task", event_type="action.started",
                          event_data='{"secret": "SUPERSECRET"}', sequence_num=1))
    test_db.commit()
    snapshot = inspect_task_uncertainty(test_db, task_id="secret-task")
    rendered = str(snapshot.to_dict())
    assert "TOP SECRET REQUEST" not in rendered
    assert "SHOULD-NOT-LEAK" not in rendered
    assert "SUPERSECRET" not in rendered
    assert "secret-task" not in rendered


def test_deep_frozen_snapshot_nested_tuple(test_db):
    snapshot = inspect_task_uncertainty(test_db, task_id="missing")
    assert isinstance(snapshot["limitations"], tuple)
    assert all(isinstance(x, str) for x in snapshot["limitations"])


def test_no_autoflush_missing_raises_before_any_data():
    with pytest.raises(ValueError, match="db is required"):
        inspect_task_uncertainty(None, task_id="missing")

import importlib


@pytest.mark.parametrize("module_name", [
    "uncertainty_inspection",
    "uncertainty_inspection_service",
    "uncertainty_inspector",
    "task_uncertainty_inspection",
    "task_uncertainty_inspection_service",
    "task_uncertainty_inspector",
])
def test_all_alias_modules_reexport_public_api(module_name):
    module = importlib.import_module(f"backend.services.{module_name}")
    expected = [
        "DeepFrozenSnapshot",
        "TaskUncertaintyInspectionService",
        "inspect_task_uncertainty",
        "inspect_uncertainty",
        "inspect_task_uncertainty_history",
        "get_uncertainty_inspection",
        "get_uncertainty_snapshot",
        "inspect_uncertain_actions",
        "inspect_task_ownership_uncertainty",
    ]
    for name in expected:
        assert hasattr(module, name), f"{module_name} missing {name}"


def test_no_db_query_proof(test_db, monkeypatch):
    calls = {"count": 0}

    def _query(*args, **kwargs):
        calls["count"] += 1
        raise AssertionError("database query must not be called")

    monkeypatch.setattr(test_db, "query", _query)
    snapshot = inspect_task_uncertainty(
        test_db, task_id="arbitrary", session_id="arbitrary-session", profile="arbitrary-profile"
    )
    assert calls["count"] == 0
    assert snapshot["data_returned"] is False
    assert snapshot["inspection_allowed"] is False
    assert snapshot["uncertainty_changed"] is False
    assert snapshot["task"]["present"] is False
    assert snapshot["ownership"]["present"] is False


def test_arbitrary_profile_task_session_remains_fail_closed(test_db):
    for task_id, session_id, profile in (
        ("task-a", "session-a", "profile-a"),
        ("task-b", "session-b", "profile-b"),
        ("task-a", "session-b", "profile-b"),
    ):
        snapshot = inspect_task_uncertainty(
            test_db, task_id=task_id, session_id=session_id, profile=profile
        )
        assert snapshot["data_returned"] is False
        assert snapshot["inspection_allowed"] is False
        assert snapshot["uncertainty"] == "unknown"
        assert snapshot["blocker"] == "profile_task_boundary_unenforceable"
        assert snapshot["uncertainty_changed"] is False
