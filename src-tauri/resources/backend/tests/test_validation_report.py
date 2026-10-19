"""
Tests for the validation dump report (data only, no implementation details).

Contract: the report returns per-store counts, memory-context injection stats
aggregated from the last N model-engine rollout-log runs, and the distinct
relation vocabulary in use -- all read-only.
"""

import pytest
from fastapi.testclient import TestClient

from backend.core.db_manager import DatabaseManager
from backend.models.database import (
    ConversationTurn,
    MemoryContextLog,
    MemoryEdge,
    MemoryEntry,
    ProposedSkill,
    Skill,
    Task,
    TaskEvent,
    get_db,
    set_db_manager,
)
from backend.services.validation_report import build_validation_report


@pytest.fixture
def report_db(tmp_path):
    db_path = str(tmp_path / "test_report.db")
    db_mgr = DatabaseManager(db_path)
    set_db_manager(db_mgr)
    with db_mgr.get_session() as session:
        yield session


def _seed_all_stores(db):
    db.add(MemoryEntry(key="user_name", value="Alice"))
    db.add(MemoryEdge(source_key="user_name", relation="prefers", target_key="preferred_editor"))
    db.add(ConversationTurn(session_id="s1", task_id="t1", role="user", summary="hi"))
    db.add(Task(id="t1", original_request="do x", state="completed", version=1))
    db.add(TaskEvent(task_id="t1", event_type="tool.executed", event_data="{}", sequence_num=1))
    db.add(Skill(id="s1", name="read workflow"))
    db.add(ProposedSkill(
        skill_id="auto-1", definition="{}", trace_ids="[]",
        status="proposed", validation_errors="[]",
    ))
    db.add(ProposedSkill(
        skill_id="auto-2", definition="{}", trace_ids="[]",
        status="enabled", validation_errors="[]",
    ))
    db.commit()


def _log_run(db, task_id, available, relevant, connected, links):
    db.add(MemoryContextLog(
        task_id=task_id,
        available=available,
        relevant=relevant,
        connected=connected,
        links=links,
    ))
    db.commit()


class TestCounts:
    def test_counts_cover_every_store(self, report_db):
        _seed_all_stores(report_db)

        counts = build_validation_report(report_db)["counts"]

        assert counts == {
            "memory_entries": 1,
            "memory_edges": 1,
            "conversation_turns": 1,
            "tasks": 1,
            "task_events": 1,
            "skills": 1,
            "proposed_skills": 2,
            "proposed_skills_pending": 1,
        }

    def test_empty_report_counts_are_zero(self, report_db):
        counts = build_validation_report(report_db)["counts"]

        assert all(v == 0 for v in counts.values())


class TestInjectionStats:
    def test_aggregates_last_n_runs_newest_first(self, report_db):
        _log_run(report_db, "task-a", available=10, relevant=2, connected=1, links=3)
        _log_run(report_db, "task-b", available=8, relevant=4, connected=2, links=5)

        injection = build_validation_report(report_db, last_n=50)["memory_context_injection"]

        assert injection["runs"] == 2
        assert injection["totals"] == {"available": 18, "relevant": 6, "connected": 3, "links": 8}
        assert [r["task_id"] for r in injection["per_run"]] == ["task-b", "task-a"]
        assert injection["per_run"][0]["links"] == 5

    def test_last_n_limits_runs(self, report_db):
        for i in range(5):
            _log_run(report_db, f"task-{i}", available=1, relevant=1, connected=1, links=1)

        injection = build_validation_report(report_db, last_n=2)["memory_context_injection"]

        assert injection["runs"] == 2
        assert [r["task_id"] for r in injection["per_run"]] == ["task-4", "task-3"]
        assert injection["totals"]["available"] == 2

    def test_no_runs_yields_empty_totals(self, report_db):
        injection = build_validation_report(report_db)["memory_context_injection"]

        assert injection["runs"] == 0
        assert injection["totals"] == {"available": 0, "relevant": 0, "connected": 0, "links": 0}
        assert injection["per_run"] == []


class TestRelationVocabulary:
    def test_distinct_relations_sorted(self, report_db):
        db = report_db
        db.add(MemoryEdge(source_key="a", relation="prefers", target_key="b"))
        db.add(MemoryEdge(source_key="c", relation="works_at", target_key="d"))
        db.add(MemoryEdge(source_key="e", relation="prefers", target_key="f"))
        db.commit()

        vocab = build_validation_report(db)["relation_vocabulary"]

        assert vocab["distinct"] == 2
        assert vocab["relations"] == ["prefers", "works_at"]

    def test_no_edges_yields_empty_vocabulary(self, report_db):
        vocab = build_validation_report(report_db)["relation_vocabulary"]

        assert vocab == {"distinct": 0, "relations": []}


class TestReportShape:
    def test_report_is_data_only_snapshot(self, report_db):
        _seed_all_stores(report_db)
        _log_run(report_db, "task-a", available=5, relevant=1, connected=0, links=1)

        report = build_validation_report(report_db)

        assert set(report.keys()) == {
            "generated_at", "last_n_runs", "counts",
            "memory_context_injection", "relation_vocabulary",
        }
        assert "generated_at" in report
        # Read-only: stores are untouched by building a report.
        assert report_db.query(MemoryEntry).count() == 1
        assert report_db.query(MemoryContextLog).count() == 1

    def test_report_is_read_only_on_stores(self, report_db):
        _seed_all_stores(report_db)
        before = {
            table.__tablename__: report_db.query(table).count()
            for table in (MemoryEntry, MemoryEdge, ConversationTurn, Task, TaskEvent, Skill, ProposedSkill)
        }

        build_validation_report(report_db)

        after = {
            table.__tablename__: report_db.query(table).count()
            for table in (MemoryEntry, MemoryEdge, ConversationTurn, Task, TaskEvent, Skill, ProposedSkill)
        }
        assert before == after


@pytest.fixture
def api_client(report_db):
    from backend.main import app
    import backend.main as backend_main

    def override_get_db():
        yield report_db

    app.dependency_overrides[get_db] = override_get_db
    previous_token = backend_main.control_token
    backend_main.control_token = "validation-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "validation-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


class TestReportApi:
    def test_report_endpoint_returns_snapshot(self, report_db, api_client):
        _seed_all_stores(report_db)

        resp = api_client.get("/api/v1/memory/report")

        assert resp.status_code == 200
        body = resp.json()
        assert body["counts"]["memory_entries"] == 1
        assert body["counts"]["proposed_skills_pending"] == 1
        assert body["relation_vocabulary"]["distinct"] == 1
        assert body["memory_context_injection"]["runs"] == 0

    def test_report_endpoint_honors_last_n(self, report_db, api_client):
        for i in range(3):
            _log_run(report_db, f"task-{i}", available=1, relevant=1, connected=1, links=1)

        resp = api_client.get("/api/v1/memory/report?last_n=1")

        assert resp.status_code == 200
        body = resp.json()
        assert body["memory_context_injection"]["runs"] == 1
        assert body["memory_context_injection"]["per_run"][0]["task_id"] == "task-2"
