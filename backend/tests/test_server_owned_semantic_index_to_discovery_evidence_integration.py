"""Isolated offline integration: successful server-owned semantic index -> gated discovery -> exact selected evidence."""

import asyncio
import json

import backend.services.grounded_discussion_index  # noqa: F401
import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

import backend.core.session as _session_state
from backend.core.session import clear_current_session_id, get_current_session_id, set_current_session_id
from backend.models.database import Base, ConversationExchange, ConversationTurn
from backend.services.grounded_discussions import Base as GroundedBase, GroundedDiscussionManager
from backend.services.grounded_discussion_semantic_index import SemanticIndexBudget
from backend.services.grounded_discussion_server_owned_semantic_index import run_server_owned_semantic_index
from backend.services.grounded_discussion_discovery import read_session_discussion_discovery
from backend.services.grounded_discussion_index_coverage import read_session_index_coverage
from backend.services.grounded_discussion_indexed_discovery import read_session_indexed_discussion_discovery
from backend.services.grounded_discussion_evidence import read_selected_discussion_evidence


@pytest.fixture
def file_backed_context(tmp_path_factory):
    db_path = tmp_path_factory.mktemp("db") / "server_owned_discovery_evidence.db"
    engine = create_engine(f"sqlite:///{db_path}", future=True)
    Base.metadata.create_all(engine)
    GroundedBase.metadata.create_all(engine)
    SessionFactory = sessionmaker(bind=engine, autoflush=False, future=True)
    caller_session = SessionFactory()
    # Capture whether an explicit override was actually active before this test.
    # ``get_current_session_id()`` always returns a value, including defaults,
    # so restoring by blindly calling ``set_current_session_id()`` would turn an
    # inactive override into an active one.
    previous_override_active = bool(_session_state._EXPLICIT_SESSION_ID)
    previous_override_value = _session_state._EXPLICIT_SESSION_ID
    try:
        yield engine, SessionFactory, caller_session
    finally:
        caller_session.close()
        engine.dispose()
        # Restore the exact prior override state instead of forcing a value.
        if previous_override_active:
            set_current_session_id(previous_override_value)
        else:
            clear_current_session_id()


def _seed(caller_session, sid, *, user_text="fictional user text", assistant_text="fictional assistant text"):
    caller_session.add(ConversationTurn(id=11, session_id=sid, role="user", summary="fictional user"))
    caller_session.add(ConversationTurn(id=12, session_id=sid, role="assistant", summary="fictional assistant"))
    caller_session.add(
        ConversationExchange(
            id="exchange-1",
            session_id=sid,
            user_text=user_text,
            assistant_text=assistant_text,
            status="ok",
        )
    )
    caller_session.commit()


class _FakeSemanticModel:
    def __init__(self, payload_factory):
        self.payload_factory = payload_factory
        self.calls = 0

    async def chat(self, *, messages, **kwargs):
        self.calls += 1
        payload = self.payload_factory(json.loads(messages[1]["content"]))
        return json.dumps(payload)


@pytest.mark.asyncio
async def test_successful_server_owned_semantic_index_links_to_gated_discovery_and_exact_evidence(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-fictional-integration"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    user_text = "fictional user text"
    assistant_text = "fictional assistant text"

    model = _FakeSemanticModel(
        lambda _payload: {
            "status": "proposals",
            "proposals": [
                {
                    "label": "Fictional Semantic Topic",
                }
            ],
            "clarification": None,
            "reason": "fictional semantic indexing success",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)

    outcome = await run_server_owned_semantic_index(
        caller_session,
        model,
        sid,
        "exchange-1",
        11,
        12,
        budget=budget,
    )

    assert outcome.provider_call_count == 1
    assert model.calls == 1
    assert budget.consumed_calls == 1
    assert outcome.applied is True
    assert outcome.applied_count == 1
    assert outcome.state_status == "complete"
    assert outcome.rejected_reason is None
    assert outcome.model_input_binding_verified is False  # fail closed: no provider receipt/full semantic persistence
    assert outcome.model_input_digest is not None
    assert outcome.overall_end_to_end_complete_possible is False
    # Ownership is intentionally not treated as proven by a supported-pool
    # heuristic. No ownership flag is asserted here.

    read_session = SessionFactory()
    try:
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        assert gated.available_count == 1
        assert len(gated.candidate_discussion_ids) == 1
        selected_id = gated.candidate_discussion_ids[0]

        raw_discovery = read_session_discussion_discovery(
            read_session, sid, max_payload_bytes=4096,
        )
        assert raw_discovery.discovery_complete is True
        assert gated.discovery_complete is False
        assert gated.reason == "unresolved_canonical_source"

        coverage = read_session_index_coverage(
            read_session,
            sid,
            max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        assert coverage.structural_state_complete is True
        assert coverage.coverage_complete is True
        assert coverage.source_freshness_verified is False
        assert coverage.end_to_end_complete is False

        evidence = read_selected_discussion_evidence(
            read_session, sid, [selected_id],
        )
        assert evidence.discussion_count == 1
        assert evidence.has_unverified_or_legacy_provenance is True
        assert evidence.untrusted is True
        assert evidence.complete is False
        assert evidence.semantic_complete is False
        assert evidence.insufficient is True
        assert "unverified_or_legacy_provenance_present" in evidence.insufficiency_reasons

        entry = evidence.entries[0]
        assert entry.label_provenance == "model_inferred_unverified"
        assert entry.label_unverified is True
        assert entry.has_unverified_or_legacy_provenance is True
        assert entry.is_structurally_complete is True
        assert entry.semantic_complete is False

        assert len(entry.members) == 1
        member = entry.members[0]
        assert member.source_available is True
        assert member.exchange_id == "exchange-1"
        assert member.user_turn_id == 11
        assert member.assistant_turn_id == 12
        assert member.canonical_user_text == user_text
        assert member.canonical_assistant_text == assistant_text
        assert member.link_provenance == "model_inferred_unverified"

        # The absence of a correction/conflict event here only proves that the
        # server-owned application layer did not emit the model claim into
        # history. It is not evidence that no correction claim exists, and it
        # does not by itself establish freshness. Coverage/freshness status is
        # asserted separately below where applicable.
        assert not any(e.is_correction or e.is_conflict for e in entry.events)
        assert entry.member_source_complete is True
        assert entry.event_source_complete is True
    finally:
        read_session.close()


@pytest.mark.asyncio
async def test_unresolved_correction_claim_stays_incomplete_and_undisclosed(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-fictional-correction"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    gm = GroundedDiscussionManager(caller_session)
    seeded = gm.create_discussion(
        session_id=sid,
        label="Seed Human Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
        link_provenance="human_stated",
        validated_proposal=None,
    )
    caller_session.commit()

    model = _FakeSemanticModel(
        lambda payload: {
            "status": "proposals",
            "proposals": [
                {
                    "discussion_id": payload["candidate_discussion_ids"][0],
                    "correction_text": "fictional unresolved correction",
                }
            ],
            "clarification": None,
            "reason": "fictional unresolved correction proposal",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)

    outcome = await run_server_owned_semantic_index(
        caller_session,
        model,
        sid,
        "exchange-1",
        11,
        12,
        budget=budget,
    )

    assert outcome.provider_call_count == 1
    assert model.calls == 1
    assert budget.consumed_calls == 1
    assert outcome.rejected_reason is None
    assert outcome.state_status == "insufficient"
    assert outcome.applied is False
    assert outcome.applied_count == 0
    assert outcome.model_input_binding_verified is False  # fail closed: no provider receipt/full semantic persistence
    assert outcome.model_input_digest is not None
    assert outcome.overall_end_to_end_complete_possible is False
    # Ownership is intentionally not treated as proven by a supported-pool
    # heuristic. No ownership flag is asserted here.

    read_session = SessionFactory()
    try:
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        assert gated.available_count == 1
        assert len(gated.candidate_discussion_ids) == 1
        selected_id = gated.candidate_discussion_ids[0]

        # Structural state remains incomplete because the correction claim made
        # the durable index state insufficient.
        coverage = read_session_index_coverage(
            read_session,
            sid,
            max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        assert coverage.structural_state_complete is False
        assert coverage.coverage_complete is False
        assert coverage.insufficient_count == 1
        assert coverage.source_freshness_verified is False
        assert coverage.end_to_end_complete is False
        assert gated.discovery_complete is False

        evidence = read_selected_discussion_evidence(
            read_session, sid, [selected_id],
        )
        assert evidence.discussion_count == 1
        assert evidence.untrusted is True
        assert evidence.complete is False
        assert evidence.semantic_complete is False

        entry = evidence.entries[0]
        # Only the application's own member linkage is asserted here. The lack of
        # a correction/conflict event proves no model correction claim was
        # emitted; it is not proof that no correction claim exists, and
        # structural insufficiency is asserted via coverage above.
        assert not any(e.is_correction or e.is_conflict for e in entry.events)
        assert entry.member_source_complete is True
        assert entry.event_source_complete is True
    finally:
        read_session.close()


from backend.services.conversation_turn_engine import ConversationTurnEngine
from backend.tests.offline_context_provider_harness import ScriptedModel


@pytest.mark.asyncio
async def test_default_unknown_freshness_fail_closed_zero_engine_calls(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-engine-default-freshness"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    model = _FakeSemanticModel(
        lambda _payload: {
            "status": "proposals",
            "proposals": [{"label": "Fictional Semantic Topic"}],
            "clarification": None,
            "reason": "fictional semantic indexing success",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        caller_session, model, sid, "exchange-1", 11, 12, budget=budget
    )
    assert outcome.applied is True

    read_session = SessionFactory()
    try:
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        # Default discovery remains fail-closed because source freshness is
        # unknown. This is a test-only observation of production fail-closed
        # behavior; it is not an authenticated producer.
        assert gated.discovery_complete is False

        turn_model = ScriptedModel([])
        turn_engine = ConversationTurnEngine(turn_model)
        result = await turn_engine.run(
            user_text="answer from discussions",
            recent_turns=[],
            clarification_descriptors=[],
            discussion_discovery=gated,
        )
        assert turn_engine.call_count == 0
        assert "verified discussion discovery" in result.assistant_text.lower()
    finally:
        read_session.close()


@pytest.mark.asyncio
async def test_unresolved_correction_fail_closed_zero_engine_calls(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-engine-correction"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    gm = GroundedDiscussionManager(caller_session)
    gm.create_discussion(
        session_id=sid,
        label="Seed Human Topic",
        label_provenance="human_stated",
        member_exchanges=[
            {"exchange_id": "exchange-1", "user_turn_id": 11, "assistant_turn_id": 12}
        ],
        link_provenance="human_stated",
        validated_proposal=None,
    )
    caller_session.commit()

    model = _FakeSemanticModel(
        lambda payload: {
            "status": "proposals",
            "proposals": [
                {
                    "discussion_id": payload["candidate_discussion_ids"][0],
                    "correction_text": "fictional unresolved correction",
                }
            ],
            "clarification": None,
            "reason": "fictional unresolved correction proposal",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        caller_session, model, sid, "exchange-1", 11, 12, budget=budget
    )
    assert outcome.state_status == "insufficient"

    read_session = SessionFactory()
    try:
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
        )
        # Unresolved durable state plus unknown freshness keeps discovery
        # fail-closed, so the turn engine must not call the interactive model.
        assert gated.discovery_complete is False

        turn_model = ScriptedModel([])
        turn_engine = ConversationTurnEngine(turn_model)
        result = await turn_engine.run(
            user_text="answer from discussions",
            recent_turns=[],
            clarification_descriptors=[],
            discussion_discovery=gated,
        )
        assert turn_engine.call_count == 0
        assert "verified discussion discovery" in result.assistant_text.lower()
    finally:
        read_session.close()


@pytest.mark.asyncio
async def test_test_only_verifier_two_call_path_with_structurally_complete_unverified_evidence(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-engine-two-call"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    model = _FakeSemanticModel(
        lambda _payload: {
            "status": "proposals",
            "proposals": [{"label": "Fictional Semantic Topic"}],
            "clarification": None,
            "reason": "fictional semantic indexing success",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)
    outcome = await run_server_owned_semantic_index(
        caller_session, model, sid, "exchange-1", 11, 12, budget=budget
    )
    assert outcome.applied is True

    read_session = SessionFactory()
    try:
        # Test-only verifier below is NOT a production freshness proof. It is
        # injected solely to exercise the opt-in two-call turn-engine branch
        # against structurally complete but unverified original evidence.
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
            source_freshness_verifier=lambda session, session_id, entries, states: True,
        )
        assert gated.discovery_complete is True

        selected_id = gated.candidate_discussion_ids[0]
        evidence = read_selected_discussion_evidence(read_session, sid, [selected_id])
        entry = evidence.entries[0]
        # The original server-owned evidence remains structurally complete but
        # unverified by construction, including model-inferred provenance.
        assert entry.is_structurally_complete is True
        assert entry.has_unverified_or_legacy_provenance is True
        assert entry.semantic_complete is False

        def evidence_reader(selected_ids):
            return read_selected_discussion_evidence(read_session, sid, list(selected_ids))

        turn_model = ScriptedModel(
            [
                json.dumps(
                    {
                        "outcome": "selected",
                        "selected_discussion_ids": [selected_id],
                        "clarification": None,
                    }
                ),
                json.dumps({"assistant_text": "Verified answer", "commands": []}),
            ]
        )
        turn_engine = ConversationTurnEngine(turn_model, discussion_evidence_reader=evidence_reader)
        result = await turn_engine.run(
            user_text="summarize the discussion",
            recent_turns=[],
            clarification_descriptors=[],
            discussion_discovery=gated,
        )
        # At most two interactive turn-engine calls, separate from indexing.
        assert len(turn_model.calls) == 2
        final_request = json.loads(turn_model.calls[1]["messages"][-1]["content"])
        final_evidence = final_request["selected_discussion_evidence"]
        final_entry = final_evidence["entries"][0]
        final_member = final_entry["members"][0]
        assert final_member["canonical_user_text"] == "fictional user text"
        assert final_member["canonical_assistant_text"] == "fictional assistant text"
        assert final_member["link_provenance"] == "model_inferred_unverified"
        assert final_member["task_id"] is None
        assert final_member["task_state"] is None
        assert final_entry["label_provenance"] == "model_inferred_unverified"
        assert final_entry["has_unverified_or_legacy_provenance"] is True
        assert final_entry["discussion_task_fields"]["task_id"] is None
        # The carried discussion event remains the server-owned member linkage
        # only; it carries no correction/conflict payload or unrelated task data.
        assert all(
            event.get("payload_text") is None
            and event.get("is_correction") is False
            and event.get("is_conflict") is False
            for event in final_entry["events"]
        )
        assert turn_engine.call_count == 2
        assert result.assistant_text == "Verified answer"
        assert result.commands == []
    finally:
        read_session.close()


@pytest.mark.asyncio
async def test_test_only_verifier_clarification_one_call(file_backed_context):
    engine, SessionFactory, caller_session = file_backed_context

    sid = "session-engine-clarification"
    set_current_session_id(sid)
    _seed(caller_session, sid)

    model = _FakeSemanticModel(
        lambda _payload: {
            "status": "proposals",
            "proposals": [{"label": "Fictional Semantic Topic"}],
            "clarification": None,
            "reason": "fictional semantic indexing success",
        }
    )
    budget = SemanticIndexBudget(max_provider_calls=1)
    await run_server_owned_semantic_index(
        caller_session, model, sid, "exchange-1", 11, 12, budget=budget
    )

    read_session = SessionFactory()
    try:
        # Test-only verifier only; not a production freshness/authenticity proof.
        gated = read_session_indexed_discussion_discovery(
            read_session,
            sid,
            discovery_max_payload_bytes=4096,
            coverage_max_payload_bytes=4096,
            max_examined_exchanges=1000,
            source_freshness_verifier=lambda session, session_id, entries, states: True,
        )
        assert gated.discovery_complete is True

        turn_model = ScriptedModel(
            [
                json.dumps(
                    {
                        "outcome": "clarification",
                        "selected_discussion_ids": [],
                        "clarification": "Need clarification",
                    }
                )
            ]
        )
        turn_engine = ConversationTurnEngine(turn_model)
        result = await turn_engine.run(
            user_text="summarize the discussion",
            recent_turns=[],
            clarification_descriptors=[],
            discussion_discovery=gated,
        )
        assert turn_engine.call_count == 1
        assert result.assistant_text == "Need clarification"
    finally:
        read_session.close()