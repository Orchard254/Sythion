"""Permanent regressions for independent ConversationTurnEngine audit repair."""

import asyncio
import json
import subprocess
import sys
from pathlib import Path

import pytest

try:
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )
except ImportError:  # pragma: no cover - alternate import roots
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        DiscussionDiscoveryInput,
    )


class FakeModelClient:
    def __init__(self, responses=None, exc=None):
        self.responses = list(responses or [])
        self.exc = exc
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if self.exc is not None:
            raise self.exc
        if not self.responses:
            return '{"assistant_text": "ok", "commands": []}'
        return self.responses.pop(0)


def _discovery_payload():
    return (
        '{"session_id":"s","available_count":1,"returned_count":1,'
        '"omitted_count":0,"unresolved_discussion_count":0,'
        '"unresolved_task_proof_count":0,"candidate_discussion_ids":["disc"],'
        '"discovery_complete":true,"reason":"complete",'
        '"representatives":[{"discussion_id":"disc"}]}'
    )


@pytest.mark.asyncio
async def test_canonical_resolver_clarification_preserves_raw_text():
    clarification = json.dumps({
        "outcome": "clarification",
        "selected_discussion_ids": [],
        "clarification": "  Keep this exact text.  ",
    })
    client = FakeModelClient([clarification])
    reader_calls = []
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: reader_calls.append(ids),
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
    assert result["assistant_text"] == "  Keep this exact text.  "
    assert result["commands"] == []
    assert engine.call_count == 1
    assert reader_calls == []


@pytest.mark.asyncio
async def test_old_resolver_envelope_is_rejected():
    old = json.dumps({
        "resolution": "selected",
        "assistant_text": "",
        "commands": [],
        "selected_discussion_ids": ["disc"],
    })
    client = FakeModelClient([old])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: {"insufficient": False})
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
async def test_extra_resolver_keys_are_rejected():
    bad = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
        "extra": 1,
    })
    client = FakeModelClient([bad])
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: {"insufficient": False})
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
@pytest.mark.parametrize("payload_override", [
    ('"candidate_discussion_ids":["other"]',),
    ('"available_count":2',),
    ('"discovery_complete":false',),
    ('"unresolved_discussion_count":1',),
    ('"omitted_count":1',),
])
async def test_contradictory_payload_metadata_rejected_before_call(payload_override):
    base = json.loads(_discovery_payload())
    # Apply one contradiction by rebuilding payload through field overrides from tuples.
    # payload_override carries a raw JSON fragment assignment expression.
    fragment = payload_override[0]
    # Build payload manually from base for clarity in each branch.
    import copy
    payload = copy.deepcopy(base)
    if fragment == '"candidate_discussion_ids":["other"]':
        payload["candidate_discussion_ids"] = ["other"]
    elif fragment == '"available_count":2':
        payload["available_count"] = 2
    elif fragment == '"discovery_complete":false':
        payload["discovery_complete"] = False
    elif fragment == '"unresolved_discussion_count":1':
        payload["unresolved_discussion_count"] = 1
    elif fragment == '"omitted_count":1':
        payload["omitted_count"] = 1
        payload["returned_count"] = 0
    payload_json = json.dumps(payload, separators=(",", ":"), sort_keys=True)
    client = FakeModelClient([])
    engine = ConversationTurnEngine(client)
    discovery = DiscussionDiscoveryInput(
        payload_json=payload_json,
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
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_alias_or_unknown_mapping_rejected():
    bad = {
        "payload_json": _discovery_payload(),
        "candidate_ids": ["disc"],
        "available_count": 1,
        "returned_count": 1,
        "omitted_count": 0,
        "unresolved_discussion_count": 0,
        "unresolved_task_proof_count": 0,
        "discovery_complete": True,
    }
    client = FakeModelClient([])
    engine = ConversationTurnEngine(client)
    with pytest.raises(ConversationTurnValidationError):
        await engine.run_turn("hello", discussion_discovery=bad)
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_real_unverified_structurally_complete_allows_attribution():
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    final = json.dumps({"assistant_text": "Attributed recall.", "commands": []})
    client = FakeModelClient([selected, final])

    def reader(ids):
        return {
            "session_id": "s",
            "requested_discussion_ids": ["disc"],
            "selected_discussion_ids": ["disc"],
            "discussion_count": 1,
            "entries": [{"discussion_id": "disc"}],
            "insufficient": True,
            "is_structurally_complete": True,
            "unresolved_member_count": 0,
            "event_unresolved_subject_count": 0,
            "has_role_text_truncation": False,
            "has_unverified_or_legacy_provenance": True,
            "member_omitted_count": 0,
            "event_omitted_count": 0,
        }

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
    assert result["assistant_text"] == "Attributed recall."
    assert engine.call_count == 2


@pytest.mark.asyncio
@pytest.mark.parametrize("mutation", [
    {"unresolved_member_count": 1},
    {"member_omitted_count": 1},
    {"event_unresolved_subject_count": 1},
    {"event_omitted_count": 1},
    {"has_role_text_truncation": True},
    {"is_structurally_complete": False},
])
async def test_structural_gaps_block_final_call(mutation):
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": ["disc"],
        "clarification": None,
    })
    client = FakeModelClient([selected])
    base = {
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
        "has_unverified_or_legacy_provenance": False,
        "member_omitted_count": 0,
        "event_omitted_count": 0,
    }
    base.update(mutation)
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: base)
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
    assert "structurally complete evidence" in result["assistant_text"]
    assert engine.call_count == 1


@pytest.mark.asyncio
async def test_frozen_discovery_input_and_nested_tuple():
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
    with pytest.raises((AttributeError, Exception)):
        discovery.candidate_discussion_ids = ("other",)
    assert not hasattr(discovery.candidate_discussion_ids, "append")


@pytest.mark.asyncio
async def test_foreign_selected_evidence_session_mismatch_propagates():
    try:
        from backend.services.grounded_discussion_evidence import (
            read_selected_discussion_evidence,
            SelectedDiscussionEvidenceError,
        )
        from backend.models.database import Base, ConversationExchange, ConversationTurn
    except ImportError:  # pragma: no cover
        from services.grounded_discussion_evidence import (
            read_selected_discussion_evidence,
            SelectedDiscussionEvidenceError,
        )
        from models.database import Base, ConversationExchange, ConversationTurn
    import uuid
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    engine_db = create_engine("sqlite:///:memory:", future=True)
    Base.metadata.create_all(engine_db)
    Session = sessionmaker(bind=engine_db, autoflush=False, future=True)
    session = Session()
    sid = "foreign-session-" + str(uuid.uuid4())
    other_sid = "other-session-" + str(uuid.uuid4())
    ex_id = "ex-foreign"
    session.add(ConversationExchange(id=ex_id, session_id=sid, user_text="u", assistant_text="a"))
    session.add(ConversationTurn(id=1, session_id=sid, role="user", summary="u"))
    session.add(ConversationTurn(id=2, session_id=sid, role="assistant", summary="a"))
    session.commit()

    from backend.services.grounded_discussions import GroundedDiscussionManager
    manager = GroundedDiscussionManager(session)
    discussion = manager.create_discussion(
        session_id=sid,
        label="Foreign",
        label_provenance="human_stated",
        member_exchanges=[{"exchange_id": ex_id, "user_turn_id": 1, "assistant_turn_id": 2}],
        link_provenance="human_stated",
    )
    session.commit()
    discussion_id = discussion.discussion_id

    resolver_text = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": [discussion_id],
        "clarification": None,
    })
    final_text = json.dumps({"assistant_text": "ignored", "commands": []})
    client = FakeModelClient([resolver_text])
    reader = lambda ids: read_selected_discussion_evidence(session, other_sid, list(ids))
    engine = ConversationTurnEngine(client, discussion_evidence_reader=reader)
    discovery = DiscussionDiscoveryInput(
        payload_json=json.dumps({
            "session_id": sid,
            "available_count": 1,
            "returned_count": 1,
            "omitted_count": 0,
            "unresolved_discussion_count": 0,
            "unresolved_task_proof_count": 0,
            "candidate_discussion_ids": [discussion_id],
            "discovery_complete": True,
            "reason": "complete",
            "representatives": [{"discussion_id": discussion_id}],
        }, separators=(",", ":"), sort_keys=True),
        candidate_discussion_ids=(discussion_id,),
        available_count=1,
        returned_count=1,
        omitted_count=0,
        unresolved_discussion_count=0,
        unresolved_task_proof_count=0,
        discovery_complete=True,
    )
    with pytest.raises(SelectedDiscussionEvidenceError):
        await engine.run_turn("hello", discussion_discovery=discovery)
    assert engine.call_count == 1
    session.close()
    engine_db.dispose()


def test_default_engine_import_does_not_activate_grounded_tables():
    root = Path(__file__).resolve().parents[2]
    code = (
        "import sys; sys.path.insert(0, '.'); "
        "import backend.services.conversation_turn_engine; "
        "from backend.models.database import Base; "
        "import json; "
        "print(json.dumps(sorted(Base.metadata.tables)))"
    )
    proc = subprocess.run([sys.executable, "-c", code], cwd=str(root), capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    import json
    tables = json.loads(proc.stdout)
    assert "grounded_discussions" not in tables
    assert "discussion_exchange_links" not in tables
    assert "discussion_events" not in tables