"""Process-local asynchronous guard for mutating operating-system actions.

This module provides one small primitive: an asynchronous guard that serializes
mutating OS actions within a single running Python process and event loop. It is
a foundation only and is not wired into the executor, routes, or tools.

Usage
-----

    guard = OSActionGuard()

    async def mutating_action() -> str:
        # perform the mutating OS action here
        ...
        return "done"

    result = await guard.run(mutating_action)

``run`` acquires the process-local lock, starts ``mutating_action`` as an
asyncio task, shields it from cancellation of the requesting coroutine, and
does not release the lock until that task has actually settled. Cancellation of
the requester is therefore propagated to the caller, but it cannot interrupt
the guarded operation or let a later acquirer overlap with it.

Scope and limitations
---------------------

* The guard is process-local. It only coordinates coroutines running in the
  same OS process and event loop. It cannot coordinate separate processes.
* The guard cannot make a non-cooperating external process or blocking call
  settle. It only waits for the coroutine passed to ``run``. If that coroutine
  starts work in another thread or process and returns before that work is done,
  the guard's guarantee ends when the coroutine returns.
* It does not fence other processes or already-running external actions by
  itself. Cross-process exclusion and crash recovery remain separate concerns
  (for example the runner lease and database coordination layers).
* Nested calls to ``run`` on the same guard from the same coroutine deadlock,
  because the lock is not reentrant.
* ``mutating_action`` should be cooperative. A coroutine that never returns
  will keep the lock held until the process ends.
"""


from __future__ import annotations

import asyncio
import inspect
from typing import Any, Callable


class OSActionGuard:
    """Serialize mutating OS actions within one process and event loop."""

    def __init__(self) -> None:
        self._lock = asyncio.Lock()

    @property
    def locked(self) -> bool:
        """Whether the underlying lock is currently held."""
        return self._lock.locked()

    async def run(
        self,
        operation: Callable[..., Any],
        *args: Any,
        **kwargs: Any,
    ) -> Any:
        """Run one mutating operation while holding the process-local lock.

        ``operation`` may be a coroutine function or a callable returning an
        awaitable. It is started as a task and shielded while awaited, so
        cancellation of this coroutine does not cancel the operation. The lock
        is not released until the operation task has actually settled, even when
        this coroutine is cancelled repeatedly.
        """
        async with self._lock:
            task = asyncio.create_task(self._invoke(operation, *args, **kwargs))
            try:
                return await asyncio.shield(task)
            except BaseException as original:
                # The operation must settle before the lock is released. Repeated
                # requester cancellation can surface during each await, so keep
                # shielding and yielding until the task is done. The underlying
                # task is never cancelled by this method.
                while not task.done():
                    try:
                        await asyncio.shield(task)
                    except BaseException:
                        try:
                            await asyncio.shield(asyncio.sleep(0))
                        except BaseException:
                            pass
                # Consume the operation's stored result/exception so it is not
                # left as an unhandled task exception.
                try:
                    task.result()
                except BaseException:
                    pass
                raise original

    @staticmethod
    async def _invoke(
        operation: Callable[..., Any], *args: Any, **kwargs: Any
    ) -> Any:
        result = operation(*args, **kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


__all__ = ["OSActionGuard"]
