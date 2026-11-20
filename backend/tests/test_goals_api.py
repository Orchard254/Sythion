"""
Phase 3 API contract tests for /goals + legacy /objectives compatibility.

Proves at the HTTP boundary:
- goal lifecycle endpoints behave per contract;
- API callers can NEVER forge completion or bypass GoalManager;
- paused/abandoned goals start no work via advance;
- malformed replans are rejected without persistence;
- legacy /objectives status writes route through the canonical machine.
"""

import json

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
from backend.models.database import Base, GoalPlan, PlanStep, TaskEvent, get_db


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


@pytest.fixture
def client(db_session):
    from backend.api.routes import router

    test_app = FastAPI()
    test_app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: "goal-api-token",
        allowed_origins=control_origins(False),
    )
    test_app.include_router(router, prefix="/api/v1")

    def override_get_db():
        yield db_session

    test_app.dependency_overrides[get_db] = override_get_db
    return TestClient(test_app, headers={"X-Control-Token": "goal-api-token"})


def _create_goal(client, steps=True):
    payload = {"title": "API goal", "purpose": "contract testing", "priority": 2}
    if steps:
        payload["steps"] = [
            {"key": "first", "description": "do first"},
            {"key": "second", "description": "do second", "depends_on": ["first"]},
        ]
    return client.post("/api/v1/goals", json=payload)


class TestGoalsApiContract:
    def test_create_get_list(self, client):
        created = _create_goal(client)
        assert created.status_code == 200
        body = created.json()
        assert body["goal"]["status"] == "active"
        assert body["active_plan"]["version"] == 1
        assert len(body["active_plan"]["steps"]) == 2

        got = client.get(f"/api/v1/goals/{body['goal']['id']}")
        assert got.status_code == 200
        assert got.json()["progress"]["total_required_steps"] == 2

        listed = client.get("/api/v1/goals")
        assert listed.status_code == 200
        assert any(g["id"] == body["goal"]["id"] for g in listed.json()["goals"])

    def test_plan_history_endpoint(self, client, db_session):
        from backend.services.planner import CandidateStepModel, Planner

        goal_id = _create_goal(client).json()["goal"]["id"]
        Planner(db_session).replan(goal_id, reason="phase3 audit", candidates=[
            CandidateStepModel(key="solo", description="only step")
        ])
        history = client.get(f"/api/v1/goals/{goal_id}/plans")
        assert history.status_code == 200
        versions = [p["version"] for p in history.json()["plans"]]
        statuses = [p["status"] for p in history.json()["plans"]]
        assert versions == [2, 1]
        assert statuses == ["active", "superseded"]

    def test_lifecycle_pause_resume_abandon(self, client):
        goal_id = _create_goal(client, steps=False).json()["goal"]["id"]
        assert client.post(f"/api/v1/goals/{goal_id}/pause").json()["goal"]["status"] == "paused"
        assert client.post(f"/api/v1/goals/{goal_id}/resume").json()["goal"]["status"] == "active"
        assert client.post(f"/api/v1/goals/{goal_id}/abandon").json()["goal"]["status"] == "abandoned"
        r = client.post(f"/api/v1/goals/{goal_id}/resume")
        assert r.status_code == 409  # terminal: resume refused

    def test_invalid_transition_via_patch_rejected(self, client):
        goal_id = _create_goal(client, steps=False).json()["goal"]["id"]
        client.post(f"/api/v1/goals/{goal_id}/abandon")
        # Legacy PATCH path must NOT resurrect an abandoned goal.
        r = client.patch(f"/api/v1/objectives/{goal_id}", json={"status": "active"})
        assert r.status_code == 409

    def test_api_caller_cannot_forge_completed(self, client):
        goal_id = _create_goal(client).json()["goal"]["id"]
        r = client.patch(f"/api/v1/objectives/{goal_id}", json={"status": "completed"})
        assert r.status_code in (400, 409)

    @staticmethod
    def _goal_with_criteria(client):
        created = client.post("/api/v1/goals", json={
            "title": "Criteria goal",
            "purpose": "verify-criteria contract",
            "completion_criteria": "All work verified.",
            "steps": [{"key": "first", "description": "do first"}],
        })
        assert created.status_code == 200
        return created.json()["goal"]["id"]

    def test_verify_criteria_cannot_forge_completion_with_ready_step(self, client, db_session):
        """A fresh plan's first step is 'ready' — verifying criteria must not
        complete the goal. The canonical open-state set includes ready/running.
        """
        goal_id = self._goal_with_criteria(client)
        step = db_session.query(PlanStep).filter_by(objective_id=goal_id).one()
        assert step.status == "ready"  # sanity: nothing has run

        result = client.post(f"/api/v1/objectives/{goal_id}/verify-criteria")
        assert result.status_code == 200
        body = result.json()
        assert body["status"] == "active"
        assert body["criteria_verified"] is True
        assert db_session.query(TaskEvent).filter_by(
            task_id=f"goal:{goal_id}", event_type="goal.completed").count() == 0

    def test_verify_criteria_completes_only_when_steps_done(self, client, db_session):
        """Happy path: all steps completed -> completion via the state machine,
        including the goal.completed ledger event.
        """
        goal_id = self._goal_with_criteria(client)
        step = db_session.query(PlanStep).filter_by(objective_id=goal_id).one()
        step.status = "completed"
        db_session.commit()

        result = client.post(f"/api/v1/objectives/{goal_id}/verify-criteria")
        assert result.status_code == 200
        assert result.json()["status"] == "completed"
        assert db_session.query(TaskEvent).filter_by(
            task_id=f"goal:{goal_id}", event_type="goal.completed").count() == 1

    def test_advance_on_paused_goal_starts_nothing(self, client):
        goal_id = _create_goal(client).json()["goal"]["id"]
        client.post(f"/api/v1/goals/{goal_id}/pause")
        r = client.post(f"/api/v1/goals/{goal_id}/advance")
        assert r.status_code == 200
        assert r.json()["advanced"] is False

    def test_malformed_replan_rejected_without_persistence(self, client, db_session):
        goal_id = _create_goal(client).json()["goal"]["id"]
        before = db_session.query(GoalPlan).count()
        r = client.post(f"/api/v1/goals/{goal_id}/replan", json={
            "steps": [
                {"key": "x", "description": "x step", "depends_on": ["y"]},
                {"key": "y", "description": "y step", "depends_on": ["x"]},
            ],
            "reason": "cycle attempt",
        })
        assert r.status_code == 422
        assert "cycle" in json.dumps(r.json()).lower()
        assert db_session.query(GoalPlan).count() == before  # zero partial state

    def test_runnable_listing_is_read_only(self, client):
        goal_id = _create_goal(client).json()["goal"]["id"]
        r = client.get("/api/v1/goals/runnable")
        assert r.status_code == 200
        assert any(g["goal_id"] == goal_id for g in r.json()["runnable"])


    def test_next_work_preview_is_read_only_but_detects_virtual_unlock(self, client, db_session):
        from sqlalchemy import event

        goal_id = _create_goal(client).json()["goal"]["id"]
        step_a, step_b = (db_session.query(PlanStep)
                          .filter_by(objective_id=goal_id)
                          .order_by(PlanStep.step_order.asc()).all())
        step_a.status = "completed"
        db_session.commit()
        assert step_b.status == "blocked"
        old_events = db_session.query(TaskEvent).count()
        committed = []
        event.listen(db_session, "after_commit", lambda _: committed.append(True))

        preview = client.get(f"/api/v1/goals/{goal_id}/next-work")
        assert preview.status_code == 200
        data = preview.json()
        assert data["state"] == "ready"
        assert data["next_step"]["key"] == "second"
        assert data["next_step"]["would_unlock"] is True
        assert data["read_only"] is True
        assert data["authorization_issued"] is False

        listed = client.get("/api/v1/goals/runnable")
        assert listed.status_code == 200
        assert any(g["goal_id"] == goal_id and g["next_step"]["key"] == "second"
                   for g in listed.json()["runnable"])
        assert db_session.get(PlanStep, step_b.id).status == "blocked"
        assert db_session.query(TaskEvent).count() == old_events
        assert not committed

    def test_next_work_preview_unknown_goal_is_404(self, client):
        result = client.get("/api/v1/goals/does-not-exist/next-work")
        assert result.status_code == 404

    def test_next_work_preview_respects_paused_goal_state(self, client):
        goal_id = _create_goal(client).json()["goal"]["id"]
        client.post(f"/api/v1/goals/{goal_id}/pause")
        result = client.get(f"/api/v1/goals/{goal_id}/next-work")
        assert result.status_code == 200
        assert result.json()["state"] == "goal_paused"
        assert result.json()["runnable"] is False
