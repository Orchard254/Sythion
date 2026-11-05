"""Focused tests for the conservative process-local OS action guard."""

import asyncio

import pytest

from backend.runtime.action_guard import OSActionGuard


@pytest.mark.asyncio
async def test_serializes_mutating_actions():
    guard = OSActionGuard()
    timeline: list[str] = []

    async def work(name: str) -> str:
        timeline.append(f"{name}:enter")
        await asyncio.sleep(0.02)
        timeline.append(f"{name}:exit")
        return name

    first, second = await asyncio.gather(
        guard.run(work, "a"),
        guard.run(work, "b"),
    )

    assert {first, second} == {"a", "b"}
    assert timeline in (
        ["a:enter", "a:exit", "b:enter", "b:exit"],
        ["b:enter", "b:exit", "a:enter", "a:exit"],
    ), timeline


@pytest.mark.asyncio
async def test_exception_propagates_and_releases_lock():
    guard = OSActionGuard()

    async def work() -> None:
        raise ValueError("boom")

    with pytest.raises(ValueError, match="boom"):
        await guard.run(work)

    assert not guard.locked
    assert (await guard.run(lambda: "ok")) == "ok"


@pytest.mark.asyncio
async def test_cancellation_retains_lock_until_work_settles():
    guard = OSActionGuard()
    started = asyncio.Event()
    finished = asyncio.Event()

    async def work() -> str:
        started.set()
        await finished.wait()
        return "done"

    async def requester() -> str:
        return await guard.run(work)

    task = asyncio.create_task(requester())
    await started.wait()

    task.cancel()
    task.cancel()  # second cancellation must not break cleanup

    # The lock stays held while the operation is still running.
    await asyncio.sleep(0.05)
    assert guard.locked

    finished.set()
    with pytest.raises(asyncio.CancelledError):
        await task

    assert not guard.locked


@pytest.mark.asyncio
async def test_queued_requester_cancellation_does_not_corrupt_guard():
    guard = OSActionGuard()
    first_started = asyncio.Event()
    release_first = asyncio.Event()

    async def first_work() -> str:
        first_started.set()
        await release_first.wait()
        return "first"

    async def second_work() -> str:
        return "second"

    first_task = asyncio.create_task(guard.run(first_work))
    await first_started.wait()

    second_task = asyncio.create_task(guard.run(second_work))
    await asyncio.sleep(0.02)
    second_task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await second_task

    # First operation still owns the lock; cancelling the waiter must not
    # release it or silently run the queued operation.
    assert guard.locked

    release_first.set()
    assert await first_task == "first"

    assert not guard.locked
    assert await guard.run(second_work) == "second"
    assert not guard.locked


@pytest.mark.asyncio
async def test_repeated_cancellation_does_not_cancel_underlying_work():
    guard = OSActionGuard()
    started = asyncio.Event()
    release = asyncio.Event()
    operation_cancelled = {"value": False}
    operation_completed = {"value": False}

    async def work() -> str:
        started.set()
        try:
            await release.wait()
        except BaseException:
            operation_cancelled["value"] = True
            raise
        operation_completed["value"] = True
        return "settled"

    async def requester() -> str:
        return await guard.run(work)

    task = asyncio.create_task(requester())
    await started.wait()

    # Two distinct cancellation requests, separated by event-loop turns.
    task.cancel()
    await asyncio.sleep(0)
    task.cancel()
    await asyncio.sleep(0)

    # The underlying operation is not cancelled and the lock remains held.
    assert not operation_cancelled["value"]
    assert not operation_completed["value"]
    await asyncio.sleep(0.05)
    assert not operation_cancelled["value"]
    assert guard.locked

    release.set()
    await asyncio.sleep(0)  # allow the operation to settle

    with pytest.raises(asyncio.CancelledError):
        await task

    assert not operation_cancelled["value"]
    assert operation_completed["value"]
    assert not guard.locked
