from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


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


def _first_env(*keys: str, default: str = "") -> str:
    for key in keys:
        value = str(os.getenv(key, "") or "").strip()
        if value:
            return value
    return default


BASE_URL = str(os.getenv("PULSE_BOSS_MCP_BASE_URL", "http://127.0.0.1:8811") or "").strip().rstrip("/")
TIMEOUT_SEC = _safe_float(
    _first_env("PULSE_BOSS_MCP_ACCEPTANCE_TIMEOUT_SEC", "PULSE_BOSS_MCP_SMOKE_TIMEOUT_SEC", default="25"),
    25.0,
    min_value=5.0,
    max_value=120.0,
)
KEYWORD = _first_env(
    "PULSE_BOSS_MCP_ACCEPTANCE_KEYWORD",
    "PULSE_BOSS_MCP_SMOKE_KEYWORD",
    default="AI Agent 实习",
)
STRICT_MODE = _safe_bool(
    _first_env("PULSE_BOSS_MCP_ACCEPTANCE_STRICT", "PULSE_BOSS_MCP_SMOKE_STRICT", default="false"),
    default=False,
)
SKIP_WRITE = _safe_bool(
    _first_env("PULSE_BOSS_MCP_ACCEPTANCE_SKIP_WRITE", "PULSE_BOSS_MCP_SMOKE_SKIP_WRITE", default="false"),
    default=False,
)
RUN_ID_PREFIX = _first_env("PULSE_BOSS_MCP_ACCEPTANCE_RUN_ID_PREFIX", default="accept")


@dataclass(slots=True)
class StepResult:
    name: str
    ok: bool
    required: bool
    detail: str


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    data: bytes | None = None
    headers: dict[str, str] = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            text = resp.read().decode("utf-8", errors="ignore")
        body = json.loads(text) if text.strip() else {}
        return 200, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            body = json.loads(raw) if raw else {"detail": raw}
        except Exception:
            body = {"detail": raw}
        return exc.code, body
    except Exception as exc:
        return 599, {"detail": str(exc)[:400]}


def _call_tool(name: str, arguments: dict[str, Any] | None = None) -> tuple[bool, dict[str, Any]]:
    payload = {
        "server": "boss",
        "name": name,
        "arguments": dict(arguments or {}),
    }
    status, body = _request("POST", "/call", payload)
    if status != 200 or not isinstance(body, dict):
        return False, {"ok": False, "status": f"http_{status}", "error": str(body)[:400]}
    result = body.get("result") if "result" in body else body
    if not isinstance(result, dict):
        return False, {"ok": False, "status": "invalid_result", "error": f"tool={name} result is not object"}
    return True, result


def _run() -> list[StepResult]:
    steps: list[StepResult] = []

    def add(name: str, ok: bool, detail: str, *, required: bool) -> None:
        icon = "OK" if ok else "FAIL"
        required_mark = "required" if required else "optional"
        print(f"[{icon}] {name} ({required_mark}): {detail}")
        steps.append(StepResult(name=name, ok=ok, required=required, detail=detail))

    status, _health_body = _request("GET", "/health")
    add("gateway.health", status == 200, f"status={status}", required=True)
    if status != 200:
        return steps

    status, tools_body = _request("GET", "/tools")
    required_tools = {
        "health",
        "check_login",
        "scan_jobs",
        "greet_job",
        "pull_conversations",
        "reply_conversation",
        "mark_processed",
    }
    found_tools: set[str] = set()
    if status == 200 and isinstance(tools_body, dict):
        raw_tools = tools_body.get("tools")
        if isinstance(raw_tools, list):
            for item in raw_tools:
                if isinstance(item, dict):
                    name = str(item.get("name") or "").strip()
                    if name:
                        found_tools.add(name)
    missing = sorted(required_tools - found_tools)
    add("gateway.tools", status == 200 and not missing, f"status={status}; missing={missing}", required=True)

    _, runtime_health = _call_tool("health", {})
    add(
        "tool.health",
        isinstance(runtime_health, dict) and runtime_health.get("ok") is True,
        f"status={runtime_health.get('status')}; source={runtime_health.get('source')}",
        required=True,
    )

    _, login_result = _call_tool("check_login", {})
    login_status = str(login_result.get("status") or "").strip()
    login_ok_status = {
        "ready",
        "auth_required",
        "risk_blocked",
        "executor_unavailable",
        "executor_error",
        "provider_unavailable",
    }
    login_ok = login_status in login_ok_status
    if STRICT_MODE:
        login_ok = login_status == "ready" and bool(login_result.get("ok"))
    add("tool.check_login", login_ok, f"status={login_status}; ok={login_result.get('ok')}", required=STRICT_MODE)

    _, scan_result = _call_tool(
        "scan_jobs",
        {
            "keyword": KEYWORD,
            "max_items": 5,
            "max_pages": 2,
            "job_type": "all",
        },
    )
    scan_items = scan_result.get("items")
    item_count = len(scan_items) if isinstance(scan_items, list) else 0
    scan_source = str(scan_result.get("source") or "").strip()
    scan_ok = bool(scan_result.get("ok")) and item_count > 0
    if STRICT_MODE:
        # strict 模式必须走真实抓取链路，不能仅靠搜索降级
        scan_ok = scan_ok and scan_source == "boss_mcp_browser_scan"
    add(
        "tool.scan_jobs",
        scan_ok,
        f"status={scan_result.get('status')}; items={item_count}; source={scan_source or '-'}",
        required=True,
    )

    first_job = scan_items[0] if isinstance(scan_items, list) and scan_items else {}
    if not isinstance(first_job, dict):
        first_job = {}

    if not SKIP_WRITE and first_job:
        greet_args = {
            "run_id": f"{RUN_ID_PREFIX}-{int(time.time())}",
            "job_id": str(first_job.get("job_id") or ""),
            "source_url": str(first_job.get("source_url") or ""),
            "job_title": str(first_job.get("title") or ""),
            "company": str(first_job.get("company") or ""),
            "greeting_text": "你好，我想了解这个岗位详情。",
        }
        _, greet_result = _call_tool("greet_job", greet_args)
        greet_status = str(greet_result.get("status") or "").strip()
        greet_ok = greet_status in {"logged", "sent", "clicked", "manual_required", "auth_required", "risk_blocked"}
        if STRICT_MODE:
            greet_ok = greet_status in {"sent", "clicked"}
        add("tool.greet_job", greet_ok, f"status={greet_status}; ok={greet_result.get('ok')}", required=STRICT_MODE)

    _, pull_result = _call_tool(
        "pull_conversations",
        {
            "max_conversations": 5,
            "unread_only": False,
            "fetch_latest_hr": True,
            "chat_tab": "全部",
        },
    )
    conversations = pull_result.get("items")
    conv_count = len(conversations) if isinstance(conversations, list) else 0
    pull_source = str(pull_result.get("source") or "").strip()
    pull_ok = bool(pull_result.get("ok")) and isinstance(conversations, list)
    if STRICT_MODE:
        # strict 模式必须走真实聊天页抓取，不能仅靠本地 inbox 降级
        pull_ok = pull_ok and pull_source == "boss_mcp_browser_chat"
    add(
        "tool.pull_conversations",
        pull_ok,
        f"status={pull_result.get('status')}; conversations={conv_count}; source={pull_source or '-'}",
        required=True,
    )

    first_conv = conversations[0] if isinstance(conversations, list) and conversations else {}
    if not isinstance(first_conv, dict):
        first_conv = {}

    if not SKIP_WRITE and first_conv:
        _, reply_result = _call_tool(
            "reply_conversation",
            {
                "conversation_id": str(first_conv.get("conversation_id") or ""),
                "reply_text": "你好，这是一条 MCP 验收检查消息，可忽略。",
                "profile_id": "default",
                "conversation_hint": {
                    "hr_name": str(first_conv.get("hr_name") or ""),
                    "company": str(first_conv.get("company") or ""),
                    "job_title": str(first_conv.get("job_title") or ""),
                },
            },
        )
        reply_status = str(reply_result.get("status") or "").strip()
        reply_ok = reply_status in {"logged", "sent", "manual_required", "auth_required", "risk_blocked"}
        if STRICT_MODE:
            reply_ok = reply_status == "sent"
        add(
            "tool.reply_conversation",
            reply_ok,
            f"status={reply_status}; ok={reply_result.get('ok')}",
            required=STRICT_MODE,
        )

        _, mark_result = _call_tool(
            "mark_processed",
            {
                "conversation_id": str(first_conv.get("conversation_id") or ""),
                "run_id": f"{RUN_ID_PREFIX}-{int(time.time())}",
                "note": "mcp acceptance",
            },
        )
        mark_status = str(mark_result.get("status") or "").strip()
        add(
            "tool.mark_processed",
            mark_status in {"marked", "manual_required", "auth_required", "risk_blocked"},
            f"status={mark_status}; ok={mark_result.get('ok')}",
            required=False,
        )
    elif SKIP_WRITE:
        add("write.steps", True, "skipped by PULSE_BOSS_MCP_ACCEPTANCE_SKIP_WRITE=true", required=False)
    else:
        add("write.steps", True, "no conversations found; skip reply/mark", required=False)

    return steps


def main() -> None:
    print(
        "Pulse boss MCP acceptance start. "
        f"base_url={BASE_URL}; keyword={KEYWORD}; strict={STRICT_MODE}; skip_write={SKIP_WRITE}"
    )
    started = time.time()
    steps = _run()
    passed = sum(1 for item in steps if item.ok)
    total = len(steps)
    required_failed = [item for item in steps if item.required and not item.ok]
    elapsed = time.time() - started
    print(
        f"\nAcceptance finished: passed={passed}/{total}; "
        f"required_failed={len(required_failed)}; elapsed={elapsed:.1f}s"
    )
    if required_failed:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
