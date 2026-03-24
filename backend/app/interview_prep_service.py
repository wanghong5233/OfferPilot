from __future__ import annotations

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .company_intel_service import generate_company_intel
from .schemas import CompanyIntelResponse, InterviewPrepQuestion, InterviewPrepResponse
from .workflow import _invoke_structured


class _InterviewQuestionParsed(BaseModel):
    question: str
    intent: str
    difficulty: str = Field(default="medium")
    related_skill: str | None = None
    answer_tips: list[str] = Field(default_factory=list)


class _InterviewPrepParsed(BaseModel):
    summary: str
    likely_focus: list[str] = Field(default_factory=list)
    key_storylines: list[str] = Field(default_factory=list)
    questions: list[_InterviewQuestionParsed] = Field(default_factory=list)


def _normalize_difficulty(value: str) -> str:
    text = value.strip().lower()
    if text in {"easy", "medium", "hard"}:
        return text
    return "medium"


def _ensure_questions(
    company: str,
    role_title: str,
    question_count: int,
    skills: list[str],
    existing: list[InterviewPrepQuestion],
) -> list[InterviewPrepQuestion]:
    questions = existing[:question_count]
    fallback_templates = [
        ("请介绍一个你做过的 Agent 项目，并说明你如何做状态管理。", "验证端到端项目经验", "LangGraph"),
        ("你如何设计 RAG 检索链路，避免召回噪声？", "验证检索增强与工程取舍能力", "RAG"),
        ("如果线上调用 LLM 超时或输出不稳定，你会如何降级？", "验证稳定性与容灾意识", "LLM reliability"),
        ("你如何在自动化流程中设计 Human-in-the-Loop？", "验证安全边界与审批机制", "HITL"),
        ("讲一下你在这个岗位最能贡献的 2-3 点。", "验证岗位匹配表达能力", "communication"),
    ]
    idx = 0
    while len(questions) < question_count:
        q, intent, skill = fallback_templates[idx % len(fallback_templates)]
        questions.append(
            InterviewPrepQuestion(
                question=q,
                intent=intent,
                difficulty="medium",
                related_skill=skill,
                answer_tips=[
                    f"先给结论，再讲你在 {company} / {role_title} 语境下可复用的做法",
                    "尽量给出量化结果和失败复盘",
                ],
            )
        )
        idx += 1
    return questions


def _heuristic_prep(
    *,
    company: str,
    role_title: str,
    jd_text: str,
    question_count: int,
    company_intel: CompanyIntelResponse | None,
) -> InterviewPrepResponse:
    jd = jd_text.lower()
    skill_candidates = [
        ("Python", "python"),
        ("LangGraph", "langgraph"),
        ("RAG", "rag"),
        ("MCP", "mcp"),
        ("Playwright", "playwright"),
        ("FastAPI", "fastapi"),
    ]
    skills = [label for label, key in skill_candidates if key in jd]
    if not skills and company_intel:
        skills = company_intel.tech_stack[:4]
    if not skills:
        skills = ["Agent engineering", "LLM application"]

    questions: list[InterviewPrepQuestion] = []
    for skill in skills[:question_count]:
        questions.append(
            InterviewPrepQuestion(
                question=f"请结合实际项目说明你如何在 {skill} 上落地，并给出可量化结果？",
                intent=f"验证 {skill} 的工程实战能力",
                difficulty="medium",
                related_skill=skill,
                answer_tips=[
                    "按照 背景-目标-方案-结果-复盘 结构回答",
                    "说明一个做过的权衡和 trade-off",
                ],
            )
        )

    questions = _ensure_questions(company, role_title, question_count, skills, questions)
    likely_focus = skills[:5]
    key_storylines = [
        "突出 OpenClaw + LangGraph 的分层职责与可恢复状态机能力",
        "强调你如何保证自动化流程可控（审批、令牌、防重放）",
        "用 1 个失败案例说明你如何定位问题并修复",
    ]
    return InterviewPrepResponse(
        company=company,
        role_title=role_title,
        summary=f"{company} 的 {role_title} 面试更可能围绕 Agent 工程落地与稳定性展开，建议优先准备项目深挖题。",
        likely_focus=likely_focus,
        key_storylines=key_storylines,
        questions=questions,
        company_intel=company_intel,
    )


def generate_interview_prep(
    *,
    company: str,
    role_title: str,
    jd_text: str,
    question_count: int = 8,
    use_company_intel: bool = True,
) -> InterviewPrepResponse:
    safe_company = company.strip() or "Unknown Company"
    safe_role = role_title.strip() or "AI Agent Intern"
    safe_jd = jd_text.strip()
    safe_count = max(3, min(question_count, 20))

    intel: CompanyIntelResponse | None = None
    if use_company_intel:
        try:
            intel = generate_company_intel(
                company=safe_company,
                role_title=safe_role,
                jd_text=safe_jd,
                focus_keywords=["技术栈", "面试流程", "融资阶段"],
                max_results=6,
                include_search=True,
            )
        except Exception:
            intel = None

    intel_blob = (
        (
            f"summary: {intel.summary}\n"
            f"business_direction: {', '.join(intel.business_direction)}\n"
            f"tech_stack: {', '.join(intel.tech_stack)}\n"
            f"interview_style: {', '.join(intel.interview_style)}\n"
            f"risks: {', '.join(intel.risks)}"
        )
        if intel
        else "(no company intel)"
    )
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是资深面试教练。请产出可实战的面试准备内容，避免空话。",
            ),
            (
                "human",
                "公司: {company}\n"
                "岗位: {role_title}\n"
                "JD:\n{jd_text}\n\n"
                "公司情报:\n{intel_blob}\n\n"
                "请输出：\n"
                "- summary: 2-4句\n"
                "- likely_focus: 3-8项（面试官重点）\n"
                "- key_storylines: 3-6项（候选人叙事主线）\n"
                "- questions: {question_count} 题，每题包含 question / intent / difficulty / related_skill / answer_tips(2-4条)\n",
            ),
        ]
    ).invoke(
        {
            "company": safe_company,
            "role_title": safe_role,
            "jd_text": safe_jd,
            "intel_blob": intel_blob,
            "question_count": safe_count,
        }
    )

    try:
        parsed: _InterviewPrepParsed = _invoke_structured(
            prompt,
            _InterviewPrepParsed,
            route="interview_prep",
        )
        questions = [
            InterviewPrepQuestion(
                question=item.question.strip(),
                intent=item.intent.strip(),
                difficulty=_normalize_difficulty(item.difficulty),  # type: ignore[arg-type]
                related_skill=(item.related_skill.strip() if item.related_skill else None),
                answer_tips=[tip.strip() for tip in item.answer_tips if str(tip).strip()],
            )
            for item in parsed.questions
            if item.question.strip() and item.intent.strip()
        ]
        questions = _ensure_questions(
            safe_company,
            safe_role,
            safe_count,
            intel.tech_stack if intel else [],
            questions,
        )
        return InterviewPrepResponse(
            company=safe_company,
            role_title=safe_role,
            summary=parsed.summary.strip(),
            likely_focus=[item.strip() for item in parsed.likely_focus if item.strip()][:8],
            key_storylines=[item.strip() for item in parsed.key_storylines if item.strip()][:6],
            questions=questions,
            company_intel=intel,
        )
    except Exception:
        return _heuristic_prep(
            company=safe_company,
            role_title=safe_role,
            jd_text=safe_jd,
            question_count=safe_count,
            company_intel=intel,
        )
