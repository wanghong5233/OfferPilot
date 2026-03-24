from typing import Any, List, Literal
from datetime import datetime

from pydantic import BaseModel, Field


class JDAnalyzeRequest(BaseModel):
    jd_text: str = Field(..., description="Raw JD text from job platforms")


class SimilarJob(BaseModel):
    title: str
    company: str
    similarity: float = Field(..., ge=0, le=1)
    match_score: float | None = None


class JDAnalyzeResponse(BaseModel):
    title: str
    company: str
    skills: List[str]
    match_score: float
    gap_analysis: str
    should_apply: bool = True
    strengths: List[str] = Field(default_factory=list)
    gaps: List[str] = Field(default_factory=list)
    one_line_reason: str = ""
    resume_evidence: List[str] = Field(default_factory=list)
    similar_jobs: List[SimilarJob] = Field(default_factory=list)


class JDMatchOutput(BaseModel):
    """Single-call LLM output for JD matching."""
    title: str = Field(..., description="岗位名称")
    company: str = Field(default="Unknown Company", description="公司名")
    skills: List[str] = Field(default_factory=list, description="核心要求技能，3-8 项")
    match_score: float = Field(..., ge=0, le=100, description="匹配度 0-100")
    should_apply: bool = Field(..., description="是否建议投递/打招呼")
    strengths: List[str] = Field(default_factory=list, description="候选人匹配优势，2-3 点")
    gaps: List[str] = Field(default_factory=list, description="匹配差距，0-3 点")
    gap_analysis: str = Field(default="", description="差距分析与建议，2-3 句话")
    one_line_reason: str = Field(default="", description="一句话总结匹配结论")


class GreetDecision(BaseModel):
    """LLM 二元判断：是否值得主动打招呼。"""
    should_greet: bool = Field(..., description="是否应该打招呼 true/false")
    reason: str = Field(..., description="一句话判断理由")
    confidence: str = Field(default="medium", description="置信度 high/medium/low")
    gaps: List[str] = Field(default_factory=list, description="主要差距，0-3 点")
    gap_analysis: str = Field(default="", description="差距分析与建议，2-3 句话")
    one_line_reason: str = Field(default="", description="一句话总结匹配结论")


class JDParsed(BaseModel):
    title: str = Field(..., description="Parsed job title")
    company: str = Field(..., description="Parsed company name")
    skills: List[str] = Field(default_factory=list, description="Core required skills")


class MatchResult(BaseModel):
    match_score: float = Field(..., ge=0, le=100, description="0-100 match score")


class JobListItem(BaseModel):
    id: str
    title: str
    company: str
    source: str
    match_score: float | None = None
    status: str
    created_at: datetime


class ResumeIndexRequest(BaseModel):
    resume_text: str = Field(..., description="Raw resume text to chunk and index")
    source_id: str = Field(default="manual_resume", description="Logical resume source id")


class ResumeIndexResponse(BaseModel):
    indexed_chunks: int
    source_id: str


class ResumeSourceResponse(BaseModel):
    source_id: str
    resume_text: str
    updated_at: datetime


class MaterialDraft(BaseModel):
    resume_bullets: List[str]
    cover_letter: str
    greeting_message: str


class MaterialGenerateRequest(BaseModel):
    job_id: str = Field(..., description="Target job id from jobs table")
    resume_version: str = Field(default="resume_v1", description="Resume version label")


class MaterialGenerateResponse(BaseModel):
    status: str
    thread_id: str | None = None
    job_id: str
    resume_version: str
    title: str
    company: str
    match_score: float | None = None
    message: str | None = None
    draft: MaterialDraft | None = None


class MaterialReviewRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "reject", "regenerate"]
    feedback: str | None = None


class MaterialReviewResponse(BaseModel):
    status: str
    thread_id: str
    message: str
    draft: MaterialDraft | None = None


class PendingMaterialItem(BaseModel):
    thread_id: str
    job_id: str
    title: str
    company: str
    match_score: float | None = None
    resume_version: str
    created_at: datetime
    updated_at: datetime
    draft: MaterialDraft | None = None


class MaterialThreadDetail(BaseModel):
    thread_id: str
    job_id: str
    title: str
    company: str
    match_score: float | None = None
    resume_version: str
    status: str
    last_feedback: str | None = None
    created_at: datetime
    updated_at: datetime
    draft: MaterialDraft | None = None


class MaterialExportRequest(BaseModel):
    thread_id: str
    format: Literal["pdf", "txt"] = "pdf"


class MaterialExportResponse(BaseModel):
    thread_id: str
    format: Literal["pdf", "txt"]
    file_name: str
    file_path: str
    download_url: str


class BossScanRequest(BaseModel):
    keyword: str = Field(..., description="Search keyword, e.g. AI Agent 实习")
    max_items: int = Field(default=10, ge=1, le=30)
    max_pages: int = Field(default=1, ge=1, le=5)


class BossScanItem(BaseModel):
    title: str
    company: str
    salary: str | None = None
    source_url: str | None = None
    snippet: str | None = None
    match_score: float | None = None


class BossScanResponse(BaseModel):
    keyword: str
    total: int
    pages_scanned: int = 1
    screenshot_path: str | None = None
    items: List[BossScanItem] = Field(default_factory=list)


class BossGreetTriggerRequest(BaseModel):
    keyword: str = Field(default="大模型 Agent 实习", description="搜索关键词")
    batch_size: int | None = Field(default=None, ge=1, le=10, description="本批打招呼数量，默认读环境变量 BOSS_GREET_BATCH_SIZE")
    match_threshold: float | None = Field(default=None, ge=30, le=95, description="JD 匹配分阈值，默认读环境变量")
    greeting_text: str | None = Field(default=None, description="打招呼附带的文本（可选，BOSS 已自动发送预设招呼语，通常无需填写）")
    profile_id: str = Field(default="default", description="用于读取 job_type 等过滤参数的画像 ID")


class BossGreetTriggerResponse(BaseModel):
    ok: bool = True
    greeted: int = 0
    failed: int = 0
    skipped: int = 0
    daily_count: int = 0
    daily_limit: int = 50
    reason: str | None = None
    matched_details: List[dict] = Field(default_factory=list)


class UserProfileUpsertRequest(BaseModel):
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    profile: dict[str, Any] = Field(default_factory=dict)


class UserProfileResponse(BaseModel):
    profile_id: str
    profile: dict[str, Any] = Field(default_factory=dict)
    updated_at: datetime | None = None


class BossChatReplyPreviewRequest(BaseModel):
    hr_message: str = Field(..., min_length=1, max_length=4000)
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    profile_override: dict[str, Any] | None = None
    company: str | None = Field(default=None, max_length=300)
    job_title: str | None = Field(default=None, max_length=300)
    conversation_id: str | None = Field(default=None, max_length=200)
    hr_id: str | None = Field(default=None, max_length=200)
    reply_count_for_hr: int = Field(default=0, ge=0, le=1000, deprecated=True)
    notify_on_escalate: bool = True


class BossChatReplyPreviewResponse(BaseModel):
    intent: str
    confidence: float = Field(default=0.0, ge=0, le=1)
    action: Literal["send_resume", "reply_from_profile", "notify_user", "ignore"]
    reason: str
    extracted_question: str | None = None
    reply_text: str | None = None
    needs_send_resume: bool = False
    needs_user_intervention: bool = False
    matched_profile_fields: List[str] = Field(default_factory=list)
    notification_sent: bool = False
    notification_error: str | None = None
    used_profile_id: str = "default"


class BossChatPullRequest(BaseModel):
    max_conversations: int = Field(default=20, ge=1, le=100)
    unread_only: bool = False
    fetch_latest_hr: bool = True
    chat_tab: str = Field(default="全部", description="BOSS 内置标签: 全部 | 未读 | 新招呼")


class BossChatConversationItem(BaseModel):
    conversation_id: str
    hr_name: str
    company: str | None = None
    job_title: str | None = None
    unread_count: int = 0
    latest_message: str | None = None
    latest_time: str | None = None
    latest_hr_message: str | None = None
    latest_hr_time: str | None = None
    preview: str | None = None
    source_url: str | None = None
    jd_text: str | None = None
    has_candidate_messages: bool = Field(default=True, description="对话中是否有候选人消息（False=HR首次联系）")
    conversation_messages: List[dict] = Field(default_factory=list, description="完整对话消息列表 [{role, text, time}]")
    pending_hr_texts: List[str] = Field(default_factory=list, description="最后一条候选人消息之后的所有HR消息")


class BossChatPullResponse(BaseModel):
    total: int
    unread_total: int
    screenshot_path: str | None = None
    items: List[BossChatConversationItem] = Field(default_factory=list)


class BossChatProcessRequest(BaseModel):
    max_conversations: int = Field(default=20, ge=1, le=100)
    unread_only: bool = True
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    notify_on_escalate: bool = True
    fetch_latest_hr: bool = True
    auto_execute: bool = Field(default=False, description="满足安全条件时实际发送回复（需 BOSS_CHAT_AUTO_EXECUTE_ENABLED=true）")


class BossChatProcessItem(BaseModel):
    conversation_id: str
    hr_name: str
    company: str | None = None
    job_title: str | None = None
    latest_hr_message: str
    latest_hr_time: str | None = None
    message_signature: str
    is_new: bool
    intent: str
    confidence: float = Field(default=0.0, ge=0, le=1)
    action: Literal["send_resume", "reply_from_profile", "notify_user", "ignore"]
    reason: str
    reply_text: str | None = None
    needs_send_resume: bool = False
    needs_user_intervention: bool = False
    notification_sent: bool = False
    notification_error: str | None = None
    source_fit_score: float | None = Field(default=None, ge=0, le=100)
    source_fit_passed: bool | None = None
    source_fit_reason: str | None = None
    proactive_contact: bool | None = None
    proactive_confidence: float | None = Field(default=None, ge=0, le=1)
    proactive_reason: str | None = None
    proactive_match_score: float | None = Field(default=None, ge=0, le=100)
    proactive_match_passed: bool | None = None
    proactive_jd_match_score: float | None = Field(default=None, ge=0, le=100)
    proactive_gap_analysis: str | None = None
    reply_sent: bool = Field(default=False, description="是否已实际发送回复（自动模式）")
    reply_sent_error: str | None = None


class BossChatProcessResponse(BaseModel):
    total_conversations: int
    candidate_messages: int
    processed_count: int
    new_count: int
    duplicated_count: int
    screenshot_path: str | None = None
    items: List[BossChatProcessItem] = Field(default_factory=list)


class BossChatHeartbeatTriggerRequest(BaseModel):
    max_conversations: int = Field(default=30, ge=1, le=100)
    unread_only: bool = True
    chat_tab: str = Field(default="未读", description="BOSS 内置标签: 全部 | 未读 | 新招呼")
    profile_id: str = Field(default="default", min_length=1, max_length=120)
    notify_on_escalate: bool = True
    fetch_latest_hr: bool = True
    notify_channel_on_hits: bool = True
    notify_when_empty: bool = False
    auto_execute: bool = Field(default=True, description="满足条件时实际发送（需 BOSS_CHAT_AUTO_EXECUTE_ENABLED=true）")


class BossChatHeartbeatTriggerResponse(BaseModel):
    ok: bool = True
    summary: str
    process: BossChatProcessResponse
    notification_sent: bool = False
    notification_error: str | None = None
    error: str | None = None


class FormAutofillPreviewRequest(BaseModel):
    html: str = Field(..., min_length=20)
    profile: dict[str, str] = Field(default_factory=dict)


class FormAutofillField(BaseModel):
    selector: str
    tag: str
    input_type: str
    field_name: str | None = None
    label: str | None = None
    inferred_type: str | None = None
    suggested_value: str | None = None
    confidence: float = 0.0


class FormAutofillPreviewResponse(BaseModel):
    total_fields: int
    mapped_fields: int
    fields: List[FormAutofillField] = Field(default_factory=list)


class FormAutofillUrlPreviewRequest(BaseModel):
    url: str = Field(..., min_length=8)
    profile: dict[str, str] = Field(default_factory=dict)


class FormAutofillUrlPreviewResponse(BaseModel):
    url: str
    total_fields: int
    mapped_fields: int
    screenshot_path: str | None = None
    fields: List[FormAutofillField] = Field(default_factory=list)


class FormAutofillFillRequest(BaseModel):
    url: str = Field(..., min_length=8)
    profile: dict[str, str] = Field(default_factory=dict)
    confirm_fill: bool = Field(
        default=False,
        description="Must be true to execute fill actions on external pages",
    )
    max_actions: int = Field(default=20, ge=1, le=80)


class FormAutofillAction(BaseModel):
    selector: str
    status: Literal["filled", "skipped", "failed"]
    reason: str | None = None
    value_preview: str | None = None


class FormAutofillFillResponse(BaseModel):
    url: str
    attempted_fields: int
    filled_fields: int
    failed_fields: int
    screenshot_path: str | None = None
    actions: List[FormAutofillAction] = Field(default_factory=list)


class FormFillThreadPreview(BaseModel):
    total_fields: int
    mapped_fields: int
    screenshot_path: str | None = None
    fields: List[FormAutofillField] = Field(default_factory=list)


class FormFillStartRequest(BaseModel):
    url: str = Field(..., min_length=8)
    profile: dict[str, str] = Field(default_factory=dict)
    max_actions: int = Field(default=20, ge=1, le=80)


class FormFillStartResponse(BaseModel):
    thread_id: str
    status: str
    url: str
    message: str | None = None
    preview: FormFillThreadPreview


class FormFillReviewRequest(BaseModel):
    thread_id: str
    decision: Literal["approve", "reject"]
    feedback: str | None = None
    max_actions: int | None = Field(default=None, ge=1, le=80)


class FormFillReviewResponse(BaseModel):
    thread_id: str
    status: str
    message: str
    preview: FormFillThreadPreview | None = None
    fill_result: FormAutofillFillResponse | None = None


class FormFillPendingItem(BaseModel):
    thread_id: str
    url: str
    status: str
    mapped_fields: int = 0
    created_at: datetime
    updated_at: datetime


class FormFillThreadDetail(BaseModel):
    thread_id: str
    url: str
    status: str
    profile: dict[str, str] = Field(default_factory=dict)
    preview: FormFillThreadPreview | None = None
    fill_result: FormAutofillFillResponse | None = None
    last_feedback: str | None = None
    created_at: datetime
    updated_at: datetime


class EmailIngestRequest(BaseModel):
    sender: str = Field(..., min_length=3)
    subject: str = Field(..., min_length=1)
    body: str = Field(..., min_length=1)
    received_at: datetime | None = None


class EmailClassification(BaseModel):
    email_type: Literal["interview_invite", "rejection", "need_material", "irrelevant"]
    company: str | None = None
    interview_time: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str | None = None


class EmailIngestResponse(BaseModel):
    email_id: str
    classification: EmailClassification
    related_job_id: str | None = None
    updated_job_status: str | None = None
    schedule_event_id: str | None = None
    message: str


class EmailEventItem(BaseModel):
    id: str
    sender: str
    subject: str
    email_type: str
    company: str | None = None
    interview_time: str | None = None
    related_job_id: str | None = None
    updated_job_status: str | None = None
    created_at: datetime


class ScheduleEventItem(BaseModel):
    id: str
    company: str | None = None
    event_type: Literal["interview", "written_test", "other"] = "interview"
    start_at: datetime
    raw_time_text: str | None = None
    mode: Literal["online", "offline", "unknown"] = "unknown"
    location: str | None = None
    contact: str | None = None
    confidence: float = Field(default=0.0, ge=0, le=1)
    status: Literal["scheduled", "completed", "cancelled"] = "scheduled"
    source_email_id: str | None = None
    reminder_sent_at: datetime | None = None
    created_at: datetime


class EmailFetchRequest(BaseModel):
    max_items: int = Field(default=10, ge=1, le=50)
    mark_seen: bool = False


class EmailFetchItem(BaseModel):
    sender: str
    subject: str
    email_type: str
    company: str | None = None
    interview_time: str | None = None
    related_job_id: str | None = None
    updated_job_status: str | None = None


class EmailFetchResponse(BaseModel):
    fetched_count: int
    processed_count: int
    items: List[EmailFetchItem] = Field(default_factory=list)


class EmailHeartbeatStatusResponse(BaseModel):
    enabled_by_env: bool
    running: bool
    interval_sec: int
    max_items: int
    mark_seen: bool
    last_run_at: datetime | None = None
    last_success_at: datetime | None = None
    last_error: str | None = None
    last_fetched_count: int | None = None
    last_processed_count: int | None = None


class EmailHeartbeatControlResponse(BaseModel):
    running: bool
    message: str


class EmailHeartbeatTriggerResponse(BaseModel):
    message: str
    fetched_count: int
    processed_count: int
    schedule_reminders: int = 0
    upcoming_schedules: int = 0
    notification_sent: bool = False
    notification_error: str | None = None


class EmailHeartbeatNotifyTestRequest(BaseModel):
    message: str = Field(
        default="OfferPilot email heartbeat test notification",
        description="Test text sent to configured channel webhook",
    )


class EmailHeartbeatNotifyTestResponse(BaseModel):
    sent: bool
    message: str
    error: str | None = None


class ActionTimelineItem(BaseModel):
    action_id: str
    job_id: str | None = None
    job_title: str | None = None
    job_company: str | None = None
    action_type: str
    status: str | None = None
    input_summary: str | None = None
    output_summary: str | None = None
    screenshot_path: str | None = None
    created_at: datetime


class AgentEvalMetricsResponse(BaseModel):
    window_days: int
    evaluated_at: datetime
    score_consistency_std: float | None = None
    score_consistency_groups: int = 0
    autofill_accuracy: float | None = None
    autofill_total_fields: int = 0
    autofill_failed_fields: int = 0
    material_approve_rate: float | None = None
    material_approved: int = 0
    material_reviewed: int = 0
    e2e_latency_sec_p50: float | None = None
    e2e_latency_samples: int = 0


class CompanyIntelRequest(BaseModel):
    company: str = Field(..., min_length=1, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    jd_text: str | None = None
    focus_keywords: List[str] = Field(default_factory=list)
    max_results: int = Field(default=6, ge=1, le=12)
    include_search: bool = True


class CompanyIntelSource(BaseModel):
    title: str
    url: str
    snippet: str | None = None


class CompanyIntelResponse(BaseModel):
    company: str
    role_title: str | None = None
    summary: str
    business_direction: List[str] = Field(default_factory=list)
    tech_stack: List[str] = Field(default_factory=list)
    funding_stage: str | None = None
    team_size_stage: str | None = None
    interview_style: List[str] = Field(default_factory=list)
    risks: List[str] = Field(default_factory=list)
    suggestions: List[str] = Field(default_factory=list)
    confidence: float = Field(default=0.0, ge=0, le=1)
    sources: List[CompanyIntelSource] = Field(default_factory=list)


class InterviewPrepQuestion(BaseModel):
    question: str
    intent: str
    difficulty: Literal["easy", "medium", "hard"] = "medium"
    related_skill: str | None = None
    answer_tips: List[str] = Field(default_factory=list)


class InterviewPrepRequest(BaseModel):
    job_id: str | None = None
    company: str | None = Field(default=None, max_length=200)
    role_title: str | None = Field(default=None, max_length=200)
    jd_text: str | None = None
    use_company_intel: bool = True
    question_count: int = Field(default=8, ge=3, le=20)


class InterviewPrepResponse(BaseModel):
    company: str
    role_title: str
    summary: str
    likely_focus: List[str] = Field(default_factory=list)
    key_storylines: List[str] = Field(default_factory=list)
    questions: List[InterviewPrepQuestion] = Field(default_factory=list)
    company_intel: CompanyIntelResponse | None = None


class SecurityTokenIssueRequest(BaseModel):
    action: str = Field(..., min_length=2, max_length=80)
    purpose: str | None = Field(default=None, max_length=300)
    expire_minutes: int = Field(default=10, ge=1, le=24 * 60)


class SecurityTokenIssueResponse(BaseModel):
    token_id: str
    token: str
    action: str
    purpose: str | None = None
    expires_at: datetime


class SecurityTokenConsumeRequest(BaseModel):
    token: str = Field(..., min_length=12, max_length=300)
    action: str = Field(..., min_length=2, max_length=80)


class SecurityTokenConsumeResponse(BaseModel):
    valid: bool
    consumed: bool
    reason: str | None = None
    token_id: str | None = None


class ToolBudgetCheckRequest(BaseModel):
    session_id: str = Field(..., min_length=2, max_length=120)
    tool_type: str = Field(..., min_length=2, max_length=80)
    limit: int = Field(default=50, ge=1, le=5000)
    consume: int = Field(default=1, ge=0, le=500)
    dry_run: bool = False


class ToolBudgetCheckResponse(BaseModel):
    session_id: str
    tool_type: str
    limit: int
    used: int
    remaining: int
    allowed: bool
    reason: str | None = None


class ToolBudgetResetRequest(BaseModel):
    session_id: str = Field(..., min_length=2, max_length=120)
    tool_type: str = Field(..., min_length=2, max_length=80)


class ToolBudgetResetResponse(BaseModel):
    ok: bool
    session_id: str
    tool_type: str
