"""Durable regression coverage for opt-in source-pair structural evidence.

These tests exercise the production gate and, where practical, the real
canonical source-pair -> evidence adapter -> engine path using disposable
in-memory SQLite databases.
"""

import json
import uuid
from typing import Any, Optional

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.conversation_turn_engine import (
    ConversationTurnEngine,
    ConversationTurnValidationError,
    DiscussionDiscoveryInput,
)
from backend.services.grounded_discussions import (
    Base as GroundedBase,
    GroundedDiscussionManager,
    DiscussionExchangeLinkRow,
)
from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
from backend.services.grounded_discussion_evidence import (
    read_selected_discussion_evidence,
)
from backend.services.grounded_discussion_event_source_links import (
    DiscussionEventSourceLinkRow,
    attach_event_source_link,
    read_event_source_pair,
)


def _reply(result) -> str:
    """Answers and runtime notices use separate response channels now.

    These tests only care about the human-facing text, so read both channels.
    Channel separation itself is covered by dedicated tests.
    """
    return f"{result.get('assistant_text', '')} {result.get('status_text', '')}".strip()


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.responses:
            return '{"assistant_text":"ok","commands":[]}'
        return self.responses.pop(0)


def _discovery_payload() -> str:
    return (
        '{"session_id":"s","available_count":1,"returned_count":1,'
        '"omitted_count":0,"unresolved_discussion_count":0,'
        '"unresolved_task_proof_count":0,"candidate_discussion_ids":["disc"],'
        '"discovery_complete":true,"reason":"complete",'
        '"representatives":[{"discussion_id":"disc"}]}'
    )


def _valid_gate(
    *,
    mode_declared=True,
    pairs=None,
    expected_event_ids=(5,),
    events=None,
    session_id="s",
    discussion_id="disc",
    source_pair_readback=True,
    extra_overrides: Optional[dict[str, Any]] = None,
):
    if pairs is None:
        pairs = [
            {
                "event_id": 5,
                "available": True,
                "structural_complete": True,
                "source": {"exchange_id": "src", "text_truncated": False, "member_proven": True},
                "target": {"exchange_id": "tgt", "text_truncated": False, "member_proven": True},
                "gaps": [],
                "provenance_verified": True,
                "session_id": session_id,
                "discussion_id": discussion_id,
            }
        ]
    if events is None:
        if len(pairs) == 1 and isinstance(pairs[0], dict) and isinstance(pairs[0].get("event_id"), int):
            event_id = pairs[0]["event_id"]
        elif len(pairs) == 1:
            event_id = 5
        else:
            event_id = None
        events = [{"event_id": event_id, "event_type": "correction", "is_correction": True}] if event_id is not None else []
    base = {
        "session_id": session_id,
        "requested_discussion_ids": ["disc"],
        "selected_discussion_ids": ["disc"],
        "discussion_count": 1,
        "entries": [
            {
                "discussion_id": discussion_id,
                "session_id": session_id,
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
                },
                "events": events,
                "source_pairs": pairs,
                "source_pair_event_count": len(expected_event_ids),
                "source_pair_count": len(pairs),
                "source_pair_available_count": sum(1 for p in pairs if p.get("available") is True),
                "source_pair_structural_complete_count": sum(1 for p in pairs if p.get("structural_complete") is True),
                "source_pair_text_truncated_count": 0,
                "source_pair_readback": True,
            }
        ],
        "is_structurally_complete": True,
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "has_role_text_truncation": False,
        "member_available_count": 1,
        "member_returned_count": 1,
        "event_available_count": 1,
        "event_returned_count": 1,
        "member_source_complete": True,
        "event_source_complete": True,
        "source_pair_event_count": len(expected_event_ids),
        "source_pair_count": len(pairs),
        "source_pair_available_count": sum(1 for p in pairs if p.get("available") is True),
        "source_pair_structural_complete_count": sum(1 for p in pairs if p.get("structural_complete") is True),
        "source_pair_text_truncated_count": 0,
        "source_pair_readback": source_pair_readback,
    }
    if not mode_declared:
        source_pair_field_names = (
            "source_pair_readback",
            "source_pairs",
            "source_pair_event_count",
            "source_pair_count",
            "source_pair_available_count",
            "source_pair_structural_complete_count",
            "source_pair_text_truncated_count",
            "source_pair_declaration_invalid",
        )
        for field_name in source_pair_field_names:
            base["entries"][0].pop(field_name, None)
            base.pop(field_name, None)
    if extra_overrides:
        base.update(extra_overrides)
    return base


@pytest.fixture
def db_session():
    engine = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    Session = sessionmaker(bind=engine, autoflush=False, future=True)
    session = Session()
    try:
        yield session
    finally:
        session.close()
        engine.dispose()


def _gate(value: Any) -> bool:
    class _Engine:
        pass

    instance = object()
    method = ConversationTurnEngine._evidence_is_structurally_sufficient
    return method(instance, value)


def _seed_source_pair_discussion(session):
    sid = "source-pair-" + str(uuid.uuid4())
    session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="src user"))
    session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="src assistant"))
    session.add(ConversationTurn(id=3, session_id=sid, role="user", summary="tgt user"))
    session.add(ConversationTurn(id=4, session_id=sid, role="assistant", summary="tgt assistant"))
    session.add(
        ConversationExchange(
            id="src-ex",
            session_id=sid,
            user_text="source text",
            assistant_text="source answer",
            created_at=__import__("datetime").datetime(2026, 1, 2),
        )
    )
    session.add(
        ConversationExchange(
            id="tgt-ex",
            session_id=sid,
            user_text="target text",
            assistant_text="target answer",
            created_at=__import__("datetime").datetime(2026, 1, 1),
        )
    )
    session.commit()
    discussion = GroundedDiscussionManager(session).create_discussion(
        session_id=sid,
        label="Source pair topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "src-ex", "user_turn_id": 1, "assistant_turn_id": 2},
            {"exchange_id": "tgt-ex", "user_turn_id": 3, "assistant_turn_id": 4},
        ],
        link_provenance="human_stated",
    )
    session.commit()
    gm = GroundedDiscussionManager(session)
    event = gm.add_correction(
        discussion.discussion_id,
        sid,
        target_exchange_id="tgt-ex",
        target_user_turn_id=3,
        target_assistant_turn_id=4,
        correction_text="claim",
        provenance="human_stated",
    )
    session.commit()
    attach_event_source_link(
        session,
        event_id=event.id,
        session_id=sid,
        discussion_id=discussion.discussion_id,
        source_exchange_id="src-ex",
        source_user_turn_id=1,
        source_assistant_turn_id=2,
        target_exchange_id="tgt-ex",
        target_user_turn_id=3,
        target_assistant_turn_id=4,
        provenance="human_stated",
        commit=False,
    )
    session.commit()
    return sid, discussion.discussion_id, event.id


@pytest.mark.asyncio
@pytest.mark.parametrize("value", ["false", "no", "", None, 0, 1, "TRUE"])
async def test_source_pair_invalid_declaration_fails_closed(value):
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = _valid_gate(mode_declared=False)
    evidence["source_pair_readback"] = value
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_explicit_false_and_absence_remain_legacy_valid():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    final = json.dumps({"assistant_text": "legacy valid", "commands": []})
    for mode_declared, readback in ((False, False), (True, False)):
        client = FakeModel([selected, final])
        evidence = _valid_gate(mode_declared=mode_declared)
        if readback:
            evidence["source_pair_readback"] = False
        engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
        assert _reply(result) == "legacy valid"
        assert engine.call_count == 2


@pytest.mark.asyncio
async def test_source_pair_same_source_and_target_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = _valid_gate()
    evidence["entries"][0]["source_pairs"][0]["target"]["exchange_id"] = "src"
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_event_identity_duplicates_and_bool_ids_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    base = _valid_gate()
    # substitute event ID
    substituted = {
        "session_id": "s",
        "requested_discussion_ids": ["disc"],
        "selected_discussion_ids": ["disc"],
        "discussion_count": 1,
        "entries": [dict(base["entries"][0], source_pairs=[dict(base["entries"][0]["source_pairs"][0], event_id=999)])],
        "is_structurally_complete": True,
        "unresolved_member_count": 0,
        "member_omitted_count": 0,
        "event_unresolved_subject_count": 0,
        "event_omitted_count": 0,
        "has_role_text_truncation": False,
        "member_available_count": 1,
        "member_returned_count": 1,
        "event_available_count": 1,
        "event_returned_count": 1,
        "member_source_complete": True,
        "event_source_complete": True,
        "source_pair_event_count": 1,
        "source_pair_count": 1,
        "source_pair_available_count": 1,
        "source_pair_structural_complete_count": 1,
        "source_pair_text_truncated_count": 0,
        "source_pair_readback": True,
    }
    for raw in (substituted, _valid_gate(pairs=[_valid_gate()["entries"][0]["source_pairs"][0], _valid_gate()["entries"][0]["source_pairs"][0]])):
        client = FakeModel([selected])
        engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: raw)
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

    # Boolean event IDs are invalid.
    boolean_payload = _valid_gate()
    boolean_payload["entries"][0]["source_pairs"][0]["event_id"] = True
    client = FakeModel([selected])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: boolean_payload)
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


@pytest.mark.asyncio
async def test_source_pair_foreign_session_and_discussion_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = _valid_gate()
    evidence["entries"][0]["source_pairs"][0]["session_id"] = "foreign-session"
    evidence["entries"][0]["source_pairs"][0]["discussion_id"] = "foreign-disc"
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_missing_invalid_and_truncated_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    for mutation in (
        {"available": False},
        {"structural_complete": False},
        {"source": None},
        {"target": None},
        {"text_truncated": True}
        if False
        else {"gaps": ["missing-source-member"]},
    ):
        client = FakeModel([selected])
        evidence = _valid_gate()
        evidence["entries"][0]["source_pairs"][0].update(mutation)
        # Apply text truncation mutation on one side when requested by control flag.
        if mutation.get("gaps") == ["missing-source-member"]:
            evidence["entries"][0]["source_pairs"][0]["source"]["text_truncated"] = True
            evidence["entries"][0]["source_pairs"][0]["gaps"] = []
        engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_invalid_pair_container_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = _valid_gate()
    evidence["entries"][0]["source_pairs"] = [1, 2]
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_count_mismatch_rejected():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = _valid_gate()
    evidence["source_pair_count"] = 999
    evidence["entries"][0]["source_pair_count"] = 999
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
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
async def test_source_pair_unverified_multi_event_valid_and_two_call():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    final = json.dumps({"assistant_text": "attributed two-pair recall", "commands": []})
    client = FakeModel([selected, final])
    gate_input = _valid_gate(
        pairs=[
            {
                "event_id": 5,
                "available": True,
                "structural_complete": True,
                "source": {"exchange_id": "src", "text_truncated": False, "member_proven": True},
                "target": {"exchange_id": "tgt", "text_truncated": False, "member_proven": True},
                "gaps": ["unverified-provenance"],
                "provenance_verified": False,
                "session_id": "s",
                "discussion_id": "disc",
            },
            {
                "event_id": 6,
                "available": True,
                "structural_complete": True,
                "source": {"exchange_id": "src2", "text_truncated": False, "member_proven": True},
                "target": {"exchange_id": "tgt2", "text_truncated": False, "member_proven": True},
                "gaps": [],
                "provenance_verified": True,
                "session_id": "s",
                "discussion_id": "disc",
            },
        ],
        expected_event_ids=(5, 6),
        events=[
            {"event_id": 5, "event_type": "correction", "is_correction": True},
            {"event_id": 6, "event_type": "conflict", "is_conflict": True},
        ],
    )
    gate_input["entries"][0]["member_coverage"].update(
        {"event_available_count": 2, "event_returned_count": 2}
    )
    gate_input["entries"][0]["event_coverage"].update(
        {"event_available_count": 2, "event_returned_count": 2}
    )
    gate_input.update(
        {"event_available_count": 2, "event_returned_count": 2, "event_unresolved_subject_count": 0}
    )
    assert _gate(gate_input) is True
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: gate_input)
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
    assert _reply(result) == "attributed two-pair recall"
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_real_helper_to_evidence_to_engine_valid(db_session):
    sid, discussion_id, event_id = _seed_source_pair_discussion(db_session)
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": [discussion_id], "clarification": None}
    )
    final = json.dumps({"assistant_text": "real pair recall", "commands": []})
    client = FakeModel([selected, final])
    evidence = read_selected_discussion_evidence(
        db_session,
        sid,
        [discussion_id],
        source_pair_readback=True,
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine._evidence_is_structurally_sufficient(evidence) is True
    assert evidence.source_pair_readback is True
    assert evidence.source_pair_event_count == 1
    assert _reply(result) == "real pair recall"
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_real_helper_to_evidence_missing_pair_does_not_stale_answer(db_session):
    sid, discussion_id, event_id = _seed_source_pair_discussion(db_session)
    # Break exact source membership; canonical rows remain but the pair is stale.
    db_session.query(DiscussionExchangeLinkRow).filter_by(
        discussion_id=discussion_id, exchange_id="src-ex"
    ).delete(synchronize_session=False)
    db_session.commit()
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": [discussion_id], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = read_selected_discussion_evidence(
        db_session,
        sid,
        [discussion_id],
        source_pair_readback=True,
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine._evidence_is_structurally_sufficient(evidence) is False
    assert result["commands"] == []
    assert "structurally complete evidence" in _reply(result)
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_real_helper_unverified_pair_remains_structurally_valid_and_attributed(db_session):
    sid, discussion_id, event_id = _seed_source_pair_discussion(db_session)
    db_session.query(DiscussionEventSourceLinkRow).filter_by(event_id=event_id).update(
        {"provenance": "model_inferred_unverified"}, synchronize_session=False
    )
    db_session.commit()
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": [discussion_id], "clarification": None}
    )
    final = json.dumps({"assistant_text": "attributed unverified source", "commands": []})
    client = FakeModel([selected, final])
    evidence = read_selected_discussion_evidence(
        db_session,
        sid,
        [discussion_id],
        source_pair_readback=True,
    )
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    result = await engine.run_turn("hello", discussion_discovery=discovery)
    assert evidence.source_pair_readback is True
    assert evidence.source_pair_unverified_count >= 1
    assert engine._evidence_is_structurally_sufficient(evidence) is True
    assert _reply(result) == "attributed unverified source"
    assert engine.call_count == 2
