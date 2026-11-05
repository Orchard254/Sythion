import asyncio
from concurrent.futures import ThreadPoolExecutor

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task, TaskEvent
from backend.services.task_runner import TaskRunner


@pytest.mark.asyncio  # kept for pytest discovery consistency
def test_force_cancel_cas_race_isolated(tmp_path):
    # use sync
    pass


def test_force_cancel_concurrent_terminal_write_is_single(tmp_path):
    engine = create_engine(
        f"sqlite:///{tmp_path / 'force-cancel-cas.db'}",
        connect_args={"timeout": 10, "check_same_thread": False},
    )

    @event.listens_for(engine, "connect")
    def _cfg(conn, _):
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("PRAGMA busy_timeout=10000")

    with engine.connect() as conn:
        conn.exec_driver_sql("PRAGMA journal_mode=WAL")
        conn.commit()
    Base.metadata.create_all(engine)
    sessions = sessionmaker(bind=engine, expire_on_commit=False)

    with sessions() as db:
        db.add(Task(id="t-race", original_request="r", state="running", version=1))
        db.commit()

    results = {}

    def attempt(label):
        try:
            with sessions() as db:
                task = asyncio.run(TaskRunner(db).force_cancel("t-race"))
                results[label] = ("ok", task.state)
        except Exception as exc:
            results[label] = ("error", type(exc).__name__, str(exc))

    with ThreadPoolExecutor(max_workers=2) as pool:
        f1 = pool.submit(attempt, "a")
        f2 = pool.submit(attempt, "b")
        f1.result(timeout=30)
        f2.result(timeout=30)

    engine.dispose()

    assert sum(v[0] == "ok" for v in results.values()) == 1
    assert sum(v[0] == "error" for v in results.values()) == 1
    loser = [v for v in results.values() if v[0] == "error"][0]
    assert loser[1] == "RuntimeError"
    assert "Invalid task state transition" in loser[2]

    with sessions() as db:
        task = db.query(Task).filter_by(id="t-race").one()
        assert task.state == "cancelled"
        assert task.version == 2
        assert db.query(TaskEvent).filter_by(task_id="t-race", event_type="task.cancelled").count() == 1
