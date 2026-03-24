from __future__ import annotations

import hashlib
import logging
import os
import re
from typing import Any, TypedDict

from langchain_core.prompts import ChatPromptTemplate
from langgraph.graph import END, START, StateGraph
from pydantic import BaseModel, Field

from .agent_events import EventType, emit
from .boss_chat_service import default_user_profile, merge_profile, preview_boss_chat_reply
from .boss_scan import execute_boss_chat_replies, pull_boss_chat_conversations
from .schemas import BossChatProcessItem, BossChatProcessResponse
from .storage import (
    get_boss_chat_event_by_signature,
    get_user_profile,
    insert_boss_chat_event,
    log_action,
)
from .workflow import _invoke_structured, run_jd_analysis

logger = logging.getLogger(__name__)


def _proactive_jd_enrichment_enabled() -> bool:
    raw = os.getenv("BOSS_CHAT_PROACTIVE_JD_ENRICHMENT", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def _run_proactive_jd_match(
    *,
    company: str | None,
    job_title: str | None,
    latest_hr_message: str,
    jd_text: str | None = None,
) -> tuple[float, str | None]:
    """复用阶段1 Matcher 评分。优先使用真实 JD，退化到伪 JD。"""
    if jd_text and len(jd_text.strip()) > 50:
        analysis_input = jd_text.strip()[:2000]
    else:
        parts = []
        if company:
            parts.append(f"公司：{company}")
        if job_title:
            parts.append(f"岗位：{job_title}")
        parts.append(f"HR消息/岗位描述：{latest_hr_message[:800]}")
        analysis_input = "\n".join(parts)
    try:
        result = run_jd_analysis(analysis_input)
        return float(result.match_score), result.gap_analysis or None
    except Exception as exc:
        logger.warning("Proactive JD enrichment failed, will fallback to source_fit: %s", exc)
        return -1.0, None


class BossChatCopilotState(TypedDict, total=False):
    max_conversations: int
    unread_only: bool
    chat_tab: str
    profile_id: str
    notify_on_escalate: bool
    fetch_latest_hr: bool
    profile: dict[str, Any]
    screenshot_path: str | None
    conversations: list[dict[str, Any]]
    candidate_messages: int
    new_count: int
    duplicated_count: int
    items: list[dict[str, Any]]


class _SourceFitParsed(BaseModel):
    fit_score: float = Field(default=0.0, ge=0, le=100)
    should_engage: bool
    reason: str = Field(default="", max_length=300)


class _ProactiveContactParsed(BaseModel):
    is_proactive_hr: bool
    confidence: float = Field(default=0.0, ge=0, le=1)
    reason: str = Field(default="", max_length=300)


_VALID_ACTIONS = {"send_resume", "reply_from_profile", "notify_user", "ignore"}


def _resume_already_sent(conversation_messages: list[dict[str, Any]]) -> bool:
    """检查对话历史中是否已发送过简历（扫描自己的消息和系统消息）。"""
    for msg in conversation_messages:
        text = str(msg.get("text") or "").strip()
        role = msg.get("role", "")
        lower_text = text.lower()
        if role == "self" and ("简历" in lower_text or "附件" in lower_text):
            return True
        if "已发送" in lower_text and "简历" in lower_text:
            return True
        if "附件简历" in lower_text:
            return True
    return False


def _safe_action(action: str | None) -> str:
    safe = str(action or "notify_user")
    return safe if safe in _VALID_ACTIONS else "notify_user"


def _message_signature(
    *,
    conversation_id: str,
    latest_hr_message: str,
    latest_hr_time: str | None,
) -> str:
    normalized_message = " ".join(latest_hr_message.strip().split())
    raw = f"{conversation_id.strip()}|{normalized_message}|{(latest_hr_time or '').strip()}"
    return hashlib.sha1(raw.encode("utf-8")).hexdigest()


def _load_profile_node(state: BossChatCopilotState) -> BossChatCopilotState:
    emit(EventType.WORKFLOW_NODE, "load_profile: 加载用户偏好")
    profile_id = str(state.get("profile_id") or "default")
    stored = get_user_profile(profile_id)
    profile = (
        stored.get("profile")
        if stored and isinstance(stored.get("profile"), dict)
        else default_user_profile()
    )
    return {"profile": merge_profile(profile)}


def _pull_conversations_node(state: BossChatCopilotState) -> BossChatCopilotState:
    chat_tab = state.get("chat_tab") or "未读"
    emit(EventType.WORKFLOW_NODE, f"pull_conversations: 抓取BOSS聊天会话 (tab={chat_tab})")
    conversations, screenshot_path = pull_boss_chat_conversations(
        max_conversations=int(state.get("max_conversations") or 20),
        unread_only=bool(state.get("unread_only", True)),
        fetch_latest_hr=bool(state.get("fetch_latest_hr", True)),
        chat_tab=chat_tab,
    )
    emit(EventType.INFO, f"pull_conversations 完成: {len(conversations)} 个会话")
    return {
        "conversations": [conv.model_dump() for conv in conversations],
        "screenshot_path": screenshot_path,
    }


def _source_fit_threshold() -> float:
    raw = os.getenv("BOSS_CHAT_SOURCE_FIT_THRESHOLD", "65").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 65.0
    return max(40.0, min(value, 95.0))


def _proactive_match_threshold() -> float:
    raw = os.getenv("BOSS_CHAT_PROACTIVE_MATCH_THRESHOLD", "70").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 70.0
    return max(50.0, min(value, 95.0))


def _estimate_proactive_contact(
    *,
    latest_hr_message: str,
    company: str | None,
    job_title: str | None,
) -> tuple[bool, float, str]:
    text = latest_hr_message.strip()
    if not text:
        return False, 0.0, "消息为空，无法判断。"
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是招聘会话判定器。判断该消息是否属于“HR主动发起联系的首轮招呼/邀约”。"
                "输出字段：is_proactive_hr(boolean)、confidence(0-1)、reason(<=80字)。\n"
                "优先判定为 true 的情况：打招呼、询问是否看机会、邀请沟通、介绍岗位。\n"
                "优先判定为 false 的情况：承接上下文回复、追问前文细节、明显是候选人已先发后的跟进。",
            ),
            (
                "human",
                "公司：{company}\n岗位：{job_title}\nHR消息：{latest_hr_message}",
            ),
        ]
    ).invoke(
        {
            "company": company or "-",
            "job_title": job_title or "-",
            "latest_hr_message": text[:500],
        }
    )
    try:
        parsed: _ProactiveContactParsed = _invoke_structured(
            prompt,
            _ProactiveContactParsed,
            route="proactive_contact",
        )
        reason = str(parsed.reason or "").strip() or "主动联系判定完成。"
        return bool(parsed.is_proactive_hr), float(parsed.confidence), reason
    except Exception:
        lowered = text.lower()
        proactive = bool(
            re.search(r"(你好|您好|在吗|看机会|有兴趣|方便沟通|聊聊|岗位|实习机会)", lowered)
        )
        followup = bool(re.search(r"(收到|好的|前面|刚才|补充|再问|继续)", lowered))
        if proactive and not followup:
            return True, 0.74, "命中主动招呼关键词，按主动联系处理。"
        return False, 0.58, "未命中主动招呼特征，按普通跟进处理。"


def _estimate_source_fit(
    *,
    profile: dict[str, Any],
    company: str | None,
    job_title: str | None,
    latest_hr_message: str,
) -> tuple[float, bool, str]:
    target_positions = profile.get("job_preference", {}).get("target_positions", [])
    work_cities = profile.get("job_preference", {}).get("work_cities", [])
    notes = str(profile.get("job_preference", {}).get("notes", "") or "").strip()
    if not company and not job_title:
        return 75.0, True, "缺少岗位标题/公司信息，暂不做硬过滤。"
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是岗位来源匹配评估器。根据候选人的目标岗位、城市、以及求职战略备注，评估该会话是否值得继续自动跟进。\n"
                "输出字段：fit_score(0-100)、should_engage(boolean)、reason(<=80字)。\n"
                "要求：\n"
                "1) 重点参考候选人的求职战略备注（notes），这是候选人的核心求职意图。\n"
                "2) 地点权重较低，候选人更关注岗位方向和业务匹配度。\n"
                "3) 保守但不过度拒绝，不确定时可给中间分并 should_engage=true。",
            ),
            (
                "human",
                "候选人目标岗位：{target_positions}\n"
                "候选人目标城市：{work_cities}\n"
                "候选人求职战略备注：{notes}\n"
                "公司：{company}\n"
                "岗位：{job_title}\n"
                "HR 最新消息：{latest_hr_message}\n",
            ),
        ]
    ).invoke(
        {
            "target_positions": ", ".join(target_positions) if isinstance(target_positions, list) else "-",
            "work_cities": ", ".join(work_cities) if isinstance(work_cities, list) else "-",
            "notes": notes if notes else "无",
            "company": company or "-",
            "job_title": job_title or "-",
            "latest_hr_message": latest_hr_message[:600],
        }
    )
    try:
        parsed: _SourceFitParsed = _invoke_structured(
            prompt,
            _SourceFitParsed,
            route="source_fit",
        )
        reason = str(parsed.reason or "").strip() or "来源匹配评估完成。"
        return float(parsed.fit_score), bool(parsed.should_engage), reason
    except Exception:
        # Fallback: keep conservative-positive to avoid missing opportunities.
        title_text = (job_title or "").lower()
        matched = False
        if isinstance(target_positions, list):
            for pos in target_positions:
                token = str(pos).strip().lower()
                if token and token in title_text:
                    matched = True
                    break
        if matched:
            return 78.0, True, "岗位标题与目标岗位关键词匹配。"
        return 62.0, True, "来源评估失败，采用保守放行策略。"


def _source_check_node(state: BossChatCopilotState) -> BossChatCopilotState:
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else default_user_profile()
    conversations_raw = state.get("conversations") or []
    threshold = _source_fit_threshold()
    checked: list[dict[str, Any]] = []
    for raw in conversations_raw:
        if not isinstance(raw, dict):
            continue
        latest_hr_message = str(raw.get("latest_hr_message") or "").strip()
        company = str(raw.get("company") or "").strip() or None
        job_title = str(raw.get("job_title") or "").strip() or None
        if not latest_hr_message:
            checked.append(dict(raw))
            continue
        fit_score, should_engage, reason = _estimate_source_fit(
            profile=profile,
            company=company,
            job_title=job_title,
            latest_hr_message=latest_hr_message,
        )
        passed = bool(should_engage) and fit_score >= threshold
        enriched = dict(raw)
        enriched["source_fit_score"] = round(float(fit_score), 1)
        enriched["source_fit_passed"] = passed
        enriched["source_fit_reason"] = reason
        checked.append(enriched)
    return {"conversations": checked}


def _proactive_gate_node(state: BossChatCopilotState) -> BossChatCopilotState:
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else default_user_profile()
    conversations_raw = state.get("conversations") or []
    proactive_threshold = _proactive_match_threshold()
    checked: list[dict[str, Any]] = []
    for raw in conversations_raw:
        if not isinstance(raw, dict):
            continue
        enriched = dict(raw)
        latest_hr_message = str(raw.get("latest_hr_message") or "").strip()
        company = str(raw.get("company") or "").strip() or None
        job_title = str(raw.get("job_title") or "").strip() or None
        if not latest_hr_message:
            checked.append(enriched)
            continue

        has_candidate_messages = raw.get("has_candidate_messages", True)
        if not has_candidate_messages:
            proactive_contact = True
            proactive_confidence = 0.95
            proactive_reason = "对话中无候选人消息，结构判定为HR首次主动联系。"
            emit(EventType.INFO, f"结构判定HR首次联系: {company or '?'}/{job_title or '?'}")
        else:
            proactive_contact, proactive_confidence, proactive_reason = _estimate_proactive_contact(
                latest_hr_message=latest_hr_message,
                company=company,
                job_title=job_title,
            )
        proactive_match_score: float | None = None
        proactive_match_passed: bool | None = None
        if proactive_contact:
            source_fit_score_raw = enriched.get("source_fit_score")
            try:
                source_fit_score = (
                    float(source_fit_score_raw) if source_fit_score_raw is not None else None
                )
            except Exception:
                source_fit_score = None

            if source_fit_score is None:
                fit_score, should_engage, fit_reason = _estimate_source_fit(
                    profile=profile,
                    company=company,
                    job_title=job_title,
                    latest_hr_message=latest_hr_message,
                )
                source_fit_score = round(float(fit_score), 1)
                enriched["source_fit_score"] = source_fit_score
                enriched["source_fit_reason"] = fit_reason
                enriched["source_fit_passed"] = bool(should_engage) and fit_score >= _source_fit_threshold()

            if _proactive_jd_enrichment_enabled():
                real_jd = str(raw.get("jd_text") or "").strip() or None
                jd_score, gap_analysis = _run_proactive_jd_match(
                    company=company,
                    job_title=job_title,
                    latest_hr_message=latest_hr_message,
                    jd_text=real_jd,
                )
                if jd_score >= 0:
                    proactive_match_score = round(float(jd_score), 1)
                    enriched["proactive_jd_match_score"] = proactive_match_score
                    if gap_analysis:
                        enriched["proactive_gap_analysis"] = gap_analysis[:400]
                else:
                    proactive_match_score = source_fit_score
            else:
                proactive_match_score = source_fit_score

            proactive_match_passed = (
                proactive_match_score is not None and proactive_match_score >= proactive_threshold
            )

        enriched["proactive_contact"] = proactive_contact
        enriched["proactive_confidence"] = round(float(proactive_confidence), 2)
        enriched["proactive_reason"] = proactive_reason
        enriched["proactive_match_score"] = proactive_match_score
        enriched["proactive_match_passed"] = proactive_match_passed
        checked.append(enriched)
    return {"conversations": checked}


def _decision_node(state: BossChatCopilotState) -> BossChatCopilotState:
    emit(EventType.WORKFLOW_NODE, "decision: 对每个会话做意图分析 + 安全检查 + 回复决策")
    profile = state.get("profile") if isinstance(state.get("profile"), dict) else default_user_profile()
    notify_on_escalate = bool(state.get("notify_on_escalate", True))
    conversations_raw = state.get("conversations") or []
    candidate_messages = 0
    new_count = 0
    duplicated_count = 0
    items: list[dict[str, Any]] = []

    for raw in conversations_raw:
        if not isinstance(raw, dict):
            continue
        conversation_id = str(raw.get("conversation_id") or "").strip()
        hr_name = str(raw.get("hr_name") or "Unknown HR").strip() or "Unknown HR"
        company = str(raw.get("company") or "").strip() or None
        job_title = str(raw.get("job_title") or "").strip() or None
        latest_hr_message = str(raw.get("latest_hr_message") or "").strip()
        latest_hr_time = str(raw.get("latest_hr_time") or "").strip() or None
        source_fit_score_raw = raw.get("source_fit_score")
        source_fit_reason = str(raw.get("source_fit_reason") or "").strip() or None
        source_fit_passed_raw = raw.get("source_fit_passed")
        source_fit_passed = bool(source_fit_passed_raw) if source_fit_passed_raw is not None else None
        try:
            source_fit_score = float(source_fit_score_raw) if source_fit_score_raw is not None else None
        except Exception:
            source_fit_score = None
        proactive_contact_raw = raw.get("proactive_contact")
        proactive_contact = bool(proactive_contact_raw) if proactive_contact_raw is not None else False
        proactive_reason = str(raw.get("proactive_reason") or "").strip() or None
        proactive_confidence_raw = raw.get("proactive_confidence")
        proactive_match_score_raw = raw.get("proactive_match_score")
        proactive_match_passed_raw = raw.get("proactive_match_passed")
        proactive_match_passed = (
            bool(proactive_match_passed_raw) if proactive_match_passed_raw is not None else None
        )
        try:
            proactive_confidence = (
                float(proactive_confidence_raw) if proactive_confidence_raw is not None else None
            )
        except Exception:
            proactive_confidence = None
        try:
            proactive_match_score = (
                float(proactive_match_score_raw) if proactive_match_score_raw is not None else None
            )
        except Exception:
            proactive_match_score = None
        proactive_jd_match_score_raw = raw.get("proactive_jd_match_score")
        try:
            proactive_jd_match_score = (
                float(proactive_jd_match_score_raw)
                if proactive_jd_match_score_raw is not None
                else None
            )
        except Exception:
            proactive_jd_match_score = None
        proactive_gap_analysis = str(raw.get("proactive_gap_analysis") or "").strip() or None
        if not conversation_id or not latest_hr_message:
            continue
        candidate_messages += 1

        signature = _message_signature(
            conversation_id=conversation_id,
            latest_hr_message=latest_hr_message,
            latest_hr_time=latest_hr_time,
        )
        existing = get_boss_chat_event_by_signature(signature)
        if existing:
            duplicated_count += 1
            items.append(
                {
                    "conversation_id": conversation_id,
                    "hr_name": hr_name,
                    "company": company,
                    "job_title": job_title,
                    "latest_hr_message": latest_hr_message,
                    "latest_hr_time": latest_hr_time,
                    "message_signature": signature,
                    "is_new": False,
                    "intent": str(existing.get("intent") or "unknown"),
                    "confidence": float(existing.get("confidence") or 0.0),
                    "action": _safe_action(existing.get("action")),
                    "reason": str(existing.get("reason") or "Duplicate message, skipped"),
                    "reply_text": str(existing.get("reply_text") or "") or None,
                    "needs_send_resume": bool(existing.get("needs_send_resume")),
                    "needs_user_intervention": bool(existing.get("needs_user_intervention")),
                    "notification_sent": bool(existing.get("notification_sent")),
                    "notification_error": str(existing.get("notification_error") or "") or None,
                    "source_fit_score": source_fit_score,
                    "source_fit_passed": source_fit_passed,
                    "source_fit_reason": source_fit_reason,
                    "proactive_contact": proactive_contact,
                    "proactive_confidence": proactive_confidence,
                    "proactive_reason": proactive_reason,
                    "proactive_match_score": proactive_match_score,
                    "proactive_match_passed": proactive_match_passed,
                    "proactive_jd_match_score": proactive_jd_match_score,
                    "proactive_gap_analysis": proactive_gap_analysis,
                }
            )
            continue

        conversation_messages = raw.get("conversation_messages") or []
        pending_hr_texts = raw.get("pending_hr_texts") or []
        hr_input = "\n".join(pending_hr_texts) if pending_hr_texts else latest_hr_message

        emit(EventType.LLM_CALL, f"分析会话 {hr_name}({company or '?'}): 待回复{len(pending_hr_texts)}条, {latest_hr_message[:50]}...")
        decision = preview_boss_chat_reply(
            hr_message=hr_input,
            profile=profile,
            company=company,
            job_title=job_title,
            notify_on_escalate=notify_on_escalate,
            conversation_messages=conversation_messages,
        )
        emit(
            EventType.INTENT_CLASSIFIED,
            f"意图={decision.intent}, 置信度={decision.confidence:.2f}, 动作={decision.action}",
            hr_name=hr_name,
            company=company,
        )
        if (
            source_fit_passed is False
            and decision.action in {"send_resume", "reply_from_profile"}
        ):
            score_text = f"{source_fit_score:.1f}" if source_fit_score is not None else "-"
            emit(EventType.SAFETY_BLOCKED, f"来源匹配拦截: {hr_name}({company}), score={score_text}", score=source_fit_score)
            decision.action = "ignore"
            decision.reason = (
                f"来源匹配未通过（score={score_text}），暂不自动回复，避免误投。"
            )
            if source_fit_reason:
                decision.reason = f"{decision.reason} {source_fit_reason}"
            decision.reply_text = None
            decision.needs_send_resume = False
            decision.needs_user_intervention = False
            decision.notification_sent = False
            decision.notification_error = None

        if proactive_contact and proactive_match_passed is False and decision.action != "ignore":
            score_text = (
                f"{proactive_match_score:.1f}" if proactive_match_score is not None else "-"
            )
            emit(EventType.SAFETY_BLOCKED, f"主动联系匹配度拦截: {hr_name}({company}), score={score_text}", score=proactive_match_score)
            decision.action = "ignore"
            decision.reason = f"HR主动联系但匹配度不足（score={score_text}），暂不跟进。"
            if proactive_reason:
                decision.reason = f"{decision.reason} {proactive_reason}"
            decision.reply_text = None
            decision.needs_send_resume = False
            decision.needs_user_intervention = False
            decision.notification_sent = False
            decision.notification_error = None

        if (
            proactive_contact
            and proactive_match_passed is True
            and decision.action in {"notify_user", "ignore", "reply_from_profile"}
            and (decision.confidence >= 0.6)
        ):
            greeting = str(profile.get("default_greeting") or "").strip()
            if not greeting:
                greeting = "您好，我对该实习岗位很感兴趣，可继续沟通。"
            if not decision.reply_text:
                decision.reply_text = greeting
            decision.action = "send_resume"
            decision.reason = "HR主动联系且匹配度达标，发送招呼并附简历。"
            decision.needs_send_resume = True
            decision.needs_user_intervention = False
            decision.notification_sent = False
            decision.notification_error = None

        if (
            decision.action in {"reply_from_profile", "send_resume"}
            and not decision.needs_send_resume
            and not _resume_already_sent(conversation_messages)
        ):
            decision.needs_send_resume = True
            decision.reason = f"{decision.reason} 检测到尚未发送简历，自动附加。"

        inserted, _event_id = insert_boss_chat_event(
            conversation_id=conversation_id,
            hr_name=hr_name,
            company=company,
            job_title=job_title,
            latest_hr_message=latest_hr_message,
            latest_hr_time=latest_hr_time,
            message_signature=signature,
            intent=decision.intent,
            confidence=decision.confidence,
            action=decision.action,
            reason=decision.reason,
            reply_text=decision.reply_text,
            needs_send_resume=decision.needs_send_resume,
            needs_user_intervention=decision.needs_user_intervention,
            notification_sent=decision.notification_sent,
            notification_error=decision.notification_error,
        )
        if inserted:
            new_count += 1
        else:
            duplicated_count += 1

        has_candidate = raw.get("has_candidate_messages", True)
        pending_count = len(raw.get("pending_hr_texts") or [])
        msg_count = len(raw.get("conversation_messages") or [])
        if not has_candidate:
            conv_state = "first_contact"
        elif decision.needs_send_resume:
            conv_state = "sending_resume"
        elif decision.action in {"reply_from_profile", "send_resume"}:
            conv_state = "chatting"
        elif decision.action == "ignore":
            conv_state = "ignored"
        else:
            conv_state = "escalated"

        log_action(
            job_id=None,
            action_type="boss_chat_process",
            input_summary=(
                f"conversation_id={conversation_id}; hr_name={hr_name}; "
                f"company={company or ''}; job_title={job_title or ''}; "
                f"msg_count={msg_count}; pending_hr={pending_count}; "
                f"has_candidate_msgs={has_candidate}; "
                f"latest_hr_message={latest_hr_message[:200]}"
            ),
            output_summary=(
                f"conv_state={conv_state}; intent={decision.intent}; "
                f"confidence={decision.confidence:.2f}; action={decision.action}; "
                f"needs_send_resume={decision.needs_send_resume}; "
                f"needs_user_intervention={decision.needs_user_intervention}; "
                f"reason={decision.reason}; source_fit={source_fit_score}; "
                f"source_fit_passed={source_fit_passed}; proactive={proactive_contact}; "
                f"proactive_match_passed={proactive_match_passed}; is_new={inserted}"
            ),
            status="success",
        )
        items.append(
            {
                "conversation_id": conversation_id,
                "hr_name": hr_name,
                "company": company,
                "job_title": job_title,
                "latest_hr_message": latest_hr_message,
                "latest_hr_time": latest_hr_time,
                "message_signature": signature,
                "is_new": inserted,
                "intent": decision.intent,
                "confidence": decision.confidence,
                "action": decision.action,
                "reason": decision.reason,
                "reply_text": decision.reply_text,
                "needs_send_resume": decision.needs_send_resume,
                "needs_user_intervention": decision.needs_user_intervention,
                "notification_sent": decision.notification_sent,
                "notification_error": decision.notification_error,
                "source_fit_score": source_fit_score,
                "source_fit_passed": source_fit_passed,
                "source_fit_reason": source_fit_reason,
                "proactive_contact": proactive_contact,
                "proactive_confidence": proactive_confidence,
                "proactive_reason": proactive_reason,
                "proactive_match_score": proactive_match_score,
                "proactive_match_passed": proactive_match_passed,
                "proactive_jd_match_score": proactive_jd_match_score,
                "proactive_gap_analysis": proactive_gap_analysis,
            }
        )

    return {
        "candidate_messages": candidate_messages,
        "new_count": new_count,
        "duplicated_count": duplicated_count,
        "items": items,
    }


def _build_graph():
    graph = StateGraph(BossChatCopilotState)
    graph.add_node("load_profile", _load_profile_node)
    graph.add_node("pull_conversations", _pull_conversations_node)
    graph.add_node("source_check", _source_check_node)
    graph.add_node("proactive_gate", _proactive_gate_node)
    graph.add_node("decision", _decision_node)
    graph.add_edge(START, "load_profile")
    graph.add_edge("load_profile", "pull_conversations")
    graph.add_edge("pull_conversations", "source_check")
    graph.add_edge("source_check", "proactive_gate")
    graph.add_edge("proactive_gate", "decision")
    graph.add_edge("decision", END)
    return graph.compile()


_GRAPH = _build_graph()


def _run_copilot_pipeline(initial_state: BossChatCopilotState) -> BossChatCopilotState:
    # 对话巡检链路固定为串行节点，直接顺序执行可避免在 LangGraph 内部
    # 触发 asyncio loop 与 Patchright Sync API 的兼容性冲突。
    state: BossChatCopilotState = dict(initial_state)
    for node in (
        _load_profile_node,
        _pull_conversations_node,
        _source_check_node,
        _proactive_gate_node,
        _decision_node,
    ):
        node_output = node(state)
        if node_output:
            state.update(node_output)
    return state


def _auto_execute_enabled() -> bool:
    raw = os.getenv("BOSS_CHAT_AUTO_EXECUTE_ENABLED", "true").strip().lower()
    return raw in ("1", "true", "yes", "on")


def run_boss_chat_copilot_workflow(
    *,
    max_conversations: int,
    unread_only: bool,
    profile_id: str,
    notify_on_escalate: bool,
    fetch_latest_hr: bool = True,
    auto_execute: bool = False,
    chat_tab: str = "未读",
) -> BossChatProcessResponse:
    emit(EventType.WORKFLOW_START, f"boss_chat_copilot: 启动智能聊天处理流水线 (tab={chat_tab})")
    state = _run_copilot_pipeline(
        {
            "max_conversations": max_conversations,
            "unread_only": unread_only,
            "chat_tab": chat_tab,
            "profile_id": profile_id,
            "notify_on_escalate": notify_on_escalate,
            "fetch_latest_hr": fetch_latest_hr,
        }
    )
    items_raw = state.get("items") or []
    items: list[BossChatProcessItem] = []
    for raw in items_raw:
        if not isinstance(raw, dict):
            continue
        try:
            items.append(BossChatProcessItem.model_validate(raw))
        except Exception:
            continue
    conversations = state.get("conversations") or []

    if auto_execute and _auto_execute_enabled():
        to_send = [
            (str(it.conversation_id), str(it.reply_text or ""))
            for it in items
            if it.is_new
            and it.action in {"reply_from_profile", "send_resume"}
            and (it.reply_text or "").strip()
            and it.source_fit_passed is not False
            and (it.proactive_contact is not True or it.proactive_match_passed is True)
        ]
        resume_cids = [
            str(it.conversation_id)
            for it in items
            if it.is_new
            and it.needs_send_resume
            and it.source_fit_passed is not False
            and (it.proactive_contact is not True or it.proactive_match_passed is True)
        ]
        if to_send or resume_cids:
            try:
                exec_results = execute_boss_chat_replies(
                    items_to_send=[(cid, txt) for cid, txt in to_send if txt.strip()],
                    resume_conversation_ids=resume_cids if resume_cids else None,
                    max_conversations=max_conversations,
                )
                exec_map = {cid: (ok, err) for cid, ok, err in exec_results}
                new_items: list[BossChatProcessItem] = []
                for it in items:
                    if it.conversation_id in exec_map:
                        ok, err = exec_map[it.conversation_id]
                        new_items.append(
                            it.model_copy(update={"reply_sent": ok, "reply_sent_error": err})
                        )
                    else:
                        new_items.append(it)
                items = new_items
            except Exception as exc:
                logger.exception("Auto execute failed: %s", exc)
                all_cids = {c for c, _ in to_send} | set(resume_cids)
                new_items = []
                for it in items:
                    if it.conversation_id in all_cids:
                        new_items.append(
                            it.model_copy(update={"reply_sent_error": str(exc)[:200]})
                        )
                    else:
                        new_items.append(it)
                items = new_items

    return BossChatProcessResponse(
        total_conversations=len(conversations),
        candidate_messages=int(state.get("candidate_messages") or 0),
        processed_count=len(items),
        new_count=int(state.get("new_count") or 0),
        duplicated_count=int(state.get("duplicated_count") or 0),
        screenshot_path=state.get("screenshot_path"),
        items=items,
    )
