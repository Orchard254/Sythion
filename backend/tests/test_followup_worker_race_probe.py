"""Safety regressions with real workers and concurrent file-backed SQLite sessions.

Model/tool/pipeline effects are fake; no live profiles or external actions.
"""

import asyncio
import json
from concurrent.futures import ThreadPoolExecutor
from threading import Barrier, Event
from types import SimpleNamespace

import pytest
from sqlalchemy import create_engine, event
from sqlalchemy.orm import sessionmaker

from backend.models.database import Base, Task, TaskDecisionWindow, TaskEvent
from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as Ledger
from backend.services.conversation_runtime import ConversationRuntime
from backend.services.model_engine import DecisionType, ModelDecisionError
from backend.runtime.task_ownership import TaskOwnershipRow
from backend.services.task_execution_loop import TaskExecutionLoop
from backend.services.task_instruction_revision import TaskInstructionRevision, RevisionUnavailable
from backend.services.task_runner import TaskRunner
from backend.tools.executor import ToolOutput


@pytest.fixture(params=["DELETE", "WAL"])
def sessions(tmp_path, monkeypatch, request):
    engine = create_engine(f"sqlite:///{tmp_path / 'worker-race.db'}", connect_args={"timeout": 10})

    @event.listens_for(engine, "connect")
    def configure(connection, _):
        connection.execute("PRAGMA foreign_keys=ON")
        connection.execute("PRAGMA busy_timeout=10000")

    with engine.connect() as connection:
        assert connection.exec_driver_sql(f"PRAGMA journal_mode={request.param}").scalar().upper() == request.param
        connection.commit()
    Base.metadata.create_all(engine)
    monkeypatch.setattr("backend.core.session._EXPLICIT_SESSION_ID", "worker-race-probe")
    monkeypatch.setattr("backend.services.conversation_state.update_state", lambda *a, **k: None)
    yield sessionmaker(bind=engine)
    engine.dispose()


async def seed(db, opened=False):
    db.add(Task(id="original", original_request="Make a study plan", state="running", version=2))
    db.commit()
    runtime = ConversationRuntime(db)
    await runtime._persist_conversation("Make a study plan", "Accepted", "source", task_id="original")
    runner = TaskRunner(db)
    if opened:
        assert (await runner.acquire_task_ownership("original", worker_id="probe")).acquired
        TaskInstructionRevision(runner).open("original")
    return runner


def enqueue(sessions):
    with sessions() as db:
        Ledger(db).receive(request_id="request", task_id="original", instruction="  Exactly fifteen minutes.  ",
                           session_id="worker-race-probe", source_exchange_id="source")
        db.commit()


class Executor:
    def __init__(self, entered=None, release=None):
        self.entered, self.release, self.calls = entered, release, 0

    def list_tools(self):
        return []

    def get_tool(self, name):
        return SimpleNamespace(get_policy_requirements=lambda: {"read_only": True})

    async def execute_tool(self, *a, **k):
        self.calls += 1
        assert self.entered is not None, "Unexpected tool execution"
        self.entered.set()
        await self.release.wait()
        return ToolOutput(success=True, data={"observed": True}, model_summary="Read completed")


def worker(db, model, executor=None):
    loop = TaskExecutionLoop(db, TaskRunner(db), executor or Executor())
    loop.model_engine = model

    async def no_effect(*a, **k):
        pass

    # No provider, memory consolidation, wake, skill, or conversation side effects.
    loop.post_task_pipeline = SimpleNamespace(**{name: no_effect for name in (
        "record_budget_snapshot", "apply_plan_outcome", "propose_skill_from_trace",
        "save_conversation_turn", "consolidate_memory_from_trace")})
    return loop


def state(sessions):
    with sessions() as db:
        task = db.get(Task, "original")
        receipt = Ledger(db).get("request")
        events = db.query(TaskEvent).filter_by(event_type="task.instruction_appended").count()
        return task.state, task.normalized_goal, receipt.status, receipt.instruction, events


@pytest.mark.asyncio
@pytest.mark.parametrize("arrival", ["before_start", "during_thinking", "during_read"])
async def test_worker_applies_queued_followup_before_next_decision(sessions, arrival):
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            if len(seen) == 1 and arrival == "during_thinking":
                entered.set()
                await release.wait()
            if len(seen) == 1 and arrival == "during_read":
                return DecisionType.EXECUTE_TOOL, {"tool_name": "read_probe", "params": {}}
            return DecisionType.COMPLETE_TASK, {"result": "Original plan"}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        await seed(db)
        if arrival == "before_start":
            enqueue(sessions)
        executor = Executor(entered, release)
        loop = worker(db, Model(), executor)
        work = asyncio.create_task(loop.execute_task("original"))
        try:
            if arrival != "before_start":
                await asyncio.wait_for(entered.wait(), 5)
                enqueue(sessions)
                release.set()
            await asyncio.wait_for(work, 10)
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    goal = "Make a study plan\n[User follow-up]\n  Exactly fifteen minutes.  "
    assert seen == ([goal] if arrival == "before_start" else [None, goal])
    assert executor.calls == (1 if arrival == "during_read" else 0)
    assert state(sessions) == ("completed", goal, "applied", "  Exactly fifteen minutes.  ", 1)


@pytest.mark.asyncio
async def test_actual_stop_during_model_retains_unapplied_instruction(sessions):
    entered, release = asyncio.Event(), asyncio.Event()

    class Model:
        async def make_decision(self, **kwargs):
            entered.set()
            await release.wait()
            return DecisionType.COMPLETE_TASK, {"result": "Must not complete"}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        await seed(db)
        loop = worker(db, Model())
        work = asyncio.create_task(loop.execute_task("original"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            enqueue(sessions)
            with sessions() as stop_db:
                await TaskRunner(stop_db).force_cancel("original")
            release.set()
            await asyncio.wait_for(work, 10)
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    assert state(sessions) == ("cancelled", None, "not_applied", "  Exactly fifteen minutes.  ", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("winner", ["apply", "default_exchange_apply", "cancel_receipt", "stop_task"])
async def test_writer_lock_revalidates_cached_pending_receipt(sessions, winner):
    with sessions() as db:
        await seed(db, opened=True)
    enqueue(sessions)
    stale_read, continue_apply = Event(), Event()

    def delayed_apply():
        async def run():
            with sessions() as db:
                runner = TaskRunner(db)
                # A real request/session may already have read/cached this row.
                assert Ledger(db).get("request").status == "pending"
                original_ensure = runner.task_ownership._ensure_transaction
                paused = False

                def before_lock():
                    nonlocal paused
                    if not paused:
                        paused = True
                        stale_read.set()
                        assert continue_apply.wait(10)
                    return original_ensure()

                runner.task_ownership._ensure_transaction = before_lock
                return await TaskInstructionRevision(runner).apply_followup_request(request_id="request")
        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=1, thread_name_prefix="stale-reader") as pool:
        future = pool.submit(delayed_apply)
        try:
            assert await asyncio.to_thread(stale_read.wait, 5)
            with sessions() as db:
                if winner in {"apply", "default_exchange_apply"}:
                    first = await TaskInstructionRevision(TaskRunner(db)).apply_followup_request(
                        request_id="request", exchange_id="first-application" if winner == "apply" else None)
                    assert first.status == "applied"
                elif winner == "cancel_receipt":
                    Ledger(db).cancel("request", reason="withdrawn")
                    db.commit()
                else:
                    await TaskRunner(db).force_cancel("original")
            continue_apply.set()
            if winner in {"stop_task", "cancel_receipt"}:
                with pytest.raises(RevisionUnavailable):
                    future.result(timeout=10)
            else:
                assert future.result(timeout=10).status == "applied"
        finally:
            continue_apply.set()

    with sessions() as db:
        task = db.get(Task, "original")
        count = db.query(TaskEvent).filter_by(event_type="task.instruction_appended").count()
        receipt = Ledger(db).get("request")
        if winner == "apply":
            assert count == 1 and task.normalized_goal.count("Exactly fifteen minutes.") == 1
            assert db.get(TaskDecisionWindow, "original").revision == 1
        elif winner == "cancel_receipt":
            assert count == 0 and task.normalized_goal is None and receipt.status == "cancelled"
            assert receipt.terminal_reason == "withdrawn"
        elif winner == "default_exchange_apply":
            assert count == 1 and receipt.status == "applied"
        else:
            assert count == 0 and task.state == "cancelled" and receipt.status == "not_applied"


@pytest.mark.asyncio
@pytest.mark.parametrize("old_type", [DecisionType.COMPLETE_TASK, DecisionType.EXECUTE_TOOL, "provider_error"])
async def test_enqueue_at_claim_boundary_fences_stale_response_and_entire_batch(sessions, old_type):
    seen, drained = [], []

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            if len(seen) == 1:
                if old_type == "provider_error":
                    raise ModelDecisionError("MODEL_UNAVAILABLE", "stale failure")
                return old_type, {"tool_name": "stale-tool", "result": "stale completion"}
            return DecisionType.COMPLETE_TASK, {"result": "Revised plan"}

        def pop_pending_tool_calls(self):
            if len(seen) == 1:
                drained.append(True)
                return [{"decision_type": DecisionType.EXECUTE_TOOL, "args": {"tool_name": "stale-batch"}}]
            return []

    with sessions() as db:
        await seed(db)
        loop = worker(db, Model())
        original_claim = loop.instruction_revision.claim

        def enqueue_before_claim(task_id, revision):
            if not seen[0] and len(seen) == 1:
                enqueue(sessions)
            return original_claim(task_id, revision)

        loop.instruction_revision.claim = enqueue_before_claim
        await asyncio.wait_for(loop.execute_task("original"), 10)
        assert loop.tool_executor.calls == 0
    assert len(seen) == 2 and seen[0] is None and seen[1].endswith("  Exactly fifteen minutes.  ")
    assert drained == [True]
    assert state(sessions)[::2] == ("completed", "applied", 1)


@pytest.mark.asyncio
async def test_queue_arriving_before_approved_handoff_claim_prevents_execution(sessions):
    class Model:
        async def make_decision(self, **kwargs):
            pytest.fail("Must retain approved proposal and queue for review")

    with sessions() as db:
        await seed(db)
        permission = '{"kind":"permission","status":"approved","action_id":"held","tool_name":"fake"}'
        db.get(Task, "original").pending_action = permission
        db.commit()
        loop = worker(db, Model())
        original_claim = loop.instruction_revision.claim

        def enqueue_before_claim(task_id, revision):
            enqueue(sessions)
            return original_claim(task_id, revision)

        loop.instruction_revision.claim = enqueue_before_claim
        await asyncio.wait_for(loop.execute_task("original"), 10)
        assert loop.tool_executor.calls == 0
    assert state(sessions) == ("paused", None, "pending", "  Exactly fifteen minutes.  ", 0)
    with sessions() as db:
        assert db.get(Task, "original").pending_action == permission


@pytest.mark.asyncio
@pytest.mark.parametrize("invalid", ["instruction", "session", "task", "source_exchange"])
async def test_applied_receipt_retry_still_validates_exact_identity(sessions, invalid):
    with sessions() as db:
        runner = await seed(db, opened=True)
        enqueue(sessions)
        revision = TaskInstructionRevision(runner)
        await revision.apply_followup_request(request_id="request")
        options = {"request_id": "request"}
        if invalid == "instruction":
            options["instruction"] = "Substituted text"
        elif invalid == "session":
            runtime = ConversationRuntime(db)
            runtime.session_id = "wrong-session"
            options["runtime"] = runtime
        elif invalid == "task":
            options["task_id"] = "different-task"
        else:
            options["source_exchange_id"] = "different-source"
        with pytest.raises(RevisionUnavailable):
            await revision.apply_followup_request(**options)
    assert state(sessions)[::2] == ("running", "applied", 1)


@pytest.mark.asyncio
async def test_additive_requests_remain_ordered_and_are_not_superseded(sessions):
    seen = []

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            return DecisionType.COMPLETE_TASK, {"result": "Both constraints honored"}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        with sessions() as request_db:
            Ledger(request_db).receive(request_id="second", task_id="original", instruction="\nAlso include weekends.\n",
                                       session_id="worker-race-probe", source_exchange_id="source")
            request_db.commit()
        await asyncio.wait_for(worker(db, Model()).execute_task("original"), 10)
    assert seen == ["Make a study plan\n[User follow-up]\n  Exactly fifteen minutes.  \n[User follow-up]\n\nAlso include weekends.\n"]
    with sessions() as db:
        assert [(r.request_id, r.status) for r in Ledger(db).list(task_id="original")] == [
            ("request", "applied"), ("second", "applied")]
        assert db.get(TaskDecisionWindow, "original").revision == 2
        assert db.query(Task).count() == 1


@pytest.mark.asyncio
@pytest.mark.parametrize("gate", ["permission", "approved_permission", "uncertain", "storage_failure"])
async def test_failed_safe_drain_pauses_and_retains_without_inference_or_permission_change(sessions, monkeypatch, gate):
    class Model:
        async def make_decision(self, **kwargs):
            pytest.fail("A failed drain must not be followed by inference")

    with sessions() as db:
        await seed(db)
        permission = '{"kind":"permission","status":"awaiting","action_id":"held","tool_name":"fake"}'
        if gate == "approved_permission":
            permission = permission.replace('"awaiting"', '"approved"')
        if gate in {"permission", "approved_permission"}:
            db.get(Task, "original").pending_action = permission
            db.commit()
        enqueue(sessions)
        loop = worker(db, Model())
        if gate == "uncertain":
            original_acquire = loop.runner.acquire_task_ownership

            async def acquire_with_uncertainty(*a, **k):
                result = await original_acquire(*a, **k)
                db.get(TaskOwnershipRow, "original").uncertain_action_ids = '["unresolved-effect"]'
                db.commit()
                return result

            monkeypatch.setattr(loop.runner, "acquire_task_ownership", acquire_with_uncertainty)
        if gate == "storage_failure":
            async def fail(*a, **k):
                raise RuntimeError("isolated conversation storage failure")
            monkeypatch.setattr(ConversationRuntime, "_persist_conversation", fail)
        await asyncio.wait_for(loop.execute_task("original"), 10)
        assert loop.tool_executor.calls == 0
    assert state(sessions) == ("paused", None, "pending", "  Exactly fifteen minutes.  ", 0)
    with sessions() as db:
        if gate in {"permission", "approved_permission"}:
            assert db.get(Task, "original").pending_action == permission
        if gate == "uncertain":
            assert json.loads(db.get(TaskOwnershipRow, "original").uncertain_action_ids) == ["unresolved-effect"]


@pytest.mark.asyncio
@pytest.mark.parametrize("stop", [False, True])
async def test_inflight_mutation_settles_once_before_followup_or_stop(sessions, monkeypatch, stop):
    # Only this fake executor runs; autonomy is scoped to the test process.
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    entered, release = asyncio.Event(), asyncio.Event()
    seen = []

    class Mutator(Executor):
        def get_tool(self, name):
            return SimpleNamespace(get_policy_requirements=lambda: {"mutating": True})

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            if len(seen) == 1:
                return DecisionType.EXECUTE_TOOL, {"tool_name": "fake-mutator", "params": {}}
            return DecisionType.COMPLETE_TASK, {"result": "Revised plan"}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        await seed(db)
        executor = Mutator(entered, release)
        loop = worker(db, Model(), executor)
        work = asyncio.create_task(loop.execute_task("original"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            enqueue(sessions)
            with sessions() as request_db:
                action_id = request_db.get(Task, "original").active_action_id
                assert action_id
                with pytest.raises(RevisionUnavailable):
                    await TaskInstructionRevision(TaskRunner(request_db)).apply_followup_request(request_id="request")
                assert request_db.get(Task, "original").active_action_id == action_id
                assert Ledger(request_db).get("request").status == "pending"
                if stop:
                    await TaskRunner(request_db).force_cancel("original")
            assert not work.done() and executor.calls == 1
            release.set()
            await asyncio.wait_for(work, 10)
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    assert executor.calls == 1
    if stop:
        assert seen == [None]
        assert state(sessions) == ("cancelled", None, "not_applied", "  Exactly fifteen minutes.  ", 0)
        with sessions() as db:
            assert db.get(Task, "original").active_action_id == action_id
    else:
        assert len(seen) == 2 and seen[1].endswith("  Exactly fifteen minutes.  ")
        assert state(sessions)[::2] == ("completed", "applied", 1)
        with sessions() as db:
            assert db.get(Task, "original").active_action_id is None


@pytest.mark.asyncio
async def test_applied_queue_does_not_grant_consequential_action_permission(sessions, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")

    class DestructiveExecutor(Executor):
        def get_tool(self, name):
            return SimpleNamespace(get_policy_requirements=lambda: {
                "mutating": True, "destructive": True, "risk_level": "high"})

    class Model:
        async def make_decision(self, **kwargs):
            assert kwargs["normalized_goal"].endswith("  Exactly fifteen minutes.  ")
            return DecisionType.EXECUTE_TOOL, {"tool_name": "file_delete", "params": {"path": "fake-only"}}

        def pop_pending_tool_calls(self):
            return []

    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        loop = worker(db, Model(), DestructiveExecutor())
        await asyncio.wait_for(loop.execute_task("original"), 10)
        assert loop.tool_executor.calls == 0
    with sessions() as db:
        task = db.get(Task, "original")
        assert task.state == "waiting_for_user" and task.final_result is None
        assert json.loads(task.pending_action)["status"] == "awaiting"
        assert Ledger(db).get("request").status == "applied"
        assert db.query(TaskEvent).filter_by(event_type="action.started").count() == 0


@pytest.mark.asyncio
async def test_concurrent_applications_with_distinct_exchange_ids_append_once(sessions):
    with sessions() as db:
        await seed(db, opened=True)
    enqueue(sessions)
    start = Barrier(2)

    def apply(exchange_id):
        async def run():
            with sessions() as db:
                assert Ledger(db).get("request").status == "pending"
                start.wait(timeout=10)
                return await TaskInstructionRevision(TaskRunner(db)).apply_followup_request(
                    request_id="request", exchange_id=exchange_id)
        return asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as pool:
        first = pool.submit(apply, "exchange-a")
        second = pool.submit(apply, "exchange-b")
        assert first.result(timeout=15).status == second.result(timeout=15).status == "applied"
    with sessions() as db:
        assert db.get(Task, "original").normalized_goal.count("Exactly fifteen minutes.") == 1
        assert db.query(TaskEvent).filter_by(event_type="task.instruction_appended").count() == 1
        assert db.get(TaskDecisionWindow, "original").revision == 1


@pytest.mark.asyncio
async def test_admitted_batch_finishes_once_then_drains_without_replaying(sessions):
    entered, release = asyncio.Event(), asyncio.Event()
    calls, seen = [], []

    class BatchExecutor(Executor):
        async def execute_tool(self, *, tool_name, **kwargs):
            calls.append(tool_name)
            if len(calls) == 1:
                entered.set()
                await release.wait()
            with sessions() as observer:
                assert Ledger(observer).get("request").status == "pending"
                assert observer.query(TaskEvent).filter_by(event_type="task.instruction_appended").count() == 0
            return ToolOutput(success=True, data={"observed": tool_name}, model_summary=tool_name)

    class Model:
        async def make_decision(self, **kwargs):
            seen.append(kwargs["normalized_goal"])
            if len(seen) == 1:
                return DecisionType.EXECUTE_TOOL, {"tool_name": "first-read", "params": {}}
            return DecisionType.COMPLETE_TASK, {"result": "Revised plan"}

        def pop_pending_tool_calls(self):
            if len(seen) == 1:
                return [{"decision_type": DecisionType.EXECUTE_TOOL,
                         "args": {"tool_name": "second-read", "params": {}}}]
            return []

    with sessions() as db:
        await seed(db)
        loop = worker(db, Model(), BatchExecutor())
        work = asyncio.create_task(loop.execute_task("original"))
        try:
            await asyncio.wait_for(entered.wait(), 5)
            enqueue(sessions)
            release.set()
            await asyncio.wait_for(work, 10)
        finally:
            if not work.done():
                work.cancel()
                await asyncio.gather(work, return_exceptions=True)
    assert calls == ["first-read", "second-read"]
    assert len(seen) == 2 and seen[1].endswith("  Exactly fifteen minutes.  ")
    assert state(sessions)[::2] == ("completed", "applied", 1)


@pytest.mark.asyncio
async def test_stop_before_window_open_preserves_unapplied_receipt(sessions):
    class Model:
        async def make_decision(self, **kwargs):
            pytest.fail("Stop must win before inference")

    with sessions() as db:
        await seed(db)
        enqueue(sessions)
        loop = worker(db, Model())
        original_open = loop.instruction_revision.open

        def stop_before_open(task_id):
            def stop():
                async def run():
                    with sessions() as stop_db:
                        await TaskRunner(stop_db).force_cancel(task_id)
                asyncio.run(run())
            with ThreadPoolExecutor(max_workers=1) as pool:
                pool.submit(stop).result(timeout=10)
            return original_open(task_id)

        loop.instruction_revision.open = stop_before_open
        await asyncio.wait_for(loop.execute_task("original"), 10)
        assert loop.tool_executor.calls == 0
    assert state(sessions) == ("cancelled", None, "not_applied", "  Exactly fifteen minutes.  ", 0)
