"""
Conversation Runtime for Phase 13B.

Handles pure CONVERSATION interaction type without creating Tasks or invoking
TaskExecutionLoop. This is the genuine conversational path for small talk,
opinions, chit-chat, and social exchanges.

CONVERSATION must NOT:
- Create Task rows
- Create Objective/GoalPlan/PlanStep
- Invoke TaskExecutionLoop
- Use action budgets
- Use task leases
- Transition task terminal states

CONVERSATION must:
- Build conversational context (reuse existing mechanisms)
- Invoke model for response
- Persist conversation turns
- Maintain conversation continuity
"""

import logging
import uuid
from datetime import datetime
from typing import Dict, Any, Optional, List
from sqlalchemy.orm import Session

from backend.models.database import ConversationExchange, ConversationTurn, MemoryEntry
from backend.core.session import get_current_session_id
from backend.cognitive.state import InteractionType

logger = logging.getLogger(__name__)


class ConversationModelError(RuntimeError):
    """Raised when the conversational model provider fails."""
    pass


class ModelClient:
    """Conversational model client interfacing with the configured model provider."""

    def __init__(
        self,
        base_url: Optional[str] = None,
        default_model: Optional[str] = None,
        provider: Optional[Any] = None,
    ):
        if provider is None:
            from backend.runtime.model_provider import get_default_provider
            self.provider = get_default_provider()
        else:
            self.provider = provider
        from backend.core.config import settings
        self.base_url = base_url or getattr(settings, "ollama_cloud_base_url", "https://ollama.com")
        self.default_model = default_model or getattr(settings, "selected_model", "")

    async def chat(
        self,
        messages: List[Dict[str, Any]],
        model: Optional[str] = None,
        temperature: float = 0.8,
        max_tokens: int = 4000,
        timeout: float = 60.0,
    ) -> str:
        resp = await self.provider.chat(
            messages=messages,
            max_tokens=max_tokens,
            timeout=timeout,
        )
        if isinstance(resp, dict):
            msg = resp.get("message", {})
            if isinstance(msg, dict) and "content" in msg:
                return msg["content"]
            if "response" in resp:
                return resp["response"]
            if "content" in resp:
                return resp["content"]
        return str(resp)


class ConversationRuntime:
    """Runtime for pure conversational interactions without task execution."""
    
    def __init__(self, db: Session, model_client: Optional[ModelClient] = None):
        self.db = db
        self.session_id = get_current_session_id()
        self._model_client = model_client

    @property
    def model_client(self) -> ModelClient:
        if self._model_client is None:
            self._model_client = ModelClient()
        return self._model_client
    
    async def handle_conversation(self, user_input: str) -> Dict[str, Any]:
        """
        Handle a pure conversation request.
        
        Args:
            user_input: The user's conversational input
            
        Returns:
            Dict with 'response' and metadata
        """
        logger.info("ConversationRuntime handling input")
        
        # Build conversational context
        context = await self._build_context(user_input)
        
        # Get model response - raises ConversationModelError on provider failure
        response = await self._get_model_response(user_input, context)
        
        # Persist conversation turn ONLY when model generation succeeds
        conversation_id = f"conv-{uuid.uuid4()}"
        await self._persist_conversation(user_input, response, conversation_id)
        
        logger.info("ConversationRuntime completed successfully")
        
        return {
            "response": response,
            "conversation_id": conversation_id,
            "interaction_type": InteractionType.CONVERSATION.value,
            "session_id": self.session_id,
            "timestamp": datetime.utcnow().isoformat(),
        }
    
    async def _build_context(self, user_input: str) -> str:
        """
        Build conversational context from recent conversation history and memory.
        
        Reuses existing context-building mechanisms without task dependency.
        """
        context_parts = []
        
        # Recent conversation turns
        try:
            recent_turns = (
                self.db.query(ConversationTurn)
                .filter_by(session_id=self.session_id)
                .order_by(ConversationTurn.created_at.desc())
                .limit(10)
                .all()
            )
            
            if recent_turns:
                context_parts.append("[RECENT CONVERSATION]")
                for turn in reversed(recent_turns[-6:]):  # Last 3 exchanges
                    context_parts.append(f"{turn.role.upper()}: {turn.summary}")
        except Exception as e:
            logger.warning(f"Could not load recent conversation: {e}")
        
        # Conversation state (active topic)
        try:
            from backend.services.conversation_state import get_or_create_state, format_state_for_context
            
            state = get_or_create_state(self.session_id)
            state_context = format_state_for_context(state)
            if state_context:
                context_parts.append(state_context)
        except Exception as e:
            logger.warning(f"Could not load conversation state: {e}")
        
        # User preferences from memory
        try:
            preferences = (
                self.db.query(MemoryEntry)
                .filter(MemoryEntry.category.in_(["user_profile", "user_preference"]))
                .limit(5)
                .all()
            )
            
            if preferences:
                context_parts.append("[USER CONTEXT]")
                for pref in preferences:
                    context_parts.append(f"{pref.key}: {pref.value}")
        except Exception as e:
            logger.warning(f"Could not load user preferences: {e}")
        
        return "\n".join(context_parts) if context_parts else ""
    
    async def _get_model_response(self, user_input: str, context: str) -> str:
        """
        Get conversational response from the model.
        
        Reuses ModelClient without TaskExecutionLoop machinery.
        Raises ConversationModelError on provider failure.
        """
        from backend.core.config import settings
        
        # Build conversation prompt
        system_prompt = self._build_conversation_system_prompt()
        
        messages = []
        
        if context:
            messages.append({
                "role": "system",
                "content": f"{system_prompt}\n\n{context}"
            })
        else:
            messages.append({
                "role": "system",
                "content": system_prompt
            })
        
        messages.append({
            "role": "user",
            "content": user_input
        })
        
        try:
            target_model = getattr(settings, "selected_model", None) or None
            response_text = await self.model_client.chat(
                messages=messages,
                model=target_model,
                temperature=0.8,
            )
            if not response_text or not response_text.strip():
                raise ConversationModelError("Empty response received from model provider")
            return response_text.strip()
        except Exception as e:
            logger.error(f"Error getting model response: {e}", exc_info=True)
            raise ConversationModelError(f"Model provider failure: {e}") from e
    
    def _build_conversation_system_prompt(self) -> str:
        """Build system prompt for conversational interactions."""
        return """You are Sythion (also called Synth), a friendly and thoughtful AI assistant.

You are engaging in natural conversation - not executing tasks or commands. Be:
- Conversational and warm
- Thoughtful and genuine in your responses
- Willing to share opinions and engage in discussion
- Natural and not overly formal

This is a casual conversation, not a task execution. Respond naturally as you would
in a friendly chat."""
    
    async def _persist_conversation(self, user_input: str, assistant_response: str, conversation_id: Optional[str] = None) -> None:
        """
        Persist conversation turn without task dependency.
        
        Unlike task-based persistence, this has no task_id.
        """
        try:
            # Truncate if necessary
            user_summary = user_input.strip()
            if len(user_summary) > 500:
                user_summary = user_summary[:497] + "..."
            
            assistant_summary = assistant_response.strip()
            if len(assistant_summary) > 2000:
                assistant_summary = assistant_summary[:1997] + "..."
            
            # Create conversation turns (no task_id)
            turn_user = ConversationTurn(
                session_id=self.session_id,
                task_id=None,  # No task for pure conversation
                role="user",
                summary=user_summary,
            )
            
            turn_assistant = ConversationTurn(
                session_id=self.session_id,
                task_id=None,  # No task for pure conversation
                role="assistant",
                summary=assistant_summary,
            )
            
            self.db.add(turn_user)
            self.db.add(turn_assistant)
            self.db.add(ConversationExchange(
                id=conversation_id or f"conv-{uuid.uuid4()}",
                session_id=self.session_id,
                user_text=user_input,
                assistant_text=assistant_response,
            ))
            self.db.commit()
            
            logger.info(f"Persisted conversation turn for session {self.session_id}")
            
            # Update conversation state
            try:
                from backend.services.conversation_state import update_state
                update_state(self.session_id, user_input, assistant_response)
            except Exception as e:
                logger.warning(f"Could not update conversation state: {e}")
                
        except Exception:
            try:
                self.db.rollback()
            except Exception:
                logger.error("Conversation persistence rollback failed")
            logger.error("Error persisting conversation")
            raise RuntimeError("Conversation persistence failed") from None
