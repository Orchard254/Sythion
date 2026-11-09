"""Standalone SQLite persistence checks for the reasoning receipt contract.

These test commit/reopen semantics of an isolated recorder, NOT the production
TaskRunner ownership and ORM integration (which has a separate test suite).
"""

import json
import sqlite3

import pytest

from backend.services.reasoning_dispatch import (
    ReasoningJob, ReasoningPurpose, dispatch_reasoning,
)
from backend.services.reasoning_journal import ReasoningJournal, unresolved_reasoning_jobs


def _read_events(db_path):
    with sqlite3.connect(db_path) as db:
        return [{"event_type": kind, "event_data": json.loads(payload)}
                for kind, payload in db.execute(
                    "SELECT kind, payload FROM evidence ORDER BY sequence")]


def _recorder(db_path):
    db = sqlite3.connect(db_path)
    db.execute("CREATE TABLE IF NOT EXISTS evidence (sequence INTEGER PRIMARY KEY AUTOINCREMENT, kind TEXT, payload TEXT)")
    db.commit()
    db.close()

    async def record(task_id, kind, payload):
        assert task_id == "owned-task"
        with sqlite3.connect(db_path) as conn:
            conn.execute("INSERT INTO evidence(kind, payload) VALUES (?, ?)",
                         (kind, json.dumps(payload)))
            conn.commit()

    return record


@pytest.mark.asyncio
async def test_persisted_result_survives_closed_connection(tmp_path):
    db_path = tmp_path / "reasoning.db"
    journal = ReasoningJournal(_recorder(db_path))
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="owned-task")

    async def provider():
        return {"private": "not a ledger field"}

    assert (await dispatch_reasoning(job, provider, on_event=journal))["private"]
    events = _read_events(db_path)
    assert [e["event_type"] for e in events] == ["reasoning.started", "reasoning.response_received"]
    assert unresolved_reasoning_jobs(events) == ()
    assert "not a ledger field" not in repr(events)


@pytest.mark.asyncio
async def test_crash_after_durable_start_is_only_unresolved(tmp_path):
    db_path = tmp_path / "crash.db"
    journal = ReasoningJournal(_recorder(db_path))
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="owned-task")
    await journal(job, "started")
    # New connection represents a subsequent runtime process.
    recovered = _read_events(db_path)
    assert unresolved_reasoning_jobs(recovered) == (job.job_id,)
    assert all(e["event_type"] != "task.completed" for e in recovered)


@pytest.mark.asyncio
async def test_provider_failure_is_durable_and_not_replayed(tmp_path):
    db_path = tmp_path / "failure.db"
    journal = ReasoningJournal(_recorder(db_path))
    calls = 0
    async def provider():
        nonlocal calls
        calls += 1
        raise OSError("disconnected")
    with pytest.raises(OSError):
        await dispatch_reasoning(ReasoningJob(ReasoningPurpose.TASK_DECISION, 1, work_id="owned-task"),
                                 provider, on_event=journal)
    assert calls == 1
    events = _read_events(db_path)
    assert [e["event_type"] for e in events] == ["reasoning.started", "reasoning.failed"]
    assert unresolved_reasoning_jobs(events) == ()
