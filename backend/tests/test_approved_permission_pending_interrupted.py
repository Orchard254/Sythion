"""Isolated regression tests for approved permission-pending actions interrupted
around the ``clear_pending_action`` -> ``action.started`` handoff.

The approved permission branch deliberately clears ``pending_action`` *before*
running ``_execute_tool`` (which records ``action.started`` and begins the
durable task-ownership action). These tests exercise the resulting window:
stopping after the clear but before the action start, stopping after the action
start but before execution/commit, realistic recovery/rerun decisions, and
concurrent ownership. They assert against subsequent model decisions and
``TaskEvent`` records, not only the pending-action column.

No production code is changed by this module.
"""

import asyncio
import json
import os

import pytest
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.api.schemas import TaskState
from backend.models.database import Base, Task, TaskEvent
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionEngine, DecisionType
from backend.services.task_runner import TaskRunner


def _make_file_db(db_path):
    engine = create_engine(
        f"sqlite:///{db_path}",
        connect_args={"check_same_thread": False, "timeout": 30},
    )
    with engine.connect() as conn:
        conn.execute(text("PRAGMA journal_mode=WAL"))
        conn.execute(text("PRAGMA busy_timeout=5000"))
        conn.commit()
    Base.metadata.create_all(engine)
    return engine


def _create_task(engine, task_id):
    session = sessionmaker(bind=engine)()
    try:
        session.add(Task(id=task_id, original_request=f"request {task_id}", state=TaskState.CREATED.value, version=1))
        session.commit()
    finally:
        session.close()


class SpyExecutor:
    def __init__(self):
        self.calls = 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        class Tool:
            def get_policy_requirements(self):
                return {"mutating": True, "risk_level": "medium"}

        return Tool()

    async def execute_tool(self, tool_name, input_data, policy_context=None):
        self.calls += 1
        return type(
            "R", (), {
                "success": True,
                "model_summary": "ok",
                "data": None,
                "error": None,
                "evidence": None,
                "duration_ms": 1,
            }
        )()


def _event_count(session, task_id, event_type):
    return session.query(TaskEvent).filter_by(task_id=task_id, event_type=event_type).count()


async def _stage_approved_pending(engine, task_id, worker_id="worker-1"):
    """Create a task, stage one permission-pending action, approve it, and return
    the (engine, runner, session, staged_action_id, pending_state) needed by the
    interruption scenarios. The runner has NOT cleared pending yet."""
    session = sessionmaker(bind=engine)()
    runner = TaskRunner(session)
    await runner.acquire_lease()

    task = await runner.get_task(task_id)
    await runner.transition_state(task_id, TaskState.RUNNING, "task.execution_started", {"started_at": "test"})

    ex = SpyExecutor()
    loop = TaskExecutionLoop(session, runner, ex)
    calls = {"n": 0}

    async def scripted_make_decision(self, task_id, *args, **kwargs):
        calls["n"] += 1
        if calls["n"] == 1:
            return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})
        return (DecisionType.COMPLETE_TASK, {"result": "done"})

    original = ModelDecisionEngine.make_decision
    ModelDecisionEngine.make_decision = scripted_make_decision

    try:
        await loop.execute_task(task_id)
    finally:
        ModelDecisionEngine.make_decision = original

    parked = await runner.get_task(task_id)
    assert parked.state == TaskState.WAITING_FOR_USER.value
    pending = json.loads(parked.pending_action)
    assert pending["kind"] == "permission"
    assert pending["status"] == "awaiting"
    action_id = pending["action_id"]
    assert not _event_count(session, task_id, "action.started")

    # Directly set the synthetic pending status field for test setup. The helper
    # refuses approved transitions because they are not authenticated consent.
    parked.pending_action = json.dumps(
        {**json.loads(parked.pending_action), "status": "approved"}
    )
    session.commit()

    # User approval path (production reply handling). The staging run released
    # the lease; the approval transition still requires it.
    await runner.acquire_lease()
    await runner.transition_state(
        task_id,
        TaskState.RUNNING,
        "action.approved",
        {"answer": "approve", "decision": "approved"},
        pending_question=None,
    )

    parked = await runner.get_task(task_id)
    approved_pending = json.loads(parked.pending_action)
    assert approved_pending["status"] == "approved"
    return engine, session, runner, action_id


@pytest.mark.asyncio
async def test_interrupt_after_clear_before_action_start_does_not_replay(tmp_path):
    task_id = "clear-gap-task"
    engine = _make_file_db(str(tmp_path / "clear-gap.db"))
    try:
        _create_task(engine, task_id)
        engine, session, runner, staged_action_id = await _stage_approved_pending(engine, task_id)

        # Interruption after clear_pending_action, before _execute_tool/action.started.
        await runner.clear_pending_action(task_id)
        assert await runner.get_task(task_id) is not None
        cleared = await runner.get_task(task_id)
        assert cleared.pending_action is None
        assert cleared.state == TaskState.RUNNING.value
        before_started = _event_count(session, task_id, "action.started")
        assert before_started == 0

        # Release ownership/lease as if the worker died after the clear.
        await runner.release_task_ownership(task_id=task_id, outcome="interrupted")
        await runner.release_lease()
        session.commit()
        session.close()

        recovered_session = sessionmaker(bind=engine)()
        recovered_runner = TaskRunner(recovered_session)
        recovered_replays = {"n": 0}

        async def recovered_decision(self, task_id, *args, **kwargs):
            recovered_replays["n"] += 1
            # A fresh model proposal is required; the old approved action is no
            # longer pending and must not be silently replayed.
            return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})

        original_make_decision = ModelDecisionEngine.make_decision
        ModelDecisionEngine.make_decision = recovered_decision
        try:
            recovered_loop = TaskExecutionLoop(recovered_session, recovered_runner, SpyExecutor())
            await recovered_loop.execute_task(task_id)
        finally:
            ModelDecisionEngine.make_decision = original_make_decision

        assert recovered_replays["n"] >= 1, "recovery did not invoke a subsequent model decision"
        after = await recovered_runner.get_task(task_id)
        # No replay: the approved action never reached action.started.
        assert _event_count(recovered_session, task_id, "action.started") == before_started
        assert _event_count(recovered_session, task_id, "tool.executed") == 0
        # The unresolved prior approval evidence blocks staging any replacement
        # permission. The task fails honestly and retains its evidence; it does
        # not replay, execute, or overwrite that evidence with a new permission.
        parked = await recovered_runner.get_task(task_id)
        assert parked.state == TaskState.FAILED.value
        assert parked.terminal_reason == "An approved or started action has no exact verified result"
        assert parked.pending_action is None
        assert _event_count(recovered_session, task_id, "action.pending_approval") == 1
        await recovered_runner.release_lease()
        recovered_session.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_interrupt_after_action_started_before_execution_does_not_replay(tmp_path):
    task_id = "start-gap-task"
    engine = _make_file_db(str(tmp_path / "start-gap.db"))
    try:
        _create_task(engine, task_id)
        engine, session, runner, staged_action_id = await _stage_approved_pending(engine, task_id)

        # Clear pending, then record the durable action-start without allowing the
        # external action to run/commit. This is the missing safety window.
        await runner.clear_pending_action(task_id)
        ownership_acquired = await runner.acquire_task_ownership(
            task_id=task_id, worker_id=f"pid-{os.getpid()}"
        )
        assert ownership_acquired.acquired, ownership_acquired.reason
        await runner.record_action_started(
            task_id=task_id,
            action_id=staged_action_id,
            tool_name="mutating_tool",
            parameter_keys=[],
        )
        assert await runner.begin_task_action(task_id=task_id, action_id=staged_action_id) is True
        started_before = _event_count(session, task_id, "action.started")
        assert started_before == 1

        await runner.release_task_ownership(task_id=task_id, outcome="interrupted")
        await runner.release_lease()
        session.commit()
        session.close()

        recovered_session = sessionmaker(bind=engine)()
        recovered_runner = TaskRunner(recovered_session)
        recovered_replays = {"n": 0}

        async def recovered_decision(self, task_id, *args, **kwargs):
            recovered_replays["n"] += 1
            return (DecisionType.EXECUTE_TOOL, {"tool_name": "mutating_tool", "params": {}})

        original_make_decision = ModelDecisionEngine.make_decision
        ModelDecisionEngine.make_decision = recovered_decision
        try:
            recovered_loop = TaskExecutionLoop(recovered_session, recovered_runner, SpyExecutor())
            await recovered_loop.execute_task(task_id)
        finally:
            ModelDecisionEngine.make_decision = original_make_decision

        assert recovered_replays["n"] >= 1, "recovery did not invoke a subsequent model decision"
        # No replay of the already-started action: the count cannot silently grow.
        assert _event_count(recovered_session, task_id, "action.started") == started_before
        assert _event_count(recovered_session, task_id, "tool.executed") == 0
        assert staged_action_id in recovered_runner.task_ownership.uncertain_actions(task_id)
        # Unresolved started-action evidence prevents staging a replacement
        # permission and prevents replay; the task fails honestly and retains
        # the active/uncertain evidence.
        parked = await recovered_runner.get_task(task_id)
        assert parked.state == TaskState.FAILED.value
        assert parked.terminal_reason == "An action outcome is unresolved"
        assert parked.pending_action is None
        assert _event_count(recovered_session, task_id, "action.pending_approval") == 1
        await recovered_runner.release_lease()
        recovered_session.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_concurrent_ownership_prevents_second_begin_action(tmp_path):
    task_id = "concurrent-action-task"
    engine = _make_file_db(str(tmp_path / "concurrent-action.db"))
    try:
        _create_task(engine, task_id)
        owner_session = sessionmaker(bind=engine)()
        owner_runner = TaskRunner(owner_session)
        await owner_runner.acquire_lease()
        acq = await owner_runner.acquire_task_ownership(task_id=task_id, worker_id="owner-a")
        assert acq.acquired

        # In-flight action owned by worker A.
        assert owner_runner.task_ownership.begin_action(task_id, acq.nonce, "action-1") is True
        owner_session.commit()

        contender_session = sessionmaker(bind=engine)()
        contender_runner = TaskRunner(contender_session)
        await contender_runner.acquire_lease()
        contender_acq = await contender_runner.acquire_task_ownership(task_id=task_id, worker_id="owner-b")
        # While A's ownership is active, B cannot take over (even after attempting).
        assert contender_acq.acquired is False
        assert owner_runner.task_ownership.get(task_id).active_action_id == "action-1"
        try:
            contender_session.commit()
        except Exception:
            contender_session.rollback()

        # A releases; B can then acquire and sees action-1 preserved as uncertain.
        assert await owner_runner.release_task_ownership(task_id=task_id, outcome="interrupted") is True
        owner_session.commit()
        contender_session2 = sessionmaker(bind=engine)()
        contender2 = TaskRunner(contender_session2)
        await contender2.acquire_lease()
        recovered = await contender2.acquire_task_ownership(task_id=task_id, worker_id="owner-b")
        assert recovered.acquired is True
        assert "action-1" in contender2.task_ownership.uncertain_actions(task_id)

        for s in (owner_session, contender_session, contender_session2):
            try:
                s.close()
            except Exception:
                pass
        await contender2.release_lease()
    finally:
        engine.dispose()
