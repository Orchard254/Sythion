"""Regression coverage for the historical clear-before-start interruption
windows. Production now hands off atomically; compatibility clears must still
preserve unresolved evidence and recovery must never invent success.
"""

import json
import os

import pytest
import pytest_asyncio
from sqlalchemy import create_engine, text
from sqlalchemy.orm import sessionmaker

from backend.api.schemas import TaskState
from backend.models.database import Base, Task, TaskEvent
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.model_engine import ModelDecisionEngine, DecisionType
from backend.services.task_runner import TaskRunner
from backend.services.execution_components import CompletionVerifier


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
        session.add(
            Task(
                id=task_id,
                original_request=f"request {task_id}",
                state=TaskState.CREATED.value,
                version=1,
            )
        )
        session.commit()
    finally:
        session.close()


class SpyExecutor:
    def list_tools(self):
        return []

    def get_tool(self, name):
        class Tool:
            def get_policy_requirements(self):
                return {"mutating": True, "risk_level": "medium"}

        return Tool()

    async def execute_tool(self, tool_name, input_data, policy_context=None):
        return type(
            "R",
            (),
            {
                "success": True,
                "model_summary": "ok",
                "data": None,
                "error": None,
                "evidence": None,
                "duration_ms": 1,
            },
        )()


async def _stage_approved_pending(engine, task_id):
    """Stage one permission-pending mutating action and approve it.

    Returns (engine, session, runner, action_id). The runner holds the lease;
    ``execute_task`` has released ownership/terminal state by staging the
    WAITING_FOR_USER park. Callers must handle lease/ownership themselves.
    """
    session = sessionmaker(bind=engine)()
    runner = TaskRunner(session)
    await runner.acquire_lease()

    task = await runner.get_task(task_id)
    await runner.transition_state(
        task_id, TaskState.RUNNING, "task.execution_started", {"started_at": "test"}
    )

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
    assert session.query(TaskEvent).filter_by(task_id=task_id, event_type="action.started").count() == 0

    # The staging run released the lease; the approval evidence transition still
    # requires it. Reacquire lease-only ownership evidence here.
    # Directly set the synthetic pending status field for test setup. The helper
    # refuses approved transitions because they are not authenticated consent.
    parked.pending_action = json.dumps({**json.loads(parked.pending_action), "status": "approved"})
    session.commit()

    await runner.acquire_lease()
    # User approval path.
    await runner.transition_state(
        task_id,
        TaskState.RUNNING,
        "action.approved",
        {"answer": "approve", "decision": "approved", "action_id": action_id},
        pending_question=None,
    )

    parked = await runner.get_task(task_id)
    approved_pending = json.loads(parked.pending_action)
    assert approved_pending["status"] == "approved"
    return engine, session, runner, action_id


def _event_count(session, task_id, event_type):
    return session.query(TaskEvent).filter_by(task_id=task_id, event_type=event_type).count()


@pytest.mark.asyncio
async def test_false_completion_after_clear_gap_when_model_returns_complete_task(tmp_path):
    task_id = "clear-false-complete-task"
    engine = _make_file_db(str(tmp_path / "clear-false-complete.db"))
    try:
        _create_task(engine, task_id)
        engine, session, runner, staged_action_id = await _stage_approved_pending(engine, task_id)

        # Interruption after clear_pending_action, before _execute_tool.
        await runner.clear_pending_action(task_id)
        cleared = await runner.get_task(task_id)
        assert cleared.pending_action is None
        assert cleared.state == TaskState.RUNNING.value
        before_started = _event_count(session, task_id, "action.started")
        assert before_started == 0

        await runner.release_task_ownership(task_id=task_id, outcome="interrupted")
        await runner.release_lease()
        session.commit()
        session.close()

        recovered_session = sessionmaker(bind=engine)()
        recovered_runner = TaskRunner(recovered_session)
        await recovered_runner.acquire_lease()

        recovered_calls = {"n": 0}

        async def recovered_decision(self, task_id, *args, **kwargs):
            recovered_calls["n"] += 1
            return (DecisionType.COMPLETE_TASK, {"result": "done"})

        original_make_decision = ModelDecisionEngine.make_decision
        ModelDecisionEngine.make_decision = recovered_decision
        try:
            recovered_loop = TaskExecutionLoop(
                recovered_session, recovered_runner, SpyExecutor()
            )
            await recovered_loop.execute_task(task_id)
        finally:
            ModelDecisionEngine.make_decision = original_make_decision

        after = await recovered_runner.get_task(task_id)
        # Evidence invariants that already hold (not the bug under test):
        assert recovered_calls["n"] >= 1
        assert _event_count(recovered_session, task_id, "action.started") == before_started
        assert _event_count(recovered_session, task_id, "tool.executed") == 0
        # Recovery must not report completion without successful execution.
        assert after.state != TaskState.COMPLETED.value, (
            "task was falsely reported completed despite no successful mutating tool execution"
        )

        await recovered_runner.release_lease()
        recovered_session.close()
    finally:
        engine.dispose()


@pytest_asyncio.fixture
async def approved_action(tmp_path):
    engine = _make_file_db(str(tmp_path / "atomic.db"))
    task_id = "atomic-task"
    _create_task(engine, task_id)
    _, session, runner, action_id = await _stage_approved_pending(engine, task_id)
    assert (await runner.acquire_task_ownership(task_id)).acquired
    try:
        yield engine, session, runner, task_id, action_id
    finally:
        session.rollback()
        await runner.release_task_ownership(task_id, outcome="test_cleanup")
        await runner.release_lease()
        session.close()
        engine.dispose()


def _verified_result(action_id=None):
    return {"action_id": action_id, "tool_name": "mutating_tool", "mutating": True,
            "success": True, "verification": "passed", "params": {}}


def _snapshot(engine, task_id):
    from backend.runtime.task_ownership import TaskOwnershipManager
    with sessionmaker(bind=engine)() as observer:
        task = observer.query(Task).filter_by(id=task_id).one()
        owner = TaskOwnershipManager(observer).get(task_id)
        events = [(e.event_type, json.loads(e.event_data)) for e in observer.query(TaskEvent).filter_by(task_id=task_id).order_by(TaskEvent.sequence_num).all()]
        return task.pending_action, task.active_action_id, owner.active_action_id, events


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ownership", "append", "before_commit", "after_commit"])
async def test_atomic_handoff_failure_windows(approved_action, monkeypatch, phase):
    engine, session, runner, task_id, action_id = approved_action
    before = _snapshot(engine, task_id)

    def interrupted(*args, **kwargs):
        raise RuntimeError("injected interruption")

    with monkeypatch.context() as patch:
        if phase == "ownership":
            original = runner.task_ownership.begin_action
            def begin(*args, **kwargs):
                assert original(*args, **kwargs)
                interrupted()
            patch.setattr(runner.task_ownership, "begin_action", begin)
        elif phase == "append":
            original = runner._append_event
            def append(*args, **kwargs):
                original(*args, **kwargs)
                session.flush()
                # A second connection cannot see any partial handoff.
                assert _snapshot(engine, task_id) == before
                interrupted()
            patch.setattr(runner, "_append_event", append)
        else:
            original = session.commit
            def commit():
                if phase == "after_commit":
                    original()
                interrupted()
            patch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError, match="injected"):
            await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)

    after = _snapshot(engine, task_id)
    if phase == "after_commit":
        assert after[:3] == (None, action_id, action_id)
        assert [data["action_id"] for kind, data in after[3] if kind == "action.started"] == [action_id]
    else:
        assert after == before
    assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)
    assert not any(kind == "tool.executed" for kind, _ in after[3])


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["ownership", "append", "before_commit", "after_commit"])
async def test_atomic_result_failure_windows(approved_action, monkeypatch, phase):
    engine, session, runner, task_id, action_id = approved_action
    await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    before = _snapshot(engine, task_id)
    with monkeypatch.context() as patch:
        def interrupted(*args, **kwargs):
            raise RuntimeError("injected result interruption")
        if phase == "ownership":
            original = runner.task_ownership.complete_action
            def complete(*args, **kwargs):
                assert original(*args, **kwargs)
                interrupted()
            patch.setattr(runner.task_ownership, "complete_action", complete)
        elif phase == "append":
            original = runner._append_event
            def append(*args, **kwargs):
                original(*args, **kwargs)
                session.flush()
                assert _snapshot(engine, task_id) == before
                interrupted()
            patch.setattr(runner, "_append_event", append)
        else:
            original = session.commit
            def commit():
                if phase == "after_commit":
                    original()
                interrupted()
            patch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError, match="injected result"):
            await runner.finish_mutating_action(task_id, action_id, _verified_result())
    after = _snapshot(engine, task_id)
    if phase == "after_commit":
        assert after[:3] == (None, None, None)
        results = [data for kind, data in after[3] if kind == "tool.executed"]
        assert len(results) == 1 and results[0]["action_id"] == action_id
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id) is None
    else:
        assert after == before
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Legacy synthetic approval: approved_by_user dispatch is disabled by the "
        "fail-closed independent human-consent policy, so this case never executes."
    ),
)
@pytest.mark.asyncio
async def test_normal_approved_execution_completes_once(approved_action, monkeypatch):
    engine, session, runner, task_id, action_id = approved_action
    class CountingExecutor(SpyExecutor):
        calls = 0
        async def execute_tool(self, *args, **kwargs):
            self.calls += 1
            assert _snapshot(engine, task_id)[:3] == (None, action_id, action_id)
            return await super().execute_tool(*args, **kwargs)
    executor = CountingExecutor()
    loop = TaskExecutionLoop(session, runner, executor)
    loop._ownership_lost = False
    assert await loop._execute_tool(task_id, {"tool_name": "mutating_tool", "params": {}}, approved_by_user=True, action_id=action_id)
    assert executor.calls == 1
    assert _snapshot(engine, task_id)[:3] == (None, None, None)
    assert await loop._complete_task(task_id, {"result": "done"})
    with pytest.raises(RuntimeError):
        await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    assert executor.calls == 1


@pytest.mark.asyncio
async def test_recovery_never_replays_or_resolves_with_unrelated_success(approved_action):
    engine, session, runner, task_id, action_id = approved_action
    await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    await runner.release_task_ownership(task_id, outcome="interrupted")
    with sessionmaker(bind=engine)() as recovered_session:
        recovered = TaskRunner(recovered_session)
        assert (await recovered.acquire_task_ownership(task_id)).acquired
        assert action_id in recovered.task_ownership.uncertain_actions(task_id)
        await recovered.record_event(task_id, "tool.executed", _verified_result("unrelated"))
        loop = TaskExecutionLoop(recovered_session, recovered, SpyExecutor())
        assert not await loop._complete_task(task_id, {"result": "done"})
        with pytest.raises(RuntimeError):
            await recovered.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
        with pytest.raises(RuntimeError):
            await recovered.finish_mutating_action(task_id, action_id, _verified_result())
        await recovered.release_task_ownership(task_id)


@pytest.mark.asyncio
async def test_stolen_owner_and_wrong_task_cannot_handoff_or_finish(approved_action):
    engine, session, runner, task_id, action_id = approved_action
    _create_task(engine, "other")
    with pytest.raises(RuntimeError, match="ownership"):
        await runner.start_mutating_action("other", action_id, "mutating_tool", {}, approved=True)
    await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    from backend.runtime.task_ownership import TaskOwnershipManager
    with sessionmaker(bind=engine)() as thief:
        assert TaskOwnershipManager(thief).recover(task_id, "replacement", "test forced recovery", force=True).acquired
        thief.commit()
    with pytest.raises(RuntimeError, match="ownership"):
        await runner.finish_mutating_action(task_id, action_id, _verified_result())
    assert _event_count(session, task_id, "tool.executed") == 0
    assert not runner.verify_task_ownership(task_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("change", ["params", "id", "kind", "missing", "malformed"])
async def test_legacy_or_changed_approval_is_preserved(approved_action, change):
    engine, session, runner, task_id, action_id = approved_action
    task = await runner.get_task(task_id)
    pending = json.loads(task.pending_action)
    if change == "params":
        pending["params"] = {"different": True}
    elif change == "id":
        pending["action_id"] = "different"
    elif change == "kind":
        pending.pop("kind")
    elif change == "missing":
        pending.pop("action_id")
    task.pending_action = "not json" if change == "malformed" else json.dumps(pending)
    session.commit()
    before = _snapshot(engine, task_id)
    with pytest.raises((RuntimeError, ValueError)):
        await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    assert _snapshot(engine, task_id) == before
    assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)


@pytest.mark.asyncio
async def test_evidence_read_failure_propagates_and_cannot_complete(approved_action, monkeypatch):
    _, session, runner, task_id, _ = approved_action
    from backend.runtime.task_ownership import TaskOwnershipManager
    def unavailable(*args):
        raise RuntimeError("ownership storage unavailable")
    monkeypatch.setattr(TaskOwnershipManager, "get", unavailable)
    with pytest.raises(RuntimeError, match="storage unavailable"):
        CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)
    with pytest.raises(RuntimeError):
        await runner.transition_state(task_id, TaskState.COMPLETED, "task.completed", {})
    assert (await runner.get_task(task_id)).state == TaskState.RUNNING.value


@pytest.mark.asyncio
async def test_concurrent_duplicate_handoff_has_one_winner(approved_action):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine, _, runner, task_id, action_id = approved_action
    barrier = Barrier(2)
    def contender():
        with sessionmaker(bind=engine)() as competing_session:
            competing = TaskRunner(competing_session)
            # Model two stale callers holding the same capability; even those
            # callers cannot consume a single approval twice.
            competing._task_ownership_nonce = runner._task_ownership_nonce
            competing._task_ownership_task_id = task_id
            competing._task_ownership_fence = runner._task_ownership_fence
            barrier.wait(timeout=5)
            try:
                asyncio.run(competing.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True))
                return "started"
            except RuntimeError:
                return "refused"
    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(contender) for _ in range(2)]
        results = [future.result(timeout=10) for future in futures]
    assert sorted(results) == ["refused", "started"]
    snapshot = _snapshot(engine, task_id)
    assert snapshot[:3] == (None, action_id, action_id)
    assert sum(kind == "action.started" for kind, _ in snapshot[3]) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("window", ["start_before_commit", "start_after_commit", "result_before_commit", "result_after_commit"])
async def test_abrupt_process_exit_preserves_atomic_evidence(approved_action, window):
    import subprocess
    import sys
    from pathlib import Path
    engine, session, runner, task_id, action_id = approved_action
    before = _snapshot(engine, task_id)
    script = r'''
import asyncio, os, sys
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from backend.services.task_runner import TaskRunner
engine = create_engine(sys.argv[1])
session = sessionmaker(bind=engine)()
runner = TaskRunner(session)
task_id, action_id, nonce, fence, window = sys.argv[2:]
runner._task_ownership_task_id = task_id
runner._task_ownership_nonce = nonce
runner._task_ownership_fence = int(fence)
real_commit = session.commit
def exit_commit():
    if window.endswith("after_commit"):
        real_commit()
    os._exit(17)
if window.startswith("start"):
    session.commit = exit_commit
asyncio.run(runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True))
if window.startswith("result"):
    # Represents a returned external operation. Only durable evidence, never
    # an assumed external result, may permit completion after process death.
    session.commit = exit_commit
    asyncio.run(runner.finish_mutating_action(task_id, action_id, {
        "tool_name": "mutating_tool", "mutating": True, "params": {},
        "success": True, "verification": "passed"}))
raise AssertionError("injection did not fire")
'''
    result = subprocess.run(
        [sys.executable, "-c", script, str(engine.url), task_id, action_id,
         runner._task_ownership_nonce, str(runner._task_ownership_fence), window],
        cwd=Path(__file__).resolve().parents[2], capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 17, result.stderr
    after = _snapshot(engine, task_id)
    if window == "start_before_commit":
        assert after == before
    elif window == "result_after_commit":
        assert after[:3] == (None, None, None)
        assert sum(kind == "tool.executed" for kind, _ in after[3]) == 1
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id) is None
    else:
        assert after[:3] == (None, action_id, action_id)
        assert not any(kind == "tool.executed" for kind, _ in after[3])
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)


@pytest.mark.asyncio
async def test_exact_evidence_survives_missing_task_and_owner_markers(approved_action):
    _, session, runner, task_id, action_id = approved_action
    # A legacy clear retains no in-flight markers. An unrelated success must
    # not erase this exact obligation (the rejected generic-counter fix).
    await runner.clear_pending_action(task_id)
    await runner.record_event(task_id, "tool.executed", _verified_result("other-action"))
    assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)
    with pytest.raises(RuntimeError):
        await runner.transition_state(task_id, TaskState.COMPLETED, "task.completed", {})
    assert (await runner.get_task(task_id)).state == TaskState.RUNNING.value


@pytest.mark.asyncio
async def test_changed_params_cannot_be_approved_by_matching_changed_snapshot(approved_action):
    _, session, runner, task_id, action_id = approved_action
    task = await runner.get_task(task_id)
    pending = json.loads(task.pending_action)
    pending["params"] = {"changed_after_proposal": True}
    task.pending_action = json.dumps(pending)
    session.commit()
    with pytest.raises(RuntimeError, match="durable proposal"):
        await runner.start_mutating_action(task_id, action_id, "mutating_tool", pending["params"], approved=True)
    assert (await runner.get_task(task_id)).pending_action == json.dumps(pending)


@pytest.mark.xfail(
    strict=True,
    reason=(
        "Legacy synthetic approval: approved_by_user dispatch is disabled by the "
        "fail-closed independent human-consent policy, so injected admission "
        "boundaries are never reached."
    ),
)
@pytest.mark.asyncio
@pytest.mark.parametrize("failure", ["before_start_commit", "after_start_commit", "after_external_return"])
async def test_production_executor_boundary_is_fail_closed(approved_action, monkeypatch, failure):
    _, session, runner, task_id, action_id = approved_action
    class CountingExecutor(SpyExecutor):
        calls = 0
        async def execute_tool(self, *args, **kwargs):
            self.calls += 1
            return await super().execute_tool(*args, **kwargs)
    executor = CountingExecutor()
    loop = TaskExecutionLoop(session, runner, executor)
    loop._ownership_lost = False
    real_commit = session.commit
    calls = 0
    def commit():
        nonlocal calls
        calls += 1
        if failure == "after_external_return" and calls == 1:
            return real_commit()
        if failure == "after_start_commit":
            real_commit()
        raise RuntimeError("injected production boundary")
    with monkeypatch.context() as patch:
        patch.setattr(session, "commit", commit)
        with pytest.raises(RuntimeError, match="production boundary"):
            await loop._execute_tool(task_id, {"tool_name": "mutating_tool", "params": {}}, approved_by_user=True, action_id=action_id)
    assert executor.calls == (1 if failure == "after_external_return" else 0)
    assert _event_count(session, task_id, "tool.executed") == 0
    assert not await loop._complete_task(task_id, {"result": "done"})


@pytest.mark.asyncio
@pytest.mark.parametrize("mismatch", ["id", "tool", "params", "legacy"])
async def test_unrelated_or_unlinked_execution_cannot_resolve_exact_obligation(approved_action, mismatch):
    _, session, runner, task_id, action_id = approved_action
    await runner.clear_pending_action(task_id)
    data = _verified_result(action_id)
    if mismatch == "id":
        data["action_id"] = "other-id"
    elif mismatch == "tool":
        data["tool_name"] = "other-tool"
    elif mismatch == "params":
        data["params"] = {"other": True}
    else:
        data.pop("action_id")
    await runner.record_event(task_id, "tool.executed", data)
    assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(task_id)


@pytest.mark.asyncio
@pytest.mark.parametrize("history", ["none", "read_failure", "denied"])
async def test_nonmutating_and_denied_paths_still_allow_completion(tmp_path, history):
    engine = _make_file_db(str(tmp_path / "clean.db"))
    _create_task(engine, "clean")
    with sessionmaker(bind=engine)() as session:
        runner = TaskRunner(session)
        assert (await runner.acquire_task_ownership("clean")).acquired
        await runner.transition_state("clean", TaskState.RUNNING, "task.execution_started", {})
        if history == "read_failure":
            await runner.record_event("clean", "tool.executed", {"mutating": False, "success": False})
        elif history == "denied":
            await runner.record_event("clean", "action.pending_approval", {"action_id": "denied", "tool_name": "mutating_tool", "params": {}})
            await runner.record_event("clean", "action.denied", {"action_id": "denied"})
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error("clean") is None
        await runner.transition_state("clean", TaskState.COMPLETED, "task.completed", {})
        assert (await runner.get_task("clean")).state == TaskState.COMPLETED.value
        await runner.release_task_ownership("clean")
    engine.dispose()


@pytest.mark.asyncio
async def test_completion_and_action_handoff_race_is_serialized(tmp_path):
    import asyncio
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    engine = _make_file_db(str(tmp_path / "completion-race.db"))
    _create_task(engine, "race")
    with sessionmaker(bind=engine)() as session:
        runner = TaskRunner(session)
        assert (await runner.acquire_task_ownership("race")).acquired
        await runner.transition_state("race", TaskState.RUNNING, "task.execution_started", {})
        barrier = Barrier(2)
        def contender(action):
            with sessionmaker(bind=engine)() as other_session:
                other = TaskRunner(other_session)
                other._task_ownership_nonce = runner._task_ownership_nonce
                other._task_ownership_task_id = "race"
                other._task_ownership_fence = runner._task_ownership_fence
                barrier.wait(timeout=5)
                try:
                    operation = other.start_mutating_action("race", "racing-action", "mutating_tool", {}) if action else other.transition_state("race", TaskState.COMPLETED, "task.completed", {})
                    asyncio.run(operation)
                    return True
                except RuntimeError:
                    return False
        with ThreadPoolExecutor(max_workers=2) as pool:
            futures = [pool.submit(contender, action) for action in (True, False)]
            started, completed = [future.result(timeout=10) for future in futures]
        assert started != completed
        session.expire_all()
        task = await runner.get_task("race")
        assert (task.state == TaskState.COMPLETED.value) == completed
        assert task.active_action_id == ("racing-action" if started else None)
        await runner.release_task_ownership("race")
    engine.dispose()


@pytest.mark.asyncio
@pytest.mark.parametrize("phase", ["start", "finish"])
@pytest.mark.parametrize("loss", ["expired", "fenced"])
async def test_expiry_and_fencing_token_are_enforced(approved_action, phase, loss):
    from datetime import datetime, timedelta
    from backend.runtime.task_ownership import TaskOwnershipRow
    engine, session, runner, task_id, action_id = approved_action
    if phase == "finish":
        await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    owner = session.query(TaskOwnershipRow).filter_by(task_id=task_id).one()
    if loss == "expired":
        owner.expires_at = datetime.utcnow() - timedelta(seconds=1)
    else:
        owner.fencing_token += 1
    session.commit()
    before = _snapshot(engine, task_id)
    with pytest.raises(RuntimeError, match="ownership"):
        if phase == "start":
            await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
        else:
            await runner.finish_mutating_action(task_id, action_id, _verified_result())
    assert _snapshot(engine, task_id) == before


@pytest.mark.asyncio
async def test_global_lease_cannot_complete_foreign_task_owner(approved_action):
    engine, _, runner, task_id, action_id = approved_action
    await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    await runner.finish_mutating_action(task_id, action_id, _verified_result())
    with sessionmaker(bind=engine)() as other_session:
        other = TaskRunner(other_session)
        other._leases._nonce = runner._leases.nonce
        with pytest.raises(RuntimeError, match="current owner"):
            await other.transition_state(task_id, TaskState.COMPLETED, "task.completed", {})
    assert (await runner.get_task(task_id)).state == TaskState.RUNNING.value


@pytest.mark.asyncio
@pytest.mark.parametrize("external_returned", [False, True])
async def test_atomic_handoff_recovery_loop_never_replays_or_claims_success(approved_action, monkeypatch, external_returned):
    engine, _, runner, task_id, action_id = approved_action
    await runner.start_mutating_action(task_id, action_id, "mutating_tool", {}, approved=True)
    if external_returned:
        # The external result was returned but never committed. Recovery must
        # not infer either success or absence of side effects from that gap.
        await SpyExecutor().execute_tool("mutating_tool", {})
    await runner.release_task_ownership(task_id, outcome="interrupted")
    class NoReplayExecutor(SpyExecutor):
        calls = 0
        async def execute_tool(self, *args, **kwargs):
            self.calls += 1
            raise AssertionError("recovery must not replay an uncertain action")
    async def complete_decision(*args, **kwargs):
        return DecisionType.COMPLETE_TASK, {"result": "done"}
    monkeypatch.setattr(ModelDecisionEngine, "make_decision", complete_decision)
    with sessionmaker(bind=engine)() as recovered_session:
        recovered = TaskRunner(recovered_session)
        executor = NoReplayExecutor()
        await TaskExecutionLoop(recovered_session, recovered, executor).execute_task(task_id)
        task = await recovered.get_task(task_id)
        assert task.state != TaskState.COMPLETED.value
        assert task.final_result is None
        assert executor.calls == 0
        assert action_id in recovered.task_ownership.uncertain_actions(task_id)
        assert _event_count(recovered_session, task_id, "action.started") == 1
        assert _event_count(recovered_session, task_id, "tool.executed") == 0


@pytest.mark.asyncio
async def test_marker_free_legacy_approval_cannot_be_resolved_by_event_counts(tmp_path):
    engine = _make_file_db(str(tmp_path / "legacy.db"))
    _create_task(engine, "legacy")
    with sessionmaker(bind=engine)() as session:
        runner = TaskRunner(session)
        await runner.acquire_lease()
        await runner.transition_state("legacy", TaskState.RUNNING, "task.execution_started", {})
        # Historical clear-before-start data has no action ID, pending JSON,
        # task marker or ownership row. Do not invent linkage to later tools.
        await runner.record_event("legacy", "action.pending_approval", {"tool_name": "mutating_tool", "params": {}})
        await runner.record_event("legacy", "action.approved", {"decision": "approved"})
        await runner.record_event("legacy", "tool.executed", _verified_result("unrelated"))
        assert (await runner.get_task("legacy")).pending_action is None
        assert runner.task_ownership.get("legacy") is None
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error("legacy")
        with pytest.raises(RuntimeError):
            await runner.transition_state("legacy", TaskState.COMPLETED, "task.completed", {})
        await runner.release_lease()
    engine.dispose()


@pytest.mark.asyncio
async def test_false_completion_after_start_gap_when_model_returns_complete_task(tmp_path):
    task_id = "start-false-complete-task"
    engine = _make_file_db(str(tmp_path / "start-false-complete.db"))
    try:
        _create_task(engine, task_id)
        engine, session, runner, staged_action_id = await _stage_approved_pending(engine, task_id)

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
        await recovered_runner.acquire_lease()

        async def recovered_decision(self, task_id, *args, **kwargs):
            return (DecisionType.COMPLETE_TASK, {"result": "done"})

        original_make_decision = ModelDecisionEngine.make_decision
        ModelDecisionEngine.make_decision = recovered_decision
        try:
            recovered_loop = TaskExecutionLoop(
                recovered_session, recovered_runner, SpyExecutor()
            )
            await recovered_loop.execute_task(task_id)
        finally:
            ModelDecisionEngine.make_decision = original_make_decision

        after = await recovered_runner.get_task(task_id)
        # Evidence invariants that already hold (not the bug under test):
        assert _event_count(recovered_session, task_id, "action.started") == started_before
        assert _event_count(recovered_session, task_id, "tool.executed") == 0
        assert staged_action_id in recovered_runner.task_ownership.uncertain_actions(task_id)
        # Recovery must not report completion while the action is uncertain.
        assert after.state != TaskState.COMPLETED.value, (
            "task was falsely reported completed despite an interrupted uncertain mutating action"
        )

        await recovered_runner.release_lease()
        recovered_session.close()
    finally:
        engine.dispose()


@pytest.mark.asyncio
async def test_forged_approved_by_user_never_dispatches_and_retains_uncertainty(tmp_path):
    """Independent consent must block even a staged approved pending action."""
    task_id = "forged-approval-never-dispatches"
    engine = _make_file_db(str(tmp_path / "forged-approval-never-dispatches.db"))
    try:
        _create_task(engine, task_id)
        _, session, runner, action_id = await _stage_approved_pending(engine, task_id)

        class CountingExecutor(SpyExecutor):
            calls = 0

            async def execute_tool(self, *args, **kwargs):
                self.calls += 1
                return await super().execute_tool(*args, **kwargs)

        executor = CountingExecutor()
        loop = TaskExecutionLoop(session, runner, executor)
        loop._ownership_lost = False

        result = await loop._execute_tool(
            task_id,
            {"tool_name": "mutating_tool", "params": {}},
            approved_by_user=True,
            action_id=action_id,
        )

        assert result is False
        assert executor.calls == 0
        assert _event_count(session, task_id, "action.started") == 0
        assert _event_count(session, task_id, "tool.executed") == 0

        parked = await runner.get_task(task_id)
        assert parked.state == TaskState.RUNNING.value
        assert parked.pending_action is not None
        assert json.loads(parked.pending_action)["status"] == "approved"
        assert CompletionVerifier(session, runner.ledger.event_history).precondition_error(
            task_id
        ) is not None
    finally:
        try:
            await runner.release_lease()
        except Exception:
            pass
        session.close()
        engine.dispose()
