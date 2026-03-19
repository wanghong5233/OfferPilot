from __future__ import annotations

import logging
import os
import random
import re
import signal
import subprocess
from datetime import datetime
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any
from urllib.parse import quote

from app.tz import now_beijing

from .agent_events import EventType, emit
from .email_notify import notify_cookie_expired
from .schemas import BossChatConversationItem, BossScanItem

logger = logging.getLogger(__name__)

_LOGIN_URL_MARKERS = ["/web/user/", "/login", "passport.zhipin.com"]


def _ensure_guard_debug_logger() -> logging.Logger:
    """确保 guard_file logger 始终可用（即使手动触发 API 也会落盘）。"""
    lg = logging.getLogger("guard_file")
    if lg.handlers:
        return lg
    try:
        log_dir = _project_root() / "backend" / "logs"
        log_dir.mkdir(parents=True, exist_ok=True)
        fh = RotatingFileHandler(
            str(log_dir / "guard.log"),
            maxBytes=20 * 1024 * 1024,
            backupCount=5,
            encoding="utf-8",
        )
        fh.setFormatter(
            logging.Formatter(
                "%(asctime)s | %(levelname)s | %(message)s",
                datefmt="%Y-%m-%d %H:%M:%S",
            )
        )
        lg.addHandler(fh)
        lg.setLevel(logging.DEBUG)
        lg.propagate = False
    except Exception:
        # logger 初始化失败不影响主流程
        pass
    return lg


def _check_login_required(page: Any) -> bool:
    """检测 BOSS 直聘是否需要登录（Cookie 过期）。"""
    try:
        current_url = page.url or ""
        for marker in _LOGIN_URL_MARKERS:
            if marker in current_url:
                return True
        login_form = page.locator(".login-form, .qr-code-area, .scan-login, .login-container")
        if login_form.count() > 0:
            return True
    except Exception:
        pass
    return False


def _handle_cookie_expired(page: Any, operation: str) -> None:
    """Cookie 过期时：截图 + emit 事件 + 飞书告警 + 重置浏览器会话。"""
    global _browser_context
    emit(EventType.ERROR, f"BOSS Cookie 已过期，{operation} 无法继续，请重新登录")
    try:
        shot_dir = _screenshot_dir()
        shot_dir.mkdir(parents=True, exist_ok=True)
        shot_path = shot_dir / f"cookie_expired_{now_beijing().strftime('%Y%m%d_%H%M%S')}.png"
        page.screenshot(path=str(shot_path))
        emit(EventType.BROWSER_SCREENSHOT, "Cookie 过期截图", path=str(shot_path))
    except Exception:
        pass
    notify_cookie_expired("BOSS 直聘")
    _browser_context = None
    logger.info("Cookie 过期，已重置浏览器会话引用，下次调用将重新创建")


def _headless() -> bool:
    return os.getenv("BOSS_HEADLESS", "false").strip().lower() in {"1", "true", "yes"}


def _stealth_enabled() -> bool:
    return os.getenv("BOSS_ENABLE_STEALTH", "true").strip().lower() in {"1", "true", "yes"}


def _action_delay_ms() -> tuple[int, int]:
    raw_min = os.getenv("BOSS_ACTION_DELAY_MIN_MS", "3000")
    raw_max = os.getenv("BOSS_ACTION_DELAY_MAX_MS", "5000")
    try:
        min_ms = int(raw_min)
    except ValueError:
        min_ms = 3000
    try:
        max_ms = int(raw_max)
    except ValueError:
        max_ms = 5000
    min_ms = max(0, min_ms)
    max_ms = max(min_ms, max_ms)
    return min_ms, max_ms


_ANTI_DETECT_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--disable-session-crashed-bubble",
    "--disable-features=InfiniteSessionRestore",
    "--hide-crash-restore-bubble",
]
_ANTI_DETECT_IGNORE = ["--enable-automation"]
_REAL_UA = (
    "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/131.0.0.0 Safari/537.36"
)

_KILL_ZHIPIN_FRAME_JS = """
(function() {
    if (typeof document === 'undefined') return;
    var observer = new MutationObserver(function(mutations) {
        for (var i = 0; i < mutations.length; i++) {
            var nodes = mutations[i].addedNodes;
            for (var j = 0; j < nodes.length; j++) {
                var node = nodes[j];
                if (node.tagName === 'IFRAME' && (node.name === 'zhipinFrame' || node.id === 'zhipinFrame')) {
                    node.remove();
                }
            }
        }
    });
    if (document.documentElement) {
        observer.observe(document.documentElement, {childList: true, subtree: true});
    } else {
        document.addEventListener('DOMContentLoaded', function() {
            observer.observe(document.documentElement, {childList: true, subtree: true});
        });
    }
})();
"""


def _aggressive_antibot() -> bool:
    return os.getenv("BOSS_AGGRESSIVE_ANTIBOT", "false").strip().lower() in {"1", "true", "yes"}


def _project_root() -> Path:
    """项目根目录（OfferPilot）。"""
    return Path(__file__).resolve().parents[2]


def _clear_profile_lock_files(profile_dir: Path) -> None:
    """清理 Chrome profile 的残留锁文件（崩溃恢复场景）。"""
    for name in ("SingletonLock", "SingletonSocket", "SingletonCookie"):
        try:
            (profile_dir / name).unlink(missing_ok=True)
        except Exception:
            pass


def _kill_profile_chrome_processes(profile_dir: Path) -> int:
    """兜底清理占用同一 profile 的僵尸 Chrome 进程。"""
    killed = 0
    profile = str(profile_dir)
    try:
        result = subprocess.run(
            ["ps", "-eo", "pid,args"],
            capture_output=True,
            text=True,
            timeout=5,
        )
        if result.returncode != 0:
            return 0
        for line in result.stdout.splitlines():
            if "chrome" not in line or "--user-data-dir=" not in line:
                continue
            if profile not in line:
                continue
            parts = line.strip().split(maxsplit=1)
            if not parts:
                continue
            try:
                pid = int(parts[0])
            except ValueError:
                continue
            try:
                os.kill(pid, signal.SIGKILL)
                killed += 1
            except (ProcessLookupError, PermissionError):
                continue
    except Exception:
        return killed
    if killed > 0:
        logger.warning("Killed %d stale chrome process(es) for profile=%s", killed, profile)
    return killed


# ---------------------------------------------------------------------------
# Browser session pool — singleton that stays alive across API calls
# ---------------------------------------------------------------------------

import threading

_browser_lock = threading.Lock()
_LOCK_TIMEOUT = 60
_browser_pw: Any = None
_browser_context: Any = None


def _get_browser_context() -> Any:
    """Return a long-lived patchright persistent context (singleton).

    The browser stays open between API calls, just like a real user keeps
    their browser running.  This avoids repeated open/close cycles that
    trigger BOSS security checks and cookie invalidation.
    """
    global _browser_pw, _browser_context
    acquired = _browser_lock.acquire(timeout=_LOCK_TIMEOUT)
    if not acquired:
        raise RuntimeError(f"浏览器锁等待超过 {_LOCK_TIMEOUT}s，可能存在死锁")
    try:
        if _browser_context is not None:
            try:
                pages = _browser_context.pages
                created_probe = False
                probe_page = pages[0] if pages else _browser_context.new_page()
                if not pages:
                    created_probe = True
                probe_page.evaluate("() => 1")
                if created_probe:
                    probe_page.close()
                return _browser_context
            except Exception:
                logger.warning("Browser context dead, full rebuild")
                try:
                    _browser_context.close()
                except Exception:
                    pass
                _browser_context = None
                try:
                    if _browser_pw is not None:
                        _browser_pw.stop()
                except Exception:
                    pass
                _browser_pw = None

        from patchright.sync_api import sync_playwright

        if _browser_pw is None:
            _browser_pw = sync_playwright().start()

        headless = _headless()
        profile_dir = _profile_dir()
        profile_dir.mkdir(parents=True, exist_ok=True)
        _clear_profile_lock_files(profile_dir)
        try:
            _browser_context = _browser_pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=headless,
                no_viewport=True,
                ignore_default_args=_ANTI_DETECT_IGNORE,
                args=["--no-sandbox", *_ANTI_DETECT_ARGS],
            )
        except Exception as exc:
            err = str(exc).lower()
            recoverable = (
                "opening in existing browser session" in err
                or "target page, context or browser has been closed" in err
            )
            if not recoverable:
                raise
            logger.warning("Browser launch failed, try hard recovery: %s", str(exc)[:200])
            _kill_profile_chrome_processes(profile_dir)
            _clear_profile_lock_files(profile_dir)
            try:
                if _browser_pw is not None:
                    _browser_pw.stop()
            except Exception:
                pass
            _browser_pw = sync_playwright().start()
            _browser_context = _browser_pw.chromium.launch_persistent_context(
                user_data_dir=str(profile_dir),
                channel="chrome",
                headless=headless,
                no_viewport=True,
                ignore_default_args=_ANTI_DETECT_IGNORE,
                args=["--no-sandbox", *_ANTI_DETECT_ARGS],
            )
        logger.info("Browser session created (headless=%s, profile=%s)", headless, profile_dir)
        return _browser_context
    finally:
        _browser_lock.release()


def shutdown_browser() -> None:
    """Explicitly close the singleton browser (called at app shutdown)."""
    global _browser_pw, _browser_context
    acquired = _browser_lock.acquire(timeout=15)
    if not acquired:
        logger.warning("shutdown_browser: 无法获取锁，强制清空引用")
        _browser_context = None
        _browser_pw = None
        return
    try:
        if _browser_context is not None:
            try:
                _browser_context.close()
            except Exception:
                pass
            _browser_context = None
        if _browser_pw is not None:
            try:
                _browser_pw.stop()
            except Exception:
                pass
            _browser_pw = None
    finally:
        _browser_lock.release()


def cleanup_browser_tabs() -> int:
    """关闭多余标签页，只保留第一个主 page。返回关闭的标签页数。

    防止长期运行中 JD 提取等操作累积未关闭的标签页导致内存膨胀。
    """
    global _browser_context
    if _browser_context is None:
        return 0
    closed = 0
    try:
        pages = _browser_context.pages
        if len(pages) <= 1:
            return 0
        for p in pages[1:]:
            try:
                p.close()
                closed += 1
            except Exception:
                pass
        if closed > 0:
            emit(EventType.INFO, f"标签页清理：关闭了 {closed} 个多余标签页")
    except Exception:
        pass
    return closed


def get_browser_health() -> dict:
    """返回浏览器单例的健康状态（供 /health 端点使用）。"""
    if _browser_context is None:
        return {"alive": False, "pages": 0, "status": "not_started"}
    try:
        pages = _browser_context.pages
        return {"alive": True, "pages": len(pages), "status": "ok"}
    except Exception as exc:
        return {"alive": False, "pages": 0, "status": f"error: {str(exc)[:100]}"}


def boss_login_via_pool(timeout: int = 300) -> dict:
    """Open the session-pool browser and wait for the user to log in.

    This navigates to BOSS in the **same** browser context that later API calls
    will use, so the session persists.  The user can log in via QR code or
    phone number in the visible browser window.

    Returns a status dict: {"ok": bool, "message": str, "url": str, "chat_items": int}
    """
    context = _get_browser_context()
    page = _get_page(context)

    authed = _navigate_and_check_auth(page, _BOSS_CHAT_URL, operation="登录检查")
    if authed:
        # Already logged in — wait for chat items to render
        try:
            page.wait_for_selector('li[role="listitem"]', timeout=10000)
        except Exception:
            pass
        try:
            count = page.locator('li[role="listitem"]').count()
        except Exception:
            count = 0
        url = page.url
        return {"ok": True, "message": f"已登录，聊天列表 {count} 个对话", "url": url, "chat_items": count}

    # Need login — wait for user to complete it
    logger.info("Waiting for user login (timeout=%ds)...", timeout)
    start = _time.time()
    while _time.time() - start < timeout:
        try:
            current = page.url or ""
        except Exception:
            break
        if not any(m in current for m in _LOGIN_URL_MARKERS) and "zhipin.com" in current:
            _time.sleep(3)
            try:
                page.wait_for_selector('li[role="listitem"]', timeout=10000)
            except Exception:
                pass
            try:
                count = page.locator('li[role="listitem"]').count()
            except Exception:
                count = 0
            url = page.url
            logger.info("Login successful: url=%s, chat_items=%d", url, count)
            return {"ok": True, "message": f"登录成功，聊天列表 {count} 个对话", "url": url, "chat_items": count}
        _time.sleep(2)

    return {"ok": False, "message": "登录超时，请在浏览器窗口中完成登录", "url": page.url, "chat_items": 0}


def _get_page(context: Any) -> Any:
    """Get a usable page from the context, reusing existing or creating new."""
    page = None
    try:
        pages = context.pages
    except Exception:
        pages = []

    for p in pages:
        try:
            if not p.is_closed():
                page = p
                break
        except Exception:
            continue

    if page is None:
        page = context.new_page()
    page.set_default_timeout(20000)
    return page


# ---------------------------------------------------------------------------
# Navigation helper — waits for SPA auth redirect to settle
# ---------------------------------------------------------------------------

import time as _time


def _navigate_and_check_auth(page: Any, url: str, *, operation: str) -> bool:
    """Navigate to *url*, wait for SPA to settle, then verify auth.

    BOSS's SPA performs an async auth check after DOM loads.  If the session
    is invalid it redirects to ``/web/user/`` a few seconds later.  This
    helper detects that redirect reliably.

    Returns True if authenticated, False if login is required.
    """
    page.goto(url, wait_until="domcontentloaded")

    for _ in range(6):
        _time.sleep(1)
        current = page.url or ""
        for marker in _LOGIN_URL_MARKERS:
            if marker in current:
                logger.warning("Auth redirect detected → %s (during %s)", current, operation)
                return False
        login_form = page.locator(".login-form, .qr-code-area, .scan-login, .login-container")
        try:
            if login_form.count() > 0:
                logger.warning("Login form detected on page (during %s)", operation)
                return False
        except Exception:
            pass
    return True


def _launch_browser(pw: Any, *, headless: bool | None = None) -> Any:
    """Legacy launcher — kept for backward compatibility but prefer _get_browser_context()."""
    if headless is None:
        headless = _headless()
    profile_dir = _profile_dir()
    profile_dir.mkdir(parents=True, exist_ok=True)
    context = pw.chromium.launch_persistent_context(
        user_data_dir=str(profile_dir),
        channel="chrome",
        headless=headless,
        no_viewport=True,
        ignore_default_args=_ANTI_DETECT_IGNORE,
        args=["--no-sandbox", *_ANTI_DETECT_ARGS],
    )
    return context


def _fetch_detail_enabled() -> bool:
    return os.getenv("BOSS_FETCH_DETAIL", "false").strip().lower() in {"1", "true", "yes"}


def _profile_dir() -> Path:
    configured = os.getenv("BOSS_BROWSER_PROFILE_DIR", "").strip()
    if configured:
        raw = Path(configured).expanduser()
        if raw.is_absolute():
            return raw.resolve()
        # 统一与 scripts/boss-login.sh 的相对路径基准（项目根目录）一致，
        # 避免 backend/backend/.playwright 这种路径漂移导致会话不一致。
        return (_project_root() / raw).resolve()
    return (_project_root() / "backend" / ".playwright" / "boss").resolve()


def _screenshot_dir() -> Path:
    configured = os.getenv("BOSS_SCREENSHOT_DIR", "").strip()
    if configured:
        return Path(configured).expanduser().resolve()
    return (Path(__file__).resolve().parents[1] / "exports" / "screenshots").resolve()


def _greet_batch_size() -> int:
    raw = os.getenv("BOSS_GREET_BATCH_SIZE", "").strip()
    if not raw:
        from .skill_loader import get_parameter
        raw = get_parameter("batch_size", "3")
    try:
        return max(1, min(int(raw), 10))
    except ValueError:
        return 3


def _greet_delay_ms() -> tuple[int, int]:
    raw_min = os.getenv("BOSS_GREET_DELAY_MIN_MS", "30000").strip()
    raw_max = os.getenv("BOSS_GREET_DELAY_MAX_MS", "90000").strip()
    try:
        min_ms = int(raw_min)
    except ValueError:
        min_ms = 30000
    try:
        max_ms = int(raw_max)
    except ValueError:
        max_ms = 90000
    return max(5000, min_ms), max(max(5000, min_ms), max_ms)


def _greet_daily_limit() -> int:
    raw = os.getenv("BOSS_GREET_DAILY_LIMIT", "").strip()
    if not raw:
        from .skill_loader import get_parameter
        raw = get_parameter("daily_limit", "50")
    try:
        return max(1, min(int(raw), 200))
    except ValueError:
        return 50


def _greet_match_threshold() -> float:
    raw = os.getenv("BOSS_GREET_MATCH_THRESHOLD", "70").strip()
    try:
        return max(30.0, min(float(raw), 95.0))
    except ValueError:
        return 70.0


_FULLTIME_SALARY_RE = re.compile(r"\d+[kK]|\d+-\d+[kK]|\d+万")
_INTERN_SALARY_RE = re.compile(r"\d+.*[/·].*天|\d+.*元.*天|天/|/天")
_INTERN_TITLE_KEYWORDS = re.compile(r"实习|intern|日薪|兼职", re.IGNORECASE)
_FULLTIME_TITLE_KEYWORDS = re.compile(r"社招|[^实]习(?!生)|资深|高级|senior|P[5-9]|T[5-9]|经验", re.IGNORECASE)

# 方向门控正则 — 运行时从 skills/jd-filter/SKILL.md 热加载
# 以下硬编码值仅作为 Skill 文件不存在时的兜底 fallback
_FALLBACK_POSITIVE_RE = re.compile(
    r"agent|智能体|大模型应用|llm应用|应用开发|应用工程|rag|langgraph|langchain|mcp|工作流|workflow|prompt|"
    r"tool\s*call|function\s*call|copilot|对话系统|业务落地|应用落地",
    re.IGNORECASE,
)
_FALLBACK_NEGATIVE_RE = re.compile(
    r"预训练|pre[-\s]?train|post[-\s]?train|底座|模型训练|训练优化|蒸馏|rlhf|sft|dpo|"
    r"算法研究|推荐算法|搜索算法|视觉算法|多模态训练|基座研发",
    re.IGNORECASE,
)


def _is_intern_salary(salary: str | None) -> bool:
    """判断薪资文本是否为实习薪资格式（按天计薪）。"""
    if not salary:
        return False
    return bool(_INTERN_SALARY_RE.search(salary))


def _is_fulltime_salary(salary: str | None) -> bool:
    """判断薪资文本是否为全职薪资格式（含 K/万 等月薪标识）。"""
    if not salary:
        return False
    return bool(_FULLTIME_SALARY_RE.search(salary))


def _salary_matches_job_type(salary: str | None, job_type: str, title: str = "", snippet: str = "") -> bool:
    """根据 profile 中的 job_type 判断岗位是否匹配。

    BOSS 直聘对薪资数字做了反爬加密（自定义字体/SVG），DOM 中只能拿到「-元/天」「-K」等格式部分。
    因此优先用薪资格式后缀判断，再用标题/snippet 关键词兜底。
    """
    if job_type == "all":
        return True

    s = (salary or "").strip()

    if s:
        has_day_suffix = bool(re.search(r"元/天|/天|·天", s))
        has_month_suffix = bool(re.search(r"[kK]|万|薪", s))

        if job_type == "intern":
            if has_month_suffix and not has_day_suffix:
                return False
            if has_day_suffix:
                return True
        if job_type == "fulltime":
            if has_day_suffix and not has_month_suffix:
                return False
            if has_month_suffix:
                return True

    context = f"{title} {snippet}"
    if job_type == "intern":
        if _INTERN_TITLE_KEYWORDS.search(context):
            return True
        if _FULLTIME_TITLE_KEYWORDS.search(context) and not _INTERN_TITLE_KEYWORDS.search(context):
            return False
        return True
    if job_type == "fulltime":
        if _FULLTIME_TITLE_KEYWORDS.search(context):
            return True
        if _INTERN_TITLE_KEYWORDS.search(context) and not _FULLTIME_TITLE_KEYWORDS.search(context):
            return False
        return True
    return True


def _greet_direction_mode() -> str:
    """主动打招呼方向门控模式：strict / auto / off。优先读 .env，fallback 读 Skill 配置。"""
    raw = os.getenv("BOSS_GREET_DIRECTION_MODE", "").strip().lower()
    if raw in {"strict", "auto", "off"}:
        return raw
    from .skill_loader import get_parameter
    skill_val = get_parameter("direction_mode", "strict").strip().lower()
    if skill_val in {"strict", "auto", "off"}:
        return skill_val
    return "strict"


def _need_agent_direction_guard(keyword: str) -> bool:
    """是否启用 Agent/应用方向硬门控。"""
    mode = _greet_direction_mode()
    if mode == "off":
        return False
    if mode == "strict":
        return True
    k = (keyword or "").lower()
    return any(token in k for token in ["agent", "智能体", "应用", "llm", "rag", "mcp", "langgraph", "langchain"])


def _agent_direction_matches(title: str, snippet: str) -> tuple[bool, str]:
    """岗位方向门控：
    - 命中应用/Agent关键词：放行
    - 未命中应用关键词但命中算法/训练关键词：拦截
    - 都未命中：拦截（strict 策略，宁缺毋滥）
    """
    from .skill_loader import load_jd_filter_config

    cfg = load_jd_filter_config()
    _pos_re = cfg.accept_re if (cfg.accept_keywords or cfg.strong_accept_keywords) else _FALLBACK_POSITIVE_RE
    _neg_re = cfg.reject_re if cfg.reject_keywords else _FALLBACK_NEGATIVE_RE
    _strong_re = cfg.strong_accept_re if cfg.strong_accept_keywords else re.compile(
        r"应用|落地|工作流|rag|langgraph|langchain|mcp|copilot|tool\s*call|function\s*call|产品",
        re.IGNORECASE,
    )
    block_kws = cfg.title_block_keywords or ["算法"]
    require_app_kws = cfg.title_require_app_keywords or ["应用", "开发", "工程化", "落地"]

    text = f"{title} {snippet}".strip()
    has_positive = bool(_pos_re.search(text))
    has_negative = bool(_neg_re.search(text))
    has_app_signal = bool(_strong_re.search(text))
    has_agent_signal = bool(re.search(r"agent|智能体", text, re.IGNORECASE))

    title_lower = (title or "").lower()
    title_has_algo = any(kw.lower() in title_lower for kw in block_kws)
    title_has_explicit_app_track = any(kw.lower() in title_lower for kw in require_app_kws)
    title_has_agent = bool(re.search(r"agent|智能体|应用", title or "", re.IGNORECASE))

    # 强规则：标题带“算法”默认判为算法岗，除非标题明确写了应用/开发导向（宁缺毋滥）
    if title_has_algo and not title_has_explicit_app_track:
        return False, "title_contains_algorithm"

    if has_negative and not has_app_signal:
        return False, "hit_algorithm_track_keywords"
    if title_has_algo and not title_has_agent:
        return False, "hit_algorithm_track_keywords"
    if has_app_signal:
        return True, "hit_application_keywords"
    if has_agent_signal and not has_negative:
        return True, "hit_agent_keywords_without_algorithm_track"
    if has_positive and not has_negative:
        return True, "hit_positive_keywords"
    return False, "missing_agent_application_keywords"


_BOSS_CHAT_URL = "https://www.zhipin.com/web/geek/chat"

_CONVERSATION_LIST_SELECTORS = [
    'li[role="listitem"]',
    ".user-list li",
    ".chat-conversation-item",
    ".chat-item",
    ".geek-item",
    ".list-item",
    ".conversation-item",
    ".chat-list li",
    ".left-list li",
]


def _find_root_selector(page: Any, selectors: list[str] | None = None) -> tuple[str | None, int]:
    """Find the first matching selector for conversation list items."""
    for selector in (selectors or _CONVERSATION_LIST_SELECTORS):
        try:
            count = page.locator(selector).count()
        except Exception:
            count = 0
        if count > 0:
            return selector, count
    return None, 0


def _boss_search_url(keyword: str, page: int = 1) -> str:
    safe_page = max(1, page)
    return f"https://www.zhipin.com/web/geek/job?query={quote(keyword)}&page={safe_page}"


def _extract_cards(page: Any, max_items: int) -> list[BossScanItem]:
    raw_cards = page.evaluate(
        r"""
        () => {
          const selectors = [
            ".job-card-wrapper",
            ".job-card-box",
            ".search-job-result .job-card-wrapper",
            ".job-list-box li"
          ];
          let nodes = [];
          for (const selector of selectors) {
            const found = Array.from(document.querySelectorAll(selector));
            if (found.length > 0) {
              nodes = found;
              break;
            }
          }
          return nodes.map((card) => {
            const titleNode =
              card.querySelector(".job-name") ||
              card.querySelector(".job-title") ||
              card.querySelector("a");
            const companyNode =
              card.querySelector(".company-name") ||
              card.querySelector(".company-text") ||
              card.querySelector(".company");
            const salaryNode =
              card.querySelector(".salary") ||
              card.querySelector(".job-info .salary") ||
              card.querySelector(".job-limit .salary") ||
              card.querySelector("[class*='salary']") ||
              card.querySelector(".job-limit .red") ||
              card.querySelector(".red");
            const hrefNode = card.querySelector("a[href]");

            let salaryText = (salaryNode?.innerText || "").trim();
            if (!salaryText) {
              const allText = card.innerText || "";
              const mK = allText.match(/\d+-\d+[kK]|[\d.]+-[\d.]+万|\d+[kK]/);
              const mDay = allText.match(/\d+[\-~]?\d*元?[/·]天/);
              salaryText = (mDay && mDay[0]) || (mK && mK[0]) || "";
            }

            return {
              title: (titleNode?.innerText || "").trim(),
              company: (companyNode?.innerText || "").trim(),
              salary: salaryText,
              source_url: hrefNode ? hrefNode.href : null,
              snippet: (card.innerText || "").trim().slice(0, 400),
            };
          });
        }
        """
    )

    items: list[BossScanItem] = []
    for row in raw_cards[:max_items]:
        if not isinstance(row, dict):
            continue
        title = str(row.get("title") or "").strip()
        company = str(row.get("company") or "").strip()
        if not title:
            continue
        items.append(
            BossScanItem(
                title=title,
                company=company or "Unknown Company",
                salary=str(row.get("salary") or "").strip() or None,
                source_url=str(row.get("source_url") or "") or None,
                snippet=str(row.get("snippet") or "") or None,
                match_score=None,
            )
        )
    return items


def _item_dedupe_key(item: BossScanItem) -> str:
    title = "".join(item.title.lower().split())
    company = "".join(item.company.lower().split())
    url = (item.source_url or "").strip().lower()
    return f"{title}|{company}|{url}"


def _extract_detail_text(detail_page: Any) -> str:
    """从BOSS详情页提取完整JD文本（工作职责+任职资格等所有段落）。"""
    multi_selectors = [
        ".job-sec-text",
        ".job-detail-section .text",
    ]
    for selector in multi_selectors:
        try:
            locator = detail_page.locator(selector)
            count = locator.count()
            if count == 0:
                continue
            parts = []
            for i in range(count):
                text = locator.nth(i).inner_text().strip()
                if text:
                    parts.append(text)
            if parts:
                return "\n\n".join(parts)
        except Exception:
            continue

    single_selectors = [
        ".job-detail",
        ".job-description",
        ".detail-content",
        ".job-box .text",
    ]
    for selector in single_selectors:
        try:
            locator = detail_page.locator(selector).first
            if locator.count() == 0:
                continue
            text = locator.inner_text().strip()
            if text:
                return text
        except Exception:
            continue

    try:
        js_text = detail_page.evaluate(r"""() => {
            const sections = document.querySelectorAll('.job-sec-text, .job-detail-section .text, .job-detail .text');
            if (sections.length > 0) {
                return Array.from(sections).map(s => s.innerText.trim()).filter(t => t).join('\n\n');
            }
            const detail = document.querySelector('.job-detail, .detail-content, .job-box');
            return detail ? detail.innerText.trim() : '';
        }""")
        if js_text and len(js_text.strip()) > 20:
            return js_text.strip()
    except Exception:
        pass

    return ""


_WORKING_DAYS_PER_MONTH = (52 * 5) / 12  # 按每周 5 天折算（月工作日约 21.67）


def _salary_unit_multiplier(unit: str | None) -> float:
    u = (unit or "").strip()
    if not u:
        return 1.0
    if u in {"k", "K", "千"}:
        return 1000.0
    if u in {"w", "W", "万"}:
        return 10000.0
    return 1.0


def _to_yuan(value: str, unit: str | None, default_unit: str = "") -> float:
    unit_text = (unit or "").strip() or default_unit
    return float(value) * _salary_unit_multiplier(unit_text)


def _format_money(value: float | None) -> str:
    if value is None:
        return "?"
    if abs(value - round(value)) < 1e-9:
        return str(int(round(value)))
    return f"{value:.1f}"


def _parse_salary_to_daily_range(text: str) -> tuple[float, float, str] | None:
    """将薪资文本解析为日薪区间(min_daily, max_daily, basis)。

    basis:
      - "daily": 原始就是日薪
      - "monthly": 由月薪折算
    """
    s = (
        (text or "")
        .replace("／", "/")
        .replace("～", "-")
        .replace("~", "-")
        .replace("—", "-")
        .replace("－", "-")
        .replace("至", "-")
        .replace("到", "-")
        .strip()
    )
    if not s:
        return None

    unit_token = r"[kKwW千万元]"
    num_token = r"\d+(?:\.\d+)?"

    # 1) 日薪区间：160-250元/天
    m = re.search(
        rf"(?P<low>{num_token})\s*(?P<ul>{unit_token})?\s*-\s*"
        rf"(?P<high>{num_token})\s*(?P<uh>{unit_token})?\s*元?\s*/\s*(?:天|日)",
        s,
    )
    if m:
        ul = m.group("ul") or m.group("uh") or ""
        uh = m.group("uh") or m.group("ul") or ""
        low = _to_yuan(m.group("low"), ul)
        high = _to_yuan(m.group("high"), uh)
        return min(low, high), max(low, high), "daily"

    # 2) 日薪单值：220元/天
    m = re.search(
        rf"(?P<v>{num_token})\s*(?P<u>{unit_token})?\s*元?\s*/\s*(?:天|日)",
        s,
    )
    if m:
        v = _to_yuan(m.group("v"), m.group("u"))
        return v, v, "daily"

    # 3) 月薪区间（显式 /月）：3.5-4.5k/月、3500-4500元/月
    m = re.search(
        rf"(?P<low>{num_token})\s*(?P<ul>{unit_token})?\s*-\s*"
        rf"(?P<high>{num_token})\s*(?P<uh>{unit_token})?\s*(?:元)?\s*/\s*月",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        ul = m.group("ul") or m.group("uh") or ""
        uh = m.group("uh") or m.group("ul") or ""
        low_month = _to_yuan(m.group("low"), ul)
        high_month = _to_yuan(m.group("high"), uh)
        low_daily = min(low_month, high_month) / _WORKING_DAYS_PER_MONTH
        high_daily = max(low_month, high_month) / _WORKING_DAYS_PER_MONTH
        return low_daily, high_daily, "monthly"

    # 4) 月薪单值（显式 /月）：3500/月、3.5k/月
    m = re.search(
        rf"(?P<v>{num_token})\s*(?P<u>{unit_token})?\s*(?:元)?\s*/\s*月",
        s,
        flags=re.IGNORECASE,
    )
    if m:
        month = _to_yuan(m.group("v"), m.group("u"))
        daily = month / _WORKING_DAYS_PER_MONTH
        return daily, daily, "monthly"

    # 5) 月薪区间（隐式 K/万）：3.5-4.5K、1.2-1.8万
    m = re.search(
        rf"(?P<low>{num_token})\s*(?P<ul>{unit_token})?\s*-\s*"
        rf"(?P<high>{num_token})\s*(?P<uh>{unit_token})",
        s,
    )
    if m:
        ul = m.group("ul") or m.group("uh")
        uh = m.group("uh") or m.group("ul")
        low_month = _to_yuan(m.group("low"), ul)
        high_month = _to_yuan(m.group("high"), uh)
        low_daily = min(low_month, high_month) / _WORKING_DAYS_PER_MONTH
        high_daily = max(low_month, high_month) / _WORKING_DAYS_PER_MONTH
        return low_daily, high_daily, "monthly"

    # 6) 月薪单值（隐式 K/万）：3.5K、1.2万
    m = re.search(rf"(?P<v>{num_token})\s*(?P<u>{unit_token})", s)
    if m and m.group("u"):
        month = _to_yuan(m.group("v"), m.group("u"))
        daily = month / _WORKING_DAYS_PER_MONTH
        return daily, daily, "monthly"

    return None


def _extract_detail_salary_info(detail_page: Any) -> dict[str, Any] | None:
    """从详情页提取薪资并折算为日薪区间。"""
    selectors = [
        ".salary",
        ".job-banner .salary",
        ".info-primary .salary",
        "[class*='salary']",
        ".job-detail .salary",
    ]
    candidates: list[tuple[str, str]] = []
    for sel in selectors:
        try:
            loc = detail_page.locator(sel).first
            if loc.count() == 0:
                continue
            text = loc.inner_text().strip()
            if text:
                candidates.append((f"selector:{sel}", text))
        except Exception:
            continue

    try:
        salary_text = detail_page.evaluate(r"""() => {
            const el = document.querySelector('.salary, [class*="salary"]');
            return el ? el.innerText.trim() : '';
        }""")
        if salary_text:
            candidates.append(("js_fallback", salary_text))
    except Exception:
        pass

    seen: set[str] = set()
    unique_candidates: list[tuple[str, str]] = []
    for source, raw in candidates:
        if raw in seen:
            continue
        seen.add(raw)
        unique_candidates.append((source, raw))

    if not unique_candidates:
        return None

    for source, raw in unique_candidates:
        parsed = _parse_salary_to_daily_range(raw)
        if not parsed:
            continue
        daily_min, daily_max, basis = parsed
        return {
            "raw": raw,
            "source": source,
            "basis": basis,
            "daily_min": daily_min,
            "daily_max": daily_max,
        }

    # 找到了薪资文本，但无法解析成数值
    source, raw = unique_candidates[0]
    return {
        "raw": raw,
        "source": source,
        "basis": "unknown",
        "daily_min": None,
        "daily_max": None,
    }


def _extract_detail_salary(detail_page: Any) -> int | None:
    """兼容旧逻辑：返回折算后日薪上限（向下取整）。"""
    info = _extract_detail_salary_info(detail_page)
    if not info:
        return None
    daily_max = info.get("daily_max")
    if daily_max is None:
        return None
    return int(daily_max)


def _min_daily_salary() -> int:
    """日薪下限（元/天）。优先读 .env，fallback 读 Skill 配置。"""
    env_val = os.getenv("BOSS_MIN_DAILY_SALARY", "").strip()
    if env_val.isdigit():
        return int(env_val)
    from .skill_loader import get_parameter
    skill_val = get_parameter("min_daily_salary", "0").strip()
    return int(skill_val) if skill_val.isdigit() else 0


def scan_boss_jobs(
    keyword: str,
    max_items: int = 10,
    max_pages: int = 1,
) -> tuple[list[BossScanItem], str | None, int]:
    from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

    emit(EventType.WORKFLOW_START, f"boss_scan: keyword={keyword}, max_items={max_items}, max_pages={max_pages}")

    screenshot_dir = _screenshot_dir()
    screenshot_dir.mkdir(parents=True, exist_ok=True)

    screenshot_path: str | None = None
    pages_scanned = 0

    context = _get_browser_context()
    page = _get_page(context)

    delay_min, delay_max = _action_delay_ms()
    deduped: dict[str, BossScanItem] = {}
    safe_max_pages = max(1, min(max_pages, 5))
    for page_idx in range(1, safe_max_pages + 1):
        url = _boss_search_url(keyword, page=page_idx)
        emit(EventType.BROWSER_NAVIGATE, f"搜索第{page_idx}页", url=url)

        if page_idx == 1:
            authed = _navigate_and_check_auth(page, url, operation="岗位扫描")
            if not authed:
                _handle_cookie_expired(page, "岗位扫描")
                return [], None, 0
        else:
            page.goto(url, wait_until="domcontentloaded")

        pages_scanned = page_idx

        try:
            page.wait_for_selector(".job-card-wrapper, .job-card-box", timeout=12000)
        except PlaywrightTimeoutError:
            emit(EventType.WARNING, f"第{page_idx}页未找到职位卡片选择器")
        page.wait_for_timeout(1500)
        if delay_max > 0:
            page.wait_for_timeout(random.randint(delay_min, delay_max))

        page_items: list[BossScanItem] = []
        for _ in range(3):
            try:
                page_items = _extract_cards(page, max_items=max_items)
                break
            except Exception:
                page.wait_for_timeout(1200)

        emit(EventType.BROWSER_EXTRACT, f"第{page_idx}页提取到 {len(page_items)} 个职位卡片")

        for item in page_items:
            key = _item_dedupe_key(item)
            if key not in deduped:
                deduped[key] = item
            if len(deduped) >= max_items:
                break
        if len(deduped) >= max_items:
            break

    items = list(deduped.values())[:max_items]

    if _fetch_detail_enabled() and items:
        emit(EventType.INFO, f"开始抓取 {len(items)} 个职位详情页")
        for idx, item in enumerate(items):
            if not item.source_url:
                continue
            detail_page = context.new_page()
            detail_page.set_default_timeout(20000)
            try:
                emit(EventType.BROWSER_NAVIGATE, f"详情页 {idx+1}/{len(items)}: {item.title}", url=item.source_url)
                detail_page.goto(item.source_url, wait_until="domcontentloaded")
                if delay_max > 0:
                    detail_page.wait_for_timeout(random.randint(delay_min, delay_max))
                detail_text = _extract_detail_text(detail_page)
                if detail_text:
                    items[idx] = item.model_copy(update={"snippet": detail_text[:1600]})
            except Exception:
                pass
            finally:
                try:
                    detail_page.close()
                except Exception:
                    pass

    shot_name = f"boss_scan_{now_beijing().strftime('%Y%m%d_%H%M%S')}.png"
    shot_file = screenshot_dir / shot_name
    try:
        page.screenshot(path=str(shot_file), full_page=True)
        screenshot_path = str(shot_file)
        emit(EventType.BROWSER_SCREENSHOT, "截图已保存", path=str(shot_file))
    except Exception:
        screenshot_path = None

    emit(EventType.WORKFLOW_END, f"boss_scan 完成: {len(items)} 个结果, {pages_scanned} 页")
    return items, screenshot_path, pages_scanned


def _extract_chat_items(page: Any, max_conversations: int) -> list[BossChatConversationItem]:
    rows = page.evaluate(
        """
        (maxConversations) => {
          const selectors = [
            'li[role="listitem"]',
            ".user-list li",
            ".chat-conversation-item",
            ".chat-item",
            ".geek-item",
            ".chat-list li",
            ".left-list li"
          ];
          let nodes = [];
          for (const selector of selectors) {
            const found = Array.from(document.querySelectorAll(selector));
            if (found.length > 0) {
              nodes = found;
              break;
            }
          }
          return nodes.slice(0, maxConversations).map((node, index) => {
            const friendContent = node.querySelector(".friend-content");
            const nameBox = node.querySelector(".name-box");
            const msgNode =
              node.querySelector(".last-msg") ||
              node.querySelector(".message") ||
              node.querySelector(".msg");
            const timeNode =
              node.querySelector(".time") ||
              node.querySelector(".date");
            const unreadNode =
              node.querySelector("[class*=unread]") ||
              node.querySelector(".badge");

            // .name-box contains "黄女士快商通招聘经理" — try to split
            // into hr_name and company+title via sub-elements
            let hrName = "";
            let company = null;
            let jobTitle = null;
            if (nameBox) {
              const nameEl = nameBox.querySelector(".name");
              const compEl = nameBox.querySelector(".company, .sub-title, .desc");
              if (nameEl) {
                hrName = (nameEl.innerText || "").trim();
                company = compEl ? (compEl.innerText || "").trim() : null;
              } else {
                hrName = (nameBox.innerText || "").replace(/\\s+/g, " ").trim();
              }
            }

            const nodeText = (node.innerText || "").replace(/\\s+/g, " ").trim();
            const unreadText = (unreadNode?.innerText || "").replace(/\\D/g, "");
            const unread = unreadText ? Number.parseInt(unreadText, 10) : 0;

            // BOSS 的 d-c 属性是用户自己的 ID（所有会话共享），不能用作 conversation_id。
            // 使用 hr_name 作为唯一标识符（每个会话的 HR 不同），辅以 index 去重。
            const rawName = hrName || (node.innerText || "").trim().substring(0, 40);
            const convId = rawName
              ? `hr_${rawName}`
              : `conv_${index + 1}`;

            return {
              conversation_id: String(convId),
              hr_name: hrName,
              company: company || null,
              job_title: jobTitle,
              unread_count: Number.isFinite(unread) ? unread : 0,
              latest_message: (msgNode?.innerText || "").trim() || null,
              latest_time: (timeNode?.innerText || "").trim() || null,
              preview: nodeText.slice(0, 400) || null,
            };
          });
        }
        """,
        max_conversations,
    )
    items: list[BossChatConversationItem] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        conversation_id = str(row.get("conversation_id") or "").strip()
        hr_name = str(row.get("hr_name") or "").strip()
        if not conversation_id:
            continue
        if not hr_name:
            hr_name = "Unknown HR"
        unread_raw = row.get("unread_count")
        try:
            unread_count = int(unread_raw)
        except Exception:
            unread_count = 0
        items.append(
            BossChatConversationItem(
                conversation_id=conversation_id,
                hr_name=hr_name,
                company=str(row.get("company") or "").strip() or None,
                job_title=str(row.get("job_title") or "").strip() or None,
                unread_count=max(0, unread_count),
                latest_message=str(row.get("latest_message") or "").strip() or None,
                latest_time=str(row.get("latest_time") or "").strip() or None,
                preview=str(row.get("preview") or "").strip() or None,
            )
        )
    return items


def _extract_chat_items_with_retry(page: Any, max_conversations: int) -> list[BossChatConversationItem]:
    """Mitigate transient navigation races when chat DOM is re-rendering."""
    last_exc: Exception | None = None
    for _ in range(3):
        try:
            return _extract_chat_items(page, max_conversations)
        except Exception as exc:  # pragma: no cover - defensive runtime retry
            last_exc = exc
            err = str(exc)
            if "Execution context was destroyed" not in err and "Cannot find context with specified id" not in err:
                raise
            try:
                page.wait_for_load_state("domcontentloaded", timeout=8000)
            except Exception:
                pass
            try:
                page.wait_for_selector('li[role="listitem"], .user-list li, .chat-list li', timeout=8000)
            except Exception:
                pass
            page.wait_for_timeout(1000)
    if last_exc:
        raise last_exc
    return []


def _extract_conversation_messages(page: Any) -> dict[str, Any]:
    """提取当前聊天窗口的完整消息列表。

    返回结构：
    {
      "messages": [{"role": "hr"|"self"|"unknown", "text": ..., "time": ...}, ...],
      "has_candidate_messages": bool,
      "latest_hr_message": str|None,
      "latest_hr_time": str|None,
      "pending_hr_texts": [str, ...],  # 最后一条 self 消息之后的所有 HR 消息
    }
    """
    data = page.evaluate(
        """
        () => {
          // BOSS 直聘对话面板结构:
          //   .chat-record .chat-message .im-list > li.message-item
          //   角色区分: li.item-myself = 自己, li.item-friend = HR
          //   消息文本: .message-content .text span
          //   时间: .item-time .time
          //   消息ID: data-mid 属性
          const msgNodes = Array.from(
            document.querySelectorAll('.im-list > li.message-item')
          );

          const messages = [];
          let hasCandidateMessages = false;
          for (const node of msgNodes) {
            const className = (node.className || '').toLowerCase();
            const isSelf = className.includes('item-myself');
            const isHr = className.includes('item-friend');
            let role = 'unknown';
            if (isSelf) { role = 'self'; hasCandidateMessages = true; }
            else if (isHr) { role = 'hr'; }

            // 提取纯文本（跳过状态标签如 "已读"/"送达"）
            const textEl = node.querySelector('.message-content .text');
            const cardEl = node.querySelector('.message-content .message-card');
            let text = '';
            if (textEl) {
              const spans = textEl.querySelectorAll('p span');
              text = Array.from(spans).map(s => (s.innerText || '').trim()).join(' ').trim();
              if (!text) text = (textEl.innerText || '').replace(/^\\s*(已读|送达|未读)\\s*/g, '').trim();
            } else if (cardEl) {
              text = '[卡片] ' + (cardEl.innerText || '').replace(/\\s+/g, ' ').trim().substring(0, 200);
            } else {
              text = (node.querySelector('.message-content')?.innerText || '').replace(/\\s+/g, ' ').trim();
            }
            if (!text) continue;

            const timeEl = node.querySelector('.item-time .time');
            const timeText = (timeEl?.innerText || '').trim() || null;
            const mid = node.getAttribute('data-mid') || null;

            messages.push({ role, text, time: timeText, mid });
          }

          let latestHrMessage = null;
          let latestHrTime = null;
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'hr') {
              latestHrMessage = messages[i].text;
              latestHrTime = messages[i].time;
              break;
            }
          }

          let lastSelfIdx = -1;
          for (let i = messages.length - 1; i >= 0; i--) {
            if (messages[i].role === 'self') { lastSelfIdx = i; break; }
          }
          const pendingHrTexts = [];
          for (let i = lastSelfIdx + 1; i < messages.length; i++) {
            if (messages[i].role === 'hr') {
              pendingHrTexts.push(messages[i].text);
            }
          }

          return {
            messages,
            has_candidate_messages: hasCandidateMessages,
            latest_hr_message: latestHrMessage,
            latest_hr_time: latestHrTime,
            pending_hr_texts: pendingHrTexts,
          };
        }
        """
    )
    if not isinstance(data, dict):
        return {
            "messages": [],
            "has_candidate_messages": False,
            "latest_hr_message": None,
            "latest_hr_time": None,
            "pending_hr_texts": [],
        }
    return data


_VIEW_JOB_SELECTORS = [
    "text=查看职位",
    ':has-text("查看职位")',
    "a[ka='job-detail']",
    "a[href*='job_detail']",
]


def _extract_jd_from_conversation(
    context: Any,
    page: Any,
    *,
    delay_min: int,
    delay_max: int,
) -> tuple[str | None, str | None]:
    """在已打开的对话面板中，点击「查看职位」打开新标签页提取完整 JD。

    Returns (jd_text, source_url). Both None if no job link found.
    """
    view_job_link = None
    job_href: str | None = None
    for sel in _VIEW_JOB_SELECTORS:
        try:
            loc = page.locator(sel).first
            if loc.count() > 0:
                view_job_link = loc
                try:
                    job_href = loc.get_attribute("href", timeout=2000)
                except Exception:
                    pass
                if not job_href:
                    try:
                        parent_a = loc.locator("xpath=ancestor::a[1]")
                        if parent_a.count() > 0:
                            job_href = parent_a.get_attribute("href", timeout=2000)
                    except Exception:
                        pass
                tag = loc.evaluate("e => e.tagName") if loc.count() > 0 else "?"
                print(f"[JD_EXTRACT] Found via: {sel}, tag={tag}, href={job_href}")
                break
        except Exception:
            continue

    if not view_job_link:
        print("[JD_EXTRACT] No 查看职位 link found with any selector")
        return None, None

    if job_href and not job_href.startswith("http"):
        job_href = f"https://www.zhipin.com{job_href}" if job_href.startswith("/") else None

    new_page = None
    try:
        with context.expect_page(timeout=12000) as new_page_info:
            view_job_link.click(timeout=8000)
        new_page = new_page_info.value
        new_page.wait_for_load_state("domcontentloaded")
        if delay_max > 0:
            new_page.wait_for_timeout(random.randint(delay_min, delay_max))

        if not job_href:
            job_href = new_page.url

        jd_text = _extract_detail_text(new_page)

        if jd_text:
            emit(EventType.BROWSER_EXTRACT, f"从对话提取到完整JD ({len(jd_text)}字)")
            return jd_text[:3000], job_href
        return None, job_href

    except Exception as exc:
        logger.debug("JD extraction from conversation failed: %s", exc)
        return None, job_href
    finally:
        if new_page is not None:
            try:
                new_page.close()
            except Exception:
                pass


def _enrich_latest_hr_messages(
    context: Any,
    page: Any,
    items: list[BossChatConversationItem],
    *,
    max_conversations: int,
    fetch_jd: bool = False,
) -> list[BossChatConversationItem]:
    if not items:
        return items

    root_selector, root_count = _find_root_selector(page)
    print(f"[ENRICH] root_selector={root_selector}, count={root_count}, page_url={page.url}")
    if not root_selector:
        print("[ENRICH] No root selector found, skipping enrichment")
        return items

    safe_max = max(1, min(max_conversations, len(items)))
    max_click = min(root_count, safe_max)
    delay_min, delay_max = _action_delay_ms()
    enriched = list(items)
    for idx in range(max_click):
        try:
            item = enriched[idx]
            cid = str(item.conversation_id or "").strip()
            if cid:
                clicked = _click_conversation_by_id(page, cid, delay_min, delay_max)
                if not clicked:
                    # 安全优先：ID 定位失败时不再回退索引，避免读取到错误会话上下文。
                    emit(EventType.WARNING, f"会话{cid} ID定位失败，跳过该会话增强，避免上下文串线")
                    continue
            else:
                # 无会话 ID 无法做安全定位，直接跳过，避免误读其他会话数据。
                emit(EventType.WARNING, f"会话索引{idx}缺少conversation_id，跳过增强")
                continue

            conv_data = _extract_conversation_messages(page)
            updates: dict[str, Any] = {}
            latest_text = str(conv_data.get("latest_hr_message") or "").strip() or None
            latest_time = str(conv_data.get("latest_hr_time") or "").strip() or None
            if latest_text:
                updates["latest_hr_message"] = latest_text[:1600]
                updates["latest_hr_time"] = latest_time
            has_candidate = bool(conv_data.get("has_candidate_messages"))
            updates["has_candidate_messages"] = has_candidate
            raw_msgs = conv_data.get("messages") or []
            updates["conversation_messages"] = raw_msgs[-50:]
            pending = conv_data.get("pending_hr_texts") or []
            if pending:
                updates["pending_hr_texts"] = pending
            emit(EventType.INFO, f"  HR消息: {bool(latest_text)}, 对话条数: {len(raw_msgs)}, 候选人消息: {has_candidate}, 待回复: {len(pending)}")

            if fetch_jd:
                # Debug: search ALL elements (not just <a>) for 查看职位
                try:
                    vj_count = page.locator("text=查看职位").count()
                    print(f"[ENRICH_DEBUG] 'text=查看职位' matches: {vj_count}")
                    if vj_count > 0:
                        el = page.locator("text=查看职位").first
                        tag = el.evaluate("e => e.tagName")
                        cls = el.evaluate("e => e.className?.substring(0,60)")
                        txt = el.inner_text()[:60]
                        print(f"[ENRICH_DEBUG]   tag={tag}, class={cls}, text={txt}")
                except Exception as e:
                    print(f"[ENRICH_DEBUG] failed: {e}")

                jd_text, source_url = _extract_jd_from_conversation(
                    context, page, delay_min=delay_min, delay_max=delay_max,
                )
                emit(EventType.INFO, f"  JD结果: text={bool(jd_text)}, url={source_url}")
                if jd_text:
                    updates["jd_text"] = jd_text
                if source_url:
                    updates["source_url"] = source_url

            if updates:
                enriched[idx] = enriched[idx].model_copy(update=updates)
        except Exception as exc:
            logger.warning("Enrichment failed for item %d: %s", idx, exc)
            emit(EventType.WARNING, f"会话{idx+1}数据增强失败: {str(exc)[:100]}")
            continue
    return enriched


def _fetch_jd_enabled() -> bool:
    return os.getenv("BOSS_CHAT_FETCH_JD", "true").strip().lower() in {"1", "true", "yes"}


def _click_chat_tab(page: Any, tab_name: str) -> bool:
    """点击 BOSS 聊天列表顶部的标签（全部/未读/新招呼）切换过滤视图。"""
    clicked = page.evaluate(
        """
        (tabName) => {
            const labels = document.querySelectorAll('.label-list li, .label-list span.label-name');
            for (const label of labels) {
                if ((label.innerText || '').trim().includes(tabName)) {
                    label.click();
                    return true;
                }
            }
            return false;
        }
        """,
        tab_name,
    )
    if clicked:
        page.wait_for_timeout(2000)
    return bool(clicked)


def pull_boss_chat_conversations(
    *,
    max_conversations: int = 20,
    unread_only: bool = False,
    fetch_latest_hr: bool = True,
    fetch_jd: bool | None = None,
    chat_tab: str = "全部",
) -> tuple[list[BossChatConversationItem], str | None]:
    """拉取 BOSS 聊天会话列表。

    chat_tab: BOSS 内置标签过滤 —— "全部" | "未读" | "新招呼"。
              使用 "未读" 做心跳巡检，"新招呼" 检测 HR 首次联系。
    """
    if fetch_jd is None:
        fetch_jd = _fetch_jd_enabled()

    emit(EventType.WORKFLOW_START, f"boss_chat_pull: tab={chat_tab}, max={max_conversations}, unread_only={unread_only}, fetch_jd={fetch_jd}")
    from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

    screenshot_dir = _screenshot_dir()
    screenshot_dir.mkdir(parents=True, exist_ok=True)
    screenshot_path: str | None = None
    safe_max = max(1, min(max_conversations, 100))

    context = _get_browser_context()
    page = _get_page(context)

    emit(EventType.BROWSER_NAVIGATE, "打开 BOSS 聊天列表页", url=_BOSS_CHAT_URL)
    authed = _navigate_and_check_auth(page, _BOSS_CHAT_URL, operation="聊天列表拉取")
    if not authed:
        _handle_cookie_expired(page, "聊天列表拉取")
        return [], None

    try:
        page.wait_for_selector('li[role="listitem"], .user-list li, .chat-list li', timeout=12000)
    except PlaywrightTimeoutError:
        emit(EventType.WARNING, "聊天列表选择器未找到")

    if chat_tab and chat_tab != "全部":
        if _click_chat_tab(page, chat_tab):
            emit(EventType.INFO, f"已切换到 [{chat_tab}] 标签")
        else:
            emit(EventType.WARNING, f"切换到 [{chat_tab}] 标签失败，使用默认全部视图")

    delay_min, delay_max = _action_delay_ms()
    if delay_max > 0:
        page.wait_for_timeout(random.randint(delay_min, delay_max))
    items = _extract_chat_items_with_retry(page, safe_max)
    emit(EventType.BROWSER_EXTRACT, f"[{chat_tab}] 提取到 {len(items)} 个会话")
    if fetch_latest_hr and items:
        emit(EventType.INFO, f"开始获取 {len(items)} 个会话的最新HR消息" + (" + JD" if fetch_jd else ""))
        items = _enrich_latest_hr_messages(
            context,
            page,
            items,
            max_conversations=safe_max,
            fetch_jd=fetch_jd,
        )
    if unread_only:
        items = [item for item in items if item.unread_count > 0]
        emit(EventType.INFO, f"过滤后剩余 {len(items)} 个未读会话")

    # 切回"全部"标签，避免影响后续操作
    if chat_tab and chat_tab != "全部":
        _click_chat_tab(page, "全部")

    shot_name = f"boss_chat_pull_{now_beijing().strftime('%Y%m%d_%H%M%S')}.png"
    shot_file = screenshot_dir / shot_name
    try:
        page.screenshot(path=str(shot_file), full_page=True)
        screenshot_path = str(shot_file)
        emit(EventType.BROWSER_SCREENSHOT, "聊天列表截图已保存", path=str(shot_file))
    except Exception:
        screenshot_path = None

    emit(EventType.WORKFLOW_END, f"boss_chat_pull 完成: {len(items[:safe_max])} 个会话")
    return items[:safe_max], screenshot_path


def _input_selectors() -> list[str]:
    raw = os.getenv("BOSS_CHAT_INPUT_SELECTORS", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return [
        ".input-area",
        "[contenteditable=true]",
        "textarea.chat-input",
        ".chat-input",
        "textarea",
        ".im-input",
    ]


def _send_selectors() -> list[str]:
    raw = os.getenv("BOSS_CHAT_SEND_SELECTORS", "").strip()
    if raw:
        return [s.strip() for s in raw.split(",") if s.strip()]
    return [
        ".send-message",
        ".send-btn",
        ".send-button",
        "[class*=send]",
        "button[type=submit]",
    ]


def _detect_and_click_resume_card(page: Any) -> tuple[str, str]:
    """检测聊天中 HR 索要简历的特殊卡片，并在可能时点击「同意」发送。

    BOSS 直聘在 HR 请求简历时，会在消息流中插入一个卡片，卡片上有「同意」按钮。
    点击后平台自动发送简历。若按钮已变灰/不可点击，说明简历已发送过。

    Returns (status, detail):
        ("clicked", "...") — 成功点击了同意按钮
        ("already_sent", "...") — 已发送过(按钮灰色/不可用)
        ("not_found", "...") — 没有找到简历请求卡片
    """
    try:
        result = page.evaluate("""
            () => {
                const chatArea = document.querySelector('.chat-conversation, .message-list, .chat-record, [class*="chat-msg"]') || document;
                const allMsgItems = chatArea.querySelectorAll(
                    '.item-myself, .item-other, .msg-item, .chat-msg-item, ' +
                    '[class*="msg-card"], [class*="resume"], .message-item'
                );

                for (const item of allMsgItems) {
                    const text = (item.textContent || '');
                    if (!(text.includes('简历') || text.includes('附件') || text.includes('resume')))
                        continue;

                    const btns = item.querySelectorAll('button, a, [role="button"], span[class*="btn"]');
                    for (const btn of btns) {
                        const btnText = (btn.textContent || '').replace(/\\s+/g, '').trim();
                        if (btnText !== '同意' && !btnText.includes('同意'))
                            continue;

                        const styles = getComputedStyle(btn);
                        const disabled = btn.disabled ||
                            btn.hasAttribute('disabled') ||
                            btn.classList.contains('disabled') ||
                            btn.classList.contains('btn-disabled') ||
                            btn.classList.contains('btn-has-done') ||
                            styles.pointerEvents === 'none' ||
                            parseFloat(styles.opacity) < 0.5;

                        if (disabled) {
                            return { status: 'already_sent', detail: '简历请求卡片已处理(按钮不可用)' };
                        }
                        btn.click();
                        return { status: 'clicked', detail: '已点击同意发送简历' };
                    }
                }
                return { status: 'not_found', detail: '未找到简历请求卡片' };
            }
        """)
    except Exception as exc:
        return "not_found", f"检测简历卡片异常: {str(exc)[:100]}"

    if not result or not isinstance(result, dict):
        return "not_found", "evaluate 返回空"

    status = result.get("status", "not_found")
    detail = result.get("detail", "")
    if status == "clicked":
        emit(EventType.BROWSER_CLICK, f"简历请求卡片: {detail}")
        page.wait_for_timeout(random.randint(800, 1500))
    elif status == "already_sent":
        emit(EventType.INFO, f"简历请求卡片: {detail}")
    return status, detail


def _try_send_resume(page: Any) -> tuple[bool, str | None]:
    """在已打开的聊天窗口中发送平台附件简历。

    优先检测 HR 索要简历的特殊卡片（「同意」按钮），若未找到则走工具栏「发简历」按钮。

    BOSS 直聘的两种简历发送路径：
      路径 A — HR 发起简历请求卡片 → 点击「同意」
      路径 B — 工具栏「发简历」按钮 → 选择简历 → 确认

    Returns (成功, 错误信息).
    """
    card_status, card_detail = _detect_and_click_resume_card(page)
    if card_status == "clicked":
        emit(EventType.REPLY_SENT, f"通过简历请求卡片发送简历: {card_detail}")
        return True, None
    if card_status == "already_sent":
        emit(EventType.INFO, f"简历已通过卡片发送过: {card_detail}")
        return True, None
    delay_min, delay_max = _action_delay_ms()
    try:
        resume_btn = None
        for sel in [".toolbar-btn", ".chat-tool-bar .toolbar-btn-content", ".toolbar-btn-content"]:
            try:
                candidates = page.locator(sel)
                for i in range(candidates.count()):
                    el = candidates.nth(i)
                    txt = (el.inner_text(timeout=2000) or "").strip()
                    if "发简历" in txt or "发送简历" in txt:
                        resume_btn = el
                        break
                if resume_btn:
                    break
            except Exception:
                continue

        if not resume_btn:
            emit(EventType.WARNING, "未找到「发简历」按钮")
            return False, "未找到「发简历」按钮"

        btn_classes = ""
        try:
            btn_classes = resume_btn.evaluate("e => e.className || ''") or ""
        except Exception:
            pass
        if "unable" in btn_classes.lower():
            emit(EventType.WARNING, "「发简历」按钮不可用（HR 未回复），跳过")
            return False, "HR 未回复，发简历按钮不可用"

        resume_btn.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, "点击「发简历」按钮")
        if delay_max > 0:
            page.wait_for_timeout(random.randint(800, 1500))

        resume_list = None
        for list_sel in ["ul.resume-list", ".resume-list", ".resume-panel ul"]:
            try:
                loc = page.locator(list_sel)
                if loc.count() > 0:
                    resume_list = loc.first
                    break
            except Exception:
                continue

        if not resume_list:
            page.wait_for_timeout(1500)
            for list_sel in ["ul.resume-list", ".resume-list", ".resume-panel ul"]:
                try:
                    loc = page.locator(list_sel)
                    if loc.count() > 0:
                        resume_list = loc.first
                        break
                except Exception:
                    continue

        if not resume_list:
            emit(EventType.WARNING, "简历列表未出现")
            return False, "简历列表未出现"

        resume_item = None
        for item_sel in ["li.list-item", "li.resume-item", "li"]:
            try:
                items = resume_list.locator(item_sel)
                if items.count() > 0:
                    resume_item = items.first
                    break
            except Exception:
                continue

        if not resume_item:
            emit(EventType.WARNING, "简历列表中没有可选简历")
            return False, "简历列表为空"

        resume_item.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, "选择第一份简历")
        if delay_max > 0:
            page.wait_for_timeout(random.randint(500, 1000))

        confirm_btn = None
        for confirm_sel in [
            "button.btn-sure-v2",
            "button.btn-confirm",
            "button.btn-v2.btn-sure-v2",
            "span.btn-sure-v2",
            ".resume-panel button",
        ]:
            try:
                loc = page.locator(confirm_sel)
                if loc.count() > 0:
                    el = loc.first
                    if el.is_enabled(timeout=2000):
                        confirm_btn = el
                        break
            except Exception:
                continue

        if not confirm_btn:
            emit(EventType.WARNING, "未找到简历发送确认按钮")
            return False, "未找到确认按钮"

        confirm_btn.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, "点击确认发送简历")
        if delay_max > 0:
            page.wait_for_timeout(random.randint(800, 2000))
        emit(EventType.REPLY_SENT, "平台附件简历已发送")
        return True, None
    except Exception as exc:
        err = str(exc)[:300]
        emit(EventType.ERROR, f"发送简历失败: {err}")
        logger.warning("Send resume failed: %s", err)
        return False, err


def _try_send_message(page: Any, text: str) -> tuple[bool, str | None]:
    """在已打开的聊天窗口中输入并发送文本。返回 (成功, 错误信息)。"""
    if not text or not text.strip():
        return False, "回复内容为空"
    text = text.strip()[:2000]
    emit(EventType.BROWSER_INPUT, f"准备输入消息: {text[:80]}...")
    input_selectors = _input_selectors()
    send_selectors = _send_selectors()
    delay_min, delay_max = _action_delay_ms()
    try:
        input_el = None
        for sel in input_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    input_el = loc.first
                    break
            except Exception:
                continue
        if not input_el:
            emit(EventType.WARNING, "未找到输入框")
            return False, "未找到输入框"
        input_el.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, "点击输入框")
        if delay_max > 0:
            page.wait_for_timeout(random.randint(500, min(delay_max, 1500)))
        input_el.fill("", timeout=3000)
        input_el.fill(text, timeout=5000)
        if delay_max > 0:
            page.wait_for_timeout(random.randint(delay_min, delay_max))
        send_el = None
        for sel in send_selectors:
            try:
                loc = page.locator(sel)
                if loc.count() > 0:
                    send_el = loc.first
                    break
            except Exception:
                continue
        if not send_el:
            emit(EventType.WARNING, "未找到发送按钮")
            return False, "未找到发送按钮"
        send_el.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, "点击发送按钮")
        if delay_max > 0:
            page.wait_for_timeout(random.randint(800, 2000))
        emit(EventType.REPLY_SENT, f"消息已发送: {text[:60]}...")
        return True, None
    except Exception as exc:
        err = str(exc)[:300]
        emit(EventType.ERROR, f"发送消息失败: {err}")
        logger.warning("Send message failed: %s", err)
        return False, err


def _click_conversation_by_id(page: Any, conversation_id: str, delay_min: int, delay_max: int) -> bool:
    """通过 HR 名称精确点击对话，避免索引漂移导致发送到错误会话。

    conversation_id 格式: "hr_<HR名称>" (由 _extract_chat_items 生成)。
    匹配策略: 遍历会话列表，找到 .name-box 文本与 HR 名称完全匹配的 li 并点击。
    """
    cid = conversation_id.strip()
    if not cid:
        return False
    hr_name = cid[3:] if cid.startswith("hr_") else cid
    if not hr_name:
        return False
    try:
        clicked = page.evaluate(
            """
            (targetName) => {
                const selectors = ['li[role="listitem"]', '.user-list li', '.chat-list li'];
                for (const selector of selectors) {
                    const items = document.querySelectorAll(selector);
                    for (const item of items) {
                        const nameBox = item.querySelector('.name-box');
                        if (!nameBox) continue;
                        const nameEl = nameBox.querySelector('.name');
                        const name = (nameEl ? nameEl.innerText : nameBox.innerText || '').replace(/\\s+/g, ' ').trim();
                        if (name === targetName) {
                            // 必须点击 .friend-content 才能触发 Vue 的对话切换，点击 li 无效
                            const fc = item.querySelector('.friend-content');
                            (fc || item).click();
                            return true;
                        }
                    }
                    if (items.length > 0) break;
                }
                return false;
            }
            """,
            hr_name,
        )
        if clicked:
            if delay_max > 0:
                page.wait_for_timeout(random.randint(delay_min, delay_max))
            return True
    except Exception:
        pass
    return False


def execute_boss_chat_replies(
    *,
    items_to_send: list[tuple[str, str]],
    resume_conversation_ids: list[str] | None = None,
    max_conversations: int = 30,
) -> list[tuple[str, bool, str | None]]:
    """对指定会话实际发送回复和/或简历。

    使用 conversation_id 属性精确定位会话（而非列表索引），
    确保发送消息后列表重排不会导致后续消息发到错误对话。
    """
    if not items_to_send and not resume_conversation_ids:
        return []
    from patchright.sync_api import TimeoutError as PlaywrightTimeoutError

    resume_cids = set(resume_conversation_ids or [])
    all_cids_ordered: list[str] = []
    for cid, _ in items_to_send:
        if cid not in all_cids_ordered:
            all_cids_ordered.append(cid)
    for cid in resume_cids:
        if cid not in all_cids_ordered:
            all_cids_ordered.append(cid)

    results: list[tuple[str, bool, str | None]] = []

    context = _get_browser_context()
    page = _get_page(context)

    authed = _navigate_and_check_auth(page, _BOSS_CHAT_URL, operation="发送聊天回复")
    if not authed:
        _handle_cookie_expired(page, "发送聊天回复")
        return [(cid, False, "Cookie 过期，需重新登录") for cid in all_cids_ordered]

    try:
        page.wait_for_selector('li[role="listitem"], .user-list li, .chat-list li', timeout=12000)
    except PlaywrightTimeoutError:
        pass
    delay_min, delay_max = _action_delay_ms()
    if delay_max > 0:
        page.wait_for_timeout(random.randint(delay_min, delay_max))

    text_map: dict[str, str] = {cid: txt for cid, txt in items_to_send}

    for conversation_id in all_cids_ordered:
        try:
            clicked = _click_conversation_by_id(page, conversation_id, delay_min, delay_max)
            if not clicked:
                results.append((conversation_id, False, "无法通过 ID 定位会话，跳过以避免误发"))
                emit(EventType.WARNING, f"会话 {conversation_id} 无法通过 ID 精确定位")
                continue

            reply_text = text_map.get(conversation_id, "").strip()
            if reply_text:
                ok, err = _try_send_message(page, reply_text)
                if not ok:
                    results.append((conversation_id, False, err))
                    continue
                if delay_max > 0:
                    page.wait_for_timeout(random.randint(delay_min, delay_max))

            if conversation_id in resume_cids:
                ok_r, err_r = _try_send_resume(page)
                if not ok_r:
                    emit(EventType.WARNING, f"简历发送失败: {err_r}")
                    results.append((conversation_id, False, f"简历发送失败: {err_r}"))
                    continue
            else:
                card_st, card_dt = _detect_and_click_resume_card(page)
                if card_st == "clicked":
                    emit(EventType.REPLY_SENT, f"会话 {conversation_id} 检测到未处理的简历请求卡片，已自动同意: {card_dt}")

            results.append((conversation_id, True, None))
        except Exception as exc:
            results.append((conversation_id, False, str(exc)[:200]))
    cleanup_browser_tabs()
    return results


# ────────────────────────────────────────────────────────────
# 主动打招呼（涓流式）
# ────────────────────────────────────────────────────────────

def _greet_today_count() -> int:
    """从 DB 统计今日打招呼成功次数（持久化，重启安全）。"""
    try:
        import psycopg
        from .storage import _database_url
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT COUNT(*) FROM actions "
                    "WHERE action_type = 'boss_greet' AND status = 'success' "
                    "AND created_at >= CURRENT_DATE"
                )
                row = cur.fetchone()
        return int(row[0]) if row else 0
    except Exception:
        return 0


def _dismiss_greet_modal(page: Any) -> None:
    """关闭点击「立即沟通」后弹出的「已向BOSS发送消息」弹窗。

    点「留在此页」或关闭弹窗，避免自动跳转到聊天页。
    """
    dismissed = page.evaluate("""
        () => {
            // 只点"留在此页"，绝不点"继续沟通"（会跳转离开搜索页）
            const safeKeywords = ['留在此页', '关闭', '取消'];
            const allClickable = [...document.querySelectorAll(
                'button, a, .btn, [role="button"], .dialog-wrap a, .dialog-container a, ' +
                '[class*="dialog"] button, [class*="dialog"] a'
            )];
            for (const kw of safeKeywords) {
                for (const el of allClickable) {
                    const text = (el.textContent || '').replace(/\\s+/g, '').trim();
                    if (text === kw || text.includes(kw)) {
                        el.click();
                        return 'clicked:' + kw;
                    }
                }
            }
            // 找弹窗关闭 X 按钮
            const closeSelectors = [
                '.dialog-wrap .close', '.dialog-container .close',
                '[class*="dialog"] .close', '[class*="dialog"] [class*="close"]',
                '[class*="dialog"] .icon-close', '.boss-popup .close',
                '.greet-dialog .close'
            ];
            for (const sel of closeSelectors) {
                const el = document.querySelector(sel);
                if (el) { el.click(); return 'clicked:close-x'; }
            }
            return null;
        }
    """)
    if dismissed:
        emit(EventType.BROWSER_CLICK, f"关闭打招呼弹窗: {dismissed}")
        page.wait_for_timeout(random.randint(500, 1000))
        return

    try:
        page.keyboard.press("Escape")
        emit(EventType.BROWSER_CLICK, "关闭弹窗: Escape 键")
        page.wait_for_timeout(random.randint(500, 1000))
    except Exception:
        pass


def _ensure_on_search_page(page: Any, search_url: str) -> bool:
    """确认当前仍在搜索结果页，若被弹窗劫持跳转则重新导航回来。

    Returns True 表示已恢复到搜索页且卡片可见。
    """
    try:
        current = page.url or ""
        cards = page.locator(".job-card-wrapper, .job-card-box")
        if cards.count() > 0:
            return True
        emit(EventType.WARNING, f"页面偏离搜索结果({current[:80]})，重新导航")
        page.goto(search_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_selector(".job-card-wrapper, .job-card-box", timeout=12000)
        page.wait_for_timeout(1500)
        return page.locator(".job-card-wrapper, .job-card-box").count() > 0
    except Exception as exc:
        emit(EventType.WARNING, f"重新导航搜索页失败: {str(exc)[:100]}")
        return False


def _click_greet_on_detail_page(page: Any, job_url: str) -> tuple[bool, str | None]:
    """直接导航到岗位详情页，在详情页点击「立即沟通」。

    这是根本方案：不依赖搜索结果页的卡片索引（BOSS 每次刷新排序会变），
    而是用岗位唯一 URL 直接导航到详情页点击按钮，确保打招呼的一定是匹配的岗位。
    """
    try:
        page.goto(job_url, wait_until="domcontentloaded", timeout=15000)
        page.wait_for_timeout(random.randint(1500, 2500))

        if _check_login_required(page):
            return False, "cookie_expired"

        greet_btn = None
        for sel in [
            ".btn-startchat",
            ".start-chat-btn",
            "a.btn-startchat",
            ".job-op .btn",
            "button:has-text('立即沟通')",
            "a:has-text('立即沟通')",
            ":has-text('立即沟通')",
        ]:
            try:
                loc = page.locator(sel)
                if loc.count() > 0 and loc.first.is_visible():
                    greet_btn = loc.first
                    break
            except Exception:
                continue

        if not greet_btn:
            already_chatted = False
            for sel in [
                "button:has-text('继续沟通')",
                "a:has-text('继续沟通')",
                ":has-text('继续沟通')",
            ]:
                try:
                    loc = page.locator(sel)
                    if loc.count() > 0 and loc.first.is_visible():
                        already_chatted = True
                        break
                except Exception:
                    continue
            if already_chatted:
                return False, "已沟通过，跳过"
            return False, "未找到「立即沟通」按钮"

        greet_btn.click(timeout=5000)
        emit(EventType.BROWSER_CLICK, f"点击「立即沟通」按钮 (详情页)")
        page.wait_for_timeout(random.randint(2000, 3500))

        _dismiss_greet_modal(page)

        return True, None
    except Exception as exc:
        return False, str(exc)[:200]


def greet_matching_jobs(
    *,
    keyword: str,
    batch_size: int | None = None,
    match_threshold: float | None = None,
    greeting_text: str | None = None,
    job_type: str = "all",
    run_id: str | None = None,
) -> dict[str, Any]:
    """涓流式主动打招呼：搜索岗位 → JD匹配 → 对匹配的岗位点击「立即沟通」。

    **核心保障：必须打满 batch_size 个招呼。**
    搜索一页不够就翻页，翻页不够就换关键词，直到打满或穷尽所有搜索组合。
    """
    _glog = _ensure_guard_debug_logger()
    if not run_id:
        run_id = f"manual-{now_beijing().strftime('%Y%m%d-%H%M%S')}"

    from .workflow import run_greet_decision
    from .storage import log_action

    if batch_size is None:
        batch_size = _greet_batch_size()
    if match_threshold is None:
        match_threshold = _greet_match_threshold()

    daily_limit = _greet_daily_limit()
    already_today = _greet_today_count()
    if already_today >= daily_limit:
        emit(EventType.WARNING, f"今日打招呼已达上限 {already_today}/{daily_limit}，跳过")
        return {
            "greeted": 0, "skipped": 0, "failed": 0,
            "daily_count": already_today, "daily_limit": daily_limit,
            "reason": "daily_limit_reached",
        }

    remaining = daily_limit - already_today
    effective_batch = min(batch_size, remaining)

    emit(
        EventType.WORKFLOW_START,
        f"greet_matching_jobs: run_id={run_id}, keyword={keyword}, batch={effective_batch}, job_type={job_type}",
    )
    _glog.info(
        "[GREET][%s] === START === keyword=%r batch=%d job_type=%s daily=%d/%d min_daily_salary=%d",
        run_id, keyword, effective_batch, job_type, already_today, daily_limit, _min_daily_salary(),
    )

    MAX_SEARCH_ROUNDS = 5
    MAX_PAGES_PER_KEYWORD = 3

    fallback_keywords = _build_keyword_list(keyword, job_type)
    _glog.info("[GREET] 搜索关键词列表: %s", fallback_keywords)

    seen_urls: set[str] = set()
    greeted = 0
    failed = 0
    llm_rejected = 0
    detail_fail = 0
    greet_results: list[tuple[BossScanItem, bool, str]] = []

    context = _get_browser_context()
    page = _get_page(context)
    greet_delay_min, greet_delay_max = _greet_delay_ms()
    min_salary = _min_daily_salary()

    round_num = 0

    for kw_idx, current_kw in enumerate(fallback_keywords):
        if greeted >= effective_batch:
            break

        for page_num in range(1, MAX_PAGES_PER_KEYWORD + 1):
            if greeted >= effective_batch:
                break
            round_num += 1
            if round_num > MAX_SEARCH_ROUNDS:
                _glog.info("[GREET] 达到最大搜索轮数 %d，停止", MAX_SEARCH_ROUNDS)
                break

            _glog.info("[GREET] 第 %d 轮: keyword=%r page=%d (greeted=%d/%d)",
                       round_num, current_kw, page_num, greeted, effective_batch)
            emit(EventType.INFO, f"搜索第 {round_num} 轮: keyword={current_kw} page={page_num} (已打{greeted}/{effective_batch})")

            scan_items: list[BossScanItem] = []

            if page_num == 1:
                scan_items, _, _ = scan_boss_jobs(current_kw, max_items=15, max_pages=1)
            else:
                url = _boss_search_url(current_kw, page=page_num)
                try:
                    page.goto(url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(1500)
                    scan_items = _extract_cards(page, max_items=15)
                except Exception as exc:
                    _glog.warning("[GREET] 翻页失败 page=%d: %s", page_num, exc)
                    break

            if not scan_items:
                _glog.info("[GREET] keyword=%r page=%d 搜索无结果", current_kw, page_num)
                break

            new_items = [item for item in scan_items if item.source_url and item.source_url not in seen_urls]
            for item in new_items:
                if item.source_url:
                    seen_urls.add(item.source_url)

            if not new_items:
                _glog.info("[GREET] keyword=%r page=%d 全部已见过，换页/换词", current_kw, page_num)
                continue

            if job_type != "all":
                before = len(new_items)
                new_items = [item for item in new_items
                             if _salary_matches_job_type(item.salary, job_type, title=item.title, snippet=item.snippet or "")]
                if before - len(new_items) > 0:
                    _glog.info("[GREET] job_type过滤: %d → %d", before, len(new_items))

            if _need_agent_direction_guard(current_kw):
                before = len(new_items)
                passed_dir: list[BossScanItem] = []
                for item in new_items:
                    ok, reason = _agent_direction_matches(item.title, item.snippet or "")
                    if ok:
                        passed_dir.append(item)
                    else:
                        emit(EventType.WARNING, f"方向过滤排除: {item.title}@{item.company} reason={reason}")
                new_items = passed_dir
                if before - len(new_items) > 0:
                    _glog.info("[GREET] 方向门控过滤: %d → %d", before, len(new_items))
                    emit(EventType.INFO, f"方向门控：{before} 个中排除 {before - len(new_items)} 个（剩余 {len(new_items)} 个）")

            if not new_items:
                _glog.info("[GREET] keyword=%r page=%d 过滤后无剩余", current_kw, page_num)
                continue

            for idx, item in enumerate(new_items):
                if greeted >= effective_batch:
                    break

                emit(EventType.INFO, f"[{idx+1}/{len(new_items)}] 导航到详情页: {item.title}@{item.company}")
                _glog.info("[GREET] 检查: %s @ %s url=%s", item.title, item.company, (item.source_url or "")[:60])

                try:
                    page.goto(item.source_url, wait_until="domcontentloaded", timeout=15000)
                    page.wait_for_timeout(random.randint(1500, 2500))
                except Exception as exc:
                    emit(EventType.WARNING, f"导航失败: {item.title} → {exc}")
                    _glog.warning("[GREET] 导航失败: %s → %s", item.title, str(exc)[:100])
                    detail_fail += 1
                    continue

                if _check_login_required(page):
                    _handle_cookie_expired(page, "打招呼-详情页")
                    _glog.error("[GREET] Cookie过期，中断")
                    failed += len(new_items) - idx
                    break

                full_jd = _extract_detail_text(page)
                if not full_jd or len(full_jd.strip()) < 20:
                    emit(EventType.WARNING, f"详情页JD提取失败或内容过短: {item.title}")
                    full_jd = item.snippet or ""

                if min_salary > 0:
                    salary_info = _extract_detail_salary_info(page)
                    if salary_info is None:
                        emit(EventType.WARNING, f"薪资信息缺失: {item.title} | 未找到薪资文本，跳过薪资门槛")
                        _glog.warning("[GREET] 薪资信息缺失: %s", item.title)
                    else:
                        raw_salary = str(salary_info.get("raw") or "")
                        basis = str(salary_info.get("basis") or "unknown")
                        source = str(salary_info.get("source") or "-")
                        daily_min = salary_info.get("daily_min")
                        daily_max = salary_info.get("daily_max")

                        if daily_max is None:
                            emit(EventType.WARNING, f"薪资解析失败: {item.title} | raw={raw_salary}，跳过薪资门槛")
                            _glog.warning(
                                "[GREET] 薪资解析失败: %s | source=%s raw=%s",
                                item.title, source, raw_salary[:120],
                            )
                        else:
                            span = f"{_format_money(daily_min)}~{_format_money(daily_max)}"
                            emit(EventType.INFO, f"薪资解析: {item.title} | raw={raw_salary} | {basis}折算日薪={span}元/天")
                            _glog.info(
                                "[GREET] 薪资解析: %s | source=%s raw=%s basis=%s daily=%s",
                                item.title, source, raw_salary[:120], basis, span,
                            )

                            # 区间判定：只看上限。上限 >= 下限就允许（有谈判空间）。
                            if daily_max < min_salary:
                                reason = (
                                    f"薪资不达标: 折算日薪上限 {_format_money(daily_max)}元/天 "
                                    f"< {min_salary}元/天 (raw={raw_salary})"
                                )
                                greet_results.append((item, False, reason))
                                emit(EventType.WARNING, f"薪资不达标: {item.title} | {reason}")
                                _glog.info("[GREET] 薪资不达标: %s | %s", item.title, reason[:160])
                                continue
                            emit(
                                EventType.INFO,
                                f"薪资检查通过: {item.title} | 折算日薪上限 {_format_money(daily_max)}元/天 >= {min_salary}元/天",
                            )

                jd_context = f"岗位标题：{item.title}\n公司：{item.company}\n薪资：{item.salary or '未知'}\n\n职位描述：\n{full_jd}"

                emit(EventType.INFO, f"LLM二元判断中: {item.title} (JD长度={len(full_jd)}字)")
                decision = run_greet_decision(jd_context)

                if not decision.should_greet:
                    llm_rejected += 1
                    greet_results.append((item, False, f"LLM拒绝: {decision.reason}"))
                    emit(EventType.WARNING, f"LLM拒绝打招呼: {item.title}@{item.company} | reason={decision.reason}")
                    _glog.info("[GREET] LLM拒绝: %s | %s", item.title, decision.reason[:80])
                    continue

                emit(EventType.INFO, f"LLM通过: {item.title}@{item.company} | reason={decision.reason}")
                _glog.info("[GREET] LLM通过: %s | %s", item.title, decision.reason[:80])

                greet_btn = None
                for sel in [
                    ".btn-startchat",
                    ".start-chat-btn",
                    "a.btn-startchat",
                    ".job-op .btn",
                    "button:has-text('立即沟通')",
                    "a:has-text('立即沟通')",
                    ":has-text('立即沟通')",
                ]:
                    try:
                        loc = page.locator(sel)
                        if loc.count() > 0 and loc.first.is_visible():
                            greet_btn = loc.first
                            break
                    except Exception:
                        continue

                if not greet_btn:
                    already_chatted = False
                    for sel in [
                        "button:has-text('继续沟通')",
                        "a:has-text('继续沟通')",
                        ":has-text('继续沟通')",
                    ]:
                        try:
                            loc = page.locator(sel)
                            if loc.count() > 0 and loc.first.is_visible():
                                already_chatted = True
                                break
                        except Exception:
                            continue
                    if already_chatted:
                        greet_results.append((item, False, "已沟通过"))
                        emit(EventType.INFO, f"已沟通过，跳过: {item.title}@{item.company}")
                        _glog.info("[GREET] 已沟通过: %s", item.title)
                    else:
                        failed += 1
                        greet_results.append((item, False, "未找到「立即沟通」按钮"))
                        emit(EventType.WARNING, f"未找到沟通按钮: {item.title}@{item.company}")
                        _glog.warning("[GREET] 未找到按钮: %s", item.title)
                    continue

                try:
                    greet_btn.click(timeout=5000)
                    emit(EventType.BROWSER_CLICK, f"点击「立即沟通」: {item.title}@{item.company}")
                    page.wait_for_timeout(random.randint(2000, 3500))
                    _dismiss_greet_modal(page)

                    greeted += 1
                    greet_results.append((item, True, f"LLM: {decision.reason}"))
                    log_action(
                        job_id=None,
                        action_type="boss_greet",
                        input_summary=f"keyword={current_kw}; title={item.title}; company={item.company}; url={item.source_url}; llm_reason={decision.reason}",
                        output_summary=f"greeted=true; daily_count={_greet_today_count()}/{daily_limit}",
                        status="success",
                    )
                    emit(EventType.REPLY_SENT, f"打招呼成功: {item.title}@{item.company} (今日第{_greet_today_count()}个)")
                    _glog.info("[GREET] ✓ 成功打招呼 #%d: %s @ %s", greeted, item.title, item.company)

                except Exception as exc:
                    failed += 1
                    greet_results.append((item, False, str(exc)[:200]))
                    log_action(
                        job_id=None,
                        action_type="boss_greet",
                        input_summary=f"keyword={current_kw}; title={item.title}; company={item.company}; url={item.source_url}",
                        output_summary=f"greeted=false; error={exc}",
                        status="error",
                    )
                    emit(EventType.WARNING, f"打招呼失败: {item.title}@{item.company}: {exc}")
                    _glog.error("[GREET] 打招呼点击失败: %s → %s", item.title, str(exc)[:100])

                wait_ms = random.randint(greet_delay_min, greet_delay_max)
                emit(EventType.INFO, f"打招呼间隔等待 {wait_ms/1000:.1f}s...")
                page.wait_for_timeout(wait_ms)

        if round_num > MAX_SEARCH_ROUNDS:
            break

    skipped = len(seen_urls) - greeted - failed - llm_rejected - detail_fail
    if skipped < 0:
        skipped = 0
    cleanup_browser_tabs()

    reason = None
    if greeted >= effective_batch:
        reason = "batch_fulfilled"
    elif round_num >= MAX_SEARCH_ROUNDS:
        reason = "max_rounds_exhausted"
    else:
        reason = "all_keywords_exhausted"

    _glog.info(
        "[GREET][%s] === END === greeted=%d/%d failed=%d llm_rejected=%d detail_fail=%d reason=%s rounds=%d unique_jobs=%d",
        run_id, greeted, effective_batch, failed, llm_rejected, detail_fail, reason, round_num, len(seen_urls),
    )

    emit(EventType.WORKFLOW_END,
         f"greet_matching_jobs 完成: greeted={greeted}/{effective_batch}, llm_rejected={llm_rejected}, "
         f"detail_fail={detail_fail}, failed={failed}, reason={reason}, rounds={round_num}")
    return {
        "greeted": greeted,
        "failed": failed,
        "llm_rejected": llm_rejected,
        "detail_fail": detail_fail,
        "skipped": skipped,
        "daily_count": _greet_today_count(),
        "daily_limit": daily_limit,
        "reason": reason,
        "rounds": round_num,
        "matched_details": [
            {"title": item.title, "company": item.company, "success": success, "detail": detail}
            for item, success, detail in greet_results
        ],
    }


def _build_keyword_list(primary_keyword: str, job_type: str) -> list[str]:
    """构建多关键词搜索列表，用于当一个关键词搜不到足够合适岗位时轮换。"""
    keywords = [primary_keyword]

    suffix = " 实习" if job_type == "intern" else ""

    variants = [
        f"AI Agent{suffix}",
        f"大模型开发{suffix}",
        f"LLM应用{suffix}",
        f"NLP算法{suffix}",
        f"AIGC{suffix}",
    ]

    for v in variants:
        if v != primary_keyword and v not in keywords:
            keywords.append(v)

    return keywords
