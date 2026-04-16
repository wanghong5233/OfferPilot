from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Any


BASE_URL = os.getenv("API_BASE_URL", "http://127.0.0.1:8010").rstrip("/")


def _timeout_sec() -> float:
    raw = os.getenv("API_TIMEOUT_SEC", "90").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 90.0
    return max(5.0, min(value, 300.0))


TIMEOUT_SEC = _timeout_sec()


@dataclass
class StepResult:
    ok: bool
    name: str
    detail: str


def _request(method: str, path: str, payload: dict[str, Any] | None = None) -> tuple[int, Any]:
    url = f"{BASE_URL}{path}"
    data = None
    headers = {}
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        headers["Content-Type"] = "application/json; charset=utf-8"
    req = urllib.request.Request(url, data=data, headers=headers, method=method.upper())
    try:
        with urllib.request.urlopen(req, timeout=TIMEOUT_SEC) as resp:
            raw = resp.read().decode("utf-8", errors="ignore")
            body = json.loads(raw) if raw else None
            return resp.status, body
    except urllib.error.HTTPError as exc:
        raw = exc.read().decode("utf-8", errors="ignore")
        try:
            body = json.loads(raw) if raw else {"detail": raw}
        except Exception:
            body = {"detail": raw}
        return exc.code, body
    except TimeoutError:
        return 599, {"detail": f"request timeout after {TIMEOUT_SEC:.1f}s", "path": path}
    except urllib.error.URLError as exc:
        return 599, {"detail": f"url error: {exc.reason}", "path": path}
    except Exception as exc:
        return 599, {"detail": f"unexpected request error: {exc}", "path": path}


def _run() -> list[StepResult]:
    results: list[StepResult] = []

    def add(ok: bool, name: str, detail: str) -> None:
        icon = "OK" if ok else "FAIL"
        print(f"[{icon}] {name}: {detail}")
        results.append(StepResult(ok=ok, name=name, detail=detail))

    status, health = _request("GET", "/health")
    if status != 200:
        add(False, "health", f"status={status}, body={health}")
        return results
    add(True, "health", f"backend is reachable; app={health.get('app') if isinstance(health, dict) else '-'}")

    status, hello = _request("GET", "/api/modules/hello/ping")
    hello_ok = status == 200 and isinstance(hello, dict) and hello.get("message") == "pong"
    add(hello_ok, "modules/hello/ping", f"status={status}")

    status, boss_health = _request("GET", "/api/modules/boss_greet/health")
    boss_health_ok = status == 200 and isinstance(boss_health, dict) and boss_health.get("status") in {"ok", "degraded"}
    add(boss_health_ok, "modules/boss_greet/health", f"status={status}")

    status, boss_scan = _request(
        "POST",
        "/api/modules/boss_greet/scan",
        {"keyword": "AI Agent 实习", "max_items": 6, "max_pages": 2},
    )
    boss_scan_ok = status == 200 and isinstance(boss_scan, dict) and isinstance(boss_scan.get("items"), list)
    add(boss_scan_ok, "modules/boss_greet/scan", f"status={status}")

    status, boss_trigger = _request(
        "POST",
        "/api/modules/boss_greet/trigger",
        {"keyword": "AI Agent 实习", "batch_size": 3, "match_threshold": 65},
    )
    boss_trigger_ok = status == 200 and isinstance(boss_trigger, dict) and "greeted" in boss_trigger
    add(boss_trigger_ok, "modules/boss_greet/trigger", f"status={status}")

    status, chat_pull_body = _request(
        "POST",
        "/api/modules/boss_chat/pull",
        {"max_conversations": 8, "unread_only": True, "fetch_latest_hr": True},
    )
    pull_ok = status == 200 and isinstance(chat_pull_body, dict) and "total" in chat_pull_body
    add(pull_ok, "modules/boss_chat/pull", f"status={status}")

    status, chat_process_body = _request(
        "POST",
        "/api/modules/boss_chat/process",
        {
            "max_conversations": 8,
            "unread_only": True,
            "profile_id": "default",
            "notify_on_escalate": False,
            "fetch_latest_hr": True,
        },
    )
    process_ok = (
        status == 200
        and isinstance(chat_process_body, dict)
        and "processed_count" in chat_process_body
        and "new_count" in chat_process_body
    )
    add(process_ok, "modules/boss_chat/process", f"status={status}")

    status, email_ingest_body = _request(
        "POST",
        "/api/modules/email_tracker/process-one",
        {
            "sender": "hr@pulse-agent.dev",
            "subject": "【Pulse】面试邀请：2026-03-20 14:00",
            "body": "你好，邀请你参加一面，请于2026-03-20 14:00线上面试。地点：腾讯会议。联系人：HR小王。",
        },
    )
    ingest_ok = (
        status == 200
        and isinstance(email_ingest_body, dict)
        and isinstance(email_ingest_body.get("classification"), dict)
        and isinstance(email_ingest_body.get("classification", {}).get("email_type"), str)
    )
    add(ingest_ok, "modules/email_tracker/process-one", f"status={status}")

    status, fetch_body = _request(
        "POST",
        "/api/modules/email_tracker/fetch-process",
        {"max_items": 5, "mark_seen": False},
    )
    fetch_ok = status == 200 and isinstance(fetch_body, dict) and "fetched_count" in fetch_body
    add(fetch_ok, "modules/email_tracker/fetch-process", f"status={status}")

    status, hb_status = _request("GET", "/api/modules/email_tracker/heartbeat/status")
    hb_status_ok = status == 200 and isinstance(hb_status, dict) and "running" in hb_status
    add(hb_status_ok, "modules/email_tracker/heartbeat/status", f"status={status}")

    status, hb_start = _request("POST", "/api/modules/email_tracker/heartbeat/start")
    hb_start_ok = status == 200 and isinstance(hb_start, dict) and bool(hb_start.get("ok"))
    add(hb_start_ok, "modules/email_tracker/heartbeat/start", f"status={status}")

    status, hb_trigger = _request("POST", "/api/modules/email_tracker/heartbeat/trigger")
    hb_trigger_ok = (
        status == 200
        and isinstance(hb_trigger, dict)
        and isinstance(hb_trigger.get("result"), dict)
        and "fetched_count" in hb_trigger.get("result", {})
    )
    add(hb_trigger_ok, "modules/email_tracker/heartbeat/trigger", f"status={status}")

    status, hb_stop = _request("POST", "/api/modules/email_tracker/heartbeat/stop")
    hb_stop_ok = status == 200 and isinstance(hb_stop, dict) and bool(hb_stop.get("ok"))
    add(hb_stop_ok, "modules/email_tracker/heartbeat/stop", f"status={status}")

    return results


def main() -> None:
    print(f"Pulse demo walkthrough starts. base_url={BASE_URL}")
    started_at = time.time()
    results = _run()
    total = len(results)
    passed = sum(1 for item in results if item.ok)
    elapsed = time.time() - started_at
    print(
        f"\nDemo walkthrough finished: passed {passed}/{total} steps in {elapsed:.1f}s."
    )
    if passed != total:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
