from __future__ import annotations

import json
import os
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from datetime import datetime
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

    stamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    resume_source_id = f"demo_resume_{stamp}"

    status, health = _request("GET", "/health")
    if status != 200:
        add(False, "health", f"status={status}, body={health}")
        return results
    add(True, "health", "backend is reachable")

    status, index_body = _request(
        "POST",
        "/api/resume/index",
        {
            "source_id": resume_source_id,
            "resume_text": (
                "教育背景\n"
                "- 211硕士，研究方向：大模型应用与Agent系统\n\n"
                "项目经历\n"
                "- OpenClaw + LangGraph + FastAPI 求职Agent项目落地\n"
                "- ChromaDB 做RAG召回，Playwright 做BOSS扫描与表单填充\n"
            ),
        },
    )
    add(status == 200, "resume/index", f"status={status}, source_id={resume_source_id}")

    status, profile_get = _request("GET", "/api/profile?profile_id=default")
    profile_ok = status == 200 and isinstance(profile_get, dict) and isinstance(profile_get.get("profile"), dict)
    add(profile_ok, "profile/get", f"status={status}")

    status, profile_put = _request(
        "PUT",
        "/api/profile",
        {
            "profile_id": "default",
            "profile": {
                "personal": {"education": "211硕士在读（2027届）", "major": "计算机科学与技术"},
                "job_preference": {
                    "work_cities": ["深圳", "广州"],
                    "expected_daily_salary": "220-300元/天",
                    "internship_duration": "3-6个月",
                    "available_days_per_week": 5,
                    "earliest_start_date": "一周内到岗",
                },
            },
        },
    )
    profile_save_ok = status == 200 and isinstance(profile_put, dict) and profile_put.get("profile_id") == "default"
    add(profile_save_ok, "profile/put", f"status={status}")

    status, reply_preview_body = _request(
        "POST",
        "/api/boss/chat/reply-preview",
        {
            "hr_message": "你好，请问你的期望日薪是多少？工作地点是哪里？",
            "profile_id": "default",
            "company": "OfferPilot Labs",
            "job_title": "AI Agent Intern",
            "notify_on_escalate": False,
        },
    )
    reply_preview_ok = (
        status == 200
        and isinstance(reply_preview_body, dict)
        and isinstance(reply_preview_body.get("action"), str)
    )
    add(reply_preview_ok, "boss/chat/reply-preview", f"status={status}")

    status, chat_pull_body = _request(
        "POST",
        "/api/boss/chat/pull",
        {"max_conversations": 8, "unread_only": True, "fetch_latest_hr": True},
    )
    if status == 503:
        add(True, "boss/chat/pull", "status=503 (Playwright/login not ready in current env)")
    else:
        pull_ok = status == 200 and isinstance(chat_pull_body, dict) and "total" in chat_pull_body
        add(pull_ok, "boss/chat/pull", f"status={status}")

    status, chat_process_body = _request(
        "POST",
        "/api/boss/chat/process",
        {
            "max_conversations": 8,
            "unread_only": True,
            "profile_id": "default",
            "notify_on_escalate": False,
            "fetch_latest_hr": True,
        },
    )
    if status == 503:
        add(True, "boss/chat/process", "status=503 (Playwright/login not ready in current env)")
    else:
        process_ok = (
            status == 200
            and isinstance(chat_process_body, dict)
            and "processed_count" in chat_process_body
            and "new_count" in chat_process_body
        )
        add(process_ok, "boss/chat/process", f"status={status}")

    status, chat_hb_body = _request(
        "POST",
        "/api/boss/chat/heartbeat/trigger",
        {
            "max_conversations": 8,
            "unread_only": True,
            "profile_id": "default",
            "notify_on_escalate": False,
            "fetch_latest_hr": True,
            "notify_channel_on_hits": False,
        },
    )
    if status == 503:
        add(
            True,
            "boss/chat/heartbeat/trigger",
            "status=503 (Playwright/login not ready in current env)",
        )
    else:
        hb_ok = (
            status == 200
            and isinstance(chat_hb_body, dict)
            and "summary" in chat_hb_body
            and "process" in chat_hb_body
        )
        add(hb_ok, "boss/chat/heartbeat/trigger", f"status={status}")

    jd_text = (
        "AI Agent Intern, need Python, LangGraph, RAG, MCP, Playwright. "
        "Need practical delivery and reliability mindset."
    )
    status, analyze_body = _request("POST", "/api/jd/analyze", {"jd_text": jd_text})
    if status == 200 and isinstance(analyze_body, dict):
        add(
            True,
            "jd/analyze",
            f"title={analyze_body.get('title')}; score={analyze_body.get('match_score')}",
        )
    else:
        add(False, "jd/analyze", f"status={status}, body={analyze_body}")

    status, jobs_body = _request("GET", "/api/jobs/recent?limit=1")
    job_id = None
    if status == 200 and isinstance(jobs_body, list) and jobs_body:
        first = jobs_body[0]
        if isinstance(first, dict):
            job_id = str(first.get("id") or "")
    add(bool(job_id), "jobs/recent", f"status={status}, picked_job_id={job_id or '-'}")

    thread_id = None
    if job_id:
        status, gen_body = _request(
            "POST",
            "/api/material/generate",
            {"job_id": job_id, "resume_version": resume_source_id},
        )
        if status == 200 and isinstance(gen_body, dict):
            material_status = str(gen_body.get("status") or "")
            thread_id = str(gen_body.get("thread_id") or "") if gen_body.get("thread_id") else None
            add(True, "material/generate", f"status={material_status}, thread_id={thread_id or '-'}")
            if material_status == "pending_review" and thread_id:
                status, review_body = _request(
                    "POST",
                    "/api/material/review",
                    {"thread_id": thread_id, "decision": "approve"},
                )
                add(status == 200, "material/review", f"status={status}, body={review_body}")
                status, export_body = _request(
                    "POST",
                    "/api/material/export",
                    {"thread_id": thread_id, "format": "txt"},
                )
                if status == 200 and isinstance(export_body, dict):
                    add(True, "material/export", f"file={export_body.get('file_name')}")
                else:
                    add(False, "material/export", f"status={status}, body={export_body}")
        else:
            add(False, "material/generate", f"status={status}, body={gen_body}")

    status, email_ingest_body = _request(
        "POST",
        "/api/email/ingest",
        {
            "sender": "hr@offerpilot.ai",
            "subject": "【OfferPilot】面试邀请：2026-03-20 14:00",
            "body": "你好，邀请你参加一面，请于2026-03-20 14:00线上面试。地点：腾讯会议。联系人：HR小王。",
        },
    )
    ingest_ok = (
        status == 200
        and isinstance(email_ingest_body, dict)
        and isinstance(email_ingest_body.get("classification"), dict)
        and isinstance(email_ingest_body.get("classification", {}).get("email_type"), str)
    )
    add(ingest_ok, "email/ingest", f"status={status}")

    status, schedules_body = _request("GET", "/api/schedules/upcoming?limit=5&days=14")
    schedules_ok = status == 200 and isinstance(schedules_body, list)
    add(schedules_ok, "schedules/upcoming", f"status={status}, count={len(schedules_body) if isinstance(schedules_body, list) else 0}")

    status, email_hb_body = _request("POST", "/api/email/heartbeat/trigger")
    if status == 503:
        add(True, "email/heartbeat/trigger", "status=503 (IMAP env not configured in current env)")
    else:
        email_hb_ok = (
            status == 200
            and isinstance(email_hb_body, dict)
            and "fetched_count" in email_hb_body
            and "processed_count" in email_hb_body
            and "schedule_reminders" in email_hb_body
            and "upcoming_schedules" in email_hb_body
        )
        add(email_hb_ok, "email/heartbeat/trigger", f"status={status}")

    status, intel_body = _request(
        "POST",
        "/api/company/intel",
        {
            "company": "OfferPilot Labs",
            "role_title": "AI Agent Intern",
            "jd_text": jd_text,
            "focus_keywords": ["技术栈", "面试流程"],
            "max_results": 4,
            "include_search": False,
        },
    )
    if status == 200 and isinstance(intel_body, dict):
        add(
            True,
            "company/intel",
            f"tech_stack={len(intel_body.get('tech_stack') or [])}; confidence={intel_body.get('confidence')}",
        )
    else:
        add(False, "company/intel", f"status={status}, body={intel_body}")

    status, prep_body = _request(
        "POST",
        "/api/interview/prep",
        {
            "company": "OfferPilot Labs",
            "role_title": "AI Agent Intern",
            "jd_text": jd_text,
            "use_company_intel": False,
            "question_count": 8,
        },
    )
    if status == 200 and isinstance(prep_body, dict):
        add(True, "interview/prep", f"questions={len(prep_body.get('questions') or [])}")
    else:
        add(False, "interview/prep", f"status={status}, body={prep_body}")

    status, issue_body = _request(
        "POST",
        "/api/security/token/issue",
        {"action": "submit_application", "purpose": "demo_walkthrough", "expire_minutes": 10},
    )
    token = None
    if status == 200 and isinstance(issue_body, dict):
        token = str(issue_body.get("token") or "")
    add(bool(token), "security/token/issue", f"status={status}")
    if token:
        status, consume_body = _request(
            "POST",
            "/api/security/token/consume",
            {"token": token, "action": "submit_application"},
        )
        first_ok = status == 200 and isinstance(consume_body, dict) and bool(consume_body.get("valid"))
        add(first_ok, "security/token/consume#1", f"status={status}, body={consume_body}")
        status, replay_body = _request(
            "POST",
            "/api/security/token/consume",
            {"token": token, "action": "submit_application"},
        )
        replay_blocked = status == 200 and isinstance(replay_body, dict) and not bool(replay_body.get("valid"))
        add(replay_blocked, "security/token/consume#2", f"status={status}, body={replay_body}")

    status, budget1 = _request(
        "POST",
        "/api/security/budget/check",
        {
            "session_id": "demo-session",
            "tool_type": "browser",
            "limit": 2,
            "consume": 1,
            "dry_run": False,
        },
    )
    ok1 = status == 200 and isinstance(budget1, dict) and bool(budget1.get("allowed"))
    add(ok1, "security/budget/check#1", f"status={status}, body={budget1}")
    status, budget2 = _request(
        "POST",
        "/api/security/budget/check",
        {
            "session_id": "demo-session",
            "tool_type": "browser",
            "limit": 2,
            "consume": 2,
            "dry_run": False,
        },
    )
    blocked = status == 200 and isinstance(budget2, dict) and not bool(budget2.get("allowed"))
    add(blocked, "security/budget/check#2", f"status={status}, body={budget2}")
    _request(
        "POST",
        "/api/security/budget/reset",
        {"session_id": "demo-session", "tool_type": "browser"},
    )

    status, timeline_body = _request("GET", "/api/actions/timeline?limit=5")
    timeline_count = len(timeline_body) if status == 200 and isinstance(timeline_body, list) else 0
    add(status == 200, "actions/timeline", f"status={status}, count={timeline_count}")

    status, metrics_body = _request("GET", "/api/eval/metrics?window_days=14")
    add(status == 200, "eval/metrics", f"status={status}, body_keys={list((metrics_body or {}).keys())[:4]}")

    return results


def main() -> None:
    print(f"OfferPilot demo walkthrough starts. base_url={BASE_URL}")
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
