"""
Phase 14A: Action/Goal Classification Boundary Regression Tests.

Verifies:
1. Filesystem actions route to ACTION/GOAL, never CONVERSATION.
2. Non-actionable conversational statements about computers/files remain CONVERSATION.
3. Contextual conversation -> action transitions correctly switch from CONVERSATION to ACTION/GOAL.
4. Compound requests with actionable clauses route to ACTION/GOAL, never vanishing into CONVERSATION.
5. Deictic checks with explicit paths are NOT flagged as ambiguous (no false CLARIFICATION_REQUIRED).
6. Safety invariant: filesystem actions reach the Task / PolicyGate / ToolExecutor runtime.
"""

import pytest
from backend.cognitive.intent import IntentInterpreter
from backend.cognitive.state import (
    AmbiguityLevel,
    IntentCategory,
    InteractionType,
)


class TestFilesystemActionClassification:
    """Section 3: Filesystem actions must route to ACTION or GOAL."""

    @pytest.mark.parametrize("phrase", [
        r"List the files in C:\Ai\sythion.",
        r"Read C:\Ai\sythion\README.md.",
        r"Inspect the Python files in C:\Ai\sythion and summarize them.",
        r"Check whether C:\Ai\sythion\backend exists.",
        r"Tell me what files are inside C:\Users\Administrator\Documents.",
        r"Inspect the top-level directory at C:\Ai\sythion and summarize what you find.",
        r"Read the file C:\nonexistent_audit_test_99.txt and report its contents.",
        r"View C:\Ai\sythion\package.json.",
    ])
    def test_filesystem_actions_route_to_action_or_goal(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)

        assert interaction in (InteractionType.ACTION, InteractionType.GOAL), (
            f"Expected ACTION or GOAL for '{phrase}', got {interaction} (category={intent.category})"
        )
        assert interaction != InteractionType.CONVERSATION, (
            f"Filesystem action '{phrase}' must never be classified as CONVERSATION"
        )
        assert not intent.needs_clarification, (
            f"Explicit path in '{phrase}' must not trigger false deictic clarification"
        )


class TestNonActionableConversation:
    """Section 3: Non-actionable conversation about computers/files must remain CONVERSATION."""

    @pytest.mark.parametrize("phrase", [
        "Let's talk about my computer.",
        "What do you think about file systems?",
        "I was wondering how computers store files.",
        "Let's talk about what file systems are.",
        "I was thinking about reading more books.",
    ])
    def test_non_actionable_conversation_remains_conversation(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)

        assert interaction == InteractionType.CONVERSATION, (
            f"Expected CONVERSATION for '{phrase}', got {interaction} (category={intent.category})"
        )


class TestContextualActionTransition:
    """Section 4: Contextual conversation -> action transition across turns."""

    def test_laptop_to_battery_transition(self):
        interp = IntentInterpreter()

        # Turn 1: Conversational opening
        turn1 = "Let's talk about my laptop."
        intent1 = interp.interpret(turn1)
        itype1 = interp.determine_interaction(turn1, intent=intent1)
        assert itype1 == InteractionType.CONVERSATION

        # Turn 2: Action transition
        turn2 = "Actually, check its battery."
        context2 = {"in_conversation": True, "last_interaction": InteractionType.CONVERSATION.value}
        intent2 = interp.interpret(turn2, context=context2)
        itype2 = interp.determine_interaction(turn2, intent=intent2, context=context2)
        assert itype2 == InteractionType.ACTION

    def test_project_to_list_files_transition(self):
        interp = IntentInterpreter()

        # Turn 1: Conversational opening
        turn1 = "Let's talk about my project."
        intent1 = interp.interpret(turn1)
        itype1 = interp.determine_interaction(turn1, intent=intent1)
        assert itype1 == InteractionType.CONVERSATION

        # Turn 2: Action transition
        turn2 = "Actually, list the Python files in it."
        context2 = {"in_conversation": True, "last_interaction": InteractionType.CONVERSATION.value}
        intent2 = interp.interpret(turn2, context=context2)
        itype2 = interp.determine_interaction(turn2, intent=intent2, context=context2)
        assert itype2 in (InteractionType.ACTION, InteractionType.GOAL)


class TestCompoundRequests:
    """Section 5: Compound requests with actionable clauses must not vanish into CONVERSATION."""

    @pytest.mark.parametrize("phrase", [
        "Can you explain why the project is slow and inspect the backend directory?",
        "Tell me about this project, then list the Python files in the backend.",
        r"Explain what this file does and read C:\Ai\sythion\README.md.",
        r"Give me your thoughts on this, but first check C:\Ai\sythion\backend exists.",
    ])
    def test_compound_actionable_clauses_preserved(self, phrase: str):
        interp = IntentInterpreter()
        intent = interp.interpret(phrase)
        interaction = interp.determine_interaction(phrase, intent=intent)

        assert interaction in (InteractionType.ACTION, InteractionType.GOAL), (
            f"Compound request '{phrase}' must route to ACTION or GOAL, got {interaction}"
        )
        assert interaction != InteractionType.CONVERSATION


class TestSafetyInvariantNoBypass:
    """Section 6: Filesystem actions must never be routed to ConversationRuntime."""

    @pytest.mark.parametrize("phrase", [
        r"Inspect the top-level directory at C:\Ai\sythion and summarize what you find.",
        r"Read C:\Ai\sythion\README.md and tell me what it contains.",
        r"List all files in C:\Ai\sythion\backend.",
    ])
    def test_filesystem_actions_target_task_pipeline(self, phrase: str):
        interp = IntentInterpreter()
        itype = interp.determine_interaction(phrase)
        assert itype != InteractionType.CONVERSATION
        assert itype in (InteractionType.ACTION, InteractionType.GOAL)
