import json
import logging
from datetime import datetime

import pytest
from fastapi import BackgroundTasks, HTTPException

try:
    from backend.api.routes import assistant_turn
    from backend.api.schemas import AssistantTurnRequest, TaskState
    from backend.core.session import set_current_session_id
    from backend.models.database import Task, ConversationTurn, ConversationExchange
    from backend.services.conversation_turn_engine import ConversationTurnValidationError
    from backend.services.conversation_runtime import ConversationModelError
except ImportError:
    from api.routes import assistant_turn
    from api.schemas import AssistantTurnRequest, TaskState
    from core.session import set_current_session_id
    from models.database import Task, ConversationTurn, ConversationExchange
    from services.conversation_turn_engine import ConversationTurnValidationError
    from services.conversation_runtime import ConversationModelError


ENGINE_STATE = {
    "assistant_text": "Hello from Synth.",
    "commands": [],
    "raise": None,
    "descriptors_seen": [],
    "discussion_contexts_seen": [],
}


class FakeEngine:
    def __init__(self, model_client=None):
        pass

    async def run_turn(self, user_text, recent_turns=None, clarification_descriptors=None, discussion_contexts=None, **kwargs):
        ENGINE_STATE["descriptors_seen"] = list(clarification_descriptors or [])
        ENGINE_STATE["discussion_contexts_seen"] = list(discussion_contexts or [])
        if ENGINE_STATE["raise"] is not None:
            raise ENGINE_STATE["raise"]
        return {
            "assistant_text": ENGINE_STATE["assistant_text"],
            "commands": [dict(c) for c in ENGINE_STATE["commands"]],
        }


def _reset_engine_state():
    ENGINE_STATE["assistant_text"] = "Hello from Synth."
    ENGINE_STATE["commands"] = []
    ENGINE_STATE["raise"] = None
    ENGINE_STATE["descriptors_seen"] = []
    ENGINE_STATE["discussion_contexts_seen"] = []


def _make_waiting_task(db, task_id, kind, version=4):
    task = Task(
        id=task_id,
        original_request="request for " + task_id,
        state=TaskState.WAITING_FOR_USER.value,
        version=version,
        pending_question="question for " + task_id + "?",
        pending_action=json.dumps({"kind": kind}),
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db.add(task)
    db.commit()
    return task


@pytest.fixture(autouse=True)
def _session():
    set_current_session_id("assistant-turn-test-session")
    _reset_engine_state()
    yield
    _reset_engine_state()


@pytest.fixture
def test_db_session(request, tmp_path):
    # Use conftest-provided test_db when available; otherwise create one.
    return None


@pytest.fixture
def _patch_engine(monkeypatch):
    monkeypatch.setattr("backend.services.conversation_turn_engine.ConversationTurnEngine", FakeEngine)
    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: object())

    worker_calls = {"count": 0}

    async def _worker(*args, **kwargs):
        worker_calls["count"] += 1
        return None

    monkeypatch.setattr("backend.api.routes.run_task", _worker)
    return worker_calls


def _db(request):
    # Pull test_db fixture value if present.
    return request.getfixturevalue("test_db")


@pytest.mark.asyncio
async def test_ordinary_conversation_works(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hello, how are you?"), bg, db, None
    )

    assert resp.status == "completed"
    assert resp.assistant_text == "Hello from Synth."
    assert resp.background_task_ids == []
    assert bg.tasks == []
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_malformed_model_output_surfaces_failure(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["raise"] = ConversationTurnValidationError("malformed model output")

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None
        )
    assert excinfo.value.status_code == 503
    assert db.query(ConversationExchange).count() == 0


@pytest.mark.asyncio
async def test_permission_tasks_are_excluded_from_descriptors(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _make_waiting_task(db, "perm-task", "permission")
    _make_waiting_task(db, "clar-task", "clarification")

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="status update"), bg, db, None
    )

    seen = {d["task_id"] for d in ENGINE_STATE["descriptors_seen"]}
    assert "clar-task" in seen
    assert "perm-task" not in seen


@pytest.mark.asyncio
async def test_topic_change_does_not_answer_clarification(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _make_waiting_task(db, "clar-task", "clarification")

    ENGINE_STATE["assistant_text"] = "We are on a new topic now."
    ENGINE_STATE["commands"] = []

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="By the way, what is your favorite color?"), bg, db, None
    )

    assert resp.status == "completed"
    assert resp.assistant_text == "We are on a new topic now."
    assert resp.background_task_ids == []
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_command_is_fail_closed_no_side_effect(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    worker_calls = _patch_engine
    _make_waiting_task(db, "clar-task", "clarification", version=4)

    before = [(t.id, t.state, t.version) for t in db.query(Task).all()]
    before_count = db.query(Task).count()

    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [
        {"type": "start_task", "request": "Do background work"},
        {"type": "answer_clarification", "task_id": "clar-task", "expected_version": 4, "answer": "yes"},
    ]

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Go ahead and answer yes"), bg, db, None
    )

    assert resp.status == "failed"
    assert resp.background_task_ids == []
    assert resp.assistant_text == (
        "Requested background work or task continuation was not started, "
        "and no task was launched or scheduled."
    )
    assert worker_calls["count"] == 0
    assert bg.tasks == []

    after = [(t.id, t.state, t.version) for t in db.query(Task).all()]
    assert after == before
    assert db.query(Task).count() == before_count
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_persistence_failure_surfaces_error(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    async def _boom(*args, **kwargs):
        raise RuntimeError("persistence boom")
    monkeypatch.setattr("backend.services.conversation_runtime.ConversationRuntime._persist_conversation", _boom)

    ENGINE_STATE["commands"] = []
    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)
    assert excinfo.value.status_code == 500
    assert db.query(ConversationExchange).count() == 0


@pytest.mark.asyncio
async def test_multiple_command_request_is_fail_closed(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "doing work"
    ENGINE_STATE["commands"] = [
        {"type": "start_task", "request": "first"},
        {"type": "start_task", "request": "second"},
    ]

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="do two"), bg, db, None
    )

    assert resp.status == "failed"
    assert resp.background_task_ids == []
    assert db.query(Task).count() == 0


@pytest.mark.asyncio
async def test_one_start_task_command_dispatches_one_worker(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    worker_calls = _patch_engine
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start work"), bg, db, None
    )

    assert resp.status == "completed"
    assert resp.assistant_text == "I started the work."
    assert len(resp.background_task_ids) == 1
    assert worker_calls["count"] == 0  # mocked worker is scheduled, not immediately invoked
    assert len(bg.tasks) == 1
    assert db.query(Task).count() == 1
    task = db.query(Task).filter_by(id=resp.background_task_ids[0]).first()
    assert task is not None
    assert task.state == TaskState.RUNNING.value
    assert task.original_request == "Do background work"
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_permission_hold_command_is_fail_closed(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _make_waiting_task(db, "perm-task", "permission")
    ENGINE_STATE["assistant_text"] = "ok"
    ENGINE_STATE["commands"] = [
        {"type": "answer_clarification", "task_id": "perm-task", "expected_version": 4, "answer": "yes"},
    ]

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="yes"), bg, db, None
    )

    assert resp.status == "failed"
    assert resp.background_task_ids == []
    task = db.query(Task).filter_by(id="perm-task").first()
    assert task.state == TaskState.WAITING_FOR_USER.value
    assert task.version == 4

@pytest.mark.asyncio
async def test_one_answer_clarification_command_dispatches_worker(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    _make_waiting_task(db, "clar-task", "clarification", version=4)
    ENGINE_STATE["assistant_text"] = "Continuing with your clarification."
    ENGINE_STATE["commands"] = [
        {"type": "answer_clarification", "task_id": "clar-task", "expected_version": 4, "answer": "yes"}
    ]

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    resp = await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="yes"), bg, db, None
    )

    assert resp.status == "completed"
    assert resp.background_task_ids == ["clar-task"]
    assert len(bg.tasks) == 1
    task = db.query(Task).filter_by(id="clar-task").first()
    assert task is not None
    assert task.state == TaskState.RUNNING.value
    assert task.version == 5
    assert db.query(ConversationExchange).count() == 1


@pytest.mark.asyncio
async def test_start_task_persistence_failure_does_not_report_running(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    async def _boom(*args, **kwargs):
        raise RuntimeError("persistence boom")
    monkeypatch.setattr("backend.services.conversation_runtime.ConversationRuntime._persist_conversation", _boom)

    from fastapi import BackgroundTasks, HTTPException
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert len(bg.tasks) == 0
    task = db.query(Task).order_by(Task.created_at.desc()).first()
    assert task is not None
    assert task.state != TaskState.RUNNING.value


@pytest.mark.asyncio
async def test_start_task_transition_failure_does_not_schedule_worker(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from backend.services.task_runner import TaskRunner
    original_transition = TaskRunner.transition_state
    async def _transition_boom(self, *args, **kwargs):
        if kwargs.get("new_state") == TaskState.RUNNING:
            raise RuntimeError("transition boom")
        return await original_transition(self, *args, **kwargs)
    monkeypatch.setattr(TaskRunner, "transition_state", _transition_boom)

    from fastapi import BackgroundTasks, HTTPException
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert len(bg.tasks) == 0
    task = db.query(Task).order_by(Task.created_at.desc()).first()
    assert task is not None
    assert task.state == TaskState.FAILED.value
    assert db.query(ConversationExchange).count() == 0
    assert db.query(ConversationTurn).count() == 0


@pytest.mark.asyncio
async def test_start_task_cleanup_failure_is_not_reported_as_success(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from backend.services.task_runner import TaskRunner
    original_transition = TaskRunner.transition_state
    async def _transition_boom(self, *args, **kwargs):
        if kwargs.get("new_state") == TaskState.RUNNING:
            raise RuntimeError("transition boom")
        return await original_transition(self, *args, **kwargs)
    monkeypatch.setattr(TaskRunner, "transition_state", _transition_boom)

    async def _cleanup_boom(*args, **kwargs):
        raise RuntimeError("cleanup boom")
    monkeypatch.setattr("backend.services.conversation_runtime.ConversationRuntime._unpersist_conversation", _cleanup_boom)

    from fastapi import BackgroundTasks, HTTPException
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert "Could not remove assistant conversation persisted before dispatch failure" in excinfo.value.detail
    assert db.query(ConversationExchange).count() == 1
    assert db.query(ConversationTurn).count() == 2


@pytest.mark.asyncio
async def test_answer_clarification_reply_failure_does_not_persist_success_promise(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    _make_waiting_task(db, "clar-task", "clarification", version=4)
    ENGINE_STATE["assistant_text"] = "Continuing with your clarification."
    ENGINE_STATE["commands"] = [
        {"type": "answer_clarification", "task_id": "clar-task", "expected_version": 4, "answer": "yes"}
    ]

    from backend.services.task_runner import TaskRunner

    async def _reply_boom(self, *args, **kwargs):
        raise RuntimeError("reply boom")

    monkeypatch.setattr(TaskRunner, "reply_to_waiting", _reply_boom)

    from fastapi import BackgroundTasks, HTTPException
    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="yes"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert len(bg.tasks) == 0
    task = db.query(Task).filter_by(id="clar-task").first()
    assert task is not None
    assert task.state == TaskState.WAITING_FOR_USER.value
    assert db.query(ConversationExchange).count() == 0
    assert db.query(ConversationTurn).count() == 0


@pytest.mark.asyncio
async def test_assistant_turn_model_validation_failure_logged_category_and_no_sensitive_text(request, _patch_engine, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SENSITIVE_VALIDATION_TEXT"
    ENGINE_STATE["raise"] = ConversationTurnValidationError(secret)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException):
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=validation" in m for m in messages)
    assert "duration_ms=" in joined or "elapsed_ms=" in joined
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_model_provider_failure_logged_category_and_no_sensitive_text(request, _patch_engine, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SENSITIVE_PROVIDER_TEXT"
    ENGINE_STATE["raise"] = ConversationModelError(secret)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException):
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=provider" in m for m in messages)
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_model_unexpected_failure_logged_category_and_no_sensitive_text(request, _patch_engine, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SENSITIVE_UNEXPECTED_TEXT"
    ENGINE_STATE["raise"] = RuntimeError(secret)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException):
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=unexpected" in m for m in messages)
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_model_provider_failure_actual_through_model_client(request, monkeypatch, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SENSITIVE_PROVIDER_TEXT_ACTUAL"

    class BoomProvider:
        async def chat(self, *args, **kwargs):
            raise RuntimeError(secret)

    from backend.services.conversation_runtime import ModelClient

    def _factory(*args, **kwargs):
        return ModelClient(provider=BoomProvider())

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", _factory)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as excinfo:
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    assert excinfo.value.status_code == 503
    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=provider" in m for m in messages)
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_transient_retry_leads_to_single_dispatch(request, monkeypatch):
    import json
    import httpx

    db = request.getfixturevalue("test_db")

    calls = {"n": 0}

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("Connection error: timed out") from httpx.ConnectTimeout(
                    "connection timed out"
                )
            return json.dumps(
                {
                    "assistant_text": "I started the work.",
                    "commands": [{"type": "start_task", "request": "Do background work"}],
                }
            )

    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *args, **kwargs: FakeModelClient(),
    )

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start work"), bg, db, None)

    assert resp.status == "completed"
    assert resp.assistant_text == "I started the work."
    assert len(resp.background_task_ids) == 1
    assert len(bg.tasks) == 1
    assert db.query(Task).count() == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_assistant_turn_real_engine_malformed_json_logged_reason_code_and_no_secret(request, monkeypatch, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SECRET_REAL_MALFORMED_JSON"

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            return secret + " {not json"

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: FakeModelClient())

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as excinfo:
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    assert excinfo.value.status_code == 503
    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=validation" in m for m in messages)
    assert any("reason_code=json_malformed" in m for m in messages)
    assert "validation_reason=" not in joined
    assert "elapsed_ms=" not in joined
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_real_engine_empty_output_logged_reason_code(request, monkeypatch, caplog):
    db = request.getfixturevalue("test_db")

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            return "   "

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: FakeModelClient())

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as excinfo:
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    assert excinfo.value.status_code == 503
    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=validation" in m for m in messages)
    assert any("reason_code=empty" in m for m in messages)
    assert "elapsed_ms=" not in joined


@pytest.mark.asyncio
async def test_assistant_turn_real_engine_bad_shape_logged_reason_code(request, monkeypatch, caplog):
    db = request.getfixturevalue("test_db")
    import json

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            return json.dumps({"assistant_text": "x", "commands": [], "extra": 1})

    monkeypatch.setattr("backend.services.conversation_runtime.ModelClient", lambda *a, **k: FakeModelClient())

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException) as excinfo:
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    assert excinfo.value.status_code == 503
    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=validation" in m for m in messages)
    assert any("reason_code=shape" in m for m in messages)
    assert "elapsed_ms=" not in joined


@pytest.mark.asyncio
async def test_assistant_turn_malicious_injected_exception_falls_back_to_unspecified(request, _patch_engine, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SECRET_INJECTED_CODE_OR_MESSAGE"

    class MaliciousInjected(ValueError):
        pass

    exc = MaliciousInjected(secret)
    # Invented/legacy aliases and arbitrary attrs must be ignored; only the
    # closed reason_code allowlist may be logged.
    exc.reason_code = secret
    exc.reason = secret
    exc.code = secret
    exc.label = secret
    exc.validation_reason = secret
    exc.diagnostic_reason = secret

    ENGINE_STATE["raise"] = exc

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException):
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=unexpected" in m for m in messages)
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_validation_exception_secret_reason_code_falls_back(request, _patch_engine, caplog):
    db = request.getfixturevalue("test_db")
    secret = "SECRET_REASON_CODE"

    ENGINE_STATE["raise"] = ConversationTurnValidationError(secret, reason_code=secret)

    with caplog.at_level(logging.WARNING, logger="backend.api.routes"):
        from fastapi import BackgroundTasks
        bg = BackgroundTasks()
        with pytest.raises(HTTPException):
            await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="Hi"), bg, db, None)

    messages = [r.getMessage() for r in caplog.records]
    joined = " ".join(messages)
    assert any("category=validation" in m for m in messages)
    assert any("reason_code=unspecified" in m for m in messages)
    assert secret not in joined
    assert secret not in caplog.text


@pytest.mark.asyncio
async def test_assistant_turn_real_engine_malformed_then_valid_start_task_dispatches_once(request, monkeypatch):
    import json

    db = request.getfixturevalue("test_db")
    calls = {"n": 0}

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            calls["n"] += 1
            if calls["n"] == 1:
                return "SECRET_RAW_MALFORMED_OUTPUT {not json"
            return json.dumps(
                {
                    "assistant_text": "I started the work.",
                    "commands": [{"type": "start_task", "request": "Do background work"}],
                }
            )

    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *args, **kwargs: FakeModelClient(),
    )

    from fastapi import BackgroundTasks

    bg = BackgroundTasks()
    resp = await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start work"), bg, db, None)

    assert resp.status == "completed"
    assert resp.assistant_text == "I started the work."
    assert len(resp.background_task_ids) == 1
    assert len(bg.tasks) == 1
    assert db.query(Task).count() == 1
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_assistant_turn_real_engine_malformed_twice_zero_dispatch(request, monkeypatch):
    db = request.getfixturevalue("test_db")
    calls = {"n": 0}

    class FakeModelClient:
        async def chat(self, messages, **kwargs):
            calls["n"] += 1
            return "{not json"

    monkeypatch.setattr(
        "backend.services.conversation_runtime.ModelClient",
        lambda *args, **kwargs: FakeModelClient(),
    )

    from fastapi import BackgroundTasks, HTTPException

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(AssistantTurnRequest(client_request_id=f"test-{__import__(chr(117)+chr(117)+chr(105)+chr(100)).uuid4()}", text="start work"), bg, db, None)

    assert excinfo.value.status_code == 503
    assert db.query(Task).count() == 0
    assert bg.tasks == []
    assert calls["n"] == 2


@pytest.mark.asyncio
async def test_named_discussion_contexts_are_session_scoped_in_assistant_turn(request, _patch_engine):
    db = request.getfixturevalue("test_db")
    from backend.models.database import DiscussionContext, ConversationExchange, ConversationTurn, DiscussionContextMembership

    current = "assistant-turn-test-session"
    db.add(ConversationExchange(id="same-exchange", session_id=current, user_text="same request", assistant_text="same reply"))
    db.add_all([
        ConversationTurn(id=101, session_id=current, role="user", summary="same request"),
        ConversationTurn(id=102, session_id=current, role="assistant", summary="same reply"),
    ])
    db.add(DiscussionContext(id="same-ctx", session_id=current, name="Same named discussion", name_source="initial_request_excerpt", created_at=datetime.utcnow()))
    db.add(DiscussionContextMembership(context_id="same-ctx", exchange_id="same-exchange", user_turn_id=101, assistant_turn_id=102))

    db.add(ConversationExchange(id="foreign-exchange", session_id="other-session", user_text="foreign request", assistant_text="foreign reply"))
    db.add_all([
        ConversationTurn(id=201, session_id="other-session", role="user", summary="foreign request"),
        ConversationTurn(id=202, session_id="other-session", role="assistant", summary="foreign reply"),
    ])
    db.add(DiscussionContext(id="foreign-ctx", session_id="other-session", name="Foreign named discussion", name_source="initial_request_excerpt", created_at=datetime.utcnow()))
    db.add(DiscussionContextMembership(context_id="foreign-ctx", exchange_id="foreign-exchange", user_turn_id=201, assistant_turn_id=202))
    db.commit()

    from fastapi import BackgroundTasks
    bg = BackgroundTasks()
    await assistant_turn(
        AssistantTurnRequest(client_request_id=f"test-{__import__('uuid').uuid4()}", text="Hello"), bg, db, None
    )
    names = [item.get("name") for item in ENGINE_STATE["discussion_contexts_seen"]]
    assert names == ["Same named discussion"]
    assert all(item.get("session_id") == current for item in ENGINE_STATE["discussion_contexts_seen"])
    assert "Foreign named discussion" not in names



@pytest.mark.asyncio
async def test_http_exception_preserves_original_detail_after_successful_cleanup(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from backend.services.task_runner import TaskRunner

    original_transition = TaskRunner.transition_state
    async def _transition_boom(self, *args, **kwargs):
        if kwargs.get("new_state") == TaskState.RUNNING:
            raise RuntimeError("transition boom")
        return await original_transition(self, *args, **kwargs)

    monkeypatch.setattr(TaskRunner, "transition_state", _transition_boom)

    from backend.api.schemas import AssistantTurnRequest
    client_request_id = f"test-{__import__('uuid').uuid4()}"
    from fastapi import BackgroundTasks, HTTPException

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=client_request_id, text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert excinfo.value.detail == "Could not start background work"
    assert "Could not remove assistant conversation persisted before dispatch failure" not in str(excinfo.value.detail)
    assert db.query(ConversationExchange).count() == 0
    assert db.query(ConversationTurn).count() == 0

    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    receipt = ComposerRequestReceiptManager(db).get(
        request_id=client_request_id, session_id="assistant-turn-test-session"
    )
    assert receipt is not None
    assert receipt.status == "resolving"


@pytest.mark.asyncio
async def test_task_outcome_uncertain_http_503_is_not_masked(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from backend.services.task_runner import TaskRunner
    from backend.api.schemas import TaskState

    original_create_task = TaskRunner.create_task
    async def _create_not_created(self, *args, **kwargs):
        import types
        task = await original_create_task(self, *args, **kwargs)
        if task is not None:
            return types.SimpleNamespace(id=task.id, state=TaskState.RUNNING.value)
        return task

    monkeypatch.setattr(TaskRunner, "create_task", _create_not_created)

    client_request_id = f"test-{__import__('uuid').uuid4()}"
    from fastapi import BackgroundTasks, HTTPException
    from backend.api.schemas import AssistantTurnRequest

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=client_request_id, text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 503
    assert "Task outcome is uncertain" in excinfo.value.detail


@pytest.mark.asyncio
async def test_start_task_cleanup_failure_transitions_receipt_to_failed_and_persists(request, _patch_engine, monkeypatch):
    db = request.getfixturevalue("test_db")
    ENGINE_STATE["assistant_text"] = "I started the work."
    ENGINE_STATE["commands"] = [{"type": "start_task", "request": "Do background work"}]

    from backend.services.task_runner import TaskRunner
    from backend.api.schemas import TaskState

    original_transition = TaskRunner.transition_state
    async def _transition_boom(self, *args, **kwargs):
        if kwargs.get("new_state") == TaskState.RUNNING:
            raise RuntimeError("transition boom")
        return await original_transition(self, *args, **kwargs)

    monkeypatch.setattr(TaskRunner, "transition_state", _transition_boom)

    async def _cleanup_boom(*args, **kwargs):
        raise RuntimeError("cleanup boom")
    monkeypatch.setattr("backend.services.conversation_runtime.ConversationRuntime._unpersist_conversation", _cleanup_boom)

    client_request_id = f"test-{__import__('uuid').uuid4()}"
    from fastapi import BackgroundTasks, HTTPException
    from backend.api.schemas import AssistantTurnRequest

    bg = BackgroundTasks()
    with pytest.raises(HTTPException) as excinfo:
        await assistant_turn(
            AssistantTurnRequest(client_request_id=client_request_id, text="start"), bg, db, None
        )
    assert excinfo.value.status_code == 500
    assert "Could not remove assistant conversation persisted before dispatch failure" in excinfo.value.detail

    from backend.runtime.composer_request_receipt import ComposerRequestReceiptManager

    receipt = ComposerRequestReceiptManager(db).get(
        request_id=client_request_id, session_id="assistant-turn-test-session"
    )
    assert receipt is not None
    assert receipt.status == "failed"
    db.commit()
    followup = ComposerRequestReceiptManager(db).get(
        request_id=client_request_id, session_id="assistant-turn-test-session"
    )
    assert followup is not None
    assert followup.status == "failed"
