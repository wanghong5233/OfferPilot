from __future__ import annotations

import copy
import os
import re
from dataclasses import dataclass
from typing import Any, Literal

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .email_notify import send_channel_notification
from .workflow import _invoke_structured

ReplyAction = Literal["send_resume", "reply_from_profile", "notify_user", "ignore"]


DEFAULT_USER_PROFILE: dict[str, Any] = {
    "personal": {
        "name": "",
        "education": "",
        "major": "",
        "graduation_year": 2027,
        "age": 24,
        "current_status": "在校",
        "phone": "",
        "wechat": "",
        "email": "",
    },
    "skills": {
        "tech_stack": [],
        "experience_summary": "",
        "english_level": "",
        "portfolio_links": [],
    },
    "job_preference": {
        "job_type": "intern",
        "target_positions": [],
        "work_cities": [],
        "expected_daily_salary": "",
        "internship_duration": "",
        "available_days_per_week": 5,
        "earliest_start_date": "",
        "is_remote_ok": True,
        "overtime_ok": True,
        "notes": "",
    },
    "default_greeting": "",
    "reply_policy": {
        "auto_reply_topics": [
            "expected_salary",
            "work_location",
            "start_date",
            "internship_duration",
            "weekly_availability",
            "education_background",
            "graduation_year",
            "contact_info",
            "tech_stack",
            "experience",
            "current_status",
            "overtime",
            "english_level",
            "express_interest",
            "request_resume",
        ],
        "escalate_topics": [
            "technical_questions",
            "salary_negotiation",
            "project_details",
            "personal_questions",
            "unknown",
        ],
        "tone": "礼貌、简洁、专业",
    },
}


class _HRMessageParsed(BaseModel):
    intent: str = Field(
        ...,
        description=(
            "request_resume | ask_salary | ask_location | ask_availability | ask_education | "
            "ask_contact | ask_skills | ask_experience | ask_status | ask_overtime | ask_english | "
            "express_interest | reject | technical_question | unknown"
        ),
    )
    confidence: float = Field(default=0.0, ge=0, le=1)
    extracted_question: str | None = None


class _HRReplyPlanParsed(BaseModel):
    action: ReplyAction = Field(
        ...,
        description="send_resume | reply_from_profile | notify_user | ignore",
    )
    policy_topic: str = Field(
        ...,
        description=(
            "request_resume | expected_salary | work_location | start_date | "
            "internship_duration | weekly_availability | education_background | graduation_year | "
            "contact_info | tech_stack | experience | current_status | overtime | english_level | "
            "technical_questions | salary_negotiation | project_details | personal_questions | "
            "express_interest | reject | unknown"
        ),
    )
    reason: str = Field(..., min_length=2, max_length=200)


@dataclass
class BossChatReplyDecision:
    intent: str
    confidence: float
    action: ReplyAction
    reason: str
    extracted_question: str | None
    reply_text: str | None
    needs_send_resume: bool
    needs_user_intervention: bool
    matched_profile_fields: list[str]
    notification_sent: bool
    notification_error: str | None


_VALID_INTENTS = {
    "request_resume",
    "ask_salary",
    "ask_location",
    "ask_availability",
    "ask_education",
    "ask_contact",
    "ask_skills",
    "ask_experience",
    "ask_status",
    "ask_overtime",
    "ask_english",
    "express_interest",
    "reject",
    "technical_question",
    "unknown",
}

_VALID_POLICY_TOPICS = {
    "request_resume",
    "expected_salary",
    "work_location",
    "start_date",
    "internship_duration",
    "weekly_availability",
    "education_background",
    "graduation_year",
    "contact_info",
    "tech_stack",
    "experience",
    "current_status",
    "overtime",
    "english_level",
    "technical_questions",
    "salary_negotiation",
    "project_details",
    "personal_questions",
    "express_interest",
    "reject",
    "unknown",
}


def default_user_profile() -> dict[str, Any]:
    return copy.deepcopy(DEFAULT_USER_PROFILE)


def merge_profile(profile: dict[str, Any] | None) -> dict[str, Any]:
    base = default_user_profile()
    if not isinstance(profile, dict):
        return base

    def _deep_merge(target: dict[str, Any], source: dict[str, Any]) -> dict[str, Any]:
        for key, value in source.items():
            if isinstance(value, dict) and isinstance(target.get(key), dict):
                target[key] = _deep_merge(dict(target[key]), value)
            else:
                target[key] = value
        return target

    return _deep_merge(base, profile)


def _deep_get(data: dict[str, Any], path: str) -> Any:
    cursor: Any = data
    for part in path.split("."):
        if not isinstance(cursor, dict):
            return None
        cursor = cursor.get(part)
        if cursor is None:
            return None
    return cursor


def _intent_to_policy_topic(intent: str) -> str:
    mapping = {
        "request_resume": "request_resume",
        "ask_salary": "expected_salary",
        "ask_location": "work_location",
        "ask_availability": "internship_duration",
        "ask_education": "education_background",
        "ask_contact": "contact_info",
        "ask_skills": "tech_stack",
        "ask_experience": "experience",
        "ask_status": "current_status",
        "ask_overtime": "overtime",
        "ask_english": "english_level",
        "express_interest": "express_interest",
        "reject": "reject",
        "technical_question": "technical_questions",
    }
    return mapping.get(intent, "unknown")


def _allowed_auto_reply_topics(profile: dict[str, Any]) -> set[str]:
    raw = _deep_get(profile, "reply_policy.auto_reply_topics")
    if not isinstance(raw, list):
        return {
            "expected_salary", "work_location", "internship_duration",
            "education_background", "contact_info", "tech_stack",
            "experience", "express_interest", "request_resume",
            "current_status", "overtime", "english_level",
        }
    return {str(item).strip() for item in raw if str(item).strip()}


def _escalate_topics(profile: dict[str, Any]) -> set[str]:
    raw = _deep_get(profile, "reply_policy.escalate_topics")
    if not isinstance(raw, list):
        return {"technical_questions", "salary_negotiation", "project_details", "personal_questions", "unknown"}
    return {str(item).strip() for item in raw if str(item).strip()}


def _reply_tone(profile: dict[str, Any]) -> str:
    raw = _deep_get(profile, "reply_policy.tone")
    if isinstance(raw, str) and raw.strip():
        return raw.strip()
    return "礼貌、简洁、专业"


def _confidence_threshold() -> float:
    raw = os.getenv("BOSS_CHAT_CONFIDENCE_THRESHOLD", "0.7").strip()
    try:
        value = float(raw)
    except ValueError:
        value = 0.7
    return max(0.3, min(value, 0.95))


def _heuristic_intent(message: str) -> tuple[str, float, str | None]:
    text = message.strip()
    lowered = text.lower()
    if not text:
        return "unknown", 0.0, None
    if re.search(r"(发.*简历|简历.*发|简历给我|resume)", lowered):
        return "request_resume", 0.9, text
    if re.search(r"(日薪|薪资|薪酬|工资|多少钱|期望薪资)", lowered):
        return "ask_salary", 0.86, text
    if re.search(r"(地点|城市|在哪|base|现场|远程)", lowered):
        return "ask_location", 0.84, text
    if re.search(r"(到岗|实习多久|几个月|每周几天|出勤|全勤)", lowered):
        return "ask_availability", 0.84, text
    if re.search(r"(学历|学校|专业|毕业|届|年龄)", lowered):
        return "ask_education", 0.82, text
    if re.search(r"(微信|加个v|手机号|联系方式|电话|方便联系)", lowered):
        return "ask_contact", 0.88, text
    if re.search(r"(技术栈|会什么|擅长|熟悉哪些|编程语言|框架)", lowered):
        return "ask_skills", 0.85, text
    if re.search(r"(项目经验|实习经验|工作经验|做过什么项目|经历)", lowered):
        return "ask_experience", 0.83, text
    if re.search(r"(在校|在职|离职|目前状态|现在在)", lowered):
        return "ask_status", 0.82, text
    if re.search(r"(加班|overtime|弹性工作)", lowered):
        return "ask_overtime", 0.80, text
    if re.search(r"(英语|英文|english|四六级|雅思|托福)", lowered):
        return "ask_english", 0.80, text
    if re.search(r"(感兴趣|安排面试|约面|进一步沟通|有兴趣)", lowered):
        return "express_interest", 0.8, text
    if re.search(r"(不合适|先这样|暂不考虑|谢谢关注|拒绝)", lowered):
        return "reject", 0.85, text
    if re.search(r"(原理|为什么|实现|细节|怎么做|架构|性能|压测|并发|算法)", lowered):
        return "technical_question", 0.72, text
    return "unknown", 0.55, text


def classify_hr_message(message: str) -> tuple[str, float, str | None]:
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是招聘沟通意图分类器。请将 HR 消息分类为以下固定标签之一：\n"
                "- request_resume: 明确索要简历\n"
                "- ask_salary: 询问薪资/日薪期望\n"
                "- ask_location: 询问工作地点/是否接受远程\n"
                "- ask_availability: 询问到岗时间/实习时长/每周出勤\n"
                "- ask_education: 询问学历/学校/专业/毕业年份\n"
                "- ask_contact: 索要微信/手机号/联系方式\n"
                "- ask_skills: 询问技术栈/擅长什么/编程语言框架\n"
                "- ask_experience: 询问项目经验/实习经历\n"
                "- ask_status: 询问在校/在职/离职状态\n"
                "- ask_overtime: 询问是否接受加班/弹性工作\n"
                "- ask_english: 询问英语水平/四六级/雅思\n"
                "- express_interest: HR表达兴趣/邀请面试/想进一步沟通\n"
                "- reject: HR明确拒绝/岗位已满/不合适\n"
                "- technical_question: 深入技术细节/架构方案/算法等需专业回答的问题\n"
                "- unknown: 以上均不匹配\n\n"
                "规则：优先选择最具体的标签，不确定时选 unknown。",
            ),
            (
                "human",
                "HR 消息：\n{message}\n\n"
                "输出字段：\n"
                "- intent: 上述固定标签之一\n"
                "- confidence: 0~1\n"
                "- extracted_question: 提取出的核心问题（可为空）",
            ),
        ]
    ).invoke({"message": message})

    try:
        parsed: _HRMessageParsed = _invoke_structured(prompt, _HRMessageParsed)
        intent = str(parsed.intent or "").strip().lower()
        if intent not in _VALID_INTENTS:
            raise ValueError(f"invalid intent: {intent}")
        extracted_question = (
            parsed.extracted_question.strip()
            if isinstance(parsed.extracted_question, str) and parsed.extracted_question.strip()
            else None
        )
        return intent, float(parsed.confidence), extracted_question
    except Exception:
        return _heuristic_intent(message)


def _default_reply_plan(intent: str) -> tuple[ReplyAction, str, str]:
    if intent == "reject":
        return "ignore", "reject", "HR 明确拒绝或结束沟通，本轮不自动回复。"
    if intent in {"technical_question", "unknown"}:
        topic = "technical_questions" if intent == "technical_question" else "unknown"
        return "notify_user", topic, "问题超出自动回复白名单，需人工介入。"
    if intent == "request_resume":
        return "send_resume", "request_resume", "HR 索取简历，建议发送附件简历。"
    if intent == "express_interest":
        return "reply_from_profile", "express_interest", "HR 表达兴趣，主动回复确认意向以推进转化。"
    topic = _intent_to_policy_topic(intent)
    return "reply_from_profile", topic, "命中常规问答类型，可基于求职画像自动回复。"


def _plan_hr_reply(
    *,
    hr_message: str,
    intent: str,
    confidence: float,
    extracted_question: str | None,
    profile: dict[str, Any],
) -> tuple[ReplyAction, str, str]:
    default_action, default_topic, default_reason = _default_reply_plan(intent)
    allowed_topics = sorted(_allowed_auto_reply_topics(profile))
    escalate_topics = sorted(_escalate_topics(profile))
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是招聘沟通策略规划器。目标：在安全前提下给出下一步动作。\n"
                "动作只能是：send_resume / reply_from_profile / notify_user / ignore。\n"
                "policy_topic 必须是：request_resume / expected_salary / work_location / start_date / "
                "internship_duration / weekly_availability / education_background / graduation_year / "
                "technical_questions / salary_negotiation / project_details / personal_questions / "
                "express_interest / reject / unknown。\n"
                "规则：\n"
                "1) 技术细节/项目细节/薪资谈判/隐私类问题优先 notify_user。\n"
                "2) 仅在常规资料问答时可 reply_from_profile。\n"
                "3) reject 一律 ignore。\n"
                "4) request_resume 通常 send_resume。\n"
                "5) 若存在不确定性，优先 notify_user（宁可保守）。",
            ),
            (
                "human",
                "HR 消息：{hr_message}\n"
                "intent：{intent}\n"
                "confidence：{confidence}\n"
                "extracted_question：{extracted_question}\n"
                "允许自动回复话题：{allowed_topics}\n"
                "强制升级话题：{escalate_topics}\n"
                "请输出 action、policy_topic、reason。",
            ),
        ]
    ).invoke(
        {
            "hr_message": hr_message,
            "intent": intent,
            "confidence": f"{confidence:.2f}",
            "extracted_question": extracted_question or "",
            "allowed_topics": ", ".join(allowed_topics) if allowed_topics else "-",
            "escalate_topics": ", ".join(escalate_topics) if escalate_topics else "-",
        }
    )
    try:
        parsed: _HRReplyPlanParsed = _invoke_structured(prompt, _HRReplyPlanParsed)
        action = parsed.action
        topic = str(parsed.policy_topic or "").strip()
        reason = str(parsed.reason or "").strip()
        if topic not in _VALID_POLICY_TOPICS:
            topic = default_topic
        if not reason:
            reason = default_reason
        return action, topic, reason
    except Exception:
        return default_action, default_topic, default_reason


class _LLMReplyParsed(BaseModel):
    reply_text: str = Field(..., min_length=2, max_length=500)
    can_reply: bool = Field(default=True)
    reason: str = Field(default="", max_length=200)


def _build_profile_text(profile: dict[str, Any]) -> str:
    """将完整 profile 序列化为结构化文本，供 LLM 作为上下文。"""
    personal = profile.get("personal", {})
    skills_data = profile.get("skills", {})
    pref = profile.get("job_preference", {})

    sections: list[str] = []

    name = str(personal.get("name") or "").strip()
    education = str(personal.get("education") or "").strip()
    major = str(personal.get("major") or "").strip()
    grad_year = personal.get("graduation_year")
    age = personal.get("age")
    current_status = str(personal.get("current_status") or "").strip()
    phone = str(personal.get("phone") or "").strip()
    wechat = str(personal.get("wechat") or "").strip()
    email = str(personal.get("email") or "").strip()

    personal_parts = []
    if name:
        personal_parts.append(f"姓名：{name}")
    if education:
        personal_parts.append(f"学历：{education}")
    if major:
        personal_parts.append(f"专业：{major}")
    if grad_year:
        personal_parts.append(f"毕业年份：{grad_year}届")
    if age:
        personal_parts.append(f"年龄：{age}")
    if current_status:
        personal_parts.append(f"当前状态：{current_status}")
    if phone:
        personal_parts.append(f"手机：{phone}")
    if wechat:
        personal_parts.append(f"微信：{wechat}")
    if email:
        personal_parts.append(f"邮箱：{email}")
    if personal_parts:
        sections.append("【个人信息】\n" + "\n".join(personal_parts))

    tech_stack = skills_data.get("tech_stack") or []
    exp_summary = str(skills_data.get("experience_summary") or "").strip()
    english = str(skills_data.get("english_level") or "").strip()
    portfolio = skills_data.get("portfolio_links") or []

    skills_parts = []
    if tech_stack:
        stack_str = ", ".join(str(s) for s in tech_stack) if isinstance(tech_stack, list) else str(tech_stack)
        skills_parts.append(f"技术栈：{stack_str}")
    if exp_summary:
        skills_parts.append(f"经验摘要：{exp_summary[:400]}")
    if english:
        skills_parts.append(f"英语水平：{english}")
    if portfolio:
        links = ", ".join(str(l) for l in portfolio) if isinstance(portfolio, list) else str(portfolio)
        skills_parts.append(f"作品链接：{links}")
    if skills_parts:
        sections.append("【技能与经验】\n" + "\n".join(skills_parts))

    targets = pref.get("target_positions") or []
    cities = pref.get("work_cities") or []
    salary = str(pref.get("expected_daily_salary") or "").strip()
    duration = str(pref.get("internship_duration") or "").strip()
    days_per_week = pref.get("available_days_per_week")
    start_date = str(pref.get("earliest_start_date") or "").strip()
    remote_ok = pref.get("is_remote_ok")
    overtime_ok = pref.get("overtime_ok")
    notes = str(pref.get("notes") or "").strip()

    pref_parts = []
    if targets:
        t_str = ", ".join(str(t) for t in targets) if isinstance(targets, list) else str(targets)
        pref_parts.append(f"目标岗位：{t_str}")
    if cities:
        c_str = ", ".join(str(c) for c in cities) if isinstance(cities, list) else str(cities)
        pref_parts.append(f"目标城市：{c_str}")
    if salary:
        pref_parts.append(f"期望日薪：{salary}")
    if duration:
        pref_parts.append(f"实习时长：{duration}")
    if days_per_week is not None:
        pref_parts.append(f"每周可出勤：{days_per_week}天")
    if start_date:
        pref_parts.append(f"最早到岗：{start_date}")
    if remote_ok is not None:
        pref_parts.append(f"接受远程：{'是' if remote_ok else '否'}")
    if overtime_ok is not None:
        pref_parts.append(f"接受加班：{'是' if overtime_ok else '否'}")
    if notes:
        pref_parts.append(f"求职备注/战略目标：{notes[:300]}")
    if pref_parts:
        sections.append("【求职偏好】\n" + "\n".join(pref_parts))

    greeting = str(profile.get("default_greeting") or "").strip()
    if greeting:
        sections.append(f"【默认招呼语】\n{greeting}")

    return "\n\n".join(sections) if sections else "（画像信息为空）"


def _format_conversation_context(conversation_messages: list[dict[str, Any]]) -> str:
    """将完整对话历史格式化为可读文本供 LLM 参考。"""
    if not conversation_messages:
        return ""
    lines: list[str] = []
    for msg in conversation_messages[-30:]:
        role = msg.get("role", "unknown")
        text = str(msg.get("text") or "").strip()
        time_str = str(msg.get("time") or "").strip()
        if not text:
            continue
        label = "HR" if role == "hr" else ("我" if role == "self" else "?")
        time_part = f" [{time_str}]" if time_str else ""
        lines.append(f"{label}{time_part}: {text}")
    return "\n".join(lines)


def generate_reply(
    *,
    hr_message: str,
    intent: str,
    profile: dict[str, Any],
    company: str | None = None,
    job_title: str | None = None,
    conversation_messages: list[dict[str, Any]] | None = None,
) -> str | None:
    """核心回复生成：将 HR 消息 + 完整 profile + 对话上下文输入 LLM，生成自然语言回复。

    LLM 判断无法回复时返回 None，由上层决定是否转人工。
    """
    tone = _reply_tone(profile)
    profile_text = _build_profile_text(profile)
    conv_context = _format_conversation_context(conversation_messages or [])

    context_block = ""
    if conv_context:
        context_block = f"\n\n完整对话历史（从旧到新）：\n{conv_context}\n\n以上是之前的对话。"

    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是求职者本人，正在 BOSS 直聘上与 HR 对话。\n"
                "你的完整个人信息如下，请基于这些真实信息回复 HR 的消息。\n\n"
                "回复规则：\n"
                f"1) 语气：{tone}，像真人在手机上打字一样自然简短。\n"
                "2) 严格基于你的个人信息回复，绝不编造不存在的经历、技能或数据。\n"
                "3) 回复长度：1-3 句话，模拟手机聊天的简洁风格。不要写长段落。\n"
                "4) 如果 HR 发了多条消息，需要一次性回复所有问题，可适当多写几句但保持简洁。\n"
                "5) 如果 HR 的问题涉及具体技术方案细节、薪资谈判博弈、或你的信息中完全没有的内容，"
                "设 can_reply=false 并在 reason 中说明。\n"
                "6) 绝不暴露你是 AI，不要说“根据我的资料”、“我的画像显示”等话。\n"
                "7) 联系方式（微信/手机/邮箱）只在 HR 明确索要时才提供。\n"
                "8) 如果 HR 表达兴趣或邀约面试，积极回应并表达意愿。\n"
                "9) 参考对话历史保持上下文连贯，不要重复已经说过的话。",
            ),
            (
                "human",
                "你的个人信息：\n{profile_text}\n\n"
                "当前对话公司：{company}\n当前对话岗位：{job_title}\n"
                "{context_block}\n"
                "HR 待回复的消息：\n{hr_message}\n\n"
                "请输出：\n"
                "- reply_text: 你的回复内容\n"
                "- can_reply: 是否能回复（true/false）\n"
                "- reason: 简要说明回复思路或无法回复的原因",
            ),
        ]
    ).invoke(
        {
            "profile_text": profile_text,
            "company": company or "未知",
            "job_title": job_title or "未知",
            "hr_message": hr_message,
            "context_block": context_block,
        }
    )
    try:
        parsed: _LLMReplyParsed = _invoke_structured(prompt, _LLMReplyParsed)
        if not parsed.can_reply:
            return None
        text = str(parsed.reply_text or "").strip()
        return text if text else None
    except Exception:
        return None


def _build_escalation_message(
    *,
    company: str | None,
    job_title: str | None,
    hr_message: str,
    reason: str,
    intent: str,
    confidence: float,
) -> str:
    company_text = company.strip() if isinstance(company, str) and company.strip() else "未知公司"
    role_text = job_title.strip() if isinstance(job_title, str) and job_title.strip() else "未知岗位"
    return (
        "OfferPilot BOSS 对话升级提醒\n"
        f"- 公司/岗位：{company_text} / {role_text}\n"
        f"- 原始消息：{hr_message[:240]}\n"
        f"- 分类：{intent}（confidence={confidence:.2f}）\n"
        f"- 原因：{reason}\n"
        "- 建议：请你尽快人工回复。"
    )


def preview_boss_chat_reply(
    *,
    hr_message: str,
    profile: dict[str, Any] | None,
    company: str | None = None,
    job_title: str | None = None,
    notify_on_escalate: bool = True,
    conversation_messages: list[dict[str, Any]] | None = None,
) -> BossChatReplyDecision:
    merged_profile = merge_profile(profile)
    intent, confidence, extracted_question = classify_hr_message(hr_message)
    threshold = _confidence_threshold()
    allowed_topics = _allowed_auto_reply_topics(merged_profile)
    escalate_topics = _escalate_topics(merged_profile)
    action, policy_topic, reason = _plan_hr_reply(
        hr_message=hr_message,
        intent=intent,
        confidence=confidence,
        extracted_question=extracted_question,
        profile=merged_profile,
    )
    reply_text: str | None = None
    matched_fields: list[str] = []
    needs_send_resume = False
    needs_user_intervention = False
    notification_sent = False
    notification_error: str | None = None

    # Hard guardrail: conservative overrides for high-risk/ambiguous intents.
    if intent == "reject":
        action = "ignore"
        reason = "HR 明确拒绝或结束沟通，本轮不自动回复。"
    elif intent in {"technical_question", "unknown"}:
        action = "notify_user"
        reason = "问题超出自动回复白名单，需人工介入。"
    elif intent == "express_interest" and action == "send_resume":
        action = "reply_from_profile"
        reason = "HR 表达兴趣，先回复确认意向，后续跟进发送简历。"

    # Hard guardrail: explicitly configured escalation topics always win.
    if policy_topic in escalate_topics:
        action = "notify_user"
        reason = f"问题命中升级话题 {policy_topic}，按策略转人工。"

    if action == "reply_from_profile":
        topic = policy_topic if policy_topic in _VALID_POLICY_TOPICS else _intent_to_policy_topic(intent)
        if topic not in allowed_topics:
            action = "notify_user"
            reason = f"问题类型 {topic} 不在自动回复白名单，转人工。"
        else:
            reply_text = generate_reply(
                hr_message=hr_message,
                intent=intent,
                profile=merged_profile,
                company=company,
                job_title=job_title,
                conversation_messages=conversation_messages,
            )
            if reply_text:
                matched_fields = ["llm_generated"]
            else:
                action = "notify_user"
                reason = "LLM 判断无法基于当前画像信息回复，转人工。"

    if action == "send_resume":
        reply_text = generate_reply(
            hr_message=hr_message,
            intent=intent,
            profile=merged_profile,
            company=company,
            job_title=job_title,
            conversation_messages=conversation_messages,
        )
        if not reply_text:
            reply_text = "好的，马上发送简历给您，辛苦查收。"
        needs_send_resume = True

    if action in {"send_resume", "reply_from_profile"} and confidence < threshold:
        action = "notify_user"
        reason = f"意图分类置信度过低（{confidence:.2f} < {threshold:.2f}），转人工更稳妥。"
        reply_text = None
        needs_send_resume = False

    if action == "notify_user":
        needs_user_intervention = True
        if notify_on_escalate:
            notify_message = _build_escalation_message(
                company=company,
                job_title=job_title,
                hr_message=hr_message,
                reason=reason,
                intent=intent,
                confidence=confidence,
            )
            notification_sent, notification_error = send_channel_notification(
                notify_message,
                payload={
                    "intent": intent,
                    "confidence": confidence,
                    "company": company,
                    "job_title": job_title,
                },
            )

    return BossChatReplyDecision(
        intent=intent,
        confidence=confidence,
        action=action,
        reason=reason,
        extracted_question=extracted_question,
        reply_text=reply_text,
        needs_send_resume=needs_send_resume,
        needs_user_intervention=needs_user_intervention,
        matched_profile_fields=matched_fields,
        notification_sent=notification_sent,
        notification_error=notification_error,
    )
