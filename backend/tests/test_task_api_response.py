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
