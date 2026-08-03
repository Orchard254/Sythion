"""
Global-stop endpoint tests.

The desktop shell's global stop shortcut needs a task-id-free way to halt the
most recently active task: `POST /api/v1/stop` cancels the newest task still
in a non-terminal state.
"""

from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.main import app
from backend.models.database import Base, Task, TaskEvent
from backend.models.database import get_db


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)
    session = SessionLocal()
    yield session
    session.close()


@pytest.fixture
def client(db_session):
    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "stop-test-token"
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "stop-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


def _make_task(db_session, task_id, state, updated_at):
    db_session.add(Task(
        id=task_id,
        original_request="some request",
        state=state,
        version=1,
        created_at=datetime.utcnow() - timedelta(hours=1),
        updated_at=updated_at,
    ))
    db_session.add(TaskEvent(task_id=task_id, event_type="task.created",
                             event_data="{}", sequence_num=1))
    db_session.commit()
    return task_id


class TestStopEndpoint:
    def test_stop_cancels_most_recent_active_task(self, client, db_session):
        _make_task(db_session, "t-stale", "running", datetime.utcnow() - timedelta(hours=2))
        _make_task(db_session, "t-current", "running", datetime.utcnow())

        response = client.post("/api/v1/stop")
        assert response.status_code == 200
        assert response.json()["id"] == "t-current"
        assert response.json()["state"] == "cancelled"

        terminal = db_session.query(Task).filter_by(id="t-current").first()
        assert terminal.state == "cancelled"
        assert terminal.terminal_reason == "cancelled_by_user"

    def test_stop_skips_terminal_tasks(self, client, db_session):
        _make_task(db_session, "t-done", "completed", datetime.utcnow())
        _make_task(db_session, "t-fail", "failed", datetime.utcnow() - timedelta(hours=1))

        response = client.post("/api/v1/stop")
        assert response.status_code == 404

    def test_stop_waiting_and_paused_are_active(self, client, db_session):
        _make_task(db_session, "t-wait", "waiting_for_user", datetime.utcnow())

        response = client.post("/api/v1/stop")
        assert response.status_code == 200
        assert response.json()["id"] == "t-wait"

        _make_task(db_session, "t-paused", "paused", datetime.utcnow())
        response = client.post("/api/v1/stop")
        assert response.status_code == 200
        assert response.json()["id"] == "t-paused"

    def test_stop_no_active_task_404(self, client):
        response = client.post("/api/v1/stop")
        assert response.status_code == 404
        assert "No active task" in response.json()["detail"]

    def test_stop_records_durable_cancel_event(self, client, db_session):
        _make_task(db_session, "t-evt", "running", datetime.utcnow())

        response = client.post("/api/v1/stop")
        assert response.status_code == 200

        events = db_session.query(TaskEvent).filter_by(task_id="t-evt").all()
        assert any(e.event_type == "task.cancelled" for e in events)
