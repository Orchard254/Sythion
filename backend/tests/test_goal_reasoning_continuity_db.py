"""Full-repository SQLite tests for the read-only goal continuity endpoint.

These require the real Sythion ORM and event decoder and are intentionally not
collected by the local isolated reasoning-unit test harness.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Objective, PlanStep, Task, TaskEvent
from backend.services.goal_reasoning_continuity import read_goal_reasoning_continuity


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False}, poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        session.add_all([
            Objective(id="goal-a", title="Goal A", status="active", priority=2),
            Objective(id="goal-b", title="Goal B", status="active", priority=2),
        ])
        session.commit()
        yield session
    finally:
        session.close()
        engine.dispose()


def _task(db, tid, goal_id, state="running"):
    db.add(Task(id=tid, objective_id=goal_id, original_request="safe read", state=state, version=1))
    db.commit()


def _event(db, tid, event_type, job, sequence):
    db.add(TaskEvent(task_id=tid, event_type=event_type,
                     event_data=json.dumps({"job_id": job, "purpose": "task_decision"}),
                     sequence_num=sequence))
    db.commit()


def test_only_goal_own_events_and_task_metadata_returned(db):
    _task(db, "task-a", "goal-a")
    _task(db, "task-b", "goal-b")
    _event(db, "task-a", "reasoning.started", "a-job", 1)
    _event(db, "task-b", "reasoning.started", "b-job", 1)
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["task_window"]["total"] == 1
    assert [t["task_id"] for t in result["tasks"]] == ["task-a"]
    assert "b-job" not in repr(result)
    assert result["reasoning_receipts"]["unresolved_tasks"] == 1


def test_plan_evidence_link_includes_legacy_task_without_cross_goal_leak(db):
    _task(db, "evidence-only", None)
    _task(db, "belongs-to-b", "goal-b")
    db.add_all([
        PlanStep(objective_id="goal-a", plan_id=None, step_order=1,
                 description="legacy evidence", status="running", evidence_task_id="evidence-only"),
        PlanStep(objective_id="goal-a", plan_id=None, step_order=2,
                 description="bad cross-goal link", status="running", evidence_task_id="belongs-to-b"),
    ])
    db.commit()
    result = read_goal_reasoning_continuity(db, "goal-a")
    assert result["task_window"]["total"] == 1
    assert result["tasks"][0]["task_id"] == "evidence-only"
    assert result["tasks"][0]["association"] == "plan_evidence"


def test_detail_shows_bounded_window_and_true_total(db):
    for index in range(4):
        _task(db, f"t-{index}", "goal-a", "completed")
    result = read_goal_reasoning_continuity(db, "goal-a", limit=2)
    assert result["task_window"] == {"total": 4, "returned": 2, "truncated": True}
    assert result["attention"] == "more_tasks_not_inspected"


def test_missing_goal_refused(db):
    with pytest.raises(LookupError, match="goal not found"):
        read_goal_reasoning_continuity(db, "does-not-exist")


@pytest.mark.parametrize("limit", [0, 101, True, 2.5, -1])
def test_invalid_limit_refused(db, limit):
    with pytest.raises(ValueError, match="limit"):
        read_goal_reasoning_continuity(db, "goal-a", limit=limit)


def test_request_does_not_mutate_rows_or_create_events(db):
    _task(db, "task-a", "goal-a")
    original_tasks = db.query(Task).count()
    original_events = db.query(TaskEvent).count()
    read_goal_reasoning_continuity(db, "goal-a")
    assert db.query(Task).count() == original_tasks
    assert db.query(TaskEvent).count() == original_events
