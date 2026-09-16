"""
Tests for CLI Conversation Response Contract (Phase 13C).

Verifies that:
1. Conversation responses return interaction_type == "CONVERSATION".
2. CLI does NOT poll /tasks/conv-... for conversational inputs.
3. No HTTP 404 task-not-found errors occur on conversation responses.
4. Action and Goal requests preserve the real Task lifecycle and polling.
"""

import pytest
from unittest.mock import patch, MagicMock, AsyncMock
from fastapi import BackgroundTasks
from sqlalchemy.orm import Session

from backend.api.schemas import TaskCreate, TaskResponse, TaskState
from backend.models.database import Task
import cli


class TestCLIConversationContract:
    """Test CLI client handling of conversation vs task responses."""

    def test_watch_task_defends_against_conv_id(self):
        """watch_task must not call API if passed a conv- pseudo ID."""
        with patch("cli.api") as mock_api:
            result = cli.watch_task("conv-12345-abcde")
            assert result == "completed"
            mock_api.assert_not_called()

    def test_run_single_request_conversation_no_watch(self, capsys):
        """Conversation input must display response and NOT call watch_task."""
        mock_response = {
            "id": "conv-a1ab0671-6c38-4522-909e-75a3b6ad93b0",
            "conversation_id": "conv-a1ab0671-6c38-4522-909e-75a3b6ad93b0",
            "interaction_type": "CONVERSATION",
            "original_request": "Let's talk about AI.",
            "state": "completed",
            "response": "AI is advancing rapidly!",
            "final_result": "AI is advancing rapidly!",
        }

        with patch("cli.api", return_value=mock_response) as mock_api,              patch("cli.watch_task") as mock_watch:
            res = cli.run_single_request("Let's talk about AI.")
            assert res == "completed"
            mock_watch.assert_not_called()
            mock_api.assert_called_once_with("/tasks", method="POST", data={"original_request": "Let's talk about AI."})
            
            captured = capsys.readouterr()
            assert "AI is advancing rapidly!" in captured.out
            assert "HTTP 404" not in captured.out

    def test_run_single_request_action_uses_watch(self):
        """Action input must call watch_task with real task ID."""
        mock_response = {
            "id": "task-real-action-uuid-123",
            "interaction_type": "TASK",
            "original_request": "Can you check what time it is?",
            "state": "running",
            "final_result": None,
        }

        with patch("cli.api", return_value=mock_response),              patch("cli.watch_task", return_value="completed") as mock_watch:
            res = cli.run_single_request("Can you check what time it is?")
            assert res == "completed"
            mock_watch.assert_called_once_with("task-real-action-uuid-123", log_path=None)

    def test_cli_main_conversation_no_watch(self, capsys):
        """cli.main() with conversation prompt must not watch task."""
        mock_response = {
            "id": "conv-123",
            "conversation_id": "conv-123",
            "interaction_type": "CONVERSATION",
            "original_request": "hi",
            "state": "completed",
            "response": "Hello there! How can I help you today?",
        }

        with patch("sys.argv", ["cli.py", "hi"]),              patch("cli.api") as mock_api,              patch("cli.watch_task") as mock_watch:
            # health check then create task
            mock_api.side_effect = [
                {"status": "healthy", "selected_model": "test-model"},
                mock_response,
            ]
            cli.main()
            mock_watch.assert_not_called()
            captured = capsys.readouterr()
            assert "Hello there!" in captured.out
            assert "HTTP 404" not in captured.out


class TestAPIContractConversationResponse:
    """Test create_task endpoint response structure for conversation."""

    @pytest.mark.asyncio
    async def test_create_task_returns_conversation_contract(self, test_db: Session):
        """create_task must return interaction_type='CONVERSATION' and conversation_id."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor

        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        task_data = TaskCreate(
            original_request="Let's talk about AI. What's your take on where it's going?",
            idempotency_key=None,
        )

        with patch("backend.services.conversation_runtime.ConversationRuntime.handle_conversation",
                   new_callable=AsyncMock) as mock_handle:
            mock_handle.return_value = {
                "response": "AI is evolving rapidly across many domains.",
                "interaction_type": "CONVERSATION",
            }
            resp = await create_task(task_data, background_tasks, test_db, tool_executor)

        assert resp.interaction_type == "CONVERSATION"
        assert resp.conversation_id is not None
        assert resp.conversation_id.startswith("conv-")
        assert resp.response == "AI is evolving rapidly across many domains."
        assert resp.final_result == "AI is evolving rapidly across many domains."
        assert resp.state == TaskState.COMPLETED
        
        # Verify no task in DB
        db_tasks = test_db.query(Task).all()
        assert len(db_tasks) == 0
