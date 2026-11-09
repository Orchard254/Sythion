"""Goal readiness integration: the GET/selector must never commit a plan event.

These tests run against Sythion's real SQLAlchemy models and require the full
backend checkout. Pure projection tests are independently runnable.
"""

import json

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Objective, GoalPlan, PlanStep, Task, TaskEvent
from backend.services.goal_selector import RunnableGoalSelector
from backend.services.planner import Planner, CandidateStepModel
from backend.services.goal_manager import GoalManager
from backend.services.goal_work_preview import read_goal_work_preview


@pytest.fixture
def db():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def goal_and_plan(db):
    goal = GoalManager(db).create_goal("Goal with two steps")
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="first", description="Do first"),
        CandidateStepModel(key="second", description="Do second", depends_on=["first"]),
    ])
    return goal, plan


def test_virtual_ready_in_selector_and_get_without_any_commits(db):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    first.status = "completed"
    db.commit()
    assert second.status == "blocked"
    before_events = db.query(TaskEvent).count()
    commits = []
    event.listen(db, "after_commit", lambda session: commits.append(True))
    preview = read_goal_work_preview(db, goal.id)
    runnable = RunnableGoalSelector(db).list_runnable_goals()
    assert preview["state"] == "ready"
    assert preview["next_step"]["key"] == "second"
    assert preview["next_step"]["would_unlock"] is True
    assert next(row for row in runnable if row["goal_id"] == goal.id)["next_step"]["key"] == "second"
    assert db.get(PlanStep, second.id).status == "blocked"
    assert db.query(TaskEvent).count() == before_events
    assert not commits and not db.dirty


def test_real_step_start_is_the_only_path_that_unlocks_ready_state(db):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    first.status = "completed"
    db.commit()
    assert read_goal_work_preview(db, goal.id)["next_step"]["would_unlock"]
    chosen = Planner(db).select_next_step(goal.id)
    assert chosen.id == second.id
    assert db.get(PlanStep, second.id).status == "ready"
    assert not read_goal_work_preview(db, goal.id)["next_step"]["would_unlock"]


def test_stale_ready_state_with_unsatisfied_dependency_cannot_be_started(db):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    first.status = "failed"
    second.status = "ready"
    db.commit()
    assert not read_goal_work_preview(db, goal.id)["runnable"]
    assert all(row["goal_id"] != goal.id for row in RunnableGoalSelector(db).list_runnable_goals())
    assert Planner(db).select_next_step(goal.id) is None


def test_malformed_plan_blocks_all_automatic_selection(db):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    second.depends_on = "not valid JSON"
    db.commit()
    assert read_goal_work_preview(db, goal.id)["state"] == "invalid_dependency_data"
    assert all(row["goal_id"] != goal.id for row in RunnableGoalSelector(db).list_runnable_goals())
    assert Planner(db).select_next_step(goal.id) is None


def test_explain_completed_goal_does_not_claim_it_can_run(db):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    first.status = "completed"
    second.status = "completed"
    db.commit()
    info = RunnableGoalSelector(db).explain_ineligibility(goal.id)
    assert info["runnable"] is False
    assert info["state"] == "steps_finished_review_goal"
    assert info["next_step"] is None


def test_scoped_preview_excludes_other_goals_plan_data(db):
    a, plan_a = goal_and_plan(db)
    b = GoalManager(db).create_goal("Private goal")
    Planner(db).create_plan(b.id, [
        CandidateStepModel(key="private", description="SECRET unrelated task"),
    ])
    p = read_goal_work_preview(db, a.id)
    assert p["goal_id"] == a.id
    assert "SECRET unrelated task" not in repr(p)


@pytest.mark.parametrize("uncommitted", [
    "goal_dirty", "step_dirty", "new_task", "deleted_step",
])
def test_preview_refuses_pending_uncommitted_orm_mutations(db, uncommitted):
    goal, plan = goal_and_plan(db)
    # A commit expires SQLAlchemy objects. Fetching goal.id after writing an
    # expired goal.title can issue an implicit SELECT, which autoflushes the
    # dirty object BEFORE preview starts. Cache identifiers before mutation
    # so this test actually exercises the preview's clean-session guard.
    goal_id = goal.id
    first = Planner(db).plan_steps(plan.id)[0]
    flushes = []
    event.listen(db, "after_flush", lambda session, context: flushes.append(True))

    if uncommitted == "goal_dirty":
        goal.title = "Not yet committed"
        assert goal in db.dirty
    elif uncommitted == "step_dirty":
        first.status = "completed"
        assert first in db.dirty
    elif uncommitted == "new_task":
        db.add(Task(
            id="pending-preview-task", original_request="uncommitted work",
            state="created", version=1, objective_id=goal_id,
        ))
        assert db.new
    else:
        db.delete(first)
        assert db.deleted

    with pytest.raises(RuntimeError, match="clean ORM"):
        read_goal_work_preview(db, goal_id)
    assert not flushes, "read-only preview must not flush uncommitted changes"
    # The preview neither commits nor discards caller-owned mutations.
    assert db.new or db.dirty or db.deleted
    db.rollback()


@pytest.mark.parametrize("state", ["interrupted", "cancelled"])
def test_interrupted_task_preview_requires_review_without_exposing_task_data(db, state):
    goal, plan = goal_and_plan(db)
    first, second = Planner(db).plan_steps(plan.id)
    task = Task(
        id="task-private", original_request="SECRET sensitive action details",
        state=state, version=1, objective_id=goal.id,
    )
    db.add(task)
    first.status = "running"
    first.linked_task_id = task.id
    first.evidence_task_id = task.id
    db.commit()

    preview = read_goal_work_preview(db, goal.id)
    assert preview["state"] == "recovery_review_required"
    assert not preview["runnable"]
    assert preview["next_step"] is None
    assert "Review" in preview["explanation"]
    assert "SECRET" not in repr(preview)
    assert "task-private" not in repr(preview)
    assert RunnableGoalSelector(db).list_runnable_goals() == []
    assert first.status == "running"
    assert second.status == "blocked"


def test_missing_task_preview_requires_review_without_autorequeue(db):
    goal, plan = goal_and_plan(db)
    first, _ = Planner(db).plan_steps(plan.id)
    first.status = "running"
    first.linked_task_id = "lost-task"
    first.evidence_task_id = "lost-task"
    db.commit()
    preview = read_goal_work_preview(db, goal.id)
    assert preview["state"] == "recovery_review_required"
    assert preview["runnable"] is False
    assert "lost-task" not in repr(preview)
    assert Planner(db).select_next_step(goal.id) is None


def test_running_task_preview_blocks_independent_ready_step(db):
    goal = GoalManager(db).create_goal("Independent work")
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="running", description="In-flight work"),
        CandidateStepModel(key="independent", description="Other ready work"),
    ])
    first, second = Planner(db).plan_steps(plan.id)
    task = Task(
        id="active-task", original_request="active work",
        state="running", version=1, objective_id=goal.id,
    )
    db.add(task)
    first.status = "running"
    first.linked_task_id = task.id
    first.evidence_task_id = task.id
    db.commit()
    preview = read_goal_work_preview(db, goal.id)
    assert preview["state"] == "step_in_progress"
    assert preview["runnable"] is False
    assert preview["next_step"] is None
    assert second.status == "ready"
    assert RunnableGoalSelector(db).list_runnable_goals() == []
