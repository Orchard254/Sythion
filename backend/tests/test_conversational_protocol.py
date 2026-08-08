"""
Unit tests for the conversational protocol (Phase 3 speech act classifier and Phase 4 response strategy).
"""

import pytest
from backend.services.speech_act_classifier import classify, SpeechAct, EmotionalTone
from backend.services.conversation_state import ConversationStateData
from backend.services.response_strategy import generate_strategy


def test_speech_act_classification():
    # Greetings
    cls = classify("hello there")
    assert cls.speech_act == SpeechAct.GREETING
    assert cls.is_social is True
    assert cls.is_request is False

    # Exhausted/tired
    cls = classify("I'm so tired and exhausted from work")
    assert cls.emotional_tone == EmotionalTone.TIRED
    assert cls.speech_act == SpeechAct.STATEMENT

    # Question/Request
    cls = classify("can you look up the weather?")
    assert cls.speech_act == SpeechAct.QUESTION
    assert cls.is_request is True

    # Corrections
    cls = classify("no actually that is not what I wanted")
    assert cls.speech_act == SpeechAct.CORRECTION
    assert cls.needs_context is True


def test_response_strategy_generation():
    # Exhaustion strategy
    cls = classify("I am exhausted")
    state = ConversationStateData(
        session_id="test_sess",
        active_topic=None,
        previous_topics=[],
        open_threads=[],
        emotional_context="tired",
        conversation_stage="developing",
    )
    strat = generate_strategy(cls, state)
    assert strat.primary_goal == "ACKNOWLEDGE"
    assert strat.emotional_stance == "EMPATHETIC"
    assert "Validate their exhaustion" in strat.action_plan
    assert strat.allowed_to_ask is True

    # Greeting strategy
    cls_greeting = classify("hi")
    strat_greeting = generate_strategy(cls_greeting, state)
    assert strat_greeting.primary_goal == "ACKNOWLEDGE"
    assert strat_greeting.allowed_to_ask is False
    assert "Never ask 'How can I help you today?'" in strat_greeting.suggested_posture


def test_format_strategy_for_context():
    from backend.services.response_strategy import format_strategy_for_context
    cls = classify("hi")
    state = ConversationStateData(
        session_id="test_sess",
        active_topic=None,
        previous_topics=[],
        open_threads=[],
        emotional_context="neutral",
        conversation_stage="opening",
    )
    strat = generate_strategy(cls, state)
    formatted = format_strategy_for_context(strat)
    assert "[RESPONSE STRATEGY (ADHERE STRICTLY)]" in formatted
    assert "Primary Goal: ACKNOWLEDGE" in formatted
    assert "DO NOT ASK ANY QUESTIONS" in formatted
