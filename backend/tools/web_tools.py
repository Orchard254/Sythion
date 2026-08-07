"""
Web search and fetch tools for Sythion (Synth).
Allows Sythion to search the internet and read web pages to answer queries.

Supports three search backends (raced concurrently, Google News RSS preferred):
1. Google News RSS (preferred; most dependable, no API key required)
2. DuckDuckGo HTML scraping (fallback, no API key required)
3. DuckDuckGo Lite (secondary fallback)
"""

import re
import asyncio
import html
import logging
import time
import urllib.request
import urllib.parse
import xml.etree.ElementTree as ET
from typing import Dict, Any, List, Optional, Iterable, Tuple

logger = logging.getLogger(__name__)

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

# Timeouts are deliberately bounded so a slow/failed backend surfaces instead
# of stalling the server for the full urllib default. The search budget is the
# highest common denominator of the slowest backend (RSS often needs ~7s).
_SEARCH_TIMEOUT_SECONDS = 12
_FETCH_TIMEOUT_SECONDS = 8

# Transient in-memory result cache (never persisted). Keyed by query so
# repeated or follow-up lookups within a task are answered instantly without
# another network round trip. Bounded by count and TTL.
_SEARCH_CACHE_TTL_SECONDS = 300
_SEARCH_CACHE_MAX = 256
_search_cache: Dict[str, Tuple[float, List[Dict[str, str]]]] = {}


def _http_get(url: str, timeout: int = _SEARCH_TIMEOUT_SECONDS,
              max_bytes: Optional[int] = None) -> bytes:
    """Fetch a URL body with the browser user agent. Returns raw bytes.

    Uses a dedicated opener whose redirect handler re-runs the loopback target
    gate on every hop, so a public URL cannot smuggle a fetch to a loopback or
    internal address via a Location redirect.
    """
    req = urllib.request.Request(url, headers={"User-Agent": _USER_AGENT})
    with _FETCH_OPENER.open(req, timeout=timeout) as resp:
        return resp.read(max_bytes) if max_bytes else resp.read()


# ---------------------------------------------------------------------------
# SSRF-safe fetching (loopback gate applies to redirects, not just the start)
# ---------------------------------------------------------------------------

def _url_target_error(url: str) -> Optional[Dict[str, str]]:
    """Deterministic, side-effect-free URL gate shared by initial validation
    and every redirect hop.

    Rejects empty/malformed URLs, non-http(s) schemes, and loopback hosts so a
    social-engineered request (or a public site that redirects there) cannot
    exfiltrate local services (SSRF). Returns an error dict or None.
    """
    raw = (url or "").strip()
    if not raw:
        return {"code": "TARGET_REQUIRED", "message": "A URL is required"}
    candidate = raw if raw.startswith(("http://", "https://")) else "https://" + raw
    parsed = urllib.parse.urlparse(candidate)
    if parsed.scheme not in ("http", "https"):
        return {"code": "INVALID_URL_SCHEME", "message": "Only http/https URLs may be fetched"}
    host = (parsed.hostname or "").lower()
    if not host:
        return {"code": "INVALID_URL", "message": "URL must include a host"}
    if host in _LOOPBACK_HOSTS or host.startswith("127.") or host == "::1":
        return {"code": "LOOPBACK_TARGET_REJECTED", "message": "Loopback targets are not allowed"}
    return None


class _RedirectTargetRejected(Exception):
    """A redirect hop failed the loopback gate.

    Carries the exact error dict from the shared `_url_target_error` gate so the
    executor can fail the fetch with the same code/message pattern as an
    initially-rejected URL — no new error type is introduced.
    """

    def __init__(self, error: Dict[str, str]):
        self.error = error
        super().__init__(error.get("message", "Redirect target rejected"))


class _ValidatingRedirectHandler(urllib.request.HTTPRedirectHandler):
    """Follow redirects only after re-running the SSRF target gate per hop.

    urllib's default handler follows `Location` headers transparently, which
    lets a public URL redirect into a loopback/internal address and bypass the
    loopback rejection on the requested URL. urllib resolves a relative
    `Location` to an absolute URL before `redirect_request`, so the hop target
    here is always an absolute http(s) URL. The stdlib default caps total
    redirects at 10 (`max_redirections`); we tighten it to 5 so a redirect
    chain cannot be used as a separate amplification vector.
    """

    max_redirections = 5

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        error = _url_target_error(newurl)
        if error:
            raise _RedirectTargetRejected(error)
        return super().redirect_request(req, fp, code, msg, headers, newurl)


_FETCH_OPENER = urllib.request.build_opener(_ValidatingRedirectHandler())


# ---------------------------------------------------------------------------
# RSS helper (legacy, used by acceptance tests)
# ---------------------------------------------------------------------------

def _fetch_rss(query: str, lang: str = "en") -> Optional[str]:
    """Fetch Google News RSS feed body. Returns None on error.

    This is the most dependable backend, so a slow first attempt gets one
    retry before we admit failure. The extra `gl`/`ceid` params pin the
    request to the requested locale instead of whatever CDN edge answers.
    """
    url = (
        "https://news.google.com/rss/search?q=" + urllib.parse.quote(query)
        + f"&hl={lang}&gl={lang.upper()}&ceid={lang.upper()}:{lang}"
    )
    # 8s per attempt keeps a retry inside the search's overall budget while
    # still being generous for the slow (but reliable) Google News edge.
    for attempt in range(2):
        try:
            return _http_get(url, timeout=8).decode("utf-8", errors="ignore")
        except Exception:
            if attempt == 1:
                return None
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
# DuckDuckGo Lite search (tertiary backend)
# ---------------------------------------------------------------------------

def _ddg_lite_search(query: str, max_results: int = 5) -> List[Dict[str, str]]:
    """Search DuckDuckGo's Lite endpoint and extract result entries.

    Lite is a separate DDG surface that is frequently still served when the
    main HTML endpoint rate-limits or blocks us, so it acts as a fallback
    rather than a duplicate request.
    """
    url = "https://lite.duckduckgo.com/lite/?q=" + urllib.parse.quote(query)
    try:
        body = _http_get(url, timeout=_SEARCH_TIMEOUT_SECONDS).decode("utf-8", errors="ignore")
    except Exception:
        return []

    results = []
    link_matches = list(re.finditer(
        r'<a[^>]*rel="nofollow"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        body, re.IGNORECASE | re.DOTALL))
    for i in range(min(len(link_matches), max_results)):
        raw_href, raw_title = link_matches[i].groups()
        title = html.unescape(re.sub(r'<[^>]+>', '', raw_title)).strip()[:200]
        if not title:
            continue
        actual_url = raw_href
        if "uddg=" in raw_href:
            parsed_qs = urllib.parse.parse_qs(urllib.parse.urlparse(raw_href).query)
            if "uddg" in parsed_qs:
                actual_url = parsed_qs["uddg"][0]
        results.append({"title": title, "snippet": "", "url": actual_url})
    return results

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


# ---------------------------------------------------------------------------
# Tool classes
# ---------------------------------------------------------------------------

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

            # These are real schema fields on WebSearchInput (bounded 1-10 / free
            # string), so the values the model passes are honored by Pydantic.
            max_results = input_data.max_results
            lang = input_data.lang

            # Serve repeat/follow-up queries from the transient cache. The
            # network calls are moved off the event loop so a slow backend
            # never freezes the whole server.
            cache_key = f"{lang}|{max_results}|{query}"
            results = _cached_search_results(cache_key)
            if results is None:
                # Race every backend concurrently against a single deadline so
                # a hung backend cannot stall the tool or starve the others,
                # then prefer RSS and fall back to the first backend that
                # actually returned something.
                async def _collect():
                    loop = asyncio.get_running_loop()
                    deadline = loop.time() + _SEARCH_TIMEOUT_SECONDS
                    tasks = {
                        "rss": asyncio.create_task(
                            asyncio.to_thread(_fetch_rss, query, lang)),
                        "ddg": asyncio.create_task(
                            asyncio.to_thread(_ddg_search, query, max_results)),
                        "lite": asyncio.create_task(
                            asyncio.to_thread(_ddg_lite_search, query, max_results)),
                    }
                    # RSS always wins, but it can be slow; fallbacks are only
                    # used when RSS yields nothing by the deadline.
                    got: Dict[str, List[Dict[str, str]]] = {}
                    try:
                        while tasks:
                            if got.get("rss"):
                                return got["rss"]
                            remaining = deadline - loop.time()
                            if remaining <= 0:
                                break
                            done, _ = await asyncio.wait(
                                tasks.values(), timeout=remaining,
                                return_when=asyncio.FIRST_COMPLETED)
                            for task in done:
                                name = next(n for n, t in tasks.items() if t is task)
                                del tasks[name]
                                try:
                                    value = task.result()
                                except Exception:
                                    value = None
                                if name == "rss":
                                    parsed = _parse_feed(value, max_results) if value else []
                                    if parsed:
                                        got["rss"] = parsed
                                elif isinstance(value, list) and value:
                                    got[name] = value
                            # Keep waiting for RSS when a fallback beat it.
                            if got.get("rss"):
                                return got["rss"]
                        for name in ("rss", "ddg", "lite"):
                            if name in got:
                                return got[name]
                        return []
                    finally:
                        # Never leak background tasks when we bail early.
                        for task in tasks.values():
                            task.cancel()

                try:
                    results = await asyncio.wait_for(
                        _collect(), timeout=_SEARCH_TIMEOUT_SECONDS + 2)
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
        The same gate is re-applied on every redirect hop inside the fetch (see
        `_ValidatingRedirectHandler`), so a redirect cannot bypass it.
        """
        return _url_target_error(getattr(input_data, "url", ""))

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
        except _RedirectTargetRejected as e:
            # A redirect hop failed the loopback gate: fail exactly like an
            # initially-rejected URL (same code/message from the shared gate).
            return ToolOutput(
                success=False,
                error=e.error,
                duration_ms=0
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
