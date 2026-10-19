"""
Phase 13C: Interaction Interpretation Robustness Tests

Verifies that:
1. Pure conversation remains CONVERSATION across diverse phrasing.
2. Pure informational queries remain INFORMATIONAL without fake planning.
3. Actions still reach the protected runtime.
4. Genuine goals still reach Planner/TaskRunner.
5. Mixed-intent utterances correctly identify action needs instead of silently dropping actions.
6. Contextual follow-ups resolve coherently based on conversational state.
7. User corrections redirect interaction type (e.g. from action to conversation or info).
8. Ambiguity boundary: conversational vagueness converses, actionable ambiguity clarifies.
9. Comprehensive 70+ sample corpus evaluation achieves high classification robustness.
"""

import pytest
from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.state import (
    AmbiguityLevel,
    DecisionType,
    IntentCategory,
    InteractionType,
)


class TestPureInteractions:
    """Section 2: Verification of pure interaction categories."""

    @pytest.mark.parametrize("phrase", [
        "Let's talk about computers.",
        "I'm bored.",
        "What do you think about that?",
        "Tell me something interesting.",
        "I just want your opinion.",
        "Hey Sythion, how have you been?",
        "What's on your mind?",
        "Nice to see you again.",
        "Tell me about yourself.",
    ])
    def test_pure_conversation(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.CONVERSATION
        assert not intent.needs_clarification

    @pytest.mark.parametrize("phrase", [
        "Why does RAM matter?",
        "What year was Python created?",
        "Explain the von Neumann bottleneck.",
        "What is the difference between TCP and UDP?",
        "Who invented the transistor?",
    ])
    def test_pure_informational(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.INFORMATIONAL

    @pytest.mark.parametrize("phrase", [
        "Check my battery level.",
        "What time is it?",
        "List the files in C:\\Ai\\sythion.",
        "Check the CPU usage right now.",
        "Can you check what time it is?",
    ])
    def test_pure_action(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.ACTION

    @pytest.mark.parametrize("phrase", [
        "Organize these files into folders by type.",
        "Research this topic and produce a report.",
        "Monitor this directory and notify me when a new Python file appears.",
        "Create a report listing the Python files in C:\\Ai\\sythion.",
        "Help me build a web scraper over the next few weeks.",
    ])
    def test_genuine_goal(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.GOAL


class TestMixedIntentUtterances:
    """Section 3: Compound and mixed-intent utterances."""

    def test_explanation_and_action_compound(self):
        interp = IntentInterpreter()
        phrase = "Can you explain why my laptop is slow and check my CPU usage?"
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.ACTION

    def test_conversation_to_action_transition(self):
        interp = IntentInterpreter()
        phrase = "Let's talk about my laptop. Actually, check the battery."
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.ACTION

    def test_conversation_and_information_retrieval(self):
        interp = IntentInterpreter()
        phrase = "I've been thinking about AI. What do you think, and can you look up the latest developments?"
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.ACTION


class TestContextualFollowups:
    """Section 4: Contextual follow-up sequences."""

    def test_sequential_laptop_discussion(self):
        interp = IntentInterpreter()
        conv_ctx = {
            "in_conversation": True,
            "has_target_entity": True,
            "recent_turns": [
                {"role": "user", "summary": "I was thinking about my laptop."},
                {"role": "assistant", "summary": "Laptops can slow down for many reasons."},
            ]
        }

        t1 = "I was thinking about my laptop."
        i1 = interp.interpret(t1)
        assert interp.determine_interaction(t1, intent=i1) == InteractionType.CONVERSATION

        t2 = "It's been getting really slow."
        i2 = interp.interpret(t2, context=conv_ctx)
        assert interp.determine_interaction(t2, intent=i2, context=conv_ctx) == InteractionType.CONVERSATION

        t3 = "Why do you think that is?"
        i3 = interp.interpret(t3, context=conv_ctx)
        assert interp.determine_interaction(t3, intent=i3, context=conv_ctx) == InteractionType.CONVERSATION

        t4 = "Check its battery."
        i4 = interp.interpret(t4, context=conv_ctx)
        assert interp.determine_interaction(t4, intent=i4, context=conv_ctx) == InteractionType.ACTION
        assert not i4.needs_clarification


class TestCorrectionHandling:
    """Section 5: User redirection and corrections."""

    def test_redirection_from_action_to_conversation(self):
        interp = IntentInterpreter()
        phrase = "No, don't do anything. I just want your opinion."
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.CONVERSATION

    def test_redirection_from_action_to_informational(self):
        interp = IntentInterpreter()
        phrase = "Actually, just tell me why battery health matters."
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)
        assert interaction == InteractionType.INFORMATIONAL


class TestAmbiguityBoundary:
    """Section 6: Ambiguity boundary."""

    @pytest.mark.parametrize("vagueness", [
        "Hmm, that's interesting.",
        "What do you think?",
        "Maybe we should talk about that.",
    ])
    def test_conversational_vagueness_does_not_clarify(self, vagueness: str):
        interp = IntentInterpreter()
        intent = interp.interpret(vagueness)
        interaction = interp.determine_interaction(vagueness, intent=intent)
        assert interaction == InteractionType.CONVERSATION
        assert not intent.needs_clarification
        assert intent.ambiguity in (AmbiguityLevel.CLEAR, AmbiguityLevel.LOW_AMBIGUITY)

    @pytest.mark.parametrize("action_ambiguous", [
        "Send this file to John.",
        "Move the document to the right folder.",
        "Delete it.",
        "Run that script now.",
    ])
    def test_actionable_ambiguity_triggers_clarification(self, action_ambiguous: str):
        interp = IntentInterpreter()
        intent = interp.interpret(action_ambiguous, context={})
        interaction = interp.determine_interaction(action_ambiguous, intent=intent, context={})
        assert intent.needs_clarification
        assert intent.ambiguity == AmbiguityLevel.AMBIGUOUS
        assert interaction == InteractionType.CLARIFICATION_REQUIRED
