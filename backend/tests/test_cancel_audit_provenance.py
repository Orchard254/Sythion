import asyncio
import json

from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task, TaskEvent
from backend.services.task_runner import TaskRunner, get_runner


def test_force_cancel_audit_provenance_uses_caller_via(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-provenance.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions() as db:
        db.add(Task(id="task-cancel", original_request="r", state="running", version=1))
        db.commit()
        runner = get_runner(db)
        asyncio.run(runner.acquire_lease())
        try:
            task = db.query(Task).filter_by(id="task-cancel").one()
            task.state = "running"
            db.commit()
        finally:
            asyncio.run(runner.release_lease())

    with sessions() as db:
        cancelled = asyncio.run(get_runner(db).force_cancel("task-cancel", via="cancel_task"))
        assert cancelled.state == "cancelled"

    with sessions() as db:
        event = (
            db.query(TaskEvent)
            .filter_by(task_id="task-cancel", event_type="task.cancelled")
            .one()
        )
        payload = json.loads(event.event_data)
        assert payload.get("via") == "cancel_task"
        assert payload.get("via") != "global_stop"

    engine.dispose()


def test_force_cancel_audit_provenance_defaults_to_global_stop(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'audit-provenance-default.db'}",
        connect_args={"check_same_thread": False},
    )
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions() as db:
        db.add(Task(id="task-global", original_request="r", state="running", version=1))
        db.commit()

    with sessions() as db:
        cancelled = asyncio.run(get_runner(db).force_cancel("task-global"))
        assert cancelled.state == "cancelled"

    with sessions() as db:
        event = (
            db.query(TaskEvent)
            .filter_by(task_id="task-global", event_type="task.cancelled")
            .one()
        )
        payload = json.loads(event.event_data)
        assert payload.get("via") == "global_stop"

    engine.dispose()
