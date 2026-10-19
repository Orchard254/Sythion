"""
Conversation-guidance adapter for model context assembly.

This module groups the higher-order conversational inputs that shape model
behavior but do not define runtime truth:

- recent conversation-turn replay
- persisted conversation-state formatting

`ModelContextBuilder` can therefore remain focused on assembling the overall
model context while delegating conversation-specific guidance to one adapter.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sqlalchemy.orm import Session

if TYPE_CHECKING:
    try:
        from backend.services.conversation_state import ConversationStateData
    except ImportError:
        from services.conversation_state import ConversationStateData

logger = logging.getLogger(__name__)


@dataclass
class ConversationGuidanceBlocks:
    recent_conversation: str = ""
    conversation_state: str = ""


class ConversationGuidanceAdapter:
    """Load conversation-facing guidance blocks for model context."""

    def __init__(
        self,
        db_session: Session,
        session_id_provider: Callable[[], str] | None = None,
    ) -> None:
        self.db = db_session
        if session_id_provider is None:
            try:
                from backend.core.session import get_current_session_id
            except ImportError:
                from core.session import get_current_session_id

            session_id_provider = get_current_session_id
        self._session_id_provider = session_id_provider

    def build_blocks(self, original_request: str, has_previous_context: bool) -> ConversationGuidanceBlocks:
        """Build all conversation-facing context blocks best-effort."""
        blocks = ConversationGuidanceBlocks()
        conv_state = None

        try:
            sess_id = self._session_id_provider()
            blocks.recent_conversation = self._recent_conversation_block(sess_id)
        except Exception as exc:
            logger.warning("Could not load conversation turns: %s", exc)

        try:
            sess_id = self._session_id_provider()
            conv_state, blocks.conversation_state = self._conversation_state_block(sess_id)
        except Exception as exc:
            logger.warning("Could not load conversation state: %s", exc)

        return blocks

    def _recent_conversation_block(self, session_id: str) -> str:
        try:
            from backend.models.database import ConversationTurn
        except ImportError:
            from models.database import ConversationTurn

        turns = (
            self.db.query(ConversationTurn)
            .filter_by(session_id=session_id)
            .order_by(ConversationTurn.created_at.desc(), ConversationTurn.id.desc())
            .limit(20)
            .all()
        )
        if not turns:
            return ""

        lines = ["RECENT CONVERSATION (this session):"]
        for turn in reversed(turns):
            prefix = "User" if str(turn.role) == "user" else "Synth"
            lines.append(f"- {prefix}: {turn.summary}")
        return "\n".join(lines)

    def _conversation_state_block(self, session_id: str) -> tuple[ConversationStateData, str]:
        try:
            from backend.services.conversation_state import get_or_create_state, format_state_for_context
        except ImportError:
            from services.conversation_state import get_or_create_state, format_state_for_context

        conv_state = get_or_create_state(session_id)
        return conv_state, format_state_for_context(conv_state)