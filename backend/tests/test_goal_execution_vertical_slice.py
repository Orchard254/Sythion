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
    db, tmp_path, monkeypatch,
):
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
