"""Real TaskRunner/SQLite integration tests for durable reasoning receipts.

Run this file only within the full Sythion backend checkout. The isolated
reasoning-unit harness does not include the repository's ORM/runtime modules.
"""

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool

from backend.models.database import Base, Task
from backend.services.reasoning_dispatch import (
    ReasoningJob, ReasoningPurpose, dispatch_reasoning,
)
from backend.services.reasoning_journal import ReasoningJournal, unresolved_reasoning_jobs
from backend.services.task_runner import TaskRunner


@pytest.fixture
def session():
    engine = create_engine("sqlite://", connect_args={"check_same_thread": False},
                           poolclass=StaticPool)
    Base.metadata.create_all(engine)
    s = sessionmaker(bind=engine)()
    s.add(Task(id="owned", original_request="a bounded read-only request",
               state="running", version=1))
    s.commit()
    try:
        yield s
    finally:
        s.close()
        engine.dispose()


async def _owner(session):
    runner = TaskRunner(session)
    result = await runner.acquire_task_ownership("owned", worker_id="reasoning-test")
    assert result.acquired is True
    return runner


@pytest.mark.asyncio
async def test_receipts_are_committed_and_visible_across_sessions(session):
    runner = await _owner(session)
    journal = ReasoningJournal(runner.record_owned_reasoning_event)
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="owned")

    async def model():
        return {"private_model_response": "must not enter receipt"}

    assert (await dispatch_reasoning(job, model, on_event=journal))["private_model_response"]
    fresh = sessionmaker(bind=session.bind)()
    try:
        events = TaskRunner(fresh).ledger.event_history("owned")
        assert [e["event_type"] for e in events if e["event_type"].startswith("reasoning.")] == [
            "reasoning.started", "reasoning.response_received",
        ]
        assert all("private_model_response" not in str(e["event_data"])
                   for e in events)
        assert unresolved_reasoning_jobs(events) == ()
    finally:
        fresh.close()


@pytest.mark.asyncio
async def test_after_crash_only_unresolved_metadata_is_recoverable(session):
    runner = await _owner(session)
    job = ReasoningJob(ReasoningPurpose.TASK_DECISION, 2, work_id="owned")
    await ReasoningJournal(runner.record_owned_reasoning_event)(job, "started")
    recovered = sessionmaker(bind=session.bind)()
    try:
        events = TaskRunner(recovered).ledger.event_history("owned")
        assert unresolved_reasoning_jobs(events) == (job.job_id,)
        assert TaskRunner(recovered).unresolved_reasoning_calls("owned") == (job.job_id,)
        # Incomplete inference does not constitute a completed action or a
        # replay grant. The model has not been invoked by this test.
        assert not any(e["event_type"] in ("tool.executed", "task.completed") for e in events)
    finally:
        recovered.close()


@pytest.mark.asyncio
async def test_unowned_event_refused_without_writing(session):
    unowned = TaskRunner(session)
    with pytest.raises(RuntimeError, match="not owned"):
        await unowned.record_owned_reasoning_event(
            "owned", "reasoning.started", {"job_id": "j", "purpose": "task_decision"}
        )
    assert not unowned.ledger.event_history("owned")


@pytest.mark.asyncio
async def test_wrong_owner_nonce_refused_without_writing(session):
    runner = await _owner(session)
    runner._task_ownership_nonce = "not-the-real-nonce"
    with pytest.raises(RuntimeError, match="not owned"):
        await runner.record_owned_reasoning_event(
            "owned", "reasoning.started", {"job_id": "j", "purpose": "task_decision"}
        )
    assert not runner.ledger.event_history("owned")


@pytest.mark.asyncio
async def test_receipt_payload_rejects_output_and_arbitrary_approval(session):
    runner = await _owner(session)
    with pytest.raises(ValueError, match="metadata"):
        await runner.record_owned_reasoning_event(
            "owned", "reasoning.started", {"job_id": "j", "purpose": "task_decision",
                                              "approval": True, "prompt": "secret"}
        )
    with pytest.raises(ValueError, match="type"):
        await runner.record_owned_reasoning_event(
            "owned", "tool.executed", {"job_id": "j", "purpose": "task_decision"}
        )
    assert not runner.ledger.event_history("owned")


@pytest.mark.asyncio
async def test_receipt_refuses_caller_owned_sqlite_transaction(session):
    runner = await _owner(session)
    session.execute(text("BEGIN IMMEDIATE"))
    session.execute(text("INSERT INTO tasks (id, original_request, state, version, created_at, updated_at) "
                         "VALUES ('caller-data', 'preserve me', 'created', 1, CURRENT_TIMESTAMP, CURRENT_TIMESTAMP)"))
    with pytest.raises(RuntimeError, match="caller-owned"):
        await runner.record_owned_reasoning_event(
            "owned", "reasoning.started", {"job_id": "j", "purpose": "task_decision"}
        )
    assert session.query(Task).filter_by(id="caller-data").first() is not None
    session.rollback()
    assert session.query(Task).filter_by(id="caller-data").first() is None


@pytest.mark.asyncio
async def test_terminal_task_cannot_accept_new_reasoning_start(session):
    runner = await _owner(session)
    session.query(Task).filter_by(id="owned").one().state = "failed"
    session.commit()
    with pytest.raises(RuntimeError, match="not running"):
        await runner.record_owned_reasoning_event(
            "owned", "reasoning.started", {"job_id": "j", "purpose": "task_decision"}
        )
    assert not runner.ledger.event_history("owned")
