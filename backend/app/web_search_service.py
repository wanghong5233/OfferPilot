from __future__ import annotations

import json
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass


@dataclass
class SearchResult:
    title: str
    url: str
    snippet: str


def _provider() -> str:
    return os.getenv("WEB_SEARCH_PROVIDER", "auto").strip().lower()


def _timeout_sec() -> float:
    raw = os.getenv("WEB_SEARCH_TIMEOUT_SEC", "10").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 10.0
    return max(3.0, min(value, 30.0))


def _default_max_results() -> int:
    raw = os.getenv("WEB_SEARCH_MAX_RESULTS", "6").strip()
    try:
        value = int(raw)
    except ValueError:
        value = 6
    return max(1, min(value, 12))


def _clean_html(text: str) -> str:
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def _normalize_ddg_href(raw_href: str) -> str:
    href = raw_href.strip()
    if href.startswith("//duckduckgo.com/l/?") or href.startswith("https://duckduckgo.com/l/?"):
        parsed = urllib.parse.urlparse(href if href.startswith("http") else f"https:{href}")
        params = urllib.parse.parse_qs(parsed.query)
        target = params.get("uddg", [""])[0]
        if target:
            return urllib.parse.unquote(target)
    return href


def _search_duckduckgo(query: str, max_results: int) -> list[SearchResult]:
    url = "https://duckduckgo.com/html/?" + urllib.parse.urlencode({"q": query})
    req = urllib.request.Request(
        url,
        headers={
            "User-Agent": (
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
                "AppleWebKit/537.36 (KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            )
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=_timeout_sec()) as resp:
        html = resp.read().decode("utf-8", errors="ignore")

    link_matches = re.findall(
        r'<a[^>]*class="result__a"[^>]*href="([^"]+)"[^>]*>(.*?)</a>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippet_matches = re.findall(
        r'<a[^>]*class="result__snippet"[^>]*>(.*?)</a>|<div[^>]*class="result__snippet"[^>]*>(.*?)</div>',
        html,
        flags=re.IGNORECASE | re.DOTALL,
    )
    snippets: list[str] = []
    for a, b in snippet_matches:
        content = a or b
        snippets.append(_clean_html(content))

    seen_urls: set[str] = set()
    results: list[SearchResult] = []
    for idx, (href, title_html) in enumerate(link_matches):
        url_value = _normalize_ddg_href(_clean_html(href))
        if not url_value or url_value in seen_urls:
            continue
        seen_urls.add(url_value)
        title = _clean_html(title_html) or "Untitled"
        snippet = snippets[idx] if idx < len(snippets) else ""
        results.append(SearchResult(title=title, url=url_value, snippet=snippet))
        if len(results) >= max_results:
            break
    return results


def _search_tavily(query: str, max_results: int) -> list[SearchResult]:
    api_key = os.getenv("TAVILY_API_KEY", "").strip()
    if not api_key:
        return []
    payload = {
        "api_key": api_key,
        "query": query,
        "max_results": max_results,
        "search_depth": "basic",
        "include_answer": False,
    }
    req = urllib.request.Request(
        "https://api.tavily.com/search",
        data=json.dumps(payload, ensure_ascii=False).encode("utf-8"),
        headers={"Content-Type": "application/json; charset=utf-8"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=_timeout_sec()) as resp:
        body = json.loads(resp.read().decode("utf-8", errors="ignore"))
    raw_results = body.get("results")
    if not isinstance(raw_results, list):
        return []

    results: list[SearchResult] = []
    for item in raw_results:
        if not isinstance(item, dict):
            continue
        title = str(item.get("title") or "").strip() or "Untitled"
        url_value = str(item.get("url") or "").strip()
        if not url_value:
            continue
        snippet = str(item.get("content") or "").strip()
        results.append(SearchResult(title=title, url=url_value, snippet=snippet))
    return results[:max_results]


def search_web(query: str, max_results: int | None = None) -> list[SearchResult]:
    max_items = max_results if max_results is not None else _default_max_results()
    safe_max = max(1, min(int(max_items), 12))
    provider = _provider()
    errors: list[str] = []

    candidates: list[str]
    if provider == "auto":
        candidates = ["tavily", "duckduckgo"]
    else:
        candidates = [provider]

    for item in candidates:
        try:
            if item == "tavily":
                results = _search_tavily(query, safe_max)
            elif item == "duckduckgo":
                results = _search_duckduckgo(query, safe_max)
            else:
                errors.append(f"unsupported provider={item}")
                continue
            if results:
                return results
            errors.append(f"{item}: empty result")
        except urllib.error.URLError as exc:
            errors.append(f"{item}: url error {exc.reason}")
        except Exception as exc:
            errors.append(f"{item}: {exc}")
    raise RuntimeError("web search failed: " + " | ".join(errors))
