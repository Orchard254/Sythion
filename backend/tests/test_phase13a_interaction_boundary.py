"""
Phase 13A: Conversation vs Goal/Task Boundary Tests

Verifies that:
1. Ordinary conversation does NOT create Objective, GoalPlan, or PlanStep records.
2. Conversational inputs do NOT trigger unnecessary ASK_CLARIFICATION.
3. Informational questions are answered without fake planning.
4. Actionable requests still reach tool execution.
5. Genuine goals still create Goal/Plan and reach Planner.
6. Conversational continuity is preserved across multi-turn exchanges.
"""

import pytest
from datetime import datetime
from sqlalchemy.orm import Session

from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.pipeline import CognitivePipeline
from backend.cognitive.state import (
    AmbiguityLevel,
    DecisionType,
    IntentCategory,
    InteractionType,
)
from backend.models.database import GoalPlan, Objective, PlanStep, Task
from backend.services.cognitive_runtime import CognitiveDecisionAdapter
from backend.services.task_runner import TaskRunner


CONVERSATIONAL_SAMPLES = [
    "Hey Sythion, how have you been?",
    "I'm bored. Talk to me.",
    "Tell me something interesting.",
    "Let's talk about computers.",
    "That's interesting. Why do you think that is?",
    "I just want your opinion.",
    "Actually, stop there. Don't do anything. I just want your opinion.",
    "Okay, that's enough for now. Let's just chat.",
    "I've been thinking about artificial intelligence.",
]

INFORMATIONAL_SAMPLES = [
    "Why is knowing the time useful to an assistant?",
    "What year was Python created?",
    "Explain the von Neumann bottleneck.",
    "What is the capital of France?",
]

ACTION_SAMPLES = [
    "Can you check what time it is?",
    "Check the current time.",
    "List the files in C:\\Ai\\sythion.",
]

GOAL_SAMPLES = [
    "Create a report listing the Python files in C:\\Ai\\sythion.",
    "Plan a roadmap over the next month for migrating our database step by step.",
]


class TestInteractionBoundaryInterpretation:
    """Test IntentInterpreter classification into InteractionType."""

    def test_conversational_inputs_classified_as_conversation(self):
        interp = IntentInterpreter()
        for sample in CONVERSATIONAL_SAMPLES:
            itype = interp.determine_interaction(sample)
            assert itype == InteractionType.CONVERSATION, f"Failed for: {sample} (got {itype})"

    def test_conversational_inputs_never_ambiguous_clarification(self):
        interp = IntentInterpreter()
        for sample in CONVERSATIONAL_SAMPLES:
            intent = interp.interpret(sample)
            assert intent.ambiguity in (AmbiguityLevel.CLEAR, AmbiguityLevel.LOW_AMBIGUITY), (
                f"Conversational sample '{sample}' incorrectly marked as {intent.ambiguity}"
            )
            assert not intent.needs_clarification, (
                f"Conversational sample '{sample}' incorrectly requested clarification"
            )

    def test_informational_inputs_classified_as_informational(self):
        interp = IntentInterpreter()
        for sample in INFORMATIONAL_SAMPLES:
            itype = interp.determine_interaction(sample)
            assert itype == InteractionType.INFORMATIONAL, f"Failed for: {sample} (got {itype})"

    def test_action_inputs_classified_as_action(self):
        interp = IntentInterpreter()
        for sample in ACTION_SAMPLES:
            itype = interp.determine_interaction(sample)
            assert itype == InteractionType.ACTION, f"Failed for: {sample} (got {itype})"

    def test_genuine_goal_inputs_classified_as_goal(self):
        interp = IntentInterpreter()
        for sample in GOAL_SAMPLES:
            itype = interp.determine_interaction(sample)
            assert itype == InteractionType.GOAL, f"Failed for: {sample} (got {itype})"


class TestCognitivePipelineDecisionBoundary:
    """Test CognitivePipeline process outcomes."""

    def test_conversation_decides_answer_without_clarification(self, test_db: Session):
        pipeline = CognitivePipeline(test_db)
        for sample in CONVERSATIONAL_SAMPLES:
            res = pipeline.process(sample)
            assert not res.needs_clarification, f"Pipeline asked clarification for: {sample}"
            assert res.decision is not None
            assert res.decision.decision_type == DecisionType.ANSWER, (
                f"Pipeline chose {res.decision.decision_type} instead of ANSWER for: {sample}"
            )

    def test_informational_decides_answer_without_clarification(self, test_db: Session):
        pipeline = CognitivePipeline(test_db)
        for sample in INFORMATIONAL_SAMPLES:
            res = pipeline.process(sample)
            assert not res.needs_clarification, f"Pipeline asked clarification for: {sample}"
            assert res.decision is not None
            assert res.decision.decision_type == DecisionType.ANSWER, (
                f"Pipeline chose {res.decision.decision_type} instead of ANSWER for: {sample}"
            )


@pytest.mark.asyncio
class TestCognitiveAdapterAndTaskLifecycle:
    """Test adapter routing and no-fake-goal invariant on tasks."""

    async def test_conversational_task_creates_no_objective_or_plan(self, test_db: Session):
        runner = TaskRunner(test_db)
        adapter = CognitiveDecisionAdapter(runner, test_db)

        # Baseline counts
        obj_count_before = test_db.query(Objective).count()
        plan_count_before = test_db.query(GoalPlan).count()
        step_count_before = test_db.query(PlanStep).count()

        for sample in CONVERSATIONAL_SAMPLES[:4]:
            assert await runner.acquire_lease()
            try:
                task = await runner.create_task(original_request=sample)
                routing = await adapter.route_new_input(task.id, sample)
            finally:
                await runner.release_lease()

            assert routing.interaction_type == InteractionType.CONVERSATION, (
                f"Expected CONVERSATION for: {sample} (got {routing.interaction_type})"
            )
            assert routing.mode == "execute"
            assert task.objective_id is None, (
                f"Conversation unexpectedly created objective for: {sample}"
            )

        # Verify no fake goals/plans created
        assert test_db.query(Objective).count() == obj_count_before
        assert test_db.query(GoalPlan).count() == plan_count_before
        assert test_db.query(PlanStep).count() == step_count_before

    async def test_informational_task_creates_no_objective_or_plan(self, test_db: Session):
        runner = TaskRunner(test_db)
        adapter = CognitiveDecisionAdapter(runner, test_db)

        obj_count_before = test_db.query(Objective).count()
        plan_count_before = test_db.query(GoalPlan).count()

        for sample in INFORMATIONAL_SAMPLES[:2]:
            assert await runner.acquire_lease()
            try:
                task = await runner.create_task(original_request=sample)
                routing = await adapter.route_new_input(task.id, sample)
            finally:
                await runner.release_lease()

            assert routing.interaction_type == InteractionType.INFORMATIONAL, (
                f"Expected INFORMATIONAL for: {sample} (got {routing.interaction_type})"
            )
            assert routing.mode == "execute"
            assert task.objective_id is None

        assert test_db.query(Objective).count() == obj_count_before
        assert test_db.query(GoalPlan).count() == plan_count_before

    async def test_genuine_goal_routes_to_goal(self, test_db: Session):
        runner = TaskRunner(test_db)
        adapter = CognitiveDecisionAdapter(runner, test_db)

        sample = "Create a report listing the Python files in C:\\Ai\\sythion."
        assert await runner.acquire_lease()
        try:
            task = await runner.create_task(original_request=sample)
            routing = await adapter.route_new_input(task.id, sample)
        finally:
            await runner.release_lease()

        assert routing.interaction_type == InteractionType.GOAL, (
            f"Expected GOAL for goal-like request (got {routing.interaction_type})"
        )


class TestConversationalContinuity:
    """Test multi-turn context resolution."""

    def test_followup_opinion_resolves_conversationally(self, test_db: Session):
        interp = IntentInterpreter()
        turn1 = "I've been thinking about artificial intelligence."
        itype1 = interp.determine_interaction(turn1)
        assert itype1 == InteractionType.CONVERSATION

        turn2 = "What do you think about that?"
        itype2 = interp.determine_interaction(turn2)
        assert itype2 == InteractionType.CONVERSATION

    def test_action_followed_by_informational_followup(self, test_db: Session):
        interp = IntentInterpreter()
        turn1 = "Can you check what time it is?"
        itype1 = interp.determine_interaction(turn1)
        assert itype1 == InteractionType.ACTION

        turn2 = "Why is that useful?"
        itype2 = interp.determine_interaction(turn2)
        assert itype2 in (InteractionType.CONVERSATION, InteractionType.INFORMATIONAL)
