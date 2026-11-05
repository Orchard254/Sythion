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

    def test_stop_cancels_interrupted_task(self, client, db_session):
        _make_task(db_session, "t-interrupted", "interrupted", datetime.utcnow())
        response = client.post("/api/v1/stop")
        assert response.status_code == 200
        assert response.json()["state"] == "cancelled"
        assert any(e.event_type == "task.cancelled" for e in db_session.query(TaskEvent).filter_by(task_id="t-interrupted"))

    def test_stopped_authorization_hold_cannot_be_resumed_or_answered(self, client, db_session):
        _make_task(db_session, "t-held", "interrupted", datetime.utcnow())
        task = db_session.query(Task).filter_by(id="t-held").one()
        task.pending_action = '{"kind":"permission","question":"Allow?"}'
        task.terminal_reason = 'Awaiting authorization'
        db_session.commit()

        stopped = client.post('/api/v1/stop')
        assert stopped.status_code == 200
        assert stopped.json()['state'] == 'cancelled'
        resumed = client.post('/api/v1/tasks/t-held/resume')
        answered = client.post('/api/v1/tasks/t-held/reply', json={'answer':'yes'})
        assert resumed.status_code == 400
        assert answered.status_code == 400
        db_session.expire_all()
        assert db_session.query(Task).filter_by(id='t-held').one().state == 'cancelled'
        events = db_session.query(TaskEvent).filter_by(task_id='t-held').all()
        assert not any(e.event_type == 'user.replied' for e in events)

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


@pytest.mark.parametrize(('attempted_state', 'event_type', 'expected_error'), [
    ('completed', 'task.completed', 'Invalid task state transition: cancelled -> completed'),
    ('running', 'tool.executed', 'Task changed during state transition'),
])
def test_stale_executor_transition_cannot_overwrite_global_stop(
    tmp_path, monkeypatch, attempted_state, event_type, expected_error,
):
    import asyncio
    from backend.api.schemas import TaskState
    from backend.services.task_runner import TaskRunner

    engine = create_engine(f"sqlite:///{(tmp_path / 'race.db').as_posix()}")
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)
    worker, stopper = sessions(), sessions()
    try:
        _make_task(worker, 'race', 'running', datetime.utcnow())
        stale = worker.query(Task).filter_by(id='race').one()
        assert stale.state == 'running'
        runner = TaskRunner(worker)
        monkeypatch.setattr(runner, '_verify_lease', lambda: True)
        stopped = asyncio.run(TaskRunner(stopper).force_cancel('race'))
        stopped_version = stopped.version
        # Completion evidence refreshes the row and rejects the terminal-state
        # transition early. A stale progress write instead hits the state/version
        # CAS. Both must preserve the same durable cancellation invariant.
        with pytest.raises(RuntimeError, match=f'^{expected_error}$'):
            asyncio.run(runner.transition_state('race', TaskState(attempted_state), event_type, {}, final_result='done'))
        worker.expire_all()
        current = worker.query(Task).filter_by(id='race').one()
        assert current.state == 'cancelled'
        assert current.version == stopped_version
        assert current.terminal_reason == 'cancelled_by_user'
        assert current.final_result is None
        events = worker.query(TaskEvent).filter_by(task_id='race').all()
        assert not any(event.event_type == 'task.completed' for event in events)
        assert not any(event.event_type == 'tool.executed' for event in events)
        assert sum(event.event_type == 'task.cancelled' for event in events) == 1
        stopper.expire_all()
        durable = stopper.query(Task).filter_by(id='race').one()
        assert (durable.state, durable.version, durable.final_result) == ('cancelled', stopped_version, None)
    finally:
        worker.close()
        stopper.close()
        engine.dispose()


def test_stop_race_returns_conflict_instead_of_server_error(client, db_session, monkeypatch):
    from backend.services.task_runner import TaskRunner

    _make_task(db_session, 'race-stop', 'running', datetime.utcnow())

    async def changed(*args, **kwargs):
        raise RuntimeError('Task changed while stopping; try again.')

    monkeypatch.setattr(TaskRunner, 'force_cancel', changed)
    response = client.post('/api/v1/stop')
    assert response.status_code == 409
    assert 'try again' in response.json()['detail']
