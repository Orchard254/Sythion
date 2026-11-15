"""Strict provider selection is lazy for task inspection, never bypassed for use."""
import pytest

from backend.runtime import model_client
from backend.runtime.active_inference import ActiveInferenceUnavailable
from backend.services.task_execution_loop import TaskExecutionLoop


@pytest.mark.asyncio
async def test_deferred_model_has_no_provider_side_effects_until_decision(monkeypatch):
    factory_calls = []
    decision_calls = []
    class Fake:
        async def decide(self, messages, tools, max_tokens):
            decision_calls.append((messages, tools, max_tokens))
            return {"choices": []}
        def provider_name(self):
            return "openrouter"

    monkeypatch.setattr(model_client, "get_default_model_client",
                        lambda: (factory_calls.append("factory") or Fake()))
    deferred = model_client.DeferredActiveModelClient()
    assert factory_calls == []
    await deferred.decide([{"role": "user", "content": "hello"}], [], 100)
    assert factory_calls == ["factory"]
    await deferred.decide([], [], 50)
    assert factory_calls == ["factory"]
    assert deferred.provider_name() == "openrouter"
    assert len(decision_calls) == 2


@pytest.mark.asyncio
async def test_bad_active_provider_fails_closed_on_first_actual_decision(monkeypatch):
    factory_calls = []
    def bad_factory():
        factory_calls.append(True)
        raise ActiveInferenceUnavailable("invalid credential binding")
    monkeypatch.setattr(model_client, "get_default_model_client", bad_factory)
    deferred = model_client.DeferredActiveModelClient()
    assert factory_calls == []
    with pytest.raises(ActiveInferenceUnavailable, match="invalid credential binding"):
        await deferred.decide([], [], 100)
    assert factory_calls == [True]


@pytest.mark.asyncio
async def test_task_executor_is_constructible_without_valid_model_but_cannot_use_it(
    db_session, task_runner, monkeypatch,
):
    def no_active_selection():
        raise ActiveInferenceUnavailable("corrupt active profile")
    monkeypatch.setattr(model_client, "get_default_model_client", no_active_selection)

    # Inspection/cancellation/fixture execution may create a task loop even
    # when no provider is usable. A REAL model decision still fails closed.
    loop = TaskExecutionLoop(db_session, task_runner, tool_executor=None)
    assert isinstance(loop.model_engine.model_client, model_client.DeferredActiveModelClient)
    with pytest.raises(ActiveInferenceUnavailable, match="corrupt active profile"):
        await loop.model_engine.model_client.decide([], [], 100)
