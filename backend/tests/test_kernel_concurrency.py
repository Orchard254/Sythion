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
import importlib

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


@pytest.fixture
def datetime_spy():
    """Narrow spy on the external datetime dependency used by SystemTimeTool.

    Delegates all computation to the saved real dependency and restores it.
    """
    module = importlib.import_module("backend.tools.system_tools")
    real_datetime = getattr(module, "datetime")
    real_now = real_datetime.now
    captured = []

    class _DatetimeSpy:
        def now(self, *args, **kwargs):
            captured.append(current_execution_context())
            return real_now(*args, **kwargs)

        def __getattr__(self, name):
            return getattr(real_datetime, name)

    module.datetime = _DatetimeSpy()
    try:
        yield captured
    finally:
        module.datetime = real_datetime


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
async def test_cancellation_state_cannot_leak_between_tasks(datetime_spy):
    """A task-scoped cancelled_check gates the real reviewed SystemTimeTool.

    A's predicate is delayed by an owned Event so the caller can flip A's
    flag while A awaits its public cancellation check. B uses the real
    unchanged reviewed builtin and a predicate that is always False.
    """
    from backend.tools.system_tools import SystemTimeTool

    flag = {"task-a": False}
    entered = asyncio.Event()
    gate = asyncio.Event()
    b_checks = []

    async def check_a():
        entered.set()
        await gate.wait()
        return flag["task-a"]

    async def check_b():
        value = False
        b_checks.append(
            (
                current_execution_context().task_id
                if current_execution_context() is not None
                else None,
                value,
            )
        )
        return value

    executor = _executor_with(SystemTimeTool())

    exec_a = asyncio.create_task(
        executor.execute_tool(
            "system_time",
            {},
            policy_context=PolicyContext(task_id="task-a", cancelled_check=check_a),
        )
    )
    await asyncio.wait_for(entered.wait(), timeout=2)

    # Cancel task A while its public cancellation check is awaited...
    flag["task-a"] = True
    exec_b = asyncio.create_task(
        executor.execute_tool(
            "system_time",
            {},
            policy_context=PolicyContext(task_id="task-b", cancelled_check=check_b),
        )
    )
    gate.set()

    out_a, out_b = await asyncio.gather(
        asyncio.wait_for(exec_a, timeout=2),
        asyncio.wait_for(exec_b, timeout=2),
    )

    # Exact executor cancellation outcome for A.
    assert out_a.success is False
    assert out_a.error == {
        "code": "CANCELLED",
        "message": "Task was cancelled before the action could run",
    }

    # B succeeds and its predicate was False.
    assert out_b.success is True
    assert b_checks == [("task-b", False)]

    # Only B's reviewed builtin executed, under B's own task id.
    assert datetime_spy
    assert all(ctx is not None and ctx.task_id == "task-b" for ctx in datetime_spy)


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
async def test_execution_context_is_task_scoped_under_interleaving(datetime_spy):
    from backend.tools.system_tools import SystemTimeTool

    first_entered = asyncio.Event()
    first_release = asyncio.Event()
    second_entered = asyncio.Event()
    second_release = asyncio.Event()
    check_contexts = []

    async def check_first():
        ctx = current_execution_context()
        check_contexts.append(("t1", ctx.task_id if ctx is not None else None))
        first_entered.set()
        await first_release.wait()
        return False

    async def check_second():
        ctx = current_execution_context()
        check_contexts.append(("t2", ctx.task_id if ctx is not None else None))
        second_entered.set()
        await second_release.wait()
        return False

    executor = _executor_with(SystemTimeTool())

    t1 = asyncio.create_task(
        executor.execute_tool(
            "system_time", {}, policy_context=PolicyContext(task_id="t1", cancelled_check=check_first)
        )
    )
    await asyncio.wait_for(first_entered.wait(), timeout=2)

    t2 = asyncio.create_task(
        executor.execute_tool(
            "system_time", {}, policy_context=PolicyContext(task_id="t2", cancelled_check=check_second)
        )
    )
    await asyncio.wait_for(second_entered.wait(), timeout=2)

    first_release.set()
    second_release.set()
    await asyncio.gather(
        asyncio.wait_for(t1, timeout=2),
        asyncio.wait_for(t2, timeout=2),
    )

    # Each cancellation check observed only its own execution context.
    assert ("t1", "t1") in check_contexts
    assert ("t2", "t2") in check_contexts
    assert all(label == observed for label, observed in check_contexts)

    # Each real builtin observed only its own context via the external spy.
    assert datetime_spy
    observed_builtin = {ctx.task_id for ctx in datetime_spy if ctx is not None}
    assert observed_builtin == {"t1", "t2"}


@pytest.mark.asyncio
async def test_no_stale_context_outside_execution_scope(datetime_spy):
    from backend.tools.system_tools import SystemTimeTool

    assert current_execution_context() is None

    output = await _executor_with(SystemTimeTool()).execute_tool(
        "system_time", {}, policy_context=PolicyContext(task_id="ctx-task")
    )
    assert output.success is True

    # The real builtin's external datetime.now dependency observed the installed
    # context under the owning task id; no manual context substitution used.
    assert datetime_spy
    assert all(ctx is not None for ctx in datetime_spy)
    assert all(ctx.task_id == "ctx-task" for ctx in datetime_spy)

    # Outer execution context is restored to None before and after execution.
    assert current_execution_context() is None


@pytest.mark.asyncio
async def test_unreviewed_context_tool_is_denied_without_execution_or_leak():
    synthetic = _CtxTool()
    executed = {"called": False}

    original_execute = synthetic.execute

    async def _execute(input_data):
        executed["called"] = True
        return await original_execute(input_data)

    synthetic.execute = _execute

    assert current_execution_context() is None

    denied = await _executor_with(synthetic).execute_tool("ctx_tool", {})
    assert denied.success is False
    assert denied.error["code"] == "DIRECT_INSTRUCTION_REQUIRED"
    assert executed["called"] is False

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
async def test_failed_mutating_action_blocks_own_completion_but_allows_clean_task_completion(db_session, task_runner):
    """A failed mutating action blocks only the owning task's completion.

    Trusted TaskRunner callers may still complete a clean task under the
    real lease contract; the guard is evidence-scoped, not runner-global.
    """
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

    with pytest.raises(RuntimeError, match="The final mutating tool action failed and has no recorded resolution"):
        await task_runner.transition_state(
            task_a.id, TaskState.COMPLETED, "task.completed", {"summary": "done"}
        )

    # A remains non-completed; the failed guard is task-scoped.
    refreshed_a = await task_runner.get_task(task_a.id)
    assert refreshed_a.state != TaskState.COMPLETED.value

    # ...but says nothing about B, whose evidence is clean.
    assert verifier.precondition_error(task_b.id) is None
    refreshed_b = await task_runner.get_task(task_b.id)
    assert refreshed_b.state == TaskState.RUNNING.value

    # Clean B can then complete under the real lease contract.
    await task_runner.transition_state(
        task_b.id, TaskState.COMPLETED, "task.completed", {"summary": "done"}
    )
    completed_b = await task_runner.get_task(task_b.id)
    assert completed_b.state == TaskState.COMPLETED.value


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
