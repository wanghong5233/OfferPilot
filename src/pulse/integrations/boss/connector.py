from __future__ import annotations

import hashlib
import json
import os
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable

from ...core.mcp_transport_http import HttpMCPTransport
from ...core.tools.web_search import search_web

_LOCAL_SEED_JOBS: tuple[tuple[str, str, str], ...] = (
    ("AI Agent Intern", "Pulse Labs", "200-300/天"),
    ("LLM Application Engineer (Intern)", "NovaMind", "180-280/天"),
    ("AI 产品实习生", "DeepBridge", "150-220/天"),
    ("RAG Engineer (Intern)", "VectorWorks", "220-320/天"),
    ("Backend Engineer (Python)", "Orbit AI", "160-240/天"),
    ("MCP Tooling Intern", "Signal Stack", "200-260/天"),
)


class _ConnectorError(RuntimeError):
    """Non-retryable provider error."""


class _RetryableConnectorError(_ConnectorError):
    """Retryable provider error."""


class _AuthExpiredConnectorError(_ConnectorError):
    """Provider auth/cookie/token is invalid."""


@dataclass(slots=True)
class _ConnectorCall:
    ok: bool
    result: Any
    attempts: int
    error: str | None = None


class _RateLimiter:
    def __init__(self, min_interval_sec: float) -> None:
        self._min_interval_sec = max(0.0, float(min_interval_sec))
        self._lock = threading.Lock()
        self._last_call_at: dict[str, float] = {}

    def wait(self, key: str) -> None:
        if self._min_interval_sec <= 0:
            return
        wait_sec = 0.0
        now = time.monotonic()
        with self._lock:
            previous = self._last_call_at.get(key)
            if previous is not None:
                elapsed = now - previous
                if elapsed < self._min_interval_sec:
                    wait_sec = self._min_interval_sec - elapsed
            self._last_call_at[key] = now + wait_sec
        if wait_sec > 0:
            time.sleep(wait_sec)


class _AuditLogger:
    def __init__(self, storage_path: Path) -> None:
        self._storage_path = storage_path
        self._lock = threading.Lock()

    @property
    def path(self) -> Path:
        return self._storage_path

    def append(self, payload: dict[str, Any]) -> None:
        row = dict(payload)
        row["logged_at"] = datetime.now(timezone.utc).isoformat()
        self._storage_path.parent.mkdir(parents=True, exist_ok=True)
        line = json.dumps(row, ensure_ascii=False)
        with self._lock:
            with self._storage_path.open("a", encoding="utf-8") as handle:
                handle.write(line + "\n")


def _safe_int(raw: str, default: int, *, min_value: int, max_value: int) -> int:
    try:
        value = int(str(raw).strip())
    except Exception:
        value = default
    return max(min_value, min(value, max_value))


def _safe_float(raw: str, default: float, *, min_value: float, max_value: float) -> float:
    try:
        value = float(str(raw).strip())
    except Exception:
        value = default
    return max(min_value, min(value, max_value))


def _safe_bool(raw: str, *, default: bool) -> bool:
    value = str(raw or "").strip().lower()
    if not value:
        return default
    return value in {"1", "true", "yes", "on"}


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[4]


def _resolve_path(raw_path: str, *, default_path: Path) -> Path:
    value = str(raw_path or "").strip()
    if not value:
        return default_path
    candidate = Path(value).expanduser()
    if candidate.is_absolute():
        return candidate
    return (_repo_root() / candidate).resolve()


def _sha(text: str) -> str:
    return hashlib.sha1(text.encode("utf-8")).hexdigest()[:16]


def _read_cookie_header(cookie_path: Path | None) -> str:
    if cookie_path is None or not cookie_path.is_file():
        return ""
    try:
        raw_text = cookie_path.read_text(encoding="utf-8").strip()
    except Exception:
        return ""
    if not raw_text:
        return ""
    try:
        payload = json.loads(raw_text)
    except Exception:
        return raw_text
    if isinstance(payload, dict):
        direct = str(payload.get("cookie") or payload.get("cookies") or "").strip()
        if direct:
            return direct
        cookie_rows = payload.get("items")
        if isinstance(cookie_rows, list):
            parts: list[str] = []
            for row in cookie_rows:
                if not isinstance(row, dict):
                    continue
                name = str(row.get("name") or "").strip()
                value = str(row.get("value") or "").strip()
                if name and value:
                    parts.append(f"{name}={value}")
            if parts:
                return "; ".join(parts)
    if isinstance(payload, list):
        parts = []
        for row in payload:
            if not isinstance(row, dict):
                continue
            name = str(row.get("name") or "").strip()
            value = str(row.get("value") or "").strip()
            if name and value:
                parts.append(f"{name}={value}")
        if parts:
            return "; ".join(parts)
    return raw_text


def _extract_items(body: Any) -> list[dict[str, Any]]:
    if isinstance(body, list):
        return [dict(item) for item in body if isinstance(item, dict)]
    if not isinstance(body, dict):
        return []
    direct = body.get("items")
    if isinstance(direct, list):
        return [dict(item) for item in direct if isinstance(item, dict)]
    data = body.get("data")
    if isinstance(data, dict):
        nested = data.get("items")
        if isinstance(nested, list):
            return [dict(item) for item in nested if isinstance(item, dict)]
    return []


def _extract_errors(body: Any) -> list[str]:
    if isinstance(body, dict):
        if isinstance(body.get("errors"), list):
            return [str(item)[:400] for item in body["errors"]]
        error = str(body.get("error") or body.get("message") or "").strip()
        if error:
            return [error[:400]]
    return []


class BossPlatformConnector:
    """Unified connector for BOSS recruitment platform execution."""

    def __init__(self) -> None:
        provider_override = str(os.getenv("PULSE_BOSS_PROVIDER", "") or "").strip().lower()
        self._openapi_base_url = str(os.getenv("PULSE_BOSS_OPENAPI_BASE_URL", "") or "").strip().rstrip("/")
        self._openapi_token = str(os.getenv("PULSE_BOSS_OPENAPI_TOKEN", "") or "").strip()
        self._openapi_timeout_sec = _safe_float(
            os.getenv("PULSE_BOSS_OPENAPI_TIMEOUT_SEC", "10"),
            10.0,
            min_value=2.0,
            max_value=40.0,
        )
        self._openapi_auth_status_path = str(
            os.getenv("PULSE_BOSS_OPENAPI_AUTH_STATUS_PATH", "/auth/status") or ""
        ).strip()
        self._openapi_scan_path = str(os.getenv("PULSE_BOSS_OPENAPI_SCAN_PATH", "/jobs/scan") or "").strip()
        self._openapi_detail_path = str(os.getenv("PULSE_BOSS_OPENAPI_DETAIL_PATH", "/jobs/detail") or "").strip()
        self._openapi_greet_path = str(os.getenv("PULSE_BOSS_OPENAPI_GREET_PATH", "/jobs/greet") or "").strip()
        self._openapi_pull_path = str(os.getenv("PULSE_BOSS_OPENAPI_PULL_PATH", "/chats/pull") or "").strip()
        self._openapi_reply_path = str(os.getenv("PULSE_BOSS_OPENAPI_REPLY_PATH", "/chats/reply") or "").strip()
        self._openapi_mark_path = str(
            os.getenv("PULSE_BOSS_OPENAPI_MARK_PATH", "/chats/mark_processed") or ""
        ).strip()

        self._mcp_base_url = str(os.getenv("PULSE_BOSS_MCP_BASE_URL", "") or "").strip().rstrip("/")
        self._mcp_token = str(os.getenv("PULSE_BOSS_MCP_TOKEN", "") or "").strip()
        self._mcp_timeout_sec = _safe_float(
            os.getenv("PULSE_BOSS_MCP_TIMEOUT_SEC", "10"),
            10.0,
            min_value=2.0,
            max_value=40.0,
        )
        self._mcp_server = str(os.getenv("PULSE_BOSS_MCP_SERVER", "boss") or "").strip() or "boss"
        self._mcp_scan_tool = str(os.getenv("PULSE_BOSS_MCP_SCAN_TOOL", "scan_jobs") or "").strip()
        self._mcp_detail_tool = str(os.getenv("PULSE_BOSS_MCP_DETAIL_TOOL", "job_detail") or "").strip()
        self._mcp_greet_tool = str(os.getenv("PULSE_BOSS_MCP_GREET_TOOL", "greet_job") or "").strip()
        self._mcp_pull_tool = str(
            os.getenv("PULSE_BOSS_MCP_PULL_TOOL", "pull_conversations") or ""
        ).strip()
        self._mcp_reply_tool = str(
            os.getenv("PULSE_BOSS_MCP_REPLY_TOOL", "reply_conversation") or ""
        ).strip()
        self._mcp_mark_tool = str(
            os.getenv("PULSE_BOSS_MCP_MARK_TOOL", "mark_processed") or ""
        ).strip()
        self._mcp_check_login_tool = str(
            os.getenv("PULSE_BOSS_MCP_CHECK_LOGIN_TOOL", "check_login") or ""
        ).strip()

        self._retry_count = _safe_int(
            os.getenv("PULSE_BOSS_RETRY_COUNT", "2"),
            2,
            min_value=0,
            max_value=6,
        )
        self._retry_backoff_sec = _safe_float(
            os.getenv("PULSE_BOSS_RETRY_BACKOFF_SEC", "0.8"),
            0.8,
            min_value=0.1,
            max_value=8.0,
        )
        self._rate_limit_sec = _safe_float(
            os.getenv("PULSE_BOSS_RATE_LIMIT_SEC", "1.2"),
            1.2,
            min_value=0.0,
            max_value=8.0,
        )
        self._rate_limiter = _RateLimiter(self._rate_limit_sec)

        cookie_path_raw = str(os.getenv("PULSE_BOSS_COOKIE_PATH", "") or "").strip()
        self._cookie_path = _resolve_path(cookie_path_raw, default_path=Path.home() / ".pulse" / "boss.cookies.json")
        if not cookie_path_raw:
            self._cookie_path = None

        audit_default = Path.home() / ".pulse" / "boss_connector_audit.jsonl"
        self._audit = _AuditLogger(
            _resolve_path(
                str(os.getenv("PULSE_BOSS_CONNECTOR_AUDIT_PATH", "") or "").strip(),
                default_path=audit_default,
            )
        )
        self._allow_seed_fallback = _safe_bool(
            os.getenv("PULSE_BOSS_ALLOW_SEED_FALLBACK", "false"),
            default=False,
        )
        self._last_auth_error = ""
        self._degraded_reason = ""
        self._mode = self._resolve_mode(provider_override)
        self._mcp_transport: HttpMCPTransport | None = None
        if self._mode == "mcp":
            try:
                self._mcp_transport = HttpMCPTransport(
                    base_url=self._mcp_base_url,
                    timeout_sec=self._mcp_timeout_sec,
                    auth_token=self._mcp_token,
                )
            except Exception as exc:
                self._degraded_reason = f"mcp transport init failed: {exc}"
                self._mode = "unconfigured"
        self._execution_ready = self._mode in {"openapi", "mcp"}

    @property
    def provider_name(self) -> str:
        if self._mode == "openapi":
            return "boss_openapi"
        if self._mode == "mcp":
            return "boss_mcp"
        if self._mode == "web_search":
            return "boss_web_search"
        return "boss_unconfigured"

    @property
    def execution_ready(self) -> bool:
        return self._execution_ready

    def _resolve_mode(self, override: str) -> str:
        if override in {"openapi", "boss_openapi"}:
            if self._openapi_base_url:
                return "openapi"
            self._degraded_reason = "PULSE_BOSS_PROVIDER=openapi but PULSE_BOSS_OPENAPI_BASE_URL is missing"
            return "unconfigured"
        if override in {"mcp", "boss_mcp"}:
            if self._mcp_base_url:
                return "mcp"
            self._degraded_reason = "PULSE_BOSS_PROVIDER=mcp but PULSE_BOSS_MCP_BASE_URL is missing"
            return "unconfigured"
        if override in {"web_search", "search"}:
            self._degraded_reason = "PULSE_BOSS_PROVIDER=web_search enables search-only mode"
            return "web_search"
        if self._mcp_base_url:
            return "mcp"
        if self._openapi_base_url:
            return "openapi"
        self._degraded_reason = "no openapi or mcp connector configured"
        return "unconfigured"

    def scan_jobs(
        self,
        *,
        keyword: str,
        max_items: int,
        max_pages: int,
        job_type: str = "all",
    ) -> dict[str, Any]:
        safe_keyword = str(keyword or "").strip() or "AI Agent 实习"
        safe_items = max(1, min(int(max_items), 80))
        safe_pages = max(1, min(int(max_pages), 8))
        payload = {
            "keyword": safe_keyword,
            "max_items": safe_items,
            "max_pages": safe_pages,
            "job_type": str(job_type or "all").strip() or "all",
        }
        if self._mode == "openapi":
            call = self._invoke("scan_jobs", payload, lambda: self._openapi_call(self._openapi_scan_path, payload))
            return self._normalize_scan_call(call, default_source="boss_openapi")
        if self._mode == "mcp":
            call = self._invoke("scan_jobs", payload, lambda: self._mcp_call(self._mcp_scan_tool, payload))
            return self._normalize_scan_call(call, default_source="boss_mcp")
        if self._mode == "web_search":
            return self._scan_with_web_search(payload)
        return {
            "ok": False,
            "items": [],
            "pages_scanned": 1,
            "source": self.provider_name,
            "errors": [self._degraded_reason or "provider is not execution-ready"],
            "attempts": 0,
        }

    def fetch_job_detail(
        self,
        *,
        job_id: str,
        source_url: str,
    ) -> dict[str, Any]:
        payload = {
            "job_id": str(job_id or "").strip(),
            "source_url": str(source_url or "").strip(),
        }
        if self._mode == "openapi" and self._openapi_detail_path:
            call = self._invoke("job_detail", payload, lambda: self._openapi_call(self._openapi_detail_path, payload))
            return self._normalize_detail_call(call)
        if self._mode == "mcp" and self._mcp_detail_tool:
            call = self._invoke("job_detail", payload, lambda: self._mcp_call(self._mcp_detail_tool, payload))
            return self._normalize_detail_call(call)
        return {
            "ok": False,
            "detail": {},
            "provider": self.provider_name,
            "source": self.provider_name,
            "error": "job detail is unavailable in current provider mode",
            "attempts": 0,
        }

    def greet_job(
        self,
        *,
        job: dict[str, Any],
        greeting_text: str,
        run_id: str,
    ) -> dict[str, Any]:
        payload = {
            "run_id": str(run_id or "").strip(),
            "job_id": str(job.get("job_id") or "").strip(),
            "source_url": str(job.get("source_url") or "").strip(),
            "job_title": str(job.get("title") or "").strip(),
            "company": str(job.get("company") or "").strip(),
            "greeting_text": str(greeting_text or "").strip(),
        }
        if not self._execution_ready:
            self._audit.append(
                {
                    "provider": self.provider_name,
                    "operation": "greet_job",
                    "status": "dry_run",
                    "request": payload,
                    "reason": "provider is not execution-ready",
                }
            )
            return {
                "ok": False,
                "status": "dry_run",
                "provider": self.provider_name,
                "source": self.provider_name,
                "error": "provider is not execution-ready",
                "attempts": 0,
            }
        if self._mode == "openapi":
            call = self._invoke("greet_job", payload, lambda: self._openapi_call(self._openapi_greet_path, payload))
        else:
            call = self._invoke("greet_job", payload, lambda: self._mcp_call(self._mcp_greet_tool, payload))
        return self._normalize_action_call(call, success_status="sent")

    def pull_conversations(
        self,
        *,
        max_conversations: int,
        unread_only: bool,
        fetch_latest_hr: bool,
        chat_tab: str,
    ) -> dict[str, Any]:
        payload = {
            "max_conversations": max(1, min(int(max_conversations), 200)),
            "unread_only": bool(unread_only),
            "fetch_latest_hr": bool(fetch_latest_hr),
            "chat_tab": str(chat_tab or "全部").strip() or "全部",
        }
        if not self._execution_ready:
            return {
                "ok": False,
                "items": [],
                "source": self.provider_name,
                "errors": ["provider is not execution-ready"],
                "attempts": 0,
            }
        if self._mode == "openapi":
            call = self._invoke("pull_conversations", payload, lambda: self._openapi_call(self._openapi_pull_path, payload))
        else:
            call = self._invoke("pull_conversations", payload, lambda: self._mcp_call(self._mcp_pull_tool, payload))
        if not call.ok:
            return {
                "ok": False,
                "items": [],
                "source": self.provider_name,
                "errors": [str(call.error or "pull conversation failed")[:400]],
                "attempts": call.attempts,
            }
        items = _extract_items(call.result)
        errors = _extract_errors(call.result)
        unread_total = 0
        if isinstance(call.result, dict):
            unread_total = max(0, int(call.result.get("unread_total") or 0))
        return {
            "ok": True,
            "items": items,
            "source": str((call.result or {}).get("source") if isinstance(call.result, dict) else "") or self.provider_name,
            "errors": errors,
            "unread_total": unread_total,
            "attempts": call.attempts,
        }

    def reply_conversation(
        self,
        *,
        conversation_id: str,
        reply_text: str,
        profile_id: str,
        conversation_hint: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        payload = {
            "conversation_id": str(conversation_id or "").strip(),
            "reply_text": str(reply_text or "").strip(),
            "profile_id": str(profile_id or "default").strip() or "default",
        }
        if conversation_hint:
            payload["conversation_hint"] = dict(conversation_hint)
        if not self._execution_ready:
            return {
                "ok": False,
                "status": "dry_run",
                "source": self.provider_name,
                "error": "provider is not execution-ready",
                "attempts": 0,
            }
        if self._mode == "openapi":
            call = self._invoke(
                "reply_conversation",
                payload,
                lambda: self._openapi_call(self._openapi_reply_path, payload),
            )
        else:
            call = self._invoke(
                "reply_conversation",
                payload,
                lambda: self._mcp_call(self._mcp_reply_tool, payload),
            )
        return self._normalize_action_call(call, success_status="sent")

    def mark_processed(
        self,
        *,
        conversation_id: str,
        run_id: str,
        note: str = "",
    ) -> dict[str, Any]:
        payload = {
            "conversation_id": str(conversation_id or "").strip(),
            "run_id": str(run_id or "").strip(),
            "note": str(note or "").strip(),
        }
        if not self._execution_ready:
            return {
                "ok": False,
                "status": "dry_run",
                "source": self.provider_name,
                "error": "provider is not execution-ready",
                "attempts": 0,
            }
        if self._mode == "openapi":
            call = self._invoke(
                "mark_processed",
                payload,
                lambda: self._openapi_call(self._openapi_mark_path, payload),
            )
        else:
            call = self._invoke(
                "mark_processed",
                payload,
                lambda: self._mcp_call(self._mcp_mark_tool, payload),
            )
        return self._normalize_action_call(call, success_status="marked")

    def check_login(self) -> dict[str, Any]:
        if not self._execution_ready:
            return {
                "ok": False,
                "status": "provider_unavailable",
                "source": self.provider_name,
                "provider": self.provider_name,
                "error": "provider is not execution-ready",
                "attempts": 0,
            }
        if self._mode == "mcp" and self._mcp_check_login_tool:
            call = self._invoke(
                "check_login",
                {},
                lambda: self._mcp_call(self._mcp_check_login_tool, {}),
            )
            if not call.ok:
                return {
                    "ok": False,
                    "status": "failed",
                    "source": self.provider_name,
                    "provider": self.provider_name,
                    "error": str(call.error or "check login failed")[:400],
                    "attempts": call.attempts,
                }
            body = call.result if isinstance(call.result, dict) else {"value": call.result}
            status = str(body.get("status") or "").strip() or ("ready" if bool(body.get("ok")) else "failed")
            ok = bool(body.get("ok")) if "ok" in body else status == "ready"
            error = str(body.get("error") or body.get("message") or "").strip() or None
            return {
                "ok": ok,
                "status": status,
                "source": self.provider_name,
                "provider": self.provider_name,
                "error": error[:400] if error else None,
                "attempts": call.attempts,
                "result": body,
            }
        cookie_loaded = bool(_read_cookie_header(self._cookie_path))
        token_ready = bool(self._openapi_token)
        auth_ready = cookie_loaded or token_ready
        return {
            "ok": auth_ready,
            "status": "ready" if auth_ready else "auth_required",
            "source": self.provider_name,
            "provider": self.provider_name,
            "error": None if auth_ready else "openapi token/cookie is missing",
            "attempts": 0,
            "result": {
                "cookie_loaded": cookie_loaded,
                "token_ready": token_ready,
            },
        }

    def health(self) -> dict[str, Any]:
        cookie_loaded = bool(_read_cookie_header(self._cookie_path))
        payload: dict[str, Any] = {
            "provider": self.provider_name,
            "mode": self._mode,
            "execution_ready": self._execution_ready,
            "degraded": not self._execution_ready,
            "degraded_reason": self._degraded_reason or None,
            "fallbacks": {
                "web_search_enabled": self._mode == "web_search",
                "seed_enabled": self._allow_seed_fallback,
            },
            "retry": {
                "count": self._retry_count,
                "backoff_sec": self._retry_backoff_sec,
            },
            "rate_limit_sec": self._rate_limit_sec,
            "audit_path": str(self._audit.path),
            "check_login_supported": bool(
                (self._mode == "mcp" and self._mcp_check_login_tool) or self._mode == "openapi"
            ),
            "auth": {
                "cookie_path": str(self._cookie_path) if self._cookie_path else None,
                "cookie_loaded": cookie_loaded,
                "token_configured": bool(self._openapi_token or self._mcp_token),
                "last_auth_error": self._last_auth_error or None,
            },
        }
        if self._openapi_base_url:
            payload["openapi"] = {
                "base_url": self._openapi_base_url,
                "scan_path": self._openapi_scan_path,
                "greet_path": self._openapi_greet_path,
                "pull_path": self._openapi_pull_path,
                "auth_status_path": self._openapi_auth_status_path or None,
            }
        if self._mcp_base_url:
            payload["mcp"] = {
                "base_url": self._mcp_base_url,
                "server": self._mcp_server,
                "scan_tool": self._mcp_scan_tool,
                "greet_tool": self._mcp_greet_tool,
                "pull_tool": self._mcp_pull_tool,
                "check_login_tool": self._mcp_check_login_tool or None,
            }
        return payload

    def _scan_with_web_search(self, payload: dict[str, Any]) -> dict[str, Any]:
        safe_keyword = str(payload.get("keyword") or "").strip() or "AI Agent 实习"
        safe_items = max(1, min(int(payload.get("max_items") or 10), 80))
        safe_pages = max(1, min(int(payload.get("max_pages") or 3), 8))
        query_pool = (
            f"site:zhipin.com {safe_keyword} 实习",
            f"site:zhipin.com {safe_keyword} 招聘",
            f"site:zhipin.com {safe_keyword} 岗位",
            f"{safe_keyword} BOSS直聘",
        )
        rows: list[dict[str, Any]] = []
        seen: set[str] = set()
        errors: list[str] = []
        pages_scanned = 0
        for query in query_pool[:safe_pages]:
            pages_scanned += 1
            try:
                hits = search_web(query, max_results=min(12, safe_items * 2))
            except Exception as exc:
                errors.append(str(exc)[:400])
                continue
            for hit in hits:
                if len(rows) >= safe_items:
                    break
                source_url = str(hit.url or "").strip()
                title = str(hit.title or "").strip()
                if not source_url and not title:
                    continue
                dedupe_key = (source_url or title).lower()
                if dedupe_key in seen:
                    continue
                seen.add(dedupe_key)
                rows.append(
                    {
                        "job_id": _sha(dedupe_key),
                        "title": title,
                        "company": "",
                        "salary": None,
                        "source_url": source_url,
                        "snippet": str(hit.snippet or "")[:1000],
                        "source": "boss_web_search",
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            if len(rows) >= safe_items:
                break

        if not rows and self._allow_seed_fallback:
            seeded = int(hashlib.sha1(safe_keyword.encode("utf-8")).hexdigest()[:8], 16)
            for idx in range(safe_items):
                template = _LOCAL_SEED_JOBS[(seeded + idx) % len(_LOCAL_SEED_JOBS)]
                title, company, salary = template
                source_url = f"https://www.zhipin.com/job_detail/seed_{seeded}_{idx}"
                rows.append(
                    {
                        "job_id": _sha(source_url),
                        "title": title,
                        "company": company,
                        "salary": salary,
                        "source_url": source_url,
                        "snippet": f"{company} 正在招聘 {title}，关键词：{safe_keyword}",
                        "source": "boss_local_seed",
                        "collected_at": datetime.now(timezone.utc).isoformat(),
                    }
                )
            errors.append("web provider unavailable; switched to local seed dataset")
        elif not rows:
            errors.append("web provider returned no jobs and seed fallback is disabled")

        source = "boss_web_search"
        if rows and rows[0].get("source") == "boss_local_seed":
            source = "boss_local_seed"
        return {
            "ok": bool(rows),
            "items": rows[:safe_items],
            "pages_scanned": max(1, pages_scanned),
            "source": source,
            "errors": errors,
            "attempts": 1,
        }

    def _normalize_scan_call(self, call: _ConnectorCall, *, default_source: str) -> dict[str, Any]:
        if not call.ok:
            return {
                "ok": False,
                "items": [],
                "pages_scanned": 1,
                "source": default_source,
                "errors": [str(call.error or "scan failed")[:400]],
                "attempts": call.attempts,
            }
        body = call.result
        items = _extract_items(body)
        pages_scanned = 1
        if isinstance(body, dict):
            pages_scanned = max(1, int(body.get("pages_scanned") or body.get("pages") or 1))
        return {
            "ok": True,
            "items": items,
            "pages_scanned": pages_scanned,
            "source": str((body or {}).get("source") if isinstance(body, dict) else "") or default_source,
            "errors": _extract_errors(body),
            "attempts": call.attempts,
        }

    def _normalize_detail_call(self, call: _ConnectorCall) -> dict[str, Any]:
        if not call.ok:
            return {
                "ok": False,
                "detail": {},
                "source": self.provider_name,
                "provider": self.provider_name,
                "error": str(call.error or "job detail failed")[:400],
                "attempts": call.attempts,
            }
        detail: dict[str, Any] = {}
        if isinstance(call.result, dict):
            nested = call.result.get("detail")
            if isinstance(nested, dict):
                detail = dict(nested)
            else:
                detail = dict(call.result)
        return {
            "ok": True,
            "detail": detail,
            "source": self.provider_name,
            "provider": self.provider_name,
            "error": None,
            "attempts": call.attempts,
        }

    def _normalize_action_call(self, call: _ConnectorCall, *, success_status: str) -> dict[str, Any]:
        if not call.ok:
            return {
                "ok": False,
                "status": "failed",
                "source": self.provider_name,
                "provider": self.provider_name,
                "error": str(call.error or "provider action failed")[:400],
                "attempts": call.attempts,
            }
        status = success_status
        error = ""
        if isinstance(call.result, dict):
            status = str(call.result.get("status") or success_status).strip() or success_status
            if "ok" in call.result and not bool(call.result.get("ok")):
                error = str(call.result.get("error") or call.result.get("message") or "").strip()
        ok = not error
        return {
            "ok": ok,
            "status": status if ok else "failed",
            "source": self.provider_name,
            "provider": self.provider_name,
            "error": error[:400] if error else None,
            "attempts": call.attempts,
            "result": call.result if isinstance(call.result, dict) else {"value": call.result},
        }

    def _invoke(
        self,
        operation: str,
        payload: dict[str, Any],
        runner: Callable[[], Any],
    ) -> _ConnectorCall:
        attempts = 0
        last_error = ""
        for attempt in range(self._retry_count + 1):
            attempts = attempt + 1
            self._rate_limiter.wait(operation)
            try:
                result = runner()
                self._audit.append(
                    {
                        "provider": self.provider_name,
                        "operation": operation,
                        "status": "ok",
                        "attempt": attempts,
                        "request": payload,
                        "response_preview": self._preview(result),
                    }
                )
                return _ConnectorCall(ok=True, result=result, attempts=attempts)
            except _RetryableConnectorError as exc:
                last_error = str(exc)[:600]
                self._audit.append(
                    {
                        "provider": self.provider_name,
                        "operation": operation,
                        "status": "retryable_error",
                        "attempt": attempts,
                        "request": payload,
                        "error": last_error,
                    }
                )
                if attempt >= self._retry_count:
                    break
                time.sleep(self._retry_backoff_sec * (2**attempt))
            except _AuthExpiredConnectorError as exc:
                last_error = str(exc)[:600]
                self._last_auth_error = last_error
                self._audit.append(
                    {
                        "provider": self.provider_name,
                        "operation": operation,
                        "status": "auth_error",
                        "attempt": attempts,
                        "request": payload,
                        "error": last_error,
                    }
                )
                break
            except Exception as exc:
                last_error = str(exc)[:600]
                self._audit.append(
                    {
                        "provider": self.provider_name,
                        "operation": operation,
                        "status": "error",
                        "attempt": attempts,
                        "request": payload,
                        "error": last_error,
                    }
                )
                break
        return _ConnectorCall(ok=False, result={}, attempts=attempts, error=last_error or "provider call failed")

    @staticmethod
    def _preview(value: Any) -> Any:
        if isinstance(value, dict):
            preview = dict(value)
            if "items" in preview and isinstance(preview["items"], list):
                preview["items"] = preview["items"][:2]
            return preview
        if isinstance(value, list):
            return value[:2]
        return str(value)[:400]

    def _openapi_call(self, path: str, payload: dict[str, Any] | None = None) -> Any:
        if not self._openapi_base_url:
            raise _ConnectorError("openapi base url is empty")
        safe_path = path if str(path).startswith("/") else f"/{path}"
        url = f"{self._openapi_base_url}{safe_path}"
        data: bytes | None = None
        headers = {"Content-Type": "application/json; charset=utf-8"}
        if self._openapi_token:
            headers["Authorization"] = f"Bearer {self._openapi_token}"
        cookie_header = _read_cookie_header(self._cookie_path)
        if cookie_header:
            headers["Cookie"] = cookie_header
        if payload is not None:
            data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        request = urllib.request.Request(url, data=data, method="POST", headers=headers)
        try:
            with urllib.request.urlopen(request, timeout=self._openapi_timeout_sec) as response:
                text = response.read().decode("utf-8", errors="ignore")
            if not text.strip():
                return {}
            return json.loads(text)
        except urllib.error.HTTPError as exc:
            body = exc.read().decode("utf-8", errors="ignore")
            message = f"openapi http {exc.code}: {body[:300]}"
            if exc.code in {401, 403}:
                raise _AuthExpiredConnectorError(message) from exc
            if exc.code in {408, 409, 425, 429, 500, 502, 503, 504}:
                raise _RetryableConnectorError(message) from exc
            raise _ConnectorError(message) from exc
        except urllib.error.URLError as exc:
            raise _RetryableConnectorError(f"openapi url error: {exc.reason}") from exc
        except json.JSONDecodeError as exc:
            raise _ConnectorError(f"openapi invalid json: {exc}") from exc

    def _mcp_call(self, tool_name: str, arguments: dict[str, Any]) -> Any:
        if not self._mcp_transport:
            raise _ConnectorError("mcp transport is not configured")
        if not tool_name:
            raise _ConnectorError("mcp tool name is empty")
        try:
            return self._mcp_transport.call_tool(self._mcp_server, tool_name, arguments)
        except RuntimeError as exc:
            message = str(exc)
            if " 401 " in message or " 403 " in message:
                raise _AuthExpiredConnectorError(f"mcp auth error: {message[:400]}") from exc
            if any(code in message for code in (" 429 ", " 500 ", " 502 ", " 503 ", " 504 ")):
                raise _RetryableConnectorError(f"mcp transient error: {message[:400]}") from exc
            raise _ConnectorError(f"mcp error: {message[:400]}") from exc
        except Exception as exc:
            raise _RetryableConnectorError(f"mcp call failed: {exc}") from exc


def build_boss_platform_connector() -> BossPlatformConnector:
    return BossPlatformConnector()
