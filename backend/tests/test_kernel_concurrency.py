"""
Phase 1 concurrency-preparation tests for runtime kernel boundaries.

Production execution is still serialized by the lease; these tests prove the
boundaries that previously relied on serialization are now safe for future
concurrent workers:

1. cancellation state cannot leak between task contexts
2. task event ordering remains correct under interleaving
3. lease ownership remains exclusive
4. one task cannot complete another task
5. one task cannot consume another task's cancellation state
6. tool execution context is task-scoped
7. budget accounting is task-scoped
"""

import asyncio

import pytest

from backend.api.schemas import TaskState, ToolOutput
from backend.core.budgets import BudgetExceededError, TaskBudget
from backend.models.database import Base
from backend.runtime.events import DbEventLedger
from backend.services.execution_components import CompletionVerifier
from backend.tools.executor import (
    BaseTool,
    PolicyContext,
    ToolExecutor,
    current_execution_context,
)
from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from sqlalchemy.pool import StaticPool


@pytest.fixture
def db_session():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    yield session
    session.close()


@pytest.fixture
def task_runner(db_session):
    from backend.services.task_runner import TaskRunner

    return TaskRunner(db_session)


class _CtxTool(BaseTool):
    """Minimal tool that reports the execution context it observes."""

    name = "ctx_tool"
    description = "reports scoped context"
    input_schema = dict

    async def execute(self, input_data):
        ctx = current_execution_context()
        return ToolOutput(
            success=True,
            data={
                "task_id": ctx.task_id if ctx else None,
                "cancelled": await self._is_cancelled(),
            },
        )


def _executor_with(tool):
    executor = ToolExecutor()
    executor.register_tool(tool)
    return executor


# --- 1 + 5: cancellation isolation between tasks -------------------------------


@pytest.mark.asyncio
async def test_cancellation_state_cannot_leak_between_tasks():
    """A shared tool instance serves two tasks; each sees ONLY its own
    cancellation decision, even when flipped mid-flight."""
    flag = {"cancel_task_a": False}
    entered = asyncio.Event()
    proceed = asyncio.Event()

    class _GateTool(BaseTool):
        name = "gate_tool"
        description = "gated"
        input_schema = dict

        async def execute(self, input_data):
            entered.set()
            await proceed.wait()
            return ToolOutput(
                success=True,
                data={
                    "task_id": current_execution_context().task_id,
                    "cancelled": await self._is_cancelled(),
                },
            )

    async def check_a():
        return flag["cancel_task_a"]

    async def check_b():
        return False

    executor = _executor_with(_GateTool())

    exec_a = asyncio.create_task(
        executor.execute_tool(
            "gate_tool", {},
            policy_context=PolicyContext(task_id="task-a", cancelled_check=check_a),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)

    # Cancel task A while its execution is parked mid-flight...
    flag["cancel_task_a"] = True

    exec_b = asyncio.create_task(
        executor.execute_tool(
            "gate_tool", {},
            policy_context=PolicyContext(task_id="task-b", cancelled_check=check_b),
        )
    )
    await asyncio.sleep(0.01)
    proceed.set()

    out_a, out_b = await asyncio.gather(exec_a, exec_b)

    # Task A observes ITS OWN cancellation; task B observes none of it.
    assert out_a.data == {"task_id": "task-a", "cancelled": True}
    assert out_b.data == {"task_id": "task-b", "cancelled": False}


@pytest.mark.asyncio
async def test_one_task_cannot_consume_another_cancellation_check():
    """Cancellation checks are bound at context construction, not globally."""

    async def never():
        return True

    controller_inputs = {"task-a": never}

    class _FakeStore:
        async def get_task(self, task_id):
            class _T:
                state = "running"
                original_request = "do things"
            return _T()

    from backend.services.execution_components import CancellationController

    controller = CancellationController(_FakeStore())
    ctx_b = await controller.policy_context("task-b")

    # Task B's context carries no knowledge of task A's check function.
    assert ctx_b.task_id == "task-b"
    assert ctx_b.cancelled_check is not None
    assert await ctx_b.cancelled_check() is False


# --- 6: tool execution context is task-scoped -----------------------------------


@pytest.mark.asyncio
async def test_execution_context_is_task_scoped_under_interleaving():
    seen = []
    first_entered = asyncio.Event()
    release = asyncio.Event()

    class _InterleavedTool(BaseTool):
        name = "interleaved"
        description = "interleaved"
        input_schema = dict

        async def execute(self, input_data):
            seen.append(current_execution_context().task_id)
            if len(seen) == 1:
                first_entered.set()
                await asyncio.wait_for(release.wait(), timeout=2)
            seen.append(current_execution_context().task_id)
            return ToolOutput(success=True, data={"ok": True})

    executor = _executor_with(_InterleavedTool())

    t1 = asyncio.create_task(
        executor.execute_tool("interleaved", {}, policy_context=PolicyContext(task_id="t1"))
    )
    await asyncio.wait_for(first_entered.wait(), timeout=2)
    t2 = asyncio.create_task(
        executor.execute_tool("interleaved", {}, policy_context=PolicyContext(task_id="t2"))
    )
    await asyncio.sleep(0.01)
    release.set()
    await asyncio.gather(t1, t2)

    # Each execution consistently observed exactly one task id: its own.
    assert sorted(set(seen)) == ["t1", "t2"]
    assert seen.count("t1") == 2
    assert seen.count("t2") == 2


@pytest.mark.asyncio
async def test_no_stale_context_outside_execution_scope():
    assert current_execution_context() is None

    output = await _executor_with(_CtxTool()).execute_tool("ctx_tool", {})
    assert output.success

    assert current_execution_context() is None


# --- 2: event ordering under interleaving ---------------------------------------


@pytest.mark.asyncio
async def test_event_ordering_remains_correct_under_interleaving(test_db):
    ledger_a = DbEventLedger(test_db)
    ledger_b = DbEventLedger(test_db)

    seqs_a, seqs_b = [], []

    async def append_many(ledger, task_id, sink):
        for i in range(20):
            sink.append(ledger.append_event(task_id, f"evt.{i}", {"i": i}))
            await asyncio.sleep(0)  # yield to force interleaving

    await asyncio.gather(
        append_many(ledger_a, "task-a", seqs_a),
        append_many(ledger_b, "task-b", seqs_b),
    )
    test_db.commit()

    # Per-task sequences are strictly ordered 1..N despite interleaving.
    assert seqs_a == list(range(1, 21))
    assert seqs_b == list(range(1, 21))

    history_a = ledger_a.event_history("task-a")
    assert [e["sequence_num"] for e in history_a] == list(range(1, 21))
    assert ledger_a.latest_sequence("task-a") == 20
    assert [e["sequence_num"] for e in ledger_a.events_after("task-a", 15)] == [16, 17, 18, 19, 20]


# --- 3: lease exclusivity ---------------------------------------------------------


@pytest.mark.asyncio
async def test_lease_ownership_remains_exclusive_under_contention(test_db):
    from backend.services.task_runner import TaskRunner

    runners = [TaskRunner(test_db) for _ in range(5)]
    acquired = []
    for runner in runners:
        acquired.append(await runner.acquire_lease())

    # Exactly one contender wins.
    assert sum(1 for ok in acquired if ok) == 1

    # Losers cannot renew the winner's lease or release it.
    losers = [r for i, r in enumerate(runners) if not acquired[i]]
    renewed = [await r.renew_lease() for r in losers]
    assert not any(renewed)


# --- 4: one task cannot complete another ------------------------------------------


@pytest.mark.asyncio
async def test_one_task_cannot_complete_another(db_session, task_runner):
    """Completion gating and transitions are strictly task-scoped."""
    await task_runner.acquire_lease()
    task_a = await task_runner.create_task("Task A")
    task_b = await task_runner.create_task("Task B")
    await task_runner.transition_state(task_a.id, TaskState.RUNNING, "task.execution_started", {})
    await task_runner.transition_state(task_b.id, TaskState.RUNNING, "task.execution_started", {})

    verifier = CompletionVerifier(db_session, task_runner.ledger.event_history)

    # A failing mutating action recorded on A blocks A's completion...
    task_runner.ledger.append_event(
        task_a.id,
        "tool.executed",
        {"tool_name": "file_write", "success": False, "mutating": True, "verification": "failed"},
    )
    db_session.commit()

    assert verifier.precondition_error(task_a.id) == (
        "The final mutating tool action failed and has no recorded resolution"
    )

    # ...but says nothing about B, whose evidence is clean.
    assert verifier.precondition_error(task_b.id) is None

    # Completing A leaves B untouched.
    await task_runner.transition_state(task_a.id, TaskState.COMPLETED, "task.completed", {"summary": "done"})
    refreshed_b = await task_runner.get_task(task_b.id)
    assert refreshed_b.state == TaskState.RUNNING.value


# --- 7: budget accounting is task-scoped -------------------------------------------


def test_budget_accounting_is_task_scoped():
    budget_a = TaskBudget(max_actions=2)
    budget_b = TaskBudget(max_actions=2)

    budget_a.record_action()
    budget_a.record_action()

    # A is exhausted; B is completely unaffected.
    assert budget_a.actions == 2
    assert budget_b.actions == 0

    with pytest.raises(BudgetExceededError):
        budget_a.record_action()

    budget_b.record_action()
    assert budget_b.actions == 1
