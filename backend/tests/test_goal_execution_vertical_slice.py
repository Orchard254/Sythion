"""Goal Execution Vertical Slice V1 over real planner, TaskRunner and SQLite.

Only the OS/tool-facing worker is scripted and restricted to temporary files.
No live provider, desktop OS actions, arbitrary external side effects, cloud
calls, or background autonomy. Tests require the complete Sythion checkout.
"""

from __future__ import annotations

import asyncio
import hashlib
import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.core.config import settings
from backend.models.database import Base, Objective, PlanStep, Task, TaskEvent
from backend.runtime.events import DbEventLedger
from backend.services.goal_manager import GoalManager
from backend.services.planner import CandidateStepModel, Planner
from backend.services.task_orchestrator import RetryPolicy, TaskOrchestrator
from backend.services.task_runner import TaskRunner


@pytest.fixture
def db():
    engine = create_engine(
        "sqlite://", connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()
    engine.dispose()


@pytest.fixture
def file_db(tmp_path):
    """Real file-backed SQLite: two Sessions must use different connections."""
    engine = create_engine(
        f"sqlite:///{tmp_path / 'goal-v1.sqlite3'}",
        connect_args={"check_same_thread": False, "timeout": 10.0},
    )
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, expire_on_commit=False)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def setup_goal(db, title="Three-step report"):
    goal = GoalManager(db).create_goal(title, priority=2)
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="inspect", description="Inspect local numeric input"),
        CandidateStepModel(key="summarize", description="Summarize numeric input",
                           depends_on=["inspect"]),
        CandidateStepModel(key="verify", description="Independently verify local summary",
                           depends_on=["summarize"]),
    ], source="deterministic", reason="bounded vertical slice test only")
    return goal, plan


def create_scripted_worker(db, runner, workdir, orch=None, *, fail_on=None,
                           return_without_terminal=False, preapply=False):
    source = workdir / "numbers.txt"
    source.write_text("2\n3\n5\n", encoding="utf-8")
    visited = []
    async def run(_db, task_id, _executor):
        row = db.query(Task).filter_by(id=task_id).one()
        step = db.query(PlanStep).filter_by(evidence_task_id=task_id).one()
        visited.append(step.step_key)
        assert row.state == "running"
        if return_without_terminal:
            return
        assert await runner.acquire_lease(task_id=task_id)
        try:
            if step.step_key == fail_on:
                await runner.transition_state(
                    task_id, TaskState.FAILED, "task.failed",
                    {"code": "SCRIPTED_WORKER_FAILURE"},
                    terminal_reason="scripted test failure",
                )
                if preapply and orch is not None:
                    await orch.apply_task_outcome(task_id)
                return
            numbers = [int(line) for line in source.read_text(encoding="utf-8").splitlines()]
            if step.step_key == "inspect":
                observation = {"count": len(numbers), "sha256": hashlib.sha256(source.read_bytes()).hexdigest()}
                (workdir / "observation.json").write_text(json.dumps(observation), encoding="utf-8")
                summary = "inspected input"
            elif step.step_key == "summarize":
                assert (workdir / "observation.json").exists()
                summary_data = {"total": sum(numbers), "count": len(numbers)}
                (workdir / "summary.json").write_text(json.dumps(summary_data), encoding="utf-8")
                summary = "summarized input"
            else:
                summary_data = json.loads((workdir / "summary.json").read_text(encoding="utf-8"))
                observation = json.loads((workdir / "observation.json").read_text(encoding="utf-8"))
                assert summary_data == {"total": sum(numbers), "count": len(numbers)}
                assert observation["sha256"] == hashlib.sha256(source.read_bytes()).hexdigest()
                (workdir / "verified.txt").write_text("PASS", encoding="utf-8")
                summary = "verified local summary"
            await runner.transition_state(
                task_id, TaskState.COMPLETED, "task.completed",
                {"summary": summary}, final_result=summary,
            )
            if preapply and orch is not None:
                # Simulates the real kernel PostTaskPipeline applying the
                # step BEFORE the waiting goal orchestrator inspects it.
                await orch.apply_task_outcome(task_id)
        finally:
            await runner.release_lease()
    return run, visited


@pytest.mark.asyncio
async def test_three_step_goal_finishes_with_verified_artifacts_and_durable_history(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db)
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    run, visited = create_scripted_worker(db, runner, tmp_path)
    response = await orch.execute_session(goal.id, run_task_fn=run)

    assert visited == ["inspect", "summarize", "verify"]
    assert [item["outcome"] for item in response["executed"]] == ["completed"] * 3
    assert response["stopped_reason"] == "no_eligible_step"
    assert GoalManager(db).get_goal(goal.id).status == "completed"
    assert GoalManager(db).progress(goal.id)["fraction"] == 1.0

    # Independent external-file assertion; no model statement is accepted as
    # proof that the scripted worker produced the expected output.
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == {
        "count": 3, "total": 10,
    }
    assert (tmp_path / "verified.txt").read_text(encoding="utf-8") == "PASS"

    steps = Planner(db).plan_steps(plan.id)
    assert [s.status for s in steps] == ["completed", "completed", "completed"]
    assert all(s.evidence_task_id == s.linked_task_id for s in steps)
    assert len(set(s.evidence_task_id for s in steps)) == 3
    assert all(db.get(Task, s.evidence_task_id).state == "completed" for s in steps)
    events = DbEventLedger(db).event_history(f"goal:{goal.id}")
    assert [e["event_type"] for e in events].count("plan_step.started") == 3
    assert [e["event_type"] for e in events].count("plan_step.completed") == 3

    # A new ORM session sees the same terminal evidence (not in-memory state).
    session = sessionmaker(bind=db.bind)()
    try:
        assert GoalManager(session).get_goal(goal.id).status == "completed"
        assert GoalManager(session).progress(goal.id)["completed_steps"] == 3
        assert len([e for e in DbEventLedger(session).event_history(f"goal:{goal.id}")
                    if e["event_type"] == "plan_step.completed"]) == 3
    finally:
        session.close()


@pytest.mark.asyncio
async def test_already_applied_kernel_outcome_counts_as_verified_completion_once(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Post-task race")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    run, visited = create_scripted_worker(db, runner, tmp_path, orch, preapply=True)
    result = await orch.execute_session(goal.id, run_task_fn=run)
    assert visited == ["inspect", "summarize", "verify"]
    assert [e["outcome"] for e in result["executed"]] == ["completed"] * 3
    assert "already_applied" not in repr(result)
    events = DbEventLedger(db).event_history(f"goal:{goal.id}")
    assert [e["event_type"] for e in events].count("plan_step.completed") == 3
    assert GoalManager(db).get_goal(goal.id).status == "completed"


@pytest.mark.asyncio
async def test_failed_middle_step_does_not_run_dependent_work(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Failure stops work")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(
        state_store=runner, planner=Planner(db), retry_policy=RetryPolicy(max_attempts=1),
    )
    run, visited = create_scripted_worker(db, runner, tmp_path, fail_on="summarize")
    result = await orch.execute_session(goal.id, run_task_fn=run)
    assert visited == ["inspect", "summarize"]
    assert [e["outcome"] for e in result["executed"]] == ["completed", "failed"]
    assert result["stopped_reason"] == "step_retry_exhausted"
    assert GoalManager(db).get_goal(goal.id).status == "paused"
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "completed", "failed", "blocked",
    ]
    assert not (tmp_path / "verified.txt").exists()
    assert GoalManager(db).progress(goal.id)["fraction"] < 1.0


@pytest.mark.asyncio
async def test_worker_returning_nonterminal_stops_without_forging_failure(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Unresolved worker")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    run, visited = create_scripted_worker(db, runner, tmp_path, return_without_terminal=True)
    result = await orch.execute_session(goal.id, run_task_fn=run)
    assert visited == ["inspect"]
    assert result["executed"][0]["outcome"] == "unverified"
    assert result["stopped_reason"] == "outcome_unverified"
    assert GoalManager(db).get_goal(goal.id).status == "active"
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]
    assert not (tmp_path / "summary.json").exists()
    assert not (tmp_path / "verified.txt").exists()


@pytest.mark.asyncio
async def test_preapplied_failed_step_never_masquerades_as_success(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Preapplied failure")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(
        state_store=runner, planner=Planner(db), retry_policy=RetryPolicy(max_attempts=1),
    )
    run, visited = create_scripted_worker(
        db, runner, tmp_path, orch, fail_on="summarize", preapply=True,
    )
    result = await orch.execute_session(goal.id, run_task_fn=run)
    assert visited == ["inspect", "summarize"]
    assert result["executed"][-1]["outcome"] == "failed"
    assert result["stopped_reason"] == "step_retry_exhausted"
    assert GoalManager(db).get_goal(goal.id).status == "paused"
    assert Planner(db).plan_steps(plan.id)[-1].status == "blocked"


@pytest.mark.asyncio
async def test_background_unverified_execution_stops_without_false_failure_accounting(
    db, tmp_path,
):
    from backend.services.goal_selector import RunnableGoalSelector

    goal, plan = setup_goal(db, "Background nonterminal")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    run, visited = create_scripted_worker(db, runner, tmp_path, return_without_terminal=True)
    result = await orch.run_background_cycle(
        RunnableGoalSelector(db), max_steps=3, run_task_fn=run,
    )
    assert visited == ["inspect"]
    assert result["executed"][0]["outcome"] == "unverified"
    assert result["stopped_reason"] == "outcome_unverified"
    goal_row = GoalManager(db).get_goal(goal.id)
    assert goal_row.status == "active"
    assert goal_row.times_served == 1  # an attempt received its fairness slot
    assert goal_row.consecutive_failed_sessions == 0  # not a proven failure
    assert [step.status for step in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]


@pytest.mark.asyncio
async def test_background_preapplied_failure_is_accounted_as_failure(
    db, tmp_path,
):
    from backend.services.goal_selector import RunnableGoalSelector

    goal, plan = setup_goal(db, "Background preapplied failure")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(
        state_store=runner, planner=Planner(db), retry_policy=RetryPolicy(max_attempts=1),
    )
    run, visited = create_scripted_worker(
        db, runner, tmp_path, orch, fail_on="inspect", preapply=True,
    )
    result = await orch.run_background_cycle(
        RunnableGoalSelector(db), max_steps=3, run_task_fn=run,
    )
    assert visited == ["inspect"]
    assert result["executed"][0]["outcome"] == "failed"
    assert result["stopped_reason"] == "step_retry_exhausted_or_permanent_failure"
    assert GoalManager(db).get_goal(goal.id).status == "paused"
    assert GoalManager(db).get_goal(goal.id).consecutive_failed_sessions >= 1
    assert [step.status for step in Planner(db).plan_steps(plan.id)] == [
        "failed", "blocked", "blocked",
    ]


@pytest.mark.asyncio
async def test_goal_with_unverified_completion_criteria_remains_active(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Criteria not asserted")
    goal.completion_criteria = "Report is independently approved by the owner"
    goal.criteria_verified = False
    db.commit()
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    run, visited = create_scripted_worker(db, runner, tmp_path)
    result = await orch.execute_session(goal.id, run_task_fn=run)
    assert [e["outcome"] for e in result["executed"]] == ["completed"] * 3
    assert (tmp_path / "verified.txt").read_text(encoding="utf-8") == "PASS"
    assert GoalManager(db).progress(goal.id)["fraction"] == 1.0
    assert GoalManager(db).get_goal(goal.id).status == "active"
    assert GoalManager(db).get_goal(goal.id).criteria_verified is False


@pytest.mark.asyncio
async def test_exhausted_goal_slot_releases_runner_lease_once(
    db, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal = GoalManager(db).create_goal("No plan: must not launch a task")
    class CountingRunner:
        def __init__(self):
            self.acquired = 0
            self.released = 0
        async def acquire_lease(self):
            self.acquired += 1
            return True
        async def release_lease(self):
            self.released += 1

    runner = CountingRunner()
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    result = await orchestrator.execute_session(
        goal.id, run_task_fn=lambda *_: pytest.fail("Worker must not launch"),
    )
    assert result == {"executed": [], "stopped_reason": "no_eligible_step"}
    assert runner.acquired == 1
    assert runner.released == 1


@pytest.mark.asyncio
async def test_completed_task_without_exact_step_evidence_link_stops(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Orphan task completion")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    healthy, visited = create_scripted_worker(db, runner, tmp_path)

    async def broken_link_worker(db_unused, task_id, executor):
        await healthy(db_unused, task_id, executor)
        step = db.query(PlanStep).filter_by(linked_task_id=task_id).one()
        step.evidence_task_id = None
        db.commit()

    result = await orch.execute_session(goal.id, run_task_fn=broken_link_worker)
    assert visited == ["inspect"]
    assert result["executed"][0]["outcome"] == "unverified"
    assert result["stopped_reason"] == "outcome_unverified"
    assert GoalManager(db).get_goal(goal.id).status == "active"
    assert Planner(db).plan_steps(plan.id)[0].status == "running"
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.asyncio
async def test_preapplied_outcomes_from_separate_worker_session_are_not_duplicated(
    file_db, tmp_path, monkeypatch,
):
    db = file_db
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Fresh sessions")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    observed_steps = []
    Session = sessionmaker(bind=db.bind)

    async def own_session_worker(_db, task_id, _executor):
        # Models cached in the outer orchestrator session remain stale until
        # explicitly reloaded. This simulates the real kernel's own session.
        separate = Session()
        try:
            # This file-backed database must be observed through a distinct
            # physical SQLite connection, not a shared StaticPool connection.
            assert (
                separate.connection().connection.dbapi_connection
                is not db.connection().connection.dbapi_connection
            )
            worker_runner = TaskRunner(separate)
            worker_orchestrator = TaskOrchestrator(
                state_store=worker_runner, planner=Planner(separate),
            )
            worker, seen = create_scripted_worker(
                separate, worker_runner, tmp_path, worker_orchestrator, preapply=True,
            )
            await worker(_db, task_id, _executor)
            observed_steps.extend(seen)
        finally:
            separate.close()

    result = await orchestrator.execute_session(
        goal.id, run_task_fn=own_session_worker,
    )
    assert observed_steps == ["inspect", "summarize", "verify"]
    assert [entry["outcome"] for entry in result["executed"]] == ["completed"] * 3
    assert GoalManager(db).get_goal(goal.id).status == "completed"
    assert GoalManager(db).progress(goal.id)["fraction"] == 1.0
    completed = [
        ev for ev in DbEventLedger(db).event_history(f"goal:{goal.id}")
        if ev["event_type"] == "plan_step.completed"
    ]
    assert len(completed) == 3
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "completed", "completed", "completed",
    ]


# --- Production wiring contract: real worker receives installed tool executor ---

@pytest.mark.asyncio
async def test_explicit_default_worker_receives_registered_executor(
    db, tmp_path, monkeypatch,
):
    from backend.api import dependencies
    from backend.services import task_execution_loop

    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    sentinel_executor = object()
    provider_calls = []
    monkeypatch.setattr(dependencies, "get_tool_executor",
                        lambda: (provider_calls.append(1), sentinel_executor)[1])
    goal, _ = setup_goal(db, "Explicit executor wiring")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    scripted, seen = create_scripted_worker(db, runner, tmp_path)
    worker_calls = []

    async def default_worker(_session, task_id, executor):
        assert executor is sentinel_executor
        worker_calls.append(task_id)
        await scripted(_session, task_id, executor)

    monkeypatch.setattr(task_execution_loop, "run_task", default_worker)
    result = await orchestrator.execute_session(goal.id)
    assert [entry["outcome"] for entry in result["executed"]] == ["completed"] * 3
    assert seen == ["inspect", "summarize", "verify"]
    assert len(worker_calls) == 3
    assert provider_calls == [1]
    assert GoalManager(db).get_goal(goal.id).status == "completed"


@pytest.mark.asyncio
async def test_background_default_worker_receives_registered_executor(
    db, tmp_path, monkeypatch,
):
    from backend.api import dependencies
    from backend.services import task_execution_loop
    from backend.services.goal_selector import RunnableGoalSelector

    sentinel_executor = object()
    monkeypatch.setattr(dependencies, "get_tool_executor", lambda: sentinel_executor)
    goal, _ = setup_goal(db, "Background executor wiring")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    scripted, seen = create_scripted_worker(db, runner, tmp_path)

    async def default_worker(_session, task_id, executor):
        assert executor is sentinel_executor
        await scripted(_session, task_id, executor)

    monkeypatch.setattr(task_execution_loop, "run_task", default_worker)
    result = await orchestrator.run_background_cycle(
        RunnableGoalSelector(db), max_steps=3,
    )
    assert [entry["outcome"] for entry in result["executed"]] == ["completed"] * 3
    assert seen == ["inspect", "summarize", "verify"]
    assert GoalManager(db).get_goal(goal.id).status == "completed"


@pytest.mark.asyncio
@pytest.mark.parametrize("background", [False, True])
async def test_unavailable_executor_does_not_create_goal_tasks(
    db, monkeypatch, background,
):
    from backend.api import dependencies
    from backend.services.goal_selector import RunnableGoalSelector

    monkeypatch.setattr(dependencies, "get_tool_executor", lambda: None)
    goal, plan = setup_goal(db, "Executor unavailable")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    if background:
        result = await orchestrator.run_background_cycle(
            RunnableGoalSelector(db), max_steps=3,
        )
    else:
        result = await orchestrator.execute_session(goal.id)

    assert result == {"executed": [], "stopped_reason": "tool_executor_unavailable"}
    assert db.query(Task).filter_by(objective_id=goal.id).count() == 0
    assert [step.status for step in Planner(db).plan_steps(plan.id)] == [
        "ready", "blocked", "blocked",
    ]
    assert not [evt for evt in DbEventLedger(db).event_history(f"goal:{goal.id}")
                if evt["event_type"] == "plan_step.started"]


@pytest.mark.asyncio
async def test_scripted_worker_does_not_consult_production_tool_provider(
    db, tmp_path, monkeypatch,
):
    from backend.api import dependencies

    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    def forbidden():
        pytest.fail("Scripted worker must not load the registered executor")
    monkeypatch.setattr(dependencies, "get_tool_executor", forbidden)
    goal, _ = setup_goal(db, "Injected worker")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    scripted, seen = create_scripted_worker(db, runner, tmp_path)
    result = await orchestrator.execute_session(goal.id, run_task_fn=scripted)
    assert [entry["outcome"] for entry in result["executed"]] == ["completed"] * 3
    assert seen == ["inspect", "summarize", "verify"]


@pytest.mark.asyncio
async def test_executor_provider_exception_stops_before_task_creation(
    db, monkeypatch,
):
    from backend.api import dependencies

    def broken_provider():
        raise RuntimeError("internal provider failure; do not expose")

    monkeypatch.setattr(dependencies, "get_tool_executor", broken_provider)
    goal, _ = setup_goal(db, "Provider broken")
    orchestrator = TaskOrchestrator(
        state_store=TaskRunner(db), planner=Planner(db),
    )
    result = await orchestrator.execute_session(goal.id)
    assert result == {"executed": [], "stopped_reason": "tool_executor_unavailable"}
    assert db.query(Task).filter_by(objective_id=goal.id).count() == 0
    assert "internal provider failure" not in repr(result)


# --- Worker/recorder exceptions must not launch the next dependent step ---

@pytest.mark.asyncio
async def test_explicit_worker_exception_halts_without_synthesizing_terminal_failure(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Worker raises after task starts")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    calls = []

    async def broken_worker(_session, task_id, _executor):
        calls.append(task_id)
        raise RuntimeError("SECRET_WORKER_EXCEPTION")

    result = await orch.execute_session(goal.id, run_task_fn=broken_worker)
    assert len(calls) == 1
    assert result["stopped_reason"] == "worker_or_outcome_error"
    assert result["executed"][0]["outcome"] == "unverified"
    assert "SECRET_WORKER_EXCEPTION" not in repr(result)
    assert db.get(Task, calls[0]).state == "running"
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]
    assert GoalManager(db).get_goal(goal.id).status == "active"
    assert not (tmp_path / "verified.txt").exists()


@pytest.mark.asyncio
async def test_post_worker_outcome_writer_exception_stops_without_retry(
    db, tmp_path, monkeypatch,
):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Outcome writer fails")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    scripted, visited = create_scripted_worker(db, runner, tmp_path)

    async def broken_applier(_task_id):
        raise RuntimeError("SECRET_POST_TASK_ERROR")

    monkeypatch.setattr(orch, "apply_task_outcome", broken_applier)
    result = await orch.execute_session(goal.id, run_task_fn=scripted)
    assert visited == ["inspect"]
    assert result["stopped_reason"] == "worker_or_outcome_error"
    assert result["executed"][0]["outcome"] == "unverified"
    assert "SECRET_POST_TASK_ERROR" not in repr(result)
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]
    assert GoalManager(db).progress(goal.id)["fraction"] == 0.0
    assert not (tmp_path / "summary.json").exists()


@pytest.mark.asyncio
async def test_background_worker_exception_is_not_counted_as_failed_session(
    db, tmp_path,
):
    from backend.services.goal_selector import RunnableGoalSelector

    goal, plan = setup_goal(db, "Background failure")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    calls = []

    async def broken_worker(_session, task_id, _executor):
        calls.append(task_id)
        raise OSError("PRIVATE_PATH")

    result = await orch.run_background_cycle(
        RunnableGoalSelector(db), max_steps=3, run_task_fn=broken_worker,
    )
    assert len(calls) == 1
    assert result["stopped_reason"] == "worker_or_outcome_error"
    assert result["executed"][0]["outcome"] == "unverified"
    assert "PRIVATE_PATH" not in repr(result)
    goal_row = GoalManager(db).get_goal(goal.id)
    assert goal_row.status == "active"
    assert goal_row.times_served == 1
    assert goal_row.consecutive_failed_sessions == 0
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]


@pytest.mark.asyncio
async def test_cancelled_worker_propagates_and_does_not_start_followup_step(
    db, tmp_path, monkeypatch,
):
    import asyncio
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal, plan = setup_goal(db, "Cancelled session")
    runner = TaskRunner(db)
    orch = TaskOrchestrator(state_store=runner, planner=Planner(db))
    calls = []

    async def cancelled_worker(_session, task_id, _executor):
        calls.append(task_id)
        raise asyncio.CancelledError()

    with pytest.raises(asyncio.CancelledError):
        await orch.execute_session(goal.id, run_task_fn=cancelled_worker)
    assert len(calls) == 1
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "running", "blocked", "blocked",
    ]


@pytest.mark.asyncio
async def test_three_step_goal_uses_actual_readonly_tool_executor_in_temp_workspace(
    db, tmp_path, monkeypatch,
):
    """Real FileRead/FileHash through ToolExecutor; scripted worker only writes test outputs."""
    import hashlib
    from backend.tools import file_tools
    from backend.tools.executor import PolicyContext, ToolExecutor
    from backend.tools.file_tools import FileCreateTool, FileHashTool, FileReadTool

    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    monkeypatch.setattr(file_tools, "_approved_roots", lambda: [tmp_path.resolve()])
    executor = ToolExecutor()
    for tool in (FileReadTool(), FileHashTool(), FileCreateTool()):
        executor.register_tool(tool)

    goal, plan = setup_goal(db, "Read-only tool inspection goal")
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    scripted, visited = create_scripted_worker(db, runner, tmp_path)
    source = tmp_path / "numbers.txt"
    original = source.read_bytes()
    checks = []

    async def worker_using_real_read_tools(_session, task_id, _test_executor):
        read = await executor.execute_tool("file_read", {"path": str(source)})
        digest = await executor.execute_tool(
            "file_hash", {"path": str(source), "algorithm": "sha256"}
        )
        assert read.success, read.error
        assert digest.success, digest.error
        # Compare text to text: file_read normalizes CRLF on Windows.
        # Independently verify the digest and byte count against raw disk
        # contents, preserving the real-tool evidence checks.
        raw = source.read_bytes()
        assert read.data["content"] == source.read_text(encoding="utf-8")
        assert read.data["bytes_read"] == len(raw)
        assert digest.data["file_size"] == len(raw)
        assert digest.data["hash"] == hashlib.sha256(raw).hexdigest()
        checks.append((read.data["bytes_read"], digest.data["hash"]))
        await scripted(_session, task_id, _test_executor)

    result = await orchestrator.execute_session(
        goal.id, run_task_fn=worker_using_real_read_tools,
    )
    assert [e["outcome"] for e in result["executed"]] == ["completed"] * 3
    assert visited == ["inspect", "summarize", "verify"]
    assert len(checks) == 3
    assert all(size == len(original) for size, _digest in checks)
    assert all(digest == hashlib.sha256(original).hexdigest() for _size, digest in checks)
    assert GoalManager(db).get_goal(goal.id).status == "completed"
    assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == {
        "total": 10, "count": 3,
    }
    # A direct-instruction flag alone is NEVER permission to mutate files.
    target = tmp_path / "not-authorized.txt"
    rejected = await executor.execute_tool(
        "file_create", {"path": str(target), "content": "must not exist"},
        policy_context=PolicyContext(direct_user_instruction=True, task_id="test-task"),
    )
    assert not rejected.success
    assert rejected.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    assert not target.exists()


# --- Startup recovery must never turn unresolved steps into new work ---

@pytest.mark.asyncio
async def test_unresolved_running_step_blocks_independent_ready_step(
    db, monkeypatch,
):
    from backend.services.planner import CandidateStepModel

    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal = GoalManager(db).create_goal("Interrupted and independent work")
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="old", description="Old unfinished work"),
        CandidateStepModel(key="independent", description="Independent work"),
    ])
    steps = Planner(db).plan_steps(plan.id)
    steps[0].status = "running"
    db.commit()

    called = []
    async def worker(*_):
        called.append(True)
        pytest.fail("Unresolved goal must not start another worker")

    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))
    result = await orchestrator.execute_session(goal.id, run_task_fn=worker)
    assert result == {
        "executed": [],
        "stopped_reason": "unresolved_step_review_required",
    }
    assert called == []
    assert [step.status for step in Planner(db).plan_steps(plan.id)] == [
        "running", "ready",
    ]
    assert db.query(Task).filter_by(objective_id=goal.id).count() == 0


@pytest.mark.asyncio
async def test_running_step_blocks_retry_mutation_of_other_failed_step(
    db, monkeypatch,
):
    from backend.services.planner import CandidateStepModel

    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 5)
    goal = GoalManager(db).create_goal("Unresolved step and retryable failure")
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="failed", description="Failed old work"),
        CandidateStepModel(key="unresolved", description="Unresolved old work"),
    ])
    steps = Planner(db).plan_steps(plan.id)
    steps[0].status = "failed"
    steps[1].status = "running"
    db.commit()
    runner = TaskRunner(db)
    orchestrator = TaskOrchestrator(state_store=runner, planner=Planner(db))

    async def unexpected(*_):
        pytest.fail("No worker must be dispatched with unresolved plan work")
    result = await orchestrator.execute_session(goal.id, run_task_fn=unexpected)
    assert result["stopped_reason"] == "unresolved_step_review_required"
    assert result["executed"] == []
    assert [step.status for step in Planner(db).plan_steps(plan.id)] == [
        "failed", "running",
    ]
    assert db.query(Task).filter_by(objective_id=goal.id).count() == 0


@pytest.mark.asyncio
async def test_interrupted_goal_preview_stop_reason_is_review_not_generic(db, monkeypatch):
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 2)
    goal = GoalManager(db).create_goal("Interrupted goal step")
    plan = Planner(db).create_plan(goal.id, [
        CandidateStepModel(key="old", description="Potentially applied old action"),
        CandidateStepModel(key="new", description="Independent new action"),
    ])
    first, second = Planner(db).plan_steps(plan.id)
    old = Task(id="interrupted-task", original_request="old action",
               state="interrupted", version=1, objective_id=goal.id)
    db.add(old)
    first.status = "running"
    first.linked_task_id = old.id
    first.evidence_task_id = old.id
    db.commit()

    async def never_run(*_):
        pytest.fail("Recovery review must prevent any new worker dispatch")

    orch = TaskOrchestrator(state_store=TaskRunner(db), planner=Planner(db))
    result = await orch.execute_session(goal.id, run_task_fn=never_run)
    assert result == {"executed": [], "stopped_reason": "unresolved_step_review_required"}
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == ["running", "ready"]
    assert second.status == "ready"
    assert db.query(Task).filter_by(objective_id=goal.id).count() == 1


@pytest.mark.asyncio
async def test_verified_three_step_goal_continues_across_two_fresh_database_sessions(
    file_db, tmp_path, monkeypatch,
):
    """Session #2 resumes at the next step; it cannot redo the verified first."""
    monkeypatch.setattr(settings, "max_steps_per_goal_execution", 1)
    db = file_db
    goal, plan = setup_goal(db, "Goal spanning independent DB sessions")
    runner1 = TaskRunner(db)
    orch1 = TaskOrchestrator(state_store=runner1, planner=Planner(db))
    first_worker, first_visited = create_scripted_worker(db, runner1, tmp_path)
    first = await orch1.execute_session(goal.id, run_task_fn=first_worker)
    assert first["stopped_reason"] == "step_budget_reached"
    assert first_visited == ["inspect"]
    assert [item["outcome"] for item in first["executed"]] == ["completed"]
    assert [s.status for s in Planner(db).plan_steps(plan.id)] == [
        "completed", "blocked", "blocked",
    ]
    # The selector will virtually unlock the next step only when it is
    # actually selected; finishing a task does not prematurely mutate it.

    # The second session sees independently committed data in file-backed
    # SQLite, not the first session's identity map or in-memory state.
    Session = sessionmaker(bind=db.bind, expire_on_commit=False)
    fresh = Session()
    try:
        assert (
            fresh.connection().connection.dbapi_connection
            is not db.connection().connection.dbapi_connection
        )
        monkeypatch.setattr(settings, "max_steps_per_goal_execution", 2)
        runner2 = TaskRunner(fresh)
        orch2 = TaskOrchestrator(state_store=runner2, planner=Planner(fresh))
        second_worker, second_visited = create_scripted_worker(
            fresh, runner2, tmp_path,
        )
        second = await orch2.execute_session(goal.id, run_task_fn=second_worker)
        assert second_visited == ["summarize", "verify"]
        assert [item["outcome"] for item in second["executed"]] == [
            "completed", "completed",
        ]
        assert GoalManager(fresh).get_goal(goal.id).status == "completed"
        steps = Planner(fresh).plan_steps(plan.id)
        assert [step.status for step in steps] == ["completed"] * 3
        assert len({step.evidence_task_id for step in steps}) == 3
        events = DbEventLedger(fresh).event_history(f"goal:{goal.id}")
        assert [e["event_type"] for e in events].count("plan_step.started") == 3
        assert [e["event_type"] for e in events].count("plan_step.completed") == 3
        assert json.loads((tmp_path / "summary.json").read_text(encoding="utf-8")) == {
            "total": 10, "count": 3,
        }
        assert (tmp_path / "verified.txt").read_text(encoding="utf-8") == "PASS"
    finally:
        fresh.close()
