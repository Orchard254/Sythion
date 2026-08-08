"""
Speech act classifier for Sythion (Synth).

Classifies user messages into speech acts to inform response strategy.
Rule-based implementation using regex patterns.
"""

import re
from dataclasses import dataclass
from enum import Enum
from typing import Optional


class SpeechAct(str, Enum):
    """Primary speech acts a user message can represent."""
    GREETING = "greeting"
    FAREWELL = "farewell"
    QUESTION = "question"
    REQUEST = "request"
    STORY = "story"
    EMOTIONAL = "emotional"
    OPINION = "opinion"
    JOKE = "joke"
    FRUSTRATION = "frustration"
    CONFUSION = "confusion"
    CORRECTION = "correction"
    TOPIC_CHANGE = "topic_change"
    CONTINUATION = "continuation"
    CASUAL = "casual"
    STATEMENT = "statement"


class EmotionalTone(str, Enum):
    """Detected emotional tone of the message."""
    NEUTRAL = "neutral"
    POSITIVE = "positive"
    NEGATIVE = "negative"
    FRUSTRATED = "frustrated"
    EXCITED = "excited"
    CONFUSED = "confused"
    TIRED = "tired"
    ANGRY = "angry"
    SAD = "sad"
    HAPPY = "happy"


@dataclass
class Classification:
    """Result of speech act classification."""
    speech_act: SpeechAct
    emotional_tone: EmotionalTone
    confidence: float
    is_request: bool  # True if this is a request for action
    is_social: bool   # True if this is social/conversational (not instrumental)
    needs_context: bool  # True if this likely references prior context


# --- Pattern definitions ----------------------------------------------------

# Greeting patterns
_GREETING_RE = re.compile(
    r"^(hi|hey|hello|sup|yo|howdy|greetings|good\s+(morning|afternoon|evening)|what'?s\s+up|hola)\b",
    re.IGNORECASE,
)

# Farewell patterns
_FAREWELL_RE = re.compile(
    r"\b(bye|goodbye|see\s+ya|later|good\s+night|take\s+care|gotta\s+go|catch\s+you\s+later|talk\s+to\s+you)\b",
    re.IGNORECASE,
)

# Story/narrative patterns
_STORY_RE = re.compile(
    r"\b(anyway|then|and\s+then|you\s+won'?t\s+believe|guess\s+what|listen\s+to\s+this|ok\s+so|right\s+so|so\s+anyway|so\s+then|so\s+basically)\b|^so[\s,]",
    re.IGNORECASE,
)

# Question marker or question starter
_QUESTION_RE = re.compile(r"\?\s*$", re.IGNORECASE)
_QUESTION_STARTER_RE = re.compile(
    r"^(what|how|why|who|when|where|which|can|could|would|is|are|does|do|did|has|have|had|will|should|may)\b",
    re.IGNORECASE,
)

# Correction patterns
_CORRECTION_RE = re.compile(
    r"\b(no|actually|wait|that'?s\s+not|i\s+meant|correction|wrong|incorrect|hold\s+on|scratch\s+that)\b",
    re.IGNORECASE,
)

# Frustration patterns
_FRUSTRATION_RE = re.compile(
    r"\b(frustrat(ed|ing)?|annoyed|irritated|angry|mad|furious|pissed|ugh|hate|stupid|damn|crap|wtf|omg)\b",
    re.IGNORECASE,
)

# Confusion patterns
_CONFUSION_RE = re.compile(
    r"\b(confused|confusing|puzzled|lost|don'?t\s+understand|unclear|what\s+do\s+you\s+mean|i\s+don'?t\s+get|how\s+does|explain)\b",
    re.IGNORECASE,
)

# Joke patterns
_JOKE_RE = re.compile(
    r"\b(haha|lol|lmao|rofl|joke|pun|knock\s+knock|why\s+did|what\s+do\s+you\s+call)\b",
    re.IGNORECASE,
)

# Emotional expression patterns
_EMOTIONAL_RE = re.compile(
    r"\b(love|hate|amazing|awesome|terrible|horrible|incredible|fantastic|awful|best|worst|so\s+happy|so\s+sad)\b",
    re.IGNORECASE,
)

# Opinion patterns
_OPINION_RE = re.compile(
    r"\b(i\s+(think|believe|feel|guess|suppose|reckon)|in\s+my\s+opinion|personally|honestly)\b",
    re.IGNORECASE,
)

# Continuation/reference patterns (references prior context)
_CONTINUATION_RE = re.compile(
    r"\b(continue|keep\s+going|and\s+then|what\s+about|what\s+of|also|oh\s+and|don'?t\s+forget)\b",
    re.IGNORECASE,
)

# Topic change patterns
_TOPIC_CHANGE_RE = re.compile(
    r"\b(by\s+the\s+way|anyway|speaking\s+of|unrelated|different\s+topic|switching\s+gears|oh\s+wait)\b",
    re.IGNORECASE,
)

# Request patterns (action-oriented)
_REQUEST_RE = re.compile(
    r"\b(can\s+you|could\s+you|would\s+you|please|help\s+me|i\s+need|i\s+want|open|close|run|start|stop|search|find|show|tell|give|send|create|delete|move|copy|edit|write|read|check|look|find|get|fetch|download|upload|install|uninstall|update|fix|repair|build|compile|run|execute)\b",
    re.IGNORECASE,
)

# Tiredness patterns
_TIRED_RE = re.compile(
    r"\b(tired|exhausted|drained|worn\s+out|sleepy|fatigued|need\s+sleep|can'?t\s+keep\s+going)\b",
    re.IGNORECASE,
)


def classify(message: str, has_previous_context: bool = False) -> Classification:
    """Classify a user message into a speech act with emotional tone.

    Args:
        message: The user's message text
        has_previous_context: Whether there is prior conversation context

    Returns:
        Classification with speech_act, emotional_tone, confidence, and flags
    """
    if not message or not message.strip():
        return Classification(
            speech_act=SpeechAct.STATEMENT,
            emotional_tone=EmotionalTone.NEUTRAL,
            confidence=1.0,
            is_request=False,
            is_social=True,
            needs_context=False,
        )

    text = message.strip()
    text_lower = text.lower()

    # Score each category
    scores = {}

    # Greeting
    if _GREETING_RE.search(text_lower):
        scores[SpeechAct.GREETING] = 1.0

    # Farewell
    if _FAREWELL_RE.search(text_lower):
        scores[SpeechAct.FAREWELL] = 1.0

    # Story
    if _STORY_RE.search(text_lower):
        scores[SpeechAct.STORY] = 0.9

    # Question
    if _QUESTION_RE.search(text) or _QUESTION_STARTER_RE.search(text_lower):
        scores[SpeechAct.QUESTION] = 0.85

    # Correction
    if _CORRECTION_RE.search(text_lower):
        scores[SpeechAct.CORRECTION] = 0.9

    # Frustration
    if _FRUSTRATION_RE.search(text_lower):
        scores[SpeechAct.FRUSTRATION] = 0.85

    # Confusion
    if _CONFUSION_RE.search(text_lower):
        scores[SpeechAct.CONFUSION] = 0.85

    # Joke
    if _JOKE_RE.search(text_lower):
        scores[SpeechAct.JOKE] = 0.8

    # Emotional expression
    if _EMOTIONAL_RE.search(text_lower):
        scores[SpeechAct.EMOTIONAL] = 0.75

    # Opinion
    if _OPINION_RE.search(text_lower):
        scores[SpeechAct.OPINION] = 0.7

    # Continuation
    if _CONTINUATION_RE.search(text_lower):
        scores[SpeechAct.CONTINUATION] = 0.8

    # Topic change
    if _TOPIC_CHANGE_RE.search(text_lower):
        scores[SpeechAct.TOPIC_CHANGE] = 0.85

    # Request (action-oriented)
    if _REQUEST_RE.search(text_lower):
        scores[SpeechAct.REQUEST] = 0.7

    # Determine primary speech act
    if scores:
        speech_act = max(scores, key=scores.get)
        confidence = scores[speech_act]
    else:
        # Default based on context
        if has_previous_context:
            speech_act = SpeechAct.CONTINUATION
            confidence = 0.5
        else:
            speech_act = SpeechAct.STATEMENT
            confidence = 0.6

    # Detect emotional tone
    emotional_tone = _detect_emotional_tone(text_lower)

    # Override emotional speech act if strong emotion detected
    if emotional_tone in (EmotionalTone.FRUSTRATED, EmotionalTone.ANGRY) and speech_act not in (SpeechAct.GREETING, SpeechAct.FAREWELL):
        speech_act = SpeechAct.FRUSTRATION
        confidence = max(confidence, 0.8)
    elif emotional_tone == EmotionalTone.CONFUSED and speech_act not in (SpeechAct.GREETING, SpeechAct.FAREWELL):
        speech_act = SpeechAct.CONFUSION
        confidence = max(confidence, 0.8)

    # Determine flags
    is_request = bool(speech_act == SpeechAct.REQUEST or (speech_act in (SpeechAct.REQUEST, SpeechAct.QUESTION) and _REQUEST_RE.search(text_lower)))
    is_social = bool(speech_act in (SpeechAct.GREETING, SpeechAct.FAREWELL, SpeechAct.JOKE, SpeechAct.CASUAL, SpeechAct.STORY))
    needs_context = bool(speech_act in (SpeechAct.CONTINUATION, SpeechAct.CORRECTION) or has_previous_context)

    return Classification(
        speech_act=speech_act,
        emotional_tone=emotional_tone,
        confidence=confidence,
        is_request=is_request,
        is_social=is_social,
        needs_context=needs_context,
    )


def _detect_emotional_tone(text_lower: str) -> EmotionalTone:
    """Detect the emotional tone of a message."""
    # Check each emotion pattern
    if _TIRED_RE.search(text_lower):
        return EmotionalTone.TIRED
    if _FRUSTRATION_RE.search(text_lower):
        return EmotionalTone.FRUSTRATED
    if _CONFUSION_RE.search(text_lower):
        return EmotionalTone.CONFUSED

    # Positive emotions
    positive_re = re.compile(r"\b(happy|glad|good|nice|great|awesome|love|enjoy|fun|cool|amazing|fantastic|incredible|wow|yes|perfect|excellent)\b", re.IGNORECASE)
    if positive_re.search(text_lower):
        return EmotionalTone.POSITIVE

    # Negative emotions
    negative_re = re.compile(r"\b(sad|bad|terrible|horrible|awful|sucks|hate|worst|annoying|no|never|unfortunately|sadly)\b", re.IGNORECASE)
    if negative_re.search(text_lower):
        return EmotionalTone.NEGATIVE

    return EmotionalTone.NEUTRAL


def format_classification_for_context(cls: Classification) -> str:
    """Format classification as a context block for the model."""
    lines = ["[USER MESSAGE ANALYSIS]"]
    lines.append(f"Speech act: {cls.speech_act.value}")
    lines.append(f"Emotional tone: {cls.emotional_tone.value}")
    lines.append(f"Is request for action: {'yes' if cls.is_request else 'no'}")
    lines.append(f"Is social/conversational: {'yes' if cls.is_social else 'no'}")
    if cls.needs_context:
        lines.append("References prior context: yes")
    return "\n".join(lines)
