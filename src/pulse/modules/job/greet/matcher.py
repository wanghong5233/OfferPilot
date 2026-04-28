"""Score how well a scanned job fits the user's current preferences.

Pure policy component — owns no IO other than the :class:`LLMRouter`. Takes a
normalized scan item (whatever ``GreetService._normalize_scan_item`` produces)
+ the latest :class:`JobMemorySnapshot`, returns a structured
:class:`MatchResult`.

Design:

  * **LLM 主路径** (route=``job_match``): 把 snapshot 渲染的 markdown 片段
    拼进 system prompt, JD 拼进 user prompt, 通过 ``invoke_json`` 拿
    ``{score, verdict, matched_signals, concerns, reason}``。
  * **无语义兜底**: LLM 不可用 / 返回非 JSON / 字段缺失时返回 ``skip``。
    自动打招呼的唯一判断依据是 LLM + JobMemory, 不用关键词启发式替代。

verdict 取值与下游行为:

    ``good``  → 强烈推荐打招呼; service 排在最前
    ``okay``  → 可以打招呼, 但提示用户确认
    ``poor``  → 不推荐, 低于 threshold 时直接丢弃
    ``skip``  → 命中用户黑名单或硬性偏好冲突, 必须丢弃

matcher 是否发射 stage 事件由调用方 (service 编排) 决定, matcher 本身不写
审计日志 — 只做 "输入→输出" 的纯函数, 方便单测。

见 ``docs/Pulse-DomainMemory与Tool模式.md`` §5.1 R2 / §5.2 性能边界。
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from typing import Any

from pulse.core.llm.router import LLMRouter
from pulse.core.tokenizer import token_preview

from ..memory import JobMemorySnapshot

_VERDICTS: frozenset[str] = frozenset({"good", "okay", "poor", "skip"})


@dataclass(frozen=True, slots=True)
class MatchResult:
    """Structured fit assessment for a single JD against user preferences."""

    score: float
    verdict: str  # one of _VERDICTS
    matched_signals: list[str] = field(default_factory=list)
    concerns: list[str] = field(default_factory=list)
    reason: str = ""

    def to_dict(self) -> dict[str, Any]:
        return {
            "score": self.score,
            "verdict": self.verdict,
            "matched_signals": list(self.matched_signals),
            "concerns": list(self.concerns),
            "reason": self.reason,
        }


class JobSnapshotMatcher:
    """LLM-backed fit scorer; invalid LLM output skips the JD."""

    def __init__(self, llm_router: LLMRouter) -> None:
        self._llm = llm_router

    # ──────────────────────────────────────────────────────── public

    def match(
        self,
        *,
        job: dict[str, Any],
        snapshot: JobMemorySnapshot | None,
        keyword: str = "",
    ) -> MatchResult:
        """Score a single JD. Empty/invalid jobs get verdict='skip'."""
        title = str(job.get("title") or "").strip()
        if not title and not str(job.get("snippet") or "").strip():
            return MatchResult(
                score=0.0,
                verdict="skip",
                reason="empty job payload",
            )

        llm_result = self._match_with_llm(job=job, snapshot=snapshot, keyword=keyword)
        if llm_result is not None:
            return llm_result
        return MatchResult(
            score=0.0,
            verdict="skip",
            concerns=["LLM matcher unavailable or returned invalid JSON"],
            reason="llm_required_no_heuristic_autosend",
        )

    # ──────────────────────────────────────────────────────── LLM path

    def _match_with_llm(
        self,
        *,
        job: dict[str, Any],
        snapshot: JobMemorySnapshot | None,
        keyword: str,
    ) -> MatchResult | None:
        snapshot_md = snapshot.to_prompt_section() if snapshot is not None else "(no preferences set)"
        job_md = self._render_job(job, keyword=keyword)

        system_prompt = (
            "You are a job-fit scorer for an AI career assistant. "
            "Given the user's current preferences (as markdown) and a job posting, "
            "score how well the job matches.\n\n"
            "## Verdict policy (READ CAREFULLY — this is the #1 source of misjudgments)\n"
            "- **skip** ONLY when the JD text contains CLEAR, EXPLICIT EVIDENCE that a "
            "  hard constraint is violated. Examples that DO warrant skip:\n"
            "    * JD says 'base 北京', user prefers ['杭州','上海']  → skip (city mismatch).\n"
            "    * JD says '月薪 5-8K', user's salary_floor_monthly is 10K  → skip (ceiling < floor).\n"
            "    * JD explicitly targets '3 年以上工作经验 / 全职', user wants 'intern'  → skip.\n"
            "    * Company name appears on user's avoid_company list.\n"
            "- **DO NOT skip** when a field is merely absent / unknown ('salary: (not "
            "  provided)', snippet doesn't mention city). Missing != violating. In that case "
            "  use 'okay' (if other signals match) or 'poor' (if weak keyword fit), and put the "
            "  missing field into `concerns` so the user can decide. The user explicitly "
            "  wants breadth — filtering 5 out of 6 jobs because salary isn't disclosed "
            "  destroys the workflow.\n"
            "- **good / okay / poor** differ only in score & confidence; all three remain in "
            "  the candidate pool downstream.\n\n"
            "Respond with ONLY a JSON object. Schema:\n"
            '{"score": <int 0-100>, "verdict": "good|okay|poor|skip", '
            '"matched_signals": [<short strings>], '
            '"concerns": [<short strings>], '
            '"reason": "<one line>"}\n\n'
            f"## User preferences (current)\n{snapshot_md}"
        )
        user_prompt = f"## Job posting\n{job_md}\n\nReturn JSON only."

        parsed = self._llm.invoke_json(
            [
                _system(system_prompt),
                _user(user_prompt),
            ],
            route="job_match",
        )
        if not isinstance(parsed, dict):
            return None

        try:
            score = float(parsed.get("score", 0))
        except (TypeError, ValueError):
            return None
        score = max(0.0, min(score, 100.0))

        verdict = str(parsed.get("verdict") or "").strip().lower()
        if verdict not in _VERDICTS:
            return None

        matched = _coerce_str_list(parsed.get("matched_signals"))
        concerns = _coerce_str_list(parsed.get("concerns"))
        reason = str(parsed.get("reason") or "").strip()[:400]
        return MatchResult(
            score=round(score, 1),
            verdict=verdict,
            matched_signals=matched,
            concerns=concerns,
            reason=reason or "llm_classification",
        )

    # ──────────────────────────────────────────────────────── helpers

    @staticmethod
    def _render_job(job: dict[str, Any], *, keyword: str) -> str:
        title = str(job.get("title") or "").strip()
        company = str(job.get("company") or "").strip()
        salary = str(job.get("salary") or "").strip() or "(not provided)"
        snippet = str(job.get("snippet") or "").strip()
        detail = job.get("detail") if isinstance(job.get("detail"), dict) else {}
        detail_md = ""
        if detail:
            try:
                detail_json = json.dumps(detail, ensure_ascii=False, indent=2)
            except (TypeError, ValueError):
                detail_json = str(detail)
            detail_md = "\n- detail: |\n" + _indent(
                token_preview(detail_json, max_tokens=700),
                prefix="    ",
            )

        return (
            f"- title: {title}\n"
            f"- company: {company}\n"
            f"- salary: {salary}\n"
            f"- user_searched_keyword: {keyword or '(none)'}\n"
            f"- snippet: {token_preview(snippet, max_tokens=600)}"
            f"{detail_md}"
        )


def _coerce_str_list(value: Any) -> list[str]:
    if not isinstance(value, list):
        return []
    out: list[str] = []
    for item in value:
        text = str(item or "").strip()
        if text:
            out.append(text[:160])
    return out


def _indent(text: str, *, prefix: str) -> str:
    return "\n".join(f"{prefix}{line}" for line in text.splitlines())


def _system(content: str) -> Any:
    from langchain_core.messages import SystemMessage
    return SystemMessage(content=content)


def _user(content: str) -> Any:
    from langchain_core.messages import HumanMessage
    return HumanMessage(content=content)


__all__ = ["JobSnapshotMatcher", "MatchResult"]
