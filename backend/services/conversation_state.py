"""
Conversation state tracking for Sythion (Synth).

Tracks active topic, previous topics, open threads, emotional context,
and conversation stage across tasks within a session.
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
    emotional_context: str
    conversation_stage: str

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

# Emotional indicators
_EMOTION_PATTERNS = {
    "frustrated": r"\b(frustrat(ed|ing)?|annoyed|irritated|angry|mad|furious|pissed|ugh|hate)\b",
    "excited": r"\b(excited|amazing|awesome|incredible|wow|great|fantastic|love it)\b",
    "confused": r"\b(confused|confusing|puzzled|lost|don't understand|unclear|what)\b",
    "positive": r"\b(happy|glad|good|nice|great|awesome|love|enjoy|fun|cool)\b",
    "negative": r"\b(sad|bad|terrible|horrible|awful|sucks|hate|worst|annoying)\b",
    "tired": r"\b(tired|exhausted|drained|worn out|sleepy|fatigued)\b",
}

# Speech act patterns
_GREETING_PATTERN = r"^(hi|hey|hello|sup|yo|howdy|greetings|good\s+(morning|afternoon|evening))\b"
_FAREWELL_PATTERN = r"\b(bye|goodbye|see ya|later|good night|take care|gotta go)\b"
_STORY_PATTERN = r"\b(so|anyway|then|and then|you won't believe|guess what|listen to this)\b"
_QUESTION_PATTERN = r"\?$"
_CORRECTION_PATTERN = r"\b(no|actually|wait|that's not|i meant|correction|wrong|incorrect)\b"


def _extract_topics(message: str) -> List[str]:
    """Extract topic keywords from a user message."""
    # Simple keyword extraction: remove stop words, take remaining content words
    words = re.findall(r"[a-z]+", message.lower())
    topics = [w for w in words if w not in _STOP_WORDS and len(w) > 2]
    # Deduplicate while preserving order
    seen = set()
    unique = []
    for t in topics:
        if t not in seen:
            seen.add(t)
            unique.append(t)
    return unique[:3]  # Max 3 topics per message


def _detect_emotion(message: str) -> str:
    """Detect emotional tone from user message."""
    message_lower = message.lower()
    scores = {}
    for emotion, pattern in _EMOTION_PATTERNS.items():
        matches = re.findall(pattern, message_lower)
        if matches:
            scores[emotion] = len(matches)
    if scores:
        return max(scores, key=scores.get)
    return "neutral"


def _detect_speech_act(message: str) -> str:
    """Detect the primary speech act of a user message."""
    message_lower = message.lower().strip()
    if re.match(_GREETING_PATTERN, message_lower):
        return "greeting"
    if re.search(_FAREWELL_PATTERN, message_lower):
        return "farewell"
    if re.search(_CORRECTION_PATTERN, message_lower):
        return "correction"
    if re.search(_STORY_PATTERN, message_lower):
        return "story"
    if re.search(_QUESTION_PATTERN, message_lower):
        return "question"
    return "statement"


def _detect_conversation_stage(message: str, existing_topics: List[str]) -> str:
    """Determine the conversation stage based on message and history."""
    speech_act = _detect_speech_act(message)
    if speech_act == "greeting" and not existing_topics:
        return "opening"
    if speech_act == "farewell":
        return "closing"
    if len(existing_topics) > 3:
        return "deepening"
    return "developing"


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
                emotional_context="neutral",
                conversation_stage="opening",
            )
            session.add(state)
            session.commit()
        return ConversationStateData(
            session_id=state.session_id,
            active_topic=state.active_topic,
            previous_topics=list(state.previous_topics or []),
            open_threads=list(state.open_threads or []),
            emotional_context=state.emotional_context,
            conversation_stage=state.conversation_stage,
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
                emotional_context="neutral",
                conversation_stage="opening",
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

        # Update emotional context
        state.emotional_context = _detect_emotion(user_message)

        # Update conversation stage
        state.conversation_stage = _detect_conversation_stage(
            user_message, state.previous_topics or []
        )

        state.last_updated = datetime.utcnow()
        session.commit()
        return ConversationStateData(
            session_id=state.session_id,
            active_topic=state.active_topic,
            previous_topics=list(state.previous_topics or []),
            open_threads=list(state.open_threads or []),
            emotional_context=state.emotional_context,
            conversation_stage=state.conversation_stage,
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

    if state.emotional_context and state.emotional_context != "neutral":
        lines.append(f"User's emotional state: {state.emotional_context}")

    if state.conversation_stage:
        lines.append(f"Conversation stage: {state.conversation_stage}")

    return "\n".join(lines)
