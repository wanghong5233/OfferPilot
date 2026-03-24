"""Production Guard — 生产环境守护进程。

管理 OfferPilot 在真实部署环境下的自动调度、资源治理与健康守护：

1. **内置调度器** — 替代外部 cron，自包含驱动 greet / chat 任务
2. **时段感知** — 工作日高峰自动加密、夜间自动休眠、早晨自动唤醒
3. **资源治理** — 定期清理多余标签页、孤儿 Chrome 进程
4. **健康守护** — 周期性探测浏览器存活，异常时自动重建

所有配置通过环境变量注入，带合理默认值。
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import threading
import time
from datetime import datetime, timedelta
from logging.handlers import RotatingFileHandler
from pathlib import Path
from typing import Any

from app.tz import now_beijing

logger = logging.getLogger(__name__)

_GUARD_LOG_DIR = Path(__file__).resolve().parents[1] / "logs"
_GUARD_LOG_MAX_BYTES = 20 * 1024 * 1024
_GUARD_LOG_BACKUP_COUNT = 5


def _guard_file_logger() -> logging.Logger:
    """独立的文件日志 — guard.log，方便调试。"""
    fl = logging.getLogger("guard_file")
    if fl.handlers:
        return fl
    _GUARD_LOG_DIR.mkdir(parents=True, exist_ok=True)
    fh = RotatingFileHandler(
        str(_GUARD_LOG_DIR / "guard.log"),
        maxBytes=_GUARD_LOG_MAX_BYTES,
        backupCount=_GUARD_LOG_BACKUP_COUNT,
        encoding="utf-8",
    )
    fh.setFormatter(logging.Formatter("%(asctime)s | %(levelname)s | %(message)s", datefmt="%Y-%m-%d %H:%M:%S"))
    fl.addHandler(fh)
    fl.setLevel(logging.DEBUG)
    fl.propagate = False
    return fl


glog = _guard_file_logger()


def _now_local() -> datetime:
    """当前时间（按 GUARD_TIMEZONE，默认北京时间）。WSL 默认 UTC 会导致时段误判。"""
    return now_beijing()

# ─────────────────────────────────────────────────
# 环境变量配置（带默认值）
# ─────────────────────────────────────────────────

def _env_bool(key: str, default: bool = False) -> bool:
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes")

def _env_int(key: str, default: int) -> int:
    try:
        return int(os.getenv(key, str(default)))
    except (ValueError, TypeError):
        return default

GUARD_ENABLED           = _env_bool("PRODUCTION_GUARD_ENABLED", True)
GUARD_GREET_ENABLED     = _env_bool("GUARD_GREET_ENABLED", True)
GUARD_CHAT_ENABLED      = _env_bool("GUARD_CHAT_ENABLED", True)

# 活跃时段（24h 格式）
ACTIVE_START_HOUR       = _env_int("GUARD_ACTIVE_START_HOUR", 9)
ACTIVE_END_HOUR         = _env_int("GUARD_ACTIVE_END_HOUR", 22)
WEEKEND_ACTIVE_START    = _env_int("GUARD_WEEKEND_START_HOUR", 10)
WEEKEND_ACTIVE_END      = _env_int("GUARD_WEEKEND_END_HOUR", 20)

# 打招呼间隔（秒）
GREET_INTERVAL_PEAK     = _env_int("GUARD_GREET_INTERVAL_PEAK", 900)      # 高峰 15min
GREET_INTERVAL_OFFPEAK  = _env_int("GUARD_GREET_INTERVAL_OFFPEAK", 1800)  # 低峰 30min

# 聊天巡检间隔（秒）
CHAT_INTERVAL_PEAK      = _env_int("GUARD_CHAT_INTERVAL_PEAK", 180)       # 高峰 3min
CHAT_INTERVAL_OFFPEAK   = _env_int("GUARD_CHAT_INTERVAL_OFFPEAK", 600)    # 低峰 10min

# 资源清理间隔（秒）
CLEANUP_INTERVAL        = _env_int("GUARD_CLEANUP_INTERVAL", 300)         # 5min

# 高峰时段定义（工作日）
PEAK_HOURS = [(10, 12), (14, 18)]


def _pick_target_keyword(targets: list[str], job_type: str) -> str:
    """从画像目标岗位中优先挑 Agent/应用导向关键词。"""
    if not targets:
        return ""
    hints = [
        "agent", "智能体", "rag", "langgraph", "langchain", "mcp",
        "应用", "应用开发", "工程化", "llm应用", "copilot",
    ]
    for raw in targets:
        text = (raw or "").strip()
        if not text:
            continue
        lower = text.lower()
        if any(h in lower for h in hints):
            if job_type == "intern" and "实习" not in text:
                return f"{text} 实习"
            return text
    # 没有强匹配时，先用第一个，但会在 _normalize_guard_keyword 里再做兜底收敛
    return (targets[0] or "").strip()


def _normalize_guard_keyword(keyword: str, job_type: str) -> str:
    """规避过泛关键词，保证主动打招呼聚焦 Agent/应用实习。"""
    kw = (keyword or "").strip()
    lower = kw.lower()

    if not kw:
        kw = "AI Agent 实习" if job_type == "intern" else "AI Agent 开发"
        lower = kw.lower()

    # 纯“大模型/LLM/AI”太泛，容易搜到大量算法岗；统一收敛到 Agent/应用导向。
    too_broad = (
        lower in {"大模型", "llm", "ai", "人工智能"}
        or ("大模型" in kw and not any(t in lower for t in ["agent", "智能体", "应用", "rag", "langgraph", "langchain", "mcp"]))
    )
    if too_broad:
        kw = "AI Agent 实习" if job_type == "intern" else "AI Agent 开发"

    if job_type == "intern" and "实习" not in kw:
        kw = f"{kw} 实习"
    return kw


def _is_weekend() -> bool:
    return _now_local().weekday() >= 5


def _current_hour() -> int:
    return _now_local().hour


def _is_active_hour() -> bool:
    """当前是否在活跃时段内。"""
    h = _current_hour()
    if _is_weekend():
        return WEEKEND_ACTIVE_START <= h < WEEKEND_ACTIVE_END
    return ACTIVE_START_HOUR <= h < ACTIVE_END_HOUR


def _is_peak_hour() -> bool:
    """工作日高峰时段。"""
    if _is_weekend():
        return False
    h = _current_hour()
    return any(start <= h < end for start, end in PEAK_HOURS)


class ProductionGuard:
    """后台守护线程，驱动所有生产环境周期性任务。"""

    def __init__(self) -> None:
        self._stop_event = threading.Event()
        self._thread: threading.Thread | None = None
        self._running = False
        self._lock = threading.Lock()
        self._tick_seq = 0

        self._last_greet: float = 0
        self._last_chat: float = 0
        self._last_cleanup: float = 0
        self._last_health_check: float = 0
        self._sleep_logged = False

        self._stats: dict[str, Any] = {
            "started_at": None,
            "greet_runs": 0,
            "chat_runs": 0,
            "cleanups": 0,
            "chrome_kills": 0,
            "browser_rebuilds": 0,
            "errors": 0,
            "sleeping": False,
        }

    # ── 生命周期 ──────────────────────────────────

    def start(self) -> bool:
        with self._lock:
            if self._running:
                return False
            self._stop_event.clear()
            self._thread = threading.Thread(
                target=self._main_loop, daemon=True, name="production-guard"
            )
            self._running = True
            self._stats["started_at"] = now_beijing().isoformat()
            self._thread.start()
            logger.info(
                "ProductionGuard started (greet=%s chat=%s active=%d-%d)",
                GUARD_GREET_ENABLED, GUARD_CHAT_ENABLED,
                ACTIVE_START_HOUR, ACTIVE_END_HOUR,
            )
            glog.info(
                "Guard started: greet_enabled=%s chat_enabled=%s active=%02d-%02d "
                "weekend_active=%02d-%02d greet_interval_peak=%ss chat_interval_peak=%ss chat_interval_offpeak=%ss",
                GUARD_GREET_ENABLED,
                GUARD_CHAT_ENABLED,
                ACTIVE_START_HOUR,
                ACTIVE_END_HOUR,
                WEEKEND_ACTIVE_START,
                WEEKEND_ACTIVE_END,
                GREET_INTERVAL_PEAK,
                CHAT_INTERVAL_PEAK,
                CHAT_INTERVAL_OFFPEAK,
            )
            return True

    def stop(self, *, timeout: float = 5.0) -> None:
        with self._lock:
            self._running = False
            self._stop_event.set()
            t = self._thread
        if t and t.is_alive():
            t.join(timeout=timeout)
        logger.info("ProductionGuard stopped")

    @property
    def stats(self) -> dict[str, Any]:
        return {**self._stats, "running": self._running}

    # ── 主循环 ────────────────────────────────────

    def _main_loop(self) -> None:
        time.sleep(10)
        glog.info("Guard main loop started")
        while not self._stop_event.is_set():
            try:
                self._tick()
            except Exception as exc:
                self._stats["errors"] += 1
                glog.exception("ProductionGuard tick error: %s", exc)
                logger.exception("ProductionGuard tick error: %s", exc)
            self._stop_event.wait(timeout=30)

    def _tick(self) -> None:
        now = time.monotonic()
        self._tick_seq += 1

        if not _is_active_hour():
            self._handle_sleep(now)
            return

        if self._stats.get("sleeping"):
            self._handle_wake()

        if now - self._last_cleanup >= CLEANUP_INTERVAL:
            self._do_cleanup()
            self._last_cleanup = now

        peak = _is_peak_hour()
        h = _current_hour()
        if self._tick_seq % 10 == 0:
            glog.debug(
                "tick#%d now=%02d:%02d active=%s peak=%s stats(greet=%d chat=%d errors=%d)",
                self._tick_seq,
                _current_hour(),
                _now_local().minute,
                _is_active_hour(),
                peak,
                self._stats.get("greet_runs", 0),
                self._stats.get("chat_runs", 0),
                self._stats.get("errors", 0),
            )

        if GUARD_GREET_ENABLED and peak and not _is_weekend():
            if now - self._last_greet >= GREET_INTERVAL_PEAK:
                logger.info("ProductionGuard 进入打招呼窗口 (当前 %02d:xx 高峰时段)", h)
                self._do_greet()
                self._last_greet = now

        if GUARD_CHAT_ENABLED:
            interval = CHAT_INTERVAL_PEAK if peak else CHAT_INTERVAL_OFFPEAK
            if now - self._last_chat >= interval:
                self._do_chat()
                self._last_chat = now

    # ── 休眠 / 唤醒 ──────────────────────────────

    def _handle_sleep(self, now: float) -> None:
        if not self._stats.get("sleeping"):
            self._stats["sleeping"] = True
            self._sleep_logged = False

        if not self._sleep_logged:
            active_start = WEEKEND_ACTIVE_START if _is_weekend() else ACTIVE_START_HOUR
            active_end = WEEKEND_ACTIVE_END if _is_weekend() else ACTIVE_END_HOUR
            logger.info(
                "ProductionGuard 进入休眠（当前 %02d:%02d，活跃时段 %d:00-%d:00）",
                _current_hour(), _now_local().minute,
                active_start, active_end,
            )
            self._sleep_logged = True

            self._release_browser_for_sleep()

        if now - self._last_cleanup >= CLEANUP_INTERVAL * 6:
            self._do_cleanup()
            self._last_cleanup = now

    def _handle_wake(self) -> None:
        self._stats["sleeping"] = False
        self._sleep_logged = False
        logger.info(
            "ProductionGuard 唤醒（%02d:%02d）— 开始工作",
            _current_hour(), _now_local().minute,
        )

    def _release_browser_for_sleep(self) -> None:
        """休眠时关闭浏览器释放内存，下次使用时自动重建。"""
        try:
            from .boss_scan import shutdown_browser
            shutdown_browser()
            logger.info("休眠模式：浏览器已关闭以释放资源")
        except Exception as exc:
            logger.warning("休眠关闭浏览器失败: %s", exc)

    # ── 打招呼 ────────────────────────────────────

    def _do_greet(self) -> None:
        try:
            from .boss_scan import greet_matching_jobs
            from .storage import get_user_profile

            run_id = f"greet-{now_beijing().strftime('%Y%m%d-%H%M%S')}"
            profile = get_user_profile("default")
            job_type = os.getenv("BOSS_GREET_FALLBACK_JOB_TYPE", "intern").strip() or "intern"
            keyword = ""
            if profile and isinstance(profile.get("profile"), dict):
                pref = profile["profile"].get("job_preference", {})
                job_type = (pref.get("job_type") or job_type).strip()
                targets = pref.get("target_positions") or []
                keyword = _pick_target_keyword(targets, job_type)

            if not keyword:
                keyword = os.getenv(
                    "BOSS_GREET_FALLBACK_KEYWORD",
                    "AI Agent 实习" if job_type == "intern" else "AI Agent 开发",
                ).strip()

            keyword = _normalize_guard_keyword(keyword, job_type)

            glog.info("=== GREET START === run_id=%s keyword=%r job_type=%s", run_id, keyword, job_type)
            logger.info("Guard greet: keyword=%r job_type=%s", keyword, job_type)

            result = greet_matching_jobs(
                keyword=keyword,
                batch_size=3,
                job_type=job_type,
                run_id=run_id,
            )
            self._stats["greet_runs"] += 1
            glog.info(
                "=== GREET END === run_id=%s greeted=%s failed=%s llm_rejected=%s detail_fail=%s daily=%s/%s reason=%s rounds=%s",
                run_id,
                result.get("greeted", 0), result.get("failed", 0),
                result.get("llm_rejected", 0), result.get("detail_fail", 0),
                result.get("daily_count", "?"), result.get("daily_limit", "?"),
                result.get("reason", "-"),
                result.get("rounds", "-"),
            )
            details = result.get("matched_details") or []
            for d in details:
                glog.info("  %s | %s | success=%s | %s", d.get("title"), d.get("company"), d.get("success"), d.get("detail", "")[:120])
            logger.info(
                "Guard greet: greeted=%s failed=%s daily=%s/%s",
                result.get("greeted", 0), result.get("failed", 0),
                result.get("daily_count", "?"), result.get("daily_limit", "?"),
            )
        except Exception as exc:
            self._stats["errors"] += 1
            glog.exception("=== GREET ERROR === %s", exc)
            logger.warning("Guard greet error: %s", str(exc)[:200])

    # ── 聊天巡检 ──────────────────────────────────

    def _do_chat(self) -> None:
        try:
            from .boss_chat_workflow import run_boss_chat_copilot_workflow

            run_id = f"chat-{now_beijing().strftime('%Y%m%d-%H%M%S')}"
            glog.info("=== CHAT START === run_id=%s unread_only=true max_conversations=10", run_id)
            resp = run_boss_chat_copilot_workflow(
                max_conversations=10,
                unread_only=True,
                profile_id="default",
                notify_on_escalate=True,
                auto_execute=True,
            )
            self._stats["chat_runs"] += 1
            glog.info(
                "=== CHAT END === run_id=%s total=%d new=%d processed=%d",
                run_id, resp.total_conversations, resp.new_count, resp.processed_count,
            )
            logger.info(
                "Guard chat: total=%d new=%d processed=%d",
                resp.total_conversations, resp.new_count, resp.processed_count,
            )
        except Exception as exc:
            self._stats["errors"] += 1
            glog.exception("=== CHAT ERROR === %s", exc)
            logger.warning("Guard chat error: %s", str(exc)[:200])

    # ── 资源清理 ──────────────────────────────────

    def _do_cleanup(self) -> None:
        closed_tabs = self._cleanup_tabs()
        killed_procs = self._cleanup_orphan_chrome()
        glog.debug("cleanup result: closed_tabs=%d killed_orphans=%d", closed_tabs, killed_procs)
        if closed_tabs > 0 or killed_procs > 0:
            self._stats["cleanups"] += 1
            glog.info("cleanup action: closed_tabs=%d killed_orphans=%d", closed_tabs, killed_procs)

    def _cleanup_tabs(self) -> int:
        try:
            from .boss_scan import cleanup_browser_tabs
            return cleanup_browser_tabs()
        except Exception:
            return 0

    def _cleanup_orphan_chrome(self) -> int:
        """清理不属于当前会话的孤儿 Chrome/Chromium 进程（仅 Linux/WSL）。

        保守策略：如果无法确认当前浏览器 PID，则跳过清理，避免误杀。
        """
        def _collect_descendants(root_pid: int) -> set[int]:
            """收集 root_pid 的全部子孙进程，避免误杀当前浏览器会话组件。"""
            protected: set[int] = {root_pid}
            stack: list[int] = [root_pid]
            while stack:
                parent = stack.pop()
                try:
                    child_res = subprocess.run(
                        ["pgrep", "-P", str(parent)],
                        capture_output=True, text=True, timeout=3,
                    )
                    if child_res.returncode != 0:
                        continue
                    for line in child_res.stdout.strip().split("\n"):
                        s = line.strip()
                        if not s:
                            continue
                        child_pid = int(s)
                        if child_pid in protected:
                            continue
                        protected.add(child_pid)
                        stack.append(child_pid)
                except Exception:
                    continue
            return protected

        killed = 0
        try:
            result = subprocess.run(
                ["pgrep", "-f", "chrome.*--user-data-dir=.*boss"],
                capture_output=True, text=True, timeout=5,
            )
            if result.returncode != 0:
                return 0

            from .boss_scan import _browser_context
            known_pids: set[int] = set()
            protected_pids: set[int] = set()
            if _browser_context is not None:
                try:
                    browser = getattr(_browser_context, "browser", None)
                    if browser:
                        proc = getattr(browser, "process", None)
                        if proc:
                            known_pids.add(proc.pid)
                            protected_pids |= _collect_descendants(proc.pid)
                except Exception:
                    pass

            if not known_pids:
                logger.info("无法获取当前浏览器 PID，跳过孤儿清理以避免误杀")
                return 0

            for line in result.stdout.strip().split("\n"):
                pid_str = line.strip()
                if not pid_str:
                    continue
                try:
                    pid = int(pid_str)
                    if pid in protected_pids or pid in known_pids:
                        continue
                    os.kill(pid, signal.SIGTERM)
                    killed += 1
                    logger.info("清理孤儿 Chrome 进程: PID=%d", pid)
                except (ValueError, ProcessLookupError, PermissionError):
                    pass

            if killed > 0:
                self._stats["chrome_kills"] += killed
        except FileNotFoundError:
            pass
        except Exception as exc:
            logger.debug("orphan chrome cleanup: %s", exc)
        return killed


# ─────────────────────────────────────────────────
# 全局单例
# ─────────────────────────────────────────────────

_guard: ProductionGuard | None = None
_guard_lock = threading.Lock()


def get_production_guard() -> ProductionGuard:
    global _guard
    with _guard_lock:
        if _guard is None:
            _guard = ProductionGuard()
        return _guard


def start_production_guard() -> bool:
    """启动守护（main.py startup 调用）。"""
    if not GUARD_ENABLED:
        logger.info("ProductionGuard disabled (PRODUCTION_GUARD_ENABLED=false)")
        return False
    return get_production_guard().start()


def stop_production_guard() -> None:
    """停止守护（main.py shutdown 调用）。"""
    g = _guard
    if g:
        g.stop()


def guard_stats() -> dict[str, Any]:
    """返回守护状态（供 /health 端点使用）。"""
    g = _guard
    if g:
        return g.stats
    return {"running": False, "enabled": GUARD_ENABLED}


# ─────────────────────────────────────────────────
# atexit 兜底：非优雅退出时也尽量清理浏览器
# ─────────────────────────────────────────────────

def _atexit_cleanup() -> None:
    try:
        from .boss_scan import shutdown_browser
        shutdown_browser()
    except Exception:
        pass
    stop_production_guard()

atexit.register(_atexit_cleanup)
