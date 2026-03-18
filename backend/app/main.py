import logging
import os

import asyncio

from fastapi import Body, FastAPI, File, HTTPException, UploadFile
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import FileResponse
from starlette.responses import StreamingResponse

from .boss_chat_service import default_user_profile, merge_profile, preview_boss_chat_reply
from .boss_chat_workflow import run_boss_chat_copilot_workflow
from .boss_scan import greet_matching_jobs, pull_boss_chat_conversations
from .boss_workflow import run_boss_scan_workflow
from .company_intel_service import generate_company_intel
from .email_fetch import fetch_unread_emails
from .email_heartbeat import EmailHeartbeatManager
from .email_notify import notify_daily_summary, send_channel_notification
from .email_workflow import extract_schedule_candidate, run_email_workflow
from .form_autofill import fill_form_autofill_url, preview_form_autofill, preview_form_autofill_url
from .form_fill_workflow import resume_form_fill_workflow, start_form_fill_workflow
from .interview_prep_service import generate_interview_prep
from .material_export import export_material_thread, resolve_export_file
from .material_service import build_material_summary, extract_skills_from_job
from .material_workflow import resume_material_workflow, start_material_workflow
from .schemas import (
    ActionTimelineItem,
    AgentEvalMetricsResponse,
    BossGreetTriggerRequest,
    BossGreetTriggerResponse,
    BossScanRequest,
    BossScanResponse,
    BossChatPullRequest,
    BossChatPullResponse,
    BossChatHeartbeatTriggerRequest,
    BossChatHeartbeatTriggerResponse,
    BossChatProcessRequest,
    BossChatProcessResponse,
    BossChatReplyPreviewRequest,
    BossChatReplyPreviewResponse,
    CompanyIntelRequest,
    CompanyIntelResponse,
    EmailHeartbeatControlResponse,
    EmailHeartbeatNotifyTestRequest,
    EmailHeartbeatNotifyTestResponse,
    EmailHeartbeatStatusResponse,
    EmailHeartbeatTriggerResponse,
    EmailEventItem,
    EmailFetchItem,
    EmailFetchRequest,
    EmailFetchResponse,
    EmailIngestRequest,
    EmailIngestResponse,
    FormAutofillFillRequest,
    FormAutofillFillResponse,
    FormAutofillField,
    FormAutofillPreviewRequest,
    FormAutofillPreviewResponse,
    FormAutofillUrlPreviewRequest,
    FormAutofillUrlPreviewResponse,
    FormFillPendingItem,
    FormFillReviewRequest,
    FormFillReviewResponse,
    FormFillStartRequest,
    FormFillStartResponse,
    FormFillThreadDetail,
    FormFillThreadPreview,
    InterviewPrepRequest,
    InterviewPrepResponse,
    JDAnalyzeRequest,
    JDAnalyzeResponse,
    JobListItem,
    MaterialExportRequest,
    MaterialExportResponse,
    MaterialGenerateRequest,
    MaterialGenerateResponse,
    MaterialReviewRequest,
    MaterialReviewResponse,
    MaterialThreadDetail,
    PendingMaterialItem,
    ResumeIndexRequest,
    ResumeIndexResponse,
    ResumeSourceResponse,
    ScheduleEventItem,
    SecurityTokenConsumeRequest,
    SecurityTokenConsumeResponse,
    SecurityTokenIssueRequest,
    SecurityTokenIssueResponse,
    ToolBudgetCheckRequest,
    ToolBudgetCheckResponse,
    ToolBudgetResetRequest,
    ToolBudgetResetResponse,
    UserProfileResponse,
    UserProfileUpsertRequest,
)
from .storage import (
    check_tool_budget,
    consume_security_token,
    create_application_record,
    get_form_fill_thread,
    get_agent_eval_metrics,
    get_job_detail,
    get_material_thread,
    get_recent_jobs,
    get_user_profile,
    get_resume_source,
    list_action_timeline,
    list_recent_email_events,
    list_due_schedule_reminders,
    list_upcoming_schedules,
    list_pending_form_fill_threads,
    list_pending_material_threads,
    log_action,
    persist_jd_analysis,
    reset_tool_budget,
    upsert_user_profile,
    issue_security_token,
    upsert_material_thread,
    upsert_form_fill_thread,
    upsert_resume_source,
    persist_email_event,
    mark_schedule_reminded,
    upsert_schedule_event,
)
from .vector_store import index_resume_text, query_similar_jds, upsert_jd_history
from .workflow import run_jd_analysis

logger = logging.getLogger(__name__)

app = FastAPI(title="OfferPilot API", version="0.1.0")

_cors_origins = os.getenv(
    "CORS_ORIGINS",
    "http://localhost:3000,http://127.0.0.1:3000",
)
app.add_middleware(
    CORSMiddleware,
    allow_origins=[origin.strip() for origin in _cors_origins.split(",") if origin.strip()],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)

from .agent_events import AgentEvent, get_history, subscribe, unsubscribe


@app.get("/api/agent/events")
async def agent_events_sse():
    """SSE endpoint streaming real-time Agent events to the frontend."""

    async def _generate():
        q = subscribe()
        try:
            for evt in get_history(30):
                yield f"data: {evt.model_dump_json()}\n\n"
            while True:
                try:
                    evt: AgentEvent = await asyncio.wait_for(q.get(), timeout=15.0)
                    yield f"data: {evt.model_dump_json()}\n\n"
                except asyncio.TimeoutError:
                    yield ": keepalive\n\n"
        except asyncio.CancelledError:
            pass
        finally:
            unsubscribe(q)

    return StreamingResponse(
        _generate(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


@app.get("/api/agent/events/history")
def agent_events_history(limit: int = 50):
    """Return recent Agent events (for non-SSE clients)."""
    return [evt.model_dump() for evt in get_history(limit)]


_EMAIL_HEARTBEAT: EmailHeartbeatManager | None = None


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.getenv(name, "true" if default else "false").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def _env_int(name: str, default: int, *, min_value: int, max_value: int) -> int:
    raw = os.getenv(name, str(default)).strip()
    try:
        value = int(raw)
    except ValueError:
        value = default
    return max(min_value, min(value, max_value))


@app.get("/health")
def health() -> dict:
    from .boss_scan import get_browser_health
    from .production_guard import guard_stats
    return {
        "status": "ok",
        "browser": get_browser_health(),
        "guard": guard_stats(),
    }


@app.post("/api/jd/analyze", response_model=JDAnalyzeResponse)
def analyze_jd(payload: JDAnalyzeRequest) -> JDAnalyzeResponse:
    result = run_jd_analysis(payload.jd_text)
    similar_jobs = query_similar_jds(payload.jd_text, top_k=3)
    result = result.model_copy(update={"similar_jobs": similar_jobs})
    job_id = persist_jd_analysis(payload.jd_text, result)
    if job_id:
        upsert_jd_history(
            doc_id=job_id,
            jd_text=payload.jd_text,
            title=result.title,
            company=result.company,
            match_score=result.match_score,
        )
    return result


@app.get("/api/jobs/recent", response_model=list[JobListItem])
def recent_jobs(limit: int = 20) -> list[JobListItem]:
    return get_recent_jobs(limit=limit)


@app.get("/api/actions/timeline", response_model=list[ActionTimelineItem])
def action_timeline(limit: int = 100, action_type: str | None = None) -> list[ActionTimelineItem]:
    return list_action_timeline(limit=limit, action_type=action_type)


@app.get("/api/eval/metrics", response_model=AgentEvalMetricsResponse)
def agent_eval_metrics(window_days: int = 14) -> AgentEvalMetricsResponse:
    return get_agent_eval_metrics(window_days=window_days)


@app.post("/api/company/intel", response_model=CompanyIntelResponse)
def company_intel(payload: CompanyIntelRequest) -> CompanyIntelResponse:
    try:
        return generate_company_intel(
            company=payload.company,
            role_title=payload.role_title,
            jd_text=payload.jd_text,
            focus_keywords=payload.focus_keywords,
            max_results=payload.max_results,
            include_search=payload.include_search,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Company intel failed: {exc}") from exc


@app.post("/api/interview/prep", response_model=InterviewPrepResponse)
def interview_prep(payload: InterviewPrepRequest) -> InterviewPrepResponse:
    job_id = (payload.job_id or "").strip()
    company = (payload.company or "").strip()
    role_title = (payload.role_title or "").strip()
    jd_text = (payload.jd_text or "").strip()

    if job_id:
        job = get_job_detail(job_id)
        if not job:
            raise HTTPException(status_code=404, detail=f"Job not found: {job_id}")
        company = company or str(job.get("company") or "").strip()
        role_title = role_title or str(job.get("title") or "").strip()
        jd_text = jd_text or str(job.get("jd_raw") or "").strip()

    if not company:
        raise HTTPException(status_code=400, detail="company is required (or provide job_id)")
    if not role_title:
        role_title = "AI Agent Intern"
    if not jd_text:
        jd_text = f"{company} {role_title} related role."

    try:
        result = generate_interview_prep(
            company=company,
            role_title=role_title,
            jd_text=jd_text,
            question_count=payload.question_count,
            use_company_intel=payload.use_company_intel,
        )
        if job_id:
            log_action(
                job_id=job_id,
                action_type="interview_prep",
                input_summary=f"use_company_intel={payload.use_company_intel}; question_count={payload.question_count}",
                output_summary=f"generated {len(result.questions)} questions",
                status="success",
            )
        return result
    except Exception as exc:
        if job_id:
            log_action(
                job_id=job_id,
                action_type="interview_prep",
                input_summary=f"use_company_intel={payload.use_company_intel}; question_count={payload.question_count}",
                output_summary=f"failed: {str(exc)[:300]}",
                status="failed",
            )
        raise HTTPException(status_code=500, detail=f"Interview prep failed: {exc}") from exc


@app.post("/api/security/token/issue", response_model=SecurityTokenIssueResponse)
def security_token_issue(payload: SecurityTokenIssueRequest) -> SecurityTokenIssueResponse:
    record = issue_security_token(
        action=payload.action,
        purpose=payload.purpose,
        expire_minutes=payload.expire_minutes,
    )
    if not record:
        raise HTTPException(status_code=500, detail="Failed to issue security token")
    return SecurityTokenIssueResponse.model_validate(record)


@app.post("/api/security/token/consume", response_model=SecurityTokenConsumeResponse)
def security_token_consume(payload: SecurityTokenConsumeRequest) -> SecurityTokenConsumeResponse:
    result = consume_security_token(token=payload.token, action=payload.action)
    return SecurityTokenConsumeResponse.model_validate(result)


@app.post("/api/security/budget/check", response_model=ToolBudgetCheckResponse)
def security_budget_check(payload: ToolBudgetCheckRequest) -> ToolBudgetCheckResponse:
    result = check_tool_budget(
        session_id=payload.session_id,
        tool_type=payload.tool_type,
        limit=payload.limit,
        consume=payload.consume,
        dry_run=payload.dry_run,
    )
    return ToolBudgetCheckResponse.model_validate(result)


@app.post("/api/security/budget/reset", response_model=ToolBudgetResetResponse)
def security_budget_reset(payload: ToolBudgetResetRequest) -> ToolBudgetResetResponse:
    ok = reset_tool_budget(session_id=payload.session_id, tool_type=payload.tool_type)
    return ToolBudgetResetResponse(
        ok=ok,
        session_id=payload.session_id,
        tool_type=payload.tool_type,
    )


@app.post("/api/resume/index", response_model=ResumeIndexResponse)
def index_resume(payload: ResumeIndexRequest) -> ResumeIndexResponse:
    upsert_resume_source(payload.source_id, payload.resume_text)
    count = index_resume_text(payload.resume_text, source_id=payload.source_id)
    return ResumeIndexResponse(indexed_chunks=count, source_id=payload.source_id)


@app.post("/api/resume/upload")
def upload_resume(file: UploadFile = File(...), source_id: str = "resume_v1"):
    """Upload a resume file (.txt/.pdf/.docx), save to project dir, extract text, and index."""
    from pathlib import Path as _Path
    allowed = {".txt", ".pdf", ".docx", ".doc", ".md"}
    suffix = _Path(file.filename or "resume.txt").suffix.lower()
    if suffix not in allowed:
        raise HTTPException(status_code=400, detail=f"Unsupported file type: {suffix}. Allowed: {', '.join(allowed)}")

    project_dir = _Path(__file__).resolve().parent.parent.parent
    resume_dir = project_dir / "data" / "resumes"
    resume_dir.mkdir(parents=True, exist_ok=True)
    save_path = resume_dir / f"{source_id}{suffix}"
    raw = file.file.read()
    save_path.write_bytes(raw)

    text = ""
    if suffix in (".txt", ".md"):
        text = raw.decode("utf-8", errors="replace")
    elif suffix == ".pdf":
        try:
            import pdfplumber
            import io
            with pdfplumber.open(io.BytesIO(raw)) as pdf:
                pages_text = []
                for page in pdf.pages:
                    words = page.extract_words(
                        x_tolerance=2, y_tolerance=2,
                        keep_blank_chars=False, use_text_flow=True,
                    )
                    if not words:
                        fallback = page.extract_text()
                        if fallback:
                            pages_text.append(fallback.strip())
                        continue
                    words.sort(key=lambda w: (round(float(w["top"]) / 8) * 8, float(w["x0"])))
                    lines: list[str] = []
                    cur_line_words: list[str] = []
                    prev_top = -999.0
                    for w in words:
                        top = round(float(w["top"]) / 8) * 8
                        if abs(top - prev_top) > 4 and cur_line_words:
                            lines.append(" ".join(cur_line_words))
                            cur_line_words = []
                        cur_line_words.append(w["text"])
                        prev_top = top
                    if cur_line_words:
                        lines.append(" ".join(cur_line_words))
                    pages_text.append("\n".join(lines))
                text = "\n\n".join(pages_text)
        except ImportError:
            raise HTTPException(status_code=500, detail="pdfplumber not installed. Run: pip install pdfplumber")
    elif suffix in (".docx", ".doc"):
        try:
            import docx
            import io
            doc = docx.Document(io.BytesIO(raw))
            text = "\n".join(p.text for p in doc.paragraphs)
        except ImportError:
            raise HTTPException(status_code=500, detail="python-docx not installed. Run: pip install python-docx")

    text = text.strip()
    if not text:
        raise HTTPException(status_code=400, detail="Could not extract text from the uploaded file.")

    upsert_resume_source(source_id, text)
    count = index_resume_text(text, source_id=source_id)
    return {
        "source_id": source_id,
        "indexed_chunks": count,
        "file_saved": str(save_path),
        "text_length": len(text),
        "text_preview": text[:500],
    }


@app.get("/api/resume/source/{source_id}", response_model=ResumeSourceResponse)
def resume_source(source_id: str) -> ResumeSourceResponse:
    row = get_resume_source(source_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Resume source not found: {source_id}")
    return ResumeSourceResponse(
        source_id=str(row["source_id"]),
        resume_text=str(row["resume_text"]),
        updated_at=row["updated_at"],
    )


@app.get("/api/profile", response_model=UserProfileResponse)
def get_profile(profile_id: str = "default") -> UserProfileResponse:
    row = get_user_profile(profile_id)
    if not row:
        profile = default_user_profile()
        return UserProfileResponse(profile_id=profile_id, profile=profile, updated_at=None)
    return UserProfileResponse(
        profile_id=str(row["profile_id"]),
        profile=merge_profile(row.get("profile") if isinstance(row.get("profile"), dict) else {}),
        updated_at=row.get("updated_at"),
    )


@app.put("/api/profile", response_model=UserProfileResponse)
def put_profile(payload: UserProfileUpsertRequest) -> UserProfileResponse:
    normalized = merge_profile(payload.profile)
    ok = upsert_user_profile(payload.profile_id, normalized)
    if not ok:
        raise HTTPException(status_code=500, detail="Failed to save profile")
    row = get_user_profile(payload.profile_id)
    updated_at = row.get("updated_at") if row else None
    return UserProfileResponse(
        profile_id=payload.profile_id,
        profile=normalized,
        updated_at=updated_at,
    )


@app.get("/api/material/pending", response_model=list[PendingMaterialItem])
def list_material_pending() -> list[PendingMaterialItem]:
    return list_pending_material_threads(limit=100)


@app.get("/api/material/thread/{thread_id}", response_model=MaterialThreadDetail)
def get_material_thread_detail(thread_id: str) -> MaterialThreadDetail:
    thread = get_material_thread(thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {thread_id}")
    return MaterialThreadDetail(
        thread_id=str(thread["thread_id"]),
        job_id=str(thread["job_id"]),
        title=str(thread["title"]),
        company=str(thread["company"]),
        match_score=thread.get("match_score"),
        resume_version=str(thread["resume_version"]),
        status=str(thread["status"]),
        last_feedback=(str(thread.get("last_feedback")) if thread.get("last_feedback") else None),
        created_at=thread["created_at"],
        updated_at=thread["updated_at"],
        draft=thread.get("draft"),
    )


@app.post("/api/material/generate", response_model=MaterialGenerateResponse)
def generate_material(payload: MaterialGenerateRequest) -> MaterialGenerateResponse:
    job = get_job_detail(payload.job_id)
    if not job:
        raise HTTPException(status_code=404, detail=f"Job not found: {payload.job_id}")

    match_score = job.get("match_score")
    threshold = float(os.getenv("MATERIAL_MIN_MATCH_SCORE", "60"))
    if match_score is not None and match_score < threshold:
        log_action(
            job_id=payload.job_id,
            action_type="generate",
            input_summary=f"resume_version={payload.resume_version}",
            output_summary=f"skipped due to low score={match_score}",
            status="skipped_low_match",
        )
        return MaterialGenerateResponse(
            status="skipped_low_match",
            thread_id=None,
            job_id=payload.job_id,
            resume_version=payload.resume_version,
            title=str(job.get("title") or "Unknown Title"),
            company=str(job.get("company") or "Unknown Company"),
            match_score=match_score,
            message=f"match_score={match_score} < threshold={threshold}, skipped",
            draft=None,
        )

    skills = extract_skills_from_job(job)
    wf_result = start_material_workflow(
        job_id=payload.job_id,
        title=str(job.get("title") or "Unknown Title"),
        company=str(job.get("company") or "Unknown Company"),
        match_score=match_score,
        skills=skills,
        jd_raw=str(job.get("jd_raw") or ""),
        resume_version=payload.resume_version,
    )
    upsert_material_thread(
        thread_id=wf_result.thread_id,
        job_id=payload.job_id,
        title=str(job.get("title") or "Unknown Title"),
        company=str(job.get("company") or "Unknown Company"),
        match_score=match_score,
        resume_version=payload.resume_version,
        status=wf_result.status,
        draft=wf_result.draft,
        feedback="",
    )
    log_action(
        job_id=payload.job_id,
        action_type="generate",
        input_summary=f"resume_version={payload.resume_version}",
        output_summary=build_material_summary(wf_result.draft) if wf_result.draft else "no draft",
        status="pending_approval",
    )
    return MaterialGenerateResponse(
        status="pending_review",
        thread_id=wf_result.thread_id,
        job_id=payload.job_id,
        resume_version=payload.resume_version,
        title=str(job.get("title") or "Unknown Title"),
        company=str(job.get("company") or "Unknown Company"),
        match_score=match_score,
        message="Draft generated, waiting for human review",
        draft=wf_result.draft,
    )


@app.post("/api/material/review", response_model=MaterialReviewResponse)
def review_material(payload: MaterialReviewRequest) -> MaterialReviewResponse:
    thread = get_material_thread(payload.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {payload.thread_id}")
    if thread.get("status") != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Thread status is {thread.get('status')}, not pending_review",
        )

    wf_result = resume_material_workflow(
        thread_id=payload.thread_id,
        decision=payload.decision,
        feedback=payload.feedback,
    )

    if wf_result.status == "pending_review":
        # This is the regenerate path: generator re-runs, then pauses again.
        upsert_material_thread(
            thread_id=payload.thread_id,
            job_id=str(thread["job_id"]),
            title=str(thread["title"]),
            company=str(thread["company"]),
            match_score=thread.get("match_score"),
            resume_version=str(thread["resume_version"]),
            status="pending_review",
            draft=wf_result.draft,
            feedback=payload.feedback,
        )
        log_action(
            job_id=str(thread["job_id"]),
            action_type="generate",
            input_summary=f"thread_id={payload.thread_id}; feedback={payload.feedback or ''}",
            output_summary=build_material_summary(wf_result.draft) if wf_result.draft else "no draft",
            status="pending_approval",
        )
        return MaterialReviewResponse(
            status="regenerated",
            thread_id=payload.thread_id,
            message="Material regenerated, waiting for review again",
            draft=wf_result.draft,
        )

    if wf_result.status == "approved":
        draft = wf_result.draft
        if not draft:
            raise HTTPException(status_code=500, detail="Approve failed: missing final draft")
        app_id = create_application_record(
            job_id=str(thread["job_id"]),
            resume_version=str(thread["resume_version"]),
            cover_letter=draft.cover_letter,
            channel="offerpilot_manual_review",
            notes=f"greeting={draft.greeting_message}",
        )
        if not app_id:
            log_action(
                job_id=str(thread["job_id"]),
                action_type="review",
                input_summary=f"thread_id={payload.thread_id}",
                output_summary="approve failed: create_application_record returned None",
                status="failed",
            )
            raise HTTPException(
                status_code=500,
                detail="Approve failed: cannot persist into applications table",
            )
        upsert_material_thread(
            thread_id=payload.thread_id,
            job_id=str(thread["job_id"]),
            title=str(thread["title"]),
            company=str(thread["company"]),
            match_score=thread.get("match_score"),
            resume_version=str(thread["resume_version"]),
            status="approved",
            draft=draft,
            feedback=payload.feedback,
        )
        log_action(
            job_id=str(thread["job_id"]),
            action_type="review",
            input_summary=f"thread_id={payload.thread_id}",
            output_summary=f"application_id={app_id}",
            status="success",
        )
        return MaterialReviewResponse(
            status="approved",
            thread_id=payload.thread_id,
            message="Material approved and recorded into applications table",
            draft=draft,
        )

    # Reject path
    upsert_material_thread(
        thread_id=payload.thread_id,
        job_id=str(thread["job_id"]),
        title=str(thread["title"]),
        company=str(thread["company"]),
        match_score=thread.get("match_score"),
        resume_version=str(thread["resume_version"]),
        status="rejected",
        draft=wf_result.draft,
        feedback=payload.feedback,
    )
    log_action(
        job_id=str(thread["job_id"]),
        action_type="review",
        input_summary=f"thread_id={payload.thread_id}; feedback={payload.feedback or ''}",
        output_summary="material rejected by user",
        status="rejected",
    )
    return MaterialReviewResponse(
        status="rejected",
        thread_id=payload.thread_id,
        message="Material rejected and thread closed",
        draft=None,
    )


@app.post("/api/material/export", response_model=MaterialExportResponse)
def export_material(payload: MaterialExportRequest) -> MaterialExportResponse:
    thread = get_material_thread(payload.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Thread not found: {payload.thread_id}")
    if str(thread.get("status")) != "approved":
        raise HTTPException(
            status_code=409,
            detail=f"Only approved threads can be exported, current status={thread.get('status')}",
        )
    file_name, file_path = export_material_thread(thread, export_format=payload.format)
    log_action(
        job_id=str(thread["job_id"]),
        action_type="export",
        input_summary=f"thread_id={payload.thread_id}; format={payload.format}",
        output_summary=f"file={file_name}",
        status="success",
    )
    return MaterialExportResponse(
        thread_id=payload.thread_id,
        format=payload.format,
        file_name=file_name,
        file_path=file_path,
        download_url=f"/api/material/files/{file_name}",
    )


@app.get("/api/material/files/{file_name}")
def download_material_file(file_name: str):
    path = resolve_export_file(file_name)
    if not path:
        raise HTTPException(status_code=404, detail=f"Export file not found: {file_name}")
    return FileResponse(path=str(path), filename=file_name)


@app.post("/api/boss/login")
def boss_login(timeout: int = 300):
    """Open browser for user to log in to BOSS.

    Uses the session-pool browser so the login persists for subsequent
    scan/chat API calls.  The user interacts with the visible browser window.
    """
    from .boss_scan import boss_login_via_pool
    try:
        return boss_login_via_pool(timeout=timeout)
    except Exception as exc:
        raise HTTPException(status_code=500, detail=str(exc)) from exc


@app.post("/api/boss/scan", response_model=BossScanResponse)
def scan_boss(payload: BossScanRequest) -> BossScanResponse:
    try:
        return run_boss_scan_workflow(payload.keyword, payload.max_items, payload.max_pages)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BOSS scan failed: {exc}") from exc


@app.post("/api/boss/greet/trigger", response_model=BossGreetTriggerResponse)
def boss_greet_trigger(payload: BossGreetTriggerRequest = BossGreetTriggerRequest()) -> BossGreetTriggerResponse:
    """涓流式主动打招呼：搜索→JD匹配→对匹配岗位点击「立即沟通」。

    由 cron 定时调用，每次打招呼 batch_size 个（默认3），日上限 BOSS_GREET_DAILY_LIMIT。
    根据 profile 中 job_type 自动过滤不匹配的岗位类型。
    """
    job_type = "all"
    try:
        stored = get_user_profile(payload.profile_id)
        if stored and isinstance(stored.get("profile"), dict):
            pref = stored["profile"].get("job_preference", {})
            job_type = pref.get("job_type", "all")
    except Exception:
        pass

    try:
        result = greet_matching_jobs(
            keyword=payload.keyword,
            batch_size=payload.batch_size,
            match_threshold=payload.match_threshold,
            greeting_text=payload.greeting_text,
            job_type=job_type,
        )
        return BossGreetTriggerResponse(**result)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BOSS greet failed: {exc}") from exc


@app.post("/api/boss/chat/pull", response_model=BossChatPullResponse)
def boss_chat_pull(payload: BossChatPullRequest) -> BossChatPullResponse:
    try:
        items, screenshot_path = pull_boss_chat_conversations(
            max_conversations=payload.max_conversations,
            unread_only=payload.unread_only,
            fetch_latest_hr=payload.fetch_latest_hr,
            chat_tab=payload.chat_tab,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BOSS chat pull failed: {exc}") from exc
    unread_total = sum(max(0, int(item.unread_count)) for item in items)
    log_action(
        job_id=None,
        action_type="boss_chat_pull",
        input_summary=(
            "max_conversations="
            f"{payload.max_conversations}; unread_only={payload.unread_only}; "
            f"fetch_latest_hr={payload.fetch_latest_hr}"
        ),
        output_summary=(
            f"total={len(items)}; unread_total={unread_total}; screenshot={screenshot_path or '-'}"
        ),
        status="success",
    )
    return BossChatPullResponse(
        total=len(items),
        unread_total=unread_total,
        screenshot_path=screenshot_path,
        items=items,
    )


@app.post("/api/boss/chat/process", response_model=BossChatProcessResponse)
def boss_chat_process(payload: BossChatProcessRequest) -> BossChatProcessResponse:
    try:
        return run_boss_chat_copilot_workflow(
            max_conversations=payload.max_conversations,
            unread_only=payload.unread_only,
            profile_id=payload.profile_id,
            notify_on_escalate=payload.notify_on_escalate,
            fetch_latest_hr=payload.fetch_latest_hr,
            auto_execute=payload.auto_execute,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"BOSS chat process failed: {exc}") from exc


@app.post("/api/boss/chat/heartbeat/trigger", response_model=BossChatHeartbeatTriggerResponse)
def boss_chat_heartbeat_trigger(
    payload: BossChatHeartbeatTriggerRequest = Body(
        default_factory=BossChatHeartbeatTriggerRequest
    ),
) -> BossChatHeartbeatTriggerResponse:
    try:
        process = run_boss_chat_copilot_workflow(
            max_conversations=payload.max_conversations,
            unread_only=payload.unread_only,
            chat_tab=payload.chat_tab,
            profile_id=payload.profile_id,
            notify_on_escalate=payload.notify_on_escalate,
            fetch_latest_hr=payload.fetch_latest_hr,
            auto_execute=payload.auto_execute,
        )
    except Exception as exc:
        logger.exception("BOSS heartbeat trigger failed: %s", exc)
        err_msg = str(exc)[:500]
        summary = f"BOSS巡检失败：{err_msg}"
        process = BossChatProcessResponse(
            total_conversations=0,
            candidate_messages=0,
            processed_count=0,
            new_count=0,
            duplicated_count=0,
            items=[],
        )
        log_action(
            job_id=None,
            action_type="boss_chat_heartbeat_trigger",
            input_summary=(
                f"max_conversations={payload.max_conversations}; profile_id={payload.profile_id}"
            ),
            output_summary=f"failed: {err_msg}",
            status="error",
        )
        return BossChatHeartbeatTriggerResponse(
            ok=False,
            summary=summary,
            process=process,
            notification_sent=False,
            notification_error=None,
            error=err_msg,
        )

    needs_user = sum(1 for item in process.items if item.needs_user_intervention)
    send_resume = sum(1 for item in process.items if item.action == "send_resume")
    reply_profile = sum(1 for item in process.items if item.action == "reply_from_profile")
    ignored = sum(1 for item in process.items if item.action == "ignore")
    source_blocked = sum(
        1
        for item in process.items
        if item.action == "ignore" and item.source_fit_passed is False
    )
    proactive_blocked = sum(
        1
        for item in process.items
        if item.proactive_contact is True and item.proactive_match_passed is False
    )
    summary = (
        f"BOSS巡检完成：会话{process.total_conversations}，候选消息{process.candidate_messages}，"
        f"处理{process.processed_count}（新增{process.new_count}，去重{process.duplicated_count}），"
        f"动作分布 send_resume={send_resume} / reply_from_profile={reply_profile} / "
        f"notify_user={needs_user} / ignore={ignored}（source_blocked={source_blocked}，"
        f"proactive_blocked={proactive_blocked}）。"
    )

    notification_sent = False
    notification_error: str | None = None
    should_notify = payload.notify_channel_on_hits and (
        needs_user > 0 or send_resume > 0 or (payload.notify_when_empty and process.processed_count == 0)
    )
    if should_notify:
        manual_items = [item for item in process.items if item.needs_user_intervention][:3]
        manual_lines = [
            f"{idx + 1}. {(item.company or '未知公司')} / {(item.job_title or '未知岗位')} - {item.latest_hr_message[:80]}"
            for idx, item in enumerate(manual_items)
        ]
        manual_text = "\n".join(manual_lines) if manual_lines else "无"
        notify_message = (
            "OfferPilot BOSS Heartbeat 巡检摘要\n"
            f"- {summary}\n"
            f"- 需人工介入（最多展示3条）：\n{manual_text}"
        )
        notification_sent, notification_error = send_channel_notification(
            notify_message,
            payload={
                "total_conversations": process.total_conversations,
                "candidate_messages": process.candidate_messages,
                "processed_count": process.processed_count,
                "new_count": process.new_count,
                "duplicated_count": process.duplicated_count,
                "needs_user": needs_user,
                "send_resume": send_resume,
                "reply_profile": reply_profile,
                "ignored": ignored,
                "source_blocked": source_blocked,
                "proactive_blocked": proactive_blocked,
            },
        )

    log_action(
        job_id=None,
        action_type="boss_chat_heartbeat_trigger",
        input_summary=(
            f"max_conversations={payload.max_conversations}; unread_only={payload.unread_only}; "
            f"profile_id={payload.profile_id}; fetch_latest_hr={payload.fetch_latest_hr}; "
            f"notify_channel_on_hits={payload.notify_channel_on_hits}"
        ),
        output_summary=(
            f"processed={process.processed_count}; new={process.new_count}; duplicate={process.duplicated_count}; "
            f"needs_user={needs_user}; send_resume={send_resume}; reply_profile={reply_profile}; "
            f"ignored={ignored}; source_blocked={source_blocked}; proactive_blocked={proactive_blocked}; "
            f"notification_sent={notification_sent}; notification_error={notification_error or '-'}"
        ),
        status="success",
    )
    return BossChatHeartbeatTriggerResponse(
        ok=True,
        summary=summary,
        process=process,
        notification_sent=notification_sent,
        notification_error=notification_error,
    )


@app.post("/api/boss/chat/reply-preview", response_model=BossChatReplyPreviewResponse)
def boss_chat_reply_preview(payload: BossChatReplyPreviewRequest) -> BossChatReplyPreviewResponse:
    stored = get_user_profile(payload.profile_id)
    profile: dict = (
        stored.get("profile")
        if stored and isinstance(stored.get("profile"), dict)
        else default_user_profile()
    )
    if isinstance(payload.profile_override, dict):
        profile = payload.profile_override
    decision = preview_boss_chat_reply(
        hr_message=payload.hr_message,
        profile=profile,
        company=payload.company,
        job_title=payload.job_title,
        notify_on_escalate=payload.notify_on_escalate,
    )
    log_action(
        job_id=None,
        action_type="boss_chat_preview",
        input_summary=(
            f"company={payload.company or ''}; job_title={payload.job_title or ''}; "
            f"hr_id={payload.hr_id or ''}; conversation_id={payload.conversation_id or ''}; "
            f"message={payload.hr_message}"
        ),
        output_summary=(
            f"intent={decision.intent}; confidence={decision.confidence:.2f}; action={decision.action}; "
            f"needs_send_resume={decision.needs_send_resume}; needs_user_intervention={decision.needs_user_intervention}; "
            f"reason={decision.reason}; matched_fields={','.join(decision.matched_profile_fields)}"
        ),
        status="success",
    )
    return BossChatReplyPreviewResponse(
        intent=decision.intent,
        confidence=decision.confidence,
        action=decision.action,
        reason=decision.reason,
        extracted_question=decision.extracted_question,
        reply_text=decision.reply_text,
        needs_send_resume=decision.needs_send_resume,
        needs_user_intervention=decision.needs_user_intervention,
        matched_profile_fields=decision.matched_profile_fields,
        notification_sent=decision.notification_sent,
        notification_error=decision.notification_error,
        used_profile_id=payload.profile_id,
    )


@app.post("/api/form/autofill/preview", response_model=FormAutofillPreviewResponse)
def preview_autofill(payload: FormAutofillPreviewRequest) -> FormAutofillPreviewResponse:
    try:
        fields_raw = preview_form_autofill(payload.html, payload.profile)
        fields = [FormAutofillField.model_validate(item) for item in fields_raw]
        mapped = sum(1 for item in fields if item.suggested_value)
        return FormAutofillPreviewResponse(
            total_fields=len(fields),
            mapped_fields=mapped,
            fields=fields,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Autofill preview failed: {exc}") from exc


def _normalize_profile(profile: dict[str, str]) -> dict[str, str]:
    normalized: dict[str, str] = {}
    for key, value in profile.items():
        safe_key = str(key).strip()
        if not safe_key:
            continue
        normalized[safe_key] = str(value or "").strip()
    return normalized


def _to_form_fill_preview(preview: dict | None) -> FormFillThreadPreview:
    payload = preview if isinstance(preview, dict) else {}
    fields_raw = payload.get("fields") if isinstance(payload.get("fields"), list) else []
    fields = [FormAutofillField.model_validate(item) for item in fields_raw if isinstance(item, dict)]
    return FormFillThreadPreview(
        total_fields=int(payload.get("total_fields") or len(fields)),
        mapped_fields=int(payload.get("mapped_fields") or sum(1 for item in fields if item.suggested_value)),
        screenshot_path=(
            str(payload.get("screenshot_path")) if payload.get("screenshot_path") is not None else None
        ),
        fields=fields,
    )


@app.post("/api/form/autofill/preview-url", response_model=FormAutofillUrlPreviewResponse)
def preview_autofill_url(payload: FormAutofillUrlPreviewRequest) -> FormAutofillUrlPreviewResponse:
    try:
        data = preview_form_autofill_url(payload.url, payload.profile)
        fields = [FormAutofillField.model_validate(item) for item in data.get("fields", [])]
        mapped = sum(1 for item in fields if item.suggested_value)
        return FormAutofillUrlPreviewResponse(
            url=payload.url,
            total_fields=len(fields),
            mapped_fields=mapped,
            screenshot_path=(str(data.get("screenshot_path")) if data.get("screenshot_path") else None),
            fields=fields,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Autofill URL preview failed: {exc}") from exc


@app.post("/api/form/autofill/fill-url", response_model=FormAutofillFillResponse)
def fill_autofill_url(payload: FormAutofillFillRequest) -> FormAutofillFillResponse:
    if not payload.confirm_fill:
        raise HTTPException(
            status_code=400,
            detail="confirm_fill must be true before executing external fill actions",
        )
    try:
        data = fill_form_autofill_url(
            payload.url,
            payload.profile,
            max_actions=payload.max_actions,
        )
        return FormAutofillFillResponse.model_validate(data)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Autofill URL fill failed: {exc}") from exc


@app.get("/api/form/fill/pending", response_model=list[FormFillPendingItem])
def list_form_fill_pending(limit: int = 50) -> list[FormFillPendingItem]:
    rows = list_pending_form_fill_threads(limit=limit)
    result: list[FormFillPendingItem] = []
    for row in rows:
        try:
            result.append(FormFillPendingItem.model_validate(row))
        except Exception:
            continue
    return result


@app.get("/api/form/fill/thread/{thread_id}", response_model=FormFillThreadDetail)
def get_form_fill_thread_detail(thread_id: str) -> FormFillThreadDetail:
    row = get_form_fill_thread(thread_id)
    if not row:
        raise HTTPException(status_code=404, detail=f"Form fill thread not found: {thread_id}")
    preview = _to_form_fill_preview(row.get("preview"))
    fill_result_raw = row.get("fill_result")
    fill_result = (
        FormAutofillFillResponse.model_validate(fill_result_raw)
        if isinstance(fill_result_raw, dict)
        else None
    )
    profile = row.get("profile") if isinstance(row.get("profile"), dict) else {}
    return FormFillThreadDetail(
        thread_id=str(row["thread_id"]),
        url=str(row["url"]),
        status=str(row["status"]),
        profile={str(k): str(v) for k, v in profile.items()},
        preview=preview,
        fill_result=fill_result,
        last_feedback=(str(row.get("last_feedback")) if row.get("last_feedback") else None),
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


@app.post("/api/form/fill/start", response_model=FormFillStartResponse)
def start_form_fill(payload: FormFillStartRequest) -> FormFillStartResponse:
    profile = _normalize_profile(payload.profile)
    try:
        wf_result = start_form_fill_workflow(
            url=payload.url,
            profile=profile,
            max_actions=payload.max_actions,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Form fill start failed: {exc}") from exc

    preview = _to_form_fill_preview(wf_result.preview)
    upsert_form_fill_thread(
        thread_id=wf_result.thread_id,
        url=payload.url,
        status=wf_result.status,
        profile=profile,
        preview=wf_result.preview,
        fill_result=wf_result.fill_result,
        feedback="",
    )
    return FormFillStartResponse(
        thread_id=wf_result.thread_id,
        status=wf_result.status,
        url=payload.url,
        message="Preview generated, waiting for human review",
        preview=preview,
    )


@app.post("/api/form/fill/review", response_model=FormFillReviewResponse)
def review_form_fill(payload: FormFillReviewRequest) -> FormFillReviewResponse:
    thread = get_form_fill_thread(payload.thread_id)
    if not thread:
        raise HTTPException(status_code=404, detail=f"Form fill thread not found: {payload.thread_id}")
    if str(thread.get("status")) != "pending_review":
        raise HTTPException(
            status_code=409,
            detail=f"Thread status is {thread.get('status')}, not pending_review",
        )

    try:
        wf_result = resume_form_fill_workflow(
            thread_id=payload.thread_id,
            decision=payload.decision,
            feedback=payload.feedback,
            max_actions=payload.max_actions,
        )
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Form fill review failed: {exc}") from exc

    preview_raw = wf_result.preview if wf_result.preview is not None else thread.get("preview")
    fill_result_raw = wf_result.fill_result if wf_result.fill_result is not None else thread.get("fill_result")
    upsert_form_fill_thread(
        thread_id=payload.thread_id,
        url=str(thread.get("url") or ""),
        status=wf_result.status,
        profile=thread.get("profile") if isinstance(thread.get("profile"), dict) else {},
        preview=preview_raw if isinstance(preview_raw, dict) else None,
        fill_result=fill_result_raw if isinstance(fill_result_raw, dict) else None,
        feedback=payload.feedback,
    )

    preview = _to_form_fill_preview(preview_raw if isinstance(preview_raw, dict) else None)
    fill_result = (
        FormAutofillFillResponse.model_validate(fill_result_raw)
        if isinstance(fill_result_raw, dict)
        else None
    )
    if wf_result.status == "approved":
        message = "Form fill approved and executed (submission not performed)"
    else:
        message = "Form fill request rejected and closed"
    return FormFillReviewResponse(
        thread_id=payload.thread_id,
        status=wf_result.status,
        message=message,
        preview=preview,
        fill_result=fill_result,
    )


def _email_heartbeat_enabled_by_env() -> bool:
    return _env_bool("EMAIL_HEARTBEAT_ENABLED", False)


def _email_notify_on_updates() -> bool:
    return _env_bool("EMAIL_HEARTBEAT_NOTIFY_ON_UPDATES", True)


def _email_schedule_reminder_hours() -> int:
    return _env_int("EMAIL_SCHEDULE_REMINDER_HOURS", 24, min_value=1, max_value=168)


def _email_schedule_upcoming_days() -> int:
    return _env_int("EMAIL_SCHEDULE_UPCOMING_DAYS", 14, min_value=1, max_value=90)


def _build_heartbeat_summary_text(result: dict[str, object]) -> str:
    fetched_count = int(result.get("fetched_count") or 0)
    processed_count = int(result.get("processed_count") or 0)
    schedule_reminders = int(result.get("schedule_reminders") or 0)
    upcoming_count = int(result.get("upcoming_schedules") or 0)
    type_counts = result.get("type_counts")
    interviews = result.get("interview_invites")
    schedule_due_items = result.get("schedule_due_items")
    lines = [
        f"OfferPilot 邮件巡检：抓取 {fetched_count} 封，处理 {processed_count} 封。",
    ]
    if isinstance(type_counts, dict) and type_counts:
        parts = [f"{key}={value}" for key, value in type_counts.items()]
        lines.append("分类统计：" + ", ".join(parts))
    if isinstance(interviews, list) and interviews:
        top = interviews[:3]
        lines.append("面试邀请：")
        for item in top:
            if not isinstance(item, dict):
                continue
            company = str(item.get("company") or "Unknown")
            interview_time = str(item.get("interview_time") or "时间待确认")
            lines.append(f"- {company} @ {interview_time}")
    if schedule_reminders > 0:
        lines.append(f"即将到期提醒：{schedule_reminders} 条（<= {_email_schedule_reminder_hours()} 小时）。")
        if isinstance(schedule_due_items, list):
            for item in schedule_due_items[:3]:
                if not isinstance(item, dict):
                    continue
                company = str(item.get("company") or "Unknown")
                when = str(item.get("start_at") or "时间待确认")
                event_type = str(item.get("event_type") or "interview")
                lines.append(f"- {company} / {event_type} @ {when}")
    if upcoming_count > 0:
        lines.append(f"未来 {_email_schedule_upcoming_days()} 天日程总计：{upcoming_count} 条。")
    return "\n".join(lines)


def _maybe_notify_heartbeat_result(result: dict[str, object]) -> tuple[bool, str | None]:
    processed_count = int(result.get("processed_count") or 0)
    schedule_reminders = int(result.get("schedule_reminders") or 0)
    if processed_count <= 0 and schedule_reminders <= 0:
        return False, "no updates"
    if not _email_notify_on_updates():
        return False, "notification disabled by env"
    message = _build_heartbeat_summary_text(result)
    return send_channel_notification(message, payload=result)


def _poll_email_once(max_items: int, mark_seen: bool) -> dict[str, object]:
    fetched = fetch_unread_emails(max_items=max_items, mark_seen=mark_seen)
    items: list[EmailFetchItem] = []
    type_counts: dict[str, int] = {}
    interview_invites: list[dict[str, str]] = []
    for mail in fetched:
        try:
            result = _ingest_email_once(
                EmailIngestRequest(
                    sender=mail.sender,
                    subject=mail.subject,
                    body=mail.body,
                    received_at=mail.received_at,
                )
            )
        except HTTPException:
            continue
        items.append(
            EmailFetchItem(
                sender=mail.sender,
                subject=mail.subject,
                email_type=result.classification.email_type,
                company=result.classification.company,
                interview_time=result.classification.interview_time,
                related_job_id=result.related_job_id,
                updated_job_status=result.updated_job_status,
            )
        )
        email_type = result.classification.email_type
        type_counts[email_type] = type_counts.get(email_type, 0) + 1
        if email_type == "interview_invite":
            interview_invites.append(
                {
                    "company": str(result.classification.company or "Unknown"),
                    "interview_time": str(result.classification.interview_time or ""),
                    "subject": mail.subject[:200],
                }
            )
    reminder_hours = _email_schedule_reminder_hours()
    due_reminders = list_due_schedule_reminders(within_hours=reminder_hours, limit=20)
    upcoming = list_upcoming_schedules(limit=20, days=_email_schedule_upcoming_days())
    summary: dict[str, object] = {
        "fetched_count": len(fetched),
        "processed_count": len(items),
        "type_counts": type_counts,
        "interview_invites": interview_invites,
        "schedule_reminders": len(due_reminders),
        "schedule_due_items": [
            {
                "id": item.id,
                "company": item.company,
                "event_type": item.event_type,
                "start_at": item.start_at.strftime("%Y-%m-%d %H:%M"),
            }
            for item in due_reminders
        ],
        "upcoming_schedules": len(upcoming),
        "items": [item.model_dump() for item in items],
    }
    sent, err = _maybe_notify_heartbeat_result(summary)
    if sent and due_reminders:
        mark_schedule_reminded([item.id for item in due_reminders if item.id])
    summary["notification_sent"] = sent
    summary["notification_error"] = err
    return summary


def _get_email_heartbeat() -> EmailHeartbeatManager:
    global _EMAIL_HEARTBEAT
    if _EMAIL_HEARTBEAT is None:
        _EMAIL_HEARTBEAT = EmailHeartbeatManager(
            runner=_poll_email_once,
            interval_sec=_env_int("EMAIL_HEARTBEAT_INTERVAL_SEC", 1800, min_value=30, max_value=86400),
            max_items=_env_int("EMAIL_HEARTBEAT_MAX_ITEMS", 10, min_value=1, max_value=50),
            mark_seen=_env_bool("EMAIL_HEARTBEAT_MARK_SEEN", False),
        )
    return _EMAIL_HEARTBEAT


@app.on_event("startup")
def startup_email_heartbeat() -> None:
    if _email_heartbeat_enabled_by_env():
        _get_email_heartbeat().start()


@app.on_event("startup")
def startup_production_guard() -> None:
    from .production_guard import start_production_guard
    start_production_guard()


@app.on_event("shutdown")
def shutdown_email_heartbeat() -> None:
    _get_email_heartbeat().stop()


@app.on_event("shutdown")
def shutdown_production_guard() -> None:
    from .production_guard import stop_production_guard
    stop_production_guard()


@app.on_event("shutdown")
def shutdown_browser_session() -> None:
    from .boss_scan import shutdown_browser
    shutdown_browser()


@app.get("/api/email/recent", response_model=list[EmailEventItem])
def recent_email_events(limit: int = 30) -> list[EmailEventItem]:
    return list_recent_email_events(limit=limit)


@app.get("/api/schedules/upcoming", response_model=list[ScheduleEventItem])
def get_upcoming_schedules(limit: int = 30, days: int = 14) -> list[ScheduleEventItem]:
    return list_upcoming_schedules(limit=limit, days=days)


def _ingest_email_once(payload: EmailIngestRequest) -> EmailIngestResponse:
    try:
        wf_result = run_email_workflow(
            sender=payload.sender,
            subject=payload.subject,
            body=payload.body,
            received_at=payload.received_at,
        )
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email classify workflow failed: {exc}") from exc

    email_id = persist_email_event(
        sender=payload.sender,
        subject=payload.subject,
        body=payload.body,
        email_type=wf_result.classification.email_type,
        company=wf_result.classification.company,
        interview_time=wf_result.classification.interview_time,
        raw_classification=wf_result.classification.model_dump(),
        related_job_id=wf_result.related_job_id,
        updated_job_status=wf_result.updated_job_status,
        received_at=payload.received_at,
    )
    if not email_id:
        raise HTTPException(status_code=500, detail="Email event persistence failed")

    schedule_event_id: str | None = None
    try:
        candidate = extract_schedule_candidate(
            classification=wf_result.classification,
            subject=payload.subject,
            body=payload.body,
            received_at=payload.received_at,
        )
        if candidate:
            schedule_event_id = upsert_schedule_event(
                source_email_id=email_id,
                company=wf_result.classification.company,
                event_type=candidate.event_type,
                start_at=candidate.start_at,
                raw_time_text=candidate.raw_time_text,
                mode=candidate.mode,
                location=candidate.location,
                contact=candidate.contact,
                confidence=candidate.confidence,
                status="scheduled",
            )
    except Exception as exc:
        logger.warning("Extract/persist schedule from email failed: %s", exc)

    message = (
        f"classified as {wf_result.classification.email_type}"
        + (
            f"; updated job {wf_result.related_job_id} -> {wf_result.updated_job_status}"
            if wf_result.related_job_id and wf_result.updated_job_status
            else "; no related job updated"
        )
    )
    if schedule_event_id:
        message = f"{message}; schedule extracted"
    return EmailIngestResponse(
        email_id=email_id,
        classification=wf_result.classification,
        related_job_id=wf_result.related_job_id,
        updated_job_status=wf_result.updated_job_status,
        schedule_event_id=schedule_event_id,
        message=message,
    )


@app.post("/api/email/ingest", response_model=EmailIngestResponse)
def ingest_email(payload: EmailIngestRequest) -> EmailIngestResponse:
    return _ingest_email_once(payload)


@app.post("/api/email/fetch", response_model=EmailFetchResponse)
def fetch_email(payload: EmailFetchRequest) -> EmailFetchResponse:
    try:
        result = _poll_email_once(payload.max_items, payload.mark_seen)
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email fetch failed: {exc}") from exc
    return EmailFetchResponse.model_validate(result)


@app.get("/api/email/heartbeat/status", response_model=EmailHeartbeatStatusResponse)
def email_heartbeat_status() -> EmailHeartbeatStatusResponse:
    status = _get_email_heartbeat().status()
    return EmailHeartbeatStatusResponse(
        enabled_by_env=_email_heartbeat_enabled_by_env(),
        running=bool(status.get("running")),
        interval_sec=int(status.get("interval_sec") or 0),
        max_items=int(status.get("max_items") or 0),
        mark_seen=bool(status.get("mark_seen")),
        last_run_at=status.get("last_run_at"),
        last_success_at=status.get("last_success_at"),
        last_error=(str(status.get("last_error")) if status.get("last_error") else None),
        last_fetched_count=(
            int(status.get("last_fetched_count"))
            if status.get("last_fetched_count") is not None
            else None
        ),
        last_processed_count=(
            int(status.get("last_processed_count"))
            if status.get("last_processed_count") is not None
            else None
        ),
    )


@app.get("/api/guard/status")
def production_guard_status() -> dict:
    """ProductionGuard 守护状态查询。"""
    from .production_guard import guard_stats
    from .boss_scan import get_browser_health
    return {
        "guard": guard_stats(),
        "browser": get_browser_health(),
    }


@app.post("/api/guard/start")
def production_guard_start() -> dict:
    from .production_guard import start_production_guard
    ok = start_production_guard()
    return {"started": ok}


@app.post("/api/guard/stop")
def production_guard_stop() -> dict:
    from .production_guard import stop_production_guard
    stop_production_guard()
    return {"stopped": True}


@app.post("/api/email/heartbeat/start", response_model=EmailHeartbeatControlResponse)
def email_heartbeat_start() -> EmailHeartbeatControlResponse:
    started = _get_email_heartbeat().start()
    return EmailHeartbeatControlResponse(
        running=True,
        message="email heartbeat started" if started else "email heartbeat already running",
    )


@app.post("/api/email/heartbeat/stop", response_model=EmailHeartbeatControlResponse)
def email_heartbeat_stop() -> EmailHeartbeatControlResponse:
    stopped = _get_email_heartbeat().stop()
    return EmailHeartbeatControlResponse(
        running=False,
        message="email heartbeat stopped" if stopped else "email heartbeat already stopped",
    )


@app.post("/api/email/heartbeat/trigger", response_model=EmailHeartbeatTriggerResponse)
def email_heartbeat_trigger() -> EmailHeartbeatTriggerResponse:
    try:
        result = _get_email_heartbeat().trigger_once()
    except RuntimeError as exc:
        raise HTTPException(status_code=503, detail=str(exc)) from exc
    except Exception as exc:
        raise HTTPException(status_code=500, detail=f"Email heartbeat trigger failed: {exc}") from exc
    fetched_count = int(result.get("fetched_count") or 0)
    processed_count = int(result.get("processed_count") or 0)
    schedule_reminders = int(result.get("schedule_reminders") or 0)
    upcoming_schedules = int(result.get("upcoming_schedules") or 0)
    return EmailHeartbeatTriggerResponse(
        message="heartbeat run completed",
        fetched_count=fetched_count,
        processed_count=processed_count,
        schedule_reminders=schedule_reminders,
        upcoming_schedules=upcoming_schedules,
        notification_sent=bool(result.get("notification_sent")),
        notification_error=(str(result.get("notification_error")) if result.get("notification_error") else None),
    )


@app.post("/api/email/heartbeat/notify-test", response_model=EmailHeartbeatNotifyTestResponse)
def email_heartbeat_notify_test(payload: EmailHeartbeatNotifyTestRequest) -> EmailHeartbeatNotifyTestResponse:
    sent, err = send_channel_notification(payload.message)
    if not sent:
        return EmailHeartbeatNotifyTestResponse(sent=False, message=payload.message, error=err)
    return EmailHeartbeatNotifyTestResponse(sent=True, message=payload.message, error=None)


@app.post("/api/notify/daily-summary")
def trigger_daily_summary():
    """查询当日 actions 统计并发送飞书每日摘要。"""
    import psycopg
    from .storage import _database_url

    stats = {"boss_scan": 0, "boss_chat_process": 0, "auto_reply": 0, "escalated": 0, "email": 0}
    errors: list[str] = []
    try:
        with psycopg.connect(_database_url()) as conn:
            with conn.cursor() as cur:
                cur.execute(
                    "SELECT action_type, status, COUNT(*) FROM actions "
                    "WHERE created_at >= CURRENT_DATE GROUP BY action_type, status"
                )
                for row in cur.fetchall():
                    atype, status, cnt = row[0], row[1], int(row[2])
                    if "boss_scan" in (atype or ""):
                        stats["boss_scan"] += cnt
                    elif "boss_chat" in (atype or ""):
                        stats["boss_chat_process"] += cnt
                    elif "email" in (atype or ""):
                        stats["email"] += cnt
                    if status and status != "success":
                        errors.append(f"{atype}: {status} x{cnt}")
    except Exception as exc:
        errors.append(f"DB query failed: {exc}")

    sent, err = notify_daily_summary(
        scan_count=stats["boss_scan"],
        chat_processed=stats["boss_chat_process"],
        auto_replied=stats["auto_reply"],
        escalated=stats["escalated"],
        emails_fetched=stats["email"],
        errors=errors if errors else None,
    )
    return {
        "sent": sent,
        "error": err,
        "stats": stats,
        "errors": errors,
    }
