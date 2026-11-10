"""Conversation model instructions must not manufacture memory-write success.

These are prompt-contract tests, not proof that an external model always obeys
instructions. A verified memory save receipt / explicit write path is future
work; chat history and post-task consolidation are not synchronous evidence.
"""
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.conversation_turn_engine import ConversationTurnEngine


def test_conversation_runtime_no_unverified_memory_save_promise():
    prompt = ConversationRuntime._build_conversation_system_prompt(None)
    assert "not proof of a durable long-term memory entry" in prompt
    assert "verified" in prompt and "memory-write receipt" in prompt
    assert "cannot confirm persistent storage" in prompt


def test_assistant_turn_prompt_requires_receipt_for_memory_claims():
    # Exercise the real message builder but avoid constructing an engine with
    # external inference clients; the builder needs no instance state here.
    engine = ConversationTurnEngine.__new__(ConversationTurnEngine)
    messages = engine._build_messages("Please remember that I like tea", [], [])
    system = messages[0]["content"]
    assert "No memory-write receipt is supplied in this path" in system
    assert "persistent memory storage is not confirmed" in system
    assert "never claim it was saved" in system
