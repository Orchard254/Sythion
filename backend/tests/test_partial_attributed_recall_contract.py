"""Default strict recall boundary contract tests."""

import inspect
import json

import pytest

try:
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        DiscussionDiscoveryInput,
    )
    from backend.models.database import Base, ConversationExchange, ConversationTurn
    from backend.services.grounded_discussions import GroundedDiscussionManager
    from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
    from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence
except ImportError:
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        DiscussionDiscoveryInput,
    )
    from models.database import Base, ConversationExchange, ConversationTurn
    from services.grounded_discussions import GroundedDiscussionManager
    from services.grounded_discussion_discovery import read_session_discussion_discovery
    from services.grounded_discussion_evidence import read_selected_discussion_evidence

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker


class FakeModel:
    def __init__(self, responses):
        self.responses = list(responses)
        self.calls = []

    async def chat(self, messages, **kwargs):
        self.calls.append(messages)
        if not self.responses:
            return '{"assistant_text":"ok","commands":[]}'
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


def _seed_lantern(session, session_id):
    ex_id = "lantern-exchange"
    ut_id = 7
    at_id = 8
    session.add(ConversationExchange(
        id=ex_id, session_id=session_id,
        user_text="Project Lantern indoor museum visit",
        assistant_text="Project Lantern museum visit",
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


@pytest.mark.asyncio
async def test_default_strict_real_incomplete_discovery_blocks(db_session):
    sid = "incomplete-discovery-session"
    _seed_lantern(db_session, sid)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=280)
    assert discovery.discovery_complete is False
    engine = ConversationTurnEngine(FakeModel([]))
    result = await engine.run_turn("What was Lantern?", discussion_discovery=discovery)
    assert result.commands == []
    assert "verified discussion discovery" in result.assistant_text.lower()
    assert engine.call_count == 0


@pytest.mark.asyncio
async def test_default_strict_real_complete_discovery_answers(db_session):
    sid = "complete-discovery-session"
    discussion_id = _seed_lantern(db_session, sid)
    discovery = read_session_discussion_discovery(db_session, sid, max_payload_bytes=1_000_000)
    assert discovery.discovery_complete is True
    selected = json.dumps({
        "outcome": "selected",
        "selected_discussion_ids": [discussion_id],
        "clarification": None,
    })
    final = json.dumps({"assistant_text": "Project Lantern answer.", "commands": []})
    client = FakeModel([selected, final])
    engine = ConversationTurnEngine(
        client,
        discussion_evidence_reader=lambda ids: read_selected_discussion_evidence(db_session, sid, list(ids)),
    )
    result = await engine.run_turn("What was Lantern?", discussion_discovery=discovery)
    assert result.assistant_text == "Project Lantern answer."
    assert result.commands == []
    assert engine.call_count == 2


@pytest.mark.asyncio
async def test_default_strict_synthetic_insufficient_evidence_blocks():
    selected = json.dumps(
        {"outcome": "selected", "selected_discussion_ids": ["disc"], "clarification": None}
    )
    client = FakeModel([selected])
    evidence = {
        "session_id": "s",
        "requested_discussion_ids": ["disc"],
        "selected_discussion_ids": ["disc"],
        "discussion_count": 1,
        "entries": [{"discussion_id": "disc"}],
        "insufficient": False,
        "is_structurally_complete": True,
        "unresolved_member_count": 1,
        "event_unresolved_subject_count": 0,
        "has_role_text_truncation": False,
        "has_unverified_or_legacy_provenance": False,
        "member_omitted_count": 0,
        "event_omitted_count": 0,
    }
    engine = ConversationTurnEngine(client, discussion_evidence_reader=lambda ids: evidence)
    assert engine._evidence_is_structurally_sufficient(evidence) is False


def test_engine_does_not_claim_partial_recall_seam():
    params = inspect.signature(ConversationTurnEngine.__init__).parameters
    assert "partial_attributed_recall_opt_in" not in params


def test_unsupported_opt_in_flag_is_rejected_now():
    with pytest.raises(TypeError):
        ConversationTurnEngine(FakeModel([]), partial_attributed_recall_opt_in=True)
