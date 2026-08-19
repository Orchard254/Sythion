"""
Conversation state tracking for Sythion (Synth).

Tracks active topic, previous topics, and open threads across tasks within a session.
"""

import json
import logging
import re
from dataclasses import dataclass
from datetime import datetime
from typing import Dict, List, Optional, Tuple

from backend.models.database import ConversationState, get_db_manager

logger = logging.getLogger(__name__)

# --- Data class for detached state ------------------------------------------

@dataclass
class ConversationStateData:
    """Detached copy of conversation state (safe to use outside session)."""
    session_id: str
    active_topic: Optional[str]
    previous_topics: List[str]
    open_threads: List[str]

# --- Topic extraction -------------------------------------------------------

# Common words to exclude from topic extraction
_STOP_WORDS = frozenset({
    "i", "you", "he", "she", "it", "we", "they", "me", "him", "her", "us", "them",
    "my", "your", "his", "its", "our", "their", "mine", "yours", "hers", "ours", "theirs",
    "a", "an", "the", "this", "that", "these", "those",
    "is", "am", "are", "was", "were", "be", "been", "being",
    "have", "has", "had", "do", "does", "did",
    "will", "would", "could", "should", "may", "might", "shall", "can",
    "to", "of", "in", "for", "on", "with", "at", "by", "from", "as", "into",
    "and", "but", "or", "not", "no", "so", "if", "then", "than", "too",
    "what", "which", "who", "whom", "where", "when", "why", "how",
    "all", "each", "every", "both", "few", "more", "most", "other", "some", "such",
    "just", "also", "very", "really", "quite", "about", "up", "out", "off",
    "over", "under", "again", "further", "once", "here", "there",
    "hi", "hey", "hello", "thanks", "thank", "please", "yes", "no",
})

# Meaningful 2-letter technical units and acronyms allowed in topic extraction
_ALLOWED_SHORT_TOPICS = frozenset({
    "mb", "kb", "gb", "tb", "pb", "eb", "zb", "yb",
    "db", "os", "ip", "ui", "ai", "id", "vm", "io",
    "py", "js", "ts", "go", "sh", "c", "r",
    "hr", "ms", "ns", "hz", "km", "cm", "mm", "kg", "lb", "oz", "ft", "in", "yd", "mi",
})


def _extract_topics(message: str) -> List[str]:
    """Extract topic keywords from a user message.
    
    Simple literal keyword extraction: remove stop words, take remaining content words.
    No sentiment, importance scoring, or categorization - just captures what words
    appear in the message that aren't common function words.
    """
    words = re.findall(r"[a-z]+", message.lower())
    topics = [
        w for w in words
        if w not in _STOP_WORDS and (len(w) > 2 or w in _ALLOWED_SHORT_TOPICS)
    ]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:3]  # Max 3 topics per message


# --- State management -------------------------------------------------------

def get_or_create_state(session_id: str) -> ConversationStateData:
    """Get or create conversation state for a session."""
    db_mgr = get_db_manager()
    with db_mgr.get_session() as session:
        state = session.query(ConversationState).filter_by(session_id=session_id).first()
        if not state:
            state = ConversationState(
                session_id=session_id,
                active_topic=None,
                previous_topics=[],
                open_threads=[],
            )
            session.add(state)
            session.commit()
        return ConversationStateData(
            session_id=state.session_id,
            active_topic=state.active_topic,
            previous_topics=list(state.previous_topics or []),
            open_threads=list(state.open_threads or []),
        )


def update_state(session_id: str, user_message: str, assistant_response: str) -> ConversationStateData:
    """Update conversation state based on new user message and assistant response.

    Called after each task completion to track conversation flow.
    """
    db_mgr = get_db_manager()
    with db_mgr.get_session() as session:
        state = session.query(ConversationState).filter_by(session_id=session_id).first()
        if not state:
            state = ConversationState(
                session_id=session_id,
                active_topic=None,
                previous_topics=[],
                open_threads=[],
            )
            session.add(state)

        # Extract topics from user message
        new_topics = _extract_topics(user_message)

        # Update active topic
        if new_topics:
            # Move current active topic to previous if it exists
            if state.active_topic and state.active_topic not in (state.previous_topics or []):
                prev = state.previous_topics or []
                prev.append(state.active_topic)
                state.previous_topics = prev[-10:]  # Keep last 10

            state.active_topic = new_topics[0]

        state.last_updated = datetime.utcnow()
        session.commit()
        return ConversationStateData(
            session_id=state.session_id,
            active_topic=state.active_topic,
            previous_topics=list(state.previous_topics or []),
            open_threads=list(state.open_threads or []),
        )


def format_state_for_context(state: ConversationStateData) -> str:
    """Format conversation state as a context block for the model."""
    if not state:
        return ""

    lines = ["[CONVERSATION STATE]"]

    if state.active_topic:
        lines.append(f"Current topic: {state.active_topic}")

    if state.previous_topics:
        lines.append(f"Recent topics: {', '.join(state.previous_topics[-5:])}")

    if state.open_threads:
        lines.append(f"Open threads: {', '.join(state.open_threads)}")

    return "\n".join(lines)