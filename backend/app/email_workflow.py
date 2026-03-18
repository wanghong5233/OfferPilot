from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime
from typing import TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .schemas import EmailClassification
from .storage import find_job_id_by_company, log_action, update_job_status
from .workflow import _invoke_structured


class EmailState(TypedDict, total=False):
    sender: str
    subject: str
    body: str
    received_at: datetime | None
    email_type: str
    company: str | None
    interview_time: str | None
    confidence: float
    reason: str | None
    related_job_id: str | None
    updated_job_status: str | None


class _EmailParsed(BaseModel):
    email_type: str = Field(..., description="interview_invite | rejection | need_material | irrelevant")
    company: str | None = None
    interview_time: str | None = None
    confidence: float = Field(default=0.8, ge=0, le=1)
    reason: str | None = None


@dataclass
class EmailWorkflowResult:
    classification: EmailClassification
    related_job_id: str | None
    updated_job_status: str | None


@dataclass
class EmailScheduleCandidate:
    event_type: str  # interview | written_test | other
    start_at: datetime
    raw_time_text: str | None
    mode: str  # online | offline | unknown
    location: str | None
    contact: str | None
    confidence: float


def _extract_company(sender: str, subject: str, body: str) -> str | None:
    bracket = re.search(r"[【\[]\s*([A-Za-z0-9\u4e00-\u9fff]{2,30})\s*[】\]]", subject)
    if bracket:
        return bracket.group(1).strip()

    sender_match = re.search(r"@([A-Za-z0-9\-]{2,40})\.", sender)
    if sender_match:
        return sender_match.group(1).strip()

    body_match = re.search(r"([A-Za-z0-9\u4e00-\u9fff]{2,24})(?:公司|科技|集团|实验室)", body)
    if body_match:
        return body_match.group(1).strip()
    return None


def _extract_interview_time(text: str) -> str | None:
    patterns = [
        r"\d{4}[/-]\d{1,2}[/-]\d{1,2}\s*\d{1,2}:\d{2}",
        r"\d{4}年\d{1,2}月\d{1,2}日\s*\d{1,2}:\d{2}",
        r"\d{4}[/-]\d{1,2}[/-]\d{1,2}",
        r"\d{4}年\d{1,2}月\d{1,2}日",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if match:
            return match.group(0).strip()
    return None


def _parse_schedule_datetime(raw: str, *, reference_time: datetime | None = None) -> datetime | None:
    text = (raw or "").strip()
    if not text:
        return None
    ref = reference_time or datetime.utcnow()
    patterns = [
        # 2026-03-20 14:00 / 2026年3月20日 14:00
        r"(?P<y>\d{4})[年/-](?P<m>\d{1,2})[月/-](?P<d>\d{1,2})[日号]?\s*(?P<h>\d{1,2})[:：](?P<mm>\d{2})",
        # 2026-03-20 / 2026年3月20日
        r"(?P<y>\d{4})[年/-](?P<m>\d{1,2})[月/-](?P<d>\d{1,2})[日号]?",
        # 3月20日 14:00 (year inferred)
        r"(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]?\s*(?P<h>\d{1,2})[:：](?P<mm>\d{2})",
        # 3月20日 (year inferred)
        r"(?P<m>\d{1,2})月(?P<d>\d{1,2})[日号]?",
    ]
    for pattern in patterns:
        match = re.search(pattern, text)
        if not match:
            continue
        groups = match.groupdict()
        try:
            year = int(groups.get("y") or ref.year)
            month = int(groups.get("m") or ref.month)
            day = int(groups.get("d") or ref.day)
            hour = int(groups.get("h") or 9)
            minute = int(groups.get("mm") or 0)
            return datetime(year, month, day, hour, minute)
        except Exception:
            continue
    return None


def _infer_schedule_mode(text: str) -> str:
    low = text.lower()
    if any(token in low for token in ["zoom", "腾讯会议", "飞书会议", "teams", "线上", "视频面试"]):
        return "online"
    if any(token in low for token in ["线下", "到场", "现场面试", "公司面试"]):
        return "offline"
    return "unknown"


def _extract_schedule_location(text: str) -> str | None:
    match = re.search(
        r"(?:地点|地址|location|面试地址|会议地点)\s*[:：]\s*([^\n，,。;；]{2,120})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip() or None


def _extract_schedule_contact(text: str) -> str | None:
    match = re.search(
        r"(?:联系人|联系邮箱|联系电话|电话|微信)\s*[:：]\s*([^\n，,。;；]{2,120})",
        text,
        re.IGNORECASE,
    )
    if not match:
        return None
    return match.group(1).strip() or None


def extract_schedule_candidate(
    *,
    classification: EmailClassification,
    subject: str,
    body: str,
    received_at: datetime | None = None,
) -> EmailScheduleCandidate | None:
    """
    从邮件分类结果中提取可落库的日程信息。
    当前仅提取带明确日期/时间的安排，避免误写入。
    """
    email_type = str(classification.email_type or "")
    merged = f"{subject}\n{body}"
    low = merged.lower()
    has_schedule_intent = (
        email_type == "interview_invite"
        or any(token in low for token in ["笔试", "测评", "online assessment", "机考", "面试"])
    )
    if not has_schedule_intent:
        return None

    raw_time = str(classification.interview_time or "").strip() or _extract_interview_time(merged)
    if not raw_time:
        return None
    start_at = _parse_schedule_datetime(raw_time, reference_time=received_at)
    if not start_at:
        # Try parse again from full text for cases where interview_time extracted poorly.
        fallback_raw = _extract_interview_time(merged)
        if not fallback_raw:
            return None
        raw_time = fallback_raw
        start_at = _parse_schedule_datetime(raw_time, reference_time=received_at)
        if not start_at:
            return None

    event_type = "written_test" if any(token in low for token in ["笔试", "测评", "机考"]) else "interview"
    mode = _infer_schedule_mode(merged)
    location = _extract_schedule_location(merged)
    contact = _extract_schedule_contact(merged)
    confidence = max(0.0, min(float(classification.confidence or 0.0), 1.0))
    return EmailScheduleCandidate(
        event_type=event_type,
        start_at=start_at,
        raw_time_text=raw_time[:120],
        mode=mode,
        location=location,
        contact=contact,
        confidence=confidence,
    )


def _heuristic_classify(sender: str, subject: str, body: str) -> EmailClassification:
    text = f"{subject}\n{body}".lower()
    company = _extract_company(sender, subject, body)
    interview_time = _extract_interview_time(f"{subject}\n{body}")

    if any(key in text for key in ["面试", "interview", "一面", "二面", "三面"]):
        return EmailClassification(
            email_type="interview_invite",
            company=company,
            interview_time=interview_time,
            confidence=0.78,
            reason="Contains interview invitation keywords",
        )
    if any(key in text for key in ["未通过", "不合适", "感谢投递", "rejected", "regret"]):
        return EmailClassification(
            email_type="rejection",
            company=company,
            interview_time=None,
            confidence=0.82,
            reason="Contains rejection keywords",
        )
    if any(key in text for key in ["补充", "补交", "材料", "作品集", "附件", "portfolio", "resume"]):
        return EmailClassification(
            email_type="need_material",
            company=company,
            interview_time=None,
            confidence=0.74,
            reason="Contains material request keywords",
        )
    return EmailClassification(
        email_type="irrelevant",
        company=company,
        interview_time=None,
        confidence=0.6,
        reason="No high-confidence hiring keywords found",
    )


def _classify_node(state: EmailState) -> EmailState:
    sender = str(state.get("sender") or "")
    subject = str(state.get("subject") or "")
    body = str(state.get("body") or "")

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "Classify recruiting emails into one of: "
                "interview_invite, rejection, need_material, irrelevant. "
                "Return concise fields only.",
            ),
            (
                "human",
                "Sender: {sender}\n"
                "Subject: {subject}\n"
                "Body:\n{body}\n\n"
                "Rules:\n"
                "- interview_invite: asks candidate to attend interview\n"
                "- rejection: explicit rejection/thanks for applying but not moving on\n"
                "- need_material: asks for supplementary materials\n"
                "- irrelevant: unrelated to application progress\n"
                "- company: infer from sender/subject/body if possible\n"
                "- interview_time: extract if any explicit date/time appears\n",
            ),
        ]
    ).invoke({"sender": sender, "subject": subject, "body": body})

    try:
        parsed: _EmailParsed = _invoke_structured(prompt, _EmailParsed)
        email_type = str(parsed.email_type or "").strip().lower()
        if email_type not in {"interview_invite", "rejection", "need_material", "irrelevant"}:
            raise ValueError(f"invalid email_type={email_type}")
        classification = EmailClassification(
            email_type=email_type,  # type: ignore[arg-type]
            company=(parsed.company.strip() if isinstance(parsed.company, str) and parsed.company.strip() else None),
            interview_time=(
                parsed.interview_time.strip()
                if isinstance(parsed.interview_time, str) and parsed.interview_time.strip()
                else None
            ),
            confidence=float(parsed.confidence),
            reason=parsed.reason.strip() if isinstance(parsed.reason, str) and parsed.reason.strip() else None,
        )
    except Exception:
        classification = _heuristic_classify(sender, subject, body)

    return {
        "email_type": classification.email_type,
        "company": classification.company,
        "interview_time": classification.interview_time,
        "confidence": classification.confidence,
        "reason": classification.reason,
    }


def _status_for_email_type(email_type: str) -> str | None:
    mapping = {
        "interview_invite": "interviewing",
        "rejection": "rejected",
        "need_material": "need_material",
    }
    return mapping.get(email_type)


def _update_status_node(state: EmailState) -> EmailState:
    email_type = str(state.get("email_type") or "")
    company = str(state.get("company") or "").strip()
    target_status = _status_for_email_type(email_type)
    if not target_status or not company:
        return {"related_job_id": None, "updated_job_status": None}

    job_id = find_job_id_by_company(company)
    if not job_id:
        return {"related_job_id": None, "updated_job_status": None}

    updated = update_job_status(job_id, target_status)
    if updated:
        log_action(
            job_id=job_id,
            action_type="email_update",
            input_summary=f"email_type={email_type}; company={company}",
            output_summary=f"status -> {target_status}",
            status="success",
        )
        return {"related_job_id": job_id, "updated_job_status": target_status}
    return {"related_job_id": job_id, "updated_job_status": None}


def _build_graph():
    graph = StateGraph(EmailState)
    graph.add_node("classify", _classify_node)
    graph.add_node("update_status", _update_status_node)
    graph.add_edge(START, "classify")
    graph.add_edge("classify", "update_status")
    graph.add_edge("update_status", END)
    return graph.compile()


_GRAPH = _build_graph()


def run_email_workflow(*, sender: str, subject: str, body: str, received_at: datetime | None = None) -> EmailWorkflowResult:
    state = _GRAPH.invoke(
        {
            "sender": sender,
            "subject": subject,
            "body": body,
            "received_at": received_at,
        }
    )
    classification = EmailClassification(
        email_type=str(state.get("email_type") or "irrelevant"),  # type: ignore[arg-type]
        company=(str(state.get("company")).strip() if state.get("company") else None),
        interview_time=(str(state.get("interview_time")).strip() if state.get("interview_time") else None),
        confidence=float(state.get("confidence") or 0.0),
        reason=(str(state.get("reason")).strip() if state.get("reason") else None),
    )
    return EmailWorkflowResult(
        classification=classification,
        related_job_id=(str(state.get("related_job_id")) if state.get("related_job_id") else None),
        updated_job_status=(str(state.get("updated_job_status")) if state.get("updated_job_status") else None),
    )
