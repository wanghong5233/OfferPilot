"""Pulse Prompt Contract Builder — P1 内核组件

根据 ExecutionMode 组装不同的 system prompt，对应设计文档 §7.1-7.4。

六类 Prompt Contract:
  - systemPrompt:     interactiveTurn — 完整身份/记忆/工具/边界
  - heartbeatPrompt:  heartbeatTurn — workspace essentials + 巡视目标
  - taskPrompt:       detachedScheduledTask / subagentTask — 任务目标/成功条件/允许工具
  - compactPrompt:    压缩阶段 — 保留目标/已完成/待办/关键发现/用户纠正
  - promotionPrompt:  晋升阶段 — 提取事实/偏好/规则/证据/冲突候选
  - recoveryPrompt:   resumedTask — checkpoint/已完成步骤/失败点/下一步

组装顺序 (interactiveTurn 为例):
  1. Soul / Identity
  2. User Profile / Preferences
  3. Workspace Summary
  4. Recent Recall
  5. Relevant Archival Facts
  6. Tool Menu
  7. Safety Boundaries
  8. Current Task / User Query
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from enum import Enum
from typing import Any, Protocol

from .task_context import ExecutionMode, TaskContext
from .memory_reader import IsolatedMemoryReader


class ContractType(str, Enum):
    system = "systemPrompt"
    heartbeat = "heartbeatPrompt"
    task = "taskPrompt"
    compact = "compactPrompt"
    promotion = "promotionPrompt"
    recovery = "recoveryPrompt"


_MODE_TO_CONTRACT: dict[ExecutionMode, ContractType] = {
    ExecutionMode.interactive_turn: ContractType.system,
    ExecutionMode.heartbeat_turn: ContractType.heartbeat,
    ExecutionMode.detached_scheduled_task: ContractType.task,
    ExecutionMode.subagent_task: ContractType.task,
    ExecutionMode.resumed_task: ContractType.recovery,
}


class MemoryReader(Protocol):
    """Memory 层提供给 PromptContract 的只读接口。"""

    def read_core_snapshot(self) -> dict[str, Any]: ...
    def read_recent(self, session_id: str | None, limit: int) -> list[dict[str, Any]]: ...
    def search_recall(self, query: str, session_id: str | None, top_k: int) -> list[dict[str, Any]]: ...
    def search_archival(self, query: str, limit: int) -> list[dict[str, Any]]: ...
    def read_workspace_essentials(self, workspace_id: str | None) -> dict[str, Any]: ...


@dataclass(frozen=True)
class PromptContract:
    """一次 prompt 组装的产物。"""

    contract_type: ContractType
    sections: list[str]
    token_estimate: int

    @property
    def text(self) -> str:
        return "\n\n".join(s for s in self.sections if s)


class PromptContractBuilder:
    """根据 TaskContext 和 Memory 状态组装 prompt。"""

    def __init__(
        self,
        *,
        memory: MemoryReader | None = None,
        tool_names: list[str] | None = None,
        recent_limit: int = 8,
        archival_limit: int = 5,
        recall_top_k: int = 4,
    ) -> None:
        self._memory = memory
        self._tool_names = tool_names or []
        self._recent_limit = recent_limit
        self._archival_limit = archival_limit
        self._recall_top_k = recall_top_k

    def build(self, ctx: TaskContext, query: str = "") -> PromptContract:
        contract_type = _MODE_TO_CONTRACT.get(ctx.mode, ContractType.system)
        ctx.prompt_contract = contract_type.value

        # P2: 根据 ctx 的隔离级别包装 memory reader
        memory: MemoryReader | None = self._memory
        if memory is not None:
            memory = IsolatedMemoryReader(memory, ctx)

        method_name = _CONTRACT_METHOD.get(contract_type, "_build_system")
        builder = getattr(self, method_name)
        sections = builder(ctx, query, memory)

        token_est = sum(len(s) // 3 for s in sections)
        return PromptContract(
            contract_type=contract_type,
            sections=sections,
            token_estimate=token_est,
        )

    # ── Contract Builders ──────────────────────────────────

    def _build_system(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """interactiveTurn: 完整 prompt。"""
        sections: list[str] = []
        sections.append(self._section_identity(mem))
        sections.append(self._section_user_profile(mem))
        sections.append(self._section_user_prefs(mem))
        sections.append(self._section_workspace(mem, ctx))
        sections.append(self._section_recent_recall(mem, ctx))
        sections.append(self._section_relevant_recall(mem, query, ctx))
        sections.append(self._section_archival(mem, query))
        sections.append(self._section_tools())
        sections.append(self._section_boundaries(mem))
        return [s for s in sections if s]

    def _build_heartbeat(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """heartbeatTurn: 轻量 prompt，只读 workspace essentials。"""
        sections: list[str] = []
        sections.append(
            "You are Pulse in heartbeat mode. "
            "Check workspace status, report anomalies, do NOT start heavy reasoning."
        )
        sections.append(self._section_identity_brief(mem))
        sections.append(self._section_workspace(mem, ctx))
        sections.append(self._section_tools())
        return [s for s in sections if s]

    def _build_task(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """detachedScheduledTask / subagentTask: 任务聚焦 prompt。"""
        sections: list[str] = []
        sections.append(
            f"You are Pulse executing a scheduled task.\n"
            f"Task ID: {ctx.task_id}\n"
            f"Execution mode: {ctx.mode.value}\n"
            f"Focus on completing the task objective. Be efficient."
        )
        sections.append(self._section_identity_brief(mem))
        sections.append(self._section_archival(mem, query))
        sections.append(self._section_tools())
        sections.append(self._section_boundaries(mem))
        return [s for s in sections if s]

    def _build_compact(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """compaction 阶段: 指导 LLM 压缩。"""
        return [
            "You are Pulse's compaction engine.\n"
            "Summarize the following execution trace into a concise task summary.\n"
            "Preserve: task objective, completed steps, pending items, key findings, user corrections.\n"
            "Discard: raw tool observations, intermediate reasoning, redundant context.\n"
            "Output a structured JSON with keys: objective, completed, pending, findings, corrections."
        ]

    def _build_promotion(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """promotion 阶段: 指导 LLM 提取事实。"""
        return [
            "You are Pulse's fact extraction engine.\n"
            "From the following conversation/summary, extract stable facts as structured triples.\n"
            "Each fact: {subject, predicate, object, confidence, evidence_ref}.\n"
            "Only extract facts with high confidence (>0.7).\n"
            "Flag conflicts with existing facts if any are provided.\n"
            "Output a JSON array of fact objects."
        ]

    def _build_recovery(self, ctx: TaskContext, query: str, mem: MemoryReader | None) -> list[str]:
        """resumedTask: 从 checkpoint 恢复。"""
        sections: list[str] = []
        sections.append(
            f"You are Pulse resuming a previously interrupted task.\n"
            f"Task ID: {ctx.task_id}\n"
            f"Review the checkpoint below, then continue from where it left off."
        )
        sections.append(self._section_identity_brief(mem))
        sections.append(self._section_tools())
        sections.append(self._section_boundaries(mem))
        return [s for s in sections if s]

    # ── Section Helpers ────────────────────────────────────

    def _section_identity(self, mem: MemoryReader | None) -> str:
        if mem is None:
            return _DEFAULT_IDENTITY
        snapshot = mem.read_core_snapshot()
        soul = snapshot.get("soul") if isinstance(snapshot.get("soul"), dict) else {}
        if not soul:
            return _DEFAULT_IDENTITY
        prefix = soul.get("assistant_prefix", "Pulse")
        role = soul.get("role", "")
        tone = soul.get("tone", "")
        principles = soul.get("principles", [])
        style = soul.get("style_rules", [])
        parts = [f"## Identity\nName: {prefix}"]
        if role:
            parts.append(f"Role: {role}")
        if tone:
            parts.append(f"Tone: {tone}")
        if principles:
            parts.append("Principles: " + "; ".join(str(p) for p in principles[:5]))
        if style:
            parts.append("Style: " + "; ".join(str(s) for s in style[:5]))
        return "\n".join(parts)

    def _section_identity_brief(self, mem: MemoryReader | None) -> str:
        if mem is None:
            return ""
        snapshot = mem.read_core_snapshot()
        soul = snapshot.get("soul") if isinstance(snapshot.get("soul"), dict) else {}
        prefix = soul.get("assistant_prefix", "Pulse")
        role = soul.get("role", "")
        return f"Identity: {prefix}" + (f" ({role})" if role else "")

    def _section_user_profile(self, mem: MemoryReader | None) -> str:
        if mem is None:
            return ""
        snapshot = mem.read_core_snapshot()
        user = snapshot.get("user") if isinstance(snapshot.get("user"), dict) else {}
        if not user or not any(v for v in user.values() if v):
            return ""
        return f"## User Profile\n{json.dumps(user, ensure_ascii=False)}"

    def _section_user_prefs(self, mem: MemoryReader | None) -> str:
        if mem is None:
            return ""
        snapshot = mem.read_core_snapshot()
        prefs = snapshot.get("prefs") if isinstance(snapshot.get("prefs"), dict) else {}
        if not prefs:
            return ""
        return f"## User Preferences\n{json.dumps(prefs, ensure_ascii=False)}"

    def _section_recent_recall(self, mem: MemoryReader | None, ctx: TaskContext) -> str:
        if mem is None:
            return ""
        recent = mem.read_recent(ctx.session_id, self._recent_limit)
        if not recent:
            return ""
        lines = ["## Recent Conversation History"]
        for entry in recent[-self._recent_limit:]:
            role = str(entry.get("role") or "")
            text = str(entry.get("text") or "")[:200]
            lines.append(f"- [{role}] {text}")
        return "\n".join(lines)

    def _section_relevant_recall(self, mem: MemoryReader | None, query: str, ctx: TaskContext) -> str:
        if mem is None or not query:
            return ""
        hits = mem.search_recall(query, ctx.session_id, self._recall_top_k)
        if not hits:
            return ""
        lines = ["## Relevant Past Conversations"]
        for hit in hits:
            text = str(hit.get("text") or "")[:200]
            sim = float(hit.get("similarity") or 0)
            lines.append(f"- (sim={sim:.2f}) {text}")
        return "\n".join(lines)

    def _section_archival(self, mem: MemoryReader | None, query: str) -> str:
        if mem is None or not query:
            return ""
        hits = mem.search_archival(query, self._archival_limit)
        if not hits:
            return ""
        lines = ["## Relevant Long-term Knowledge"]
        for fact in hits:
            s = str(fact.get("subject") or "")
            p = str(fact.get("predicate") or "")
            o = str(fact.get("object") or "")
            lines.append(f"- {s} {p} {o}")
        return "\n".join(lines)

    def _section_tools(self) -> str:
        if not self._tool_names:
            return ""
        return "## Available Tools\n" + ", ".join(self._tool_names)

    def _section_boundaries(self, mem: MemoryReader | None) -> str:
        if mem is None:
            return ""
        snapshot = mem.read_core_snapshot()
        soul = snapshot.get("soul") if isinstance(snapshot.get("soul"), dict) else {}
        boundaries = soul.get("boundaries", [])
        if not boundaries:
            return ""
        return "## Safety Boundaries\n" + "\n".join(f"- {b}" for b in boundaries[:8])

    def _section_workspace(self, mem: MemoryReader | None, ctx: TaskContext) -> str:
        if mem is None:
            return ""
        essentials = mem.read_workspace_essentials(ctx.workspace_id)
        if not essentials:
            return ""
        summary = essentials.get("summary", "")
        facts = essentials.get("facts", [])
        if not summary and not facts:
            return ""
        parts = ["## Workspace Context"]
        if summary:
            parts.append(summary)
        if facts:
            parts.append("Key facts:")
            for f in facts[:10]:
                parts.append(f"- {f.get('key', '')}: {f.get('value', '')}")
        return "\n".join(parts)


_DEFAULT_IDENTITY = (
    "You are Pulse, a personal AI assistant with ReAct reasoning.\n"
    "You have access to tools. Decide what to do step by step:\n"
    "- Call tools when you need information or need to perform actions.\n"
    "- You can chain multiple tool calls across steps.\n"
    "- When you have enough information, respond directly to the user.\n"
    "- Be concise, direct, and helpful."
)

_CONTRACT_METHOD: dict[ContractType, str] = {
    ContractType.system: "_build_system",
    ContractType.heartbeat: "_build_heartbeat",
    ContractType.task: "_build_task",
    ContractType.compact: "_build_compact",
    ContractType.promotion: "_build_promotion",
    ContractType.recovery: "_build_recovery",
}
