"""Model visibility and prompt honesty must agree with executor dispatch policy.

A tool that the executor can never auto-dispatch must not be advertised to the
model: the prompt is built from ``_model_visible_tools()``, so a mismatch there
is what previously produced "Unknown decision type: app_launch" after the model
was told to call a tool the runtime rejects.

The single authority is the conjunction the executor itself uses in
``_policy_error``: ``is_reviewed_safe_builtin(tool)`` and
``_safe_automatic_dispatch_metadata(requirements)``. This module verifies the
engine uses exactly that predicate in *both* autonomy modes.
"""

import pytest

from backend.services.model_engine import ModelDecisionEngine
from backend.tools.executor import (
    ToolExecutor,
    is_reviewed_safe_builtin,
    _safe_automatic_dispatch_metadata,
)


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_visible_set_equals_executor_auto_dispatch_safe_set(
    test_db, tool_executor, monkeypatch, autonomy,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)
    visible = {t["name"] for t in engine._model_visible_tools()}

    expected = {
        name
        for name in tool_executor.list_tools()
        if (tool := tool_executor.get_tool(name))
        and is_reviewed_safe_builtin(tool)
        and _safe_automatic_dispatch_metadata(tool.get_policy_requirements())
    }
    assert visible == expected


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_no_undispatchable_tool_is_model_visible(
    test_db, tool_executor, monkeypatch, autonomy,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)
    visible = {t["name"] for t in engine._model_visible_tools()}

    for name in visible:
        tool = tool_executor.get_tool(name)
        reqs = tool.get_policy_requirements()
        assert not reqs.get("mutating"), name
        assert not reqs.get("destructive"), name
        assert not reqs.get("requires_confirmation"), name
        assert not reqs.get("app_internal"), name
        assert not reqs.get("requires_admin"), name
        assert not reqs.get("privacy_sensitive"), name
        assert not reqs.get("requires_screen_access"), name
        assert not reqs.get("requires_ui_automation"), name
        assert reqs.get("risk_level", "none") in ("none", "low", None), name
        assert not name.startswith("gui_"), name

    # Concrete tools that must never be advertised in either mode.
    for name in ("app_launch", "app_focus", "file_write", "memory_store", "gui_hotkey"):
        assert name not in visible


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_reviewed_safe_read_only_tool_stays_visible_in_both_modes(
    test_db, tool_executor, monkeypatch, autonomy,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)
    visible = {t["name"] for t in engine._model_visible_tools()}

    assert "system_time" in visible
    reqs = tool_executor.get_tool("system_time").get_policy_requirements()
    assert reqs.get("risk_level") in ("none", "low")
    assert not reqs.get("requires_confirmation")
    assert is_reviewed_safe_builtin(tool_executor.get_tool("system_time"))
    assert _safe_automatic_dispatch_metadata(reqs)


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_prompt_never_advertises_undispatchable_tools(
    test_db, tool_executor, monkeypatch, autonomy,
):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)
    prompt = engine._build_system_prompt()

    # The prompt's tool guidance is keyed on available_tools; these three are
    # never in that list, so it must not advertise them in either mode.
    for name in ("app_launch", "app_focus", "file_write"):
        assert name not in prompt


def test_autonomous_prompt_does_not_promise_mutating_execution(test_db, tool_executor, monkeypatch):
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", "autonomous")
    engine = ModelDecisionEngine(test_db, tool_executor)
    prompt = engine._build_system_prompt()

    assert "Execute mutating and destructive actions directly" not in prompt
    assert "trusted consent boundary that is not available" in prompt
    assert "only call tools listed in AVAILABLE TOOLS" in prompt
    assert "report the limitation honestly" in prompt


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_prompt_with_only_undispatchable_tools_advertises_none(
    test_db, tool_executor, monkeypatch, autonomy,
):
    """Regression for the exact failure shape: those are the only tools present."""
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    only_undispatchable = ToolExecutor()
    for name in ("app_launch", "app_focus", "file_write"):
        only_undispatchable.register_tool(tool_executor.get_tool(name))
    engine = ModelDecisionEngine(test_db, only_undispatchable)

    assert engine._model_visible_tools() == []
    prompt = engine._build_system_prompt()
    for name in ("app_launch", "app_focus", "file_write"):
        assert name not in prompt
    assert "AVAILABLE TOOLS" in prompt
