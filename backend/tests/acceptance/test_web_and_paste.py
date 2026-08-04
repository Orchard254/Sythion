"""
Acceptance gate: web read tool + clipboard paste (Phase C+ enablement).

web_search fetches bounded news headlines via Google News RSS (stdlib only,
read-only, no confirmation). gui_paste inserts text into a foreground window
via the clipboard, gated like gui_type (high risk, confirmation required).
"""

import types

import pytest

from backend.tools.executor import ToolExecutor, PolicyContext
from backend.tools.web_tools import create_web_tools, WebSearchTool, _parse_feed
from backend.tools.gui_tools import create_gui_tools


def _fixture(tag, tools):
    ex = ToolExecutor()
    for t in tools:
        ex.register_tool(t)
    return ex


@pytest.mark.asyncio
async def test_web_search_returns_bounded_headlines(monkeypatch):
    """web_search parses the RSS feed and stays bounded + read-only."""
    body = (
        '<rss><channel>'
        '<item><title>   Kenya headline one   </title><link>https://a.example/k1</link>'
        '<source>The Star</source><pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>'
        '<item><title>Kenya headline two with a very long title that should be truncated to keep the context small '
        'and bounded and not blow up the model window at all</title><link>https://b.example/k2</link>'
        '<pubDate>Mon, 03 Aug 2026 09:00:00 GMT</pubDate></item>'
        '</channel></rss>'
    )
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: body)
    tool = WebSearchTool()
    inp = types.SimpleNamespace(query="kenya", lang="en", max_results=2)
    out = await tool.execute(inp)
    assert out.success
    results = out.data["results"]
    assert len(results) == 2
    assert results[0]["title"].strip() == "Kenya headline one"
    assert results[0]["source"] == "The Star"
    # Titles are bounded to a max char length.
    assert all(len(r["title"]) <= 200 for r in results)
    # model_summary is present, bounded, and carries the headlines the model
    # needs to answer from; it does not leak a raw full body.
    assert out.data["model_summary"]
    assert "Kenya headline one" in out.data["model_summary"]
    assert len(out.data["model_summary"]) <= 2000
    assert "body" not in out.data


@pytest.mark.asyncio
async def test_web_search_feed_unreachable(monkeypatch):
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: None)
    monkeypatch.setattr("backend.tools.web_tools._ddg_search", lambda q, max_results: [])
    tool = WebSearchTool()
    out = await tool.execute(types.SimpleNamespace(query="x", lang="en", max_results=1))
    assert not out.success
    assert out.error["code"] == "WEB_SEARCH_UNAVAILABLE"


def test_web_search_read_only_and_visible():
    """web_search is low-risk, no-confirmation, and model-visible."""
    reqs = WebSearchTool().get_policy_requirements()
    assert reqs["requires_confirmation"] is False
    assert reqs["read_only"] is True

    from backend.services.model_engine import ModelDecisionEngine
    from backend.tools.system_tools import create_system_tools
    ex = _fixture("x", list(create_web_tools()) + list(create_system_tools()))
    visible = {t["name"] for t in ModelDecisionEngine(None, ex)._model_visible_tools()}
    assert "web_search" in visible


def test_routes_executor_includes_web_tools():
    """The API executor (which the model sees) must include web + memory tools.

    Regression: routes.py built its own 28-tool executor without web/memory
    tools, so the model was never offered web_search/web_fetch even though
    main.py registered them.
    """
    from backend.api.routes import get_tool_executor
    ex = get_tool_executor()
    names = set(ex.list_tools())
    assert "web_search" in names
    assert "web_fetch" in names
    assert "memory_store" in names
    assert "memory_recall" in names

    from backend.services.model_engine import ModelDecisionEngine
    visible = {t["name"] for t in ModelDecisionEngine(None, ex)._model_visible_tools()}
    assert "web_search" in visible
    assert "web_fetch" in visible


@pytest.mark.asyncio
async def test_gui_paste_is_approval_gated(monkeypatch):
    """paste, like type, is high-risk and requires confirmation (not model-run)."""
    from backend.api.schemas import GUIPasteInput
    from backend.tools.gui_tools import create_gui_tools

    tools = {t.name: t for t in create_gui_tools()}
    assert "gui_paste" in tools
    reqs = tools["gui_paste"].get_policy_requirements()
    assert reqs["requires_confirmation"] is True
    assert reqs["clipboard_access"] is True
    assert reqs["risk_level"] == "high"


def test_gui_paste_validate_targets(monkeypatch):
    from backend.tools.gui_tools import create_gui_tools, _gui_target_error
    tool = next(t for t in create_gui_tools() if t.name == "gui_paste")
    # An empty window title is rejected during target validation.
    err = tool.validate_targets(types.SimpleNamespace(window_title="   "))
    assert err is not None
    assert err["code"] == "TARGET_REQUIRED"