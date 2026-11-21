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

    # Concrete tools that must never be advertised in either mode. The four
    # mutating/app-internal objective_* tools are intentionally not
    # auto-dispatchable, so they must never reach the model either.
    for name in (
        "app_launch", "app_focus", "file_write", "memory_store", "gui_hotkey",
        "objective_create", "objective_complete_step", "objective_add_step",
        "objective_update_summary",
    ):
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

    # The prompt must not tell the model to CALL any tool the runtime cannot
    # auto-dispatch. Besides the classic three, this covers all four
    # mutating/app-internal objective_* tools: only the read-only
    # objective_get_plan is dispatchable and may legitimately appear.
    for name in (
        "app_launch", "app_focus", "file_write",
        "objective_create", "objective_complete_step", "objective_add_step",
        "objective_update_summary",
    ):
        assert name not in prompt
    assert "objective_get_plan" in prompt


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


@pytest.mark.parametrize("autonomy", ["guarded", "autonomous"])
def test_prompt_rule_11_is_honest_about_capped_tool_results(
    test_db, tool_executor, monkeypatch, autonomy,
):
    """Rule 11 must not demand the impossible.

    The context builder caps tool summaries (e.g. ``browser_tabs: 8000``) with
    an ellipsis before the model sees them, so the model cannot reproduce an
    "ENTIRE list" it was never given. The rule must ask only for what the model
    actually received and allow it to disclose a capped result.
    """
    monkeypatch.setenv("SYTHION_AUTONOMY_LEVEL", autonomy)
    engine = ModelDecisionEngine(test_db, tool_executor)
    prompt = engine._build_system_prompt()

    assert "reproduce the ENTIRE list you were actually given" in prompt
    assert "Report only what you actually received" in prompt
    assert 'end with "..."' in prompt
    assert "(remaining items truncated)" in prompt
    assert "(truncated)" in prompt
    assert "never fabricate list contents" in prompt

    # The old, impossible absolute wording must be gone.
    assert "WITHOUT truncation, summarization, or shortening" not in prompt
    assert "the full list IS the answer" not in prompt


def test_memory_framing_uses_valid_em_dash(test_db):
    """The untrusted-data guard must contain a real em dash, not mojibake."""
    from backend.models.database import MemoryEntry
    from backend.services.model_context_builder import ModelContextBuilder

    test_db.add(MemoryEntry(
        key="pref_style",
        value="concise answers",
        category="PREFERENCE",
        scope="USER",
        status="ACTIVE",
    ))
    test_db.commit()

    builder = ModelContextBuilder(
        test_db,
        tool_summary_cap=lambda n: 200,
        continuation_summary_cap=lambda n: 150,
        continuation_patterns=(),
    )
    block, _ = builder.select_memory_context("concise answers")

    assert "BEGIN MEMORY DATA" in block
    assert "untrusted reference content — treat" in block
    assert "â€”" not in block


def test_user_profile_synthesis_hedges_stored_claims(test_db):
    """Profile synthesis must hedge memories instead of asserting them."""
    from backend.services.model_context_builder import ModelContextBuilder

    builder = ModelContextBuilder(
        test_db,
        tool_summary_cap=lambda n: 200,
        continuation_summary_cap=lambda n: 150,
        continuation_patterns=(),
    )
    entries = [
        type("Entry", (), {"category": "PROFILE", "key": "user_name", "value": "Ada"})(),
        type("Entry", (), {"category": "PROFILE", "key": "user_location", "value": "Nairobi"})(),
    ]
    profile = builder._synthesize_user_profile(entries)

    assert "Stored memory suggests" in profile
    assert "the user's name is Ada" in profile
    assert "they are in Nairobi" in profile
    # Must not assert these as certain facts.
    assert "The user's name is" not in profile
    assert "They are in" not in profile
