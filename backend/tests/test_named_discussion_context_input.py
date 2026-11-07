import json

import pytest

try:
    from backend.services.conversation_turn_engine import ConversationTurnEngine
except ImportError:
    from services.conversation_turn_engine import ConversationTurnEngine


class FakeModel:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps({"assistant_text": "ok", "commands": []})


@pytest.mark.asyncio
async def test_named_discussion_contexts_are_visible_to_model():
    model = FakeModel()
    engine = ConversationTurnEngine(model)
    context = {
        "context_id": "ctx-123",
        "name": "Same named discussion",
        "session_id": "live-session",
        "exchange_id": "conv-1",
        "user_turn_id": 7,
        "assistant_turn_id": 8,
    }
    await engine.run("hello", [], [], discussion_contexts=[context])
    payload = json.loads(model.calls[0][1]["content"])
    assert payload["discussion_contexts"][0]["context_id"] == "ctx-123"
    assert payload["discussion_contexts"][0]["name"] == "Same named discussion"


@pytest.mark.asyncio
async def test_foreign_context_arrays_cannot_be_merged_by_shared_keys():
    model = FakeModel()
    engine = ConversationTurnEngine(model)
    contexts = [
        {"context_id": "ctx-a", "name": "Chess A", "session_id": "live-session"},
        {"context_id": "ctx-b", "name": "Chess B", "session_id": "live-session"},
    ]
    await engine.run("change plan", [], [], discussion_contexts=contexts)
    payload = json.loads(model.calls[0][1]["content"])
    assert [row["context_id"] for row in payload["discussion_contexts"]] == ["ctx-a", "ctx-b"]
import json

import pytest

from backend.services.conversation_turn_engine import ConversationTurnEngine, ConversationTurnValidationError


class FakeModel:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return json.dumps({"assistant_text": "ok", "commands": []})


@pytest.mark.asyncio
async def test_invalid_exact_identifiers_are_rejected_not_truncated():
    engine = ConversationTurnEngine(FakeModel())
    for context in ({"context_id": None, "name": "x"}, {"context_id": "", "name": "x"},
                    {"context_id": "x" * 4097, "name": "x"}):
        with pytest.raises(ConversationTurnValidationError):
            await engine.run("hi", [], [], discussion_contexts=[context])


@pytest.mark.asyncio
async def test_exact_identifiers_and_identical_names_are_preserved():
    model = FakeModel()
    engine = ConversationTurnEngine(model)
    contexts = [
        {"context_id": "ctx-one", "name": "Chess", "session_id": "live"},
        {"context_id": "ctx-two", "name": "Chess", "session_id": "live"},
    ]
    await engine.run("plan", [], [], discussion_contexts=contexts)
    payload = json.loads(model.calls[0][1]["content"])
    assert [row["context_id"] for row in payload["discussion_contexts"]] == ["ctx-one", "ctx-two"]
    assert [row["name"] for row in payload["discussion_contexts"]] == ["Chess", "Chess"]


import json as _json


@pytest.mark.asyncio
async def test_null_task_id_context_preserves_absence_and_reaches_model():
    model = FakeModel()
    engine = ConversationTurnEngine(model)
    context = {
        "context_id": "ctx-one",
        "name": "Chess",
        "session_id": "live",
        "exchange_id": "conv-1",
        "user_turn_id": 7,
        "assistant_turn_id": 8,
        "task_id": None,
        "task_state": None,
        "task_version": None,
        "original_request": "request",
        "normalized_goal": "goal",
        "final_result": "result",
        "created_at": "2026-01-01",
    }
    await engine.run("hello", [], [], discussion_contexts=[context])
    payload = _json.loads(model.calls[0][1]["content"])
    row = payload["discussion_contexts"][0]
    assert row["context_id"] == "ctx-one"
    assert row.get("task_id") is None
    assert "task_id" in row


@pytest.mark.asyncio
async def test_missing_and_null_task_id_are_preserved_not_merged_or_truncated():
    model = FakeModel()
    engine = ConversationTurnEngine(model)
    base = {
        "context_id": "ctx-a",
        "name": "A",
        "session_id": "live",
        "exchange_id": "conv-a",
        "user_turn_id": 1,
        "assistant_turn_id": 2,
        "created_at": "2026-01-01",
    }
    null_context = dict(base, task_id=None, name="null task")
    missing_context = dict(base, name="missing task")
    await engine.run("hello", [], [], discussion_contexts=[null_context, missing_context])
    payload = _json.loads(model.calls[0][1]["content"])
    rows = payload["discussion_contexts"]
    assert len(rows) == 2
    assert [row["name"] for row in rows] == ["null task", "missing task"]
    assert rows[0].get("task_id") is None
    assert rows[1].get("task_id") is None


@pytest.mark.asyncio
async def test_taskless_context_validates_non_task_identifiers_strictly():
    engine = ConversationTurnEngine(FakeModel())
    base = {
        "context_id": "ctx-one",
        "session_id": "live",
        "exchange_id": "conv-1",
        "user_turn_id": 7,
        "assistant_turn_id": 8,
        "task_id": None,
        "created_at": "2026-01-01",
    }
    for required in ("context_id", "session_id", "exchange_id"):
        for bad in (None, "", True, False):
            context = dict(base, **{required: bad})
            with pytest.raises(ConversationTurnValidationError):
                await engine.run("hi", [], [], discussion_contexts=[context])
    for bad in ("", True, False):
        context = dict(base, task_id=bad)
        with pytest.raises(ConversationTurnValidationError):
            await engine.run("hi", [], [], discussion_contexts=[context])