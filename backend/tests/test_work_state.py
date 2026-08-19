"""
Tests for the durable objective / plan work-state layer.
"""

from datetime import datetime

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.main import app
from backend.models.database import Base, Objective, PlanStep, Task, get_db
from backend.services.model_engine import ModelDecisionEngine
from backend.services.task_runner import TaskRunner
from backend.services.work_state import ensure_task_objective, decompose_request
from backend.tools.executor import ToolExecutor


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "work-state-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "work-state-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


def test_decompose_request_splits_sequence_connectors():
    steps = decompose_request(
        "Summarize the meeting notes and then email them to the team"
    )
    assert steps == ["Summarize the meeting notes", "email them to the team"]


def test_decompose_request_splits_sentences():
    steps = decompose_request(
        "Draft the roadmap for the runtime core. Include milestones and owners."
    )
    assert steps == [
        "Draft the roadmap for the runtime core.",
        "Include milestones and owners.",
    ]


def test_decompose_request_strips_leading_connector_words():
    steps = decompose_request("First, gather the requirements. Then write the design doc")
    assert steps == ["gather the requirements.", "write the design doc"]


def test_decompose_request_single_clause_stays_single():
    assert decompose_request("Draft a professional roadmap") == []
    assert decompose_request("Organize the project architecture") == []


def test_decompose_request_short_or_greeting_not_decomposed():
    assert decompose_request("hi") == []
    assert decompose_request("hey there") == []
    assert decompose_request("Thanks for the help") == []


def test_decompose_request_is_bounded():
    request = "then ".join([
        "Prepare the environment",
        "Write the module",
        "Add the tests",
        "Run the suite",
        "Commit the change",
        "Deploy the build",
    ])
    steps = decompose_request(request)
    assert len(steps) <= 5


def test_decompose_request_deduplicates_parts():
    steps = decompose_request(
        "Write the report then write the report and email it to the team"
    )
    assert len(steps) == 2


@pytest.mark.asyncio
async def test_multistep_request_creates_plan_with_pending_steps(db_session):
    runner = TaskRunner(db_session)
    await runner.acquire_lease()
    task = await runner.create_task(
        "Summarize the meeting notes and then email them to the team"
    )

    objective = ensure_task_objective(db_session, task)
    steps = (
        db_session.query(PlanStep)
        .filter_by(objective_id=objective.id)
        .order_by(PlanStep.step_order.asc())
        .all()
    )
    assert len(steps) == 2
    assert steps[0].status == "in_progress"
    assert steps[0].linked_task_id == task.id
    assert steps[1].status == "pending"
    assert steps[1].linked_task_id is None
    await runner.release_lease()


@pytest.mark.asyncio
async def test_continuation_advances_to_pending_step(db_session):
    runner = TaskRunner(db_session)
    await runner.acquire_lease()

    first = await runner.create_task(
        "Summarize the meeting notes and then email them to the team"
    )
    objective = ensure_task_objective(db_session, first)
    await runner.transition_state(first.id, TaskState.RUNNING, "task.execution_started", {})
    await runner.transition_state(
        first.id,
        TaskState.COMPLETED,
        "task.completed",
        {"summary": "Notes summarized"},
        final_result="Notes summarized",
    )

    # Step 1 done; step 2 is pending and objective stays active.
    db_session.refresh(objective)
    assert objective.status == "active"

    second = await runner.create_task("continue")
    same_objective = ensure_task_objective(db_session, second)
    assert same_objective.id == objective.id

    steps = (
        db_session.query(PlanStep)
        .filter_by(objective_id=objective.id)
        .order_by(PlanStep.step_order.asc())
        .all()
    )
    assert steps[0].status == "completed"
    assert steps[1].status == "in_progress"
    assert steps[1].linked_task_id == second.id
    await runner.release_lease()


@pytest.mark.asyncio
async def test_task_gets_objective_and_completing_task_completes_step(db_session):
    runner = TaskRunner(db_session)
    await runner.acquire_lease()
    task = await runner.create_task("Draft a professional roadmap for the runtime core")

    objective = ensure_task_objective(db_session, task)
    assert task.objective_id == objective.id
    assert objective.status == "active"
    assert "roadmap" in objective.title.lower()

    step = db_session.query(PlanStep).filter_by(objective_id=objective.id).one()
    assert step.status == "in_progress"
    assert step.linked_task_id == task.id

    await runner.transition_state(task.id, TaskState.RUNNING, "task.execution_started", {})
    await runner.transition_state(
        task.id,
        TaskState.COMPLETED,
        "task.completed",
        {"summary": "Roadmap drafted"},
        final_result="Roadmap drafted",
    )
    await runner.release_lease()

    db_session.refresh(objective)
    db_session.refresh(step)
    assert objective.status == "completed"
    assert objective.current_summary == "Roadmap drafted"
    assert step.status == "completed"
    assert step.result_summary == "Roadmap drafted"


@pytest.mark.asyncio
async def test_short_greeting_creates_new_objective_instead_of_reusing_previous(db_session):
    runner = TaskRunner(db_session)
    await runner.acquire_lease()

    first = await runner.create_task("hi")
    first_objective = ensure_task_objective(db_session, first)
    await runner.transition_state(first.id, TaskState.RUNNING, "task.execution_started", {})
    await runner.transition_state(
        first.id,
        TaskState.FAILED,
        "task.failed",
        {"reason": "synthetic failure"},
        terminal_reason="synthetic failure",
    )

    second = await runner.create_task("hey")
    second_objective = ensure_task_objective(db_session, second)
    await runner.release_lease()

    assert second_objective.id != first_objective.id
    assert second_objective.title == "hey"
    assert second.objective_id == second_objective.id


@pytest.mark.asyncio
async def test_continuation_reuses_paused_objective_and_waiting_marks_step_blocked(db_session):
    runner = TaskRunner(db_session)
    await runner.acquire_lease()

    first = await runner.create_task("Organize the project architecture")
    objective = ensure_task_objective(db_session, first)
    await runner.transition_state(first.id, TaskState.RUNNING, "task.execution_started", {})
    await runner.transition_state(
        first.id,
        TaskState.PAUSED,
        "task.paused",
        {"note": "Need a follow-up task to continue."},
    )

    second = await runner.create_task("continue")
    same_objective = ensure_task_objective(db_session, second)
    assert same_objective.id == objective.id
    assert second.objective_id == objective.id

    await runner.transition_state(second.id, TaskState.RUNNING, "task.execution_started", {})
    await runner.transition_state(
        second.id,
        TaskState.WAITING_FOR_USER,
        "task.waiting_for_user",
        {"question": "Which subsystem should I work on first?"},
        pending_question="Which subsystem should I work on first?",
    )
    await runner.release_lease()

    db_session.refresh(same_objective)
    assert same_objective.status == "active"
    assert "Which subsystem" in same_objective.current_summary

    step = (
        db_session.query(PlanStep)
        .filter_by(objective_id=objective.id)
        .order_by(PlanStep.step_order.asc())
        .first()
    )
    assert step.linked_task_id == second.id
    assert step.status == "blocked"
    assert "Which subsystem" in step.blocker_reason


@pytest.mark.asyncio
async def test_model_context_injects_current_objective_and_plan(db_session):
    task = Task(
        id="task_ctx_1",
        original_request="continue",
        normalized_goal="continue the runtime planning work",
        state=TaskState.RUNNING.value,
        version=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(task)
    db_session.commit()

    objective = ensure_task_objective(db_session, task)
    db_session.add(PlanStep(
        objective_id=objective.id,
        step_order=2,
        description="Document pending blockers for the runtime core",
        status="blocked",
        blocker_reason="Need confirmation about the next subsystem.",
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    ))
    objective.current_summary = "Need confirmation about the next subsystem."
    db_session.commit()

    engine = ModelDecisionEngine(db_session, ToolExecutor())
    messages = engine._build_context(
        task_id=task.id,
        original_request="continue",
        normalized_goal=task.normalized_goal,
        event_history=[],
        current_state="running",
    )
    user_msg = next(message for message in messages if message["role"] == "user")
    content = user_msg["content"]
    assert "CURRENT OBJECTIVE & PLAN" in content
    assert objective.title in content
    assert "Document pending blockers" in content
    assert "Need confirmation about the next subsystem." in content


class TestObjectivesApi:
    def test_get_task_objective(self, client, db_session):
        task = Task(
            id="task_obj_api",
            original_request="Continue building the runtime mind",
            normalized_goal="build the runtime mind",
            state=TaskState.RUNNING.value,
            version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db_session.add(task)
        db_session.commit()

        objective = ensure_task_objective(db_session, task)
        response = client.get(f"/api/v1/tasks/{task.id}/objective")
        assert response.status_code == 200
        body = response.json()
        assert body["id"] == objective.id
        assert body["title"] == objective.title
        assert body["steps"][0]["linked_task_id"] == task.id

    def test_create_list_and_update_objective(self, client):
        created = client.post(
            "/api/v1/objectives",
            json={
                "title": "Build durable work-state layer",
                "purpose": "Track what is active, done, and pending above tasks",
                "priority": 2,
                "plan_steps": [
                    "Define the data model",
                    "Expose APIs",
                ],
            },
        )
        assert created.status_code == 200
        body = created.json()
        assert body["title"] == "Build durable work-state layer"
        assert len(body["steps"]) == 2
        assert body["steps"][0]["status"] == "pending"

        objective_id = body["id"]
        updated = client.patch(
            f"/api/v1/objectives/{objective_id}",
            json={"status": "paused", "current_summary": "Waiting for implementation review"},
        )
        assert updated.status_code == 200
        assert updated.json()["status"] == "paused"
        assert updated.json()["current_summary"] == "Waiting for implementation review"

        listed = client.get("/api/v1/objectives?status=paused")
        assert listed.status_code == 200
        paused = listed.json()["objectives"]
        assert any(item["id"] == objective_id for item in paused)

    def test_add_and_update_plan_step(self, client):
        objective = client.post(
            "/api/v1/objectives",
            json={"title": "Professionalize runtime docs", "priority": 3, "plan_steps": []},
        ).json()

        created_step = client.post(
            f"/api/v1/objectives/{objective['id']}/steps",
            json={"description": "Add decision index updates", "status": "pending"},
        )
        assert created_step.status_code == 200
        step = created_step.json()
        assert step["step_order"] == 1

        updated_step = client.patch(
            f"/api/v1/objectives/steps/{step['id']}",
            json={"status": "completed", "result_summary": "Decision index updated"},
        )
        assert updated_step.status_code == 200
        assert updated_step.json()["status"] == "completed"
        assert updated_step.json()["result_summary"] == "Decision index updated"


# ---------------------------------------------------------------------------
# Objective planning tool unit tests
# ---------------------------------------------------------------------------

class TestObjectivePlanningTools:
    """Unit tests for the objective_planning_tool.py BaseTool subclasses."""

    @pytest.fixture(autouse=True)
    def setup_db(self, tmp_path):
        """Wire a fresh in-memory database into the global db_manager slot."""
        from backend.core.db_manager import DatabaseManager
        from backend.models.database import set_db_manager

        db_path = str(tmp_path / "test_obj_planning.db")
        db_mgr = DatabaseManager(db_path)
        set_db_manager(db_mgr)
        yield
        set_db_manager(None)

    @pytest.mark.asyncio
    async def test_objective_create_tool_creates_with_steps(self):
        """ObjectiveCreateTool persists an objective with ordered steps in DB."""
        from backend.tools.objective_planning_tool import ObjectiveCreateTool, ObjectiveCreateInput
        from backend.models.database import get_db_manager, Objective, PlanStep

        tool = ObjectiveCreateTool()
        inp = ObjectiveCreateInput(
            title="Find remote work in Kenya",
            purpose="Generate income through legitimate online platforms",
            steps=[
                "Research DataAnnotation.tech eligibility for Kenya",
                "Check Appen current project availability",
                "Evaluate Toloka task volume and payout",
                "Submit applications to top 2 platforms",
            ],
            priority=2,
        )
        output = await tool.execute(inp)

        assert output.success is True, f"Tool failed: {output.error}"
        assert output.data["title"] == "Find remote work in Kenya"
        assert len(output.data["steps"]) == 4
        assert output.data["steps"][0]["status"] == "in_progress"
        assert output.data["steps"][1]["status"] == "pending"
        assert output.data["priority"] == 2

        # Verify DB persistence
        db_mgr = get_db_manager()
        with db_mgr.get_session() as session:
            obj = session.query(Objective).filter_by(title="Find remote work in Kenya").first()
            assert obj is not None
            assert obj.status == "active"
            steps = session.query(PlanStep).filter_by(objective_id=obj.id).order_by(PlanStep.step_order).all()
            assert len(steps) == 4
            assert steps[0].status == "in_progress"
            assert steps[1].status == "pending"

    @pytest.mark.asyncio
    async def test_objective_complete_step_advances_plan(self):
        """ObjectiveCompleteStepTool marks step done and auto-advances the next one."""
        from backend.tools.objective_planning_tool import (
            ObjectiveCreateTool, ObjectiveCreateInput,
            ObjectiveCompleteStepTool, ObjectiveCompleteStepInput,
            ObjectiveGetPlanTool, ObjectiveGetPlanInput,
        )

        # Create a plan first
        create_tool = ObjectiveCreateTool()
        create_out = await create_tool.execute(ObjectiveCreateInput(
            title="Learn Forex basics",
            steps=["Watch Rayner Teo intro videos", "Study candlestick patterns", "Practice on demo account"],
        ))
        assert create_out.success is True
        objective_id = create_out.data["objective_id"]

        # Complete step 1
        complete_tool = ObjectiveCompleteStepTool()
        comp_out = await complete_tool.execute(ObjectiveCompleteStepInput(
            result_summary="Watched 5 Rayner Teo videos. Learned trend-following strategy and risk/reward basics.",
            objective_id=objective_id,
        ))
        assert comp_out.success is True, f"Complete step failed: {comp_out.error}"
        assert comp_out.data["completed_step"] == 1
        assert comp_out.data["next_step"] == 2
        assert comp_out.data["objective_status"] == "active"

        # Verify via get_plan
        get_tool = ObjectiveGetPlanTool()
        plan_out = await get_tool.execute(ObjectiveGetPlanInput(objective_id=objective_id))
        assert plan_out.success is True
        steps = plan_out.data["steps"]
        assert steps[0]["status"] == "completed"
        assert "Rayner Teo" in steps[0]["result_summary"]
        assert steps[1]["status"] == "in_progress"

    @pytest.mark.asyncio
    async def test_complete_all_steps_marks_objective_completed(self):
        """Completing every step transitions the objective to completed status."""
        from backend.tools.objective_planning_tool import (
            ObjectiveCreateTool, ObjectiveCreateInput,
            ObjectiveCompleteStepTool, ObjectiveCompleteStepInput,
        )

        create_out = await ObjectiveCreateTool().execute(ObjectiveCreateInput(
            title="Quick two-step goal",
            steps=["Step A", "Step B"],
        ))
        assert create_out.success is True
        obj_id = create_out.data["objective_id"]

        complete_tool = ObjectiveCompleteStepTool()
        out1 = await complete_tool.execute(ObjectiveCompleteStepInput(
            result_summary="Step A done.", objective_id=obj_id,
        ))
        assert out1.success is True
        assert out1.data["objective_status"] == "active"

        out2 = await complete_tool.execute(ObjectiveCompleteStepInput(
            result_summary="Step B done.", objective_id=obj_id,
        ))
        assert out2.success is True
        assert out2.data["objective_status"] == "completed"
        assert out2.data["next_step"] is None

    @pytest.mark.asyncio
    async def test_build_work_context_uses_expanded_limits(self):
        """build_work_context should not truncate 500-char summaries."""
        from backend.tools.objective_planning_tool import ObjectiveCreateTool, ObjectiveCreateInput
        from backend.services.work_state import build_work_context
        from backend.models.database import get_db_manager, Objective, Task, get_db_manager

        # Create objective via tool
        create_out = await ObjectiveCreateTool().execute(ObjectiveCreateInput(
            title="Long summary test",
            steps=["Research phase"],
        ))
        assert create_out.success is True
        obj_id = create_out.data["objective_id"]

        # Inject a 400-char summary directly
        long_summary = "Found 12 relevant platforms: " + ("DataAnnotation, Appen, Toloka, Remotasks, Outlier, " * 5)
        long_summary = long_summary[:400]

        db_mgr = get_db_manager()
        with db_mgr.get_session() as session:
            obj = session.query(Objective).filter_by(id=obj_id).first()
            obj.current_summary = long_summary
            # Attach a dummy task for build_work_context lookup
            task = Task(
                id="test-task-expand",
                original_request="test",
                state="running",
                objective_id=obj_id,
            )
            session.add(task)
            session.commit()

            context = build_work_context(session, task_id="test-task-expand", original_request="test")

        assert long_summary[:300].rstrip() in context, "build_work_context truncated summary below 400 chars"


class TestContinuationPatterns:
    """Regression tests for is_continuation_request covering true continuations and adversarial fresh requests."""

    @pytest.mark.parametrize("request_text", [
        "or kb",
        "and in gb",
        "what about tomorrow",
        "how about next week",
        "to mb",
        "in megabytes",
        "or 10mb",
        "continue",
        "resume",
        "try python instead",
        "1",
        "option 1",
        "yes",
        "sure",
        "same one",
    ])
    def test_true_continuations_attach_context(self, request_text):
        from backend.services.work_state import is_continuation_request
        assert is_continuation_request(request_text) is True

    @pytest.mark.parametrize("request_text", [
        "and how's my battery",
        "and how is my battery",
        "or should I just restart",
        "in what year was Python created",
        "and what time is it",
        "to what extent is AI regulated in Europe",
        "in Python, how do you sort a list?",
        "hi",
        "hello",
        "what time is it",
        "read the file test.txt",
        "check my disk space",
        "thanks",
        "good morning",
    ])
    def test_fresh_requests_do_not_attach_context(self, request_text):
        from backend.services.work_state import is_continuation_request
        assert is_continuation_request(request_text) is False

