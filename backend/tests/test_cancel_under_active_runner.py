"""Per-task cancel must stop work while another runner holds the real lease."""

import asyncio
from datetime import datetime, timedelta

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.api.schemas import TaskState
from backend.main import app
from backend.models.database import Base, Task, TaskEvent, get_db
from backend.services.task_runner import TaskRunner, get_runner


def _make_task_with_active_runner(db_session):
    """Create a RUNNING task owned by a separate physical runner/lease."""
    runner = get_runner(db_session)
    assert asyncio.run(runner.acquire_lease()) is True
    task = asyncio.run(runner.create_task("active work"))
    task = asyncio.run(runner.transition_state(
        task.id,
        TaskState.RUNNING,
        "task.execution_started",
        {},
    ))
    return runner, task


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
    engine.dispose()


@pytest.fixture
def client(db_session):
    import backend.main as backend_main

    previous_token = backend_main.control_token
    backend_main.control_token = "cancel-test-token"

    def override_get_db():
        yield db_session

    app.dependency_overrides[get_db] = override_get_db
    try:
        yield TestClient(
            app,
            headers={
                "Origin": "tauri://localhost",
                "X-Control-Token": "cancel-test-token",
            },
        )
    finally:
        backend_main.control_token = previous_token
        app.dependency_overrides.clear()


def test_cancel_endpoint_works_while_a_different_runner_holds_the_real_lease(client, db_session):
    active_runner, task = _make_task_with_active_runner(db_session)

    lease_before = db_session.query(Task).filter_by(id=task.id).one().state  # noqa: kept for explicit read
    assert lease_before == TaskState.RUNNING.value

    response = client.post(f"/api/v1/tasks/{task.id}/cancel")
    assert response.status_code == 200, response.text
    assert response.json()["id"] == task.id
    assert response.json()["state"] == "cancelled"

    db_session.expire_all()
    refreshed = db_session.query(Task).filter_by(id=task.id).one()
    assert refreshed.state == TaskState.CANCELLED.value
    assert refreshed.terminal_reason == "cancelled_by_user"

    # The active runner still owns its lease; per-task cancel did not steal it.
    assert asyncio.run(active_runner.acquire_lease()) is False
    asyncio.run(active_runner.release_lease())


def test_cancel_endpoint_404_for_missing_task(client):
    response = client.post("/api/v1/tasks/no-such-task/cancel")
    assert response.status_code == 404


def test_cancel_endpoint_400_for_terminal_task(client, db_session):
    task = Task(
        id="t-terminal",
        original_request="done",
        state=TaskState.COMPLETED.value,
        version=1,
        created_at=datetime.utcnow(),
        updated_at=datetime.utcnow(),
    )
    db_session.add(task)
    db_session.commit()

    response = client.post("/api/v1/tasks/t-terminal/cancel")
    assert response.status_code == 400
    assert "Cannot cancel task in state completed" in response.text


def test_cancel_endpoint_maps_force_cancel_race_to_409(client, db_session, monkeypatch):
    active_runner, task = _make_task_with_active_runner(db_session)

    async def changed(*args, **kwargs):
        raise RuntimeError("Task changed while stopping; try again.")

    monkeypatch.setattr(TaskRunner, "force_cancel", changed)
    response = client.post(f"/api/v1/tasks/{task.id}/cancel")
    assert response.status_code == 409
    assert "try again" in response.json()["detail"]

    asyncio.run(active_runner.release_lease())


def test_repeated_force_cancel_does_not_double_cancel_or_change_event_count(db_session):
    from backend.models.database import TaskEvent

    runner = get_runner(db_session)
    asyncio.run(runner.acquire_lease())
    try:
        task = asyncio.run(runner.create_task("uncertain work"))
        task = asyncio.run(runner.transition_state(
            task.id, TaskState.RUNNING, "task.execution_started", {}
        ))
        asyncio.run(runner.release_lease())

        first = asyncio.run(get_runner(db_session).force_cancel(task.id))
        assert first.state == TaskState.CANCELLED.value

        # The durable guard refuses a second terminal cancellation instead of
        # silently mutating or appending another event.
        with pytest.raises(RuntimeError, match="Invalid task state transition"):
            asyncio.run(get_runner(db_session).force_cancel(task.id))

        cancelled_events = db_session.query(TaskEvent).filter_by(
            task_id=task.id, event_type="task.cancelled").count()
        assert cancelled_events == 1
    finally:
        pass
from sqlalchemy import event


@pytest.fixture(params=["DELETE", "WAL"])
def race_sessions(tmp_path, request):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'cancel-race.db'}",
        connect_args={"timeout": 10, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    with engine.connect() as connection:
        assert connection.exec_driver_sql(
            f"PRAGMA journal_mode={request.param}"
        ).scalar().upper() == request.param
        connection.commit()

    Base.metadata.create_all(engine)
    yield sessionmaker(bind=engine, expire_on_commit=False)
    engine.dispose()


def test_concurrent_force_cancel_only_one_wins(race_sessions):
    from concurrent.futures import ThreadPoolExecutor

    with race_sessions() as db:
        task = Task(
            id="t-concurrent-cancel",
            original_request="concurrent stop",
            state=TaskState.RUNNING.value,
            version=1,
            created_at=datetime.utcnow(),
            updated_at=datetime.utcnow(),
        )
        db.add(task)
        db.commit()

    results = {}

    def attempt(label):
        try:
            with race_sessions() as db:
                updated = asyncio.run(TaskRunner(db).force_cancel("t-concurrent-cancel"))
                results[label] = ("ok", updated.state)
        except Exception as exc:
            results[label] = ("error", type(exc).__name__, str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(attempt, "a"), pool.submit(attempt, "b")]
        for future in futures:
            future.result(timeout=15)

    assert sum(value[0] == "ok" for value in results.values()) == 1
    assert sum(value[0] == "error" for value in results.values()) == 1
    losing = [value for value in results.values() if value[0] == "error"][0]
    assert losing[1] == "RuntimeError"
    assert "Invalid task state transition" in losing[2]

    with race_sessions() as db:
        task = db.query(Task).filter_by(id="t-concurrent-cancel").one()
        assert task.state == "cancelled"
        events = db.query(TaskEvent).filter_by(
            task_id="t-concurrent-cancel", event_type="task.cancelled"
        ).all()
        assert len(events) == 1
def test_force_cancel_retains_active_action_id_and_history(db_session):
    runner = get_runner(db_session)
    asyncio.run(runner.acquire_lease())
    try:
        task = asyncio.run(runner.create_task("uncertain active action"))
        task = asyncio.run(runner.transition_state(
            task.id, TaskState.RUNNING, "task.execution_started", {}
        ))
        task.active_action_id = "action-kept"
        db_session.commit()
        cancelled = asyncio.run(get_runner(db_session).force_cancel(task.id))
        assert cancelled.state == TaskState.CANCELLED.value
        assert cancelled.active_action_id == "action-kept"
        events = db_session.query(TaskEvent).filter_by(
            task_id=task.id, event_type="task.cancelled"
        ).all()
        assert len(events) == 1
    finally:
        asyncio.run(runner.release_lease())


def test_cancelled_late_provider_completion_cannot_overwrite_cancelled_task():
    """A terminal cancellation must remain authoritative over late completion.

    The production execution loop first asks the evidence gate and state machine
    before allowing ``complete_task``.  This regression performs that check after
    a separate runner/session has committed ``force_cancel`` for the running task.
    It deliberately does not steal or bypass the active runner's lease; the
    cancellation is durable across sessions and the late completion attempt returns
    without mutating state or appending completion evidence.
    """
    import json

    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker
    from sqlalchemy.pool import StaticPool

    from backend.services.task_execution_loop import TaskExecutionLoop
    from backend.tools.executor import ToolExecutor

    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    SessionLocal = sessionmaker(bind=engine)

    owner_session = SessionLocal()
    runner = get_runner(owner_session)
    asyncio.run(runner.acquire_lease())
    try:
        task = asyncio.run(runner.create_task("uncertain active action"))
        task = asyncio.run(runner.transition_state(
            task.id,
            TaskState.RUNNING,
            "task.execution_started",
            {},
        ))
        task.active_action_id = "action-kept"
        task.pending_action = json.dumps({
            "status": "approved",
            "tool_name": "mutating_tool",
            "params": {},
        })
        owner_session.commit()

        task_id = task.id
        active_action_id = task.active_action_id
        pending_action = task.pending_action
        before_events = owner_session.query(TaskEvent).filter_by(
            task_id=task_id,
        ).count()

        # A genuinely separate DB session commits the cancellation, exactly as
        # the public route does through a separate runner-owned transaction.
        cancel_session = SessionLocal()
        try:
            cancelled = asyncio.run(get_runner(cancel_session).force_cancel(task_id))
            assert cancelled.state == TaskState.CANCELLED.value
            assert cancelled.active_action_id == active_action_id
        finally:
            cancel_session.close()

        owner_session.expire_all()
        cancelled_task = owner_session.query(Task).filter_by(id=task_id).one()
        assert cancelled_task.state == TaskState.CANCELLED.value
        cancelled_version = cancelled_task.version
        events_after_cancel = owner_session.query(TaskEvent).filter_by(
            task_id=task_id,
        ).count()

        late_runner = get_runner(owner_session)
        loop = TaskExecutionLoop(owner_session, late_runner, ToolExecutor())

        # This is the production completion path invoked after a provider's late
        # completion decision.  It must be refused before any terminal mutation.
        assert asyncio.run(loop._complete_task(task_id, {
            "result": "late provider completion",
        })) is False

        owner_session.expire_all()
        after = owner_session.query(Task).filter_by(id=task_id).one()
        assert after.state == TaskState.CANCELLED.value
        assert after.version == cancelled_version
        assert after.terminal_reason == "cancelled_by_user"
        assert after.active_action_id == active_action_id
        assert after.pending_action == pending_action
        assert owner_session.query(TaskEvent).filter_by(
            task_id=task_id, event_type="task.completed",
        ).count() == 0
        # No late-provider replay or cleanup appended an event.
        assert owner_session.query(TaskEvent).filter_by(
            task_id=task_id,
        ).count() == events_after_cancel
    finally:
        asyncio.run(runner.release_lease())
        owner_session.close()
        engine.dispose()
