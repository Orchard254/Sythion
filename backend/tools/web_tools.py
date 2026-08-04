"""
Web search and fetch tools for Sythion (Synth).
Allows Sythion to search the internet and read web pages to answer queries.

Supports two search backends:
1. DuckDuckGo HTML scraping (primary, no API key required)
2. Google News RSS parsing (legacy, used by acceptance tests)
"""

import re
import asyncio
import html
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Iterable, Tuple

try:
    from backend.tools.executor import BaseTool
    from backend.api.schemas import (
        ToolOutput,
        WebSearchInput,
        WebFetchInput,
    )
except ImportError:
    from tools.executor import BaseTool
    from api.schemas import (
        ToolOutput,
        WebSearchInput,
        WebFetchInput,
    )

_USER_AGENT = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"

# Bounded output so tool results never balloon the model context or the
# durable event ledger. Web content is untrusted data and reaches the model
# only through a bounded, redacted summary.
_MAX_FETCH_CHARS = 3000
_MAX_RESPONSE_BYTES = 2_000_000
_MAX_SNIPPET_CHARS = 300
_MAX_SEARCH_SUMMARY_CHARS = 2000
_LOOPBACK_HOSTS = frozenset({"localhost", "localhost.localdomain"})

# Timeouts are deliberately short so a slow/failed backend surfaces fast
# instead of stalling the server for the full urllib default.
_SEARCH_TIMEOUT_SECONDS = 6
_FETCH_TIMEOUT_SECONDS = 8

# Transient in-memory result cache (never persisted). Keyed by query so
# repeated or follow-up lookups within a task are answered instantly without
# another network round trip. Bounded by count and TTL.
_SEARCH_CACHE_TTL_SECONDS = 300
_SEARCH_CACHE_MAX = 256
_search_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def _http_get(url: str, timeout: int = _SEARCH_TIMEOUT_SECONDS,
              max_bytes: Optional[int] = None) -> bytes:
    """Fetch a URL body with the browser user agent. Returns raw bytes."""
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return resp.read(max_bytes) if max_bytes else resp.read()


# ---------------------------------------------------------------------------
# RSS helper (legacy, used by acceptance tests)
# ---------------------------------------------------------------------------

def _fetch_rss(query: str, lang: str = "en") -> Optional[str]:
    """Fetch Google News RSS feed body. Returns None on error."""
    url = f"https://news.google.com/rss/search?q={urllib.parse.quote(query)}&hl={lang}"
    try:
        return _http_get(url, timeout=_SEARCH_TIMEOUT_SECONDS).decode("utf-8", errors="ignore")
    except Exception:
        return None


def _parse_feed(body: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Parse an RSS XML body into a list of result dicts."""
    results = []
    try:
        root = ET.fromstring(body)
        for item in root.iter("item"):
            if len(results) >= max_results:
                break
            title = (item.findtext("title") or "").strip()[:200]
            link = (item.findtext("link") or "").strip()
            source_el = item.find("source")
            source = source_el.text.strip() if source_el is not None and source_el.text else ""
            pub_date = (item.findtext("pubDate") or "").strip()
            results.append({
                "title": title,
                "url": link,
                "source": source,
                "published": pub_date,
            })
    except ET.ParseError:
        pass
    return results


# ---------------------------------------------------------------------------
# DuckDuckGo HTML search (primary backend)
# ---------------------------------------------------------------------------

def _ddg_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Search DuckDuckGo HTML and extract result entries."""
    url = f"https://html.duckduckgo.com/html/?q={urllib.parse.quote(query)}"
    body = _http_get(url, timeout=_SEARCH_TIMEOUT_SECONDS).decode("utf-8", errors="ignore")

    results = []
    link_matches = list(re.finditer(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        body, re.IGNORECASE | re.DOTALL))
    snippet_matches = list(re.finditer(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>',
        body, re.IGNORECASE | re.DOTALL))

    for i in range(min(len(link_matches), max_results)):
        raw_href, raw_title = link_matches[i].groups()
        raw_snippet = snippet_matches[i].group(1) if i < len(snippet_matches) else ""

        title = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()[:200]
        snippet = html.unescape(re.sub(r'<[^>]+>', '', raw_snippet)).strip()[:_MAX_SNIPPET_CHARS]

        actual_url = raw_href
        if "uddg=" in raw_href:
            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
            if "uddg" in parsed_qs:
                actual_url = parsed_qs["uddg"][0]

        results.append({"title": title, "snippet": snippet, "url": actual_url})
    return results


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

def _cached_search_results(cache_key: str) -> Optional[List[Dict[str, str]]]:
    """Return fresh cached results for a search key, or None on a miss."""
    entry = _search_cache.get(cache_key)
    if entry and time.monotonic() - entry[0] < _SEARCH_CACHE_TTL_SECONDS:
        return entry[1]
    return None


def _cache_search_results(cache_key: str, results: List[Dict[str, str]]) -> None:
    """Store results for a search key, evicting expired/oldest entries."""
    now = time.monotonic()
    _search_cache[cache_key] = (now, results)
    if len(_search_cache) > _SEARCH_CACHE_MAX:
        expired = [
            key for key, (ts, _) in _search_cache.items()
            if now - ts >= _SEARCH_CACHE_TTL_SECONDS
        ]
        for key in expired:
            del _search_cache[key]
        while len(_search_cache) > _SEARCH_CACHE_MAX:
            oldest = min(_search_cache, key=lambda key: _search_cache[key][0])
            del _search_cache[oldest]


def _search_summary(query: str, results: List[Dict[str, str]]) -> str:
    """Build the bounded model-facing summary for a set of search results.

    Only this redacted view of the results reaches the model context; the
    structured results live in `data` for the audit event.
    """
    lines = [f"Top {len(results)} results for '{query}':"]
    for i, result in enumerate(results, 1):
        title = result.get("title", "").strip()
        snippet = result.get("snippet", "").strip()
        url = result.get("url", "").strip()
        entry = f"{i}. {title}"
        if snippet:
            entry += f" - {snippet}"
        entry += f" ({url})"
        lines.append(entry[:400])
    return "\n".join(lines)[:_MAX_SEARCH_SUMMARY_CHARS]

class WebSearchTool(BaseTool):
    """Search the internet for real-time information."""

    name = "web_search"
    description = "Search the internet for real-time news, weather, facts, or information"
    input_schema = WebSearchInput

    async def execute(self, input_data) -> ToolOutput:
        try:
            query = getattr(input_data, "query", "").strip()
            if not query:
                return ToolOutput(
                    success=False,
                    error={"code": "EMPTY_QUERY", "message": "Search query cannot be empty"},
                    duration_ms=0
                )

            max_results = getattr(input_data, "max_results", 5)
            lang = getattr(input_data, "lang", "en")

            # Serve repeat/follow-up queries from the transient cache. The
            # network calls are moved off the event loop so a slow backend
            # never freezes the whole server.
            cache_key = f"{lang}|{max_results}|{query}"
            results = _cached_search_results(cache_key)
            if results is None:
                # Race both backends concurrently and prefer RSS results.
                # The hard cap keeps a hung backend from stalling the tool
                # for the full sequential timeout sum.
                async def _collect():
                    rss_task = asyncio.create_task(
                        asyncio.to_thread(_fetch_rss, query, lang))
                    ddg_task = asyncio.create_task(
                        asyncio.to_thread(_ddg_search, query, max_results))
                    # Prefer RSS: return its results the moment they arrive and
                    # cancel the slower DDG probe, so the tool never waits on
                    # the slowest backend. Only fall back to DDG if RSS is
                    # empty or still running.
                    rss_done, _ = await asyncio.wait(
                        {rss_task}, timeout=_SEARCH_TIMEOUT_SECONDS)
                    if rss_done:
                        try:
                            rss_body = rss_task.result()
                        except Exception:
                            rss_body = None
                        parsed = _parse_feed(rss_body, max_results) if rss_body else []
                        if parsed:
                            ddg_task.cancel()
                            return parsed
                    ddg_done, _ = await asyncio.wait(
                        {ddg_task}, timeout=_SEARCH_TIMEOUT_SECONDS)
                    if ddg_done:
                        try:
                            res = ddg_task.result()
                        except Exception:
                            res = None
                        return res if isinstance(res, list) else []
                    return []

                try:
                    results = await asyncio.wait_for(
                        _collect(), timeout=_SEARCH_TIMEOUT_SECONDS)
                except asyncio.TimeoutError:
                    results = []
                if results:
                    _cache_search_results(cache_key, results)

            if not results:
                return ToolOutput(
                    success=False,
                    error={"code": "WEB_SEARCH_UNAVAILABLE", "message": "No search results available"},
                    duration_ms=0
                )

            summary = _search_summary(query, results)
            return ToolOutput(
                success=True,
                data={
                    "query": query, "count": len(results), "results": results,
                    "model_summary": summary,
                },
                model_summary=summary,
                evidence={"source": "web_search"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "WEB_SEARCH_ERROR", "message": f"Web search failed: {e}"},
                duration_ms=0
            )

    async def verify(self, input_data, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
            "read_only": True,
        }


class WebFetchTool(BaseTool):
    """Fetch and extract clean text from a web page URL."""

    name = "web_fetch"
    description = "Fetch and read text content from a web page URL"
    input_schema = WebFetchInput

    def validate_targets(self, input_data: WebFetchInput) -> Optional[Dict[str, str]]:
        """Deterministic, side-effect-free URL gate before any network call.

        Rejects empty/malformed URLs and loopback targets so a social-engineered
        request cannot exfiltrate local services (SSRF) through the fetch path.
        """
        raw_url = getattr(input_data, "url", "").strip()
        if not raw_url:
            return {"code": "TARGET_REQUIRED", "message": "A URL is required"}
        candidate = raw_url if raw_url.startswith(("http://", "https://")) else "https://" + raw_url
        parsed = urllib.parse.urlparse(candidate)
        if parsed.scheme not in ("http", "https"):
            return {"code": "INVALID_URL_SCHEME", "message": "Only http/https URLs may be fetched"}
        host = (parsed.hostname or "").lower()
        if not host:
            return {"code": "INVALID_URL", "message": "URL must include a host"}
        if host in _LOOPBACK_HOSTS or host.startswith("127.") or host == "::1":
            return {"code": "LOOPBACK_TARGET_REJECTED", "message": "Loopback targets are not allowed"}
        return None

    async def execute(self, input_data: WebFetchInput) -> ToolOutput:
        try:
            target_url = input_data.url.strip()
            if not target_url.startswith(("http://", "https://")):
                target_url = "https://" + target_url

            # Run the blocking download off the event loop so the API stays
            # responsive while a slow page is being fetched. The hard cap
            # bounds total wait even if the socket timeout is missed (DNS).
            try:
                raw_body = await asyncio.wait_for(
                    asyncio.to_thread(
                        _http_get, target_url,
                        _FETCH_TIMEOUT_SECONDS, _MAX_RESPONSE_BYTES
                    ),
                    timeout=_FETCH_TIMEOUT_SECONDS + 2
                )
            except asyncio.TimeoutError:
                return ToolOutput(
                    success=False,
                    error={"code": "WEB_FETCH_TIMEOUT",
                           "message": "Timed out fetching the page"},
                    duration_ms=0
                )
            body = raw_body.decode("utf-8", errors="ignore")

            # Remove scripts, styles, and non-content tags
            cleaned = re.sub(r'<(script|style|svg|header|footer|nav)[^>]*>.*?</\1>', '', body, flags=re.IGNORECASE | re.DOTALL)
            text = re.sub(r'<[^>]+>', ' ', cleaned)
            text = html.unescape(text)
            text = re.sub(r'\s+', ' ', text).strip()
            
            truncated_text = text[:_MAX_FETCH_CHARS]

            summary = f"Fetched {len(truncated_text)} chars of text content from {target_url}"
            return ToolOutput(
                success=True,
                data={"url": target_url, "length": len(truncated_text), "content": truncated_text},
                model_summary=truncated_text,
                evidence={"source": "http_web_fetch"}
            )
        except Exception as e:
            return ToolOutput(
                success=False,
                error={"code": "WEB_FETCH_ERROR", "message": f"Web fetch failed: {e}"},
                duration_ms=0
            )

    async def verify(self, input_data: WebFetchInput, output: ToolOutput) -> bool:
        return output.success

    def get_policy_requirements(self) -> Dict[str, Any]:
        return {
            "risk_level": "none",
            "requires_confirmation": False,
            "mutating": False,
            "read_only": True,
        }


# ---------------------------------------------------------------------------
# Factory (backward-compatible with acceptance tests)
# ---------------------------------------------------------------------------

def create_web_tools() -> Iterable[BaseTool]:
    """Create and return all web tools."""
    return [WebSearchTool(), WebFetchTool()]
