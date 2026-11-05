import asyncio
from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

try:
    from backend.core.session import set_current_session_id
    from backend.models.database import Base, SessionLineage, ComposerRequestReceiptRow
    from backend.services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
    )
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest
except ImportError:
    from core.session import set_current_session_id
    from models.database import Base, SessionLineage, ComposerRequestReceiptRow
    from services.conversation_turn_engine import (
        ConversationTurnEngine,
        ConversationTurnValidationError,
    )
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest

SID = "fault-window-session"
MODEL_REJECTION_DETAIL = "Synth could not understand the model's reply. No conversation or action was recorded, and no background work was started."
ENGINE_STATE = {"raise": None, "pause": False, "released": None, "calls": 0}


class FakeEngine:
    def __init__(self, model_client=None):
        pass

    async def run_turn(self, user_text, recent_turns=None, clarification_descriptors=None, discussion_contexts=None, **kwargs):
        ENGINE_STATE["calls"] += 1
        started = ENGINE_STATE.get("started")
        if started is not None:
            started.set()
        if ENGINE_STATE["pause"]:
            released = ENGINE_STATE["released"]
            assert released is not None
            await asyncio.wait_for(released.wait(), timeout=5)
        if ENGINE_STATE["raise"] is not None:
            raise ENGINE_STATE["raise"]
        return {"assistant_text": "Hello.", "commands": []}


def _reset():
    ENGINE_STATE["raise"] = None
    ENGINE_STATE["pause"] = False
    ENGINE_STATE["released"] = asyncio.Event()
    ENGINE_STATE["started"] = asyncio.Event()
    ENGINE_STATE["calls"] = 0


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id(SID)
    _reset()
    yield
    _reset()


@pytest.fixture
def _patch_engine(monkeypatch):
    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", FakeEngine)
    try:
        monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: object())
    except Exception:
        pass
    monkeypatch.setattr("backend.api.routes.run_task", lambda *a, **k: None)
    return True


def _make_db(tmp_path, name):
    dbfile = tmp_path / name
    engine = create_engine(
        "sqlite:///" + str(dbfile),
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    lineage = SessionLineage(
        session_id=SID,
        generation=1,
        started_at=datetime.utcnow(),
        system_generated=True,
        provenance="system_uuid4",
        parent_session_id=None,
        reason="test",
        created_at=datetime.utcnow(),
    )
    session.add(lineage)
    session.commit()
    return engine, session, dbfile


def _row(engine, rid):
    from sqlalchemy.orm import sessionmaker

    s = sessionmaker(bind=engine)()
    try:
        return s.query(ComposerRequestReceiptRow).filter_by(request_id=rid, session_id=SID).first()
    finally:
        s.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["response_type", "empty", "json_malformed", "shape", "command_validation"])
async def test_rejection_commit_raises_before_persistence(tmp_path, _patch_engine, reason):
    engine, db, _ = _make_db(tmp_path, "before.db")
    req = AssistantTurnRequest(client_request_id="before-" + reason, text="Hello")
    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code=reason)

    original_commit = db.commit
    active = {"on": False}

    def commit_wrapper():
        if active["on"]:
            active["on"] = False
            raise RuntimeError("commit failed before persistence")
        return original_commit()

    db.commit = commit_wrapper

    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    orig_transition = ComposerRequestReceiptManager.transition

    def transition_wrapper(self, *args, **kwargs):
        out = orig_transition(self, *args, **kwargs)
        if kwargs.get("status") == "failed" or (len(args) >= 3 and args[2] == "failed"):
            active["on"] = True
        return out

    _patch = _patch_engine
    from unittest.mock import patch

    with patch.object(ComposerRequestReceiptManager, "transition", transition_wrapper):
        set_current_session_id(SID)
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as info:
            await assistant_turn(req, bg, db, None)
        assert info.value.detail == "Request outcome is uncertain; it was not rerun."

    db.close()
    reopened = _row(engine, "before-" + reason)
    assert reopened is not None
    assert reopened.status == "resolving"

    # duplicate sees resolving, no provider/engine call
    prior_calls = ENGINE_STATE["calls"]
    sess2 = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(req, bg2, sess2, None)
    assert info2.value.detail == "Request outcome is uncertain; it was not rerun."
    assert ENGINE_STATE["calls"] == prior_calls
    sess2.close()


@pytest.mark.asyncio
@pytest.mark.parametrize("reason", ["response_type", "empty", "json_malformed", "shape", "command_validation"])
async def test_commit_persists_then_raises(tmp_path, _patch_engine, reason):
    engine, db, _ = _make_db(tmp_path, "after.db")
    req = AssistantTurnRequest(client_request_id="after-" + reason, text="Hello")
    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code=reason)

    original_commit = db.commit
    active = {"on": False}

    def commit_wrapper():
        if active["on"]:
            original_commit()
            active["on"] = False
            raise RuntimeError("commit raised after persistence")
        return original_commit()

    db.commit = commit_wrapper

    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager
    from unittest.mock import patch

    orig_transition = ComposerRequestReceiptManager.transition

    def transition_wrapper(self, *args, **kwargs):
        out = orig_transition(self, *args, **kwargs)
        if kwargs.get("status") == "failed" or (len(args) >= 3 and args[2] == "failed"):
            active["on"] = True
        return out

    with patch.object(ComposerRequestReceiptManager, "transition", transition_wrapper):
        set_current_session_id(SID)
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as info:
            await assistant_turn(req, bg, db, None)
        assert info.value.detail == "Request outcome is uncertain; it was not rerun."

    db.close()
    reopened = _row(engine, "after-" + reason)
    assert reopened is not None
    assert reopened.status == "failed"

    prior_calls = ENGINE_STATE["calls"]
    sess2 = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(req, bg2, sess2, None)
    assert info2.value.status_code == 503
    assert info2.value.detail == MODEL_REJECTION_DETAIL
    assert ENGINE_STATE["calls"] == prior_calls
    sess2.close()


@pytest.mark.asyncio
async def test_claim_commit_before_durable_claim_raises(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "claim-before.db")
    req = AssistantTurnRequest(client_request_id="claim-before", text="Hello")

    original_commit = db.commit
    commit_calls = {"n": 0}

    def commit_wrapper():
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            raise RuntimeError("claim commit failed before claim")
        return original_commit()

    db.commit = commit_wrapper
    set_current_session_id(SID)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(req, bg, db, None)
    assert info.value.status_code == 500

    db.close()
    assert _row(engine, "claim-before") is None
    assert ENGINE_STATE["calls"] == 0


@pytest.mark.asyncio
async def test_claim_commit_succeeds_then_raises(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "claim-after.db")
    req = AssistantTurnRequest(client_request_id="claim-after", text="Hello")

    original_commit = db.commit
    commit_calls = {"n": 0}

    def commit_wrapper():
        commit_calls["n"] += 1
        if commit_calls["n"] == 1:
            original_commit()
            raise RuntimeError("claim commit raised after persistence")
        return original_commit()

    db.commit = commit_wrapper
    set_current_session_id(SID)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(req, bg, db, None)
    assert info.value.status_code == 500

    db.close()
    reopened = _row(engine, "claim-after")
    assert reopened is not None
    assert reopened.status == "resolving"

    prior_calls = ENGINE_STATE["calls"]
    sess2 = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(req, bg2, sess2, None)
    assert info2.value.detail == "Request outcome is uncertain; it was not rerun."
    assert ENGINE_STATE["calls"] == prior_calls
    sess2.close()


@pytest.mark.asyncio
async def test_duplicate_while_first_engine_paused(tmp_path, _patch_engine):
    engine, db1, _ = _make_db(tmp_path, "pause.db")
    req = AssistantTurnRequest(client_request_id="paused", text="Hello")
    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")
    ENGINE_STATE["pause"] = True
    ENGINE_STATE["released"] = asyncio.Event()

    set_current_session_id(SID)
    bg1 = BackgroundTasks()
    first = asyncio.ensure_future(assistant_turn(req, bg1, db1, None))

    await asyncio.wait_for(ENGINE_STATE["started"].wait(), timeout=5)

    db2 = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg2 = BackgroundTasks()
    with pytest.raises(HTTPException) as info2:
        await assistant_turn(req, bg2, db2, None)
    assert info2.value.detail == "Request outcome is uncertain; it was not rerun."
    assert ENGINE_STATE["calls"] == 1
    assert bg2.tasks == []

    # release first
    ENGINE_STATE["released"].set()
    with pytest.raises(HTTPException) as info1:
        await first
    assert info1.value.status_code == 503
    assert info1.value.detail == MODEL_REJECTION_DETAIL

    db1.close()
    reopened = _row(engine, "paused")
    assert reopened is not None
    assert reopened.status == "failed"

    prior_calls = ENGINE_STATE["calls"]
    sess3 = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg3 = BackgroundTasks()
    with pytest.raises(HTTPException) as info3:
        await assistant_turn(req, bg3, sess3, None)
    assert info3.value.detail == MODEL_REJECTION_DETAIL
    assert ENGINE_STATE["calls"] == prior_calls
    assert db2 is not None
    from backend.models.database import ConversationExchange, Task
    check = sessionmaker(bind=engine)()
    assert check.query(ConversationExchange).count() == 0
    assert check.query(Task).count() == 0
    check.close()
    sess3.close()
    db2.close()


@pytest.mark.asyncio
async def test_reject_modified_payload_same_id_409(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "payload.db")
    set_current_session_id(SID)
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(
        request_id="same-id",
        session_id=SID,
        text="Hello",
        payload={"client_request_id": "same-id", "text": "Hello"},
    )
    db.commit()

    db.close()
    engine2 = engine
    sess = sessionmaker(bind=engine2)()
    set_current_session_id(SID)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="same-id", text="Different"), bg, sess, None)
    assert info.value.status_code == 409
    sess.close()


@pytest.mark.asyncio
async def test_terminal_model_rejected_immutable(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "terminal.db")
    set_current_session_id(SID)
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(
        request_id="term",
        session_id=SID,
        text="Hello",
        payload={"client_request_id": "term", "text": "Hello"},
    )
    mgr.transition("term", SID, status="resolving")
    db.commit()
    mgr.transition("term", SID, status="failed")
    db.commit()

    from backend.runtime.composer_request_receipt import ComposerModelRejectionMarkerRow

    marker = ComposerModelRejectionMarkerRow(
        request_id="term",
        session_id=SID,
        text_digest=mgr.db.query(ComposerRequestReceiptRow).filter_by(request_id="term", session_id=SID).one().text_digest,
        payload_digest=mgr.db.query(ComposerRequestReceiptRow).filter_by(request_id="term", session_id=SID).one().payload_digest,
        rejection_class="not_allowed",
    )
    db.add(marker)
    with pytest.raises(Exception):
        db.commit()
    db.rollback()

    for target in ["resolving", "resolved", "received", "needs_clarification"]:
        with pytest.raises(ValueError):
            mgr.transition("term", SID, status=target)
    db.close()


@pytest.mark.asyncio
async def test_old_schema_create_all_adds_marker_table_and_preserves_rows(tmp_path):
    from sqlalchemy import create_engine, inspect
    from sqlalchemy.orm import sessionmaker

    dbfile = tmp_path / "old-schema.db"
    engine = create_engine("sqlite:///" + str(dbfile), connect_args={"check_same_thread": False})
    # Simulate a pre-feature database: only the historical receipt table exists.
    ComposerRequestReceiptRow.__table__.create(engine, checkfirst=True)
    session = sessionmaker(bind=engine)()
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    mgr = ComposerRequestReceiptManager(session)
    for status in ["received", "resolving", "failed"]:
        mgr.receive(request_id="old-" + status, session_id=SID, text="Hello", payload={"client_request_id": "old-" + status, "text": "Hello"})
        if status != "received":
            mgr.transition("old-" + status, SID, status="resolving")
        if status == "failed":
            mgr.transition("old-" + status, SID, status="failed")
    session.commit()
    session.close()

    # New candidate registers and creates only the additive marker table.
    from backend.models.database import Base

    Base.metadata.create_all(engine)
    insp = inspect(engine)
    assert "composer_model_rejection_markers" in insp.get_table_names()
    assert "composer_request_receipts" in insp.get_table_names()

    session2 = sessionmaker(bind=engine)()
    receipts = session2.query(ComposerRequestReceiptRow).order_by(ComposerRequestReceiptRow.request_id).all()
    assert len(receipts) == 3
    assert {r.status for r in receipts} == {"received", "resolving", "failed"}
    session2.close()

    # Idempotent registration does not need another manual gate and does not damage rows.
    Base.metadata.create_all(engine)
    session3 = sessionmaker(bind=engine)()
    assert session3.query(ComposerRequestReceiptRow).count() == 3
    session3.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_marker_lookup_survives_reopen(tmp_path):
    engine, db, _ = _make_db(tmp_path, "reopen.db")
    from backend.runtime.composer_request_receipt import (
        ComposerRequestReceiptManager,
        ComposerModelRejectionMarkerRow,
        MODEL_REJECTION_CLASS,
    )

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(request_id="reopen", session_id=SID, text="Hello", payload={"client_request_id": "reopen", "text": "Hello"})
    mgr.transition("reopen", SID, status="resolving")
    db.commit()
    mgr.transition("reopen", SID, status="failed")
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="reopen", session_id=SID).one()
    marker = ComposerModelRejectionMarkerRow(
        request_id="reopen",
        session_id=SID,
        text_digest=row.text_digest,
        payload_digest=row.payload_digest,
        rejection_class=MODEL_REJECTION_CLASS,
    )
    db.add(marker)
    db.commit()
    db.close()

    from sqlalchemy.orm import sessionmaker

    session = sessionmaker(bind=engine)()
    marker2 = ComposerModelRejectionMarkerRow.__table__.select().where(
        ComposerModelRejectionMarkerRow.request_id == "reopen"
    )
    assert session.execute(marker2).first() is not None
    session.close()
    engine.dispose()


@pytest.mark.asyncio
async def test_mismatched_marker_fails_closed_no_known(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "mismatch.db")
    set_current_session_id(SID)
    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager, ComposerModelRejectionMarkerRow, MODEL_REJECTION_CLASS

    mgr = ComposerRequestReceiptManager(db)
    mgr.receive(request_id="mismatch", session_id=SID, text="Hello", payload={"client_request_id": "mismatch", "text": "Hello"})
    mgr.transition("mismatch", SID, status="resolving")
    db.commit()
    mgr.transition("mismatch", SID, status="failed")
    row = db.query(ComposerRequestReceiptRow).filter_by(request_id="mismatch", session_id=SID).one()
    bad_marker = ComposerModelRejectionMarkerRow(
        request_id="mismatch",
        session_id=SID,
        text_digest="0" * 64,
        payload_digest=row.payload_digest,
        rejection_class=MODEL_REJECTION_CLASS,
    )
    db.add(bad_marker)
    db.commit()

    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")
    prior_calls = ENGINE_STATE["calls"]
    sess = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as info:
        await assistant_turn(AssistantTurnRequest(client_request_id="mismatch", text="Hello"), bg, sess, None)
    assert info.value.detail == "Request outcome is uncertain; it was not rerun."
    assert ENGINE_STATE["calls"] == prior_calls
    assert bg.tasks == []
    sess.close()
    db.close()


@pytest.mark.asyncio
async def test_missing_marker_table_route_fails_closed(tmp_path, _patch_engine):
    engine, db, _ = _make_db(tmp_path, "missing-table.db")
    from sqlalchemy import text

    db.execute(text("DROP TABLE composer_model_rejection_markers"))
    db.commit()
    db.close()

    req = AssistantTurnRequest(client_request_id="missing-table", text="Hello")
    ENGINE_STATE["raise"] = ConversationTurnValidationError("bad", reason_code="json_malformed")
    sess = sessionmaker(bind=engine)()
    set_current_session_id(SID)
    bg = BackgroundTasks()
    with pytest.raises(HTTPException):
        await assistant_turn(req, bg, sess, None)

    reopened = _row(engine, "missing-table")
    if reopened is not None:
        assert reopened.status == "resolving"
    sess.close()
