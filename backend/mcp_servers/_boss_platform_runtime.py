from __future__ import annotations

import hashlib
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from threading import Lock
from typing import Any

from pulse.core.tools.web_search import search_web

_SEED_JOBS: tuple[tuple[str, str, str], ...] = (
    ("AI Agent Intern", "Pulse Labs", "200-300/天"),
    ("LLM Application Engineer (Intern)", "NovaMind", "180-280/天"),
    ("AI 产品实习生", "DeepBridge", "150-220/天"),
    ("RAG Engineer (Intern)", "VectorWorks", "220-320/天"),
    ("Backend Engineer (Python)", "Orbit AI", "160-240/天"),
    ("MCP Tooling Intern", "Signal Stack", "200-260/天"),
)

_LOGIN_MARKERS = ("/web/user/", "/login", "passport.zhipin.com")
_DEFAULT_BROWSER_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)
_KILL_ZHIPIN_FRAME_JS = """
(function() {
    if (typeof document === 'undefined') return;
    var observer = new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var nodes = mutations[i].addedNodes || [];
            for (var j = 0; j < nodes.length; j++) {
                var node = nodes[j];
                if (!node || !node.tagName) continue;
                if (node.tagName === 'IFRAME' && (node.name === 'zhipinFrame' || node.id === 'zhipinFrame')) {
                    node.remove();
                }
            }
        }
    });
    var start = function() {
        if (document.documentElement) {
            observer.observe(document.documentElement, {childList: true, subtree: true});
        }
    };
    if (document.readyState === 'loading') {
        document.addEventListener('DOMContentLoaded', start);
    } else {
        start();
    }
})();
"""
_BROWSER_LOCK = Lock()
_PLAYWRIGHT_MANAGER = None
_PLAYWRIGHT = None
_CONTEXT = None
_PAGE = None


def _safe_int(raw: Any, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(raw)
    except Exception:
        value = default
    return max(min_value, min(value, max_value))


def _safe_bool(raw: Any, *, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _resolve_path(raw_path: str | None, *, default_path: Path) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return default_path
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _guess_title(raw_title: str, *, keyword: str) -> str:
    title = re.sub(r"\s+", " ", str(raw_title or "").strip())
    if not title:
        return f"{keyword} 招聘信息"
    for sep in (" - ", " | ", " _ ", "｜", "|", "-", "_"):
        if sep in title:
            candidate = title.split(sep, 1)[0].strip()
            if len(candidate) >= 4:
                return candidate[:120]
    return title[:120]


def _guess_company(title: str, url: str) -> str:
    for sep in (" - ", " | ", " _ ", "｜", "|", "-", "_"):
        if sep in title:
            parts = [item.strip() for item in title.split(sep) if item.strip()]
            if len(parts) >= 2:
                return parts[1][:80]
    if "://" in url:
        host = url.split("://", 1)[1].split("/", 1)[0].strip()
        if host:
            return host[:80]
    return "Unknown"


def _clean_html(text: str) -> str:
    text = re.sub(r"(?is)<script.*?>.*?</script>", " ", text)
    text = re.sub(r"(?is)<style.*?>.*?</style>", " ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = text.replace("&nbsp;", " ").replace("&amp;", "&").replace("&quot;", '"')
    return re.sub(r"\s+", " ", text).strip()


def _csv_list(raw: str) -> list[str]:
    return [item.strip() for item in str(raw or "").split(",") if item.strip()]


def _browser_profile_dir() -> Path:
    explicit = str(os.getenv("PULSE_BOSS_BROWSER_PROFILE_DIR", "") or "").strip()
    if not explicit:
        explicit = str(os.getenv("BOSS_BROWSER_PROFILE_DIR", "") or "").strip()
    return _resolve_path(explicit, default_path=Path.home() / ".pulse" / "boss_browser_profile")


def _browser_headless() -> bool:
    raw = str(os.getenv("PULSE_BOSS_BROWSER_HEADLESS", "") or "").strip()
    if raw:
        return _safe_bool(raw, default=False)
    return _safe_bool(os.getenv("BOSS_HEADLESS", "false"), default=False)


def _browser_timeout_ms() -> int:
    return _safe_int(
        os.getenv("PULSE_BOSS_BROWSER_TIMEOUT_MS", "20000"),
        20000,
        min_value=3000,
        max_value=90000,
    )


def _browser_screenshot_dir() -> Path | None:
    configured = str(os.getenv("PULSE_BOSS_MCP_SCREENSHOT_DIR", "") or "").strip()
    if not configured:
        configured = str(os.getenv("BOSS_SCREENSHOT_DIR", "") or "").strip()
    if not configured:
        return None
    return _resolve_path(configured, default_path=_repo_root() / "backend" / "exports" / "screenshots")


def _browser_channel() -> str:
    return str(os.getenv("PULSE_BOSS_BROWSER_CHANNEL", "") or "").strip()


def _browser_user_agent() -> str:
    value = str(os.getenv("PULSE_BOSS_BROWSER_USER_AGENT", "") or "").strip()
    return value or _DEFAULT_BROWSER_UA


def _browser_stealth_enabled() -> bool:
    return _safe_bool(os.getenv("PULSE_BOSS_BROWSER_STEALTH_ENABLED", "true"), default=True)


def _browser_block_iframe_core() -> bool:
    return _safe_bool(os.getenv("PULSE_BOSS_BROWSER_BLOCK_IFRAME_CORE", "true"), default=True)


def _is_login_page(url: str) -> bool:
    value = str(url or "").strip().lower()
    return any(marker in value for marker in _LOGIN_MARKERS)


def _is_security_page(url: str) -> bool:
    value = str(url or "").strip().lower()
    return any(
        marker in value
        for marker in (
            "/web/passport/zp/security",
            "passport/zp/security.html",
            "_security_check=",
            "code=37",
        )
    )


def _default_greet_button_selectors() -> list[str]:
    return [
        "button:has-text('立即沟通')",
        "button:has-text('立即沟通') span",
        "button:has-text('发起沟通')",
        "button:has-text('立即开聊')",
        "a:has-text('立即沟通')",
    ]


def _default_chat_input_selectors() -> list[str]:
    return [
        "textarea",
        "[contenteditable='true']",
        ".chat-input",
        ".input-area",
    ]


def _default_chat_send_selectors() -> list[str]:
    return [
        "button:has-text('发送')",
        ".send-message",
        ".send-btn",
    ]


def _default_chat_item_selectors(conversation_id: str) -> list[str]:
    safe = str(conversation_id or "").strip()
    if not safe:
        return []
    return [
        f"[data-conversation-id='{safe}']",
        f"[data-id='{safe}']",
        f"li[data-id='{safe}']",
    ]


def _scan_mode() -> str:
    value = str(os.getenv("PULSE_BOSS_MCP_SCAN_MODE", "browser_only") or "").strip().lower()
    if value in {"browser_only", "browser_first", "web_search_only"}:
        return value
    return "browser_only"


def _pull_mode() -> str:
    value = str(os.getenv("PULSE_BOSS_MCP_PULL_MODE", "browser_only") or "").strip().lower()
    if value in {"browser_only", "browser_first", "local_only"}:
        return value
    return "browser_only"


def _allow_seed_fallback() -> bool:
    return _safe_bool(os.getenv("PULSE_BOSS_ALLOW_SEED_FALLBACK", "false"), default=False)


def _search_url_template() -> str:
    return str(
        os.getenv("PULSE_BOSS_SEARCH_URL_TEMPLATE", "https://www.zhipin.com/web/geek/jobs?query={keyword}") or ""
    ).strip()


def _build_search_url(*, keyword: str, page: int) -> str:
    template = _search_url_template()
    encoded_keyword = urllib.parse.quote_plus(str(keyword or "").strip())
    safe_page = max(1, int(page))
    if "{keyword}" in template:
        template = template.replace("{keyword}", encoded_keyword)
    if "{page}" in template:
        template = template.replace("{page}", str(safe_page))
    return template


def _chat_list_url() -> str:
    return str(os.getenv("PULSE_BOSS_CHAT_LIST_URL", "https://www.zhipin.com/web/geek/chat") or "").strip()


def _default_job_card_selectors() -> list[str]:
    return [
        ".job-list-box li",
        ".job-list li",
        ".job-card-wrapper",
        ".job-card-box",
        ".search-job-result .job-card",
    ]


def _default_job_next_page_selectors() -> list[str]:
    return [
        "a[ka='page-next']",
        "a:has-text('下一页')",
        ".options-pages a.next",
        ".page a.next",
    ]


def _default_job_nav_selectors() -> list[str]:
    return [
        "a:has-text('职位')",
        "button:has-text('职位')",
        "text=职位",
        "a[href*='/web/geek/jobs']",
        "a[href*='/web/geek/job']",
    ]


def _default_job_search_input_selectors() -> list[str]:
    return [
        "input[placeholder*='搜索职位']",
        "input[placeholder*='关键词']",
        "input[placeholder*='搜索']",
        "input[type='search']",
        "input[type='text']",
    ]


def _default_chat_row_selectors() -> list[str]:
    return [
        ".friend-list li",
        ".chat-list li",
        ".message-list li",
        "[data-conversation-id]",
    ]


def _browser_executor_retry_count() -> int:
    return _safe_int(
        os.getenv("PULSE_BOSS_BROWSER_EXECUTOR_RETRY_COUNT", "1"),
        1,
        min_value=0,
        max_value=4,
    )


def _browser_executor_retry_backoff_ms() -> int:
    return _safe_int(
        os.getenv("PULSE_BOSS_BROWSER_EXECUTOR_RETRY_BACKOFF_MS", "700"),
        700,
        min_value=100,
        max_value=8000,
    )


def _risk_keywords() -> list[str]:
    configured = _csv_list(os.getenv("PULSE_BOSS_RISK_KEYWORDS", ""))
    if configured:
        return [item.lower() for item in configured if item]
    return [
        "验证码",
        "人机验证",
        "访问受限",
        "异常访问",
        "风险提示",
        "请完成验证",
        "captcha",
        "access denied",
    ]


def _contains_risk_keywords(text: str) -> bool:
    lowered = str(text or "").lower()
    return any(keyword in lowered for keyword in _risk_keywords())


def _read_page_text(url: str, *, max_chars: int = 2500) -> str:
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
    with urllib.request.urlopen(req, timeout=12) as response:
        raw = response.read().decode("utf-8", errors="ignore")
    return _clean_html(raw)[: max(500, min(max_chars, 8000))]


def _ensure_browser_page():
    global _PLAYWRIGHT_MANAGER, _PLAYWRIGHT, _CONTEXT, _PAGE
    with _BROWSER_LOCK:
        if _PAGE is not None:
            try:
                if not _PAGE.is_closed():
                    return _PAGE
            except Exception:
                pass

        try:
            from playwright.sync_api import sync_playwright
        except Exception as exc:
            raise RuntimeError(
                "playwright is not available; install `playwright` and run `playwright install chromium`"
            ) from exc

        profile_dir = _browser_profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        if _PLAYWRIGHT_MANAGER is None or _PLAYWRIGHT is None:
            _PLAYWRIGHT_MANAGER = sync_playwright()
            _PLAYWRIGHT = _PLAYWRIGHT_MANAGER.start()
        if _PLAYWRIGHT is None:
            raise RuntimeError("playwright runtime is empty after start()")

        launch_args: dict[str, Any] = {
            "user_data_dir": str(profile_dir),
            "headless": _browser_headless(),
            "no_viewport": True,
            "user_agent": _browser_user_agent(),
            "ignore_default_args": ["--enable-automation"],
            "args": ["--disable-blink-features=AutomationControlled"],
        }
        channel = _browser_channel()
        if channel:
            launch_args["channel"] = channel
        try:
            _CONTEXT = _PLAYWRIGHT.chromium.launch_persistent_context(**launch_args)
        except TypeError:
            launch_args.pop("no_viewport", None)
            _CONTEXT = _PLAYWRIGHT.chromium.launch_persistent_context(**launch_args)
        try:
            _CONTEXT.add_init_script(_KILL_ZHIPIN_FRAME_JS)
        except Exception:
            pass
        if _browser_block_iframe_core():
            try:
                _CONTEXT.route(
                    "**/iframe-core*",
                    lambda route: route.fulfill(body="", content_type="application/javascript"),
                )
            except Exception:
                pass
        if _browser_stealth_enabled():
            try:
                from playwright_stealth import Stealth

                stealth = Stealth(
                    chrome_runtime=True,
                    navigator_languages_override=("zh-CN", "zh", "en-US", "en"),
                    navigator_platform_override="Linux x86_64",
                    navigator_vendor_override="Google Inc.",
                    navigator_user_agent_override=_browser_user_agent(),
                )
                stealth.apply_stealth_sync(_CONTEXT)
            except Exception:
                pass
        _PAGE = _CONTEXT.pages[0] if _CONTEXT.pages else _CONTEXT.new_page()
        _PAGE.set_default_timeout(_browser_timeout_ms())
        return _PAGE


def _wait_for_any_selector(page: Any, selectors: list[str], *, timeout_ms: int) -> tuple[Any, str]:
    for selector in selectors:
        try:
            page.wait_for_selector(selector, timeout=timeout_ms)
            return page.locator(selector).first, selector
        except Exception:
            continue
    return None, ""


def _fill_text(locator: Any, text: str) -> None:
    try:
        locator.fill(text, timeout=min(_browser_timeout_ms(), 10000))
        return
    except Exception:
        pass
    locator.click(timeout=min(_browser_timeout_ms(), 10000))
    locator.type(text, delay=20, timeout=min(_browser_timeout_ms(), 10000))


def _take_browser_screenshot(page: Any, *, prefix: str) -> str | None:
    directory = _browser_screenshot_dir()
    if directory is None:
        return None
    directory.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    safe_prefix = "".join(ch if ch.isalnum() or ch in {"_", "-"} else "_" for ch in prefix).strip("_") or "boss"
    target = directory / f"{stamp}_{safe_prefix}.png"
    try:
        page.screenshot(path=str(target), full_page=True)
    except Exception:
        return None
    return str(target)


def _build_chat_url(conversation_id: str) -> str:
    template = str(os.getenv("PULSE_BOSS_CHAT_URL_TEMPLATE", "") or "").strip()
    if not template:
        template = "https://www.zhipin.com/web/geek/chat?conversationId={conversation_id}"
    safe_id = str(conversation_id or "").strip()
    if "{conversation_id}" in template:
        return template.replace("{conversation_id}", safe_id)
    if safe_id and "conversationId=" not in template and "?" in template:
        return f"{template}&conversationId={safe_id}"
    if safe_id and "conversationId=" not in template:
        return f"{template}?conversationId={safe_id}"
    return template


def _build_search_url_candidates(*, keyword: str, page: int) -> list[str]:
    primary = _build_search_url(keyword=keyword, page=page)
    candidates: list[str] = [primary]
    if "/web/geek/job?" in primary:
        candidates.append(primary.replace("/web/geek/job?", "/web/geek/jobs?"))
    if "/web/geek/jobs?" in primary:
        candidates.append(primary.replace("/web/geek/jobs?", "/web/geek/job?"))
    encoded_keyword = urllib.parse.quote_plus(str(keyword or "").strip())
    if encoded_keyword:
        candidates.append(f"https://www.zhipin.com/web/geek/jobs?query={encoded_keyword}")
        candidates.append(f"https://www.zhipin.com/web/geek/job?query={encoded_keyword}")
    deduped: list[str] = []
    seen: set[str] = set()
    for url in candidates:
        safe = str(url or "").strip()
        if not safe or safe in seen:
            continue
        seen.add(safe)
        deduped.append(safe)
    return deduped


def _extract_jobs_with_retries(
    page: Any,
    *,
    keyword: str,
    max_items: int,
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    attempts = max(1, _safe_int(os.getenv("PULSE_BOSS_SCAN_EXTRACT_ATTEMPTS", "4"), 4, min_value=1, max_value=8))
    rows: list[dict[str, Any]] = []
    for attempt in range(attempts):
        rows = _extract_jobs_from_page(page, keyword=keyword, max_items=max_items, seen_keys=seen_keys)
        if rows:
            return rows
        try:
            page.mouse.wheel(0, 1200)
        except Exception:
            pass
        try:
            page.wait_for_load_state("networkidle", timeout=min(_browser_timeout_ms(), 3000))
        except Exception:
            pass
        page.wait_for_timeout(900 + min(900, attempt * 250))
    return rows


def _navigate_jobs_from_chat(page: Any, *, keyword: str) -> tuple[bool, str]:
    try:
        page.goto(_chat_list_url(), wait_until="domcontentloaded", timeout=_browser_timeout_ms())
    except Exception:
        return False, ""
    current_url = str(page.url or "")
    risk_status = _detect_runtime_risk(page, current_url=current_url)
    if risk_status:
        return False, current_url

    nav_selectors = _csv_list(os.getenv("PULSE_BOSS_JOB_NAV_SELECTORS", ""))
    if not nav_selectors:
        nav_selectors = _default_job_nav_selectors()
    nav_loc, _nav_selector = _wait_for_any_selector(page, nav_selectors, timeout_ms=min(_browser_timeout_ms(), 4500))
    if nav_loc is not None:
        try:
            nav_loc.click(timeout=min(_browser_timeout_ms(), 6000))
            try:
                page.wait_for_load_state("domcontentloaded", timeout=min(_browser_timeout_ms(), 6000))
            except Exception:
                pass
            page.wait_for_timeout(900)
        except Exception:
            pass

    safe_keyword = str(keyword or "").strip()
    if safe_keyword:
        input_selectors = _csv_list(os.getenv("PULSE_BOSS_JOB_SEARCH_INPUT_SELECTORS", ""))
        if not input_selectors:
            input_selectors = _default_job_search_input_selectors()
        input_loc, _input_selector = _wait_for_any_selector(
            page,
            input_selectors,
            timeout_ms=min(_browser_timeout_ms(), 4500),
        )
        if input_loc is not None:
            try:
                _fill_text(input_loc, safe_keyword)
                input_loc.press("Enter", timeout=min(_browser_timeout_ms(), 5000))
                try:
                    page.wait_for_load_state("domcontentloaded", timeout=min(_browser_timeout_ms(), 8000))
                except Exception:
                    pass
                page.wait_for_timeout(1200)
            except Exception:
                pass

    return True, str(page.url or "")


def _extract_jobs_from_page(
    page: Any,
    *,
    keyword: str,
    max_items: int,
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    selectors = _csv_list(os.getenv("PULSE_BOSS_JOB_CARD_SELECTORS", ""))
    if not selectors:
        selectors = _default_job_card_selectors()
    raw_rows: list[dict[str, Any]] = []
    for selector in selectors:
        try:
            rows = page.eval_on_selector_all(
                selector,
                """nodes => nodes.map(node => {
                    const text = (node.innerText || "").replace(/\\s+/g, " ").trim();
                    const titleEl = node.querySelector(".job-name,.job-title,.job-card-left .title,.job-info .name,[class*='title']");
                    const companyEl = node.querySelector(".company-name,.company-text,.company-info .name,[class*='company']");
                    const salaryEl = node.querySelector(".salary,[class*='salary']");
                    const linkEl = node.querySelector("a[href*='/job_detail'],a[href*='/web/geek/job'],a[href]");
                    const href = linkEl ? linkEl.href : "";
                    return {
                        title: titleEl ? (titleEl.innerText || "").trim() : "",
                        company: companyEl ? (companyEl.innerText || "").trim() : "",
                        salary: salaryEl ? (salaryEl.innerText || "").trim() : "",
                        source_url: href || "",
                        snippet: text.slice(0, 1000),
                    };
                })""",
            )
        except Exception:
            rows = []
        if isinstance(rows, list) and rows:
            for row in rows:
                if isinstance(row, dict):
                    raw_rows.append(dict(row))
            break

    if not raw_rows:
        try:
            anchor_rows = page.eval_on_selector_all(
                "a[href*='job_detail'],a[href*='/web/geek/job']",
                """nodes => nodes.map(node => {
                    const href = node.href || "";
                    const ownText = (node.innerText || "").replace(/\\s+/g, " ").trim();
                    const parent = node.closest("li,div,article,section") || node.parentElement || node;
                    const parentText = parent && parent.innerText ? parent.innerText.replace(/\\s+/g, " ").trim() : ownText;
                    return {
                        title: ownText || parentText.slice(0, 60),
                        company: "",
                        salary: "",
                        source_url: href,
                        snippet: parentText.slice(0, 1000),
                    };
                })""",
            )
        except Exception:
            anchor_rows = []
        if isinstance(anchor_rows, list):
            for row in anchor_rows:
                if isinstance(row, dict):
                    raw_rows.append(dict(row))

    result: list[dict[str, Any]] = []
    for row in raw_rows:
        source_url = str(row.get("source_url") or "").strip()
        title_raw = str(row.get("title") or "").strip()
        snippet = str(row.get("snippet") or "").strip()
        dedupe_key = (source_url or title_raw or snippet).lower()
        if not dedupe_key or dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        title = _guess_title(title_raw, keyword=keyword)
        company = str(row.get("company") or "").strip() or _guess_company(title_raw, source_url)
        if not source_url:
            source_url = f"https://www.zhipin.com/job_detail/{hashlib.sha1(dedupe_key.encode('utf-8')).hexdigest()[:16]}"
        result.append(
            {
                "job_id": hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16],
                "title": title,
                "company": company,
                "salary": str(row.get("salary") or "").strip() or None,
                "source_url": source_url,
                "snippet": snippet[:1000],
                "source": "boss_mcp_browser_scan",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(result) >= max(1, max_items):
            break
    return result


def _goto_next_search_page(page: Any) -> bool:
    selectors = _csv_list(os.getenv("PULSE_BOSS_SEARCH_NEXT_SELECTORS", ""))
    if not selectors:
        selectors = _default_job_next_page_selectors()
    next_loc, _selector = _wait_for_any_selector(page, selectors, timeout_ms=min(4000, _browser_timeout_ms()))
    if next_loc is None:
        return False
    try:
        next_loc.click(timeout=min(_browser_timeout_ms(), 8000))
        try:
            page.wait_for_load_state("domcontentloaded", timeout=min(_browser_timeout_ms(), 6000))
        except Exception:
            pass
        page.wait_for_timeout(900)
        return True
    except Exception:
        return False


def _scan_jobs_via_browser(*, keyword: str, max_items: int, max_pages: int) -> dict[str, Any]:
    safe_keyword = str(keyword or "").strip() or "AI Agent 实习"
    safe_items = _safe_int(max_items, 10, min_value=1, max_value=80)
    safe_pages = _safe_int(max_pages, 2, min_value=1, max_value=8)
    try:
        page = _ensure_browser_page()
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_unavailable",
            "items": [],
            "pages_scanned": 0,
            "source": "boss_mcp_browser_scan",
            "errors": [str(exc)[:300]],
        }

    use_page_template = "{page}" in _search_url_template()
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = []
    pages_scanned = 0

    for index in range(1, safe_pages + 1):
        extracted: list[dict[str, Any]] = []
        page_ready = False

        if index == 1 or use_page_template:
            candidates = _build_search_url_candidates(keyword=safe_keyword, page=index)
            for target_url in candidates:
                try:
                    page.goto(target_url, wait_until="domcontentloaded", timeout=_browser_timeout_ms())
                    page_ready = True
                except Exception as exc:
                    errors.append(f"page navigation failed: {str(exc)[:220]}")
                    continue
                current_url = str(page.url or "")
                risk_status = _detect_runtime_risk(page, current_url=current_url)
                if risk_status:
                    errors.append(f"risk status={risk_status}; url={current_url[:160]}")
                    continue
                extracted = _extract_jobs_with_retries(
                    page,
                    keyword=safe_keyword,
                    max_items=max(1, safe_items - len(rows)),
                    seen_keys=seen,
                )
                if extracted:
                    break
        else:
            moved = _goto_next_search_page(page)
            if moved:
                page_ready = True
                current_url = str(page.url or "")
                risk_status = _detect_runtime_risk(page, current_url=current_url)
                if risk_status:
                    errors.append(f"risk status={risk_status}; url={current_url[:160]}")
                else:
                    extracted = _extract_jobs_with_retries(
                        page,
                        keyword=safe_keyword,
                        max_items=max(1, safe_items - len(rows)),
                        seen_keys=seen,
                    )

        if not extracted and index == 1:
            nav_ok, nav_url = _navigate_jobs_from_chat(page, keyword=safe_keyword)
            if nav_ok:
                page_ready = True
                risk_status = _detect_runtime_risk(page, current_url=nav_url)
                if not risk_status:
                    extracted = _extract_jobs_with_retries(
                        page,
                        keyword=safe_keyword,
                        max_items=max(1, safe_items - len(rows)),
                        seen_keys=seen,
                    )
                else:
                    errors.append(f"risk status={risk_status}; url={nav_url[:160]}")

        if not extracted and index == 1:
            # 真实平台兜底：从聊天页提取岗位沟通线索，不回退到 mock。
            try:
                page.goto(_chat_list_url(), wait_until="domcontentloaded", timeout=_browser_timeout_ms())
                page_ready = True
                current_url = str(page.url or "")
                risk_status = _detect_runtime_risk(page, current_url=current_url)
                if not risk_status:
                    extracted = _extract_job_leads_from_chat_page(
                        page,
                        keyword=safe_keyword,
                        max_items=max(1, safe_items - len(rows)),
                        seen_keys=seen,
                    )
            except Exception as exc:
                errors.append(f"chat lead fallback failed: {str(exc)[:220]}")

        if not page_ready:
            break

        pages_scanned = index
        rows.extend(extracted)
        if len(rows) >= safe_items:
            break

    if rows:
        return {
            "ok": True,
            "status": "ready",
            "items": rows[:safe_items],
            "pages_scanned": max(1, pages_scanned),
            "source": "boss_mcp_browser_scan",
            "errors": errors,
        }
    return {
        "ok": False,
        "status": "no_result",
        "items": [],
        "pages_scanned": max(1, pages_scanned),
        "source": "boss_mcp_browser_scan",
        "errors": errors or ["browser scan returned no jobs"],
    }


def _switch_chat_tab(page: Any, *, chat_tab: str) -> str:
    safe_tab = str(chat_tab or "").strip()
    if not safe_tab:
        return ""
    safe_tab_selector = safe_tab.replace("\\", "\\\\").replace("'", "\\'")
    selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_TAB_SELECTORS", ""))
    if not selectors:
        selectors = [
            f"text={safe_tab}",
            f"button:has-text('{safe_tab_selector}')",
            f"a:has-text('{safe_tab_selector}')",
            f"li:has-text('{safe_tab_selector}')",
        ]
    loc, selector = _wait_for_any_selector(page, selectors, timeout_ms=min(3500, _browser_timeout_ms()))
    if loc is None:
        return ""
    try:
        loc.click(timeout=min(_browser_timeout_ms(), 6000))
        page.wait_for_timeout(700)
        return selector
    except Exception:
        return ""


def _looks_like_chat_time_token(value: str) -> bool:
    text = str(value or "").strip()
    if not text:
        return False
    if text in {"刚刚", "昨天", "前天"}:
        return True
    if re.match(r"^\d{1,2}:\d{2}$", text):
        return True
    if re.match(r"^\d{1,2}月\d{1,2}日$", text):
        return True
    if re.match(r"^\d{4}-\d{1,2}-\d{1,2}$", text):
        return True
    return False


def _parse_conversation_header(header: str) -> tuple[str, str, str]:
    text = re.sub(r"\s+", " ", str(header or "").strip())
    if not text:
        return "Unknown HR", "Unknown", "Unknown Job"

    hr_name = text[:20]
    tail = ""
    for marker in ("先生", "女士", "老师", "经理", "总监", "主管"):
        idx = text.find(marker)
        if idx > 0:
            hr_name = text[: idx + len(marker)]
            tail = text[idx + len(marker) :].strip()
            break
    if not tail:
        if len(text) >= 5:
            hr_name = text[:3]
            tail = text[3:].strip()
        else:
            tail = ""

    company = tail[:40] if tail else "Unknown"
    job_title = "招聘沟通" if ("招聘" in text or "hr" in text.lower()) else "Unknown Job"
    return hr_name[:40], company[:80], job_title[:80]


def _extract_conversations_from_body_text(page: Any, *, max_items: int) -> list[dict[str, Any]]:
    try:
        body = str(page.inner_text("body") or "")
    except Exception:
        body = ""
    if not body.strip():
        return []

    lines = [re.sub(r"\s+", " ", line).strip() for line in body.splitlines()]
    lines = [line for line in lines if line]
    skip_tokens = {
        "首页",
        "职位",
        "公司",
        "校园",
        "APP",
        "有了",
        "海外",
        "无障碍专区",
        "在线客服",
        "消息",
        "简历",
        "全部",
        "未读",
        "新招呼",
        "更多",
        "AI筛选",
    }
    cleaned = [line for line in lines if line not in skip_tokens]

    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    index = 0
    while index < len(cleaned) and len(rows) < max(1, max_items):
        token = cleaned[index]
        if not _looks_like_chat_time_token(token):
            index += 1
            continue

        header = cleaned[index + 1] if index + 1 < len(cleaned) else ""
        message = ""
        probe = index + 2
        while probe < len(cleaned):
            candidate = cleaned[probe]
            if _looks_like_chat_time_token(candidate):
                break
            if candidate in {"[送达]", "[已读]", "[未读]"}:
                probe += 1
                continue
            message = candidate
            break

        if header and message:
            hr_name, company, job_title = _parse_conversation_header(header)
            conversation_id = hashlib.sha1(f"{token}-{header}-{message}".encode("utf-8")).hexdigest()[:16]
            if conversation_id not in seen:
                seen.add(conversation_id)
                rows.append(
                    {
                        "conversation_id": conversation_id,
                        "hr_name": hr_name,
                        "company": company,
                        "job_title": job_title,
                        "latest_message": message[:2000],
                        "latest_time": token[:40],
                        "unread_count": 0,
                        "source": "boss_mcp_browser_chat",
                    }
                )
        index = max(index + 1, probe if probe > index else index + 1)

    return rows[: max(1, max_items)]


def _extract_job_leads_from_chat_page(
    page: Any,
    *,
    keyword: str,
    max_items: int,
    seen_keys: set[str],
) -> list[dict[str, Any]]:
    conversations = _extract_conversations_from_body_text(page, max_items=max(1, max_items * 2))
    if not conversations:
        return []
    rows: list[dict[str, Any]] = []
    for conv in conversations:
        conversation_id = str(conv.get("conversation_id") or "").strip()
        company = str(conv.get("company") or "").strip() or "Unknown"
        job_title = str(conv.get("job_title") or "").strip()
        if not job_title or job_title.lower().startswith("unknown"):
            job_title = f"{keyword} 相关岗位"
        latest_message = str(conv.get("latest_message") or "").strip()
        source_url = _build_chat_url(conversation_id) if conversation_id else _chat_list_url()
        dedupe_key = f"{company}-{job_title}-{source_url}".lower()
        if dedupe_key in seen_keys:
            continue
        seen_keys.add(dedupe_key)
        rows.append(
            {
                "job_id": hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16],
                "title": job_title[:120],
                "company": company[:80],
                "salary": None,
                "source_url": source_url,
                "snippet": latest_message[:1000],
                "source": "boss_mcp_browser_chat_lead",
                "collected_at": datetime.now(timezone.utc).isoformat(),
            }
        )
        if len(rows) >= max(1, max_items):
            break
    return rows


def _extract_conversations_from_page(page: Any, *, max_items: int) -> list[dict[str, Any]]:
    selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_ROW_SELECTORS", ""))
    if not selectors:
        selectors = _default_chat_row_selectors()
    raw_rows: list[dict[str, Any]] = []
    for selector in selectors:
        try:
            rows = page.eval_on_selector_all(
                selector,
                """nodes => nodes.map(node => {
                    const text = (node.innerText || "").replace(/\\s+/g, " ").trim();
                    const hrEl = node.querySelector(".name,.title,.boss-name,.friend-name,[class*='name']");
                    const companyEl = node.querySelector(".company,.company-name,.sub-title,[class*='company']");
                    const jobEl = node.querySelector(".job,.position,.job-name,[class*='job']");
                    const msgEl = node.querySelector(".last-msg,.msg,.message,.text,.content,[class*='msg']");
                    const timeEl = node.querySelector(".time,.last-time,[class*='time']");
                    const unreadEl = node.querySelector(".unread,.badge,.dot,.num,.count,[class*='unread']");
                    const linkEl = node.querySelector("a[href]");
                    const href = linkEl ? (linkEl.getAttribute("href") || "") : "";
                    const attrs = {
                        cid1: node.getAttribute("data-conversation-id") || "",
                        cid2: node.getAttribute("data-id") || "",
                        cid3: node.id || "",
                    };
                    const unreadRaw = unreadEl ? (unreadEl.innerText || unreadEl.textContent || "").trim() : "";
                    const m = unreadRaw.match(/\\d+/);
                    let unread = 0;
                    if (m) unread = parseInt(m[0], 10);
                    else if (unreadRaw) unread = 1;
                    return {
                        conversation_id: attrs.cid1 || attrs.cid2 || attrs.cid3 || "",
                        href: href,
                        hr_name: hrEl ? (hrEl.innerText || "").trim() : "",
                        company: companyEl ? (companyEl.innerText || "").trim() : "",
                        job_title: jobEl ? (jobEl.innerText || "").trim() : "",
                        latest_message: msgEl ? (msgEl.innerText || "").trim() : text,
                        latest_time: timeEl ? (timeEl.innerText || "").trim() : "",
                        unread_count: unread,
                    };
                })""",
            )
        except Exception:
            rows = []
        if isinstance(rows, list) and rows:
            for row in rows:
                if isinstance(row, dict):
                    raw_rows.append(dict(row))
            break

    result: list[dict[str, Any]] = []
    seen: set[str] = set()
    for row in raw_rows:
        conversation_id = str(row.get("conversation_id") or "").strip()
        href = str(row.get("href") or "").strip()
        if not conversation_id and "conversationId=" in href:
            try:
                conversation_id = href.split("conversationId=", 1)[1].split("&", 1)[0].strip()
            except Exception:
                conversation_id = ""
        hr_name = str(row.get("hr_name") or "").strip()
        company = str(row.get("company") or "").strip()
        job_title = str(row.get("job_title") or "").strip()
        latest_message = str(row.get("latest_message") or "").strip()
        if not conversation_id:
            seed = f"{hr_name}-{company}-{job_title}-{latest_message}"
            conversation_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
        dedupe_key = conversation_id.lower()
        if dedupe_key in seen:
            continue
        seen.add(dedupe_key)
        if not latest_message:
            latest_message = f"{company} {job_title}".strip() or "message"
        if not hr_name:
            hr_name = "Unknown HR"
        if not company:
            company = "Unknown"
        if not job_title:
            job_title = "Unknown Job"
        result.append(
            {
                "conversation_id": conversation_id,
                "hr_name": hr_name[:80],
                "company": company[:120],
                "job_title": job_title[:160],
                "latest_message": latest_message[:2000],
                "latest_time": str(row.get("latest_time") or "刚刚")[:40],
                "unread_count": max(0, min(_safe_int(row.get("unread_count"), 0, min_value=0, max_value=99), 99)),
                "source": "boss_mcp_browser_chat",
            }
        )
        if len(result) >= max(1, max_items):
            break
    if not result:
        result = _extract_conversations_from_body_text(page, max_items=max_items)
    return result


def _pull_conversations_via_browser(
    *,
    max_conversations: int,
    unread_only: bool,
    fetch_latest_hr: bool,
    chat_tab: str,
) -> dict[str, Any]:
    _ = bool(fetch_latest_hr)
    safe_max = _safe_int(max_conversations, 20, min_value=1, max_value=200)
    try:
        page = _ensure_browser_page()
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_unavailable",
            "items": [],
            "unread_total": 0,
            "source": "boss_mcp_browser_chat",
            "errors": [str(exc)[:300]],
        }

    target_url = _chat_list_url()
    try:
        page.goto(target_url, wait_until="domcontentloaded", timeout=_browser_timeout_ms())
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_error",
            "items": [],
            "unread_total": 0,
            "source": "boss_mcp_browser_chat",
            "errors": [f"chat page navigation failed: {str(exc)[:250]}"],
        }

    current_url = str(page.url or "")
    risk_status = _detect_runtime_risk(page, current_url=current_url)
    if risk_status:
        return {
            "ok": False,
            "status": risk_status,
            "items": [],
            "unread_total": 0,
            "source": "boss_mcp_browser_chat",
            "errors": [f"risk status={risk_status}"],
            "url": current_url,
        }

    tab_selector = _switch_chat_tab(page, chat_tab=chat_tab)
    rows = _extract_conversations_from_page(page, max_items=max(10, safe_max * 2))
    if unread_only:
        rows = [item for item in rows if int(item.get("unread_count") or 0) > 0]
    rows = rows[:safe_max]
    unread_total = sum(int(item.get("unread_count") or 0) for item in rows)
    return {
        "ok": bool(rows),
        "status": "ready" if rows else "no_result",
        "items": rows,
        "unread_total": unread_total,
        "source": "boss_mcp_browser_chat",
        "errors": [],
        "tab_selector": tab_selector or None,
    }


def _detect_runtime_risk(page: Any, *, current_url: str) -> str:
    if _is_login_page(current_url):
        return "auth_required"
    if _is_security_page(current_url):
        return "risk_blocked"
    if _contains_risk_keywords(current_url):
        return "risk_blocked"
    try:
        body_text = str(page.inner_text("body") or "")[:2500]
    except Exception:
        body_text = ""
    if _contains_risk_keywords(body_text):
        return "risk_blocked"
    return ""


def _run_browser_executor_with_retry(operation_name: str, executor: Any) -> dict[str, Any]:
    retry_count = _browser_executor_retry_count()
    backoff_ms = _browser_executor_retry_backoff_ms()
    final_result: dict[str, Any] = {}
    for attempt in range(retry_count + 1):
        result = executor()
        safe_result = dict(result) if isinstance(result, dict) else {"ok": False, "status": "executor_error"}
        safe_result["attempt"] = attempt + 1
        safe_result["max_attempts"] = retry_count + 1
        if bool(safe_result.get("ok")):
            final_result = safe_result
            break
        status = str(safe_result.get("status") or "").strip()
        retryable = status in {"executor_error", "selector_missing"}
        if attempt >= retry_count or not retryable:
            final_result = safe_result
            break
        sleep_ms = backoff_ms * (attempt + 1)
        time.sleep(max(0.05, sleep_ms / 1000.0))
    _append_action_log(
        {
            "action": f"{operation_name}_attempt_summary",
            "status": str(final_result.get("status") or ""),
            "ok": bool(final_result.get("ok")),
            "attempt": int(final_result.get("attempt") or 0),
            "max_attempts": int(final_result.get("max_attempts") or 0),
            "source": str(final_result.get("source") or ""),
            "error": str(final_result.get("error") or "")[:300] or None,
        }
    )
    return final_result


def _try_click_conversation_by_hint(page: Any, hint: dict[str, Any]) -> tuple[bool, str]:
    candidates: list[str] = []
    for key in ("hr_name", "company", "job_title", "hint_text"):
        value = str(hint.get(key) or "").strip()
        if value and value not in candidates:
            candidates.append(value)
    for text in candidates:
        try:
            locator = page.get_by_text(text, exact=False).first
            locator.wait_for(timeout=min(_browser_timeout_ms(), 3500))
            locator.click(timeout=min(_browser_timeout_ms(), 6000))
            return True, f"text:{text}"
        except Exception:
            continue
    return False, ""


def check_login(*, check_url: str = "") -> dict[str, Any]:
    target = str(check_url or "").strip() or str(
        os.getenv("PULSE_BOSS_LOGIN_CHECK_URL", "https://www.zhipin.com/web/geek/chat")
    ).strip()
    try:
        page = _ensure_browser_page()
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_unavailable",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
            "url": target,
        }
    try:
        page.goto(target, wait_until="domcontentloaded", timeout=_browser_timeout_ms())
        current_url = str(page.url or "")
        risk_status = _detect_runtime_risk(page, current_url=current_url)
        if risk_status == "auth_required":
            return {
                "ok": False,
                "status": "auth_required",
                "source": "boss_mcp_browser",
                "error": "boss login is required",
                "url": current_url,
            }
        if risk_status == "risk_blocked":
            return {
                "ok": False,
                "status": "risk_blocked",
                "source": "boss_mcp_browser",
                "error": "risk or captcha page detected",
                "url": current_url,
            }
        return {
            "ok": True,
            "status": "ready",
            "source": "boss_mcp_browser",
            "error": None,
            "url": current_url,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_error",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
            "url": str(getattr(page, "url", "") or target),
        }


def _execute_browser_reply(
    *,
    conversation_id: str,
    reply_text: str,
    profile_id: str,
    conversation_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _ = profile_id
    safe_conversation_id = str(conversation_id or "").strip()
    safe_reply = str(reply_text or "").strip()
    if not safe_conversation_id:
        return {
            "ok": False,
            "status": "failed",
            "source": "boss_mcp_browser",
            "error": "conversation_id is required",
        }
    if not safe_reply:
        return {
            "ok": False,
            "status": "failed",
            "source": "boss_mcp_browser",
            "error": "reply_text is required",
        }

    try:
        page = _ensure_browser_page()
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_unavailable",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
        }

    url = _build_chat_url(safe_conversation_id)
    try:
        page.goto(url, wait_until="domcontentloaded", timeout=_browser_timeout_ms())
        current_url = str(page.url or "")
        risk_status = _detect_runtime_risk(page, current_url=current_url)
        if risk_status == "auth_required":
            return {
                "ok": False,
                "status": "auth_required",
                "source": "boss_mcp_browser",
                "error": "boss login is required",
                "url": current_url,
            }
        if risk_status == "risk_blocked":
            return {
                "ok": False,
                "status": "risk_blocked",
                "source": "boss_mcp_browser",
                "error": "risk or captcha page detected",
                "url": current_url,
            }

        item_selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_ITEM_SELECTORS", ""))
        if not item_selectors:
            item_selectors = _default_chat_item_selectors(safe_conversation_id)
        item_loc, item_selector = _wait_for_any_selector(
            page,
            item_selectors,
            timeout_ms=min(2500, _browser_timeout_ms()),
        )
        if item_loc is not None:
            try:
                item_loc.click(timeout=min(_browser_timeout_ms(), 8000))
            except Exception:
                item_selector = ""
        if item_loc is None and isinstance(conversation_hint, dict) and conversation_hint:
            clicked, hint_selector = _try_click_conversation_by_hint(page, conversation_hint)
            if clicked:
                item_selector = hint_selector

        input_selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_INPUT_SELECTORS", ""))
        if not input_selectors:
            input_selectors = _default_chat_input_selectors()
        input_loc, input_selector = _wait_for_any_selector(
            page,
            input_selectors,
            timeout_ms=min(5000, _browser_timeout_ms()),
        )
        if input_loc is None:
            return {
                "ok": False,
                "status": "selector_missing",
                "source": "boss_mcp_browser",
                "error": "chat input selector not found",
                "url": current_url,
            }
        _fill_text(input_loc, safe_reply)

        send_selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_SEND_SELECTORS", ""))
        if not send_selectors:
            send_selectors = _default_chat_send_selectors()
        send_loc, send_selector = _wait_for_any_selector(
            page,
            send_selectors,
            timeout_ms=min(5000, _browser_timeout_ms()),
        )
        if send_loc is None:
            return {
                "ok": False,
                "status": "selector_missing",
                "source": "boss_mcp_browser",
                "error": "chat send selector not found",
                "url": current_url,
                "input_selector": input_selector,
            }
        send_loc.click(timeout=min(_browser_timeout_ms(), 8000))
        screenshot_path = _take_browser_screenshot(page, prefix=f"reply_{safe_conversation_id}")
        return {
            "ok": True,
            "status": "sent",
            "source": "boss_mcp_browser",
            "error": None,
            "url": current_url,
            "conversation_hint": dict(conversation_hint or {}),
            "conversation_selector": item_selector or None,
            "input_selector": input_selector,
            "send_selector": send_selector,
            "screenshot_path": screenshot_path,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_error",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
            "url": str(getattr(page, "url", "") or ""),
        }


def _execute_browser_greet(
    *,
    run_id: str,
    job_id: str,
    source_url: str,
    greeting_text: str,
) -> dict[str, Any]:
    _ = run_id, job_id
    safe_url = str(source_url or "").strip()
    safe_text = str(greeting_text or "").strip()
    if not safe_url:
        return {
            "ok": False,
            "status": "failed",
            "source": "boss_mcp_browser",
            "error": "source_url is required",
        }
    try:
        page = _ensure_browser_page()
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_unavailable",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
        }
    try:
        page.goto(safe_url, wait_until="domcontentloaded", timeout=_browser_timeout_ms())
        current_url = str(page.url or "")
        risk_status = _detect_runtime_risk(page, current_url=current_url)
        if risk_status == "auth_required":
            return {
                "ok": False,
                "status": "auth_required",
                "source": "boss_mcp_browser",
                "error": "boss login is required",
                "url": current_url,
            }
        if risk_status == "risk_blocked":
            return {
                "ok": False,
                "status": "risk_blocked",
                "source": "boss_mcp_browser",
                "error": "risk or captcha page detected",
                "url": current_url,
            }
        greet_selectors = _csv_list(os.getenv("PULSE_BOSS_GREET_BUTTON_SELECTORS", ""))
        if not greet_selectors:
            greet_selectors = _default_greet_button_selectors()
        greet_loc, greet_selector = _wait_for_any_selector(
            page,
            greet_selectors,
            timeout_ms=min(6000, _browser_timeout_ms()),
        )
        if greet_loc is None:
            return {
                "ok": False,
                "status": "selector_missing",
                "source": "boss_mcp_browser",
                "error": "greet button selector not found",
                "url": current_url,
            }
        greet_loc.click(timeout=min(_browser_timeout_ms(), 8000))

        input_selector = ""
        send_selector = ""
        if safe_text:
            input_selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_INPUT_SELECTORS", ""))
            if not input_selectors:
                input_selectors = _default_chat_input_selectors()
            input_loc, input_selector = _wait_for_any_selector(
                page,
                input_selectors,
                timeout_ms=min(5000, _browser_timeout_ms()),
            )
            if input_loc is not None:
                _fill_text(input_loc, safe_text)
                send_selectors = _csv_list(os.getenv("PULSE_BOSS_CHAT_SEND_SELECTORS", ""))
                if not send_selectors:
                    send_selectors = _default_chat_send_selectors()
                send_loc, send_selector = _wait_for_any_selector(
                    page,
                    send_selectors,
                    timeout_ms=min(5000, _browser_timeout_ms()),
                )
                if send_loc is not None:
                    send_loc.click(timeout=min(_browser_timeout_ms(), 8000))
        screenshot_path = _take_browser_screenshot(page, prefix=f"greet_{job_id or 'job'}")
        status = "sent" if send_selector else "clicked"
        return {
            "ok": True,
            "status": status,
            "source": "boss_mcp_browser",
            "error": None,
            "url": current_url,
            "greet_selector": greet_selector,
            "input_selector": input_selector or None,
            "send_selector": send_selector or None,
            "screenshot_path": screenshot_path,
        }
    except Exception as exc:
        return {
            "ok": False,
            "status": "executor_error",
            "source": "boss_mcp_browser",
            "error": str(exc)[:300],
            "url": str(getattr(page, "url", "") or ""),
        }


def _action_audit_path() -> Path:
    return _resolve_path(
        os.getenv("PULSE_BOSS_MCP_ACTION_AUDIT_PATH", "").strip(),
        default_path=Path.home() / ".pulse" / "boss_mcp_actions.jsonl",
    )


def _append_action_log(row: dict[str, Any]) -> None:
    path = _action_audit_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload = dict(row)
    payload["logged_at"] = datetime.now(timezone.utc).isoformat()
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def scan_jobs(
    *,
    keyword: str,
    max_items: int,
    max_pages: int,
    job_type: str = "all",
) -> dict[str, Any]:
    safe_keyword = str(keyword or "").strip() or "AI Agent 实习"
    safe_items = _safe_int(max_items, 10, min_value=1, max_value=80)
    safe_pages = _safe_int(max_pages, 2, min_value=1, max_value=8)
    _ = str(job_type or "all").strip() or "all"
    mode = _scan_mode()
    browser_errors: list[str] = []

    if mode in {"browser_only", "browser_first"}:
        browser_result = _scan_jobs_via_browser(
            keyword=safe_keyword,
            max_items=safe_items,
            max_pages=safe_pages,
        )
        browser_items = browser_result.get("items")
        if isinstance(browser_items, list) and browser_items:
            return {
                "ok": True,
                "items": browser_items[:safe_items],
                "pages_scanned": max(1, _safe_int(browser_result.get("pages_scanned"), 1, min_value=1, max_value=99)),
                "source": str(browser_result.get("source") or "boss_mcp_browser_scan"),
                "errors": list(browser_result.get("errors") or []),
                "mode": mode,
            }
        browser_errors.extend(str(err)[:300] for err in list(browser_result.get("errors") or []))
        browser_status = str(browser_result.get("status") or "").strip()
        browser_url = str(browser_result.get("url") or "").strip()
        if browser_status:
            browser_errors.append(f"browser_status={browser_status}")
        if browser_url:
            browser_errors.append(f"browser_url={browser_url}")
        if mode == "browser_only":
            return {
                "ok": bool(browser_result.get("ok")),
                "items": list(browser_items or []),
                "pages_scanned": max(1, _safe_int(browser_result.get("pages_scanned"), 1, min_value=1, max_value=99)),
                "source": str(browser_result.get("source") or "boss_mcp_browser_scan"),
                "errors": browser_errors or [f"browser scan failed: {str(browser_result.get('status') or 'unknown')}"],
                "mode": mode,
            }

    query_pool = (
        f"site:zhipin.com {safe_keyword} 实习",
        f"site:zhipin.com {safe_keyword} 招聘",
        f"site:zhipin.com {safe_keyword} 岗位",
        f"{safe_keyword} BOSS直聘",
    )
    rows: list[dict[str, Any]] = []
    seen: set[str] = set()
    errors: list[str] = list(browser_errors)
    pages_scanned = 0
    for query in query_pool[:safe_pages]:
        pages_scanned += 1
        try:
            hits = search_web(query, max_results=min(12, safe_items * 2))
        except Exception as exc:
            errors.append(str(exc)[:300])
            continue
        for hit in hits:
            if len(rows) >= safe_items:
                break
            source_url = str(hit.url or "").strip()
            title_raw = str(hit.title or "").strip()
            if not source_url and not title_raw:
                continue
            dedupe_key = (source_url or title_raw).lower()
            if dedupe_key in seen:
                continue
            seen.add(dedupe_key)
            title = _guess_title(title_raw, keyword=safe_keyword)
            company = _guess_company(title_raw, source_url)
            if not source_url:
                source_url = f"https://www.zhipin.com/job_detail/{hashlib.sha1(dedupe_key.encode('utf-8')).hexdigest()[:16]}"
            rows.append(
                {
                    "job_id": hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16],
                    "title": title,
                    "company": company,
                    "salary": None,
                    "source_url": source_url,
                    "snippet": str(hit.snippet or "")[:1000],
                    "source": "boss_mcp_web_search",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        if len(rows) >= safe_items:
            break
    if not rows and _allow_seed_fallback():
        seeded = int(hashlib.sha1(safe_keyword.encode("utf-8")).hexdigest()[:8], 16)
        for idx in range(safe_items):
            title, company, salary = _SEED_JOBS[(seeded + idx) % len(_SEED_JOBS)]
            source_url = f"https://www.zhipin.com/job_detail/seed_{seeded}_{idx}"
            rows.append(
                {
                    "job_id": hashlib.sha1(source_url.encode("utf-8")).hexdigest()[:16],
                    "title": title,
                    "company": company,
                    "salary": salary,
                    "source_url": source_url,
                    "snippet": f"{company} 正在招聘 {title}，关键词：{safe_keyword}",
                    "source": "boss_mcp_seed",
                    "collected_at": datetime.now(timezone.utc).isoformat(),
                }
            )
        errors.append("web search unavailable, switched to seed dataset")
    elif not rows:
        errors.append("web search returned no jobs and seed fallback is disabled")
    source = "boss_mcp_web_search"
    if rows and rows[0].get("source") == "boss_mcp_seed":
        source = "boss_mcp_seed"
    return {
        "ok": bool(rows),
        "items": rows[:safe_items],
        "pages_scanned": max(1, pages_scanned),
        "source": source,
        "errors": errors,
        "mode": mode,
    }


def job_detail(*, job_id: str, source_url: str) -> dict[str, Any]:
    safe_job_id = str(job_id or "").strip()
    safe_url = str(source_url or "").strip()
    if not safe_job_id and safe_url:
        safe_job_id = hashlib.sha1(safe_url.encode("utf-8")).hexdigest()[:16]
    if not safe_url:
        return {
            "ok": False,
            "detail": {},
            "error": "source_url is required",
            "source": "boss_mcp",
        }
    try:
        page_text = _read_page_text(safe_url, max_chars=2200)
        return {
            "ok": True,
            "detail": {
                "job_id": safe_job_id,
                "source_url": safe_url,
                "page_summary": page_text,
            },
            "source": "boss_mcp",
        }
    except Exception as exc:
        return {
            "ok": False,
            "detail": {},
            "error": str(exc)[:300],
            "source": "boss_mcp",
        }


def _inbox_path() -> Path:
    raw = os.getenv("PULSE_BOSS_CHAT_INBOX_PATH", "").strip()
    return _resolve_path(raw, default_path=Path.home() / ".pulse" / "boss_chat_inbox.jsonl")


def pull_conversations(
    *,
    max_conversations: int,
    unread_only: bool,
    fetch_latest_hr: bool,
    chat_tab: str,
) -> dict[str, Any]:
    safe_fetch_latest = bool(fetch_latest_hr)
    safe_chat_tab = str(chat_tab or "").strip()
    mode = _pull_mode()
    browser_errors: list[str] = []

    if mode in {"browser_only", "browser_first"}:
        browser_result = _pull_conversations_via_browser(
            max_conversations=max_conversations,
            unread_only=unread_only,
            fetch_latest_hr=safe_fetch_latest,
            chat_tab=safe_chat_tab,
        )
        browser_items = browser_result.get("items")
        if isinstance(browser_items, list) and browser_result.get("ok"):
            return {
                "ok": True,
                "items": browser_items,
                "unread_total": _safe_int(browser_result.get("unread_total"), 0, min_value=0, max_value=9999),
                "source": str(browser_result.get("source") or "boss_mcp_browser_chat"),
                "errors": list(browser_result.get("errors") or []),
                "mode": mode,
            }
        browser_errors.extend(str(err)[:300] for err in list(browser_result.get("errors") or []))
        browser_status = str(browser_result.get("status") or "").strip()
        browser_url = str(browser_result.get("url") or "").strip()
        if browser_status:
            browser_errors.append(f"browser_status={browser_status}")
        if browser_url:
            browser_errors.append(f"browser_url={browser_url}")
        if mode == "browser_only":
            return {
                "ok": bool(browser_result.get("ok")),
                "items": list(browser_items or []),
                "unread_total": _safe_int(browser_result.get("unread_total"), 0, min_value=0, max_value=9999),
                "source": str(browser_result.get("source") or "boss_mcp_browser_chat"),
                "errors": browser_errors or [f"browser pull failed: {str(browser_result.get('status') or 'unknown')}"],
                "mode": mode,
            }

    path = _inbox_path()
    rows: list[dict[str, Any]] = []
    if path.is_file():
        for line in path.read_text(encoding="utf-8").splitlines():
            raw = line.strip()
            if not raw:
                continue
            try:
                item = json.loads(raw)
            except Exception:
                continue
            if not isinstance(item, dict):
                continue
            conversation_id = str(item.get("conversation_id") or "").strip()
            if not conversation_id:
                seed = f"{item.get('company')}-{item.get('job_title')}-{item.get('hr_name')}"
                conversation_id = hashlib.sha1(seed.encode("utf-8")).hexdigest()[:16]
            row = {
                "conversation_id": conversation_id,
                "hr_name": str(item.get("hr_name") or "").strip(),
                "company": str(item.get("company") or "").strip(),
                "job_title": str(item.get("job_title") or "").strip(),
                "latest_message": str(item.get("latest_message") or "").strip(),
                "latest_time": str(item.get("latest_time") or "刚刚"),
                "unread_count": max(0, min(int(item.get("unread_count") or 0), 99)),
            }
            if row["hr_name"] and row["company"] and row["job_title"] and row["latest_message"]:
                rows.append(row)
    if unread_only:
        rows = [item for item in rows if int(item.get("unread_count") or 0) > 0]
    rows.reverse()
    safe_max = _safe_int(max_conversations, 20, min_value=1, max_value=200)
    rows = rows[:safe_max]
    return {
        "ok": True,
        "items": rows,
        "unread_total": sum(int(item.get("unread_count") or 0) for item in rows),
        "source": "boss_mcp_local_inbox",
        "errors": browser_errors,
        "mode": mode,
    }


def reply_conversation(
    *,
    conversation_id: str,
    reply_text: str,
    profile_id: str,
    conversation_hint: dict[str, Any] | None = None,
) -> dict[str, Any]:
    safe_conversation_id = str(conversation_id or "").strip()
    safe_reply_text = str(reply_text or "").strip()
    safe_profile_id = str(profile_id or "default").strip() or "default"
    if not safe_conversation_id:
        return {"ok": False, "status": "failed", "error": "conversation_id is required"}
    if not safe_reply_text:
        return {"ok": False, "status": "failed", "error": "reply_text is required"}
    _append_action_log(
        {
            "action": "reply_conversation",
            "conversation_id": safe_conversation_id,
            "profile_id": safe_profile_id,
            "reply_text": safe_reply_text,
            "conversation_hint": dict(conversation_hint or {}),
        }
    )
    mode = str(os.getenv("PULSE_BOSS_MCP_REPLY_MODE", "manual_required") or "").strip().lower()
    if mode in {"log_only", "dry_run_ok"}:
        result = {
            "ok": True,
            "status": "logged",
            "source": "boss_mcp",
            "error": None,
        }
    elif mode in {"browser", "playwright"}:
        result = _run_browser_executor_with_retry(
            "reply_conversation",
            lambda: _execute_browser_reply(
                conversation_id=safe_conversation_id,
                reply_text=safe_reply_text,
                profile_id=safe_profile_id,
                conversation_hint=dict(conversation_hint or {}),
            ),
        )
    else:
        result = {
            "ok": False,
            "status": "manual_required",
            "source": "boss_mcp",
            "error": "reply executor is not configured yet; action is logged for audit",
        }
    _append_action_log(
        {
            "action": "reply_conversation_result",
            "conversation_id": safe_conversation_id,
            "profile_id": safe_profile_id,
            "mode": mode,
            "status": str(result.get("status") or ""),
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or "")[:300] or None,
            "source": str(result.get("source") or "boss_mcp"),
            "conversation_hint": dict(conversation_hint or {}),
            "screenshot_path": str(result.get("screenshot_path") or "") or None,
        }
    )
    return result


def greet_job(
    *,
    run_id: str,
    job_id: str,
    source_url: str,
    job_title: str,
    company: str,
    greeting_text: str,
) -> dict[str, Any]:
    safe_run_id = str(run_id or "").strip()
    safe_job_id = str(job_id or "").strip()
    safe_source_url = str(source_url or "").strip()
    if not safe_job_id and safe_source_url:
        safe_job_id = hashlib.sha1(safe_source_url.encode("utf-8")).hexdigest()[:16]
    _append_action_log(
        {
            "action": "greet_job",
            "run_id": safe_run_id,
            "job_id": safe_job_id,
            "source_url": safe_source_url,
            "job_title": str(job_title or "").strip(),
            "company": str(company or "").strip(),
            "greeting_text": str(greeting_text or "").strip(),
        }
    )
    mode = str(os.getenv("PULSE_BOSS_MCP_GREET_MODE", "manual_required") or "").strip().lower()
    if mode in {"log_only", "dry_run_ok"}:
        result = {
            "ok": True,
            "status": "logged",
            "source": "boss_mcp",
            "error": None,
        }
    elif mode in {"browser", "playwright"}:
        result = _run_browser_executor_with_retry(
            "greet_job",
            lambda: _execute_browser_greet(
                run_id=safe_run_id,
                job_id=safe_job_id,
                source_url=safe_source_url,
                greeting_text=str(greeting_text or "").strip(),
            ),
        )
    else:
        result = {
            "ok": False,
            "status": "manual_required",
            "source": "boss_mcp",
            "error": "greet executor is not configured yet; action is logged for audit",
        }
    _append_action_log(
        {
            "action": "greet_job_result",
            "run_id": safe_run_id,
            "job_id": safe_job_id,
            "source_url": safe_source_url,
            "mode": mode,
            "status": str(result.get("status") or ""),
            "ok": bool(result.get("ok")),
            "error": str(result.get("error") or "")[:300] or None,
            "source": str(result.get("source") or "boss_mcp"),
            "screenshot_path": str(result.get("screenshot_path") or "") or None,
        }
    )
    return result


def mark_processed(*, conversation_id: str, run_id: str, note: str = "") -> dict[str, Any]:
    safe_conversation_id = str(conversation_id or "").strip()
    if not safe_conversation_id:
        return {"ok": False, "status": "failed", "error": "conversation_id is required"}
    _append_action_log(
        {
            "action": "mark_processed",
            "conversation_id": safe_conversation_id,
            "run_id": str(run_id or "").strip(),
            "note": str(note or "").strip(),
        }
    )
    return {
        "ok": True,
        "status": "marked",
        "source": "boss_mcp",
        "error": None,
    }


def health() -> dict[str, Any]:
    return {
        "ok": True,
        "source": "boss_mcp",
        "inbox_path": str(_inbox_path()),
        "action_audit_path": str(_action_audit_path()),
        "scan_mode": _scan_mode(),
        "pull_mode": _pull_mode(),
        "seed_fallback_enabled": _allow_seed_fallback(),
        "reply_mode": str(os.getenv("PULSE_BOSS_MCP_REPLY_MODE", "manual_required")).strip() or "manual_required",
        "greet_mode": str(os.getenv("PULSE_BOSS_MCP_GREET_MODE", "manual_required")).strip() or "manual_required",
        "browser": {
            "profile_dir": str(_browser_profile_dir()),
            "headless": _browser_headless(),
            "timeout_ms": _browser_timeout_ms(),
            "channel": _browser_channel() or None,
            "user_agent": _browser_user_agent(),
            "stealth_enabled": _browser_stealth_enabled(),
            "block_iframe_core": _browser_block_iframe_core(),
            "login_check_url": str(os.getenv("PULSE_BOSS_LOGIN_CHECK_URL", "https://www.zhipin.com/web/geek/chat")),
            "chat_url_template": str(os.getenv("PULSE_BOSS_CHAT_URL_TEMPLATE", "") or "").strip() or None,
            "screenshot_dir": str(_browser_screenshot_dir()) if _browser_screenshot_dir() is not None else None,
            "executor_retry_count": _browser_executor_retry_count(),
            "executor_retry_backoff_ms": _browser_executor_retry_backoff_ms(),
            "risk_keywords": _risk_keywords(),
            "greet_button_selectors": _csv_list(os.getenv("PULSE_BOSS_GREET_BUTTON_SELECTORS", "")),
            "chat_input_selectors": _csv_list(os.getenv("PULSE_BOSS_CHAT_INPUT_SELECTORS", "")),
            "chat_send_selectors": _csv_list(os.getenv("PULSE_BOSS_CHAT_SEND_SELECTORS", "")),
            "search_url_template": _search_url_template(),
            "search_next_selectors": _csv_list(os.getenv("PULSE_BOSS_SEARCH_NEXT_SELECTORS", "")),
            "job_card_selectors": _csv_list(os.getenv("PULSE_BOSS_JOB_CARD_SELECTORS", "")),
            "job_nav_selectors": _csv_list(os.getenv("PULSE_BOSS_JOB_NAV_SELECTORS", "")),
            "job_search_input_selectors": _csv_list(os.getenv("PULSE_BOSS_JOB_SEARCH_INPUT_SELECTORS", "")),
            "chat_list_url": _chat_list_url(),
            "chat_row_selectors": _csv_list(os.getenv("PULSE_BOSS_CHAT_ROW_SELECTORS", "")),
            "chat_tab_selectors": _csv_list(os.getenv("PULSE_BOSS_CHAT_TAB_SELECTORS", "")),
        },
    }
