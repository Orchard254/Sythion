"""
Phase 3.5 legacy objective sunset + single-authority tests.

Covers:
- idempotent migration of unplanned legacy objectives to plan-backed Goals;
- conservative state mapping (no fabricated completions);
- canonical creation paths (API adapter + model tool);
- authority proofs (single writer; legacy paths cannot corrupt plan state).
"""

import asyncio
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, GoalPlan, PlanStep, TaskEvent
from backend.services.goal_manager import GoalManager, migrate_legacy_objectives
from backend.services.planner import Planner


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


def _mk_legacy_objective(db, title="Legacy obj", steps=None):
    """Create an objective the OLD way (raw rows, plan_id NULL)."""
    from backend.models.database import Objective
    from datetime import datetime

    now = datetime.utcnow()
    obj = Objective(id="legacy-" + title.lower().replace(" ", "-")[:12],
                    title=title, status="active", priority=3,
                    created_at=now, updated_at=now)
    db.add(obj)
    db.flush()
    for idx, (desc, status) in enumerate(steps or [], start=1):
        db.add(PlanStep(objective_id=obj.id, step_order=idx,
                        description=desc, status=status,
                        created_at=now, updated_at=now))
    db.commit()
    return obj


class TestLegacyMigration:
    def test_migrates_unplanned_objective_to_plan_backed(self, db_session):
        obj = _mk_legacy_objective(db_session, steps=[
            ("do first", "in_progress"), ("do second", "pending"),
        ])
        report = migrate_legacy_objectives(db_session)
        assert len(report["migrated"]) == 1
        assert report["migrated"][0]["objective_id"] == obj.id

        plan = db_session.query(GoalPlan).filter_by(goal_id=obj.id).one()
        steps = (
            db_session.query(PlanStep)
            .filter_by(plan_id=plan.id)
            .order_by(PlanStep.step_order)
            .all()
        )
        assert [s.status for s in steps] == ["ready", "blocked"]  # linear chain

    def test_idempotent_second_run_creates_nothing(self, db_session):
        _mk_legacy_objective(db_session, steps=[("only", "pending")])
        first = migrate_legacy_objectives(db_session)
        plans_after_first = db_session.query(GoalPlan).count()

        second = migrate_legacy_objectives(db_session)

        assert len(first["migrated"]) == 1
        assert second["migrated"] == []
        assert db_session.query(GoalPlan).count() == plans_after_first
        # No duplicate events either.
        from sqlalchemy import text as _text

        goal_scopes = [
            row[0] for row in db_session.execute(
                _text("SELECT DISTINCT task_id FROM task_events")
            ).fetchall()
        ]
        for scope in goal_scopes:
            types = [e["event_type"]
                     for e in Planner(db_session).ledger.event_history(scope)]
            assert types.count("goal.migrated") <= 1

    def test_completed_without_evidence_becomes_recoverable_not_completed(self, db_session):
        """A legacy 'completed' claim with NO evidence must never be trusted.

        The first plan step has no dependencies, so the plan default is
        READY (eligible for execution) — the assertion that matters is that
        the unverified claim does NOT become COMPLETED.
        """
        obj = _mk_legacy_objective(db_session, steps=[("claimed done", "completed")])
        migrate_legacy_objectives(db_session)

        step = (
            db_session.query(PlanStep)
            .filter_by(objective_id=obj.id)
            .filter(PlanStep.plan_id.isnot(None))
            .first()
        )
        assert step.status != "completed"
        assert step.evidence_task_id is None

    def test_completed_with_evidence_is_carried(self, db_session):
        from datetime import datetime as dt

        obj = _mk_legacy_objective(db_session, steps=[("really done", "completed")])
        legacy_step = (
            db_session.query(PlanStep)
            .filter_by(objective_id=obj.id)
            .filter(PlanStep.plan_id.is_(None))
            .first()
        )
        legacy_step.linked_task_id = "task-evidence"
        legacy_step.result_summary = "verified outcome"
        db_session.commit()

        migrate_legacy_objectives(db_session)
        step = (
            db_session.query(PlanStep)
            .filter_by(objective_id=obj.id)
            .filter(PlanStep.plan_id.isnot(None))
            .first()
        )
        assert step.status == "completed"
        assert step.evidence_task_id == "task-evidence"

    def test_failed_carries_reason(self, db_session):
        obj = _mk_legacy_objective(db_session, steps=[("broke", "failed")])
        legacy = (
            db_session.query(PlanStep)
            .filter_by(objective_id=obj.id)
            .first()
        )
        legacy.blocker_reason = "missing dependency tool"
        db_session.commit()

        migrate_legacy_objectives(db_session)
        step = (
            db_session.query(PlanStep)
            .filter_by(objective_id=obj.id)
            .filter(PlanStep.plan_id.isnot(None))
            .first()
        )
        assert step.status == "failed"
        assert "missing dependency tool" in step.failure_reason

    def test_empty_objective_skipped_with_reason(self, db_session):
        obj = _mk_legacy_objective(db_session, steps=[])
        report = migrate_legacy_objectives(db_session)
        assert report["migrated"] == []
        assert report["skipped"][0]["reason"] == "no_legacy_steps"

    def test_migration_event_audited(self, db_session):
        obj = _mk_legacy_objective(db_session, steps=[("a step", "pending")])
        migrate_legacy_objectives(db_session)
        from backend.runtime.events import DbEventLedger

        types = [e["event_type"]
                 for e in DbEventLedger(db_session).event_history(f"goal:{obj.id}")]
        assert "goal.migrated" in types

    def test_short_description_hi_migrates_conservatively(self, db_session):
        """Phase 12.8: 'hi' (<3 chars) must not crash migration and must produce a valid plan."""
        obj = _mk_legacy_objective(db_session, steps=[("hi", "completed")])
        report = migrate_legacy_objectives(db_session)
        assert len(report["migrated"]) == 1

        plan = db_session.query(GoalPlan).filter_by(goal_id=obj.id).one()
        steps = db_session.query(PlanStep).filter_by(plan_id=plan.id).all()
        assert len(steps) == 1
        assert steps[0].description == "Legacy step 1"
        assert len(steps[0].description) >= 3

    def test_multiple_invalid_descriptions_migrate_conservatively(self, db_session):
        """Multiple invalid descriptions (single char, empty, whitespace, 2-char) mixed with valid."""
        obj = _mk_legacy_objective(db_session, steps=[
            ("hi", "completed"),
            (".", "failed"),
            ("a", "pending"),
            ("ab", "pending"),
            ("", "pending"),
            ("   ", "pending"),
            ("valid long step description", "completed"),
        ])
        report = migrate_legacy_objectives(db_session)
        assert len(report["migrated"]) == 1

        plan = db_session.query(GoalPlan).filter_by(goal_id=obj.id).one()
        steps = (
            db_session.query(PlanStep)
            .filter_by(plan_id=plan.id)
            .order_by(PlanStep.step_order)
            .all()
        )
        assert len(steps) == 7
        assert steps[0].description == "Legacy step 1"
        assert steps[1].description == "Legacy step 2"
        assert steps[2].description == "Legacy step 3"
        assert steps[3].description == "Legacy step 4"
        assert steps[4].description == "Legacy step 5"
        assert steps[5].description == "Legacy step 6"
        # Valid description is preserved unchanged
        assert steps[6].description == "valid long step description"
        # All descriptions satisfy the >= 3 constraint
        for s in steps:
            assert len(s.description) >= 3



class TestCanonicalCreationPaths:
    def test_api_created_objectives_are_plan_backed(self, client_api):
        body = client_api.post("/api/v1/objectives", json={
            "title": "Via adapter",
            "purpose": "single authority",
            "plan_steps": ["step one", "step two"],
        }).json()
        goal_id = body["id"]
        plan_backed = (
            client_api.get(f"/api/v1/goals/{goal_id}").json()["active_plan"]
        )
        assert plan_backed is not None
        assert plan_backed["source"] == "user"

    def test_model_tool_creates_canonical_goal(self, tmp_path):
        from backend.tools.objective_planning_tool import (
            ObjectiveCreateTool, ObjectiveCreateInput,
        )
        from backend.core.db_manager import DatabaseManager
        from backend.models.database import GoalPlan as _GP, set_db_manager

        db_mgr = DatabaseManager(str(tmp_path / "tool_canonical.db"))
        set_db_manager(db_mgr)
        try:
            out = asyncio.run(ObjectiveCreateTool().execute(ObjectiveCreateInput(
                title="Tool goal",
                steps=["alpha action", "beta action"],
            )))
            assert out.success is True, out.error
            with db_mgr.get_session() as session:
                plan = session.query(_GP).filter_by(
                    goal_id=out.data["objective_id"]).one()
                assert plan.source == "model"
        finally:
            set_db_manager(None)

    def test_auto_task_objective_creation_goes_through_goal_manager(self, db_session):
        """ensure_task_objective now creates via GoalManager → the ledger has
        a goal.created event even on the legacy auto-projection path."""
        from backend.models.database import Task
        from backend.services.work_state import ensure_task_objective

        task = Task(id="t-auto", original_request="Build the widget and ship it",
                    state="created", version=1)
        db_session.add(task)
        db_session.commit()

        objective = ensure_task_objective(db_session, task)
        from backend.runtime.events import DbEventLedger

        types = [e["event_type"]
                 for e in DbEventLedger(db_session).event_history(f"goal:{objective.id}")]
        assert "goal.created" in types


class TestSingleAuthority:
    def test_legacy_projection_cannot_touch_plan_backed_goal(self, db_session):
        """The work_state projection is a no-op for plan-backed goals."""
        from backend.services.work_state import sync_objective_state_for_task
        from backend.api.schemas import TaskState
        from backend.models.database import Task

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Protected")
        planner.create_plan(goal.id, [
            __import__("backend.services.planner", fromlist=["CandidateStepModel"])
            .CandidateStepModel(key="a", description="step a"),
        ])
        task = Task(id="proj-task", original_request="x", state="failed",
                    version=1, objective_id=goal.id, terminal_reason="boom")
        db_session.add(task)
        db_session.commit()

        before_status = manager.get_goal(goal.id).status
        sync_objective_state_for_task(
            db_session, task, event_type="task.failed", event_data={})
        after = manager.get_goal(goal.id)

        assert after.status == before_status == "active"  # NOT paused by projection

    def test_terminal_goal_untouched_by_projection(self, db_session):
        from backend.services.work_state import sync_objective_state_for_task
        from backend.models.database import Task

        manager = GoalManager(db_session)
        goal = manager.create_goal("Terminal")
        manager.abandon_goal(goal.id)
        task = Task(id="t-term", original_request="x", state="failed",
                    version=1, objective_id=goal.id)
        db_session.add(task)
        db_session.commit()
        sync_objective_state_for_task(db_session, task,
                                      event_type="task.failed", event_data={})
        assert manager.get_goal(goal.id).status == "abandoned"


def objective_noop(x):
    return x


@pytest.fixture
def client_api(db_session):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from backend.api.routes import router
    from backend.core.auth import LocalControlBoundaryMiddleware
    from backend.models.database import get_db

    app = FastAPI()
    app.add_middleware(LocalControlBoundaryMiddleware,
                       token_provider=lambda: "tok")
    app.include_router(router, prefix="/api/v1")

    def override():
        yield db_session

    app.dependency_overrides[get_db] = override
    return TestClient(app, headers={"X-Control-Token": "tok"})