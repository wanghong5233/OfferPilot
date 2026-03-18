"use client";

import { FormEvent, useCallback, useEffect, useRef, useState } from "react";
import { ProfileForm } from "../components/ProfileForm";
import { ResumeUpload } from "../components/ResumeUpload";

type SimilarJob = {
  title: string;
  company: string;
  similarity: number;
  match_score?: number | null;
};

type AnalyzeResponse = {
  title: string;
  company: string;
  skills: string[];
  match_score: number;
  gap_analysis: string;
  resume_evidence: string[];
  similar_jobs: SimilarJob[];
};

type JobListItem = {
  id: string;
  title: string;
  company: string;
  source: string;
  match_score?: number | null;
  status: string;
  created_at: string;
};

type JobKanbanKey =
  | "todo_apply"
  | "applied"
  | "testing"
  | "interviewing"
  | "rejected"
  | "offer";

const JOB_KANBAN_COLUMNS: Array<{ key: JobKanbanKey; title: string }> = [
  { key: "todo_apply", title: "待投递" },
  { key: "applied", title: "已投递" },
  { key: "testing", title: "笔试中" },
  { key: "interviewing", title: "面试中" },
  { key: "rejected", title: "已拒" },
  { key: "offer", title: "已 Offer" },
];

const mapJobStatusToKanban = (rawStatus: string | null | undefined): JobKanbanKey => {
  const status = String(rawStatus || "").toLowerCase();
  if (status.includes("offer")) return "offer";
  if (status.includes("reject")) return "rejected";
  if (status.includes("interview")) return "interviewing";
  if (status.includes("written") || status.includes("test") || status.includes("assessment")) {
    return "testing";
  }
  if (status.includes("applied") || status.includes("submitted") || status.includes("need_material")) {
    return "applied";
  }
  return "todo_apply";
};

const QUICK_LINKS: Array<{ href: string; label: string }> = [
  { href: "#sec-resume", label: "简历与画像" },
  { href: "#sec-agent-monitor", label: "Agent监控" },
  { href: "#sec-boss-scan", label: "BOSS扫描" },
  { href: "#sec-boss-chat", label: "对话Copilot" },
  { href: "#sec-jobs", label: "岗位看板" },
  { href: "#sec-jd", label: "JD分析" },
  { href: "#sec-more", label: "更多工具 ▾" },
];

type MaterialDraft = {
  resume_bullets: string[];
  cover_letter: string;
  greeting_message: string;
};

type MaterialGenerateResponse = {
  status: string;
  thread_id?: string | null;
  job_id: string;
  resume_version: string;
  title: string;
  company: string;
  match_score?: number | null;
  message?: string | null;
  draft?: MaterialDraft | null;
};

type MaterialReviewResponse = {
  status: string;
  thread_id: string;
  message: string;
  draft?: MaterialDraft | null;
};

type MaterialThreadDetail = {
  thread_id: string;
  job_id: string;
  title: string;
  company: string;
  match_score?: number | null;
  resume_version: string;
  status: string;
  last_feedback?: string | null;
  created_at: string;
  updated_at: string;
  draft?: MaterialDraft | null;
};

type MaterialExportResponse = {
  thread_id: string;
  format: "pdf" | "txt";
  file_name: string;
  file_path: string;
  download_url: string;
};

type PendingMaterialItem = {
  thread_id: string;
  job_id: string;
  title: string;
  company: string;
  match_score?: number | null;
  resume_version: string;
  created_at: string;
  updated_at: string;
  draft?: MaterialDraft | null;
};

type BossScanItem = {
  title: string;
  company: string;
  source_url?: string | null;
  snippet?: string | null;
  match_score?: number | null;
};

type BossScanResponse = {
  keyword: string;
  total: number;
  pages_scanned: number;
  screenshot_path?: string | null;
  items: BossScanItem[];
};

type UserProfileResponse = {
  profile_id: string;
  profile: Record<string, unknown>;
  updated_at?: string | null;
};

type BossChatReplyPreviewResponse = {
  intent: string;
  confidence: number;
  action: "send_resume" | "reply_from_profile" | "notify_user" | "ignore";
  reason: string;
  extracted_question?: string | null;
  reply_text?: string | null;
  needs_send_resume: boolean;
  needs_user_intervention: boolean;
  matched_profile_fields: string[];
  notification_sent: boolean;
  notification_error?: string | null;
  used_profile_id: string;
};

type BossChatConversationItem = {
  conversation_id: string;
  hr_name: string;
  company?: string | null;
  job_title?: string | null;
  unread_count: number;
  latest_message?: string | null;
  latest_time?: string | null;
  latest_hr_message?: string | null;
  latest_hr_time?: string | null;
  preview?: string | null;
};

type BossChatPullResponse = {
  total: number;
  unread_total: number;
  screenshot_path?: string | null;
  items: BossChatConversationItem[];
};

type BossChatProcessItem = {
  conversation_id: string;
  hr_name: string;
  company?: string | null;
  job_title?: string | null;
  latest_hr_message: string;
  latest_hr_time?: string | null;
  message_signature: string;
  is_new: boolean;
  intent: string;
  confidence: number;
  action: "send_resume" | "reply_from_profile" | "notify_user" | "ignore";
  reason: string;
  reply_text?: string | null;
  needs_send_resume: boolean;
  needs_user_intervention: boolean;
  notification_sent: boolean;
  notification_error?: string | null;
  source_fit_score?: number | null;
  source_fit_passed?: boolean | null;
  source_fit_reason?: string | null;
  proactive_contact?: boolean | null;
  proactive_confidence?: number | null;
  proactive_reason?: string | null;
  proactive_match_score?: number | null;
  proactive_match_passed?: boolean | null;
  proactive_jd_match_score?: number | null;
  proactive_gap_analysis?: string | null;
  reply_sent?: boolean;
  reply_sent_error?: string | null;
};

type BossChatProcessResponse = {
  total_conversations: number;
  candidate_messages: number;
  processed_count: number;
  new_count: number;
  duplicated_count: number;
  screenshot_path?: string | null;
  items: BossChatProcessItem[];
};

type BossChatHeartbeatTriggerResponse = {
  ok: boolean;
  summary: string;
  process: BossChatProcessResponse;
  notification_sent: boolean;
  notification_error?: string | null;
  error?: string | null;
};

type ResumeSourceResponse = {
  source_id: string;
  resume_text: string;
  updated_at: string;
};

type DiffRow = {
  kind: "same" | "added" | "removed" | "changed";
  left: string;
  right: string;
};

type AutofillField = {
  selector: string;
  tag: string;
  input_type: string;
  field_name?: string | null;
  label?: string | null;
  inferred_type?: string | null;
  suggested_value?: string | null;
  confidence: number;
};

type AutofillPreviewResponse = {
  url?: string;
  total_fields: number;
  mapped_fields: number;
  screenshot_path?: string | null;
  fields: AutofillField[];
};

type AutofillFillAction = {
  selector: string;
  status: "filled" | "skipped" | "failed";
  reason?: string | null;
  value_preview?: string | null;
};

type AutofillFillResponse = {
  url: string;
  attempted_fields: number;
  filled_fields: number;
  failed_fields: number;
  screenshot_path?: string | null;
  actions: AutofillFillAction[];
};

type FormFillThreadPreview = {
  total_fields: number;
  mapped_fields: number;
  screenshot_path?: string | null;
  fields: AutofillField[];
};

type FormFillStartResponse = {
  thread_id: string;
  status: string;
  url: string;
  message?: string | null;
  preview: FormFillThreadPreview;
};

type FormFillReviewResponse = {
  thread_id: string;
  status: string;
  message: string;
  preview?: FormFillThreadPreview | null;
  fill_result?: AutofillFillResponse | null;
};

type FormFillPendingItem = {
  thread_id: string;
  url: string;
  status: string;
  mapped_fields: number;
  created_at: string;
  updated_at: string;
};

type FormFillThreadDetail = {
  thread_id: string;
  url: string;
  status: string;
  profile: Record<string, string>;
  preview?: FormFillThreadPreview | null;
  fill_result?: AutofillFillResponse | null;
  last_feedback?: string | null;
  created_at: string;
  updated_at: string;
};

type EmailClassification = {
  email_type: "interview_invite" | "rejection" | "need_material" | "irrelevant";
  company?: string | null;
  interview_time?: string | null;
  confidence: number;
  reason?: string | null;
};

type EmailIngestResponse = {
  email_id: string;
  classification: EmailClassification;
  related_job_id?: string | null;
  updated_job_status?: string | null;
  schedule_event_id?: string | null;
  message: string;
};

type EmailEventItem = {
  id: string;
  sender: string;
  subject: string;
  email_type: string;
  company?: string | null;
  interview_time?: string | null;
  related_job_id?: string | null;
  updated_job_status?: string | null;
  created_at: string;
};

type EmailFetchResponse = {
  fetched_count: number;
  processed_count: number;
  items: Array<{
    sender: string;
    subject: string;
    email_type: string;
    company?: string | null;
    interview_time?: string | null;
    related_job_id?: string | null;
    updated_job_status?: string | null;
  }>;
};

type EmailHeartbeatStatus = {
  enabled_by_env: boolean;
  running: boolean;
  interval_sec: number;
  max_items: number;
  mark_seen: boolean;
  last_run_at?: string | null;
  last_success_at?: string | null;
  last_error?: string | null;
  last_fetched_count?: number | null;
  last_processed_count?: number | null;
};

type EmailHeartbeatControlResponse = {
  running: boolean;
  message: string;
};

type EmailHeartbeatTriggerResponse = {
  message: string;
  fetched_count: number;
  processed_count: number;
  schedule_reminders: number;
  upcoming_schedules: number;
  notification_sent: boolean;
  notification_error?: string | null;
};

type ScheduleEventItem = {
  id: string;
  company?: string | null;
  event_type: "interview" | "written_test" | "other";
  start_at: string;
  raw_time_text?: string | null;
  mode: "online" | "offline" | "unknown";
  location?: string | null;
  contact?: string | null;
  confidence: number;
  status: "scheduled" | "completed" | "cancelled";
  source_email_id?: string | null;
  reminder_sent_at?: string | null;
  created_at: string;
};

type ActionTimelineItem = {
  action_id: string;
  job_id?: string | null;
  job_title?: string | null;
  job_company?: string | null;
  action_type: string;
  status?: string | null;
  input_summary?: string | null;
  output_summary?: string | null;
  screenshot_path?: string | null;
  created_at: string;
};

type AgentEvent = {
  timestamp: string;
  event_type: string;
  detail: string;
  metadata: Record<string, unknown>;
};

const EVENT_TYPE_COLORS: Record<string, string> = {
  browser_launch: "bg-blue-100 text-blue-800",
  browser_navigate: "bg-blue-50 text-blue-700",
  browser_click: "bg-blue-50 text-blue-600",
  browser_input: "bg-blue-50 text-blue-600",
  browser_screenshot: "bg-indigo-100 text-indigo-800",
  browser_close: "bg-zinc-100 text-zinc-600",
  browser_extract: "bg-cyan-100 text-cyan-800",
  llm_call: "bg-purple-100 text-purple-800",
  llm_response: "bg-purple-50 text-purple-700",
  intent_classified: "bg-amber-100 text-amber-800",
  safety_check: "bg-orange-100 text-orange-800",
  safety_blocked: "bg-red-100 text-red-800",
  reply_generated: "bg-green-100 text-green-800",
  reply_sent: "bg-emerald-100 text-emerald-800",
  action_logged: "bg-zinc-100 text-zinc-700",
  workflow_start: "bg-violet-100 text-violet-800",
  workflow_node: "bg-violet-50 text-violet-700",
  workflow_end: "bg-violet-100 text-violet-800",
  info: "bg-sky-50 text-sky-700",
  warning: "bg-yellow-100 text-yellow-800",
  error: "bg-red-100 text-red-800",
};

type AgentEvalMetricsResponse = {
  window_days: number;
  evaluated_at: string;
  score_consistency_std?: number | null;
  score_consistency_groups: number;
  autofill_accuracy?: number | null;
  autofill_total_fields: number;
  autofill_failed_fields: number;
  material_approve_rate?: number | null;
  material_approved: number;
  material_reviewed: number;
  e2e_latency_sec_p50?: number | null;
  e2e_latency_samples: number;
};

type CompanyIntelSource = {
  title: string;
  url: string;
  snippet?: string | null;
};

type CompanyIntelResponse = {
  company: string;
  role_title?: string | null;
  summary: string;
  business_direction: string[];
  tech_stack: string[];
  funding_stage?: string | null;
  team_size_stage?: string | null;
  interview_style: string[];
  risks: string[];
  suggestions: string[];
  confidence: number;
  sources: CompanyIntelSource[];
};

type InterviewPrepQuestion = {
  question: string;
  intent: string;
  difficulty: "easy" | "medium" | "hard";
  related_skill?: string | null;
  answer_tips: string[];
};

type InterviewPrepResponse = {
  company: string;
  role_title: string;
  summary: string;
  likely_focus: string[];
  key_storylines: string[];
  questions: InterviewPrepQuestion[];
  company_intel?: CompanyIntelResponse | null;
};

type SecurityTokenIssueResponse = {
  token_id: string;
  token: string;
  action: string;
  purpose?: string | null;
  expires_at: string;
};

type SecurityTokenConsumeResponse = {
  valid: boolean;
  consumed: boolean;
  reason?: string | null;
  token_id?: string | null;
};

type ToolBudgetCheckResponse = {
  session_id: string;
  tool_type: string;
  limit: number;
  used: number;
  remaining: number;
  allowed: boolean;
  reason?: string | null;
};

function splitLines(text: string): string[] {
  return text.replace(/\r\n/g, "\n").split("\n");
}

function buildTailoredResumePreview(originalResume: string, draft: MaterialDraft): string {
  const lines = splitLines(originalResume);
  const bullets = draft.resume_bullets.map((item) => `- ${item}`);
  const projectIdx = lines.findIndex((line) => line.includes("项目经历"));
  if (projectIdx < 0) {
    return [originalResume.trim(), "", "定制简历要点", ...bullets].join("\n").trim();
  }

  let sectionEnd = lines.length;
  for (let idx = projectIdx + 1; idx < lines.length; idx += 1) {
    const text = lines[idx].trim();
    if (!text) {
      continue;
    }
    if (idx > projectIdx + 1 && !text.startsWith("-") && !text.startsWith("•")) {
      sectionEnd = idx;
      break;
    }
    if (text.includes("技能") || text.includes("教育背景") || text.includes("经历")) {
      sectionEnd = idx;
      break;
    }
  }
  const nextLines = [
    ...lines.slice(0, projectIdx + 1),
    ...bullets,
    ...lines.slice(sectionEnd),
  ];
  return nextLines.join("\n");
}

function buildLineDiffRows(leftText: string, rightText: string): DiffRow[] {
  const left = splitLines(leftText);
  const right = splitLines(rightText);
  const n = left.length;
  const m = right.length;
  const dp = Array.from({ length: n + 1 }, () => Array<number>(m + 1).fill(0));

  for (let i = n - 1; i >= 0; i -= 1) {
    for (let j = m - 1; j >= 0; j -= 1) {
      if (left[i] === right[j]) {
        dp[i][j] = dp[i + 1][j + 1] + 1;
      } else {
        dp[i][j] = Math.max(dp[i + 1][j], dp[i][j + 1]);
      }
    }
  }

  const raw: DiffRow[] = [];
  let i = 0;
  let j = 0;
  while (i < n && j < m) {
    if (left[i] === right[j]) {
      raw.push({ kind: "same", left: left[i], right: right[j] });
      i += 1;
      j += 1;
      continue;
    }
    if (dp[i + 1][j] >= dp[i][j + 1]) {
      raw.push({ kind: "removed", left: left[i], right: "" });
      i += 1;
    } else {
      raw.push({ kind: "added", left: "", right: right[j] });
      j += 1;
    }
  }
  while (i < n) {
    raw.push({ kind: "removed", left: left[i], right: "" });
    i += 1;
  }
  while (j < m) {
    raw.push({ kind: "added", left: "", right: right[j] });
    j += 1;
  }

  const merged: DiffRow[] = [];
  for (let k = 0; k < raw.length; k += 1) {
    const cur = raw[k];
    const next = raw[k + 1];
    if (cur.kind === "removed" && next?.kind === "added") {
      merged.push({ kind: "changed", left: cur.left, right: next.right });
      k += 1;
      continue;
    }
    merged.push(cur);
  }
  return merged;
}

const API_BASE_URL =
  process.env.NEXT_PUBLIC_API_BASE_URL ?? "http://127.0.0.1:8010";

export default function Home() {
  const [jdText, setJdText] = useState(
    "AI Agent intern role. Require Python, LangGraph, RAG, MCP, Playwright.",
  );
  const [analysis, setAnalysis] = useState<AnalyzeResponse | null>(null);
  const [analyzing, setAnalyzing] = useState(false);
  const [analyzeError, setAnalyzeError] = useState<string | null>(null);

  const [resumeText, setResumeText] = useState(
    [
      "教育背景",
      "- 211硕士，研究方向：大模型应用与Agent系统",
      "",
      "项目经历",
      "- Python + FastAPI + LangGraph 构建 Agent 工作流（含审批节点）",
      "- ChromaDB 做 RAG 检索，支持历史 JD 相似度",
      "- Playwright 自动化网页操作和表单填充",
      "",
      "技能",
      "- Python, LangGraph, LangChain, RAG, MCP, PostgreSQL, ChromaDB, Playwright",
    ].join("\n"),
  );
  const [resumeSourceId, setResumeSourceId] = useState("resume_v1");
  const [indexingResume, setIndexingResume] = useState(false);
  const [indexedChunks, setIndexedChunks] = useState<number | null>(null);
  const [indexError, setIndexError] = useState<string | null>(null);

  const [recentJobs, setRecentJobs] = useState<JobListItem[]>([]);
  const [loadingJobs, setLoadingJobs] = useState(false);
  const [jobsError, setJobsError] = useState<string | null>(null);

  const [materialJobId, setMaterialJobId] = useState("");
  const [materialResumeVersion, setMaterialResumeVersion] = useState("resume_v1");
  const [materialThreadId, setMaterialThreadId] = useState<string | null>(null);
  const [currentResumeVersion, setCurrentResumeVersion] = useState("resume_v1");
  const [materialDraft, setMaterialDraft] = useState<MaterialDraft | null>(null);
  const [previousMaterialDraft, setPreviousMaterialDraft] =
    useState<MaterialDraft | null>(null);
  const [resumeDiffRows, setResumeDiffRows] = useState<DiffRow[]>([]);
  const [materialStatus, setMaterialStatus] = useState<string | null>(null);
  const [materialMessage, setMaterialMessage] = useState<string | null>(null);
  const [materialFeedback, setMaterialFeedback] = useState("");
  const [materialError, setMaterialError] = useState<string | null>(null);
  const [generatingMaterial, setGeneratingMaterial] = useState(false);
  const [reviewingMaterial, setReviewingMaterial] = useState(false);
  const [exportingMaterial, setExportingMaterial] = useState(false);

  const [pendingMaterials, setPendingMaterials] = useState<PendingMaterialItem[]>([]);
  const [loadingPendingMaterials, setLoadingPendingMaterials] = useState(false);

  const [bossKeyword, setBossKeyword] = useState("AI Agent 实习");
  const [scanningBoss, setScanningBoss] = useState(false);
  const [bossMaxPages, setBossMaxPages] = useState(1);
  const [bossPagesScanned, setBossPagesScanned] = useState<number | null>(null);
  const [bossItems, setBossItems] = useState<BossScanItem[]>([]);
  const [bossScreenshotPath, setBossScreenshotPath] = useState<string | null>(null);
  const [bossError, setBossError] = useState<string | null>(null);
  const [cachedProfile, setCachedProfile] = useState<Record<string, unknown> | null>(null);

  const [bossHrMessage, setBossHrMessage] = useState("你好，请问你的期望日薪是多少？工作地点是哪里？");
  const [bossHrCompany, setBossHrCompany] = useState("某AI初创公司");
  const [bossHrJobTitle, setBossHrJobTitle] = useState("AI Agent 实习生");
  const [bossNotifyOnEscalate, setBossNotifyOnEscalate] = useState(true);
  const [bossChatAutoExecute, setBossChatAutoExecute] = useState(false);
  const [bossPreviewingReply, setBossPreviewingReply] = useState(false);
  const [bossReplyPreview, setBossReplyPreview] = useState<BossChatReplyPreviewResponse | null>(null);
  const [bossChatError, setBossChatError] = useState<string | null>(null);
  const [bossChatPulling, setBossChatPulling] = useState(false);
  const [bossChatUnreadOnly, setBossChatUnreadOnly] = useState(true);
  const [bossChatItems, setBossChatItems] = useState<BossChatConversationItem[]>([]);
  const [bossChatScreenshotPath, setBossChatScreenshotPath] = useState<string | null>(null);
  const [bossChatProcessing, setBossChatProcessing] = useState(false);
  const [bossChatProcessed, setBossChatProcessed] = useState<BossChatProcessItem[]>([]);
  const [bossChatProcessSummary, setBossChatProcessSummary] = useState<string | null>(null);
  const [bossChatHeartbeatTriggering, setBossChatHeartbeatTriggering] = useState(false);
  const [bossChatHeartbeatSummary, setBossChatHeartbeatSummary] = useState<string | null>(null);

  const [autofillHtml, setAutofillHtml] = useState(
    "<form>\n  <label for='candidateName'>姓名</label>\n  <input id='candidateName' name='name' />\n  <label for='candidateEmail'>邮箱</label>\n  <input id='candidateEmail' type='email' />\n  <textarea name='projectSummary' placeholder='请填写项目经历'></textarea>\n</form>",
  );
  const [autofillProfileJson, setAutofillProfileJson] = useState(
    '{\n  "name": "张三",\n  "email": "zhangsan@example.com",\n  "phone": "13800000000",\n  "project_summary": "负责 Agent 工作流与 RAG 检索系统开发。"\n}',
  );
  const [autofillTargetUrl, setAutofillTargetUrl] = useState(
    "data:text/html,%3Cform%3E%3Clabel%20for%3D%27candidateName%27%3E%E5%A7%93%E5%90%8D%3C%2Flabel%3E%3Cinput%20id%3D%27candidateName%27%20name%3D%27name%27%20%2F%3E%3Clabel%20for%3D%27candidateEmail%27%3E%E9%82%AE%E7%AE%B1%3C%2Flabel%3E%3Cinput%20id%3D%27candidateEmail%27%20type%3D%27email%27%20%2F%3E%3Ctextarea%20name%3D%27projectSummary%27%20placeholder%3D%27%E8%AF%B7%E5%A1%AB%E5%86%99%E9%A1%B9%E7%9B%AE%E7%BB%8F%E5%8E%86%27%3E%3C%2Ftextarea%3E%3C%2Fform%3E",
  );
  const [autofillUrlPreviewing, setAutofillUrlPreviewing] = useState(false);
  const [autofillFilling, setAutofillFilling] = useState(false);
  const [autofillConfirmFill, setAutofillConfirmFill] = useState(false);
  const [autofillReviewFeedback, setAutofillReviewFeedback] = useState("");
  const [autofillLoading, setAutofillLoading] = useState(false);
  const [autofillError, setAutofillError] = useState<string | null>(null);
  const [autofillPreview, setAutofillPreview] = useState<AutofillPreviewResponse | null>(null);
  const [autofillFillResult, setAutofillFillResult] = useState<AutofillFillResponse | null>(null);
  const [formFillThreadId, setFormFillThreadId] = useState<string | null>(null);
  const [formFillStatus, setFormFillStatus] = useState<string | null>(null);
  const [formFillMessage, setFormFillMessage] = useState<string | null>(null);
  const [startingFormFill, setStartingFormFill] = useState(false);
  const [reviewingFormFill, setReviewingFormFill] = useState(false);
  const [pendingFormFills, setPendingFormFills] = useState<FormFillPendingItem[]>([]);
  const [loadingPendingFormFills, setLoadingPendingFormFills] = useState(false);

  const [emailSender, setEmailSender] = useState("hr@offerpilot.ai");
  const [emailSubject, setEmailSubject] = useState("【OfferPilot】面试邀请：2026-03-20 14:00");
  const [emailBody, setEmailBody] = useState(
    "你好，邀请你参加一面，请于2026-03-20 14:00线上面试。",
  );
  const [ingestingEmail, setIngestingEmail] = useState(false);
  const [emailResult, setEmailResult] = useState<EmailIngestResponse | null>(null);
  const [emailError, setEmailError] = useState<string | null>(null);
  const [emailEvents, setEmailEvents] = useState<EmailEventItem[]>([]);
  const [loadingEmailEvents, setLoadingEmailEvents] = useState(false);
  const [fetchingInboxEmails, setFetchingInboxEmails] = useState(false);
  const [emailFetchMessage, setEmailFetchMessage] = useState<string | null>(null);
  const [emailHeartbeatStatus, setEmailHeartbeatStatus] = useState<EmailHeartbeatStatus | null>(null);
  const [upcomingSchedules, setUpcomingSchedules] = useState<ScheduleEventItem[]>([]);
  const [loadingSchedules, setLoadingSchedules] = useState(false);
  const [loadingHeartbeatStatus, setLoadingHeartbeatStatus] = useState(false);
  const [controllingHeartbeat, setControllingHeartbeat] = useState(false);
  const [testingNotify, setTestingNotify] = useState(false);
  const [actionTimeline, setActionTimeline] = useState<ActionTimelineItem[]>([]);
  const [loadingActionTimeline, setLoadingActionTimeline] = useState(false);
  const [evalMetrics, setEvalMetrics] = useState<AgentEvalMetricsResponse | null>(null);
  const [loadingEvalMetrics, setLoadingEvalMetrics] = useState(false);

  const [agentEvents, setAgentEvents] = useState<AgentEvent[]>([]);
  const [agentSseConnected, setAgentSseConnected] = useState(false);
  const [agentMonitorAutoScroll, setAgentMonitorAutoScroll] = useState(true);
  const [agentEventFilter, setAgentEventFilter] = useState<string>("all");
  const agentLogEndRef = useRef<HTMLDivElement>(null);
  const eventSourceRef = useRef<EventSource | null>(null);

  const [intelCompany, setIntelCompany] = useState("OfferPilot Labs");
  const [intelRoleTitle, setIntelRoleTitle] = useState("AI Agent Intern");
  const [intelJdText, setIntelJdText] = useState(
    "Need Python, LangGraph, RAG, MCP, Playwright; focus on Agent workflow and delivery.",
  );
  const [intelLoading, setIntelLoading] = useState(false);
  const [intelResult, setIntelResult] = useState<CompanyIntelResponse | null>(null);
  const [intelError, setIntelError] = useState<string | null>(null);

  const [prepJobId, setPrepJobId] = useState("");
  const [prepQuestionCount, setPrepQuestionCount] = useState(8);
  const [prepUseCompanyIntel, setPrepUseCompanyIntel] = useState(true);
  const [prepLoading, setPrepLoading] = useState(false);
  const [prepResult, setPrepResult] = useState<InterviewPrepResponse | null>(null);
  const [prepError, setPrepError] = useState<string | null>(null);

  const [securityAction, setSecurityAction] = useState("submit_application");
  const [securityPurpose, setSecurityPurpose] = useState("manual approval for external submit");
  const [issuedToken, setIssuedToken] = useState<SecurityTokenIssueResponse | null>(null);
  const [tokenConsumeResult, setTokenConsumeResult] = useState<SecurityTokenConsumeResponse | null>(null);
  const [tokenLoading, setTokenLoading] = useState(false);
  const [budgetSessionId, setBudgetSessionId] = useState("offerpilot-demo");
  const [budgetToolType, setBudgetToolType] = useState("browser");
  const [budgetLimit, setBudgetLimit] = useState(20);
  const [budgetConsume, setBudgetConsume] = useState(1);
  const [budgetDryRun, setBudgetDryRun] = useState(false);
  const [budgetLoading, setBudgetLoading] = useState(false);
  const [budgetResult, setBudgetResult] = useState<ToolBudgetCheckResponse | null>(null);
  const [securityError, setSecurityError] = useState<string | null>(null);

  const fetchRecentJobs = async () => {
    setLoadingJobs(true);
    setJobsError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/jobs/recent?limit=20`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as JobListItem[];
      setRecentJobs(data);
    } catch (error) {
      setJobsError(`加载最近岗位失败：${String(error)}`);
    } finally {
      setLoadingJobs(false);
    }
  };

  const fetchPendingMaterials = async () => {
    setLoadingPendingMaterials(true);
    setMaterialError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/material/pending`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as PendingMaterialItem[];
      setPendingMaterials(data);
    } catch (error) {
      setMaterialError(`加载待审批材料失败：${String(error)}`);
    } finally {
      setLoadingPendingMaterials(false);
    }
  };

  const fetchCachedProfile = useCallback(async () => {
    try {
      const resp = await fetch(`${API_BASE_URL}/api/profile?profile_id=default`);
      if (resp.ok) {
        const data = (await resp.json()) as UserProfileResponse;
        setCachedProfile((data.profile ?? {}) as Record<string, unknown>);
      }
    } catch { /* ignore */ }
  }, []);

  const handleBossReplyPreview = async () => {
    setBossPreviewingReply(true);
    setBossChatError(null);
    setBossReplyPreview(null);
    try {
      const profile = cachedProfile ?? {};
      const resp = await fetch(`${API_BASE_URL}/api/boss/chat/reply-preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          hr_message: bossHrMessage,
          profile_id: "default",
          profile_override: profile,
          company: bossHrCompany || null,
          job_title: bossHrJobTitle || null,
          notify_on_escalate: bossNotifyOnEscalate,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as BossChatReplyPreviewResponse;
      setBossReplyPreview(data);
      await fetchActionTimeline();
    } catch (error) {
      setBossChatError(`消息回复预览失败：${String(error)}`);
    } finally {
      setBossPreviewingReply(false);
    }
  };

  const handleBossChatPull = async () => {
    setBossChatPulling(true);
    setBossChatError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/boss/chat/pull`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_conversations: 30,
          unread_only: bossChatUnreadOnly,
          fetch_latest_hr: true,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as BossChatPullResponse;
      setBossChatItems(data.items ?? []);
      setBossChatScreenshotPath(data.screenshot_path ?? null);
      await fetchActionTimeline();
    } catch (error) {
      setBossChatError(`拉取聊天列表失败：${String(error)}。请先确保 BOSS 已登录。`);
    } finally {
      setBossChatPulling(false);
    }
  };

  const handleBossChatProcess = async () => {
    setBossChatProcessing(true);
    setBossChatError(null);
    setBossChatProcessSummary(null);
    setBossChatHeartbeatSummary(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/boss/chat/process`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_conversations: 30,
          unread_only: bossChatUnreadOnly,
          profile_id: "default",
          notify_on_escalate: bossNotifyOnEscalate,
          fetch_latest_hr: true,
          auto_execute: bossChatAutoExecute,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as BossChatProcessResponse;
      setBossChatProcessed(data.items ?? []);
      setBossChatScreenshotPath(data.screenshot_path ?? null);
      setBossChatProcessSummary(
        `会话 ${data.total_conversations}，候选消息 ${data.candidate_messages}，处理 ${data.processed_count}，新增 ${data.new_count}，去重跳过 ${data.duplicated_count}。`,
      );
      await fetchActionTimeline();
    } catch (error) {
      setBossChatError(`批量处理失败：${String(error)}。请先确保 BOSS 已登录并可访问聊天页面。`);
    } finally {
      setBossChatProcessing(false);
    }
  };

  const handleBossChatHeartbeatTrigger = async () => {
    setBossChatHeartbeatTriggering(true);
    setBossChatError(null);
    setBossChatHeartbeatSummary(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/boss/chat/heartbeat/trigger`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          max_conversations: 30,
          unread_only: bossChatUnreadOnly,
          profile_id: "default",
          notify_on_escalate: bossNotifyOnEscalate,
          fetch_latest_hr: true,
          notify_channel_on_hits: false,
          auto_execute: bossChatAutoExecute,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as BossChatHeartbeatTriggerResponse;
      setBossChatHeartbeatSummary(data.summary || null);
      setBossChatProcessed(data.process?.items ?? []);
      setBossChatScreenshotPath(data.process?.screenshot_path ?? null);
      if (data.ok === false) {
        setBossChatError(data.error || data.summary || "巡检失败");
        setBossChatProcessSummary("");
      } else {
        setBossChatError(null);
        setBossChatProcessSummary(
          `会话 ${data.process.total_conversations}，候选消息 ${data.process.candidate_messages}，处理 ${data.process.processed_count}，新增 ${data.process.new_count}，去重跳过 ${data.process.duplicated_count}。`,
        );
      }
      await fetchActionTimeline();
    } catch (error) {
      setBossChatError(`触发巡检失败：${String(error)}。请先确保 BOSS 已登录并可访问聊天页面。`);
    } finally {
      setBossChatHeartbeatTriggering(false);
    }
  };

  const parseAutofillProfile = (): Record<string, string> => {
    const parsed = JSON.parse(autofillProfileJson) as Record<string, unknown>;
    return Object.fromEntries(
      Object.entries(parsed).map(([key, value]) => [key, String(value ?? "")]),
    );
  };

  const fetchPendingFormFills = async () => {
    setLoadingPendingFormFills(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/form/fill/pending?limit=30`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as FormFillPendingItem[];
      setPendingFormFills(data);
    } catch {
      // Keep UI resilient: pending list is optional for local demo.
      setPendingFormFills([]);
    } finally {
      setLoadingPendingFormFills(false);
    }
  };

  const fetchRecentEmails = async () => {
    setLoadingEmailEvents(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/recent?limit=20`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailEventItem[];
      setEmailEvents(data);
    } catch {
      setEmailEvents([]);
    } finally {
      setLoadingEmailEvents(false);
    }
  };

  const fetchUpcomingSchedules = async () => {
    setLoadingSchedules(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/schedules/upcoming?limit=20&days=14`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as ScheduleEventItem[];
      setUpcomingSchedules(data);
    } catch {
      setUpcomingSchedules([]);
    } finally {
      setLoadingSchedules(false);
    }
  };

  const fetchEmailHeartbeatStatus = async () => {
    setLoadingHeartbeatStatus(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/heartbeat/status`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailHeartbeatStatus;
      setEmailHeartbeatStatus(data);
    } catch {
      setEmailHeartbeatStatus(null);
    } finally {
      setLoadingHeartbeatStatus(false);
    }
  };

  const fetchActionTimeline = async () => {
    setLoadingActionTimeline(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/actions/timeline?limit=40`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as ActionTimelineItem[];
      setActionTimeline(data);
    } catch {
      setActionTimeline([]);
    } finally {
      setLoadingActionTimeline(false);
    }
  };

  const fetchEvalMetrics = async () => {
    setLoadingEvalMetrics(true);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/eval/metrics?window_days=14`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as AgentEvalMetricsResponse;
      setEvalMetrics(data);
    } catch {
      setEvalMetrics(null);
    } finally {
      setLoadingEvalMetrics(false);
    }
  };

  const handleGenerateCompanyIntel = async () => {
    setIntelLoading(true);
    setIntelError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/company/intel`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          company: intelCompany,
          role_title: intelRoleTitle || null,
          jd_text: intelJdText || null,
          focus_keywords: ["技术栈", "面试流程", "融资"],
          max_results: 6,
          include_search: true,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as CompanyIntelResponse;
      setIntelResult(data);
      setPrepError(null);
    } catch (error) {
      setIntelError(`公司情报生成失败：${String(error)}`);
    } finally {
      setIntelLoading(false);
    }
  };

  const handleGenerateInterviewPrep = async () => {
    setPrepLoading(true);
    setPrepError(null);
    try {
      const payload = prepJobId.trim()
        ? {
            job_id: prepJobId.trim(),
            use_company_intel: prepUseCompanyIntel,
            question_count: prepQuestionCount,
          }
        : {
            company: intelCompany,
            role_title: intelRoleTitle || null,
            jd_text: intelJdText || null,
            use_company_intel: prepUseCompanyIntel,
            question_count: prepQuestionCount,
          };
      const resp = await fetch(`${API_BASE_URL}/api/interview/prep`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify(payload),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as InterviewPrepResponse;
      setPrepResult(data);
      if (data.company_intel) {
        setIntelResult(data.company_intel);
      }
      await fetchActionTimeline();
    } catch (error) {
      setPrepError(`面试题生成失败：${String(error)}`);
    } finally {
      setPrepLoading(false);
    }
  };

  const handleIssueSecurityToken = async () => {
    setTokenLoading(true);
    setSecurityError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/security/token/issue`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          action: securityAction,
          purpose: securityPurpose || null,
          expire_minutes: 10,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as SecurityTokenIssueResponse;
      setIssuedToken(data);
      setTokenConsumeResult(null);
    } catch (error) {
      setSecurityError(`签发令牌失败：${String(error)}`);
    } finally {
      setTokenLoading(false);
    }
  };

  const handleConsumeSecurityToken = async () => {
    if (!issuedToken?.token) {
      setSecurityError("请先签发 token。");
      return;
    }
    setTokenLoading(true);
    setSecurityError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/security/token/consume`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          token: issuedToken.token,
          action: securityAction,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as SecurityTokenConsumeResponse;
      setTokenConsumeResult(data);
    } catch (error) {
      setSecurityError(`消费令牌失败：${String(error)}`);
    } finally {
      setTokenLoading(false);
    }
  };

  const handleCheckBudget = async () => {
    setBudgetLoading(true);
    setSecurityError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/security/budget/check`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: budgetSessionId,
          tool_type: budgetToolType,
          limit: budgetLimit,
          consume: budgetConsume,
          dry_run: budgetDryRun,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as ToolBudgetCheckResponse;
      setBudgetResult(data);
    } catch (error) {
      setSecurityError(`预算校验失败：${String(error)}`);
    } finally {
      setBudgetLoading(false);
    }
  };

  const handleResetBudget = async () => {
    setBudgetLoading(true);
    setSecurityError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/security/budget/reset`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          session_id: budgetSessionId,
          tool_type: budgetToolType,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      setBudgetResult(null);
    } catch (error) {
      setSecurityError(`预算重置失败：${String(error)}`);
    } finally {
      setBudgetLoading(false);
    }
  };

  useEffect(() => {
    void fetchRecentJobs();
    void fetchPendingMaterials();
    void fetchCachedProfile();
    void fetchPendingFormFills();
    void fetchRecentEmails();
    void fetchUpcomingSchedules();
    void fetchEmailHeartbeatStatus();
    void fetchActionTimeline();
    void fetchEvalMetrics();
  }, []);

  const connectAgentSSE = useCallback(() => {
    if (eventSourceRef.current) {
      eventSourceRef.current.close();
    }
    const es = new EventSource(`${API_BASE_URL}/api/agent/events`);
    eventSourceRef.current = es;
    es.onopen = () => setAgentSseConnected(true);
    es.onmessage = (msg) => {
      try {
        const evt: AgentEvent = JSON.parse(msg.data);
        setAgentEvents((prev) => {
          const next = [...prev, evt];
          return next.length > 500 ? next.slice(-300) : next;
        });
      } catch { /* ignore malformed */ }
    };
    es.onerror = () => {
      setAgentSseConnected(false);
      es.close();
      setTimeout(connectAgentSSE, 3000);
    };
  }, []);

  useEffect(() => {
    connectAgentSSE();
    return () => {
      eventSourceRef.current?.close();
    };
  }, [connectAgentSSE]);

  useEffect(() => {
    if (agentMonitorAutoScroll && agentLogEndRef.current) {
      agentLogEndRef.current.scrollIntoView({ behavior: "smooth" });
    }
  }, [agentEvents, agentMonitorAutoScroll]);

  const filteredAgentEvents = agentEventFilter === "all"
    ? agentEvents
    : agentEvents.filter((e) => e.event_type === agentEventFilter);

  const updateResumeDiffView = (sourceText: string, draft: MaterialDraft | null) => {
    if (!draft) {
      setResumeDiffRows([]);
      return;
    }
    const preview = buildTailoredResumePreview(sourceText, draft);
    setResumeDiffRows(buildLineDiffRows(sourceText, preview));
  };

  const loadResumeSourceForDiff = async (sourceId: string, draft: MaterialDraft | null) => {
    try {
      const resp = await fetch(`${API_BASE_URL}/api/resume/source/${encodeURIComponent(sourceId)}`);
      if (!resp.ok) {
        // Fallback to local textarea text if server-side source is unavailable.
        updateResumeDiffView(resumeText, draft);
        return;
      }
      const data = (await resp.json()) as ResumeSourceResponse;
      updateResumeDiffView(data.resume_text, draft);
    } catch {
      updateResumeDiffView(resumeText, draft);
    }
  };

  const handleAnalyze = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAnalyzing(true);
    setAnalyzeError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/jd/analyze`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ jd_text: jdText }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as AnalyzeResponse;
      setAnalysis(data);
      await fetchRecentJobs();
    } catch (error) {
      setAnalyzeError(`JD 分析失败：${String(error)}`);
    } finally {
      setAnalyzing(false);
    }
  };

  const handleIndexResume = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIndexingResume(true);
    setIndexError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/resume/index`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          resume_text: resumeText,
          source_id: resumeSourceId,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as { indexed_chunks: number; source_id: string };
      setIndexedChunks(data.indexed_chunks);
      setCurrentResumeVersion(data.source_id);
      if (materialDraft) {
        updateResumeDiffView(resumeText, materialDraft);
      }
    } catch (error) {
      setIndexError(`简历入库失败：${String(error)}`);
    } finally {
      setIndexingResume(false);
    }
  };

  const generateMaterialForJob = async (jobId: string) => {
    setGeneratingMaterial(true);
    setMaterialError(null);
    setMaterialMessage(null);
    setMaterialStatus(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/material/generate`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          job_id: jobId,
          resume_version: materialResumeVersion,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as MaterialGenerateResponse;
      setMaterialJobId(data.job_id);
      setMaterialThreadId(data.thread_id ?? null);
      setCurrentResumeVersion(data.resume_version);
      setMaterialDraft(data.draft ?? null);
      setPreviousMaterialDraft(null);
      setMaterialStatus(data.status);
      setMaterialMessage(data.message ?? null);
      await loadResumeSourceForDiff(data.resume_version, data.draft ?? null);
      await fetchPendingMaterials();
    } catch (error) {
      setMaterialError(`材料生成失败：${String(error)}`);
    } finally {
      setGeneratingMaterial(false);
    }
  };

  const handleGenerateMaterialSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (!materialJobId.trim()) {
      setMaterialError("请先输入 job_id，或在最近岗位里点“一键生成”。");
      return;
    }
    await generateMaterialForJob(materialJobId.trim());
  };

  const loadPendingDraft = async (item: PendingMaterialItem) => {
    setMaterialJobId(item.job_id);
    setMaterialThreadId(item.thread_id);
    setCurrentResumeVersion(item.resume_version);
    setMaterialDraft(item.draft ?? null);
    setPreviousMaterialDraft(null);
    setMaterialStatus("pending_review");
    setMaterialMessage("已载入待审批草稿");
    setMaterialError(null);
    await loadResumeSourceForDiff(item.resume_version, item.draft ?? null);
  };

  const handleReviewMaterial = async (
    decision: "approve" | "reject" | "regenerate",
  ) => {
    if (!materialThreadId) {
      setMaterialError("当前没有待审批 thread_id。");
      return;
    }
    setReviewingMaterial(true);
    setMaterialError(null);
    const snapshotBeforeReview = materialDraft;
    try {
      const resp = await fetch(`${API_BASE_URL}/api/material/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: materialThreadId,
          decision,
          feedback: materialFeedback || null,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as MaterialReviewResponse;
      setMaterialStatus(data.status);
      setMaterialMessage(data.message);
      if (data.draft) {
        if (decision === "regenerate" && snapshotBeforeReview) {
          setPreviousMaterialDraft(snapshotBeforeReview);
        }
        setMaterialDraft(data.draft);
        await loadResumeSourceForDiff(currentResumeVersion, data.draft);
      }
      if (data.status === "rejected") {
        setMaterialThreadId(null);
        setMaterialDraft(null);
        setPreviousMaterialDraft(null);
        setResumeDiffRows([]);
      }
      setMaterialFeedback("");
      await fetchPendingMaterials();
      await fetchRecentJobs();
    } catch (error) {
      setMaterialError(`材料审批失败：${String(error)}`);
    } finally {
      setReviewingMaterial(false);
    }
  };

  const copyText = async (text: string, label: string) => {
    try {
      await navigator.clipboard.writeText(text);
      setMaterialMessage(`${label}已复制到剪贴板`);
      setMaterialError(null);
    } catch (error) {
      setMaterialError(`复制失败：${String(error)}`);
    }
  };

  const handleExportMaterial = async (format: "pdf" | "txt") => {
    if (!materialThreadId) {
      setMaterialError("没有可导出的 thread_id。");
      return;
    }
    setExportingMaterial(true);
    setMaterialError(null);
    try {
      const detailResp = await fetch(`${API_BASE_URL}/api/material/thread/${materialThreadId}`);
      if (!detailResp.ok) {
        throw new Error(`HTTP ${detailResp.status}`);
      }
      const detail = (await detailResp.json()) as MaterialThreadDetail;
      if (detail.status !== "approved") {
        setMaterialError("请先 Approve，再导出。");
        return;
      }

      const resp = await fetch(`${API_BASE_URL}/api/material/export`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: materialThreadId,
          format,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as MaterialExportResponse;
      setMaterialMessage(`导出成功：${data.file_name}`);
      window.open(`${API_BASE_URL}${data.download_url}`, "_blank");
    } catch (error) {
      setMaterialError(`导出失败：${String(error)}`);
    } finally {
      setExportingMaterial(false);
    }
  };

  const handleBossScan = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setScanningBoss(true);
    setBossError(null);
    setBossPagesScanned(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/boss/scan`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          keyword: bossKeyword,
          max_items: 10,
          max_pages: bossMaxPages,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as BossScanResponse;
      setBossItems(data.items ?? []);
      setBossPagesScanned(data.pages_scanned ?? null);
      setBossScreenshotPath(data.screenshot_path ?? null);
      await fetchRecentJobs();
    } catch (error) {
      setBossError(
        `BOSS 扫描失败：${String(
          error,
        )}。请先确认 Playwright 与登录态已就绪（见 README）。`,
      );
    } finally {
      setScanningBoss(false);
    }
  };

  const handleAutofillPreview = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setAutofillLoading(true);
    setAutofillError(null);
    try {
      const profile = parseAutofillProfile();
      const resp = await fetch(`${API_BASE_URL}/api/form/autofill/preview`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          html: autofillHtml,
          profile,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as AutofillPreviewResponse;
      setAutofillPreview(data);
      setAutofillFillResult(null);
    } catch (error) {
      setAutofillError(`Autofill 预览失败：${String(error)}`);
    } finally {
      setAutofillLoading(false);
    }
  };

  const handleAutofillUrlPreview = async () => {
    setAutofillUrlPreviewing(true);
    setAutofillError(null);
    try {
      const profile = parseAutofillProfile();
      const resp = await fetch(`${API_BASE_URL}/api/form/autofill/preview-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: autofillTargetUrl,
          profile,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as AutofillPreviewResponse;
      setAutofillPreview(data);
      setAutofillFillResult(null);
    } catch (error) {
      setAutofillError(`URL 预览失败：${String(error)}`);
    } finally {
      setAutofillUrlPreviewing(false);
    }
  };

  const handleAutofillFillUrl = async () => {
    if (!autofillConfirmFill) {
      setAutofillError("请先勾选“确认执行填充（不提交）”。");
      return;
    }
    setAutofillFilling(true);
    setAutofillError(null);
    try {
      const profile = parseAutofillProfile();
      const resp = await fetch(`${API_BASE_URL}/api/form/autofill/fill-url`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: autofillTargetUrl,
          profile,
          confirm_fill: true,
          max_actions: 20,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as AutofillFillResponse;
      setAutofillFillResult(data);
    } catch (error) {
      setAutofillError(`URL 自动填充失败：${String(error)}`);
    } finally {
      setAutofillFilling(false);
    }
  };

  const handleStartFormFillWorkflow = async () => {
    setStartingFormFill(true);
    setAutofillError(null);
    try {
      const profile = parseAutofillProfile();
      const resp = await fetch(`${API_BASE_URL}/api/form/fill/start`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          url: autofillTargetUrl,
          profile,
          max_actions: 20,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as FormFillStartResponse;
      setFormFillThreadId(data.thread_id);
      setFormFillStatus(data.status);
      setFormFillMessage(data.message ?? "预览完成，等待审批");
      setAutofillPreview({
        url: data.url,
        total_fields: data.preview.total_fields,
        mapped_fields: data.preview.mapped_fields,
        screenshot_path: data.preview.screenshot_path ?? null,
        fields: data.preview.fields,
      });
      setAutofillFillResult(null);
      await fetchPendingFormFills();
    } catch (error) {
      setAutofillError(`启动审批流失败：${String(error)}`);
    } finally {
      setStartingFormFill(false);
    }
  };

  const handleReviewFormFill = async (decision: "approve" | "reject") => {
    if (!formFillThreadId) {
      setAutofillError("当前没有可审批的 form fill thread。");
      return;
    }
    setReviewingFormFill(true);
    setAutofillError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/form/fill/review`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          thread_id: formFillThreadId,
          decision,
          feedback: autofillReviewFeedback || null,
          max_actions: 20,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as FormFillReviewResponse;
      setFormFillStatus(data.status);
      setFormFillMessage(data.message);
      if (data.preview) {
        setAutofillPreview({
          url: autofillTargetUrl,
          total_fields: data.preview.total_fields,
          mapped_fields: data.preview.mapped_fields,
          screenshot_path: data.preview.screenshot_path ?? null,
          fields: data.preview.fields,
        });
      }
      if (data.fill_result) {
        setAutofillFillResult(data.fill_result);
      }
      setAutofillReviewFeedback("");
      await fetchPendingFormFills();
    } catch (error) {
      setAutofillError(`审批失败：${String(error)}`);
    } finally {
      setReviewingFormFill(false);
    }
  };

  const loadFormFillThread = async (threadId: string) => {
    setAutofillError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/form/fill/thread/${encodeURIComponent(threadId)}`);
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as FormFillThreadDetail;
      setFormFillThreadId(data.thread_id);
      setFormFillStatus(data.status);
      setFormFillMessage(`已载入 thread: ${data.thread_id}`);
      setAutofillTargetUrl(data.url);
      if (data.preview) {
        setAutofillPreview({
          url: data.url,
          total_fields: data.preview.total_fields,
          mapped_fields: data.preview.mapped_fields,
          screenshot_path: data.preview.screenshot_path ?? null,
          fields: data.preview.fields,
        });
      }
      setAutofillFillResult(data.fill_result ?? null);
    } catch (error) {
      setAutofillError(`载入线程失败：${String(error)}`);
    }
  };

  const handleIngestEmail = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    setIngestingEmail(true);
    setEmailError(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/ingest`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          sender: emailSender,
          subject: emailSubject,
          body: emailBody,
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailIngestResponse;
      setEmailResult(data);
      await fetchRecentEmails();
      await fetchUpcomingSchedules();
      await fetchRecentJobs();
    } catch (error) {
      setEmailError(`邮件分类失败：${String(error)}`);
    } finally {
      setIngestingEmail(false);
    }
  };

  const handleFetchInboxEmails = async () => {
    setFetchingInboxEmails(true);
    setEmailError(null);
    setEmailFetchMessage(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/fetch`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ max_items: 10, mark_seen: false }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailFetchResponse;
      setEmailFetchMessage(`抓取 ${data.fetched_count} 封，处理 ${data.processed_count} 封。`);
      await fetchRecentEmails();
      await fetchUpcomingSchedules();
      await fetchRecentJobs();
    } catch (error) {
      setEmailError(`IMAP 拉取失败：${String(error)}（请检查 IMAP_* 环境变量）`);
    } finally {
      setFetchingInboxEmails(false);
    }
  };

  const handleEmailHeartbeatControl = async (action: "start" | "stop") => {
    setControllingHeartbeat(true);
    setEmailError(null);
    setEmailFetchMessage(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/heartbeat/${action}`, {
        method: "POST",
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailHeartbeatControlResponse;
      setEmailFetchMessage(data.message);
      await fetchEmailHeartbeatStatus();
    } catch (error) {
      setEmailError(`Heartbeat ${action} 失败：${String(error)}`);
    } finally {
      setControllingHeartbeat(false);
    }
  };

  const handleEmailHeartbeatTrigger = async () => {
    setControllingHeartbeat(true);
    setEmailError(null);
    setEmailFetchMessage(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/heartbeat/trigger`, {
        method: "POST",
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as EmailHeartbeatTriggerResponse;
      const notifyText = data.notification_sent
        ? "已发送通道通知"
        : `未发送通道通知（${data.notification_error || "no updates"}）`;
      setEmailFetchMessage(
        `${data.message}：抓取 ${data.fetched_count} 封，处理 ${data.processed_count} 封，提醒 ${data.schedule_reminders} 条，未来日程 ${data.upcoming_schedules} 条；${notifyText}。`,
      );
      await fetchRecentEmails();
      await fetchUpcomingSchedules();
      await fetchRecentJobs();
      await fetchEmailHeartbeatStatus();
    } catch (error) {
      setEmailError(`Heartbeat 手动触发失败：${String(error)}`);
    } finally {
      setControllingHeartbeat(false);
    }
  };

  const handleEmailNotifyTest = async () => {
    setTestingNotify(true);
    setEmailError(null);
    setEmailFetchMessage(null);
    try {
      const resp = await fetch(`${API_BASE_URL}/api/email/heartbeat/notify-test`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({
          message: "OfferPilot 测试通知：邮件巡检通道联动正常。",
        }),
      });
      if (!resp.ok) {
        throw new Error(`HTTP ${resp.status}`);
      }
      const data = (await resp.json()) as { sent: boolean; error?: string | null };
      if (data.sent) {
        setEmailFetchMessage("测试通知发送成功。");
      } else {
        setEmailError(`测试通知发送失败：${data.error || "unknown error"}`);
      }
    } catch (error) {
      setEmailError(`测试通知失败：${String(error)}`);
    } finally {
      setTestingNotify(false);
    }
  };

  const jobKanban = JOB_KANBAN_COLUMNS.reduce(
    (acc, col) => {
      acc[col.key] = [];
      return acc;
    },
    {} as Record<JobKanbanKey, JobListItem[]>,
  );
  for (const job of recentJobs) {
    const lane = mapJobStatusToKanban(job.status);
    jobKanban[lane].push(job);
  }

  return (
    <main className="mx-auto min-h-screen max-w-6xl p-4 sm:p-6 md:p-10">
      <header className="mb-8 space-y-2">
        <h1 className="text-2xl font-bold sm:text-3xl">OfferPilot 求职助手</h1>
        <p className="text-sm text-zinc-600">
          当前后端：
          <code className="rounded bg-zinc-100 px-2 py-1 text-xs">
            {API_BASE_URL}
          </code>
        </p>
        <div className="overflow-x-auto pb-1">
          <nav className="flex min-w-max gap-2">
            {QUICK_LINKS.map((link) => (
              <a
                key={link.href}
                href={link.href}
                className="rounded-full border border-zinc-300 bg-white px-3 py-1 text-xs text-zinc-700 hover:bg-zinc-50"
              >
                {link.label}
              </a>
            ))}
          </nav>
        </div>
      </header>

      <section className="mb-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <h2 id="sec-resume" className="mb-3 scroll-mt-24 text-xl font-semibold" title="上传简历文件（PDF/Word/文本）并填写求职画像（联系方式、技术栈、求职偏好等），这是 Agent 做 JD 匹配和自动回复的数据基础">简历与求职画像</h2>
        <div className="grid gap-6">
          <ResumeUpload />
          <ProfileForm />
        </div>
      </section>

      <section id="sec-agent-monitor" className="mb-8 scroll-mt-24 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 className="text-xl font-semibold" title="通过 SSE 实时展示 Agent 的所有行为：浏览器操作、LLM 调用、意图分类、安全拦截、工作流执行等，所有操作都记录到数据库">
            Agent 实时监控
          </h2>
          <div className="flex items-center gap-3">
            <span className={`inline-flex items-center gap-1.5 rounded-full px-2.5 py-0.5 text-xs font-medium ${agentSseConnected ? "bg-emerald-100 text-emerald-800" : "bg-red-100 text-red-800"}`}>
              <span className={`h-1.5 w-1.5 rounded-full ${agentSseConnected ? "bg-emerald-500 animate-pulse" : "bg-red-500"}`} />
              {agentSseConnected ? "SSE 已连接" : "SSE 断开"}
            </span>
            <select
              className="rounded border border-zinc-300 px-2 py-1 text-xs"
              value={agentEventFilter}
              onChange={(e) => setAgentEventFilter(e.target.value)}
            >
              <option value="all">全部事件</option>
              <option value="browser_launch">浏览器启动</option>
              <option value="browser_navigate">页面导航</option>
              <option value="browser_click">点击操作</option>
              <option value="browser_input">输入操作</option>
              <option value="browser_screenshot">截图</option>
              <option value="browser_extract">数据提取</option>
              <option value="llm_call">LLM 调用</option>
              <option value="intent_classified">意图分类</option>
              <option value="safety_blocked">安全拦截</option>
              <option value="reply_sent">回复发送</option>
              <option value="workflow_start">流程开始</option>
              <option value="workflow_node">流程节点</option>
              <option value="workflow_end">流程结束</option>
              <option value="error">错误</option>
              <option value="warning">警告</option>
            </select>
            <label className="flex items-center gap-1 text-xs text-zinc-600">
              <input
                type="checkbox"
                checked={agentMonitorAutoScroll}
                onChange={(e) => setAgentMonitorAutoScroll(e.target.checked)}
              />
              自动滚动
            </label>
            <button
              type="button"
              className="rounded border border-zinc-300 px-2 py-1 text-xs hover:bg-zinc-50"
              onClick={() => setAgentEvents([])}
            >
              清空
            </button>
          </div>
        </div>

        <div className="h-[48rem] overflow-y-auto rounded border border-zinc-200 bg-zinc-950 p-3 font-mono text-xs leading-relaxed">
          {filteredAgentEvents.length === 0 ? (
            <p className="text-zinc-500">等待 Agent 事件...</p>
          ) : (
            filteredAgentEvents.map((evt, idx) => (
              <div key={idx} className="flex gap-2 py-0.5">
                <span className="shrink-0 text-zinc-500">{evt.timestamp}</span>
                <span className={`shrink-0 rounded px-1.5 py-0.5 text-[10px] font-medium ${EVENT_TYPE_COLORS[evt.event_type] || "bg-zinc-700 text-zinc-300"}`}>
                  {evt.event_type}
                </span>
                <span className="text-zinc-200">{evt.detail}</span>
                {typeof evt.metadata?.screenshot_path === "string" && (
                  <span className="text-indigo-400" title={evt.metadata.screenshot_path}>
                    [screenshot]
                  </span>
                )}
                {typeof evt.metadata?.url === "string" && (
                  <span className="truncate text-sky-400" title={evt.metadata.url}>
                    {evt.metadata.url.slice(0, 60)}
                  </span>
                )}
              </div>
            ))
          )}
          <div ref={agentLogEndRef} />
        </div>

        <div className="mt-2 flex items-center justify-between text-xs text-zinc-500">
          <span>共 {agentEvents.length} 事件 {agentEventFilter !== "all" ? `(已筛选 ${filteredAgentEvents.length})` : ""}</span>
          <span>
            {agentEvents.length > 0
              ? `最新: ${agentEvents[agentEvents.length - 1].timestamp} ${agentEvents[agentEvents.length - 1].event_type}`
              : ""}
          </span>
        </div>
      </section>

      <section className="mb-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <h2 id="sec-jd" className="mb-3 scroll-mt-24 text-xl font-semibold" title="粘贴 JD 文本，LLM 根据你的简历和求职画像做匹配评估，判断是否值得投递">JD 匹配测试</h2>
        <form className="space-y-3" onSubmit={handleAnalyze}>
          <label className="block">
            <span className="mb-1 block text-sm text-zinc-600">JD 文本</span>
            <textarea
              className="h-32 w-full rounded border border-zinc-300 px-3 py-2 outline-none focus:border-zinc-500"
              value={jdText}
              onChange={(e) => setJdText(e.target.value)}
            />
          </label>
          <div className="flex items-center gap-3">
            <button
              type="submit"
              className="rounded bg-black px-4 py-2 text-white hover:bg-zinc-800 disabled:opacity-50"
              disabled={analyzing}
            >
              {analyzing ? "分析中..." : "匹配评估"}
            </button>
            {analyzeError && (
              <span className="text-sm text-red-600">{analyzeError}</span>
            )}
          </div>
        </form>
      </section>

      {analysis && (
        <section className="mb-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
          <h2 id="sec-analysis" className="mb-3 scroll-mt-24 text-xl font-semibold" title="JD 分析后的详细结果：匹配分数、关键词命中、优劣势分析">分析结果</h2>

          <div className="mb-4 flex flex-wrap items-center gap-3">
            {(analysis as Record<string, unknown>).should_apply != null && (
              <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${(analysis as Record<string, unknown>).should_apply ? "bg-green-100 text-green-800" : "bg-red-100 text-red-800"}`}>
                {(analysis as Record<string, unknown>).should_apply ? "✓ 建议投递" : "✗ 不建议投递"}
              </span>
            )}
            <span className={`inline-flex items-center rounded-full px-3 py-1 text-sm font-medium ${analysis.match_score >= 80 ? "bg-green-100 text-green-800" : analysis.match_score >= 60 ? "bg-amber-100 text-amber-800" : "bg-red-100 text-red-800"}`}>
              匹配分：{analysis.match_score}
            </span>
          </div>

          {!!(analysis as Record<string, unknown>).one_line_reason && (
            <p className="mb-4 rounded-lg bg-blue-50 p-3 text-sm leading-6 text-blue-900">
              {String((analysis as Record<string, unknown>).one_line_reason)}
            </p>
          )}

          <div className="grid gap-4 md:grid-cols-2">
            <div className="space-y-2">
              <p><span className="font-medium">岗位：</span>{analysis.title}</p>
              <p><span className="font-medium">公司：</span>{analysis.company}</p>
              <div>
                <span className="font-medium">技能要求：</span>
                <div className="mt-2 flex flex-wrap gap-2">
                  {analysis.skills.map((skill) => (
                    <span key={skill} className="rounded-full bg-zinc-100 px-2 py-1 text-xs">{skill}</span>
                  ))}
                </div>
              </div>
            </div>
            <div className="space-y-3">
              {Array.isArray((analysis as Record<string, unknown>).strengths) && ((analysis as Record<string, unknown>).strengths as string[]).length > 0 && (
                <div>
                  <p className="font-medium text-green-700">优势</p>
                  <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-green-800">
                    {((analysis as Record<string, unknown>).strengths as string[]).map((s, i) => <li key={i}>{s}</li>)}
                  </ul>
                </div>
              )}
              {Array.isArray((analysis as Record<string, unknown>).gaps) && ((analysis as Record<string, unknown>).gaps as string[]).length > 0 && (
                <div>
                  <p className="font-medium text-amber-700">待提升</p>
                  <ul className="mt-1 list-inside list-disc space-y-1 text-sm text-amber-800">
                    {((analysis as Record<string, unknown>).gaps as string[]).map((g, i) => <li key={i}>{g}</li>)}
                  </ul>
                </div>
              )}
              {analysis.gap_analysis && (
                <div>
                  <p className="font-medium">Gap 分析</p>
                  <p className="mt-1 rounded bg-zinc-50 p-3 text-sm leading-6">{analysis.gap_analysis}</p>
                </div>
              )}
            </div>
          </div>

          {analysis.resume_evidence.length > 0 && (
          <div className="mt-5">
            <p className="mb-2 text-sm text-zinc-500">匹配依据（命中简历片段 × {analysis.resume_evidence.length}）</p>
            <div className="space-y-2">
              {analysis.resume_evidence.map((chunk, idx) => (
                <p key={`${idx}-${chunk.slice(0, 32)}`} className="rounded bg-zinc-50 p-3 text-sm leading-6">
                  <span className="mr-2 text-xs text-zinc-400">#{idx + 1}</span>
                  {chunk}
                </p>
              ))}
            </div>
          </div>
          )}

          <div className="mt-5">
            <p className="mb-2 font-medium">历史相似岗位（jd_history）</p>
            {analysis.similar_jobs.length === 0 ? (
              <p className="text-sm text-zinc-500">暂无相似岗位。</p>
            ) : (
              <div className="overflow-x-auto">
                <table className="w-full border-collapse text-sm">
                  <thead>
                    <tr className="border-b">
                      <th className="py-2 text-left">岗位</th>
                      <th className="py-2 text-left">公司</th>
                      <th className="py-2 text-left">相似度</th>
                      <th className="py-2 text-left">历史匹配分</th>
                    </tr>
                  </thead>
                  <tbody>
                    {analysis.similar_jobs.map((item, idx) => (
                      <tr key={`${item.title}-${idx}`} className="border-b">
                        <td className="py-2">{item.title}</td>
                        <td className="py-2">{item.company}</td>
                        <td className="py-2">{item.similarity}</td>
                        <td className="py-2">{item.match_score ?? "-"}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </section>
      )}

      <section className="rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="sec-jobs" className="scroll-mt-24 text-xl font-semibold" title="所有被 Agent 抓取和分析过的岗位汇总，可查看匹配分、投递状态，跟踪从投递到 Offer 的全流程">岗位看板</h2>
          <button
            onClick={() => void fetchRecentJobs()}
            className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50"
            disabled={loadingJobs}
          >
            {loadingJobs ? "刷新中..." : "刷新"}
          </button>
        </div>
        {jobsError ? (
          <p className="text-sm text-red-600">{jobsError}</p>
        ) : (
          <div className="space-y-4">
            <div className="overflow-x-auto">
              <table className="w-full border-collapse text-sm">
                <thead>
                  <tr className="border-b">
                    <th className="py-2 text-left">岗位</th>
                    <th className="py-2 text-left">公司</th>
                    <th className="py-2 text-left">来源</th>
                    <th className="py-2 text-left">匹配分</th>
                    <th className="py-2 text-left">状态</th>
                    <th className="py-2 text-left">创建时间</th>
                  </tr>
                </thead>
                <tbody>
                  {recentJobs.map((job) => (
                    <tr key={job.id} className="border-b">
                      <td className="py-2">{job.title}</td>
                      <td className="py-2">{job.company}</td>
                      <td className="py-2">{job.source}</td>
                      <td className="py-2">{job.match_score ?? "-"}</td>
                      <td className="py-2">{job.status}</td>
                      <td className="py-2">
                        {new Date(job.created_at).toLocaleString()}
                      </td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
            <div className="mt-4 rounded border border-zinc-200 bg-zinc-50 p-3">
            <p className="mb-2 text-sm font-medium text-zinc-800">投递状态看板（阶段 5）</p>
            <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-3">
              {JOB_KANBAN_COLUMNS.map((col) => (
                <div key={col.key} className="rounded border border-zinc-200 bg-white p-2">
                  <div className="mb-2 text-xs font-medium text-zinc-700">
                    {col.title}（{jobKanban[col.key].length}）
                  </div>
                  <div className="max-h-56 space-y-2 overflow-auto">
                    {jobKanban[col.key].length === 0 ? (
                      <p className="text-xs text-zinc-400">暂无</p>
                    ) : (
                      jobKanban[col.key].map((job) => (
                        <div key={`${col.key}-${job.id}`} className="rounded border border-zinc-200 px-2 py-1">
                          <div className="truncate text-xs font-medium text-zinc-800" title={job.title}>
                            {job.title}
                          </div>
                          <div className="truncate text-xs text-zinc-500" title={job.company}>
                            {job.company}
                          </div>
                          <div className="text-[10px] text-zinc-400">{new Date(job.created_at).toLocaleDateString()}</div>
                        </div>
                      ))
                    )}
                  </div>
                </div>
              ))}
            </div>
          </div>
          </div>
        )}
      </section>

      <section className="mt-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <h2 id="sec-boss-scan" className="mb-3 scroll-mt-24 text-xl font-semibold" title="输入关键词，Agent 自动在 BOSS 直聘搜索岗位、提取 JD、匹配打分，批量入库">BOSS 岗位扫描</h2>
        <form className="mb-4 flex flex-wrap items-end gap-3" onSubmit={handleBossScan}>
          <label className="min-w-0 flex-1 sm:min-w-[260px]">
            <span className="mb-1 block text-sm text-zinc-600">关键词</span>
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 outline-none focus:border-zinc-500"
              value={bossKeyword}
              onChange={(e) => setBossKeyword(e.target.value)}
              placeholder="例如：AI Agent 实习 深圳"
            />
          </label>
          <label className="w-28">
            <span className="mb-1 block text-sm text-zinc-600">页数</span>
            <input
              type="number"
              min={1}
              max={5}
              className="w-full rounded border border-zinc-300 px-3 py-2 outline-none focus:border-zinc-500"
              value={bossMaxPages}
              onChange={(e) => {
                const raw = Number(e.target.value);
                const safe = Number.isFinite(raw) ? Math.max(1, Math.min(5, raw)) : 1;
                setBossMaxPages(safe);
              }}
            />
          </label>
          <button
            type="submit"
            className="rounded bg-black px-4 py-2 text-white hover:bg-zinc-800 disabled:opacity-50"
            disabled={scanningBoss}
          >
            {scanningBoss ? "扫描中..." : "开始扫描"}
          </button>
        </form>

        {bossError && <p className="mb-2 text-sm text-red-600">{bossError}</p>}
        {bossScreenshotPath && (
          <p className="mb-2 text-xs text-zinc-500">
            本次截图：<code className="rounded bg-zinc-100 px-1 py-0.5">{bossScreenshotPath}</code>
          </p>
        )}
        {bossPagesScanned !== null && (
          <p className="mb-2 text-xs text-zinc-500">本次扫描页数：{bossPagesScanned}</p>
        )}

        {bossItems.length === 0 ? (
          <p className="text-sm text-zinc-500">暂无扫描结果。</p>
        ) : (
          <div className="overflow-x-auto">
            <table className="w-full border-collapse text-sm">
              <thead>
                <tr className="border-b">
                  <th className="py-2 text-left">岗位</th>
                  <th className="py-2 text-left">公司</th>
                  <th className="py-2 text-left">匹配分</th>
                  <th className="py-2 text-left">链接</th>
                </tr>
              </thead>
              <tbody>
                {bossItems.map((item, idx) => (
                  <tr key={`${item.title}-${idx}`} className="border-b">
                    <td className="py-2">{item.title}</td>
                    <td className="py-2">{item.company}</td>
                    <td className="py-2">{item.match_score ?? "-"}</td>
                    <td className="py-2">
                      {item.source_url ? (
                        <a
                          href={item.source_url}
            target="_blank"
                          rel="noreferrer"
                          className="text-blue-600 hover:underline"
                        >
                          打开
                        </a>
                      ) : (
                        "-"
                      )}
                    </td>
                  </tr>
                ))}
              </tbody>
            </table>
        </div>
        )}
      </section>

      <section className="mt-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="sec-boss-chat" className="scroll-mt-24 text-xl font-semibold" title="拉取 BOSS 直聘的聊天消息，Agent 自动识别 HR 意图并生成回复建议，需人工确认后才会发送">BOSS 对话 Copilot</h2>
          <div className="flex flex-wrap gap-2">
            <label className="inline-flex items-center gap-2 rounded border border-zinc-300 px-2 py-1 text-xs text-zinc-700">
              <input
                type="checkbox"
                checked={bossChatUnreadOnly}
                onChange={(e) => setBossChatUnreadOnly(e.target.checked)}
              />
              仅拉取未读
            </label>
            <label className="inline-flex items-center gap-2 rounded border border-amber-300 bg-amber-50 px-2 py-1 text-xs text-amber-800" title="需后端 BOSS_CHAT_AUTO_EXECUTE_ENABLED=true">
              <input
                type="checkbox"
                checked={bossChatAutoExecute}
                onChange={(e) => setBossChatAutoExecute(e.target.checked)}
              />
              自动发送（满足条件时实际发送）
            </label>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={bossChatPulling}
              onClick={() => void handleBossChatPull()}
            >
              {bossChatPulling ? "拉取中..." : "拉取聊天列表"}
            </button>
            <button
              type="button"
              className="rounded bg-indigo-700 px-3 py-1 text-sm text-white hover:bg-indigo-800 disabled:opacity-50"
              disabled={bossChatProcessing}
              onClick={() => void handleBossChatProcess()}
            >
              {bossChatProcessing ? "处理中..." : "批量处理未读"}
            </button>
            <button
              type="button"
              className="rounded bg-emerald-700 px-3 py-1 text-sm text-white hover:bg-emerald-800 disabled:opacity-50"
              disabled={bossChatHeartbeatTriggering}
              onClick={() => void handleBossChatHeartbeatTrigger()}
            >
              {bossChatHeartbeatTriggering ? "巡检中..." : "触发巡检摘要"}
            </button>
    </div>
        </div>

        <div className="grid gap-4 xl:grid-cols-2">
          <div className="space-y-2">
            <p className="text-sm text-zinc-600">
              求职画像请在上方「简历与求职画像」区域配置，此处仅做聊天预览测试。
            </p>
          </div>

          <div className="space-y-3 rounded border border-zinc-200 bg-zinc-50 p-3">
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-600">HR 消息</span>
              <textarea
                className="h-24 w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={bossHrMessage}
                onChange={(e) => setBossHrMessage(e.target.value)}
              />
            </label>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="block">
                <span className="mb-1 block text-sm text-zinc-600">公司（可选）</span>
                <input
                  className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                  value={bossHrCompany}
                  onChange={(e) => setBossHrCompany(e.target.value)}
                />
              </label>
              <label className="block">
                <span className="mb-1 block text-sm text-zinc-600">岗位（可选）</span>
                <input
                  className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                  value={bossHrJobTitle}
                  onChange={(e) => setBossHrJobTitle(e.target.value)}
                />
              </label>
            </div>
            <div className="grid gap-3 md:grid-cols-2">
              <label className="flex items-center gap-2 text-sm text-zinc-700">
                <input
                  type="checkbox"
                  checked={bossNotifyOnEscalate}
                  onChange={(e) => setBossNotifyOnEscalate(e.target.checked)}
                />
                无法自动回复时发送通道提醒
              </label>
            </div>
            <button
              type="button"
              className="rounded bg-indigo-700 px-4 py-2 text-sm text-white hover:bg-indigo-800 disabled:opacity-50"
              disabled={bossPreviewingReply}
              onClick={() => void handleBossReplyPreview()}
            >
              {bossPreviewingReply ? "分析中..." : "预览回复决策"}
            </button>

            {bossChatError && <p className="text-sm text-red-600">{bossChatError}</p>}

            {bossReplyPreview && (
              <div className="space-y-2 rounded border border-indigo-200 bg-white p-3 text-sm">
                <p>
                  意图：<span className="font-medium">{bossReplyPreview.intent}</span>
                  {"  "}置信度：{bossReplyPreview.confidence.toFixed(2)}
                </p>
                <p>
                  动作：<span className="font-medium">{bossReplyPreview.action}</span>
                  {"  "}理由：{bossReplyPreview.reason}
                </p>
                {bossReplyPreview.extracted_question && (
                  <p className="text-zinc-600">提取问题：{bossReplyPreview.extracted_question}</p>
                )}
                {bossReplyPreview.reply_text && (
                  <p className="rounded border border-zinc-200 bg-zinc-50 px-2 py-1 text-zinc-800">
                    建议回复：{bossReplyPreview.reply_text}
                  </p>
                )}
                <p className="text-zinc-600">
                  需发简历：{bossReplyPreview.needs_send_resume ? "是" : "否"}
                  {"  "}需人工介入：{bossReplyPreview.needs_user_intervention ? "是" : "否"}
                </p>
                <p className="text-zinc-600">
                  命中画像字段：
                  {bossReplyPreview.matched_profile_fields.length > 0
                    ? bossReplyPreview.matched_profile_fields.join(", ")
                    : "-"}
                </p>
                <p className="text-zinc-600">
                  升级通知：
                  {bossReplyPreview.notification_sent
                    ? "已发送"
                    : `未发送${bossReplyPreview.notification_error ? `（${bossReplyPreview.notification_error}）` : ""}`}
                </p>
              </div>
            )}

            {bossChatScreenshotPath && (
              <p className="text-xs text-zinc-500">
                聊天列表截图：<code className="rounded bg-zinc-100 px-1">{bossChatScreenshotPath}</code>
              </p>
            )}
            {bossChatItems.length > 0 && (
              <div className="max-h-56 overflow-auto rounded border border-zinc-200 bg-white">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b">
                      <th className="py-2 text-left">HR</th>
                      <th className="py-2 text-left">公司/岗位</th>
                      <th className="py-2 text-left">未读</th>
                      <th className="py-2 text-left">最近消息</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bossChatItems.map((item) => (
                      <tr key={item.conversation_id} className="border-b">
                        <td className="py-2">
                          {item.hr_name}
                          <div className="text-[10px] text-zinc-500">{item.latest_time || "-"}</div>
                        </td>
                        <td className="py-2">
                          <div>{item.company || "-"}</div>
                          <div className="text-[10px] text-zinc-500">{item.job_title || "-"}</div>
                        </td>
                        <td className="py-2">{item.unread_count}</td>
                        <td className="py-2 whitespace-pre-wrap">
                          {item.latest_hr_message || item.latest_message || item.preview || "-"}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
            {bossChatProcessSummary && (
              <p className="rounded border border-emerald-200 bg-emerald-50 px-2 py-1 text-xs text-emerald-800">
                {bossChatProcessSummary}
              </p>
            )}
            {bossChatHeartbeatSummary && (
              <p className="rounded border border-blue-200 bg-blue-50 px-2 py-1 text-xs text-blue-800">
                Heartbeat 摘要：{bossChatHeartbeatSummary}
              </p>
            )}
            {bossChatProcessed.length > 0 && (
              <div className="max-h-64 overflow-auto rounded border border-indigo-200 bg-white">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b">
                      <th className="py-2 text-left">HR</th>
                      <th className="py-2 text-left">消息</th>
                      <th className="py-2 text-left">决策</th>
                      <th className="py-2 text-left">状态</th>
                    </tr>
                  </thead>
                  <tbody>
                    {bossChatProcessed.map((item) => (
                      <tr key={item.message_signature} className="border-b">
                        <td className="py-2">
                          {item.hr_name}
                          <div className="text-[10px] text-zinc-500">
                            {item.company || "-"} / {item.job_title || "-"}
                          </div>
                        </td>
                        <td className="py-2 whitespace-pre-wrap">
                          {item.latest_hr_message}
                          <div className="text-[10px] text-zinc-500">{item.latest_hr_time || "-"}</div>
                        </td>
                        <td className="py-2">
                          <div className="font-medium">
                            {item.action} ({item.confidence.toFixed(2)})
                          </div>
                          <div className="text-[10px] text-zinc-600">{item.reason}</div>
                          <div className="text-[10px] text-zinc-500">
                            SourceCheck：
                            {typeof item.source_fit_score === "number"
                              ? `${item.source_fit_score.toFixed(1)} / ${item.source_fit_passed ? "pass" : "block"}`
                              : "-"}
                          </div>
                          {item.source_fit_reason && (
                            <div className="text-[10px] text-zinc-500">{item.source_fit_reason}</div>
                          )}
                          <div className="text-[10px] text-zinc-500">
                            主动联系：
                            {item.proactive_contact
                              ? `yes (${typeof item.proactive_confidence === "number" ? item.proactive_confidence.toFixed(2) : "-"})`
                              : "no"}
                            {item.proactive_contact && typeof item.proactive_match_score === "number"
                              ? ` / ${item.proactive_match_score.toFixed(1)} / ${item.proactive_match_passed ? "pass" : "block"}`
                              : ""}
                            {item.proactive_jd_match_score != null && (
                              <span className="ml-1 text-zinc-400">
                                (JD Matcher: {item.proactive_jd_match_score.toFixed(1)})
                              </span>
                            )}
                          </div>
                          {item.proactive_gap_analysis && (
                            <div className="text-[10px] text-zinc-400" title={item.proactive_gap_analysis}>
                              差距分析：{item.proactive_gap_analysis.slice(0, 60)}
                              {item.proactive_gap_analysis.length > 60 ? "…" : ""}
                            </div>
                          )}
                          {item.proactive_reason && (
                            <div className="text-[10px] text-zinc-500">{item.proactive_reason}</div>
                          )}
                          {item.reply_text && (
                            <div className="mt-1 rounded border border-zinc-200 bg-zinc-50 px-1 py-0.5 text-[10px] text-zinc-700">
                              {item.reply_text}
                            </div>
                          )}
                        </td>
                        <td className="py-2">
                          {item.is_new ? "new" : "duplicate"}
                          <div className="text-[10px] text-zinc-500">
                            需人工：{item.needs_user_intervention ? "是" : "否"}
                          </div>
                          <div className="text-[10px] text-zinc-500">
                            通知：{item.notification_sent ? "已发" : "未发"}
                          </div>
                          {item.reply_text && (item.reply_sent === true || item.reply_sent_error) && (
                            <div className={`text-[10px] ${item.reply_sent ? "text-emerald-600" : "text-rose-600"}`}>
                              已发送：{item.reply_sent ? "是" : "否"}
                              {item.reply_sent_error && (
                                <span className="block truncate" title={item.reply_sent_error}>
                                  {item.reply_sent_error.slice(0, 30)}…
                                </span>
                              )}
                            </div>
                          )}
                        </td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            )}
          </div>
        </div>
      </section>


      <details id="sec-more" className="mt-8 scroll-mt-24">
        <summary className="cursor-pointer rounded-xl border border-zinc-200 bg-white px-5 py-4 text-lg font-semibold text-zinc-700 shadow-sm hover:bg-zinc-50">
          更多工具（邮件 / 审计 / 情报 / 安全）
        </summary>

      <section className="mt-4 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="sec-email" className="scroll-mt-24 text-xl font-semibold" title="从邮箱拉取求职相关邮件，自动分类（面试邀请/拒信/offer等），更新岗位状态">邮件分类与状态更新</h2>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={fetchingInboxEmails}
              onClick={() => void handleFetchInboxEmails()}
            >
              {fetchingInboxEmails ? "拉取中..." : "从 IMAP 拉取"}
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={loadingHeartbeatStatus}
              onClick={() => void fetchEmailHeartbeatStatus()}
            >
              {loadingHeartbeatStatus ? "读取中..." : "刷新心跳状态"}
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={loadingEmailEvents}
              onClick={() => void fetchRecentEmails()}
            >
              {loadingEmailEvents ? "刷新中..." : "刷新邮件记录"}
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={loadingSchedules}
              onClick={() => void fetchUpcomingSchedules()}
            >
              {loadingSchedules ? "读取中..." : "刷新日程看板"}
            </button>
          </div>
        </div>

        <div className="mb-4 rounded border border-zinc-200 bg-zinc-50 p-3 text-xs">
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded bg-indigo-700 px-3 py-1.5 text-white hover:bg-indigo-800 disabled:opacity-50"
              disabled={controllingHeartbeat}
              onClick={() => void handleEmailHeartbeatControl("start")}
            >
              启动自动巡检
            </button>
            <button
              type="button"
              className="rounded bg-rose-700 px-3 py-1.5 text-white hover:bg-rose-800 disabled:opacity-50"
              disabled={controllingHeartbeat}
              onClick={() => void handleEmailHeartbeatControl("stop")}
            >
              停止自动巡检
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 bg-white px-3 py-1.5 hover:bg-zinc-100 disabled:opacity-50"
              disabled={controllingHeartbeat}
              onClick={() => void handleEmailHeartbeatTrigger()}
            >
              手动触发一次巡检
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 bg-white px-3 py-1.5 hover:bg-zinc-100 disabled:opacity-50"
              disabled={testingNotify}
              onClick={() => void handleEmailNotifyTest()}
            >
              {testingNotify ? "发送中..." : "发送测试通知"}
            </button>
          </div>
          {emailHeartbeatStatus ? (
            <div className="mt-2 space-y-1 text-zinc-700">
              <p>
                运行状态：<span className="font-medium">{emailHeartbeatStatus.running ? "running" : "stopped"}</span>
                {"  "}env启用：{emailHeartbeatStatus.enabled_by_env ? "true" : "false"}
                {"  "}间隔：{emailHeartbeatStatus.interval_sec}s
                {"  "}每次拉取：{emailHeartbeatStatus.max_items}
              </p>
              <p>
                最近运行：{emailHeartbeatStatus.last_run_at ? new Date(emailHeartbeatStatus.last_run_at).toLocaleString() : "-"}
                {"  "}最近成功：{emailHeartbeatStatus.last_success_at ? new Date(emailHeartbeatStatus.last_success_at).toLocaleString() : "-"}
              </p>
              <p>
                最近结果：fetched={emailHeartbeatStatus.last_fetched_count ?? "-"} / processed={emailHeartbeatStatus.last_processed_count ?? "-"}
              </p>
              {emailHeartbeatStatus.last_error && (
                <p className="text-rose-700">最近错误：{emailHeartbeatStatus.last_error}</p>
              )}
            </div>
          ) : (
            <p className="mt-2 text-zinc-500">未读取到 heartbeat 状态。</p>
          )}
        </div>

        <form className="space-y-3" onSubmit={handleIngestEmail}>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-600">发件人</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={emailSender}
                onChange={(e) => setEmailSender(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-sm text-zinc-600">邮件标题</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={emailSubject}
                onChange={(e) => setEmailSubject(e.target.value)}
              />
            </label>
          </div>
          <label className="block">
            <span className="mb-1 block text-sm text-zinc-600">邮件正文</span>
            <textarea
              className="h-24 w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
              value={emailBody}
              onChange={(e) => setEmailBody(e.target.value)}
            />
          </label>
          <button
            type="submit"
            className="rounded bg-black px-4 py-2 text-white hover:bg-zinc-800 disabled:opacity-50"
            disabled={ingestingEmail}
          >
            {ingestingEmail ? "分类中..." : "分类并同步状态"}
          </button>
        </form>

        {emailError && <p className="mt-2 text-sm text-red-600">{emailError}</p>}
        {emailFetchMessage && <p className="mt-2 text-sm text-emerald-700">{emailFetchMessage}</p>}
        {emailResult && (
          <div className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
            <p>
              分类结果：<span className="font-medium">{emailResult.classification.email_type}</span>
              {"  "}置信度：{emailResult.classification.confidence.toFixed(2)}
            </p>
            {emailResult.classification.company && <p>公司：{emailResult.classification.company}</p>}
            {emailResult.classification.interview_time && (
              <p>面试时间：{emailResult.classification.interview_time}</p>
            )}
            {emailResult.schedule_event_id && (
              <p className="text-zinc-600">日程已入库：{emailResult.schedule_event_id.slice(0, 8)}...</p>
            )}
            <p className="text-zinc-600">{emailResult.message}</p>
          </div>
        )}

        <div className="mt-4 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b">
                <th className="py-2 text-left">时间</th>
                <th className="py-2 text-left">发件人</th>
                <th className="py-2 text-left">主题</th>
                <th className="py-2 text-left">类型</th>
                <th className="py-2 text-left">公司</th>
                <th className="py-2 text-left">岗位关联</th>
              </tr>
            </thead>
            <tbody>
              {emailEvents.map((item) => (
                <tr key={item.id} className="border-b">
                  <td className="py-2">{new Date(item.created_at).toLocaleString()}</td>
                  <td className="py-2">{item.sender}</td>
                  <td className="py-2">{item.subject}</td>
                  <td className="py-2">{item.email_type}</td>
                  <td className="py-2">{item.company || "-"}</td>
                  <td className="py-2">
                    {item.related_job_id ? `${item.related_job_id.slice(0, 8)}... -> ${item.updated_job_status || "-"}` : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {emailEvents.length === 0 && (
            <p className="mt-2 text-sm text-zinc-500">暂无邮件记录。</p>
          )}
        </div>

        <div className="mt-4 rounded border border-zinc-200 bg-zinc-50 p-3">
          <div className="mb-2 flex items-center justify-between">
            <p className="text-sm font-medium text-zinc-800">未来 14 天日程看板</p>
            <p className="text-xs text-zinc-500">来源：邮件解析（interview/test）</p>
          </div>
          {upcomingSchedules.length === 0 ? (
            <p className="text-sm text-zinc-500">暂无即将到来的面试/笔试安排。</p>
          ) : (
            <div className="grid gap-2 md:grid-cols-2 xl:grid-cols-3">
              {upcomingSchedules.map((item) => (
                <div key={item.id} className="rounded border border-zinc-200 bg-white p-2 text-xs">
                  <div className="font-medium text-zinc-800">
                    {item.company || "未知公司"} · {item.event_type}
                  </div>
                  <div className="mt-1 text-zinc-600">{new Date(item.start_at).toLocaleString()}</div>
                  <div className="text-zinc-500">模式：{item.mode}</div>
                  {item.location && <div className="text-zinc-500">地点：{item.location}</div>}
                  {item.contact && <div className="text-zinc-500">联系：{item.contact}</div>}
                  <div className="text-zinc-400">置信度：{item.confidence.toFixed(2)}</div>
                </div>
              ))}
            </div>
          )}
        </div>
      </section>

      <section className="mt-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="sec-metrics" className="scroll-mt-24 text-xl font-semibold" title="Agent 的操作审计日志：每次动作的时间、类型、输入输出、状态和截图，用于回溯排查">评测指标与审计时间线</h2>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={loadingEvalMetrics}
              onClick={() => void fetchEvalMetrics()}
            >
              {loadingEvalMetrics ? "读取中..." : "刷新评测指标"}
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={loadingActionTimeline}
              onClick={() => void fetchActionTimeline()}
            >
              {loadingActionTimeline ? "读取中..." : "刷新审计时间线"}
            </button>
          </div>
        </div>

        {evalMetrics ? (
          <div className="grid gap-3 md:grid-cols-2 xl:grid-cols-4">
            <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
              <p className="text-zinc-500">匹配评分一致性（std）</p>
              <p className="mt-1 text-lg font-semibold">
                {evalMetrics.score_consistency_std !== null &&
                evalMetrics.score_consistency_std !== undefined
                  ? evalMetrics.score_consistency_std.toFixed(2)
                  : "-"}
              </p>
              <p className="text-xs text-zinc-500">样本组数：{evalMetrics.score_consistency_groups}</p>
            </div>
            <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
              <p className="text-zinc-500">表单填充准确率</p>
              <p className="mt-1 text-lg font-semibold">
                {evalMetrics.autofill_accuracy !== null && evalMetrics.autofill_accuracy !== undefined
                  ? `${(evalMetrics.autofill_accuracy * 100).toFixed(1)}%`
                  : "-"}
              </p>
              <p className="text-xs text-zinc-500">
                attempted={evalMetrics.autofill_total_fields} / failed={evalMetrics.autofill_failed_fields}
              </p>
            </div>
            <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
              <p className="text-zinc-500">材料评审通过率</p>
              <p className="mt-1 text-lg font-semibold">
                {evalMetrics.material_approve_rate !== null &&
                evalMetrics.material_approve_rate !== undefined
                  ? `${(evalMetrics.material_approve_rate * 100).toFixed(1)}%`
                  : "-"}
              </p>
              <p className="text-xs text-zinc-500">
                approved={evalMetrics.material_approved} / reviewed={evalMetrics.material_reviewed}
              </p>
            </div>
            <div className="rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
              <p className="text-zinc-500">端到端延迟 p50</p>
              <p className="mt-1 text-lg font-semibold">
                {evalMetrics.e2e_latency_sec_p50 !== null && evalMetrics.e2e_latency_sec_p50 !== undefined
                  ? `${evalMetrics.e2e_latency_sec_p50.toFixed(1)}s`
                  : "-"}
              </p>
              <p className="text-xs text-zinc-500">样本数：{evalMetrics.e2e_latency_samples}</p>
            </div>
          </div>
        ) : (
          <p className="text-sm text-zinc-500">暂无评测指标数据。</p>
        )}

        <p className="mt-3 text-xs text-zinc-500">
          统计窗口：最近 {evalMetrics?.window_days ?? 14} 天；评估时间：
          {evalMetrics?.evaluated_at ? ` ${new Date(evalMetrics.evaluated_at).toLocaleString()}` : " -"}
        </p>

        <div className="mt-4 overflow-x-auto">
          <table className="w-full border-collapse text-xs">
            <thead>
              <tr className="border-b">
                <th className="py-2 text-left">时间</th>
                <th className="py-2 text-left">动作类型</th>
                <th className="py-2 text-left">状态</th>
                <th className="py-2 text-left">岗位</th>
                <th className="py-2 text-left">输出摘要</th>
                <th className="py-2 text-left">截图</th>
              </tr>
            </thead>
            <tbody>
              {actionTimeline.map((item) => (
                <tr key={item.action_id} className="border-b">
                  <td className="py-2">{new Date(item.created_at).toLocaleString()}</td>
                  <td className="py-2">{item.action_type}</td>
                  <td className="py-2">{item.status || "-"}</td>
                  <td className="py-2">
                    {item.job_title ? `${item.job_title} @ ${item.job_company || "-"}` : item.job_id?.slice(0, 8) || "-"}
                  </td>
                  <td className="py-2">{(item.output_summary || "-").slice(0, 120)}</td>
                  <td className="py-2">
                    {item.screenshot_path ? (
                      <span className="inline-flex items-center gap-1 text-indigo-600" title={item.screenshot_path}>
                        <svg className="h-3 w-3" fill="none" stroke="currentColor" viewBox="0 0 24 24"><path strokeLinecap="round" strokeLinejoin="round" strokeWidth={2} d="M4 16l4.586-4.586a2 2 0 012.828 0L16 16m-2-2l1.586-1.586a2 2 0 012.828 0L20 14m-6-6h.01M6 20h12a2 2 0 002-2V6a2 2 0 00-2-2H6a2 2 0 00-2 2v12a2 2 0 002 2z" /></svg>
                        有
                      </span>
                    ) : "-"}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
          {actionTimeline.length === 0 && (
            <p className="mt-2 text-sm text-zinc-500">暂无审计动作记录。</p>
          )}
        </div>
      </section>

      <section className="mt-8 rounded-xl border border-zinc-200 bg-white p-4 shadow-sm sm:p-5">
        <div className="mb-3 flex flex-wrap items-center justify-between gap-2">
          <h2 id="sec-intel" className="scroll-mt-24 text-xl font-semibold" title="查询公司情报、预算控制、安全策略检查等辅助工具">公司情报 + 面试准备 + 安全治理</h2>
          <div className="flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={intelLoading}
              onClick={() => void handleGenerateCompanyIntel()}
            >
              {intelLoading ? "分析中..." : "生成公司情报"}
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1 text-sm hover:bg-zinc-50 disabled:opacity-50"
              disabled={prepLoading}
              onClick={() => void handleGenerateInterviewPrep()}
            >
              {prepLoading ? "生成中..." : "生成面试题库"}
            </button>
          </div>
        </div>

        <div className="grid gap-3 md:grid-cols-2">
          <label className="block">
            <span className="mb-1 block text-sm text-zinc-600">公司名</span>
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
              value={intelCompany}
              onChange={(e) => setIntelCompany(e.target.value)}
            />
          </label>
          <label className="block">
            <span className="mb-1 block text-sm text-zinc-600">岗位名</span>
            <input
              className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
              value={intelRoleTitle}
              onChange={(e) => setIntelRoleTitle(e.target.value)}
            />
          </label>
        </div>
        <label className="mt-3 block">
          <span className="mb-1 block text-sm text-zinc-600">JD 片段（可选）</span>
          <textarea
            className="h-24 w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
            value={intelJdText}
            onChange={(e) => setIntelJdText(e.target.value)}
          />
        </label>

        {intelError && <p className="mt-2 text-sm text-red-600">{intelError}</p>}
        {intelResult && (
          <div className="mt-4 rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
            <p className="font-medium">
              {intelResult.company} / {intelResult.role_title || "-"}（置信度 {intelResult.confidence.toFixed(2)}）
            </p>
            <p className="mt-1 text-zinc-700">{intelResult.summary}</p>
            <p className="mt-2 text-xs text-zinc-600">
              业务方向：{intelResult.business_direction.join("、") || "-"} | 技术栈：
              {intelResult.tech_stack.join("、") || "-"}
            </p>
            <p className="text-xs text-zinc-600">
              面试风格：{intelResult.interview_style.join("、") || "-"} | 融资：{intelResult.funding_stage || "-"} | 团队阶段：
              {intelResult.team_size_stage || "-"}
            </p>
            <p className="mt-2 text-xs text-zinc-600">建议：{intelResult.suggestions.join("；") || "-"}</p>
            {intelResult.sources.length > 0 && (
              <details className="mt-2">
                <summary className="cursor-pointer text-xs text-zinc-600">查看情报来源（{intelResult.sources.length}）</summary>
                <ul className="mt-2 list-disc space-y-1 pl-4 text-xs text-zinc-700">
                  {intelResult.sources.map((source, idx) => (
                    <li key={`${source.url}-${idx}`}>
                      <a href={source.url} target="_blank" rel="noreferrer" className="text-indigo-700 underline">
                        {source.title}
                      </a>
                      {source.snippet ? `：${source.snippet.slice(0, 120)}` : ""}
                    </li>
                  ))}
                </ul>
              </details>
            )}
          </div>
        )}

        <div className="mt-4 rounded border border-zinc-200 p-3">
          <p className="mb-2 text-sm font-medium text-zinc-800">面试题库参数</p>
          <div className="grid gap-3 md:grid-cols-3">
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">job_id（可选）</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={prepJobId}
                onChange={(e) => setPrepJobId(e.target.value)}
                placeholder="优先使用 jobs/recent 的 id"
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">题目数量</span>
              <input
                type="number"
                min={3}
                max={20}
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={prepQuestionCount}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setPrepQuestionCount(Number.isNaN(value) ? 8 : value);
                }}
              />
            </label>
            <label className="mt-6 inline-flex items-center gap-2 text-sm text-zinc-700">
              <input
                type="checkbox"
                checked={prepUseCompanyIntel}
                onChange={(e) => setPrepUseCompanyIntel(e.target.checked)}
              />
              使用公司情报增强
            </label>
          </div>
          {prepError && <p className="mt-2 text-sm text-red-600">{prepError}</p>}
          {prepResult && (
            <div className="mt-3 rounded border border-zinc-200 bg-zinc-50 p-3 text-sm">
              <p className="font-medium">{prepResult.company} / {prepResult.role_title}</p>
              <p className="mt-1 text-zinc-700">{prepResult.summary}</p>
              <p className="mt-2 text-xs text-zinc-600">面试重点：{prepResult.likely_focus.join("、") || "-"}</p>
              <p className="text-xs text-zinc-600">叙事主线：{prepResult.key_storylines.join("；") || "-"}</p>
              <div className="mt-2 overflow-x-auto">
                <table className="w-full border-collapse text-xs">
                  <thead>
                    <tr className="border-b">
                      <th className="py-2 text-left">问题</th>
                      <th className="py-2 text-left">意图</th>
                      <th className="py-2 text-left">难度</th>
                      <th className="py-2 text-left">回答提示</th>
                    </tr>
                  </thead>
                  <tbody>
                    {prepResult.questions.map((item, idx) => (
                      <tr key={`${item.question}-${idx}`} className="border-b">
                        <td className="py-2">{item.question}</td>
                        <td className="py-2">{item.intent}</td>
                        <td className="py-2">{item.difficulty}</td>
                        <td className="py-2">{item.answer_tips.join(" / ")}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div className="mt-4 rounded border border-zinc-200 p-3">
          <p className="mb-2 text-sm font-medium text-zinc-800">安全治理（审批令牌 + 工具预算）</p>
          <div className="grid gap-3 md:grid-cols-2">
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">令牌动作 action</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={securityAction}
                onChange={(e) => setSecurityAction(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">purpose</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={securityPurpose}
                onChange={(e) => setSecurityPurpose(e.target.value)}
              />
            </label>
          </div>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded bg-black px-3 py-1.5 text-xs text-white hover:bg-zinc-800 disabled:opacity-50"
              disabled={tokenLoading}
              onClick={() => void handleIssueSecurityToken()}
            >
              签发一次性令牌
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-50"
              disabled={tokenLoading}
              onClick={() => void handleConsumeSecurityToken()}
            >
              消费令牌（防重放）
            </button>
          </div>
          {issuedToken && (
            <p className="mt-2 text-xs text-zinc-600">
              token: <code className="rounded bg-zinc-100 px-1">{issuedToken.token}</code>，过期时间：
              {new Date(issuedToken.expires_at).toLocaleString()}
            </p>
          )}
          {tokenConsumeResult && (
            <p className={`mt-1 text-xs ${tokenConsumeResult.valid ? "text-emerald-700" : "text-rose-700"}`}>
              consume result: valid={String(tokenConsumeResult.valid)}; reason={tokenConsumeResult.reason || "-"}
            </p>
          )}

          <div className="mt-3 grid gap-3 md:grid-cols-4">
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">session_id</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={budgetSessionId}
                onChange={(e) => setBudgetSessionId(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">tool_type</span>
              <input
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={budgetToolType}
                onChange={(e) => setBudgetToolType(e.target.value)}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">limit</span>
              <input
                type="number"
                min={1}
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={budgetLimit}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setBudgetLimit(Number.isNaN(value) ? 20 : value);
                }}
              />
            </label>
            <label className="block">
              <span className="mb-1 block text-xs text-zinc-600">consume</span>
              <input
                type="number"
                min={0}
                className="w-full rounded border border-zinc-300 px-3 py-2 text-sm outline-none focus:border-zinc-500"
                value={budgetConsume}
                onChange={(e) => {
                  const value = Number(e.target.value);
                  setBudgetConsume(Number.isNaN(value) ? 1 : value);
                }}
              />
            </label>
          </div>
          <label className="mt-2 inline-flex items-center gap-2 text-xs text-zinc-700">
            <input
              type="checkbox"
              checked={budgetDryRun}
              onChange={(e) => setBudgetDryRun(e.target.checked)}
            />
            dry_run（只校验不扣减）
          </label>
          <div className="mt-2 flex flex-wrap gap-2">
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-50"
              disabled={budgetLoading}
              onClick={() => void handleCheckBudget()}
            >
              预算校验/扣减
            </button>
            <button
              type="button"
              className="rounded border border-zinc-300 px-3 py-1.5 text-xs hover:bg-zinc-50 disabled:opacity-50"
              disabled={budgetLoading}
              onClick={() => void handleResetBudget()}
            >
              重置预算
            </button>
          </div>
          {budgetResult && (
            <p className={`mt-2 text-xs ${budgetResult.allowed ? "text-emerald-700" : "text-rose-700"}`}>
              allowed={String(budgetResult.allowed)}; used={budgetResult.used}; remaining={budgetResult.remaining}; reason=
              {budgetResult.reason || "-"}
            </p>
          )}
          {securityError && <p className="mt-2 text-xs text-red-600">{securityError}</p>}
        </div>
      </section>
      </details>
    </main>
  );
}
