import json

import pytest

from backend.services.conversation_turn_engine import ConversationTurnEngine, ConversationTurnValidationError


def _reply(result) -> str:
    """Answers and runtime notices use separate response channels now."""
    return f"{getattr(result, 'assistant_text', '')} {getattr(result, 'status_text', '')}".strip()


def _empty_discovery(complete):
    payload = {
        "session_id": "empty-session",
        "available_count": 0,
        "returned_count": 0,
        "omitted_count": 0,
        "unresolved_discussion_count": 0,
        "unresolved_task_proof_count": 0,
        "candidate_discussion_ids": [],
        "discovery_complete": bool(complete),
        "reason": "complete" if complete else "unresolved_canonical_source",
        "representatives": [],
    }
    discovery_keys = (
        "available_count",
        "returned_count",
        "omitted_count",
        "unresolved_discussion_count",
        "unresolved_task_proof_count",
        "candidate_discussion_ids",
        "discovery_complete",
    )
    return {key: payload[key] for key in discovery_keys} | {
        "payload_json": json.dumps(payload)
    }


class ScriptedModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.responses:
            return json.dumps({"assistant_text": "ok", "commands": []})
        return self.responses.pop(0)


@pytest.mark.asyncio
async def test_empty_session_first_normal_reply():
    model = ScriptedModel(
        [json.dumps({"assistant_text": "Hello there.", "commands": []})]
    )
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn(
        "Hello", discussion_discovery=_empty_discovery(True)
    )
    assert result.commands == []
    assert _reply(result).startswith(
        "No indexed earlier discussions were found."
    )
    assert "Hello there." in _reply(result)
    assert len(model.calls) == 1
    assert "empty commands array" in model.calls[0][-1]["content"]


@pytest.mark.asyncio
async def test_empty_session_malicious_valid_command_stripped():
    model = ScriptedModel(
        [
            json.dumps(
                {
                    "assistant_text": "No commands were run.",
                    "commands": [{"type": "start_task", "request": "delete a file"}],
                }
            )
        ]
    )
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn(
        "Delete a file", discussion_discovery=_empty_discovery(True)
    )
    assert result.commands == []
    assert len(model.calls) == 1


@pytest.mark.asyncio
async def test_empty_session_incomplete_zero_calls():
    model = ScriptedModel([])
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn(
        "Hello", discussion_discovery=_empty_discovery(False)
    )
    assert result.commands == []
    assert not model.calls
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_empty_session_malformed_output_is_not_retried():
    model = ScriptedModel(["not json", json.dumps({"assistant_text": "ok", "commands": []})])
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn("Hello", discussion_discovery=_empty_discovery(True))
    assert result.commands == []
    assert len(model.calls) == 1
    assert "not json" in _reply(result)


@pytest.mark.asyncio
async def test_empty_session_prose_output_is_relaxed_without_retry():
    model = ScriptedModel(
        ["That is a nice story.", json.dumps({"assistant_text": "ok", "commands": []})]
    )
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn("Hello", discussion_discovery=_empty_discovery(True))
    assert result.commands == []
    assert len(model.calls) == 1
    assert "That is a nice story." in _reply(result)


@pytest.mark.asyncio
async def test_strict_default_unchanged():
    model = ScriptedModel([])
    engine = ConversationTurnEngine(model)
    result = await engine.run_turn(
        "Hello", discussion_discovery=_empty_discovery(True)
    )
    assert result.commands == []
    assert not model.calls
    assert engine.call_count == 0
    assert "verified discussion discovery" in _reply(result).lower()
