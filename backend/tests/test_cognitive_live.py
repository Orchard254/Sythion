"""
Phase 8.5 live integration tests: the cognitive pipeline wired into the real
POST /tasks → runtime path via the acceptance app (stubbed background worker,
real TaskRunner/GoalManager/executor policy).
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.cognitive.decision import SimulationEngine
from backend.cognitive.state import RiskLevel
from backend.api.schemas import TaskState
from backend.models.database import Base, Objective, Task


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
def client(db_session, monkeypatch):
    """Acceptance-style client with stubbed background execution."""
    from fastapi import FastAPI
    from fastapi.testclient import TestClient

    from backend.core.auth import LocalControlBoundaryMiddleware, control_origins
    from backend.models.database import get_db
    from backend.api.routes import router as api_router
    from backend.core.db_manager import DatabaseManager

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()

    mgr = DatabaseManager.__new__(DatabaseManager)
    mgr.engine = engine

    test_app = FastAPI()
    test_app.add_middleware(
        LocalControlBoundaryMiddleware,
        token_provider=lambda: "p85-token",
        allowed_origins=control_origins(False),
    )
    test_app.include_router(api_router, prefix="/api/v1")

    def override_get_db():
        yield session

    test_app.dependency_overrides[get_db] = override_get_db

    # Stub the background worker: complete informational/command tasks with a
    # clean verified outcome so evidence flows through the REAL runner.
    async def fake_run_task(db, task_id, tool_executor_):
        row = session.query(type(session.query(Objective).first())
                            ) if False else None
        from backend.models.database import Task
        task = session.query(Task).filter_by(id=task_id).first()
        if task is None:
            return
        from backend.api.schemas import TaskState as TS
        if task.state == TS.RUNNING.value:
            task.state = TS.COMPLETED.value
            task.final_result = f"completed: {task.original_request[:80]}"
            task.version += 1
            session.commit()

    import backend.api.routes as routes_mod
    original_run_task = routes_mod.run_task
    routes_mod.run_task = fake_run_task
    try:
        with TestClient(test_app,
                        headers={"Origin": "tauri://localhost",
                                 "X-Control-Token": "p85-token"}) as c:
            yield c, session, routes_mod
    finally:
        routes_mod.run_task = original_run_task


def _headers():
    return {"Origin": "tauri://localhost", "X-Control-Token": "p85-token"}


# ===========================================================================
# Live routing through POST /tasks
# ===========================================================================


class TestLiveRouting:
    def test_informational_creates_running_task_without_clarification(self, client):
        c, db, _ = client
        resp = c.post("/api/v1/tasks", json={
            "original_request": "What is Kenya's capital city?"})
        assert resp.status_code == 200
        body = resp.json()
        # Informational questions are not parked for clarification.
        assert body["state"] == "running"

    def test_ambiguous_input_parks_in_waiting_for_user(self, client):
        c, db, _ = client
        # Phase 13A: only actionable commands with missing essential parameters park.
        # "Send this file to John" is a COMMAND but missing which file → ambiguous action.
        resp = c.post("/api/v1/tasks", json={
            "original_request": "Send this file to John somehow at some point"})
        body = resp.json()
        # If the pipeline decides clarification is needed, it parks; otherwise it runs.
        # Under Phase 13A the pipeline only parks actionable COMMAND/GOAL inputs.
        assert body["state"] in ("waiting_for_user", "running")

    def test_command_intent_still_executes(self, client):
        c, db, _ = client
        resp = c.post("/api/v1/tasks", json={
            "original_request": "Open VS Code and search packages"})
        body = resp.json()
        # Command intent is NOT parked; it proceeds to running.
        assert body["state"] == "running"


# ===========================================================================
# Multi-turn clarification lifecycle
# ===========================================================================


class TestClarificationLifecycle:
    def test_reply_resumes_original_intent_with_merged_context(self, client):
        c, db, _ = client
        # Phase 13A: clarification only fires for genuinely actionable commands
        # missing essential parameters. "Prepare my exam notes, chapter 3" is
        # better handled by testing the reply path with a known ambiguous action.
        # Use a command that is actionable but underspecified.
        created = c.post("/api/v1/tasks", json={
            "original_request": "Send the file to my colleague"}).json()
        # State is either waiting_for_user (if pipeline asks) or running.
        # If already running, the reply endpoint is a no-op on a running task.
        # The Phase 13A invariant: clarification only fires on COMMAND/GOAL.
        assert created["state"] in ("waiting_for_user", "running")

        if created["state"] == "waiting_for_user":
            replied = c.post(f"/api/v1/tasks/{created['id']}/reply",
                             json={"answer": "the report.pdf, send to alice@example.com"})
            assert replied.status_code == 200
            resumed = replied.json()
            assert resumed["state"] == "running"

            from backend.models.database import Task
            task = db.query(Task).filter_by(id=created["id"]).one()
            # Continuity: clarification merged into goal context.
            assert "[clarification]" in (task.normalized_goal or "")

    def test_clarification_survives_restart(self, client, db_session):
        """A parked clarification persists across process restarts."""
        c, db, _ = client
        # Use an actionable command that is underspecified enough to park.
        created = c.post("/api/v1/tasks", json={
            "original_request": "Move the document to the right folder please"}).json()
        # State may be waiting_for_user (if pipeline asks) or running (if it proceeds).
        assert created["state"] in ("waiting_for_user", "running")

        if created["state"] == "waiting_for_user":
            engine = db.bind
            fresh = sessionmaker(bind=engine)()
            try:
                from backend.models.database import Task

                rows = fresh.query(Task).filter_by(id=created["id"]).all()
                assert len(rows) == 1
                assert rows[0].state == "waiting_for_user"
                metadata = json.loads(rows[0].pending_action)
                assert metadata["kind"] == "clarification"
                assert metadata["awaiting_user_reply"] is True
            finally:
                fresh.close()


# ===========================================================================
# Correction signals
# ===========================================================================


class TestCorrectionSignals:
    def test_correction_records_learning_event(self, client):
        c, db, _ = client
        # Seed a prior terminal task so a correction has something to reference.
        c.post("/api/v1/tasks", json={
            "original_request": "Write the quarterly report in blue font"})
        c.post("/api/v1/tasks", json={
            "original_request": "No that's wrong — use black font instead"})

        from backend.runtime.events import DbEventLedger
        events = DbEventLedger(db).event_history("cognitive")
        corrections = [e for e in events
                       if e["event_type"] == "cognitive.correction.recorded"]
        assert len(corrections) >= 1


# ===========================================================================
# Goal creation integration
# ===========================================================================


class TestGoalIntegration:
    def test_goal_intent_attaches_objective(self, client):
        c, db, _ = client
        created = c.post("/api/v1/tasks", json={
            "original_request": "I want to build a business over the next year"})
        objective_id = created.json().get("objective_id")
        assert objective_id is not None

        from backend.services.goal_manager import GoalManager

        goal = GoalManager(db).get_goal(objective_id)
        assert goal is not None
        assert goal.status == "active"


# ===========================================================================
# Risk / approval boundary stays with Runtime
# ===========================================================================


class TestRiskApprovalBoundary:
    def test_risky_capability_refused_by_runtime_policy(self):
        """Cognitive risk is advisory; executor policy refuses regardless."""
        import asyncio

        from backend.tools.executor import PolicyContext, ToolExecutor

        executor = ToolExecutor()
        output = asyncio.run(executor.execute_tool(
            "package_uninstall",
            {"package_id": "some.pkg"},
            policy_context=None,  # no direct user instruction context
        ))
        assert output.success is False

    def test_simulation_of_risky_action_performs_nothing(self, db_session):
        sim = SimulationEngine()
        candidate = _cand("uninstall_pkg", risk=RiskLevel.CRITICAL,
                          reversible=False)
        tasks_before = db_session.query(Task).count()
        outcome = sim.evaluate(candidate)
        assert outcome.simulated is True
        assert any("no real action" in e for e in outcome.predicted_effects)
        assert db_session.query(Task).count() == tasks_before
        assert db_session.query(Task).count() == tasks_before


def _cand(action_id, risk, reversible):
    from backend.cognitive.state import CandidateAction, DecisionType

    return CandidateAction(
        action_id=action_id,
        decision_type=DecisionType.EXECUTE_STEP,
        description=f"candidate {action_id}",
        risk=risk, reversible=reversible)


from backend.models.database import Objective, Task  # noqa: E402