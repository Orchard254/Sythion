"""
Unit and integration tests for Sythion's Web Search & Fetch tools.
"""

import pytest
from unittest.mock import patch, MagicMock
from backend.api.schemas import WebSearchInput, WebFetchInput
from backend.tools.web_tools import WebSearchTool, WebFetchTool, _parse_feed, create_web_tools


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
    tool = WebSearchTool()
    out = await tool.execute(WebSearchInput(query="test"))
    assert not out.success
    assert out.error["code"] == "WEB_SEARCH_UNAVAILABLE"


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
    tool = WebSearchTool()
    first = await tool.execute(WebSearchInput(query="cache-test-query"))
    second = await tool.execute(WebSearchInput(query="cache-test-query"))
    assert first.success and second.success
    assert calls["n"] == 1
    assert first.data["results"] == second.data["results"]
    web_tools._search_cache.clear()


@pytest.mark.asyncio
async def test_web_fetch_tool_mocked():
    fetch_tool = WebFetchTool()
    mock_page = "<html><body><h1>Title</h1><p>Hello World Content</p></body></html>"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = mock_page.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

        out = await fetch_tool.execute(WebFetchInput(url="https://example.com"))
        assert out.success is True
        assert "Hello World Content" in out.data["content"]
        # The fetched text is the bounded model summary so the model can read it.
        assert "Hello World Content" in out.model_summary


@pytest.mark.asyncio
async def test_web_fetch_http_read_is_bounded():
    fetch_tool = WebFetchTool()
    big_page = "<html><body>" + ("<p>padding text content</p>" * 5000) + "</body></html>"
    with patch("urllib.request.urlopen") as mock_urlopen:
        mock_resp = MagicMock()
        mock_resp.read.return_value = big_page.encode("utf-8")
        mock_resp.__enter__.return_value = mock_resp
        mock_urlopen.return_value = mock_resp

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


def test_web_search_policy_read_only():
    reqs = WebSearchTool().get_policy_requirements()
    assert reqs["requires_confirmation"] is False
    assert reqs["read_only"] is True
    assert reqs["mutating"] is False
