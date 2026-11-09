"""The registered app executor must reach the real goal worker, never a null placeholder."""

import pytest
from backend.services.goal_worker_binding import bind_goal_worker


def default_worker(*args):
    return args


def override_worker(*args):
    return args


def test_real_worker_uses_exact_app_executor():
    executor = object()
    calls = []
    def provide():
        calls.append(1)
        return executor
    binding = bind_goal_worker(override=None, default_worker=default_worker, executor_provider=provide)
    assert binding.available
    assert binding.stop_reason is None
    assert binding.tool_executor is executor
    assert binding.run is default_worker
    assert calls == [1]
    assert binding.run(None, "task-1", binding.tool_executor) == (None, "task-1", executor)


def test_injected_script_worker_never_asks_for_production_executor():
    def forbidden():
        raise AssertionError("Production executor must not load during scripted tests")
    b = bind_goal_worker(override=override_worker, default_worker=default_worker, executor_provider=forbidden)
    assert b.available
    assert b.tool_executor is None
    assert b.run is override_worker
    assert b.run(None, "task-2", b.tool_executor) == (None, "task-2", None)


def test_default_without_executor_fails_closed():
    b = bind_goal_worker(override=None, default_worker=default_worker, executor_provider=lambda: None)
    assert not b.available
    assert b.stop_reason == "tool_executor_unavailable"
    assert b.tool_executor is None


@pytest.mark.parametrize("reason", [RuntimeError("broken"), PermissionError("denied")])
def test_provider_failure_fails_closed_without_leaking_exception(reason):
    def broken():
        raise reason
    b = bind_goal_worker(override=None, default_worker=default_worker, executor_provider=broken)
    assert not b.available
    assert b.stop_reason == "tool_executor_unavailable"
    assert "broken" not in repr(b)
    assert "denied" not in repr(b)


@pytest.mark.parametrize("override", [0, False, "run", [], {}])
def test_invalid_script_worker_is_rejected(override):
    with pytest.raises(TypeError):
        bind_goal_worker(override=override, default_worker=default_worker, executor_provider=lambda: object())


@pytest.mark.parametrize("worker,provider", [(None, lambda: object()), (default_worker, None), ("not callable", lambda: object())])
def test_invalid_default_wiring_rejected(worker, provider):
    with pytest.raises(TypeError):
        bind_goal_worker(override=None, default_worker=worker, executor_provider=provider)


def test_no_authorization_or_tool_invocation_is_produced():
    b = bind_goal_worker(override=None, default_worker=default_worker, executor_provider=object)
    assert not hasattr(b, "authorization_issued")
    assert not hasattr(b, "task_started")
    assert not hasattr(b, "run_task_now")


def test_executor_repr_does_not_expose_private_backend_state():
    class SecretExecutor:
        def __repr__(self):
            return "SECRET_EXECUTOR_STATE"
    binding = bind_goal_worker(override=None, default_worker=default_worker, executor_provider=SecretExecutor)
    assert binding.available
    assert "SECRET_EXECUTOR_STATE" not in repr(binding)
