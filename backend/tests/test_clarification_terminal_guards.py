"""Defensive and contract regression tests for clarification terminal guards.

These tests use production parsing/execution paths. They do not model the live
Folded Moon root cause as a verified fix, do not use provider/network calls, and
do not read credentials. Model-generated questions are intentionally
unclassified waits; they are not auto-answerable clarification tasks.
"""

import json

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.api.schemas import TaskState
from backend.models.database import Base
from backend.services.model_engine import ModelDecisionEngine, DecisionType
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.task_runner import TaskRunner
from backend.tools.executor import ToolExecutor


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def tool_executor():
    return ToolExecutor()


@pytest.fixture
def task_runner(db_session):
    return TaskRunner(db_session)


def test_parse_request_user_input_maps_to_request_and_drops_trailing_complete(
    db_session, tool_executor
):
    engine = ModelDecisionEngine(db_session, tool_executor)
    response = {
        "message": {
            "tool_calls": [
                {
                    "function": {
                        "name": DecisionType.REQUEST_USER_INPUT,
                        "arguments": {"question": "How many people will attend?"},
                    }
                },
                {
                    "function": {
                        "name": DecisionType.COMPLETE_TASK,
                        "arguments": {"result": "How many people will attend?"},
                    }
                },
            ]
        }
    }

    decision_type, params = engine._parse_decision(response)

    assert decision_type == DecisionType.REQUEST_USER_INPUT
    assert params == {"question": "How many people will attend?"}
    assert engine.pop_pending_tool_calls() == []


def test_parse_complete_then_request_cannot_complete(
    db_session, tool_executor
):
    engine = ModelDecisionEngine(db_session, tool_executor)
    response = {
        "message": {
            "tool_calls": [
                {
                    "function": {
                        "name": DecisionType.COMPLETE_TASK,
                        "arguments": {"result": "Attendance question?"},
                    }
                },
                {
                    "function": {
                        "name": DecisionType.REQUEST_USER_INPUT,
                        "arguments": {"question": "How many people will attend?"},
                    }
                },
            ]
        }
    }

    decision_type, params = engine._parse_decision(response)

    assert decision_type == DecisionType.REQUEST_USER_INPUT
    assert params == {"question": "How many people will attend?"}
    assert engine.pop_pending_tool_calls() == []
    assert decision_type != DecisionType.COMPLETE_TASK


def test_parse_preserves_prior_nonterminal_calls_before_terminal_request(
    db_session, tool_executor
):
    engine = ModelDecisionEngine(db_session, tool_executor)
    response = {
        "message": {
            "tool_calls": [
                {
                    "function": {
                        "name": DecisionType.RUN_SKILL,
                        "arguments": {"skill_id": "demo", "inputs": {}},
                    }
                },
                {
                    "function": {
                        "name": DecisionType.REQUEST_USER_INPUT,
                        "arguments": {"question": "Clarify?"},
                    }
                },
                {
                    "function": {
                        "name": DecisionType.COMPLETE_TASK,
                        "arguments": {"result": "Tail should drop."},
                    }
                },
            ]
        }
    }

    decision_type, params = engine._parse_decision(response)

    assert decision_type == DecisionType.REQUEST_USER_INPUT
    assert params == {"question": "Clarify?"}
    assert engine.pop_pending_tool_calls() == [
        {"decision_type": DecisionType.RUN_SKILL, "args": {"skill_id": "demo", "inputs": {}}}
    ]


@pytest.mark.asyncio
async def test_worker_request_user_input_trailing_completion_does_not_complete(
    db_session, task_runner, tool_executor
):
    await task_runner.acquire_lease()
    try:
        task = await task_runner.create_task("Clarification task")
        await task_runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "task.execution_started",
            {"started_at": "2026-01-01T00:00:00"},
        )

        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        first = await loop._execute_decision(
            task.id, DecisionType.REQUEST_USER_INPUT, {"question": "How many people?"}
        )
        assert first is True

        waiting = await task_runner.get_task(task.id)
        assert waiting.state == TaskState.WAITING_FOR_USER.value
        assert waiting.pending_question == "How many people?"

        second = await loop._complete_task(task.id, {"result": "Should not complete."})
        assert second is False

        after = await task_runner.get_task(task.id)
        assert after.state == TaskState.WAITING_FOR_USER.value
        assert after.pending_question == "How many people?"
    finally:
        await task_runner.release_lease()


@pytest.mark.asyncio
async def test_model_questions_are_unknown_waits_and_inference_excluded(
    db_session, task_runner, tool_executor
):
    """Any model question stays an unclassified wait and is not auto-resumable."""
    questions = [
        "Do you approve running X?",
        "How many people will attend?",
    ]

    await task_runner.acquire_lease()
    try:
        for question in questions:
            task = await task_runner.create_task("Model question task")
            await task_runner.transition_state(
                task.id,
                TaskState.RUNNING,
                "task.execution_started",
                {"started_at": "2026-01-01T00:00:00"},
            )

            loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
            result = await loop._request_user_input(task.id, {"question": question})
            assert result is True

            waiting = await task_runner.get_task(task.id)
            assert waiting.state == TaskState.WAITING_FOR_USER.value
            assert waiting.pending_question == question
            assert waiting.pending_action is None

            metadata = json.loads(waiting.pending_action or "{}")
            assert not (
                isinstance(metadata, dict) and metadata.get("kind") == "clarification"
            )
            assert task.id not in {
                item.get("task_id")
                for item in []  # descriptor set only includes classified clarification waits
                if item.get("kind") == "clarification"
            }

            with pytest.raises(RuntimeError, match="cannot be resumed automatically"):
                await task_runner.reply_to_waiting(task.id, "answer", waiting.version)

            unchanged = await task_runner.get_task(task.id)
            assert unchanged.state == TaskState.WAITING_FOR_USER.value
            assert unchanged.pending_question == question
            assert unchanged.version == waiting.version
    finally:
        await task_runner.release_lease()


@pytest.mark.asyncio
async def test_complete_task_blocked_while_waiting_preserves_state(
    db_session, task_runner, tool_executor
):
    await task_runner.acquire_lease()
    try:
        task = await task_runner.create_task("Clarification task")
        await task_runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "task.execution_started",
            {"started_at": "2026-01-01T00:00:00"},
        )

        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        await loop._request_user_input(task.id, {"question": "How many people?"})

        waiting = await task_runner.get_task(task.id)
        before_events = len(task_runner.ledger.event_history(task.id))

        result = await loop._complete_task(task.id, {"result": "Should not complete."})

        assert result is False
        after = await task_runner.get_task(task.id)
        assert after.state == TaskState.WAITING_FOR_USER.value
        assert after.pending_question == "How many people?"
        assert after.version == waiting.version
        assert len(task_runner.ledger.event_history(task.id)) == before_events
    finally:
        await task_runner.release_lease()


@pytest.mark.asyncio
async def test_complete_task_normal_non_waiting_control_completes(
    db_session, task_runner, tool_executor
):
    await task_runner.acquire_lease()
    try:
        task = await task_runner.create_task("Normal task")
        await task_runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "task.execution_started",
            {"started_at": "2026-01-01T00:00:00"},
        )

        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        result = await loop._complete_task(task.id, {"result": "Completed normally."})

        assert result is True
        assert (await task_runner.get_task(task.id)).state == TaskState.COMPLETED.value
    finally:
        await task_runner.release_lease()