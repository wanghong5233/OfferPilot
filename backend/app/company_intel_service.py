from __future__ import annotations

import re
from typing import Iterable

from langchain_core.prompts import ChatPromptTemplate
from pydantic import BaseModel, Field

from .schemas import CompanyIntelResponse, CompanyIntelSource
from .web_search_service import SearchResult, search_web
from .workflow import _invoke_structured


class _CompanyIntelParsed(BaseModel):
    summary: str = Field(..., description="2-4句结论摘要")
    business_direction: list[str] = Field(default_factory=list)
    tech_stack: list[str] = Field(default_factory=list)
    funding_stage: str | None = None
    team_size_stage: str | None = None
    interview_style: list[str] = Field(default_factory=list)
    risks: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)
    confidence: float = Field(default=0.7, ge=0, le=1)


def _dedupe_keep_order(items: Iterable[str], *, max_items: int = 8) -> list[str]:
    seen: set[str] = set()
    output: list[str] = []
    for raw in items:
        value = str(raw).strip()
        if not value:
            continue
        key = value.lower()
        if key in seen:
            continue
        seen.add(key)
        output.append(value)
        if len(output) >= max_items:
            break
    return output


def _build_queries(company: str, role_title: str | None, focus_keywords: list[str]) -> list[str]:
    role = role_title.strip() if isinstance(role_title, str) and role_title.strip() else "AI Agent 实习"
    seeds = [
        f"{company} {role} 技术栈",
        f"{company} 融资 团队 业务",
        f"{company} 面试 题目 流程",
    ]
    for kw in focus_keywords:
        keyword = str(kw).strip()
        if keyword:
            seeds.append(f"{company} {keyword}")
    return _dedupe_keep_order(seeds, max_items=6)


def _collect_sources(
    company: str,
    role_title: str | None,
    focus_keywords: list[str],
    max_results: int,
    include_search: bool,
) -> list[SearchResult]:
    if not include_search:
        return []
    queries = _build_queries(company, role_title, focus_keywords)
    per_query = max(2, min(4, max_results))
    merged: list[SearchResult] = []
    seen_urls: set[str] = set()
    for query in queries:
        try:
            results = search_web(query, max_results=per_query)
        except Exception:
            continue
        for item in results:
            url_key = item.url.strip()
            if not url_key or url_key in seen_urls:
                continue
            seen_urls.add(url_key)
            merged.append(item)
            if len(merged) >= max_results:
                return merged
    return merged


def _extract_funding_stage(text: str) -> str | None:
    patterns = [
        r"(Pre-A|A轮|A\+轮|B轮|B\+轮|C轮|D轮|战略融资|天使轮)",
        r"(上市|IPO|并购)",
    ]
    for pattern in patterns:
        match = re.search(pattern, text, flags=re.IGNORECASE)
        if match:
            return match.group(1)
    return None


def _heuristic_intel(
    *,
    company: str,
    role_title: str | None,
    jd_text: str | None,
    sources: list[SearchResult],
) -> CompanyIntelResponse:
    jd = str(jd_text or "")
    corpus = "\n".join([jd] + [f"{item.title}\n{item.snippet}" for item in sources]).lower()

    stack_candidates = [
        ("Python", "python"),
        ("LangGraph", "langgraph"),
        ("LangChain", "langchain"),
        ("RAG", "rag"),
        ("MCP", "mcp"),
        ("FastAPI", "fastapi"),
        ("Playwright", "playwright"),
        ("Docker", "docker"),
        ("PostgreSQL", "postgres"),
        ("ChromaDB", "chroma"),
    ]
    tech_stack = [label for label, token in stack_candidates if token in corpus]
    if not tech_stack:
        tech_stack = ["Python", "LLM Application"]

    direction_candidates = [
        ("Agent平台", ["agent", "智能体", "workflow"]),
        ("企业效率工具", ["效率", "办公", "copilot", "助手"]),
        ("招聘/人力场景", ["招聘", "hr", "简历", "面试"]),
        ("ToB SaaS", ["企业", "saa", "客户"]),
    ]
    business_direction: list[str] = []
    for label, markers in direction_candidates:
        if any(marker in corpus for marker in markers):
            business_direction.append(label)
    if not business_direction:
        business_direction = ["AI 应用探索阶段"]

    interview_style = []
    if any(key in corpus for key in ["机试", "coding", "算法"]):
        interview_style.append("算法/编程基础")
    if any(key in corpus for key in ["项目", "实战", "落地"]):
        interview_style.append("项目深挖与落地能力")
    if any(key in corpus for key in ["llm", "agent", "rag", "langgraph"]):
        interview_style.append("LLM/Agent 技术细节")
    if not interview_style:
        interview_style = ["项目经历与岗位匹配度"]

    funding_stage = _extract_funding_stage(corpus)
    team_size_stage = "早期团队（需一人多面）" if funding_stage in {"天使轮", "Pre-A", "A轮", None} else "成长型团队"

    summary = (
        f"{company} 当前更看重可快速落地的 Agent 工程能力。"
        f"建议围绕 {', '.join(tech_stack[:4])} 展示可演示的端到端项目成果。"
    )
    risks = [
        "公开信息有限，部分结论基于招聘文案推断",
        "初创团队需求变化快，需持续更新情报",
    ]
    suggestions = [
        "简历突出可复现 Demo、稳定性与可维护性",
        "准备 2-3 个与岗位直接相关的项目追问回答",
        "面试前一天复盘最近招聘描述和技术栈变动",
    ]

    return CompanyIntelResponse(
        company=company,
        role_title=role_title,
        summary=summary,
        business_direction=_dedupe_keep_order(business_direction),
        tech_stack=_dedupe_keep_order(tech_stack),
        funding_stage=funding_stage,
        team_size_stage=team_size_stage,
        interview_style=_dedupe_keep_order(interview_style),
        risks=_dedupe_keep_order(risks),
        suggestions=_dedupe_keep_order(suggestions),
        confidence=0.58 if sources else 0.45,
        sources=[
            CompanyIntelSource(title=item.title, url=item.url, snippet=item.snippet or None)
            for item in sources
        ],
    )


def generate_company_intel(
    *,
    company: str,
    role_title: str | None = None,
    jd_text: str | None = None,
    focus_keywords: list[str] | None = None,
    max_results: int = 6,
    include_search: bool = True,
) -> CompanyIntelResponse:
    safe_company = company.strip()
    if not safe_company:
        raise RuntimeError("company is required for company intel")
    keywords = [str(item).strip() for item in (focus_keywords or []) if str(item).strip()]
    sources = _collect_sources(
        safe_company,
        role_title,
        keywords,
        max_results=max(1, min(max_results, 12)),
        include_search=include_search,
    )

    source_lines = []
    for idx, item in enumerate(sources, start=1):
        source_lines.append(
            f"[{idx}] 标题: {item.title}\nURL: {item.url}\n摘要: {item.snippet[:350]}"
        )
    source_blob = "\n\n".join(source_lines) if source_lines else "(no external sources)"
    prompt = ChatPromptTemplate.from_messages(
        [
            (
                "system",
                "你是求职情报分析助手。请根据输入生成结构化公司情报，用于实习面试准备。"
                "输出务必务实、可执行，避免夸张。",
            ),
            (
                "human",
                "公司: {company}\n"
                "岗位: {role_title}\n"
                "JD 文本:\n{jd_text}\n\n"
                "关注关键词: {keywords}\n\n"
                "外部检索片段:\n{source_blob}\n\n"
                "请输出:\n"
                "- summary: 2-4句\n"
                "- business_direction: 2-5项\n"
                "- tech_stack: 4-8项\n"
                "- funding_stage/team_size_stage（可空）\n"
                "- interview_style: 2-5项\n"
                "- risks: 2-4项\n"
                "- suggestions: 3-6项（必须可执行）\n"
                "- confidence: 0~1\n",
            ),
        ]
    ).invoke(
        {
            "company": safe_company,
            "role_title": role_title or "未知岗位",
            "jd_text": jd_text or "",
            "keywords": ", ".join(keywords) if keywords else "-",
            "source_blob": source_blob,
        }
    )

    try:
        parsed: _CompanyIntelParsed = _invoke_structured(prompt, _CompanyIntelParsed)
        return CompanyIntelResponse(
            company=safe_company,
            role_title=role_title,
            summary=parsed.summary.strip(),
            business_direction=_dedupe_keep_order(parsed.business_direction),
            tech_stack=_dedupe_keep_order(parsed.tech_stack),
            funding_stage=(parsed.funding_stage.strip() if parsed.funding_stage else None),
            team_size_stage=(parsed.team_size_stage.strip() if parsed.team_size_stage else None),
            interview_style=_dedupe_keep_order(parsed.interview_style),
            risks=_dedupe_keep_order(parsed.risks),
            suggestions=_dedupe_keep_order(parsed.suggestions),
            confidence=max(0.0, min(float(parsed.confidence), 1.0)),
            sources=[
                CompanyIntelSource(title=item.title, url=item.url, snippet=item.snippet or None)
                for item in sources
            ],
        )
    except Exception:
        return _heuristic_intel(
            company=safe_company,
            role_title=role_title,
            jd_text=jd_text,
            sources=sources,
        )
