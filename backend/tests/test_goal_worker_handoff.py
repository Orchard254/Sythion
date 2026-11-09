import asyncio
from dataclasses import FrozenInstanceError
import pytest
from backend.services.goal_worker_handoff import run_goal_worker_handoff


@pytest.mark.asyncio
async def test_verified_handoff_runs_worker_then_applier_once():
    order = []
    sentinel = object()
    async def run(db, task_id, executor):
        assert db is None and task_id == "task-1" and executor is sentinel
        order.append("run")
    async def apply(tid):
        assert tid == "task-1"
        order.append("apply")
        return {"updated": False}  # never interpreted as completion
    obs = await run_goal_worker_handoff(worker=run, task_id="task-1",
                                         tool_executor=sentinel, apply_outcome=apply)
    assert obs.settled_without_error
    assert obs.stop_reason is None
    assert order == ["run", "apply"]


@pytest.mark.asyncio
async def test_worker_exception_never_invokes_outcome_applier():
    calls = []
    async def run(*_):
        calls.append("run")
        raise RuntimeError("SECRET_PATH/private-key")
    async def apply(_):
        calls.append("apply")
    obs = await run_goal_worker_handoff(worker=run, task_id="a",
                                         tool_executor=None, apply_outcome=apply)
    assert not obs.settled_without_error
    assert obs.stop_reason == "worker_or_outcome_error"
    assert calls == ["run"]
    assert "SECRET_PATH" not in repr(obs)


@pytest.mark.asyncio
async def test_outcome_applier_error_does_not_repeat_worker():
    calls = []
    async def run(*_):
        calls.append("run")
    async def apply(_):
        calls.append("apply")
        raise ValueError("CREDENTIAL_XYZ")
    obs = await run_goal_worker_handoff(worker=run, task_id="a",
                                         tool_executor=None, apply_outcome=apply)
    assert not obs.settled_without_error
    assert calls == ["run", "apply"]
    assert "CREDENTIAL_XYZ" not in repr(obs)


@pytest.mark.asyncio
async def test_cancelled_worker_propagates():
    async def run(*_):
        raise asyncio.CancelledError()
    async def apply(_):
        pytest.fail("outcome applier must not run")
    with pytest.raises(asyncio.CancelledError):
        await run_goal_worker_handoff(worker=run, task_id="a",
                                      tool_executor=None, apply_outcome=apply)


@pytest.mark.asyncio
async def test_cancelled_outcome_applier_propagates():
    async def run(*_):
        return None
    async def apply(_):
        raise asyncio.CancelledError()
    with pytest.raises(asyncio.CancelledError):
        await run_goal_worker_handoff(worker=run, task_id="a",
                                      tool_executor=None, apply_outcome=apply)


@pytest.mark.parametrize("task_id", [None, "", "  ", 33])
@pytest.mark.asyncio
async def test_invalid_task_identity_fails_before_execution(task_id):
    async def do(*_):
        pytest.fail("must not execute")
    with pytest.raises(ValueError):
        await run_goal_worker_handoff(worker=do, task_id=task_id,
                                      tool_executor=None, apply_outcome=do)


@pytest.mark.parametrize("bad_worker,bad_applier", [(None, lambda _: None), (lambda *_: None, None), (False, lambda _: None)])
@pytest.mark.asyncio
async def test_invalid_callable_contract_fails_before_execution(bad_worker,bad_applier):
    with pytest.raises(TypeError):
        await run_goal_worker_handoff(worker=bad_worker, task_id="t",
                                      tool_executor=None, apply_outcome=bad_applier)


def test_observation_is_immutable():
    from backend.services.goal_worker_handoff import GoalWorkerHandoff
    h = GoalWorkerHandoff(True)
    with pytest.raises(FrozenInstanceError):
        h.stop_reason = "success"


@pytest.mark.asyncio
async def test_diagnostic_logs_exception_type_not_private_payload(caplog):
    async def broken(*_):
        raise RuntimeError("SECRET_PRIVATE_TOOL_RESULT")
    async def ignored(_):
        pytest.fail("must not apply after worker error")
    with caplog.at_level("WARNING"):
        observation = await run_goal_worker_handoff(worker=broken, task_id="task",
                                                     tool_executor=None, apply_outcome=ignored)
    assert not observation.settled_without_error
    assert "RuntimeError" in caplog.text
    assert "SECRET_PRIVATE_TOOL_RESULT" not in caplog.text
