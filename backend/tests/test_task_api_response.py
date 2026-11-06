"""Task API responses preserve persisted clarification and task context."""

from datetime import datetime
from types import SimpleNamespace

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from backend.api import routes
from backend.models.database import Task, get_db


@pytest.fixture
def task_client(test_db, monkeypatch):
    task = Task(
        id="clarification-task", original_request="Help me", state="waiting_for_user",
        version=3, idempotency_key="replay-key", normalized_goal="Find the right option",
        active_action_id="action-1", pending_question="Which option do you prefer?",
        terminal_reason="Awaiting input", created_at=datetime.utcnow(), updated_at=datetime.utcnow(),
    )
    test_db.add(task)
    test_db.commit()

    async def list_tasks(limit):
        return [task]

    async def get_task(task_id):
        return task if task_id == task.id else None

    monkeypatch.setattr(routes, "get_runner", lambda db: SimpleNamespace(
        list_tasks=list_tasks, get_task=get_task,
    ))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        yield client, task, test_db


def test_task_reads_and_idempotent_replay_preserve_clarification(task_client):
    client, task, _ = task_client
    responses = [
        client.get("/tasks").json()["tasks"][0],
        client.get(f"/tasks/{task.id}").json(),
        client.post("/tasks", json={"original_request": "Help me", "idempotency_key": "replay-key"}).json(),
    ]
    for response in responses:
        assert response["state"] == "waiting_for_user"
        assert response["pending_question"] == "Which option do you prefer?"
        assert response["normalized_goal"] == "Find the right option"
        assert response["active_action_id"] == "action-1"
        assert response["terminal_reason"] == "Awaiting input"


def test_task_read_preserves_null_question_and_result(task_client):
    client, task, db = task_client
    task.pending_question = None
    task.final_result = "Done"
    task.state = "completed"
    db.commit()
    response = client.get(f"/tasks/{task.id}").json()
    assert response["pending_question"] is None
    assert response["final_result"] == "Done"


@pytest.mark.parametrize(('metadata', 'kind'), [
    ('{"kind":"clarification","private":"hidden"}', 'clarification'),
    ('{"kind":"permission"}', 'permission'),
    ('{"kind":"other"}', 'unknown'),
    ('[]', 'unknown'), ('broken', 'unknown'), (None, 'unknown'),
])
def test_waiting_kind_is_conservative_and_metadata_is_private(task_client, metadata, kind):
    client, task, db = task_client
    task.pending_action = metadata
    db.commit()
    response = client.get(f'/tasks/{task.id}').json()
    assert response['waiting_kind'] == kind
    assert 'pending_action' not in response


def test_task_preparation_failure_is_recorded(test_db, monkeypatch):
    from backend.cognitive.state import InteractionType
    from backend.cognitive.intent import IntentInterpreter
    from backend.services.cognitive_runtime import CognitiveDecisionAdapter
    from backend.services.task_runner import TaskRunner

    monkeypatch.setattr(IntentInterpreter, "determine_interaction", lambda *a, **k: InteractionType.ACTION)

    async def fail_routing(*args, **kwargs):
        raise RuntimeError("test preparation failure")

    monkeypatch.setattr(CognitiveDecisionAdapter, "route_new_input", fail_routing)
    monkeypatch.setattr(routes, "get_runner", lambda db: TaskRunner(db))
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app, raise_server_exceptions=False) as client:
        response = client.post("/tasks", json={"original_request": "do a test action"})
    assert response.status_code == 500
    task = test_db.query(Task).filter_by(original_request="do a test action").one()
    assert task.state == "failed"
    assert "try sending it again" in task.terminal_reason


def test_model_router_cannot_target_permission_wait(test_db, monkeypatch):
    from backend.services.conversation_runtime import ModelClient

    test_db.add(Task(id='permission', original_request='Do something', state='waiting_for_user',
                     pending_question='Allow?', pending_action='{"kind":"permission"}', version=1,
                     created_at=datetime.utcnow(), updated_at=datetime.utcnow()))
    test_db.commit()

    async def fake_chat(self, **kwargs):
        return '{"kind":"reply","task_id":"permission"}'

    monkeypatch.setattr(ModelClient, 'chat', fake_chat)
    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        result = client.post('/assistant/interpret', json={'original_request':'yes'})
        reply = client.post('/tasks/permission/reply', json={'answer':'yes', 'inferred':True})
        stale = client.post('/tasks/permission/reply', json={'answer':'yes', 'expected_version':0})
    assert result.status_code == 200
    assert result.json() == {'kind':'ambiguous', 'task_id':None}
    assert reply.status_code == 409
    assert stale.status_code == 409
    assert 'question changed' in stale.json()['detail']
    assert test_db.query(Task).filter_by(id='permission').one().state == 'waiting_for_user'


def test_interpret_recent_turns_preserve_insertion_order_on_equal_timestamp(test_db, monkeypatch):
    import json
    from backend.core import session as session_module
    from backend.models.database import ConversationTurn
    from backend.services.conversation_runtime import ModelClient

    monkeypatch.setattr(session_module, "get_current_session_id", lambda: "session-tie")
    captured = {}

    async def fake_chat(self, **kwargs):
        captured["messages"] = kwargs["messages"]
        return '{"kind":"conversation","task_id":null}'

    monkeypatch.setattr(ModelClient, "chat", fake_chat)

    shared_time = datetime.utcnow().replace(microsecond=0)
    test_db.add_all([
        ConversationTurn(session_id="session-tie", role="user", summary="user 1", created_at=shared_time),
        ConversationTurn(session_id="session-tie", role="assistant", summary="assistant 1", created_at=shared_time),
        ConversationTurn(session_id="session-tie", role="user", summary="user 2", created_at=shared_time),
        ConversationTurn(session_id="session-tie", role="assistant", summary="assistant 2", created_at=shared_time),
    ])
    test_db.commit()

    app = FastAPI()
    app.include_router(routes.router)
    app.dependency_overrides[get_db] = lambda: test_db
    with TestClient(app) as client:
        response = client.post("/assistant/interpret", json={"original_request": "follow up"})
    assert response.status_code == 200

    context = json.loads(captured["messages"][1]["content"])
    assert [item["text"] for item in context["recent"]] == [
        "user 1", "assistant 1", "user 2", "assistant 2",
    ]
