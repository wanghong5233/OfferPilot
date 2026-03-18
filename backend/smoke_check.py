from __future__ import annotations

from fastapi.testclient import TestClient
from urllib.parse import quote

from app.main import app


def main() -> None:
    client = TestClient(app)

    health = client.get("/health")
    assert health.status_code == 200, f"/health failed: {health.status_code}"

    resume_source_id = "smoke_resume"
    indexed = client.post(
        "/api/resume/index",
        json={
            "source_id": resume_source_id,
            "resume_text": (
                "教育背景\n"
                "- 211硕士\n\n"
                "项目经历\n"
                "- Python + FastAPI + LangGraph 项目实践\n"
                "- ChromaDB + RAG 检索实现\n"
            ),
        },
    )
    assert indexed.status_code == 200, f"/api/resume/index failed: {indexed.status_code}"
    indexed_body = indexed.json()
    assert indexed_body.get("source_id") == resume_source_id, "resume source_id mismatch"

    resume_source = client.get(f"/api/resume/source/{resume_source_id}")
    assert resume_source.status_code == 200, f"/api/resume/source failed: {resume_source.status_code}"
    assert "resume_text" in resume_source.json(), "missing resume_text in source response"

    profile_get = client.get("/api/profile?profile_id=default")
    assert profile_get.status_code == 200, f"/api/profile GET failed: {profile_get.status_code}"
    profile_body = profile_get.json()
    assert isinstance(profile_body.get("profile"), dict), "profile GET should return dict profile"

    profile_put = client.put(
        "/api/profile",
        json={
            "profile_id": "default",
            "profile": {
                "personal": {"education": "211硕士在读（2027届）", "major": "计算机"},
                "job_preference": {
                    "expected_daily_salary": "220-300元/天",
                    "work_cities": ["深圳", "广州"],
                    "internship_duration": "3-6个月",
                    "available_days_per_week": 5,
                    "earliest_start_date": "一周内到岗",
                },
            },
        },
    )
    assert profile_put.status_code == 200, f"/api/profile PUT failed: {profile_put.status_code}"
    assert profile_put.json().get("profile_id") == "default", "profile PUT profile_id mismatch"

    boss_reply_preview = client.post(
        "/api/boss/chat/reply-preview",
        json={
            "hr_message": "你好，请问你的期望日薪是多少？工作地点在哪？",
            "profile_id": "default",
            "company": "OfferPilot Labs",
            "job_title": "AI Agent Intern",
            "notify_on_escalate": False,
        },
    )
    assert boss_reply_preview.status_code == 200, (
        f"/api/boss/chat/reply-preview failed: {boss_reply_preview.status_code}"
    )
    boss_reply_body = boss_reply_preview.json()
    assert boss_reply_body.get("intent"), "boss reply preview missing intent"
    assert boss_reply_body.get("action") in {"send_resume", "reply_from_profile", "notify_user", "ignore"}, (
        "boss reply preview action invalid"
    )

    analyzed = client.post(
        "/api/jd/analyze",
        json={"jd_text": "AI Agent intern. Need Python, LangGraph, RAG, MCP, Playwright."},
    )
    assert analyzed.status_code == 200, f"/api/jd/analyze failed: {analyzed.status_code}"
    body = analyzed.json()
    assert "resume_evidence" in body, "missing resume_evidence in analyze response"

    recent = client.get("/api/jobs/recent?limit=1")
    assert recent.status_code == 200, f"/api/jobs/recent failed: {recent.status_code}"
    jobs = recent.json()

    timeline = client.get("/api/actions/timeline?limit=20")
    assert timeline.status_code == 200, f"/api/actions/timeline failed: {timeline.status_code}"
    assert isinstance(timeline.json(), list), "actions timeline should return list"

    metrics = client.get("/api/eval/metrics?window_days=14")
    assert metrics.status_code == 200, f"/api/eval/metrics failed: {metrics.status_code}"
    metrics_body = metrics.json()
    assert "window_days" in metrics_body and "evaluated_at" in metrics_body, "metrics schema invalid"

    intel_resp = client.post(
        "/api/company/intel",
        json={
            "company": "OfferPilot Labs",
            "role_title": "AI Agent Intern",
            "jd_text": "Need Python, LangGraph, RAG, MCP",
            "focus_keywords": ["技术栈", "面试流程"],
            "max_results": 4,
            "include_search": False,
        },
    )
    assert intel_resp.status_code == 200, f"/api/company/intel failed: {intel_resp.status_code}"
    intel_body = intel_resp.json()
    assert intel_body.get("company") == "OfferPilot Labs", "company intel company mismatch"
    assert isinstance(intel_body.get("tech_stack"), list), "company intel tech_stack invalid"

    prep_resp = client.post(
        "/api/interview/prep",
        json={
            "company": "OfferPilot Labs",
            "role_title": "AI Agent Intern",
            "jd_text": "Need Python, LangGraph, RAG, MCP, Playwright",
            "use_company_intel": False,
            "question_count": 6,
        },
    )
    assert prep_resp.status_code == 200, f"/api/interview/prep failed: {prep_resp.status_code}"
    prep_body = prep_resp.json()
    assert len(prep_body.get("questions", [])) >= 6, "interview prep question count invalid"

    token_issue = client.post(
        "/api/security/token/issue",
        json={"action": "submit_application", "purpose": "smoke", "expire_minutes": 5},
    )
    assert token_issue.status_code == 200, (
        f"/api/security/token/issue failed: {token_issue.status_code}"
    )
    token_body = token_issue.json()
    assert token_body.get("token"), "security token issue missing token"

    token_consume = client.post(
        "/api/security/token/consume",
        json={"token": token_body["token"], "action": "submit_application"},
    )
    assert token_consume.status_code == 200, (
        f"/api/security/token/consume failed: {token_consume.status_code}"
    )
    assert token_consume.json().get("valid") is True, "security token first consume should be valid"

    token_replay = client.post(
        "/api/security/token/consume",
        json={"token": token_body["token"], "action": "submit_application"},
    )
    assert token_replay.status_code == 200, (
        f"/api/security/token/consume replay failed: {token_replay.status_code}"
    )
    assert token_replay.json().get("valid") is False, "security token replay should be invalid"

    budget_first = client.post(
        "/api/security/budget/check",
        json={
            "session_id": "smoke-session",
            "tool_type": "browser",
            "limit": 2,
            "consume": 1,
            "dry_run": False,
        },
    )
    assert budget_first.status_code == 200, (
        f"/api/security/budget/check failed: {budget_first.status_code}"
    )
    budget_first_body = budget_first.json()
    assert budget_first_body.get("allowed") is True, "budget first consume should be allowed"

    budget_second = client.post(
        "/api/security/budget/check",
        json={
            "session_id": "smoke-session",
            "tool_type": "browser",
            "limit": 2,
            "consume": 2,
            "dry_run": False,
        },
    )
    assert budget_second.status_code == 200, (
        f"/api/security/budget/check second failed: {budget_second.status_code}"
    )
    assert budget_second.json().get("allowed") is False, "budget exceed should be blocked"

    budget_reset = client.post(
        "/api/security/budget/reset",
        json={"session_id": "smoke-session", "tool_type": "browser"},
    )
    assert budget_reset.status_code == 200, (
        f"/api/security/budget/reset failed: {budget_reset.status_code}"
    )
    assert budget_reset.json().get("ok") is True, "budget reset should return ok=true"

    if not jobs:
        print("Smoke check passed (no jobs available for material flow).")
        return

    job_id = jobs[0]["id"]
    generated = client.post(
        "/api/material/generate",
        json={"job_id": job_id, "resume_version": resume_source_id},
    )
    assert generated.status_code == 200, f"/api/material/generate failed: {generated.status_code}"
    generated_body = generated.json()
    status = generated_body.get("status")
    assert status in {"pending_review", "skipped_low_match"}, f"unexpected generate status: {status}"

    if status == "skipped_low_match":
        print("Smoke check passed (material generation skipped by low match score).")
        return

    thread_id = generated_body.get("thread_id")
    assert isinstance(thread_id, str) and thread_id, "missing thread_id after material generation"

    pending = client.get("/api/material/pending")
    assert pending.status_code == 200, f"/api/material/pending failed: {pending.status_code}"

    regenerated = client.post(
        "/api/material/review",
        json={
            "thread_id": thread_id,
            "decision": "regenerate",
            "feedback": "Please strengthen measurable impact and reliability details.",
        },
    )
    assert regenerated.status_code == 200, f"regenerate failed: {regenerated.status_code}"
    assert regenerated.json().get("status") == "regenerated", "unexpected regenerate status"

    approved = client.post(
        "/api/material/review",
        json={"thread_id": thread_id, "decision": "approve"},
    )
    assert approved.status_code == 200, f"approve failed: {approved.status_code}"
    assert approved.json().get("status") == "approved", "unexpected approve status"

    exported = client.post(
        "/api/material/export",
        json={"thread_id": thread_id, "format": "txt"},
    )
    assert exported.status_code == 200, f"export failed: {exported.status_code}"
    assert exported.json().get("file_name"), "missing export file name"

    autofill_preview = client.post(
        "/api/form/autofill/preview",
        json={
            "html": """
            <form>
              <label for='candidateName'>姓名</label>
              <input id='candidateName' name='name' />
              <label for='candidateEmail'>邮箱</label>
              <input id='candidateEmail' type='email' />
              <textarea name='projectSummary' placeholder='请填写项目经历'></textarea>
            </form>
            """,
            "profile": {
                "name": "张三",
                "email": "zhangsan@example.com",
                "project_summary": "负责 Agent 工作流与 RAG 检索系统开发。",
            },
        },
    )
    assert autofill_preview.status_code == 200, (
        f"autofill preview failed: {autofill_preview.status_code}"
    )
    preview_body = autofill_preview.json()
    assert preview_body.get("mapped_fields", 0) >= 2, "autofill mapping too low"

    sample_form_html = """
    <form>
      <label for='candidateName'>姓名</label>
      <input id='candidateName' name='name' />
      <label for='candidateEmail'>邮箱</label>
      <input id='candidateEmail' type='email' />
      <textarea name='projectSummary' placeholder='请填写项目经历'></textarea>
    </form>
    """
    data_url = f"data:text/html,{quote(sample_form_html)}"
    profile = {
        "name": "张三",
        "email": "zhangsan@example.com",
        "project_summary": "负责 Agent 工作流与 RAG 检索系统开发。",
    }

    url_preview = client.post(
        "/api/form/autofill/preview-url",
        json={"url": data_url, "profile": profile},
    )
    assert url_preview.status_code in {200, 503}, (
        f"autofill preview-url unexpected status: {url_preview.status_code}"
    )
    if url_preview.status_code == 200:
        assert url_preview.json().get("mapped_fields", 0) >= 2, "preview-url mapping too low"

    fill_guard = client.post(
        "/api/form/autofill/fill-url",
        json={"url": data_url, "profile": profile, "confirm_fill": False},
    )
    assert fill_guard.status_code == 400, "fill-url guard should require confirm_fill=true"

    url_fill = client.post(
        "/api/form/autofill/fill-url",
        json={"url": data_url, "profile": profile, "confirm_fill": True, "max_actions": 8},
    )
    assert url_fill.status_code in {200, 503}, (
        f"autofill fill-url unexpected status: {url_fill.status_code}"
    )
    if url_fill.status_code == 200:
        body = url_fill.json()
        assert body.get("attempted_fields", 0) >= 1, "fill-url attempted_fields invalid"

    form_fill_start = client.post(
        "/api/form/fill/start",
        json={"url": data_url, "profile": profile, "max_actions": 6},
    )
    assert form_fill_start.status_code in {200, 503}, (
        f"form fill start unexpected status: {form_fill_start.status_code}"
    )
    if form_fill_start.status_code == 200:
        start_body = form_fill_start.json()
        assert start_body.get("status") == "pending_review", "form fill start status invalid"
        fill_thread_id = start_body.get("thread_id")
        assert isinstance(fill_thread_id, str) and fill_thread_id, "missing form fill thread_id"

        pending_fill = client.get("/api/form/fill/pending")
        assert pending_fill.status_code == 200, f"/api/form/fill/pending failed: {pending_fill.status_code}"

        fill_detail = client.get(f"/api/form/fill/thread/{fill_thread_id}")
        assert fill_detail.status_code == 200, f"/api/form/fill/thread failed: {fill_detail.status_code}"

        form_fill_review = client.post(
            "/api/form/fill/review",
            json={
                "thread_id": fill_thread_id,
                "decision": "approve",
                "feedback": "smoke auto approve",
                "max_actions": 6,
            },
        )
        assert form_fill_review.status_code in {200, 503}, (
            f"form fill review unexpected status: {form_fill_review.status_code}"
        )
        if form_fill_review.status_code == 200:
            review_body = form_fill_review.json()
            assert review_body.get("status") in {"approved", "rejected"}, "form fill review status invalid"

    email_ingest = client.post(
        "/api/email/ingest",
        json={
            "sender": "hr@offerpilot.ai",
            "subject": "【OfferPilot】面试邀请：2026-03-20 14:00",
            "body": "你好，邀请你参加一面，请于2026-03-20 14:00线上面试。",
        },
    )
    assert email_ingest.status_code == 200, f"/api/email/ingest failed: {email_ingest.status_code}"
    email_body = email_ingest.json()
    assert email_body.get("classification", {}).get("email_type"), "missing email_type"

    email_recent = client.get("/api/email/recent?limit=5")
    assert email_recent.status_code == 200, f"/api/email/recent failed: {email_recent.status_code}"
    assert isinstance(email_recent.json(), list), "email recent should return list"

    schedule_upcoming = client.get("/api/schedules/upcoming?limit=5&days=14")
    assert schedule_upcoming.status_code == 200, (
        f"/api/schedules/upcoming failed: {schedule_upcoming.status_code}"
    )
    assert isinstance(schedule_upcoming.json(), list), "upcoming schedules should return list"

    email_fetch = client.post("/api/email/fetch", json={"max_items": 3, "mark_seen": False})
    assert email_fetch.status_code in {200, 503}, f"/api/email/fetch unexpected: {email_fetch.status_code}"
    if email_fetch.status_code == 200:
        fetch_body = email_fetch.json()
        assert "fetched_count" in fetch_body and "processed_count" in fetch_body, "email fetch schema invalid"

    hb_status = client.get("/api/email/heartbeat/status")
    assert hb_status.status_code == 200, f"/api/email/heartbeat/status failed: {hb_status.status_code}"
    hb_body = hb_status.json()
    assert "running" in hb_body and "interval_sec" in hb_body, "heartbeat status schema invalid"

    hb_start = client.post("/api/email/heartbeat/start")
    assert hb_start.status_code == 200, f"/api/email/heartbeat/start failed: {hb_start.status_code}"

    hb_trigger = client.post("/api/email/heartbeat/trigger")
    assert hb_trigger.status_code in {200, 503}, (
        f"/api/email/heartbeat/trigger unexpected: {hb_trigger.status_code}"
    )
    if hb_trigger.status_code == 200:
        hb_trigger_body = hb_trigger.json()
        assert "notification_sent" in hb_trigger_body, "heartbeat trigger missing notification flag"
        assert "schedule_reminders" in hb_trigger_body, "heartbeat trigger missing schedule reminders"
        assert "upcoming_schedules" in hb_trigger_body, "heartbeat trigger missing upcoming schedules"

    hb_notify_test = client.post(
        "/api/email/heartbeat/notify-test",
        json={"message": "smoke notify test"},
    )
    assert hb_notify_test.status_code == 200, (
        f"/api/email/heartbeat/notify-test failed: {hb_notify_test.status_code}"
    )
    assert "sent" in hb_notify_test.json(), "notify-test schema invalid"

    hb_stop = client.post("/api/email/heartbeat/stop")
    assert hb_stop.status_code == 200, f"/api/email/heartbeat/stop failed: {hb_stop.status_code}"

    boss_scan = client.post(
        "/api/boss/scan",
        json={"keyword": "AI Agent 实习", "max_items": 3, "max_pages": 2},
    )
    assert boss_scan.status_code in {200, 503}, f"boss scan unexpected status: {boss_scan.status_code}"

    boss_chat_pull = client.post(
        "/api/boss/chat/pull",
        json={"max_conversations": 5, "unread_only": True, "fetch_latest_hr": True},
    )
    assert boss_chat_pull.status_code in {200, 503}, (
        f"boss chat pull unexpected status: {boss_chat_pull.status_code}"
    )
    if boss_chat_pull.status_code == 200:
        body = boss_chat_pull.json()
        assert "total" in body and "unread_total" in body, "boss chat pull schema invalid"

    boss_chat_process = client.post(
        "/api/boss/chat/process",
        json={
            "max_conversations": 5,
            "unread_only": True,
            "profile_id": "default",
            "notify_on_escalate": False,
            "fetch_latest_hr": True,
        },
    )
    assert boss_chat_process.status_code in {200, 503}, (
        f"boss chat process unexpected status: {boss_chat_process.status_code}"
    )
    if boss_chat_process.status_code == 200:
        body = boss_chat_process.json()
        assert "processed_count" in body and "new_count" in body, "boss chat process schema invalid"

    boss_chat_hb = client.post(
        "/api/boss/chat/heartbeat/trigger",
        json={
            "max_conversations": 5,
            "unread_only": True,
            "profile_id": "default",
            "notify_on_escalate": False,
            "fetch_latest_hr": True,
            "notify_channel_on_hits": False,
        },
    )
    assert boss_chat_hb.status_code in {200, 503}, (
        f"boss chat heartbeat unexpected status: {boss_chat_hb.status_code}"
    )
    if boss_chat_hb.status_code == 200:
        body = boss_chat_hb.json()
        assert "summary" in body and "process" in body, "boss chat heartbeat schema invalid"

    print("Smoke check passed.")


if __name__ == "__main__":
    main()
