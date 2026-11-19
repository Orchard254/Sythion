"""Task decisions use the same provider-neutral ReasoningResult contract.

The conversation path (as_text_result) and the task-decision path
(as_mapping_result) must both produce the authority-free ReasoningResult
before any model output is parsed, accounted or executed.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError


class FakeModelClient:
    def __init__(self, response):
        self.response = response
        self.calls = 0

    def provider_name(self):
        return "ollama-cloud"

    async def decide(self, messages, tools, max_tokens, **kwargs):
        self.calls += 1
        return self.response


def _valid_response():
    return {
        "choices": [{"message": {"tool_calls": [{
            "function": {"name": "complete_task", "arguments": '{"result":"done"}'},
        }]}}],
        "usage": {"total_tokens": 3, "prompt_tokens": 1, "completion_tokens": 2},
    }


async def _decide(engine):
    return await engine.make_decision(
        task_id="t-result-contract",
        original_request="say done",
        normalized_goal=None,
        event_history=[],
        current_state="running",
    )


@pytest.mark.asyncio
async def test_task_decision_records_shared_result_provenance(test_db, tool_executor):
    client = FakeModelClient(_valid_response())
    engine = ModelDecisionEngine(test_db, tool_executor, model_client=client)

    decision_type, params = await _decide(engine)

    assert decision_type == "complete_task"
    assert params == {"result": "done"}
    assert client.calls == 1
    entry = engine.decision_history[0]
    assert entry["reasoning_provider_id"] == "ollama-cloud"
    assert len(entry["reasoning_job_id"]) == 32


@pytest.mark.asyncio
@pytest.mark.parametrize("terminal_state", ["completed", "failed", "cancelled"])
async def test_terminal_task_state_admits_no_reasoning_call(
    test_db, tool_executor, terminal_state,
):
    client = FakeModelClient(_valid_response())
    engine = ModelDecisionEngine(test_db, tool_executor, model_client=client)
    with pytest.raises(ModelDecisionError) as exc:
        await engine.make_decision(
            task_id="t-terminal",
            original_request="done",
            normalized_goal=None,
            event_history=[],
            current_state=terminal_state,
        )
    assert exc.value.code == "ADMISSION_DENIED"
    assert client.calls == 0


@pytest.mark.asyncio
@pytest.mark.parametrize("bad_response", ["not a mapping", ["list"], 42, True])
async def test_invalid_result_fails_closed_without_fallback(
    test_db, tool_executor, bad_response,
):
    client = FakeModelClient(bad_response)
    engine = ModelDecisionEngine(test_db, tool_executor, model_client=client)

    with pytest.raises(ModelDecisionError) as exc:
        await _decide(engine)

    assert exc.value.code == "INVALID_RESPONSE"
    assert client.calls == 1        # no retry, no fallback to another processor
    assert engine.decision_history == []
