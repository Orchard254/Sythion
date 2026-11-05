"""Terminal receipt lifecycle acceptance: real SQLite/worker, no live actions."""

import asyncio
from concurrent.futures import ThreadPoolExecutor
from threading import Event

import pytest

from backend.api.schemas import TaskState
from backend.core.budgets import BudgetExceededError
from backend.models.database import Task, TaskEvent
from backend.runtime.followup_instruction_ledger import FollowupInstructionLedgerManager as Ledger
from backend.services.execution_components import BudgetGuard
from backend.services.task_runner import TaskRunner
from backend.tests.test_followup_worker_race_probe import sessions, seed, enqueue, worker, state


@pytest.mark.asyncio
@pytest.mark.parametrize("exit_kind", ["elapsed_budget", "action_budget", "history_exception"])
async def test_worker_failure_exit_finalizes_unapplied_receipts(sessions, monkeypatch, exit_kind):
    class Model:
        async def make_decision(self, **kwargs):
            pytest.fail("Failure must occur before inference")

    with sessions() as db:
        await seed(db)
        loop = worker(db, Model())
        if exit_kind == "history_exception":
            def broken_history(task_id):
                enqueue(sessions)
                raise RuntimeError("isolated history failure")
            monkeypatch.setattr(loop, "_get_event_history", broken_history)
            with pytest.raises(RuntimeError, match="isolated history failure"):
                await loop.execute_task("original")
        else:
            enqueue(sessions)
            if exit_kind == "elapsed_budget":
                def exhausted(self):
                    raise BudgetExceededError("TIME_BUDGET_EXCEEDED", "isolated time exhaustion")
                monkeypatch.setattr(BudgetGuard, "ensure_time_left", exhausted)
            else:
                monkeypatch.setattr(BudgetGuard, "can_execute_action", lambda self: False)
            await loop.execute_task("original")
        assert loop.tool_executor.calls == 0
    assert state(sessions) == ("failed", None, "not_applied", "  Exactly fifteen minutes.  ", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal", ["completed", "failed", "cancelled"])
async def test_receive_on_terminal_task_preserves_text_without_queueing(sessions, terminal):
    with sessions() as db:
        await seed(db)
        db.get(Task, "original").state = terminal
        db.commit()
    enqueue(sessions)
    assert state(sessions) == (terminal, None, "not_applied", "  Exactly fifteen minutes.  ", 0)
    with sessions() as db:
        receipt = Ledger(db).get("request")
        assert receipt.terminal_at is not None and receipt.terminal_reason


@pytest.mark.asyncio
@pytest.mark.parametrize("first", ["enqueue", "terminal"])
async def test_terminal_transition_and_enqueue_serialize_both_orders(sessions, first):
    with sessions() as db:
        await seed(db, opened=True)
        await TaskRunner(db).release_task_ownership("original")
    entered, release, contender_started = Event(), Event(), Event()

    def enqueue_request(hold=False):
        with sessions() as db:
            if not hold:
                contender_started.set()
            Ledger(db).receive(request_id="request", task_id="original", instruction="  Exactly fifteen minutes.  ",
                               session_id="worker-race-probe", source_exchange_id="source")
            if hold:
                entered.set()
                assert release.wait(10)
            db.commit()

    def terminate(hold=False):
        async def run():
            with sessions() as db:
                runner = TaskRunner(db)
                # Hook the actual final commit, not a fake task state transition.
                original_commit = db.commit

                def commit():
                    if hold:
                        entered.set()
                        assert release.wait(10)
                    return original_commit()

                db.commit = commit
                if not hold:
                    contender_started.set()
                await runner.force_cancel("original")
        asyncio.run(run())

    with ThreadPoolExecutor(max_workers=2) as pool:
        leader_fn, follower_fn = (enqueue_request, terminate) if first == "enqueue" else (terminate, enqueue_request)
        leader = pool.submit(leader_fn, True)
        try:
            assert entered.wait(5)
            follower = pool.submit(follower_fn)
            assert contender_started.wait(5)
            release.set()
            leader.result(timeout=15)
            follower.result(timeout=15)
        finally:
            release.set()
    assert state(sessions) == ("cancelled", None, "not_applied", "  Exactly fifteen minutes.  ", 0)


@pytest.mark.asyncio
async def test_cleanup_locks_before_pending_snapshot_and_late_enqueue_is_not_applied(sessions, monkeypatch):
    with sessions() as db:
        await seed(db)
        db.get(Task, "original").state = "completed"
        db.commit()
    snapshot_read, release, receiving = Event(), Event(), Event()

    def cleanup():
        with sessions() as db:
            ledger = Ledger(db)
            original_pending = ledger.pending

            def paused_snapshot(*a, **k):
                rows = original_pending(*a, **k)
                snapshot_read.set()
                assert release.wait(10)
                return rows

            monkeypatch.setattr(ledger, "pending", paused_snapshot)
            ledger.on_task_terminal("original", reason="completed")
            db.commit()

    def receive():
        receiving.set()
        enqueue(sessions)

    with ThreadPoolExecutor(max_workers=2) as pool:
        cleanup_future = pool.submit(cleanup)
        try:
            assert snapshot_read.wait(5)
            receive_future = pool.submit(receive)
            assert receiving.wait(5)
            release.set()
            cleanup_future.result(timeout=15)
            receive_future.result(timeout=15)
        finally:
            release.set()
    assert state(sessions) == ("completed", None, "not_applied", "  Exactly fifteen minutes.  ", 0)


@pytest.mark.asyncio
@pytest.mark.parametrize("via", ["transition", "force_cancel"])
async def test_cleanup_failure_rolls_back_terminal_state_and_propagates(sessions, monkeypatch, via):
    with sessions() as db:
        runner = await seed(db, opened=True)
        enqueue(sessions)

        def fail(*a, **k):
            raise RuntimeError("isolated receipt storage failure")

        monkeypatch.setattr(Ledger, "task_terminal", fail)
        with pytest.raises(RuntimeError, match="isolated receipt storage failure"):
            if via == "transition":
                await runner.transition_state("original", TaskState.FAILED, "task.failed", {"code": "synthetic"})
            else:
                await runner.force_cancel("original")
        # Reusing the same session must not commit a half-finished transition.
        db.commit()
    assert state(sessions) == ("running", None, "pending", "  Exactly fifteen minutes.  ", 0)
    with sessions() as db:
        assert db.query(TaskEvent).filter(TaskEvent.event_type.in_(["task.failed", "task.cancelled"])).count() == 0
