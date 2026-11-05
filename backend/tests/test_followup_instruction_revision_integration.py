"""Integration contract for reviewed follow-up instructions."""

import asyncio
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker
import pytest

from backend.api.schemas import TaskState
from backend.models.database import Base, Task, TaskDecisionWindow, TaskEvent, ConversationExchange
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.task_runner import TaskRunner
from backend.services.task_instruction_revision import TaskInstructionRevision, RevisionUnavailable
from backend.runtime.followup_instruction_ledger import (
    FollowupInstructionLedgerManager as Ledger,
    STATUS_PENDING,
)
from backend.runtime.task_ownership import TaskOwnershipRow


@pytest.fixture
def db(tmp_path, monkeypatch):
    engine = create_engine(f"sqlite:///{tmp_path / 'followup_integration.db'}", connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def setup(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "followup-integration-test")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    with sessionmaker(bind=engine)() as session:
        yield session
    engine.dispose()


async def seed(db, *, include_source=True):
    db.add(Task(id="original", original_request="Create a chess study plan", state="running", version=2))
    db.commit()
    runtime = ConversationRuntime(db)
    identity = await runtime._persist_conversation(
        "Create a chess study plan", "Work accepted.", "source", task_id="original"
    )
    runner = TaskRunner(db)
    assert (await runner.acquire_task_ownership("original", worker_id="test-worker")).acquired
    TaskInstructionRevision(runner).open("original")
    if not include_source:
        identity = None
    return runner, runtime, identity


def appended_events(db):
    return db.query(TaskEvent).filter_by(task_id="original", event_type="task.instruction_appended").all()


@pytest.mark.asyncio
async def test_exact_ledger_instruction_becomes_goal_and_audit(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    instruction = "Maximum fifteen minutes; do not send or delete anything."
    ledger.receive(
        request_id="req-exact",
        task_id="original",
        instruction=instruction,
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    record = await TaskInstructionRevision(runner).apply_followup_request(
        task_id="original",
        request_id="req-exact",
        runner=runner,
        runtime=runtime,
        commit=True,
    )

    task = db.get(Task, "original")
    events = appended_events(db)
    assert record.status == "applied"
    assert task.normalized_goal == "Create a chess study plan\n[User follow-up]\n" + instruction
    assert task.version == 3
    assert db.get(TaskDecisionWindow, "original").revision == 1
    assert len(events) == 1
    assert events[0].event_data and instruction in events[0].event_data


@pytest.mark.asyncio
async def test_instruction_override_is_rejected_unchanged(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    instruction = "Maximum fifteen minutes."
    ledger.receive(
        request_id="req-override",
        task_id="original",
        instruction=instruction,
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()
    before_version = db.get(Task, "original").version
    before_events = len(appended_events(db))

    with pytest.raises(RevisionUnavailable):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-override",
            runner=runner,
            runtime=runtime,
            instruction="Injected rewrite",
            commit=True,
        )

    assert db.get(Task, "original").version == before_version
    assert len(appended_events(db)) == before_events
    assert ledger.get("req-override").status == STATUS_PENDING


@pytest.mark.asyncio
async def test_cross_task_and_session_mismatch_are_rejected(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-cross",
        task_id="original",
        instruction="Cross task check",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()
    wrong_session_runtime = ConversationRuntime(db)
    wrong_session_runtime.session_id = "different-session"

    with pytest.raises(RevisionUnavailable):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-cross",
            runner=runner,
            runtime=wrong_session_runtime,
            commit=True,
        )

    with pytest.raises(RevisionUnavailable):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="not-original",
            request_id="req-cross",
            runner=runner,
            runtime=runtime,
            commit=True,
        )

    assert ledger.get("req-cross").status == STATUS_PENDING


@pytest.mark.asyncio
async def test_forged_direct_applied_transition_is_rejected(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-forged",
        task_id="original",
        instruction="No fake applied",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    with pytest.raises(ValueError):
        ledger.transition(
            "req-forged",
            "applied",
            applied_task_version=999,
            applied_instruction_revision=999,
        )
    db.rollback()

    assert ledger.get("req-forged").status == STATUS_PENDING
    assert db.get(Task, "original").version == 2


@pytest.mark.asyncio
async def test_duplicate_receipt_does_not_replay(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-duplicate",
        task_id="original",
        instruction="Only once",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    first = await TaskInstructionRevision(runner).apply_followup_request(
        task_id="original",
        request_id="req-duplicate",
        runner=runner,
        runtime=runtime,
        commit=True,
    )
    second = await TaskInstructionRevision(runner).apply_followup_request(
        task_id="original",
        request_id="req-duplicate",
        runner=runner,
        runtime=runtime,
        commit=True,
    )

    assert first.status == "applied"
    assert second.status == "applied"
    assert db.get(Task, "original").version == 3
    assert len(appended_events(db)) == 1


@pytest.mark.asyncio
async def test_atomic_failure_leaves_pending_and_task_unchanged(db, monkeypatch):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-rollback",
        task_id="original",
        instruction="Rollback",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()
    before_events = len(appended_events(db))
    before_version = db.get(Task, "original").version

    monkeypatch.setattr(db, "commit", lambda: (_ for _ in ()).throw(RuntimeError("commit failed")))

    with pytest.raises(RuntimeError, match="commit failed"):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-rollback",
            runner=runner,
            runtime=runtime,
            commit=True,
        )

    db.rollback()
    assert db.get(Task, "original").version == before_version
    assert len(appended_events(db)) == before_events
    assert ledger.get("req-rollback").status == STATUS_PENDING


@pytest.mark.asyncio
async def test_ordered_multiple_followups_accumulate(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="first",
        task_id="original",
        instruction="First follow-up",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    ledger.receive(
        request_id="second",
        task_id="original",
        instruction="Second follow-up",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    applied = await TaskInstructionRevision(runner).apply_pending_followups(
        task_id="original",
        runner=runner,
        runtime=runtime,
        commit=True,
    )

    assert len(applied) == 2
    task = db.get(Task, "original")
    assert "First follow-up" in task.normalized_goal
    assert "Second follow-up" in task.normalized_goal
    assert task.version == 4
    assert ledger.pending(task_id="original") == []
    assert len(appended_events(db)) == 2


@pytest.mark.asyncio
async def test_permission_hold_rejects_without_mutation(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    owner_row = db.get(TaskOwnershipRow, "original")
    task = db.get(Task, "original")
    task.pending_action = '{"kind":"permission","status":"awaiting","action_id":"x"}'
    owner_row.active_action_id = None
    db.commit()

    TaskInstructionRevision(runner).open("original")
    ledger = Ledger(db)
    ledger.receive(
        request_id="req-permission",
        task_id="original",
        instruction="Should wait",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    with pytest.raises(RevisionUnavailable, match="not at a cooperative safe point"):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-permission",
            runner=runner,
            runtime=runtime,
            commit=True,
        )

    assert ledger.get("req-permission").status == STATUS_PENDING
    assert db.get(Task, "original").normalized_goal is None


@pytest.mark.asyncio
async def test_uncertain_action_rejects_without_mutation(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    owner_row = db.get(TaskOwnershipRow, "original")
    owner_row.uncertain_action_ids = '["unknown-effect"]'
    db.commit()
    TaskInstructionRevision(runner).open("original")

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-uncertain",
        task_id="original",
        instruction="Should not replay",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    with pytest.raises(RevisionUnavailable, match="not at a cooperative safe point"):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-uncertain",
            runner=runner,
            runtime=runtime,
            commit=True,
        )

    assert ledger.get("req-uncertain").status == STATUS_PENDING


@pytest.mark.asyncio
async def test_completed_race_does_not_mark_applied(db):
    runner, runtime, _ = await seed(db)
    from backend.core.session import get_current_session_id

    ledger = Ledger(db)
    ledger.receive(
        request_id="req-terminal",
        task_id="original",
        instruction="Too late",
        source_task_id="original",
        session_id=get_current_session_id(),
        source_exchange_id="source",
    )
    db.commit()

    task = db.get(Task, "original")
    task.state = TaskState.COMPLETED.value
    task.version += 1
    db.commit()

    with pytest.raises(RevisionUnavailable):
        await TaskInstructionRevision(runner).apply_followup_request(
            task_id="original",
            request_id="req-terminal",
            runner=runner,
            runtime=runtime,
            commit=True,
        )
    db.rollback()

    assert ledger.get("req-terminal").status == STATUS_PENDING