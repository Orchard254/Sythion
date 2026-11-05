"""Offline truthful startup bootstrap stage status regression coverage."""

import os

import pytest

from backend.core.db_manager import init_database


def _db(tmp_path):
    db_path = str(tmp_path / "bootstrap-stage-status.db")
    return init_database(db_path)


def test_run_startup_sequence_reports_all_stages_completed(tmp_path):
    db_manager = _db(tmp_path)
    try:
        from backend.services.bootstrap import run_startup_sequence

        boot = run_startup_sequence(db_manager)

        assert boot["order"] == [
            "task_recovery",
            "goal_migration",
            "agent_init",
            "wake_recovery",
        ]
        assert set(boot["stage_statuses"]) == {
            "task_recovery",
            "goal_migration",
            "agent_init",
            "wake_recovery",
        }
        assert all(
            status == "completed"
            for status in boot["stage_statuses"].values()
        )
        assert boot["startup_sequence_status"] == "completed"
        assert boot["status"] == "completed"
        assert boot["overall_status"] == "completed"
        assert boot["aggregate_status"] == "completed"
        assert len(boot["stages"]) == 4
        assert all(stage["status"] == "completed" for stage in boot["stages"])
    finally:
        db_manager.engine.dispose()


def test_run_startup_sequence_marks_failed_stage_truthfully(monkeypatch, tmp_path):
    def _raise_task_orchestrator(*args, **kwargs):
        raise RuntimeError("injected task recovery failure")

    monkeypatch.setattr(
        "backend.services.task_orchestrator.TaskOrchestrator",
        _raise_task_orchestrator,
    )

    db_manager = _db(tmp_path)
    try:
        from backend.services.bootstrap import run_startup_sequence

        boot = run_startup_sequence(db_manager)

        assert boot["stage_statuses"]["task_recovery"] == "failed"
        stage = next(s for s in boot["stages"] if s["stage"] == "task_recovery")
        assert stage["status"] == "failed"
        assert "injected task recovery failure" in stage["error"]
        # Other stages can still run and succeed, but the aggregate is no longer
        # reported as completed just because later stages finished.
        assert boot["startup_sequence_status"] == "failed"
        assert boot["status"] == "failed"
        assert boot["recovery"] == []
    finally:
        db_manager.engine.dispose()
