"""Opt-in labelled-discussion retrieval tests for ConversationTurnEngine."""

import asyncio
import json
import sqlite3
import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

try:
    from backend.services.conversation_turn_engine import (
        ConversationModelError,
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )
    from backend.models.database import Base, ConversationExchange, ConversationTurn
    from backend.services.conversation_runtime import ModelClient
    from backend.services.grounded_discussions import GroundedDiscussionManager
    from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
except ImportError:  # pragma: no cover - alternate import roots
    from services.conversation_turn_engine import (
        ConversationModelError,
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )
    from models.database import Base, ConversationExchange, ConversationTurn
    from services.conversation_runtime import ModelClient
    from services.grounded_discussions import GroundedDiscussionManager
    from services.grounded_discussion_discovery import read_session_discussion_discovery
    from services.grounded_discussion_evidence import read_selected_discussion_evidence


def _reply(result) -> str:
    """Genuine answers and runtime notices travel on separate channels now.

    These retrieval-validation tests only care about the human-facing text, so
    read both channels. Channel separation itself is covered by dedicated tests.
    """
    return f"{result.get('assistant_text', '')} {result.get('status_text', '')}".strip()


class FakeModelClient:
    def __init__(self, responses: list[Any] | None = None, exc: Exception | None = None):
        self.responses = list(responses or [])
        self.exc = exc
        self.calls: list[Any] = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.exc is not None:
            raise self.exc
        if not self.responses:
            return '{"assistant_text": "ok", "commands": []}'
        return self.responses.pop(0)


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _discovery_payload():
    return (
        '{"session_id":"s","available_count":1,"returned_count":1,'
        '"omitted_count":0,"unresolved_discussion_count":0,'
        '"unresolved_task_proof_count":0,"candidate_discussion_ids":["disc"],'
        '"discovery_complete":true,"reason":"complete",'
        '"representatives":[{"discussion_id":"disc"}]}'
    )

def _incomplete_discovery_payload():
    return (
        '{"session_id":"s","available_count":1,"returned_count":0,'
        '"omitted_count":1,"unresolved_discussion_count":0,'
        '"unresolved_task_proof_count":0,"candidate_discussion_ids":[],'
        '"discovery_complete":false,"reason":"payload_budget_exceeded",'
        '"representatives":[]}'
    )


def _seed_lantern(session, session_id: str):
    ex_id = "lantern-exchange"
    ut_id = 7
    at_id = 8
    session.add(ConversationExchange(
        id=ex_id, session_id=session_id,
        user_text="Project Lantern: one indoor museum visit, $9, no cafés.",
        assistant_text="Project Lantern: museum visit, $9, no cafés.",
    ))
    session.add(ConversationTurn(id=ut_id, session_id=session_id, role="user", summary="Lantern user"))
    session.add(ConversationTurn(id=at_id, session_id=session_id, role="assistant", summary="Lantern assistant"))
    session.flush()
    manager = GroundedDiscussionManager(session)
    discussion = manager.create_discussion(
        session_id=session_id,
        label="Project Lantern",
        label_provenance="human_stated",
        member_exchanges=[{
            "exchange_id": ex_id,
            "user_turn_id": ut_id,
            "assistant_turn_id": at_id,
        }],
        link_provenance="human_stated",
    )
    session.commit()
    return discussion.discussion_id


def _valid_reader_result(unverified: bool = False):
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
        "has_unverified_or_legacy_provenance": unverified,
        "member_omitted_count": 0,
        "event_omitted_count": 0,
    }


@pytest.mark.asyncio
async def test_default_path_preserves_existing_behavior():
    client = FakeModelClient(['{"assistant_text":"hi","commands":[]}'])
    engine = ConversationTurnEngine(client)
    result = await engine.run_turn("hello", recent_turns=[])
    assert _reply(result) == "hi"
    assert result["commands"] == []
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_incomplete_discovery_makes_no_provider_call():
    reader = lambda ids: _valid_reader_result()
    client = FakeModelClient()
    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader)
    discovery = DiscussionDiscoveryInput(
        payload_json=_incomplete_discovery_payload(),
        candidate_discussion_ids=(),
        available_count=1,
        returned_count=0,
        omitted_count=1,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=False,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_invalid_discovery_input_fails_before_provider_call(db_session):
    client = FakeModelClient()
    engine = ConversationTurnEngine(client)
    bad = DiscussionDiscoveryInput(payload_json="", candidate_discussion_ids=(), discovery_complete=True)
    with pytest.raises(ConversationTurnValidationError):
        await engine.run_turn("hello", discussion_discovery=bad)
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_resolver_clarification_is_terminal_with_one_call():
    payload = _discovery_payload()
    clarification = json.dumps({
        "outcome": "clarification",
        "selected_discussion_ids": [],
        "clarification": "I need to know which Lantern budget you mean.",
    })
    client = FakeModelClient([clarification])
    reader_calls = []
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: reader_calls.append(ids),
    )
    discovery = DiscussionDiscoveryInput(
        payload_json=payload,
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert _reply(result) == "I need to know which Lantern budget you mean."
    assert result["commands"] == []
    assert engine.call_count == 1
    assert reader_calls == []


@pytest.mark.asyncio
async def test_valid_selection_calls_reader_and_answers_with_two_calls():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    final = json.dumps({"assistant_text": "Lantern remains $9 with no cafés.", "commands": []})
    client = FakeModelClient([selected, final])
    reader_calls = []

    def reader(ids):
        reader_calls.append(tuple(ids))
        return _valid_reader_result()

    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert _reply(result) == "Lantern remains $9 with no cafés."
    assert result["commands"] == []
    assert engine.call_count == 2
    assert reader_calls == [("disc",)]


@pytest.mark.asyncio
@pytest.mark.parametrize("read_only", [True, False])
async def test_selected_recall_excludes_legacy_context_and_its_coverage_only_in_read_only_mode(read_only):
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected, '{"assistant_text":"answer","commands":[]}'])
    reader_calls = []
    def reader(ids):
        reader_calls.append(ids)
        return _valid_reader_result()

    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader, read_only_discussion_recall=read_only)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(), candidate_discussion_ids=("disc",),
        available_count=1, returned_count=1, omitted_count=0,
        unresolved_discussion_count=0, unresolved_task_proof_count=0, discovery_complete=True,
    )
    contexts = [{"context_id": "unselected", "text": "unselected legacy marker"}]
    coverage = {"available_count": 3, "returned_count": 1, "omitted_count": 2}
    turns = [{"role": "user", "content": "recent turn marker"}]
    result = await engine.run_turn("question", recent_turns=turns, discussion_discovery=discovery,
                                   discussion_contexts=contexts, discussion_context_coverage=coverage)
    payload = json.loads(client.calls[1][1]["content"])
    assert reader_calls == [("disc",)]
    assert payload["selected_discussion_evidence"]["selected_discussion_ids"] == ["disc"]
    assert payload["recent_turns"] == turns
    assert payload["discussion_contexts"] == ([] if read_only else contexts)
    assert payload["discussion_context_coverage"] == (None if read_only else coverage)
    assert result["commands"] == []
    if read_only:
        assert "may be incomplete" in _reply(result)


@pytest.mark.asyncio
async def test_resolver_malformed_json_does_not_call_final():
    client = FakeModelClient(["not json", '{"assistant_text":"x","commands":[]}'])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert exc.value.reason_code == "json_malformed"
    assert engine.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("selected", [
    ["unknown"],
    ["disc", "disc"],
    [" disc"],
    ["disc "],
])
async def test_resolver_closed_contract_rejects_bad_ids(selected):
    bad = json.dumps({
        "resolution": "selected",
        "assistant_text": "",
        "commands": [],
        "selected_discussion_ids": selected,
    })
    client = FakeModelClient([bad])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(ConversationTurnValidationError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_resolver_extra_keys_fail_whole_turn():
    bad = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
        "extra": 1,
    })
    client = FakeModelClient([bad])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(ConversationTurnValidationError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_transient_resolver_failure_propagates_without_final():
    request = httpx.Request("POST", "http://example")
    response = httpx.Response(503, request=request)
    exc = httpx.HTTPStatusError("unavailable", request=request, response=response)
    client = FakeModelClient(exc=exc)
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(ConversationModelError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_cancelled_reader_prevents_final_call():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: (_ for _ in ()).throw(asyncio.CancelledError()),
    )
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(asyncio.CancelledError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_operational_error_from_reader_propagates():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])

    def reader(ids):
        raise sqlite3.OperationalError("database is locked")

    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(sqlite3.OperationalError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"unresolved_member_count": 1},
    {"event_unresolved_subject_count": 1},
    {"has_role_text_truncation": True},
    {"member_omitted_count": 1},
    {"event_omitted_count": 1},
    {"is_structurally_complete": False},
])
async def test_structurally_insufficient_evidence_blocks_final_call(mutation):
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])
    result0 = _valid_reader_result()
    result0.update(mutation)
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: result0)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_unverified_but_structurally_complete_evidence_supports_recall():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    final = json.dumps({"assistant_text": "Recalled Lantern context, unverified.", "commands": []})
    client = FakeModelClient([selected, final])
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: _valid_reader_result(unverified=True),
    )
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert _reply(result) == "Recalled Lantern context, unverified."
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_current_request_not_shadowed_in_retrieval_calls():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    final = json.dumps({"assistant_text": "ok", "commands": []})
    client = FakeModelClient([selected, final])
    current = "New standalone request: summarize Lantern separately."
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    await engine.run_turn(current, discussion_discovery=discovery)
    resolver_request = json.loads(client.calls[0][1]["content"])["user_request"]
    final_request = json.loads(client.calls[1][1]["content"])["user_request"]
    assert resolver_request == current
    assert final_request == current


@pytest.mark.asyncio
async def test_final_foreign_keys_fail_and_consume_second_call():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    final_bad = json.dumps({"assistant_text": "ok", "commands": [], "foreign": 1})
    client = FakeModelClient([selected, final_bad])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(ConversationTurnValidationError) as exc:
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert exc.value.reason_code == "shape"
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_final_cancelled_prevents_success():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])

    async def chat(messages, **kwargs):
        client.calls.append(messages)
        if len(client.calls) == 1:
            return selected
        raise asyncio.CancelledError()

    client.chat = chat
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: _valid_reader_result())
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(asyncio.CancelledError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert len(client.calls) == 2


@pytest.mark.asyncio
async def test_real_discovery_and_evidence_readers_engine_path(db_session):
    sid = "lantern-session-" + str(uuid.uuid4())
    discussion_id = _seed_lantern(db_session, sid)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    assert discovery.discovery_complete is True
    assert discussion_id in discovery.candidate_discussion_ids

    resolver_text = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": [discussion_id],
        "clarification": None,
    })
    final_text = json.dumps({
        "assistant_text": "Project Lantern is an indoor museum visit for $9 with no cafés.",
        "commands": [],
    })

    client = FakeModelClient([resolver_text, final_text])

    def reader(ids):
        return read_selected_discussion_evidence(db_session, sid, list(ids))

    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader)
    result = await engine.run_turn(
        "What was Lantern?",
        discussion_discovery=discovery,
    )
    assert _reply(result) == "Project Lantern is an indoor museum visit for $9 with no cafés."
    assert result["commands"] == []
    assert engine.call_count == 2
    user_message = json.loads(client.calls[1][1]["content"])
    evidence = user_message["selected_discussion_evidence"]
    assert evidence["selected_discussion_ids"] == [discussion_id]
    assert evidence["has_unverified_or_legacy_provenance"] is False
    assert evidence["semantic_complete"] is False


@pytest.mark.asyncio
async def test_real_readers_with_unresolved_link_do_not_claim_latest_agreement(db_session):
    sid = "broken-session-" + str(uuid.uuid4())
    ex_id = "broken-exchange"
    db_session.add(ConversationExchange(
        id=ex_id, session_id=sid,
        user_text="old agreement",
        assistant_text="old answer",
    ))
    db_session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="old agreement"))
    db_session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="old answer"))
    db_session.commit()
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    assert discovery.candidate_discussion_ids == ()
    client = FakeModelClient([])
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: read_selected_discussion_evidence(db_session, sid, list(ids)),
    )
    result = await engine.run_turn("latest agreement?", discussion_discovery=discovery)
    assert result["commands"] == []
    assert engine.call_count == 0
    assert "enough verified discussion discovery" in _reply(result)


@pytest.mark.asyncio
async def test_contradictory_entry_structural_state_blocks_final_call():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])
    contradictory = {
        "session_id": "s",
        "requested_discussion_ids": ["disc"],
        "selected_discussion_ids": ["disc"],
        "discussion_count": 1,
        "entries": [
            {
                "discussion_id": "disc",
                "session_id": "s",
                "is_structurally_complete": False,
                "has_role_text_truncation": True,
                "member_coverage": {
                    "unresolved_member_count": 1,
                    "member_source_complete": False,
                    "member_omitted_count": 1,
                    "member_available_count": 1,
                    "member_returned_count": 0,
                    "event_available_count": 1,
                    "event_returned_count": 0,
                    "event_omitted_count": 1,
                    "event_unresolved_subject_count": 1,
                    "event_source_complete": False,
                },
                "members": [
                    {
                        "discussion_id": "disc",
                        "session_id": "s",
                        "source_available": False,
                        "user_text_truncated": True,
                        "assistant_text_truncated": True,
                        "canonical_user_text": "full user",
                        "user_text": "truncated",
                        "canonical_assistant_text": "full assistant",
                        "assistant_text": "truncated",
                    }
                ],
            }
        ],
        "insufficient": False,
        "is_structurally_complete": True,
        "unresolved_member_count": 0,
        "event_unresolved_subject_count": 0,
        "has_role_text_truncation": False,
        "has_unverified_or_legacy_provenance": False,
        "member_omitted_count": 0,
        "event_omitted_count": 0,
    }
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: contradictory)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_selected_id_inconsistency_blocks_final_call():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])
    inconsistent = _valid_reader_result()
    inconsistent["selected_discussion_ids"] = ["other"]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: inconsistent)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_evidence_gate_accepts_actual_production_result_object(db_session):
    sid = "lantern-production-" + str(uuid.uuid4())
    discussion_id = _seed_lantern(db_session, sid)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    evidence = read_selected_discussion_evidence(db_session, sid, [discussion_id])
    class _Model:
        def chat(self, messages, **kwargs):
            return '{"assistant_text":"ok","commands":[]}'
    engine = ConversationTurnEngine(_Model())
    assert engine._evidence_is_structurally_sufficient(evidence) is True
    assert evidence.selected_discussion_ids == (discussion_id,)
    assert evidence.session_id == sid


def _valid_strict_reader_base():
    base = _valid_reader_result()
    base.update({
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "member_available_count": 1,
        "member_returned_count": 1,
        "event_available_count": 1,
        "event_returned_count": 1,
        "member_source_complete": False,
        "event_source_complete": False,
    })
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "is_structurally_complete": True,
        "has_role_text_truncation": False,
        "member_coverage": {
            "member_available_count": 1,
            "member_returned_count": 1,
            "member_omitted_count": 0,
            "unresolved_member_count": 0,
            "member_source_complete": True,
            "event_available_count": 1,
            "event_returned_count": 1,
            "event_omitted_count": 0,
            "event_unresolved_subject_count": 0,
            "event_source_complete": True,
            "link_count": 0,
        },
        "event_coverage": {
            "event_available_count": 1,
            "event_returned_count": 1,
            "event_omitted_count": 0,
            "event_unresolved_subject_count": 0,
            "event_source_complete": True,
            "member_available_count": 1,
            "member_returned_count": 1,
            "member_omitted_count": 0,
            "unresolved_member_count": 0,
            "member_source_complete": True,
            "link_count": 0,
        },
    }]
    return base


@pytest.mark.asyncio
@pytest.mark.parametrize("field", [
    "member_available_count",
    "member_returned_count",
    "event_available_count",
    "event_returned_count",
])
async def test_supplied_count_invalid_does_not_pass_structural_gate(field):
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_strict_reader_base()
    base["member_source_complete"] = True
    base["event_source_complete"] = True
    # Make every coverage copy use valid true bool harmonization above.
    if field.startswith("member_"):
        base[field] = True
        base["entries"][0]["member_coverage"][field] = True
    else:
        base[field] = True
        base["entries"][0]["event_coverage"][field] = True
        base["entries"][0]["member_coverage"][field] = True
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_aggregate_reconciliation_invalid_per_field_blocks_final_call():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    # Aggregate invalid but entries valid; top-level strict rejects.
    for field in ("member_available_count", "member_returned_count", "event_available_count", "event_returned_count"):
        client = FakeModelClient([selected])
        base = _valid_strict_reader_base()
        base["member_source_complete"] = True
        base["event_source_complete"] = True
        base[field] = True
        engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
        discovery = DiscussionDiscoveryInput(
            payload_json=_discovery_payload(),
            candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
            omitted_count=0, unresolved_discussion_count=0,
            unresolved_task_proof_count=0, discovery_complete=True,
        )
        result = await engine.run_turn("hello", discussion_discovery=discovery)
        assert result["commands"] == []
        assert "structurally complete evidence" in _reply(result)
        assert engine.call_count == 1


@pytest.mark.asyncio
async def test_real_multi_discussion_shared_source_is_structurally_sufficient(db_session):
    import uuid
    from backend.services.grounded_discussions import GroundedDiscussionManager
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
    sid = "shared-production-" + str(uuid.uuid4())
    discussion_id = _seed_lantern(db_session, sid)
    # Create a second discussion reusing the same exact source exchange. Shared
    # discussions legitimately duplicate aggregate counts.
    manager = GroundedDiscussionManager(db_session)
    second = manager.create_discussion(
        session_id=sid,
        label="Second Lantern lens",
        label_provenance="human_stated",
        member_exchanges=[{
            "exchange_id": "lantern-exchange",
            "user_turn_id": 7,
            "assistant_turn_id": 8,
        }],
        link_provenance="human_stated",
    )
    db_session.commit()
    evidence = read_selected_discussion_evidence(db_session, sid, [discussion_id, second.discussion_id])
    class _Model:
        def chat(self, messages, **kwargs):
            return '{"assistant_text":"ok","commands":[]}'
    engine = ConversationTurnEngine(_Model())
    assert engine._evidence_is_structurally_sufficient(evidence) is True
    assert len(evidence.entries) == 2
    # Aggregate totals are exact sums even though one exchange is shared.
    assert evidence.member_available_count == 2
    assert evidence.discussion_count == 2


@pytest.mark.asyncio
async def test_real_unverified_provenance_still_passes_structural_gate(db_session):
    import uuid
    from backend.services.grounded_discussions import GroundedDiscussionManager
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
    sid = "unverified-production-" + str(uuid.uuid4())
    ex_id = "unverified-exchange"
    ut_id = 9
    at_id = 10
    db_session.add(ConversationExchange(id=ex_id, session_id=sid, user_text="Lantern correction: keep unverified history exact.", assistant_text="Lantern assistant exact."))
    db_session.add(ConversationTurn(id=ut_id, session_id=sid, role="user", summary="Lantern user"))
    db_session.add(ConversationTurn(id=at_id, session_id=sid, role="assistant", summary="Lantern assistant"))
    db_session.flush()
    manager = GroundedDiscussionManager(db_session)
    discussion = manager.create_discussion(
        session_id=sid,
        label="Unverified Project Lantern",
        label_provenance="legacy_unverified",
        member_exchanges=[{
            "exchange_id": ex_id,
            "user_turn_id": ut_id,
            "assistant_turn_id": at_id,
        }],
        link_provenance="legacy_unverified",
    )
    db_session.commit()
    evidence = read_selected_discussion_evidence(db_session, sid, [discussion.discussion_id])
    class _Model:
        def chat(self, messages, **kwargs):
            return '{"assistant_text":"ok","commands":[]}'
    engine = ConversationTurnEngine(_Model())
    assert engine._evidence_is_structurally_sufficient(evidence) is True
    assert evidence.has_unverified_or_legacy_provenance is True
    assert evidence.insufficient is True
    assert evidence.semantic_complete is False


@pytest.mark.asyncio
@pytest.mark.parametrize("coverage_field", ["member_coverage", "event_coverage"])
async def test_invalid_supplied_coverage_container_does_not_pass_structural_gate(coverage_field):
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base.update({
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "member_available_count": 1,
        "member_returned_count": 1,
        "event_available_count": 1,
        "event_returned_count": 1,
        "member_source_complete": False,
        "event_source_complete": False,
    })
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "is_structurally_complete": True,
        "has_role_text_truncation": False,
        coverage_field: [1, 2],
    }]
    base["member_source_complete"] = True
    base["event_source_complete"] = True
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_conflicting_aggregate_count_escapes_reconciliation_rejected():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base.update({
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "member_available_count": 999,
        "member_returned_count": 0,
        "event_available_count": 999,
        "event_returned_count": 0,
        "member_source_complete": True,
        "event_source_complete": True,
    })
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "is_structurally_complete": True,
        "has_role_text_truncation": False,
        "member_coverage": [1, 2],
        "event_coverage": [3, 4],
    }]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_unverified_semantic_recall_with_real_style_shape_supports_attribution():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    final = json.dumps({"assistant_text": "Attributed recall from unverified evidence.", "commands": []})
    client = FakeModelClient([selected, final])
    base = _valid_reader_result()
    base["has_unverified_or_legacy_provenance"] = True
    base["insufficient"] = True
    base["semantic_complete"] = False
    base["untrusted"] = True
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert _reply(result) == "Attributed recall from unverified evidence."
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_aggregate_false_source_completeness_blocks_final_call():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base["member_source_complete"] = False
    base["event_source_complete"] = False
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_bool_nested_coverage_counts_do_not_pass_structural_gate():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "member_coverage": {"unresolved_member_count": True, "member_omitted_count": 0, "member_source_complete": False},
        "event_coverage": {"event_unresolved_subject_count": True, "event_omitted_count": 0, "event_source_complete": False},
    }]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_invalid_or_unidentified_entries_do_not_pass_selected_ids():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    for entries in ([None], [{}]):
        client = FakeModelClient([selected])
        base = _valid_reader_result()
        base["entries"] = entries
        engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
        discovery = DiscussionDiscoveryInput(
            payload_json=_discovery_payload(),
            candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
            omitted_count=0, unresolved_discussion_count=0,
            unresolved_task_proof_count=0, discovery_complete=True,
        )
        result = await engine.run_turn("hello", discussion_discovery=discovery)
        assert result["commands"] == []
        assert "structurally complete evidence" in _reply(result)


@pytest.mark.asyncio
async def test_available_source_requires_exact_canonical_projected_pair():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "members": [{
            "discussion_id": "disc",
            "session_id": "s",
            "source_available": True,
            "canonical_user_text": "full user",
            "user_text": "projection",
            "canonical_assistant_text": "full assistant",
            "assistant_text": "full assistant",
            "user_text_truncated": False,
            "assistant_text_truncated": False,
        }],
    }]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_false_event_target_source_completeness_blocks_final_call():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "events": [{"event_id": 1, "target_source_complete": False}],
    }]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_reconciled_entry_coverage_total_mismatch_blocks_final_call():
    selected = json.dumps({"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None})
    client = FakeModelClient([selected])
    base = _valid_reader_result()
    base.update({
        "member_available_count": 1,
        "member_returned_count": 1,
        "member_omitted_count": 0,
        "unresolved_member_count": 0,
        "event_available_count": 1,
        "event_returned_count": 1,
        "event_omitted_count": 0,
        "event_unresolved_subject_count": 0,
    })
    base["entries"] = [{
        "discussion_id": "disc",
        "session_id": "s",
        "member_coverage": {
            "member_available_count": 2,
            "member_returned_count": 2,
            "member_omitted_count": 0,
            "unresolved_member_count": 0,
            "member_source_complete": True,
            "event_available_count": 2,
            "event_returned_count": 2,
            "event_omitted_count": 0,
            "event_unresolved_subject_count": 0,
            "event_source_complete": True,
        },
        "event_coverage": {
            "event_available_count": 2,
            "event_returned_count": 2,
            "event_omitted_count": 0,
            "event_unresolved_subject_count": 0,
            "event_source_complete": True,
        },
    }]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
    discovery = DiscussionDiscoveryInput(
        payload_json=_discovery_payload(),
        candidate_discussion_ids=("disc",), available_count=1, returned_count=1,
        omitted_count=0, unresolved_discussion_count=0,
        unresolved_task_proof_count=0, discovery_complete=True,
    )
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1
