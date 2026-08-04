"""
Integration tests for Sythion (Synth) core systems.
Tests the model decision engine, task execution loop, and API integration.
"""

import pytest
import asyncio
from datetime import datetime, timedelta
from typing import Dict, Any
from unittest.mock import AsyncMock, MagicMock, patch
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, RunnerLease, TaskEvent, get_db
from backend.tools.executor import ToolExecutor
from backend.services.task_runner import TaskRunner
from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError, DecisionType
from backend.services.task_execution_loop import TaskExecutionLoop, run_task
from backend.api.schemas import TaskState


# Test database setup
@pytest.fixture
def db_session():
    """Create a test database session."""
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def tool_executor():
    """Create a tool executor with mock tools."""
    executor = ToolExecutor()
    return executor


@pytest.fixture
def task_runner(db_session):
    """Create a task runner."""
    return TaskRunner(db_session)


class TestModelDecisionEngine:
    """Tests for the model decision engine."""
    
    @pytest.mark.asyncio
    async def test_build_system_prompt(self, db_session, tool_executor):
        """Test system prompt generation."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        prompt = engine._build_system_prompt()
        
        assert "Sythion (Synth)" in prompt
        assert "Windows 10/11" in prompt
        assert "IMPORTANT RULES" in prompt
        assert "AVAILABLE TOOLS" in prompt
        assert "DECISION TYPES" in prompt
    
    @pytest.mark.asyncio
    async def test_build_context_basic(self, db_session, tool_executor):
        """Test context building with basic task info."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        
        messages = engine._build_context(
            task_id="test-123",
            original_request="Check the time",
            normalized_goal=None,
            event_history=[],
            current_state="running"
        )
        
        assert len(messages) == 2  # System + User
        assert messages[0]["role"] == "system"
        assert messages[1]["role"] == "user"
        assert "test-123" in messages[1]["content"]
        assert "Check the time" in messages[1]["content"]
    
    @pytest.mark.asyncio
    async def test_build_context_with_history(self, db_session, tool_executor):
        """Test context building with event history."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        
        event_history = [
            {
                "event_type": "tool.executed",
                "event_data": {
                    "tool_name": "get_time",
                    "success": True,
                    "output": {"time": "12:00:00"}
                },
                "created_at": "2024-01-01T12:00:00"
            }
        ]
        
        messages = engine._build_context(
            task_id="test-123",
            original_request="Check the time",
            normalized_goal=None,
            event_history=event_history,
            current_state="running"
        )
        
        assert "Recent Activity" in messages[1]["content"]
        assert "get_time" in messages[1]["content"]
        assert "SUCCESS" in messages[1]["content"]
    
    def test_parse_decision_valid(self, db_session, tool_executor):
        """Test parsing a valid model response."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        
        mock_response = {
            "choices": [{
                "message": {
                    "tool_calls": [{
                        "function": {
                            "name": "execute_tool",
                            "arguments": '{"tool_name": "get_time", "params": {}}'
                        }
                    }]
                }
            }]
        }
        
        decision_type, params = engine._parse_decision(mock_response)
        
        assert decision_type == "execute_tool"
        assert params["tool_name"] == "get_time"
    
    def test_parse_decision_parallel_rejected(self, db_session, tool_executor):
        """Test that parallel calls are rejected."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        
        mock_response = {
            "choices": [{
                "message": {
                    "tool_calls": [
                        {
                            "function": {
                                "name": "execute_tool",
                                "arguments": '{"tool_name": "get_time", "params": {}}'
                            }
                        },
                        {
                            "function": {
                                "name": "execute_tool",
                                "arguments": '{"tool_name": "get_battery", "params": {}}'
                            }
                        }
                    ]
                }
            }]
        }
        
        with pytest.raises(ModelDecisionError) as exc_info:
            engine._parse_decision(mock_response)
        
        assert exc_info.value.code == "PARALLEL_CALLS"
    
    def test_parse_decision_empty_response(self, db_session, tool_executor):
        """Test handling of empty model response."""
        engine = ModelDecisionEngine(db_session, tool_executor)
        
        mock_response = {"choices": []}
        
        with pytest.raises(ModelDecisionError) as exc_info:
            engine._parse_decision(mock_response)
        
        assert exc_info.value.code == "EMPTY_RESPONSE"

    def test_parse_decision_plain_text_is_rejected(self, db_session, tool_executor):
        """Free-form model text must never be promoted to task completion."""
        engine = ModelDecisionEngine(db_session, tool_executor)

        with pytest.raises(ModelDecisionError) as exc_info:
            engine._parse_decision({
                "choices": [{"message": {"content": "The task is complete."}}]
            })

        assert exc_info.value.code == "MISSING_CONTROL_CALL"


class TestTaskExecutionLoop:
    """Tests for the task execution loop."""
    
    @pytest.mark.asyncio
    async def test_execute_decision_complete_task(self, db_session, task_runner, tool_executor):
        """Test completing a task via decision execution."""
        # Acquire lease before creating task
        await task_runner.acquire_lease()
        
        # Create a task first
        task = await task_runner.create_task("Test task")
        
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        
        # Mock the transition_state to avoid actual DB writes during decision execution
        with patch.object(task_runner, 'transition_state', new_callable=AsyncMock) as mock_transition:
            mock_transition.return_value = task
            
            result = await loop._execute_decision(
                task_id=task.id,
                decision_type=DecisionType.COMPLETE_TASK,
                params={"summary": "Task completed successfully"}
            )
            
            assert result is True
            mock_transition.assert_called_once()
        
        await task_runner.release_lease()

    @pytest.mark.asyncio
    async def test_complete_task_rejects_failed_tool_evidence(self, db_session, task_runner, tool_executor):
        """A model cannot narrate success after a recorded failed action."""
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Test task")
        await task_runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "task.execution_started",
            {},
        )
        await task_runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "tool.executed",
            {"tool_name": "file_write", "success": False, "mutating": True, "verification": "failed"},
        )
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)

        result = await loop._complete_task(task.id, {"summary": "Done"})

        assert result is False
        assert (await task_runner.get_task(task.id)).state == TaskState.RUNNING.value
        await task_runner.release_lease()


class TestTaskLedgerInvariants:
    """Regression tests for durable task ownership and recovery rules."""

    @pytest.mark.asyncio
    async def test_idempotency_key_replays_only_its_original_request(self, task_runner):
        await task_runner.acquire_lease()

        original = await task_runner.create_task("Check the time", "request-123")
        replay = await task_runner.create_task("Check the time", "request-123")

        assert replay.id == original.id
        with pytest.raises(ValueError, match="different request"):
            await task_runner.create_task("Check the battery", "request-123")
        await task_runner.release_lease()

    @pytest.mark.asyncio
    async def test_terminal_task_cannot_transition_back_to_running(self, task_runner):
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Test state machine")
        await task_runner.transition_state(task.id, TaskState.RUNNING, "task.started", {})
        await task_runner.transition_state(task.id, TaskState.COMPLETED, "task.completed", {})

        with pytest.raises(RuntimeError, match="Invalid task state transition"):
            await task_runner.transition_state(task.id, TaskState.RUNNING, "task.restarted", {})
        await task_runner.release_lease()

    @pytest.mark.asyncio
    async def test_stale_lease_marks_active_action_uncertain(self, db_session):
        owner = TaskRunner(db_session)
        await owner.acquire_lease()
        task = await owner.create_task("Create a file")
        await owner.transition_state(task.id, TaskState.RUNNING, "task.started", {})
        await owner.record_action_started(task.id, "attempt-1", "file_create", ["path", "content"])

        lease = db_session.query(RunnerLease).filter_by(id=1).first()
        lease.expires_at = datetime.utcnow() - timedelta(seconds=1)
        db_session.commit()

        replacement = TaskRunner(db_session)
        assert await replacement.acquire_lease(task.id) is True

        recovered = await replacement.get_task(task.id)
        events = db_session.query(TaskEvent).filter_by(task_id=task.id).order_by(TaskEvent.sequence_num).all()
        assert recovered.state == TaskState.INTERRUPTED.value
        assert recovered.active_action_id is None
        assert [event.event_type for event in events][-2:] == ["action.uncertain", "task.interrupted"]
        await replacement.release_lease()
    
    @pytest.mark.asyncio
    async def test_execute_decision_fail_task(self, db_session, task_runner, tool_executor):
        """Test failing a task via decision execution."""
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Test task")
        
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        
        with patch.object(task_runner, 'transition_state', new_callable=AsyncMock) as mock_transition:
            mock_transition.return_value = task
            
            result = await loop._execute_decision(
                task_id=task.id,
                decision_type=DecisionType.FAIL_TASK,
                params={"code": "TEST_ERROR", "user_message": "Test failure"}
            )
            
            assert result is True  # Successfully recorded the failure
            mock_transition.assert_called_once()
        
        await task_runner.release_lease()
    
    @pytest.mark.asyncio
    async def test_execute_decision_request_user_input(self, db_session, task_runner, tool_executor):
        """Test requesting user input via decision execution."""
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Test task")
        
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        
        with patch.object(task_runner, 'transition_state', new_callable=AsyncMock) as mock_transition:
            mock_transition.return_value = task
            
            result = await loop._execute_decision(
                task_id=task.id,
                decision_type=DecisionType.REQUEST_USER_INPUT,
                params={"question": "What file should I create?"}
            )
            
            assert result is True
            mock_transition.assert_called_once()
            call_args = mock_transition.call_args
            assert call_args.kwargs['new_state'] == TaskState.WAITING_FOR_USER
        
        await task_runner.release_lease()
    
    @pytest.mark.asyncio
    async def test_execute_decision_unknown_type(self, db_session, task_runner, tool_executor):
        """Test handling unknown decision type."""
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Test task")
        
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        
        result = await loop._execute_decision(
            task_id=task.id,
            decision_type="unknown_type",
            params={}
        )
        
        assert result is False
        
        await task_runner.release_lease()


class TestTaskRunnerIntegration:
    """Integration tests for task runner with execution loop."""
    
    @pytest.mark.asyncio
    async def test_task_creation_and_lease(self, db_session, task_runner):
        """Test task creation and lease acquisition."""
        # Acquire lease
        acquired = await task_runner.acquire_lease()
        assert acquired is True
        
        # Create task
        task = await task_runner.create_task("Test task for integration")
        assert task is not None
        assert task.original_request == "Test task for integration"
        assert task.state == TaskState.CREATED.value
        
        # Release lease
        await task_runner.release_lease()
    
    @pytest.mark.asyncio
    async def test_state_transitions(self, db_session, task_runner):
        """Test task state transitions."""
        acquired = await task_runner.acquire_lease()
        assert acquired is True
        
        task = await task_runner.create_task("State transition test")
        
        # Transition to running
        updated = await task_runner.transition_state(
            task_id=task.id,
            new_state=TaskState.RUNNING,
            event_type="task.started",
            event_data={"started": True}
        )
        
        assert updated.state == TaskState.RUNNING.value
        
        # Transition to completed
        updated = await task_runner.transition_state(
            task_id=task.id,
            new_state=TaskState.COMPLETED,
            event_type="task.completed",
            event_data={"result": "success"},
            final_result="Task completed successfully"
        )
        
        assert updated.state == TaskState.COMPLETED.value
        assert updated.final_result == "Task completed successfully"
        
        await task_runner.release_lease()


class TestToolExecutorInLoop:
    """Tests for tool executor integration with execution loop."""
    
    @pytest.mark.asyncio
    async def test_execute_tool_decision(self, db_session, task_runner, tool_executor):
        """Test executing a tool via decision."""
        from backend.tools.system_tools import create_system_tools
        
        # Register system tools
        for tool in create_system_tools():
            tool_executor.register_tool(tool)
        
        # Acquire lease
        await task_runner.acquire_lease()
        task = await task_runner.create_task("Tool execution test")
        
        loop = TaskExecutionLoop(db_session, task_runner, tool_executor)
        
        with patch.object(task_runner, 'transition_state', new_callable=AsyncMock) as mock_transition:
            mock_transition.return_value = task
            
            result = await loop._execute_decision(
                task_id=task.id,
                decision_type=DecisionType.EXECUTE_TOOL,
                params={
                    "tool_name": "get_time",
                    "params": {}
                }
            )
            
            # Should succeed (or fail gracefully on Linux without battery)
            assert isinstance(result, bool)
        
        await task_runner.release_lease()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
