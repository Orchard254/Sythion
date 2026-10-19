"""
Phase 2 goal & planning architecture tests.

Covers: goal lifecycle, plan validation (deps/cycles/duplicates), scheduling
readiness & priority, execution integration through the kernel, replanning
versions, persistence across sessions, and the security boundaries:

- the Planner cannot execute tools,
- LLM output cannot directly mark completion,
- blocked steps cannot be executed,
- plan validation happens before persistence,
- task state stays owned by TaskStateStore,
- the event ledger remains authoritative.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, GoalPlan, PlanStep, TaskEvent
from backend.runtime.events import DbEventLedger
from backend.services.goal_manager import (
    GoalManager,
    InvalidGoalTransitionError,
)
from backend.services.planner import (
    CandidateStepModel,
    PlanValidationError,
    Planner,
    parse_candidate_plan,
    validate_dependency_graph,
)
from backend.services.task_orchestrator import TaskOrchestrator


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
def runner(db_session):
    from backend.api.schemas import TaskState  # noqa: F401
    from backend.services.task_runner import TaskRunner

    r = TaskRunner(db_session)
    import asyncio
    assert asyncio.run(r.acquire_lease())
    return r


def _candidates(*specs):
    return [
        CandidateStepModel(key=k, description=(d if len(d) >= 3 else f"step {d}"))
        for k, d in specs
    ]


# ===========================================================================
# Goal lifecycle
# ===========================================================================


class TestGoalLifecycle:
    def test_create_and_read(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("Find remote work", purpose="income", priority=2)
        fetched = manager.get_goal(goal.id)
        assert fetched.status == "active"
        assert fetched.priority == 2

    def test_valid_lifecycle(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("G")
        manager.pause_goal(goal.id)
        manager.resume_goal(goal.id)
        assert manager.get_goal(goal.id).status == "active"

    def test_invalid_transition_completed_to_active(self, db_session):
        from backend.models.database import Objective

        manager = GoalManager(db_session)
        goal = manager.create_goal("G2")
        # Deterministic completion eligibility: no steps, criteria verified.
        db_session.query(Objective).filter_by(id=goal.id).update(
            {"criteria_verified": True}
        )
        db_session.commit()
        manager.complete_goal(goal.id)
        with pytest.raises(InvalidGoalTransitionError):
            manager.resume_goal(goal.id)
        with pytest.raises(InvalidGoalTransitionError):
            manager.pause_goal(goal.id)

    def test_abandon_and_fail_are_terminal(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("G")
        manager.abandon_goal(goal.id)
        with pytest.raises(InvalidGoalTransitionError):
            manager.resume_goal(goal.id)

        goal2 = manager.create_goal("G2")
        manager.fail_goal(goal2.id, reason="unreachable")
        with pytest.raises(InvalidGoalTransitionError):
            manager.transition_goal(goal2.id, "active")

    def test_unknown_status_rejected(self, db_session):
        manager = GoalManager(db_session)
        goal = manager.create_goal("G")
        with pytest.raises(ValueError):
            manager.transition_goal(goal.id, "bogus")

    def test_complete_requires_no_open_steps(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("G")
        planner.create_plan(goal.id, _candidates(("a", "step a"), ("b", "step b")))
        with pytest.raises(ValueError, match="still open"):
            manager.complete_goal(goal.id)

    def test_complete_requires_verified_criteria(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("G", completion_criteria="all files backed up")
        plan = planner.create_plan(goal.id, _candidates(("a", "only step")))
        step = planner.plan_steps(plan.id)[0]
        step.status = "completed"
        db_session.commit()
        with pytest.raises(ValueError, match="criteria"):
            manager.complete_goal(goal.id)

    def test_progress_derived_from_step_state(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("G")
        plan = planner.create_plan(
            goal.id,
            [
                CandidateStepModel(key="a", description="step a"),
                CandidateStepModel(key="b", description="step b"),
                CandidateStepModel(key="c", description="step c"),
                CandidateStepModel(key="gone", description="cancelled"),
            ],
        )
        for s in planner.plan_steps(plan.id):
            if s.step_key in ("a", "b"):
                s.status = "completed"
            elif s.step_key == "gone":
                s.status = "cancelled"
        db_session.commit()
        progress = manager.progress(goal.id)
        assert progress["completed_steps"] == 2
        assert progress["total_required_steps"] == 3  # cancelled excluded


# ===========================================================================
# Plan validation
# ===========================================================================


class TestPlanValidation:
    def test_schema_malformed_model_output_rejected(self):
        raw_model_output = "I think we should probably search first"
        with pytest.raises(PlanValidationError):
            parse_candidate_plan(raw_model_output)

    def test_json_without_steps_key_rejected(self):
        with pytest.raises(PlanValidationError):
            parse_candidate_plan({"plan": "do stuff"})

    def test_bad_key_format_rejected(self):
        with pytest.raises(PlanValidationError):
            parse_candidate_plan([{"key": "Bad Key!", "description": "x" * 10}])

    def test_missing_dependency_rejected(self, db_session):
        planner = Planner(db_session)
        with pytest.raises(PlanValidationError, match="unknown step"):
            planner.create_plan(
                _mk_goal(db_session).id,
                [CandidateStepModel(key="a", description="step a", depends_on=["ghost"])],
            )

    def test_self_dependency_rejected(self):
        steps = [CandidateStepModel(key="a", description="step a", depends_on=["a"])]
        with pytest.raises(PlanValidationError, match="itself"):
            validate_dependency_graph(steps)

    def test_duplicate_keys_rejected(self):
        steps = [
            CandidateStepModel(key="a", description="one"),
            CandidateStepModel(key="a", description="two"),
        ]
        with pytest.raises(PlanValidationError, match="duplicate"):
            validate_dependency_graph(steps)

    @pytest.mark.parametrize("cycle", [
        ["a->b", "b->a"],
        ["a->b", "b->c", "c->a"],
    ])
    def test_dependency_cycles_rejected(self, cycle):
        specs = []
        for item in cycle:
            key, dep = item.split("->")
            specs.append(CandidateStepModel(key=key, description=f"step {key}", depends_on=[dep]))
        with pytest.raises(PlanValidationError, match="cycle"):
            validate_dependency_graph(specs)

    def test_validation_before_persistence(self, db_session):
        """A rejected plan leaves ZERO rows behind."""
        goal = _mk_goal(db_session)
        before_plans = db_session.query(GoalPlan).count()
        before_steps = db_session.query(PlanStep).count()
        planner = Planner(db_session)
        with pytest.raises(PlanValidationError):
            planner.create_plan(
                goal.id,
                [
                    CandidateStepModel(key="a", description="an okay step"),
                    CandidateStepModel(key="b", description="a dangling step",
                                       depends_on=["missing"]),
                ],
            )
        assert db_session.query(GoalPlan).count() == before_plans
        assert db_session.query(PlanStep).count() == before_steps

    def test_linear_branching_converging_plans_valid(self, db_session):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="root", description="root"),
            CandidateStepModel(key="left", description="left", depends_on=["root"]),
            CandidateStepModel(key="right", description="right", depends_on=["root"]),
            CandidateStepModel(key="join", description="join",
                               depends_on=["left", "right"]),
        ])
        statuses = {s.step_key: s.status for s in planner.plan_steps(plan.id)}
        assert statuses["root"] == "ready"
        assert statuses["left"] == "blocked"
        assert statuses["right"] == "blocked"
        assert statuses["join"] == "blocked"


def _mk_goal(db_session):
    return GoalManager(db_session).create_goal("Validation goal")


# ===========================================================================
# Scheduling readiness & priority
# ===========================================================================


class TestScheduling:
    def test_blocked_until_dependency_completes(self, db_session):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="a", description="first"),
            CandidateStepModel(key="b", description="second", depends_on=["a"]),
        ])
        a, b = planner.plan_steps(plan.id)
        assert planner.select_next_step(goal.id).id == a.id
        # B cannot be selected while A incomplete.
        a.status = "running"
        db_session.commit()
        assert planner.select_next_step(goal.id) is None or \
            planner.select_next_step(goal.id).id != b.id
        # A completes â†’ B unlocks.
        a.status = "completed"
        db_session.commit()
        assert planner.select_next_step(goal.id).id == b.id

    def test_priority_ordering(self, db_session):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="low", description="low", priority=5),
            CandidateStepModel(key="critical", description="crit", priority=1),
            CandidateStepModel(key="mid", description="mid", priority=3),
        ])
        steps_by_key = {s.step_key: s for s in planner.plan_steps(plan.id)}
        order = []
        picked = planner.select_next_step(goal.id)
        while picked is not None:
            order.append(picked.step_key)
            steps_by_key[picked.step_key].status = "completed"
            db_session.commit()
            picked = planner.select_next_step(goal.id)
        assert order == ["critical", "mid", "low"]

    def test_deterministic_tie_break_by_order(self, db_session):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="s1", description="step s1"),
            CandidateStepModel(key="s2", description="step s2"),
            CandidateStepModel(key="s3", description="step s3"),
        ])
        # Same priority â†’ step_order decides, deterministically.
        picks = {planner.select_next_step(goal.id).step_key}
        assert picks == {"s1"}
        again = planner.select_next_step(goal.id).step_key
        assert again == "s1"

    def test_converging_dependencies_require_all(self, db_session):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="l", description="step l"),
            CandidateStepModel(key="r", description="step r"),
            CandidateStepModel(key="join", description="step j", depends_on=["l", "r"]),
        ])
        l, r, join = planner.plan_steps(plan.id)
        l.status = "completed"
        db_session.commit()
        planner.refresh_readiness(plan.id)
        assert join.status == "blocked"
        r.status = "completed"
        db_session.commit()
        planner.refresh_readiness(plan.id)
        assert join.status == "ready"


# ===========================================================================
# Execution integration through the kernel
# ===========================================================================


class TestExecutionIntegration:
    def _goal_with_one_step(self, db_session, runner):
        from backend.services.execution_components import CompletionVerifier

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=runner, planner=planner)
        goal = manager.create_goal("Execution goal")
        plan = planner.create_plan(goal.id, _candidates(("work", "Do real work")))
        verifier_error_fn = self._verifier(db_session)
        return goal, plan, orchestrator, verifier_error_fn

    @staticmethod
    def _verifier(db_session):
        from backend.runtime.events import DbEventLedger
        from backend.services.execution_components import CompletionVerifier

        ledger = DbEventLedger(db_session)

        def error_fn(task_id):
            return CompletionVerifier(db_session, ledger.event_history) \
                .precondition_error(task_id)

        return error_fn

    @pytest.mark.asyncio
    async def test_step_creates_task_through_state_store(self, db_session, runner):
        goal, plan, orchestrator, _ = self._goal_with_one_step(db_session, runner)
        started = await orchestrator.start_next_step(goal.id)
        assert started is not None
        task = started["task"]
        step = started["step"]
        # Task created via TaskStateStore; step RUNNING and bound.
        assert task.state == "created"
        assert task.objective_id == goal.id
        assert step.status == "running"
        assert step.linked_task_id == task.id
        assert step.attempt_count == 1
        # Idempotency key binds attempts.
        assert task.idempotency_key.startswith(f"goalstep:{step.id}:")

    @pytest.mark.asyncio
    async def test_blocked_steps_cannot_execute(self, db_session, runner):
        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=runner, planner=planner)
        planner.create_plan(goal.id, [
            CandidateStepModel(key="a", description="step a"),
            CandidateStepModel(key="b", description="step b", depends_on=["a"]),
        ])
        started = await orchestrator.start_next_step(goal.id)
        assert started["step"].step_key == "a"
        # 'b' is BLOCKED â€” selecting again yields nothing while 'a' runs.
        assert await orchestrator.start_next_step(goal.id) is None

    @pytest.mark.asyncio
    async def test_successful_verified_execution_completes_step(self, db_session, runner):
        goal, plan, orchestrator, error_fn = self._goal_with_one_step(db_session, runner)
        started = await orchestrator.start_next_step(goal.id)
        task_id = started["task"].id
        # Runtime reaches terminal COMPLETED with clean evidence.
        from backend.api.schemas import TaskState as _TS
        await runner.transition_state(task_id, _TS.RUNNING, "task.execution_started", {})
        await runner.transition_state(task_id, _TS.COMPLETED, "task.completed",
                                      {"summary": "done"}, final_result="done")

        result = await orchestrator.apply_task_outcome(task_id, completion_precondition_error_fn=error_fn)
        assert result["outcome"] == "completed"
        step = result["step"]
        assert step.status == "completed"
        assert step.evidence_task_id == task_id
        progress = GoalManager(db_session).progress(goal.id)
        assert progress["fraction"] == 1.0

    @pytest.mark.asyncio
    async def test_failed_execution_does_not_complete_step(self, db_session, runner):
        from backend.api.schemas import TaskState

        goal, plan, orchestrator, error_fn = self._goal_with_one_step(db_session, runner)
        started = await orchestrator.start_next_step(goal.id)
        task_id = started["task"].id
        await runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {})
        await runner.transition_state(task_id, TaskState.FAILED, "task.failed",
                                {"code": "TOOL_FAILED", "message": "boom"},
                                terminal_reason="boom")
        result = await orchestrator.apply_task_outcome(task_id, completion_precondition_error_fn=error_fn)
        assert result["outcome"] == "failed"
        assert result["step"].status == "failed"
        assert result["step"].failure_reason

    @pytest.mark.asyncio
    async def test_unverified_mutating_failure_blocks_completion(self, db_session, runner):
        """A completed task whose last mutating action failed verification
        can NOT complete its step."""
        from backend.api.schemas import TaskState

        goal, plan, orchestrator, error_fn = self._goal_with_one_step(db_session, runner)
        started = await orchestrator.start_next_step(goal.id)
        task_id = started["task"].id
        await runner.record_event(task_id, "tool.executed", {
            "tool_name": "file_write", "success": False,
            "mutating": True, "verification": "failed",
        })
        await runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {})
        await runner.transition_state(task_id, TaskState.COMPLETED, "task.completed",
                                {"summary": "done anyway"})
        result = await orchestrator.apply_task_outcome(task_id, completion_precondition_error_fn=error_fn)
        assert result["outcome"] == "failed"


# ===========================================================================
# Replanning
# ===========================================================================


class TestReplanning:
    async def _failed_scenario(self, db_session, runner):
        from backend.api.schemas import TaskState

        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=runner, planner=planner)
        goal = manager.create_goal("Replan goal")
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="a", description="step a"),
            CandidateStepModel(key="b", description="step b", depends_on=["a"]),
        ])
        started = await orchestrator.start_next_step(goal.id)
        task_id = started["task"].id
        await runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {})
        await runner.transition_state(task_id, TaskState.FAILED, "task.failed",
                                {"code": "X", "message": "no disk space"},
                                terminal_reason="no disk space")
        await orchestrator.apply_task_outcome(
            task_id,
            completion_precondition_error_fn=TestExecutionIntegration._verifier(db_session),
        )
        return goal, planner, orchestrator, started["step"]

    @pytest.mark.asyncio
    async def test_failure_context_is_structured(self, db_session, runner):
        goal, planner, _, step = await self._failed_scenario(db_session, runner)
        context = planner.build_failure_context(step.id)
        assert context["failed_step"]["key"] == "a"
        assert context["failed_step"]["attempt_count"] == 1
        assert "no disk space" in context["failed_step"]["failure_reason"]
        assert any(s["key"] == "b" for s in context["remaining_plan"])

    @pytest.mark.asyncio
    async def test_replan_creates_new_version_keeps_history(self, db_session, runner):
        goal, planner, _, step = await self._failed_scenario(db_session, runner)
        old_plan = planner.active_plan(goal.id)
        new_plan = planner.replan(goal.id, reason="step a failed")
        assert new_plan.version == old_plan.version + 1
        db_session.refresh(old_plan)
        assert old_plan.status == "superseded"  # auditable, not erased
        versions = db_session.query(GoalPlan).filter_by(goal_id=goal.id).all()
        assert sorted(p.version for p in versions) == [1, 2]

    @pytest.mark.asyncio
    async def test_invalid_revised_plan_rejected(self, db_session, runner):
        goal, planner, _, step = await self._failed_scenario(db_session, runner)
        before = db_session.query(GoalPlan).count()
        with pytest.raises(PlanValidationError):
            planner.replan(goal.id, reason="bad", candidates=[
                CandidateStepModel(key="x", description="step x", depends_on=["y"]),
                CandidateStepModel(key="y", description="step y", depends_on=["x"]),
            ])
        assert db_session.query(GoalPlan).count() == before

    @pytest.mark.asyncio
    async def test_carried_over_open_steps_when_no_candidates(self, db_session, runner):
        goal, planner, _, step = await self._failed_scenario(db_session, runner)
        new_plan = planner.replan(goal.id, reason="retry")
        keys = [s.step_key for s in planner.plan_steps(new_plan.id)]
        assert keys == ["a", "b"]  # both still open after failure

    def test_model_proposal_via_model_client_boundary(self, db_session):
        """The Planner uses ModelClient only; malformed output is rejected."""

        class _GoodClient:
            async def decide(self, messages, tools, max_tokens=4000):
                return {"message": {"content": json.dumps({"steps": [
                    {"key": "research", "description": "research options"},
                    {"key": "apply", "description": "apply", "depends_on": ["research"]},
                ]})}}

            def provider_name(self):
                return "fake"

        class _BadClient:
            async def decide(self, messages, tools, max_tokens=4000):
                return {"message": {"content": "Just wing it."}}

        goal = _mk_goal(db_session)
        planner = Planner(db_session)
        candidates = planner.request_model_plan(goal, model_client=_GoodClient())
        assert [c.key for c in candidates] == ["research", "apply"]

        with pytest.raises(PlanValidationError):
            planner.request_model_plan(goal, model_client=_BadClient())


# ===========================================================================
# Persistence / restart safety + boundaries
# ===========================================================================


class TestPersistenceAndBoundaries:
    def test_goal_plan_step_survive_new_session(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Durable")
        plan = planner.create_plan(goal.id, _candidates(("a", "a"), ("b", "b")))
        engine = db_session.bind
        fresh = sessionmaker(bind=engine)()

        refetched = GoalManager(fresh).get_goal(goal.id)
        assert refetched is not None
        assert refetched.title == "Durable"
        fresh_planner = Planner(fresh)
        assert len(fresh_planner.plan_steps(plan.id)) == 2
        fresh.close()

    def test_ledger_records_goal_and_plan_events(self, db_session):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        goal = manager.create_goal("Audited")
        plan = planner.create_plan(goal.id, [
            CandidateStepModel(key="first", description="do first"),
            CandidateStepModel(key="second", description="do second",
                               depends_on=["first"]),
        ])
        events = DbEventLedger(db_session).event_history(f"goal:{goal.id}")
        types = [e["event_type"] for e in events]
        assert "goal.created" in types
        assert "plan.created" in types
        # Completing the first step unlocks the second via a ledger event.
        first = planner.plan_steps(plan.id)[0]
        first.status = "completed"
        db_session.commit()
        planner.refresh_readiness(plan.id)
        types = [
            e["event_type"]
            for e in DbEventLedger(db_session).event_history(f"goal:{goal.id}")
        ]
        assert "plan_step.ready" in types

    def test_planner_cannot_execute_tools(self):
        """Structural boundary: the planning layer has no tool-execution
        surface whatsoever."""
        import inspect

        import backend.services.goal_manager as gm
        import backend.services.planner as pl
        import backend.services.task_orchestrator as to

        for module in (gm, pl, to):
            src = inspect.getsource(module)
            assert "execute_tool" not in src, f"{module.__name__} touches tool execution"
            assert "get_adapter()" not in src, f"{module.__name__} bypasses ModelClient"

    def test_llm_claim_cannot_mark_step_completed(self, db_session, runner):
        """Even with the model asserting success, without kernel evidence the
        step stays un-completed."""
        goal, plan, orchestrator, error_fn = self._simple(db_session, runner)
        step = planner_step(db_session, plan)
        # No task was ever run; simulate a bare model claim by calling the
        # evidence gate directly.
        with pytest.raises(ValueError, match="nonexistent-task"):
            orchestrator.planner.complete_step_from_evidence(step.id, "nonexistent-task")
        assert planner_step(db_session, plan).status != "completed"

    def _simple(self, db_session, runner):
        manager = GoalManager(db_session)
        planner = Planner(db_session)
        orchestrator = TaskOrchestrator(state_store=runner, planner=planner)
        goal = manager.create_goal("Simple")
        plan = planner.create_plan(goal.id, _candidates(("only", "only step")))
        return goal, plan, orchestrator, None

    def test_task_state_owned_by_state_store(self, db_session, runner):
        """The orchestrator creates tasks only through create_task."""
        import inspect

        src = inspect.getsource(TaskOrchestrator)
        assert ".create_task(" in src
        assert "db.add(Task" not in src
        assert "INSERT INTO tasks" not in src.lower()


def planner_step(db_session, plan):
    from backend.services.planner import Planner

    return Planner(db_session).plan_steps(plan.id)[0]








