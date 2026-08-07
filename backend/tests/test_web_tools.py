"""
Unit and integration tests for Sythion's Web Search & Fetch tools.
"""

import pytest
import urllib.request
from unittest.mock import patch, MagicMock
from backend.api.schemas import WebSearchInput, WebFetchInput
from backend.tools.web_tools import (
    WebSearchTool, WebFetchTool, _parse_feed, create_web_tools,
    _url_target_error, _ValidatingRedirectHandler, _RedirectTargetRejected,
)


def test_parse_feed_basic():
    body = (
        '<rss><channel>'
        '<item><title>Test Headline</title><link>https://example.com/1</link>'
        '<source>Reuters</source><pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>'
        '</channel></rss>'
    )
    results = _parse_feed(body, max_results=5)
    assert len(results) == 1
    assert results[0]["title"] == "Test Headline"
    assert results[0]["source"] == "Reuters"
    assert results[0]["url"] == "https://example.com/1"


def test_create_web_tools_factory():
    tools = list(create_web_tools())
    names = {t.name for t in tools}
    assert "web_search" in names
    assert "web_fetch" in names


@pytest.mark.asyncio
async def test_web_search_tool_with_rss_mock(monkeypatch):
    body = (
        '<rss><channel>'
        '<item><title>Python 3.12 Released</title><link>https://python.org</link>'
        '<source>Python Blog</source><pubDate>Mon, 03 Aug 2026</pubDate></item>'
        '</channel></rss>'
    )
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: body)
    monkeypatch.setattr("backend.tools.web_tools._ddg_search", lambda q, max_results: [])
    monkeypatch.setattr("backend.tools.web_tools._ddg_lite_search", lambda q, max_results: [])
    tool = WebSearchTool()
    out = await tool.execute(WebSearchInput(query="python 3.12"))
    assert out.success is True
    assert out.data["count"] == 1
    assert out.data["results"][0]["title"] == "Python 3.12 Released"
    # The bounded model summary carries the result so the model can answer.
    assert "Python 3.12 Released" in out.model_summary
    assert "python 3.12" in out.model_summary
    assert len(out.model_summary) <= 2000


@pytest.mark.asyncio
async def test_web_search_unavailable(monkeypatch):
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: None)
    monkeypatch.setattr("backend.tools.web_tools._ddg_search", lambda q, max_results: [])
    monkeypatch.setattr("backend.tools.web_tools._ddg_lite_search", lambda q, max_results: [])
    tool = WebSearchTool()
    out = await tool.execute(WebSearchInput(query="test"))
    assert not out.success
    assert out.error["code"] == "WEB_SEARCH_UNAVAILABLE"


@pytest.mark.asyncio
async def test_web_search_falls_back_to_ddg_lite_when_rss_empty(monkeypatch):
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: None)
    monkeypatch.setattr("backend.tools.web_tools._ddg_search", lambda q, max_results: [])
    monkeypatch.setattr("backend.tools.web_tools._ddg_lite_search", lambda q, max_results: [
        {"title": "Lite Result", "snippet": "found via lite", "url": "https://lite.example"},
    ])
    from backend.tools import web_tools
    web_tools._search_cache.clear()
    tool = WebSearchTool()
    out = await tool.execute(WebSearchInput(query="latest news"))
    assert out.success is True
    assert out.data["results"][0]["title"] == "Lite Result"
    assert "Lite Result" in out.model_summary


@pytest.mark.asyncio
async def test_web_search_prefers_rss_over_ddg_lite(monkeypatch):
    body = (
        '<rss><channel>'
        '<item><title>RSS Wins</title><link>https://example.com/r</link>'
        '<pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>'
        '</channel></rss>'
    )
    monkeypatch.setattr("backend.tools.web_tools._fetch_rss", lambda q, lang: body)
    monkeypatch.setattr("backend.tools.web_tools._ddg_search", lambda q, max_results: [])
    monkeypatch.setattr("backend.tools.web_tools._ddg_lite_search", lambda q, max_results: [
        {"title": "Lite Result", "snippet": "", "url": "https://lite.example"},
    ])
    from backend.tools import web_tools
    web_tools._search_cache.clear()
    tool = WebSearchTool()
    out = await tool.execute(WebSearchInput(query="latest news"))
    assert out.success is True
    assert out.data["results"][0]["title"] == "RSS Wins"


@pytest.mark.asyncio
async def test_web_search_results_are_cached(monkeypatch):
    from backend.tools import web_tools
    calls = {"n": 0}
    body = (
        '<rss><channel>'
        '<item><title>Cached Headline</title><link>https://example.com/c</link>'
        '<pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>'
        '</channel></rss>'
    )

    def fake_rss(q, lang):
        calls["n"] += 1
        return body

    web_tools._search_cache.clear()
    monkeypatch.setattr(web_tools, "_fetch_rss", fake_rss)
    monkeypatch.setattr(web_tools, "_ddg_search", lambda q, max_results: [])
    monkeypatch.setattr(web_tools, "_ddg_lite_search", lambda q, max_results: [])
    tool = WebSearchTool()
    first = await tool.execute(WebSearchInput(query="cache-test-query"))
    second = await tool.execute(WebSearchInput(query="cache-test-query"))
    assert first.success and second.success
    assert calls["n"] == 1
    assert first.data["results"] == second.data["results"]
    web_tools._search_cache.clear()


@pytest.mark.asyncio
async def test_web_search_max_results_is_honored(monkeypatch):
    """The model-settable max_results is honored, not silently defaulted to 5."""
    from backend.tools import web_tools
    items = "".join(
        f'<item><title>Headline number {i}</title><link>https://example.com/{i}</link>'
        f'<pubDate>Mon, 03 Aug 2026 10:00:00 GMT</pubDate></item>'
        for i in range(8)
    )
    body = f"<rss><channel>{items}</channel></rss>"

    monkeypatch.setattr(web_tools, "_fetch_rss", lambda q, lang: body)
    monkeypatch.setattr(web_tools, "_ddg_search", lambda q, max_results: [])
    monkeypatch.setattr(web_tools, "_ddg_lite_search", lambda q, max_results: [])
    web_tools._search_cache.clear()
    tool = WebSearchTool()

    out = await tool.execute(WebSearchInput(query="python 3.12", max_results=8))
    assert out.success is True
    assert out.data["count"] == 8

    out_three = await tool.execute(WebSearchInput(query="python 3.12", max_results=3))
    assert out_three.success is True
    assert out_three.data["count"] == 3
    web_tools._search_cache.clear()


def test_web_search_schema_exposes_max_results_and_lang_to_model():
    """The model-facing tool schema advertises the settable fields."""
    from backend.adapters.ollama_adapter import format_tools_for_ollama
    from backend.api.schemas import WebSearchInput

    formatted = format_tools_for_ollama([{
        "name": "web_search",
        "description": "search",
        "input_schema": WebSearchInput,
    }])
    params = formatted[0]["function"]["parameters"]
    assert params["properties"]["max_results"]["type"] == "integer"
    assert params["properties"]["lang"]["type"] == "string"
    assert "max_results" not in params["required"]
    assert "lang" not in params["required"]


@pytest.mark.asyncio
async def test_web_fetch_tool_mocked():
    fetch_tool = WebFetchTool()
    mock_page = "<html><body><h1>Title</h1><p>Hello World Content</p></body></html>"
    with patch("backend.tools.web_tools._FETCH_OPENER.open") as mock_open:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_page.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_open.return_value = mock_resp

        out = await fetch_tool.execute(WebFetchInput(url="https://example.com"))
        assert out.success is True
        assert "Hello World Content" in out.data["content"]
        # The fetched text is the bounded model summary so the model can read it.
        assert "Hello World Content" in out.model_summary


@pytest.mark.asyncio
async def test_web_fetch_http_read_is_bounded():
    fetch_tool = WebFetchTool()
    big_page = "<html><body>" + ("<p>padding text content</p>" * 5000) + "</body></html>"
    with patch("backend.tools.web_tools._FETCH_OPENER.open") as mock_open:
        mock_resp = MagicMock()
        mock_resp.read.return_value = big_page.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_open.return_value = mock_resp

        out = await fetch_tool.execute(WebFetchInput(url="https://example.com"))
        assert out.success is True
        assert len(out.data["content"]) <= 3000
        assert len(out.model_summary) <= 3000


def test_web_fetch_validate_targets():
    fetch_tool = WebFetchTool()
    assert fetch_tool.validate_targets(WebFetchInput(url="")) is not None
    assert fetch_tool.validate_targets(WebFetchInput(url="   ")) is not None
    assert fetch_tool.validate_targets(WebFetchInput(url="http://127.0.0.1:8000/x")) == {
        "code": "LOOPBACK_TARGET_REJECTED",
        "message": "Loopback targets are not allowed",
    }
    assert fetch_tool.validate_targets(WebFetchInput(url="localhost")) == {
        "code": "LOOPBACK_TARGET_REJECTED",
        "message": "Loopback targets are not allowed",
    }
    assert fetch_tool.validate_targets(WebFetchInput(url="https://example.com/page")) is None


def test_redirect_handler_rejects_loopback_hops():
    """A redirect hop to 127.0.0.1/localhost is rejected, not followed."""
    handler = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    for bad in ("http://127.0.0.1:8000/admin", "http://localhost/secret", "https://[::1]/x"):
        with pytest.raises(_RedirectTargetRejected) as exc:
            handler.redirect_request(req, None, 302, "Found", {}, bad)
        assert exc.value.error == {
            "code": "LOOPBACK_TARGET_REJECTED",
            "message": "Loopback targets are not allowed",
        }


def test_redirect_handler_allows_public_multi_hop():
    """Legitimate multi-hop redirects to public URLs still resolve."""
    handler = _ValidatingRedirectHandler()
    req = urllib.request.Request("https://example.com/start")
    hop1 = handler.redirect_request(req, None, 302, "Found", {}, "https://b.example/mid")
    assert hop1 is not None
    assert "b.example" in hop1.full_url
    hop2 = handler.redirect_request(hop1, None, 301, "Moved", {}, "https://c.example/end")
    assert hop2 is not None
    assert "c.example" in hop2.full_url


def test_redirect_handler_caps_redirect_chain():
    """Redirects are bounded below the stdlib default to avoid amplification."""
    assert _ValidatingRedirectHandler.max_redirections == 5


@pytest.mark.asyncio
async def test_web_fetch_rejected_redirect_surfaces_loopback_error(monkeypatch):
    """A redirect rejected mid-chain fails like an initially-rejected URL."""
    from backend.tools import web_tools

    def fake_open(req, timeout=None):
        raise _RedirectTargetRejected({
            "code": "LOOPBACK_TARGET_REJECTED",
            "message": "Loopback targets are not allowed",
        })

    monkeypatch.setattr(web_tools._FETCH_OPENER, "open", fake_open)
    tool = WebFetchTool()
    out = await tool.execute(WebFetchInput(url="https://example.com/start"))
    assert out.success is False
    assert out.error == {
        "code": "LOOPBACK_TARGET_REJECTED",
        "message": "Loopback targets are not allowed",
    }


@pytest.mark.asyncio
async def test_web_fetch_public_redirect_chain_still_succeeds(monkeypatch):
    """A chain that resolves to a public page still yields the fetched text."""
    from backend.tools import web_tools

    page = b"<html><body><p>Final public page content</p></body></html>"
    mock_resp = MagicMock()
    mock_resp.read.return_value = page
    mock_resp.__enter__.return_value = mock_resp

    monkeypatch.setattr(web_tools._FETCH_OPENER, "open", lambda req, timeout=None: mock_resp)
    tool = WebFetchTool()
    out = await tool.execute(WebFetchInput(url="https://example.com/start"))
    assert out.success is True
    assert "Final public page content" in out.data["content"]


def test_web_search_policy_read_only():
    reqs = WebSearchTool().get_policy_requirements()
    assert reqs["requires_confirmation"] is False
    assert reqs["read_only"] is True
    assert reqs["mutating"] is False
