import json

import pytest

from backend.services.conversation_turn_engine import ConversationTurnEngine


class Model:
    def __init__(self):
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if len(self.calls) == 1:
            return json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
        return json.dumps({"assistant_text": "The exchange records a museum visit.", "commands": [{"type": "start_task", "request": "delete a file"}]})


def discovery(complete):
    payload = {"session_id": "s", "available_count": 1, "returned_count": 1,
               "omitted_count": 0, "unresolved_discussion_count": 0,
               "unresolved_task_proof_count": 0, "candidate_discussion_ids": ["disc"],
               "discovery_complete": complete, "reason": "complete" if complete else "unresolved_canonical_source",
               "representatives": [{"discussion_id": "disc"}]}
    return {key: payload[key] for key in (
        "available_count", "returned_count", "omitted_count",
        "unresolved_discussion_count", "unresolved_task_proof_count",
        "candidate_discussion_ids", "discovery_complete",
    )} | {"payload_json": json.dumps(payload)}


def evidence(ids):
    return {"session_id": "s", "requested_discussion_ids": ["disc"],
            "selected_discussion_ids": ["disc"], "discussion_count": 1,
            "entries": [{"discussion_id": "disc"}], "insufficient": False,
            "is_structurally_complete": True, "unresolved_member_count": 0,
            "event_unresolved_subject_count": 0, "has_role_text_truncation": False,
            "has_unverified_or_legacy_provenance": False, "member_omitted_count": 0,
            "event_omitted_count": 0}


@pytest.mark.asyncio
async def test_read_only_recall_suppresses_commands_and_adds_caveat():
    model = Model()
    engine = ConversationTurnEngine(model, discussion_evidence_reader=evidence,
                                    read_only_discussion_recall=True)
    result = await engine.run_turn("What did we discuss?", discussion_discovery=discovery(True))
    assert result.commands == []
    assert "recall may be incomplete" in result.assistant_text
    assert len(model.calls) == 2
    assert "do not assume the search is complete" in model.calls[0][-1]["content"]
    assert "return an empty commands list" in model.calls[1][-1]["content"]
    assert "one JSON object" in model.calls[1][-1]["content"]


@pytest.mark.asyncio
async def test_read_only_recall_does_not_bypass_incomplete_discovery():
    model = Model()
    engine = ConversationTurnEngine(model, discussion_evidence_reader=evidence,
                                    read_only_discussion_recall=True)
    result = await engine.run_turn("What did we discuss?", discussion_discovery=discovery(False))
    assert result.commands == []
    assert not model.calls


@pytest.mark.asyncio
async def test_read_only_recall_without_discovery_cannot_fall_back_to_actions():
    model = Model()
    engine = ConversationTurnEngine(model, read_only_discussion_recall=True)
    result = await engine.run_turn("Delete a file")
    assert result.commands == []
    assert not model.calls
