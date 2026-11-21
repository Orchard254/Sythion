"""Regression tests for the app_launch advertise/visibility contract.

The runtime gates model-callable tools through ``_model_visible_tools()``. The
system prompt is built from that exact same list, so the prompt must never
advertise a tool the same mode would reject. That agreement is what prevents a
model from being told to call ``app_launch`` in a mode where the call fails.

Hidden tools stay indistinguishable from unknown tools (INVALID_DECISION_TYPE)
so the model cannot enumerate capabilities it is not allowed to use.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine, ModelDecisionError
from backend.services.model_prompt_contract import build_system_prompt


def _app_launch_response():
    return {"message": {"tool_calls": [{
        "function": {"name": "app_launch", "arguments": '{"app_id":"notepad"}'},
    }]}}


@pytest.mark.parametrize("autonomy,expected_visible", [
    ("guarded", False),
    ("autonomous", True),
])
def test_prompt_advertises_app_launch_exactly_when_visible(
    test_db, tool_executor, monkeypatch, autonomy, expected_visible,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)

    visible = {t["name"] for t in engine._model_visible_tools()}
    prompt = engine._build_system_prompt()

    assert ("app_launch" in visible) is expected_visible
    # The prompt must name the tool only if the same mode will accept its call.
    assert ("`app_launch`" in prompt) is expected_visible
    if not expected_visible:
        assert "app_launch" not in prompt


def test_prompt_without_app_launch_in_available_tools_never_advertises_it():
    prompt = build_system_prompt(
        available_tools="system_time, system_capabilities", active_autonomy="autonomous"
    )
    assert "app_launch" not in prompt
    assert "app_focus" not in prompt


def test_hidden_app_launch_call_fails_closed_without_parking_a_call(
    test_db, tool_executor, monkeypatch,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "guarded")
    engine = ModelDecisionEngine(test_db, tool_executor)

    with pytest.raises(ModelDecisionError) as exc:
        engine._parse_decision(_app_launch_response())

    # Fail closed: a hidden tool is treated like an unknown one, so the model
    # learns nothing about capabilities it cannot use.
    assert exc.value.code == "INVALID_DECISION_TYPE"
    assert engine.pop_pending_tool_calls() == []


def test_autonomous_app_launch_call_maps_to_execute_tool(
    test_db, tool_executor, monkeypatch,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    engine = ModelDecisionEngine(test_db, tool_executor)

    kind, params = engine._parse_decision(_app_launch_response())

    assert kind == "execute_tool"
    assert params["tool_name"] == "app_launch"
    assert params["params"]["app_id"] == "notepad"
