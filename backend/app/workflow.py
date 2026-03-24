from __future__ import annotations

import json
import logging
import os
from pathlib import Path
from typing import Any

from langchain_core.messages import AIMessage
from langchain_core.prompts import ChatPromptTemplate
from langchain_openai import ChatOpenAI

from .schemas import GreetDecision, JDAnalyzeResponse, JDMatchOutput

logger = logging.getLogger(__name__)


_MODEL_ROUTE_DEFAULTS: dict[str, tuple[str, str]] = {
    # 全局默认：优先成本友好的 plus，失败再回退 max。
    "default": ("qwen-plus", "qwen3-max"),
    # JD 匹配与主动打招呼属于核心决策链路，保留 max 兜底。
    "jd_analysis": ("qwen-plus", "qwen3-max"),
    "greet_decision": ("qwen3-max", "qwen-plus"),
    # 对话类默认保持稳态；若要更省钱可在 .env 单独将其切到 turbo。
    "chat_classify": ("qwen-plus", "qwen3-max"),
    "chat_plan": ("qwen-plus", "qwen3-max"),
    "proactive_contact": ("qwen-plus", "qwen3-max"),
    # 来源匹配会影响自动跟进，优先稳定性。
    "source_fit": ("qwen-plus", "qwen3-max"),
    # 生成回复面向真实 HR 对话，优先质量。
    "chat_reply": ("qwen-plus", "qwen3-max"),
    # 其他业务模块。
    "email_classify": ("qwen-plus", "qwen3-max"),
    "material_draft": ("qwen-plus", "qwen3-max"),
    "company_intel": ("qwen-plus", "qwen3-max"),
    "interview_prep": ("qwen-plus", "qwen3-max"),
}


def _route_env_prefix(route: str) -> str:
    key = "".join(ch if ch.isalnum() else "_" for ch in str(route or "default").upper())
    return f"MODEL_ROUTE_{key}"


def _route_default_pair(route: str) -> tuple[str, str]:
    return _MODEL_ROUTE_DEFAULTS.get(route, _MODEL_ROUTE_DEFAULTS["default"])


def _dedupe_models(models: list[str]) -> list[str]:
    return list(dict.fromkeys([m.strip() for m in models if isinstance(m, str) and m.strip()]))


def _load_api_config() -> tuple[str, str]:
    """
    Resolve model API key and base URL.

    Priority:
    1) Explicit env vars for backend runtime
    2) Local OpenClaw config (dev convenience only)
    """
    dashscope_key = os.getenv("DASHSCOPE_API_KEY") or os.getenv("QWEN_API_KEY")
    if dashscope_key:
        return (
            os.getenv("OPENAI_COMPAT_BASE_URL", "https://dashscope.aliyuncs.com/compatible-mode/v1"),
            dashscope_key,
        )

    deepseek_key = os.getenv("DEEPSEEK_API_KEY")
    if deepseek_key:
        return (os.getenv("OPENAI_COMPAT_BASE_URL", "https://api.deepseek.com/v1"), deepseek_key)

    # Dev-only fallback: reuse key from OpenClaw local config if exists.
    oc_config = Path.home() / ".openclaw" / "openclaw.json"
    if oc_config.exists():
        try:
            data = json.loads(oc_config.read_text(encoding="utf-8"))
            providers = data.get("models", {}).get("providers", {})
            for provider in ("qwen", "deepseek"):
                cfg = providers.get(provider, {})
                key = cfg.get("apiKey")
                base_url = cfg.get("baseUrl")
                if isinstance(key, str) and key.startswith("sk-") and isinstance(base_url, str):
                    return (base_url, key)
        except Exception as exc:  # pragma: no cover - dev fallback only
            logger.warning("Failed to read OpenClaw config fallback: %s", exc)

    raise RuntimeError(
        "No model API key found. Set DASHSCOPE_API_KEY (recommended) or DEEPSEEK_API_KEY "
        "before starting backend."
    )


def _candidate_models(route: str = "default") -> list[str]:
    route = str(route or "default").strip() or "default"
    route_primary_default, route_fallback_default = _route_default_pair(route)

    global_primary = os.getenv("MODEL_PRIMARY", _MODEL_ROUTE_DEFAULTS["default"][0])
    global_fallback = os.getenv("MODEL_FALLBACK", _MODEL_ROUTE_DEFAULTS["default"][1])
    prefix = _route_env_prefix(route)

    primary = os.getenv(f"{prefix}_PRIMARY", route_primary_default or global_primary)
    fallback = os.getenv(f"{prefix}_FALLBACK", route_fallback_default or global_fallback)

    # 最终保证：route 配置 -> 全局配置 -> 内置默认
    return _dedupe_models(
        [
            primary,
            fallback,
            global_primary,
            global_fallback,
            _MODEL_ROUTE_DEFAULTS["default"][0],
            _MODEL_ROUTE_DEFAULTS["default"][1],
        ]
    )


def _build_llm(model: str) -> ChatOpenAI:
    base_url, api_key = _load_api_config()
    return ChatOpenAI(
        model=model,
        base_url=base_url,
        api_key=api_key,
        temperature=0.1,
        timeout=60,
        max_retries=1,
    )


def _invoke_structured(prompt_value: Any, schema: type[Any], *, route: str = "default") -> Any:
    errors: list[str] = []
    for model in _candidate_models(route):
        try:
            llm = _build_llm(model).with_structured_output(schema)
            return llm.invoke(prompt_value)
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            continue
    raise RuntimeError(
        f"All models failed for structured output (route={route}): " + " | ".join(errors)
    )


def _coerce_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts: list[str] = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(str(item.get("text", "")))
            else:
                parts.append(str(item))
        return "\n".join(parts).strip()
    return str(content)


def _invoke_text(prompt_value: Any, *, route: str = "default") -> str:
    errors: list[str] = []
    for model in _candidate_models(route):
        try:
            msg = _build_llm(model).invoke(prompt_value)
            if isinstance(msg, AIMessage):
                return _coerce_text(msg.content)
            return _coerce_text(msg)
        except Exception as exc:
            errors.append(f"{model}: {exc}")
            continue
    raise RuntimeError(
        f"All models failed for text output (route={route}): " + " | ".join(errors)
    )


def _load_candidate_context() -> str:
    """Load full resume text + user profile from DB, combine into candidate context."""
    from .storage import get_resume_source, get_user_profile

    parts: list[str] = []

    profile_row = get_user_profile("default")
    if profile_row:
        p = profile_row.get("profile_json") or profile_row.get("profile") or {}
        if isinstance(p, str):
            try:
                p = json.loads(p)
            except Exception:
                p = {}
        personal = p.get("personal", {})
        skills_data = p.get("skills", {})
        pref = p.get("job_preference", {})

        profile_lines = ["【求职画像】"]

        if pref.get("notes"):
            profile_lines.append(f"⚠️ 求职核心目标：{pref['notes']}")

        if personal.get("name"):
            profile_lines.append(f"姓名：{personal['name']}")
        if personal.get("education"):
            profile_lines.append(f"学历：{personal['education']}")
        if personal.get("major"):
            profile_lines.append(f"专业：{personal['major']}")
        if personal.get("current_status"):
            profile_lines.append(f"当前状态：{personal['current_status']}")
        if skills_data.get("tech_stack"):
            profile_lines.append(f"核心技术栈：{', '.join(skills_data['tech_stack'])}")
        if skills_data.get("experience_summary"):
            profile_lines.append(f"项目经验：{skills_data['experience_summary']}")
        if skills_data.get("english_level"):
            profile_lines.append(f"英语水平：{skills_data['english_level']}")
        if pref.get("target_positions"):
            profile_lines.append(f"目标岗位：{', '.join(pref['target_positions'])}")
        if pref.get("work_cities"):
            profile_lines.append(f"期望城市（非硬性要求）：{', '.join(pref['work_cities'])}")
        if pref.get("expected_daily_salary"):
            profile_lines.append(f"期望薪资：{pref['expected_daily_salary']}")
        if pref.get("internship_duration"):
            profile_lines.append(f"实习时长：{pref['internship_duration']}")
        parts.append("\n".join(profile_lines))

    source_id = os.getenv("RESUME_SOURCE_ID", "resume_v1")
    resume_row = get_resume_source(source_id)
    if resume_row and resume_row.get("resume_text"):
        text = resume_row["resume_text"].strip()
        if len(text) > 6000:
            text = text[:6000] + "\n...(截断)"
        parts.append(f"【简历全文】\n{text}")

    if not parts:
        return "候选人信息暂未配置，请先上传简历并填写求职画像。"
    return "\n\n".join(parts)


_JD_MATCH_PROMPT = ChatPromptTemplate.from_messages(
    [
        (
            "system",
            "你是一位资深求职匹配评估专家。你的任务是根据候选人的完整信息（简历+求职画像+求职核心目标）和目标岗位的完整 JD，做出精准的匹配判断。\n\n"
            "## ⚠️ 最重要：理解候选人的求职核心目标\n"
            "候选人信息中标注了「求职核心目标」，这是最高优先级的判断依据。\n"
            "例如：如果候选人的目标是'为秋招积累垂直领域经验'，那么该岗位是否能提供目标领域的实战经验就是最关键的维度——\n"
            "即使技术栈不完全匹配，只要业务方向对口、能积累目标经验，就应该积极投递。\n\n"
            "## 评分标准\n"
            "- 85-100：业务方向高度契合候选人目标，技术栈吻合，能提供高价值经验\n"
            "- 70-84：方向对口，核心技术匹配，可以积累有效经验\n"
            "- 50-69：有部分相关性但与候选人核心目标存在偏差\n"
            "- 30-49：方向有一定重叠但价值有限\n"
            "- 0-29：基本不匹配（方向完全不同，或不可接受的硬性冲突）\n\n"
            "## 判断维度（按权重排序）\n"
            "1. **业务方向与目标契合度**（35%）：该岗位的工作内容是否能帮助候选人达成求职核心目标？能否积累目标领域的垂直经验？\n"
            "2. **技术栈匹配**（30%）：候选人掌握的技术 vs JD 要求的技术\n"
            "3. **项目经验相关性**（20%）：候选人做过的项目 vs 岗位工作内容\n"
            "4. **基础条件**（15%）：薪资能否覆盖生存成本、学历专业是否符合等硬性门槛\n\n"
            "## 关于地点\n"
            "除非候选人明确标注地点为硬性要求，否则**地点不作为扣分项**。\n"
            "很多实习生愿意为好机会搬迁，地点只在提示信息中客观提及即可。\n\n"
            "## 关键原则\n"
            "- should_apply 为 true 的条件：match_score >= 60 且业务方向与候选人目标基本一致\n"
            "- 对于方向对口的机会要积极，不要因为地点或非核心技能差距而过度保守\n"
            "- strengths 和 gaps 要具体，不要泛泛而谈\n"
            "- one_line_reason 要简洁有力，适合通知推送\n"
            "- gap_analysis 侧重分析该岗位对候选人目标的价值",
        ),
        (
            "human",
            "## 候选人完整信息\n{candidate_ctx}\n\n"
            "## 目标岗位 JD\n{jd_text}\n\n"
            "请一次性完成：岗位信息提取 + 匹配评分 + 差距分析。",
        ),
    ]
)


def run_jd_analysis(jd_text: str) -> JDAnalyzeResponse:
    candidate_ctx = _load_candidate_context()
    prompt_value = _JD_MATCH_PROMPT.invoke(
        {"candidate_ctx": candidate_ctx, "jd_text": jd_text[:4000]}
    )
    try:
        result: JDMatchOutput = _invoke_structured(
            prompt_value,
            JDMatchOutput,
            route="jd_analysis",
        )
        score = max(0.0, min(100.0, float(result.match_score)))
        return JDAnalyzeResponse(
            title=result.title.strip() or "未知岗位",
            company=result.company.strip() or "Unknown Company",
            skills=[s.strip() for s in result.skills if s and s.strip()] or ["General"],
            match_score=round(score, 1),
            should_apply=result.should_apply,
            strengths=[s for s in (result.strengths or []) if isinstance(s, str) and s.strip()],
            gaps=[g for g in (getattr(result, "gaps", []) or []) if isinstance(g, str) and g.strip()],
            gap_analysis=(
                str(getattr(result, "gap_analysis", "") or "").strip()
                or str(getattr(result, "one_line_reason", "") or "").strip()
                or "与候选人画像部分匹配，建议结合业务方向进一步确认。"
            ),
            one_line_reason=str(getattr(result, "one_line_reason", "") or "").strip(),
            resume_evidence=[],
        )
    except Exception as exc:
        logger.exception("JD match failed: %s", exc)
        return _heuristic_fallback(jd_text)


def _heuristic_fallback(jd_text: str) -> JDAnalyzeResponse:
    text = jd_text.lower()
    kw_map = {
        "python": "Python", "langgraph": "LangGraph", "langchain": "LangChain",
        "rag": "RAG", "mcp": "MCP", "playwright": "Playwright", "fastapi": "FastAPI",
        "react": "React", "typescript": "TypeScript", "docker": "Docker",
        "kubernetes": "Kubernetes", "llm": "LLM", "agent": "Agent",
        "transformer": "Transformer", "pytorch": "PyTorch", "fine-tun": "Fine-tuning",
    }
    skills = [v for k, v in kw_map.items() if k in text] or ["General"]
    return JDAnalyzeResponse(
        title="(解析失败) 岗位",
        company="未知公司",
        skills=skills,
        match_score=50.0,
        should_apply=False,
        gap_analysis="LLM 分析失败，使用关键词启发式评分，结果仅供参考。",
        one_line_reason="LLM 调用失败，无法判断",
        resume_evidence=[],
    )


def _build_greet_decision_prompt() -> ChatPromptTemplate:
    """从 skills/jd-filter/SKILL.md 动态构建 LLM 二元判断 prompt。"""
    from .skill_loader import get_accept_rules, get_intent, get_principles, get_reject_rules

    reject_rules = get_reject_rules()
    accept_rules = get_accept_rules()
    principles = get_principles()
    intent = get_intent()

    reject_block = "\n".join(f"   - {r}" for r in reject_rules) if reject_rules else (
        "   - 岗位核心工作是模型预训练/后训练/RLHF/SFT/蒸馏，而非应用开发\n"
        "   - 岗位核心工作是传统算法（推荐/搜索/CV/NLP基础研究），而非LLM应用\n"
        "   - 岗位核心职责偏产品（如产品经理/产品实习/需求分析/PRD输出），而非研发编码落地\n"
        "   - 岗位核心工作是测试/QA/运维，而非开发\n"
        "   - 岗位要求博士学历（候选人硕士）"
    )
    accept_block = "\n".join(f"   - {r}" for r in accept_rules) if accept_rules else (
        "   - 岗位涉及 Agent/RAG/LLM应用/工作流/Prompt工程/对话系统/AI应用落地\n"
        "   - 岗位涉及大模型应用层开发，即使标题含\"算法\"但JD实际是应用开发"
    )
    principle_block = "\n".join(f"- {p}" for p in principles) if principles else (
        "- 宁缺毋滥：不确定时拒绝\n"
        "- 地点不作为拒绝理由\n"
        "- 重点看JD中的工作职责和岗位描述的具体工作内容，不要被标题迷惑\n"
        "- 候选人的求职核心目标是最高优先级"
    )

    system_msg = (
        "你是求职方向匹配专家。你需要判断这个岗位是否值得主动打招呼。\n\n"
        "## 候选人信息\n{candidate_ctx}\n\n"
    )
    if intent:
        system_msg += f"## 求职意图\n{intent}\n\n"
    system_msg += (
        "## 判断规则（严格遵守）\n"
        "1. 候选人的「求职核心目标」是最高优先级。如果岗位方向与目标不一致，直接拒绝。\n"
        "2. 重点看JD中的【工作职责】和【岗位描述】的具体工作内容——不要被标题迷惑。\n"
        f"3. 以下情况必须拒绝（should_greet=false）：\n{reject_block}\n"
        f"4. 以下情况应该接受（should_greet=true）：\n{accept_block}\n"
        f"5. 判断原则：\n{principle_block}"
    )

    return ChatPromptTemplate.from_messages([
        ("system", system_msg),
        ("human", "## 岗位信息\n{jd_text}\n\n请判断是否值得打招呼。"),
    ])


def run_greet_decision(jd_text: str) -> GreetDecision:
    """基于完整JD文本做二元判断：是否值得打招呼。prompt 从 Skill 配置动态组装。"""
    candidate_ctx = _load_candidate_context()
    prompt_template = _build_greet_decision_prompt()
    prompt_value = prompt_template.invoke(
        {"candidate_ctx": candidate_ctx, "jd_text": jd_text[:4000]}
    )
    try:
        return _invoke_structured(
            prompt_value,
            GreetDecision,
            route="greet_decision",
        )
    except Exception as exc:
        logger.warning("greet_decision LLM failed: %s", exc)
        return GreetDecision(should_greet=False, reason=f"LLM调用失败: {exc}", confidence="low")
