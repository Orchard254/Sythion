import json
from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException

try:
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest, TaskState
    from backend.core.session import set_current_session_id
    from backend.models.database import Task, ConversationTurn, ConversationExchange, SessionLineage, ComposerRequestReceiptRow
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        ConversationModelError,
    )
except ImportError:
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest, TaskState
    from core.session import set_current_session_id
    from models.database import Task, ConversationTurn, ConversationExchange, SessionLineage, ComposerRequestReceiptRow
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
        ConversationModelError,
    )

MODEL_REJECTION_DETAIL = "Synth could not understand the model's reply. No conversation or action was recorded, and no background work was started."

ENGINE_STATE = {"raise": None, "calls": 0}


class FakeEngine:
    def __init__(self, model_client=None):
        pass

    async def run_turn(self, user_text, recent_turns=None, clarification_descriptors=None, discussion_contexts=None, **kwargs):
        ENGINE_STATE["calls"] += 1
        if ENGINE_STATE["raise"] is not None:
            raise ENGINE_STATE["raise"]
        return {"assistant_text": "Hello.", "commands": []}


def _reset():
    ENGINE_STATE["raise"] = None
    ENGINE_STATE["calls"] = 0


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id("known-rejection-test-session")
    _reset()
    yield
    _reset()


@pytest.fixture
def _patch_engine(monkeypatch):
    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", FakeEngine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: object())
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)
    return True


def _trusted_session(db):
    row = SessionLineage(
        session_id="known-rejection-test-session",
        generation=1,
        started_at=datetime.utcnow(),
        system_generated=True,
        provenance="system_uuid4",
        parent_session_id=None,
        reason="test",
        created_at=datetime.utcnow(),
    )
    db.add(row)
    db.commit()


@pytest.mark.asyncio
async def test_fresh_model_output_rejection_marks_receipt(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)
    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="kr-1", text="Hello"), bg, db, None)
    assert info.value.status_code == 503
    assert info.value.detail == MODEL_REJECTION_DETAIL
    assert bg.tasks == []

    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="kr-1", session_id="known-rejection-test-session").one()
    assert row.status == "failed"
    assert db.query(ConversationExchange).count() == 0


@pytest.mark.asyncio
async def test_known_rejection_duplicate_no_engine_or_dispatch(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)

    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")
    bg1 = BackgroundTasks()
    with pytest.raises(HTTPException):
        await assistant_turn(AssistantTurnRequest(client_request_id="kr-2", text="Hello"), bg1, db, None)

    calls_after_first = ENGINE_STATE["calls"]
    ENGINE_STATE["raise"] = None
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="kr-2", text="Hello"), bg2, db, None)
    assert info.value.status_code == 503
    assert info.value.detail == MODEL_REJECTION_DETAIL
    assert ENGINE_STATE["calls"] == calls_after_first
    assert bg2.tasks == []
    assert db.query(ConversationExchange).count() == 0


@pytest.mark.asyncio
async def test_input_unspecified_no_marker(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)

    for reason in ["input", "unspecified"]:
        ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code=reason)
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as info:
            await assistant_turn(AssistantTurnRequest(client_request_id="reason-" + reason, text="Hello"), bg, db, None)
        assert info.value.detail == "Assistant turn engine unavailable"

        row = db.query(ComposerRequestReceiptRow).filter_by(request_id="reason-" + reason, session_id="known-rejection-test-session").one()
        assert row.status == "resolving"


@pytest.mark.asyncio
async def test_provider_unexpected_no_marker(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)

    ENGINE_STATE["raise"] = ConversationModelError()
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="provider", text="Hello"), bg, db, None)
    assert info.value.detail == "Assistant turn engine unavailable"
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="provider", session_id="known-rejection-test-session").one()
    assert row.status == "resolving"

    ENGINE_STATE["raise"] = RuntimeError("boom")
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(AssistantTurnRequest(client_request_id="unexpected", text="Hello"), bg2, db, None)
    assert info2.value.detail == "Assistant turn engine unavailable"
    row2 = db.query(ComposerRequestReceiptRow).filter_by(request_id="unexpected", session_id="known-rejection-test-session").one()
    assert row2.status == "resolving"


@pytest.mark.asyncio
async def test_old_resolving_failed_unchanged(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)

    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(request_id="old-failed", session_id="known-rejection-test-session", text="Hello", payload={"client_request_id": "old-failed", "text": "Hello"})
    mgr.transition("old-failed", "known-rejection-test-session", status="resolving")
    db.commit()
    mgr.transition("old-failed", "known-rejection-test-session", status="failed")
    db.commit()

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="old-failed", text="Hello"), bg, db, None)
    assert info.value.status_code == 503
    assert info.value.detail == "Request previously failed; it was not rerun."
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="old-failed", session_id="known-rejection-test-session").one()
    assert row.status == "failed"

    mgr.receive(request_id="old-resolving", session_id="known-rejection-test-session", text="Hello", payload={"client_request_id": "old-resolving", "text": "Hello"})
    mgr.transition("old-resolving", "known-rejection-test-session", status="resolving")
    db.commit()

    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(AssistantTurnRequest(client_request_id="old-resolving", text="Hello"), bg2, db, None)
    assert info2.value.detail == "Request outcome is uncertain; it was not rerun."
    row2 = db.query(ComposerRequestReceiptRow).filter_by(request_id="old-resolving", session_id="known-rejection-test-session").one()
    assert row2.status == "resolving"


@pytest.mark.asyncio
async def test_transition_failure_no_durable_mark(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)
    from fastapi import BackgroundTasks
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")

    original_transition = ComposerRequestReceiptManager.transition

    def _fail_on_model_rejected(self, *args, **kwargs):
        if kwargs.get("status") == "failed" or (len(args) >= 3 and args[2] == "failed"):
            raise RuntimeError("commit failed")
        return original_transition(self, *args, **kwargs)

    monkeypatch.setattr(ComposerRequestReceiptManager, "transition", _fail_on_model_rejected)

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="failmark", text="Hello"), bg, db, None)
    assert info.value.detail == "Request outcome is uncertain; it was not rerun."
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="failmark", session_id="known-rejection-test-session").one()
    assert row.status == "resolving"


@pytest.mark.asyncio
async def test_failed_ownership_claim_returns_uncertain_no_engine(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)
    from fastapi import BackgroundTasks
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(request_id="owned", session_id="known-rejection-test-session", text="Hello", payload={"client_request_id": "owned", "text": "Hello"})
    mgr.transition("owned", "known-rejection-test-session", status="resolving")
    db.commit()

    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")
    calls_before = ENGINE_STATE["calls"]
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="owned", text="Hello"), bg, db, None)
    assert info.value.detail == "Request outcome is uncertain; it was not rerun."
    assert ENGINE_STATE["calls"] == calls_before
    assert bg.tasks == []

    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="owned", session_id="known-rejection-test-session").one()
    assert row.status == "resolving"


@pytest.mark.asyncio
async def test_commit_after_model_rejected_is_durable(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _trusted_session(db)
    from fastapi import BackgroundTasks

    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="shape")
    bg = BackgroundTasks()
    with pytest.raises(HTTPException):
        await assistant_turn(AssistantTurnRequest(client_request_id="dur", text="Hello"), bg, db, None)

    ENGINE_STATE["raise"] = None
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="dur", text="Hello"), bg2, db, None)
    assert info.value.status_code == 503
    assert info.value.detail == MODEL_REJECTION_DETAIL
    from backend.runtime.composer_request_receipt import get_composer_model_rejection_marker
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="dur", session_id="known-rejection-test-session").one()
    assert row.status == "failed"
    marker = get_composer_model_rejection_marker(row, row.request_id, row.session_id) if False else None
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager
    marker = ComposerRequestReceiptManager(db).get_model_rejection_marker("dur", "known-rejection-test-session")
    assert marker is not None
    assert marker.text_digest == row.text_digest
    assert marker.payload_digest == row.payload_digest
