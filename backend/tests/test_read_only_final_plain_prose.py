"""Focused tests for read-only final recall plain-prose parsing."""

import json

import pytest

try:
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )
except ImportError:  # pragma: no cover
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )


class FakeModelClient:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        return self.responses.pop(0)


def _discovery_payload():
    return (
        '{"session_id":"s","available_count":1,"returned_count":1,'
        '"omitted_count":0,"unresolved_discussion_count":0,'
        '"unresolved_task_proof_count":0,"candidate_discussion_ids":["disc"],'
        '"discovery_complete":true,"reason":"complete",'
        '"representatives":[{"discussion_id":"disc"}]}'
    )


def _evidence():
    return {
        "session_id": "s",
        "requested_discussion_ids": ["disc"],
        "selected_discussion_ids": ["disc"],
        "discussion_count": 1,
        "entries": [{"discussion_id": "disc"}],
        "insufficient": False,
        "is_structurally_complete": True,
        "unresolved_member_count": 0,
        "event_unresolved_subject_count": 0,
        "has_role_text_truncation": False,
        "member_omitted_count": 0,
        "event_omitted_count": 0,
    }


def discovery():
    payload = json.loads(_discovery_payload())
    return DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )


@pytest.mark.asyncio
async def test_read_only_final_plain_prose_accepted_with_caveat():
    prose = "Based on the unverified evidence, Juniper prefers a yellow perch. This is unverified."
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            prose,
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    result = await engine.run_turn("What about my parrot?", discussion_discovery=discovery())
    assert result["commands"] == []
    assert "may be incomplete" in result["assistant_text"]
    assert "Juniper prefers a yellow perch" in result["assistant_text"]
    assert "unverified" in result["assistant_text"]
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_read_only_final_plain_prose_bounded():
    long_prose = "a" * (9000)
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            long_prose,
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    result = await engine.run_turn("q", discussion_discovery=discovery())
    assert len(result["assistant_text"]) <= 8000 + 100


@pytest.mark.asyncio
async def test_read_only_final_malformed_machine_payload_fails_closed():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            "{not valid json",
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "json_malformed"


@pytest.mark.asyncio
async def test_read_only_final_json_command_payload_validated_then_commands_dropped():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            json.dumps({"assistant_text": "answer", "commands": [{"type": "delete_file", "request": "x"}]}),
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    # command must validate against descriptors; no task descriptors => fails command_validation
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "command_validation"


@pytest.mark.asyncio
async def test_non_read_only_final_plain_prose_fails_json_malformed():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            "plain prose answer",
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=False)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "json_malformed"


@pytest.mark.asyncio
async def test_read_only_final_fenced_json_accepted():
    fenced = (
        "```json\n"
        + json.dumps({"assistant_text": "fenced answer", "commands": []})
        + "\n```"
    )
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            fenced,
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    result = await engine.run_turn("q", discussion_discovery=discovery())
    assert result["commands"] == []
    assert "fenced answer" in result["assistant_text"]
    assert result["assistant_text"].startswith(
        "This recall may be incomplete; other conversations or later corrections may exist.\n\n"
    )


@pytest.mark.asyncio
async def test_read_only_final_valid_command_payload_commands_are_dropped():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            json.dumps({"assistant_text": "guarded answer", "commands": [{"type": "start_task", "request": "do evil"}]}),
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    result = await engine.run_turn("q", discussion_discovery=discovery())
    assert result["commands"] == []
    assert "guarded answer" in result["assistant_text"]
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_read_only_final_no_retry_on_malformed_machine_payload():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            "{not valid json",
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "json_malformed"
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_read_only_final_empty_output_raises_empty():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            "   ",
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "empty"


@pytest.mark.asyncio
async def test_read_only_final_duplicate_json_key_raises_malformed():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            '{"assistant_text":"x","commands":[],"assistant_text":"y"}',
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "json_malformed"


@pytest.mark.asyncio
async def test_read_only_final_array_payload_raises_shape():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            '[{"assistant_text":"x","commands":[]}]',
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("q", discussion_discovery=discovery())
    assert exc.value.reason_code == "shape"


@pytest.mark.asyncio
async def test_read_only_final_deterministic_uncertainty_caveat():
    client = FakeModelClient(
        [
            json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}),
            "plain recall",
        ]
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _evidence(), read_only_discussion_recall=True)
    result = await engine.run_turn("q", discussion_discovery=discovery())
    assert result["assistant_text"] == (
        "This recall may be incomplete; other conversations or later corrections may exist.\n\nplain recall"
    )