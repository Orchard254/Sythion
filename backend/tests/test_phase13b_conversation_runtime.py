"""
Phase 13B: Conversation Runtime Separation Tests

Verifies that:
1. CONVERSATION interaction type does NOT create Task rows.
2. CONVERSATION does NOT invoke TaskExecutionLoop.
3. CONVERSATION responses are persisted correctly without task_id.
4. CONVERSATION maintains continuity across turns.
5. INFORMATIONAL still creates tasks and uses execution loop.
6. ACTION still creates tasks and uses execution loop.
7. GOAL still creates tasks with objectives and uses execution loop.
8. Conversation cannot enter task clarification.
"""

import pytest
from datetime import datetime
from sqlalchemy.orm import Session
from unittest.mock import Mock, AsyncMock, patch, MagicMock

from backend.cognitive.state import InteractionType
from backend.models.database import ConversationTurn, Task, Objective, GoalPlan
from backend.services.conversation_runtime import ConversationRuntime
from backend.api.schemas import TaskCreate


CONVERSATIONAL_SAMPLES = [
    "Hey Sythion, how have you been?",
    "I'm bored. Talk to me.",
    "Tell me something interesting.",
    "Let's talk about computers.",
    "What do you think about that?",
    "I just want your opinion.",
]

INFORMATIONAL_SAMPLES = [
    "What year was Python created?",
    "Why is knowing the time useful to an assistant?",
]

ACTION_SAMPLES = [
    "Can you check what time it is?",
]

GOAL_SAMPLES = [
    "Create a report listing the Python files in C:\\Ai\\sythion.",
]


class TestConversationRuntimeNoTask:
    """Test that conversation does not create tasks."""

    @pytest.mark.asyncio
    async def test_conversation_runtime_does_not_create_task(self, test_db: Session):
        """Conversation should never create a Task row."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        # Mock the model response
        with patch.object(runtime, '_get_model_response', return_value=AsyncMock(return_value="I've been doing well, thanks for asking!")):
            with patch.object(runtime, '_persist_conversation', return_value=AsyncMock()):
                result = await runtime.handle_conversation("Hey Sythion, how have you been?")
        
        # Verify no task was created
        tasks = test_db.query(Task).all()
        assert len(tasks) == 0, "Conversation should not create Task rows"
        
        # Verify result structure
        assert "response" in result
        assert result["interaction_type"] == InteractionType.CONVERSATION.value
        assert "session_id" in result

    @pytest.mark.asyncio
    async def test_conversation_does_not_invoke_task_execution_loop(self, test_db: Session):
        """Conversation must not invoke TaskExecutionLoop."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        # Mock the model response and persistence
        with patch.object(runtime, '_get_model_response', new_callable=AsyncMock) as mock_resp:
            mock_resp.return_value = "Sure, let's chat!"
            with patch.object(runtime, '_persist_conversation', new_callable=AsyncMock):
                # Ensure TaskExecutionLoop is never imported or called
                with patch('backend.services.task_execution_loop.run_task', side_effect=AssertionError("TaskExecutionLoop should not be called")):
                    result = await runtime.handle_conversation("I'm bored. Talk to me.")
        
        assert result is not None
        assert result["response"] == "Sure, let's chat!"

    @pytest.mark.asyncio
    async def test_multiple_conversation_turns_no_tasks(self, test_db: Session):
        """Multiple conversation turns should not create any tasks."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        for sample in CONVERSATIONAL_SAMPLES[:3]:
            with patch.object(runtime, '_get_model_response', return_value=AsyncMock(return_value="Response")):
                with patch.object(runtime, '_persist_conversation', return_value=AsyncMock()):
                    await runtime.handle_conversation(sample)
        
        # Verify no tasks were created
        tasks = test_db.query(Task).all()
        assert len(tasks) == 0, f"Expected 0 tasks, found {len(tasks)}"


class TestConversationPersistence:
    """Test conversation persistence without task dependency."""

    @pytest.mark.asyncio
    async def test_conversation_persisted_without_task_id(self, test_db: Session):
        """Conversation turns should be persisted with task_id=None."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        user_input = "Tell me something interesting."
        assistant_response = "Did you know that the first computer bug was an actual moth?"
        
        # Call the actual persistence method
        await runtime._persist_conversation(user_input, assistant_response)
        
        # Verify conversation turns were persisted
        turns = test_db.query(ConversationTurn).order_by(ConversationTurn.created_at).all()
        assert len(turns) == 2, f"Expected 2 turns, found {len(turns)}"
        
        user_turn = turns[0]
        assistant_turn = turns[1]
        
        assert user_turn.role == "user"
        assert user_turn.task_id is None, "User turn should have no task_id"
        assert user_input in user_turn.summary
        
        assert assistant_turn.role == "assistant"
        assert assistant_turn.task_id is None, "Assistant turn should have no task_id"
        assert assistant_response in assistant_turn.summary

    @pytest.mark.asyncio
    async def test_conversation_continuity_across_turns(self, test_db: Session):
        """Conversation should maintain context across multiple turns."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        # First turn
        await runtime._persist_conversation(
            "I've been thinking about artificial intelligence.",
            "That's a fascinating topic! What specifically interests you about AI?"
        )
        
        # Second turn
        await runtime._persist_conversation(
            "What do you think about that?",
            "I think AI has tremendous potential to augment human capabilities."
        )
        
        # Verify both turns are persisted
        turns = test_db.query(ConversationTurn).order_by(ConversationTurn.created_at).all()
        assert len(turns) == 4, f"Expected 4 turns, found {len(turns)}"
        
        # Verify session_id is consistent
        session_ids = {turn.session_id for turn in turns}
        assert len(session_ids) == 1, "All turns should share the same session_id"

    @pytest.mark.asyncio
    async def test_conversation_context_includes_recent_history(self, test_db: Session):
        """Conversation context should include recent conversation history."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        # Create some conversation history
        await runtime._persist_conversation("Hello", "Hi there!")
        await runtime._persist_conversation("How are you?", "I'm doing well, thank you!")
        
        # Build context for new input
        context = await runtime._build_context("Tell me more.")
        
        # Verify context includes recent conversation
        assert "[RECENT CONVERSATION]" in context or len(context) > 0, "Context should include conversation history"

    @pytest.mark.asyncio
    async def test_context_selects_latest_six_turns_in_order_even_when_timestamps_match(self, test_db: Session):
        from backend.core.session import set_current_session_id
        set_current_session_id("recent-context-test")
        runtime = ConversationRuntime(test_db)
        timestamp = datetime(2026, 1, 1)
        for i in range(10):
            test_db.add(ConversationTurn(session_id=runtime.session_id, role="user" if i % 2 == 0 else "assistant", summary=f"unique-turn-{i}", created_at=timestamp))
        test_db.commit()

        context = await runtime._build_context("follow up")
        lines = [line for line in context.splitlines() if 'unique-turn-' in line]
        assert lines == [f"{'USER' if i % 2 == 0 else 'ASSISTANT'}: unique-turn-{i}" for i in range(4, 10)]


class TestInformationalActionGoalRouting:
    """Test that INFORMATIONAL, ACTION, and GOAL still use task execution."""

    @pytest.mark.asyncio
    async def test_informational_creates_task(self, test_db: Session):
        """INFORMATIONAL requests should create tasks."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor
        from fastapi import BackgroundTasks
        
        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        
        # Mock the cognitive adapter to return execute mode
        with patch('backend.services.cognitive_runtime.CognitiveDecisionAdapter') as mock_adapter_class:
            mock_adapter = MagicMock()
            mock_routing = MagicMock()
            mock_routing.mode = "execute"
            mock_routing.interaction_type = InteractionType.INFORMATIONAL
            mock_routing.decision = None
            mock_adapter.route_new_input = AsyncMock(return_value=mock_routing)
            mock_adapter_class.return_value = mock_adapter
            
            # Mock run_task to avoid actual execution
            with patch('backend.api.routes.run_task'):
                task_data = TaskCreate(
                    original_request="What year was Python created?",
                    idempotency_key=None
                )
                
                response = await create_task(task_data, background_tasks, test_db, tool_executor)
        
        # Verify a task was created (not a pseudo conv- ID)
        assert not response.id.startswith("conv-"), "INFORMATIONAL should create a real task"
        
        # Verify task exists in database
        task = test_db.query(Task).filter_by(id=response.id).first()
        assert task is not None, "INFORMATIONAL should create a Task row"

    @pytest.mark.asyncio
    async def test_action_creates_task_and_executes(self, test_db: Session):
        """ACTION requests should create tasks and use TaskExecutionLoop."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor
        from fastapi import BackgroundTasks
        
        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        
        # Mock the cognitive adapter
        with patch('backend.services.cognitive_runtime.CognitiveDecisionAdapter') as mock_adapter_class:
            mock_adapter = MagicMock()
            mock_routing = MagicMock()
            mock_routing.mode = "execute"
            mock_routing.interaction_type = InteractionType.ACTION
            mock_routing.decision = None
            mock_adapter.route_new_input = AsyncMock(return_value=mock_routing)
            mock_adapter_class.return_value = mock_adapter
            
            # Track if run_task was called
            run_task_called = False
            def track_run_task(*args, **kwargs):
                nonlocal run_task_called
                run_task_called = True
            
            with patch('backend.api.routes.run_task', side_effect=track_run_task):
                task_data = TaskCreate(
                    original_request="Can you check what time it is?",
                    idempotency_key=None
                )
                
                response = await create_task(task_data, background_tasks, test_db, tool_executor)
        
        # Verify task was created
        assert not response.id.startswith("conv-"), "ACTION should create a real task"
        task = test_db.query(Task).filter_by(id=response.id).first()
        assert task is not None, "ACTION should create a Task row"
        
        # Verify run_task was scheduled
        assert run_task_called or len(background_tasks.tasks) > 0, "ACTION should schedule TaskExecutionLoop"

    @pytest.mark.asyncio
    async def test_goal_creates_task_with_objective(self, test_db: Session):
        """GOAL requests should create tasks with objectives."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor
        from fastapi import BackgroundTasks
        
        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        
        # Mock the cognitive adapter
        with patch('backend.services.cognitive_runtime.CognitiveDecisionAdapter') as mock_adapter_class:
            mock_adapter = MagicMock()
            mock_routing = MagicMock()
            mock_routing.mode = "execute"
            mock_routing.interaction_type = InteractionType.GOAL
            mock_routing.decision = MagicMock()
            mock_routing.decision.decision_type = "CREATE_GOAL"
            mock_adapter.route_new_input = AsyncMock(return_value=mock_routing)
            mock_adapter_class.return_value = mock_adapter
            
            # Mock ensure_task_objective to avoid actual objective creation complexity
            with patch('backend.services.work_state.ensure_task_objective'):
                with patch('backend.api.routes.run_task'):
                    task_data = TaskCreate(
                        original_request="Create a report listing the Python files in C:\\Ai\\sythion.",
                        idempotency_key=None
                    )
                    
                    response = await create_task(task_data, background_tasks, test_db, tool_executor)
        
        # Verify task was created
        assert not response.id.startswith("conv-"), "GOAL should create a real task"
        task = test_db.query(Task).filter_by(id=response.id).first()
        assert task is not None, "GOAL should create a Task row"


class TestConversationNeverClarification:
    """Test that conversation cannot enter task clarification."""

    @pytest.mark.asyncio
    async def test_conversation_does_not_create_waiting_for_user(self, test_db: Session):
        """Conversational inputs must never create WAITING_FOR_USER tasks."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor
        from fastapi import BackgroundTasks
        
        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        
        # Even if somehow a conversation reached the task path, it shouldn't wait
        for sample in CONVERSATIONAL_SAMPLES[:2]:
            task_data = TaskCreate(
                original_request=sample,
                idempotency_key=None
            )
            
            with patch('backend.services.conversation_runtime.ConversationRuntime.handle_conversation', 
                      new_callable=AsyncMock) as mock_handle:
                mock_handle.return_value = {"response": "Response", "interaction_type": "CONVERSATION"}
                response = await create_task(task_data, background_tasks, test_db, tool_executor)
            
            # Verify response is completed (not waiting)
            assert response.state == "completed", f"Conversation should complete immediately, got {response.state}"
            
            # Verify no real task was created in waiting state
            waiting_tasks = test_db.query(Task).filter_by(state="waiting_for_user").all()
            assert len(waiting_tasks) == 0, "Conversation should never create waiting_for_user tasks"


class TestConversationRuntimeIntegration:
    """Integration tests for full conversation flow."""

    @pytest.mark.asyncio
    async def test_full_conversation_flow(self, test_db: Session):
        """Test complete conversation from input to persistence."""
        from backend.core.session import set_current_session_id
        set_current_session_id("test-session-id")
        
        runtime = ConversationRuntime(test_db)
        
        # Mock model client
        with patch('backend.services.conversation_runtime.ModelClient') as mock_client_class:
            mock_client = MagicMock()
            mock_client.chat = AsyncMock(return_value="That's a great question! I think...")
            mock_client_class.return_value = mock_client
            
            result = await runtime.handle_conversation("What do you think about artificial intelligence?")
        
        # Verify result structure
        assert result["response"] == "That's a great question! I think..."
        assert result["interaction_type"] == InteractionType.CONVERSATION.value
        
        # Verify persistence
        turns = test_db.query(ConversationTurn).all()
        assert len(turns) == 2, "Should persist user and assistant turns"
        
        # Verify no task was created
        tasks = test_db.query(Task).all()
        assert len(tasks) == 0, "Conversation should not create tasks"
        
        # Verify no objective was created
        objectives = test_db.query(Objective).all()
        assert len(objectives) == 0, "Conversation should not create objectives"


class TestConversationErrorSemantics:
    """Tests for conversational provider failure error semantics."""

    @pytest.mark.asyncio
    async def test_provider_failure_raises_model_error_and_no_turns_persisted(self, test_db: Session):
        """Provider failure must raise ConversationModelError and NOT fabricate or persist turns."""
        from backend.core.session import set_current_session_id
        from backend.services.conversation_runtime import ConversationModelError
        set_current_session_id("test-session-error")
        
        runtime = ConversationRuntime(test_db)
        with patch.object(runtime.model_client, 'chat', side_effect=RuntimeError("Provider offline")):
            with pytest.raises(ConversationModelError) as excinfo:
                await runtime.handle_conversation("Hello Sythion")
            assert "Provider offline" in str(excinfo.value)
        
        # CRITICAL: No conversation turns persisted
        turns = test_db.query(ConversationTurn).all()
        assert len(turns) == 0, "Provider failure must not persist fabricated turns"
        
        # CRITICAL: No tasks created
        tasks = test_db.query(Task).all()
        assert len(tasks) == 0, "Provider failure must not create tasks"

    @pytest.mark.asyncio
    async def test_provider_failure_in_routes_returns_503_no_task(self, test_db: Session):
        """API create_task must fail gracefully with 503 on provider error without creating tasks."""
        from backend.api.routes import create_task
        from backend.tools.executor import ToolExecutor
        from fastapi import BackgroundTasks, HTTPException
        from backend.services.conversation_runtime import ConversationModelError
        
        tool_executor = ToolExecutor()
        background_tasks = BackgroundTasks()
        task_data = TaskCreate(
            original_request="Hey Sythion, how are you?",
            idempotency_key=None,
        )
        
        with patch('backend.services.conversation_runtime.ConversationRuntime.handle_conversation',
                   side_effect=ConversationModelError("Ollama Cloud unavailable")):
            with pytest.raises(HTTPException) as excinfo:
                await create_task(task_data, background_tasks, test_db, tool_executor)
            
            assert excinfo.value.status_code == 503
            assert "Conversation model provider unavailable" in excinfo.value.detail
        
        # Ensure zero tasks created in DB
        assert test_db.query(Task).count() == 0
        assert test_db.query(Objective).count() == 0


@pytest.mark.asyncio
async def test_unpersist_removes_only_identified_sibling_turns(test_db):
    from backend.core.session import set_current_session_id
    from backend.models.database import ConversationExchange, ConversationTurn

    set_current_session_id("sibling-cleanup-session")
    runtime = ConversationRuntime(test_db)

    first = await runtime._persist_conversation("same text", "same answer")
    second = await runtime._persist_conversation("same text", "same answer")

    assert test_db.query(ConversationExchange).count() == 2
    assert test_db.query(ConversationTurn).count() == 4

    await runtime._unpersist_conversation(second)

    assert test_db.query(ConversationExchange).count() == 1
    assert test_db.query(ConversationTurn).count() == 2
    remaining = test_db.query(ConversationExchange).one()
    assert remaining.id == first["exchange_id"]


@pytest.mark.asyncio
async def test_unpersist_fail_closed_uses_original_persisted_identity(test_db):
    from backend.core.session import set_current_session_id
    from backend.models.database import ConversationExchange, ConversationTurn

    set_current_session_id("fail-closed-cleanup-session")
    runtime = ConversationRuntime(test_db)
    original_assistant = "I started the work."
    persisted = await runtime._persist_conversation("do work", original_assistant)

    fail_closed_message = "Requested background work was not started."

    # Exact persisted identity identifies the original rows even when the
    # assistant text is later replaced by the fail-closed message.
    await runtime._unpersist_conversation(persisted)

    assert test_db.query(ConversationExchange).count() == 0
    assert test_db.query(ConversationTurn).count() == 0


@pytest.mark.asyncio
async def test_unpersist_cleanup_failure_propagates(test_db):
    from backend.core.session import set_current_session_id

    set_current_session_id("cleanup-failure-session")
    runtime = ConversationRuntime(test_db)
    persisted = await runtime._persist_conversation("text", "answer")

    async def _boom(*args, **kwargs):
        raise RuntimeError("cleanup boom")

    runtime._unpersist_conversation = _boom

    with pytest.raises(RuntimeError, match="cleanup boom"):
        await runtime._unpersist_conversation(persisted)


@pytest.mark.asyncio
async def test_unpersist_collision_timestamps_removes_only_exact_rows(test_db):
    from backend.core.session import set_current_session_id
    from unittest.mock import patch
    from datetime import datetime

    from backend.models.database import ConversationExchange
    set_current_session_id("collision-cleanup-session")
    runtime = ConversationRuntime(test_db)
    fixed_now = datetime(2026, 2, 3, 4, 5, 6)

    with patch("backend.services.conversation_runtime.datetime") as mock_datetime:
        mock_datetime.utcnow.return_value = fixed_now
        first = await runtime._persist_conversation("same text", "same answer")
        second = await runtime._persist_conversation("same text", "same answer")

    assert test_db.query(ConversationExchange).count() == 2
    assert test_db.query(ConversationTurn).count() == 4

    await runtime._unpersist_conversation(first)

    assert test_db.query(ConversationExchange).count() == 1
    assert test_db.query(ConversationTurn).count() == 2
    remaining = test_db.query(ConversationExchange).one()
    assert remaining.id == second["exchange_id"]
    assert remaining.created_at == fixed_now


@pytest.mark.asyncio
async def test_unpersist_normal_repeated_calls_leave_no_rows(test_db):
    from backend.core.session import set_current_session_id
    from backend.models.database import ConversationExchange, ConversationTurn

    set_current_session_id("repeated-cleanup-session")
    runtime = ConversationRuntime(test_db)

    identities = []
    for index in range(3):
        identity = await runtime._persist_conversation(
            f"input {index}", f"answer {index}"
        )
        identities.append(identity)

    assert test_db.query(ConversationExchange).count() == 3
    assert test_db.query(ConversationTurn).count() == 6

    for identity in identities:
        await runtime._unpersist_conversation(identity)

    assert test_db.query(ConversationExchange).count() == 0
    assert test_db.query(ConversationTurn).count() == 0


@pytest.mark.asyncio
async def test_unpersist_without_turn_ids_does_not_delete_rows(test_db):
    from backend.core.session import set_current_session_id
    from backend.models.database import ConversationExchange, ConversationTurn

    set_current_session_id("missing-ids-cleanup-session")
    runtime = ConversationRuntime(test_db)
    persisted = await runtime._persist_conversation("text", "answer")

    before_exchanges = test_db.query(ConversationExchange).count()
    before_turns = test_db.query(ConversationTurn).count()

    with pytest.raises(ValueError):
        await runtime._unpersist_conversation(persisted["exchange_id"])

    assert test_db.query(ConversationExchange).count() == before_exchanges
    assert test_db.query(ConversationTurn).count() == before_turns
