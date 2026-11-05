import json
from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException

try:
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest, TaskState
    from backend.core.session import set_current_session_id
    from backend.models.database import Task, ConversationTurn, ConversationExchange, SessionLineage
    from backend.services.conversation_turn_engine import ConversationTurnValidationError
except ImportError:
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest, TaskState
    from core.session import set_current_session_id
    from models.database import Task, ConversationTurn, ConversationExchange, SessionLineage
    from services.conversation_turn_engine import ConversationTurnValidationError

from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager
from backend.runtime.followup_instruction_ledger import FollowupInstructionLedger as FollowupLedger


ENGINE_STATE = {"assistant_text": "Hello from Synth.", "commands": [], "raise": None, "descriptors_seen": []}


class FakeEngine:
    def __init__(self, model_client=None):
        pass

    async def run_turn(self, user_text, recent_turns=None, clarification_descriptors=None, discussion_contexts=None, **kwargs):
        ENGINE_STATE["descriptors_seen"] = list(clarification_descriptors or [])
        if ENGINE_STATE["raise"] is not None:
            raise ENGINE_STATE["raise"]
        return {"assistant_text": ENGINE_STATE["assistant_text"], "commands": [dict(c) for c in ENGINE_STATE["commands"]]}


def _reset():
    ENGINE_STATE["assistant_text"] = "Hello from Synth."
    ENGINE_STATE["commands"] = []
    ENGINE_STATE["raise"] = None
    ENGINE_STATE["descriptors_seen"] = []


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id("assistant-turn-test-session")
    _reset()
    yield
    _reset()


@pytest.fixture
def _patch_engine(monkeypatch):
    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", FakeEngine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: object())

    async def _worker(*args, **kwargs):
        return None

    monkeypatch.setattr("backend.api.routes.run_task", _worker)
    return True


def _trusted_session(db, sid):
    row = SessionLineage(
        session_id=sid,
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
async def test_receipt_capture_round_trip_and_duplicate_conflict(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    sid = "assistant-turn-trusted-session"
    _trusted_session(db, sid)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", sid)
    set_current_session_id(sid)

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id="receipt-1", text="Hello"), bg, db, None
    )
    assert resp.receipt_id == "receipt-1"
    assert resp.composer_receipt_id == "receipt-1"

    # Exact retry replays the durable result instead of launching again.
    from backend.models.database import ConversationExchange
    print("EXCHANGES_AFTER_FIRST=", db.query(ConversationExchange).count(), [r.id for r in db.query(ConversationExchange).all()])
    bg2 = BackgroundTasks()
    replay = await assistant_turn(
        AssistantTurnRequest(client_request_id="receipt-1", text="Hello"), bg2, db, None
    )
    assert replay.exchange_id == resp.exchange_id
    assert replay.receipt_id == "receipt-1"
    assert bg2.tasks == []

    # Changed payload for the same durable identity conflicts.
    bg3 = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id="receipt-1", text="Different"), bg3, db, None
        )
    assert excinfo.value.status_code == 409


@pytest.mark.asyncio
async def test_followup_enqueue_does_not_mutate_or_launch(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")

    # Seed a running task with a conversation association.
    task = Task(id="task-followup", original_request="Do work", state=TaskState.RUNNING.value, version=2, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(task)
    db.commit()

    from backend.services.conversation_runtime import ConversationRuntime

    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation("Do work", "Accepted.", "source-ex", task_id=task.id)
    db.commit()

    from backend.models.database import TaskDecisionWindow

    window = TaskDecisionWindow(task_id=task.id, revision=0, phase="thinking", owner_nonce=1)
    db.add(window)
    db.commit()

    # Pretend an active ownership row exists for the window; bypass ownership by monkeypatch.
    from backend.runtime.task_ownership import TaskOwnershipManager

    class _Owner:
        status = "ACTIVE"
        expires_at = datetime.max.replace(year=2999)
        active_action_id = None
        uncertain_action_ids = None
        owner_nonce = 1

    original_get = TaskOwnershipManager.get

    def _fake_get(self, task_id):
        return _Owner()

    monkeypatch.setattr(TaskOwnershipManager, "get", _fake_get)

    ENGINE_STATE["assistant_text"] = "Queued follow-up."
    ENGINE_STATE["commands"] = [{"type": "append_task_instruction", "task_id": "task-followup", "expected_version": 2}]
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"it-{__import__('uuid').uuid4()}", text="Please limit to fifteen minutes."), bg, db, None
    )

    assert resp.status == "queued"
    assert bg.tasks == []
    assert db.query(Task).filter_by(id="task-followup").one().version == 2

    pending = FollowupLedger(db).pending(task_id="task-followup")
    assert len(pending) == 1
    assert pending[0].instruction == "Please limit to fifteen minutes."
    assert pending[0].source_exchange_id == "source-ex"


@pytest.mark.asyncio
async def test_followup_ambiguous_target_fails_closed(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "ok"
    ENGINE_STATE["commands"] = [{"type": "append_task_instruction", "task_id": None, "expected_version": 1}]
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(AssistantTurnRequest(client_request_id=f"it-{__import__('uuid').uuid4()}", text="update"), bg, db, None)
    assert resp.status == "failed"
    assert bg.tasks == []
    assert FollowupLedger(db).pending() == []


@pytest.mark.asyncio
async def test_followup_stale_expected_version_fails_closed(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    task = Task(id="stale-task", original_request="Do work", state=TaskState.RUNNING.value, version=5, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(task)
    db.commit()
    from backend.services.conversation_runtime import ConversationRuntime

    runtime = ConversationRuntime(db)
    await runtime._persist_conversation("Do work", "Accepted.", "source-stale", task_id=task.id)
    db.commit()
    ENGINE_STATE["assistant_text"] = "ok"
    ENGINE_STATE["commands"] = [{"type": "append_task_instruction", "task_id": "stale-task", "expected_version": 1}]
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(AssistantTurnRequest(client_request_id=f"it-{__import__('uuid').uuid4()}", text="update"), bg, db, None)
    assert resp.status == "failed"
    assert FollowupLedger(db).pending() == []


@pytest.mark.asyncio
async def test_followup_terminal_task_fails_closed(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    task = Task(id="terminal-task", original_request="Do work", state=TaskState.COMPLETED.value, version=2, created_at=datetime.utcnow(), updated_at=datetime.utcnow())
    db.add(task)
    db.commit()
    from backend.services.conversation_runtime import ConversationRuntime

    runtime = ConversationRuntime(db)
    await runtime._persist_conversation("Do work", "Accepted.", "source-terminal", task_id=task.id)
    db.commit()
    ENGINE_STATE["assistant_text"] = "ok"
    ENGINE_STATE["commands"] = [{"type": "append_task_instruction", "task_id": "terminal-task", "expected_version": 2}]
    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(AssistantTurnRequest(client_request_id=f"it-{__import__('uuid').uuid4()}", text="update"), bg, db, None)
    assert resp.status == "failed"
    assert FollowupLedger(db).pending() == []


@pytest.mark.asyncio
async def test_ledger_transaction_rollback_leaves_no_row():
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    try:
        from backend.models.database import Base
    except ImportError:
        from models.database import Base

    engine = create_engine("sqlite://", connect_args={"check_same_thread": False})
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    db = SessionLocal()
    try:
        mgr = FollowupLedger(db)
        db.begin()
        mgr.receive(
            request_id="rollback-me",
            task_id="some-task",
            instruction="rollback",
            source_task_id="some-task",
            session_id="s",
            source_exchange_id="ex",
            status="queued",
        )
        db.rollback()
        assert mgr.get(request_id="rollback-me") is None
    finally:
        db.close()
        engine.dispose()