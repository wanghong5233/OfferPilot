"""Pulse Compaction Engine — P1+P2 内核组件

对应设计文档 §9.1-9.2: 四级 Compaction Pipeline。

  - turn → taskRun:      每轮推理结束后，将 raw tool calls / observations 压缩为 running task summary (P1)
  - taskRun → session:    run 完成或中止后，将 task summary + outcome 压缩为 session summary (P2)
  - session → workspace:  会话结束后压缩为 workspace summary (P2)

CompactionEngine 不直接绑定某个 LLM 客户端，而是通过 CompactionStrategy
接口解耦：默认 RuleCompactionStrategy 生成 token-bounded breadcrumb；
高质量语义摘要可通过注入 LLM-backed strategy 替换。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Protocol

from .task_context import TaskContext
from .tokenizer import count_tokens, token_preview
from .memory.envelope import (
    MemoryEnvelope,
    MemoryKind,
    MemoryLayer,
    MemoryScope,
)

logger = logging.getLogger(__name__)


class CompactionLevel(str, Enum):
    turn_to_taskrun = "turn→taskRun"
    taskrun_to_session = "taskRun→session"
    session_to_workspace = "session→workspace"


@dataclass
class CompactionInput:
    """压缩引擎的输入。"""

    ctx: TaskContext
    level: CompactionLevel
    raw_steps: list[dict[str, Any]]
    existing_summary: str = ""


@dataclass
class CompactionOutput:
    """压缩引擎的输出。"""

    summary: str
    token_estimate: int
    level: CompactionLevel
    elapsed_ms: int = 0


class CompactionStrategy(Protocol):
    """压缩策略接口。"""

    def compact(self, inp: CompactionInput) -> CompactionOutput: ...


class RuleCompactionStrategy:
    """基于规则的压缩策略 — 零 LLM 成本。

    策略:
      1. 保留每个 step 的 tool_name + token-bounded observation preview
      2. 保留最终 answer preview
      3. 如果有 existing_summary，追加新内容
    """

    def __init__(
        self,
        *,
        max_obs_tokens: int = 120,
        max_answer_tokens: int = 200,
        max_steps: int = 20,
        tokenizer_model: str = "gpt-4o-mini",
    ) -> None:
        self._max_obs_tokens = max(16, int(max_obs_tokens))
        self._max_answer_tokens = max(16, int(max_answer_tokens))
        self._max_steps = max_steps
        self._tokenizer_model = tokenizer_model

    def compact(self, inp: CompactionInput) -> CompactionOutput:
        t0 = time.monotonic()
        lines: list[str] = []

        if inp.existing_summary:
            lines.append(f"[Previous] {inp.existing_summary}")

        for step in inp.raw_steps[-self._max_steps:]:
            tool = step.get("tool_name", "")
            obs = str(step.get("observation", ""))
            obs = token_preview(
                obs,
                max_tokens=self._max_obs_tokens,
                model=self._tokenizer_model,
            )
            action = step.get("action", "")
            if tool:
                lines.append(f"- {tool}: {obs}")
            elif action == "respond":
                answer = token_preview(
                    str(step.get("answer", "")),
                    max_tokens=self._max_answer_tokens,
                    model=self._tokenizer_model,
                )
                lines.append(f"- [answer] {answer}")

        summary = "\n".join(lines) if lines else "(no steps)"
        elapsed = int((time.monotonic() - t0) * 1000)
        return CompactionOutput(
            summary=summary,
            token_estimate=count_tokens(summary, model=self._tokenizer_model),
            level=inp.level,
            elapsed_ms=elapsed,
        )


class CompactionEngine:
    """管理压缩流程，协调 Strategy 和 Memory 写入。"""

    def __init__(self, *, strategy: CompactionStrategy | None = None) -> None:
        self._strategy = strategy or RuleCompactionStrategy()

    def compact_turn(
        self,
        ctx: TaskContext,
        steps: list[dict[str, Any]],
        existing_summary: str = "",
    ) -> CompactionOutput:
        """执行 turn → taskRun 压缩。

        Args:
            ctx: 当前执行上下文
            steps: 本轮的 raw steps (tool calls + observations)
            existing_summary: 之前累积的 task summary

        Returns:
            CompactionOutput 包含新的 summary
        """
        inp = CompactionInput(
            ctx=ctx,
            level=CompactionLevel.turn_to_taskrun,
            raw_steps=steps,
            existing_summary=existing_summary,
        )
        output = self._strategy.compact(inp)

        logger.debug(
            "Compaction [%s] task=%s: %d steps → %d tokens est, %dms",
            output.level.value, ctx.task_id, len(steps),
            output.token_estimate, output.elapsed_ms,
        )
        return output

    def to_envelope(self, ctx: TaskContext, output: CompactionOutput) -> MemoryEnvelope:
        """将压缩结果包装为 MemoryEnvelope。

        layer 和 scope 根据 CompactionLevel 自动选择。
        """
        ids = ctx.id_dict()
        layer, scope, kind = _LEVEL_MAPPING[output.level]
        return MemoryEnvelope(
            kind=kind,
            layer=layer,
            scope=scope,
            trace_id=ids.get("trace_id") or "",
            run_id=ids.get("run_id") or "",
            task_id=ids.get("task_id") or "",
            session_id=ids.get("session_id") or "",
            workspace_id=ids.get("workspace_id"),
            content={
                "summary": output.summary,
                "level": output.level.value,
                "token_estimate": output.token_estimate,
            },
            source="compaction_engine",
        )

    def compact_session(
        self,
        ctx: TaskContext,
        task_summaries: list[str],
        outcome: str = "",
        existing_session_summary: str = "",
    ) -> CompactionOutput:
        """执行 taskRun → session 压缩。

        Args:
            ctx: 当前执行上下文
            task_summaries: 本 session 内所有 task run 的 summary
            outcome: 最终结果/停止原因
            existing_session_summary: 已有的 session summary（增量重写）
        """
        steps = [{"action": "respond", "answer": s} for s in task_summaries]
        if outcome:
            steps.append({"action": "respond", "answer": f"[outcome] {outcome}"})
        inp = CompactionInput(
            ctx=ctx,
            level=CompactionLevel.taskrun_to_session,
            raw_steps=steps,
            existing_summary=existing_session_summary,
        )
        output = self._strategy.compact(inp)
        logger.debug(
            "Compaction [%s] session=%s: %d summaries → %d tokens est, %dms",
            output.level.value, ctx.session_id, len(task_summaries),
            output.token_estimate, output.elapsed_ms,
        )
        return output

    def compact_workspace(
        self,
        ctx: TaskContext,
        session_summaries: list[str],
        existing_workspace_summary: str = "",
    ) -> CompactionOutput:
        """执行 session → workspace 压缩。

        Args:
            ctx: 当前执行上下文
            session_summaries: 多个 session 的 summary
            existing_workspace_summary: 已有的 workspace summary（增量重写）
        """
        steps = [{"action": "respond", "answer": s} for s in session_summaries]
        inp = CompactionInput(
            ctx=ctx,
            level=CompactionLevel.session_to_workspace,
            raw_steps=steps,
            existing_summary=existing_workspace_summary,
        )
        output = self._strategy.compact(inp)
        logger.debug(
            "Compaction [%s] workspace=%s: %d sessions → %d tokens est, %dms",
            output.level.value, ctx.workspace_id, len(session_summaries),
            output.token_estimate, output.elapsed_ms,
        )
        return output


_LEVEL_MAPPING: dict[CompactionLevel, tuple[MemoryLayer, MemoryScope, MemoryKind]] = {
    CompactionLevel.turn_to_taskrun: (MemoryLayer.recall, MemoryScope.task_run, MemoryKind.task_summary),
    CompactionLevel.taskrun_to_session: (MemoryLayer.recall, MemoryScope.session, MemoryKind.summary),
    CompactionLevel.session_to_workspace: (MemoryLayer.workspace, MemoryScope.workspace, MemoryKind.workspace_summary),
}
