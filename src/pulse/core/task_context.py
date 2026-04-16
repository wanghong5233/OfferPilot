"""Pulse Task Context — P0 内核骨架

统一执行上下文，贯穿 Agent OS → Task Runtime → Memory Runtime 全链路。
设计参考：Pulse-MemoryRuntime设计.md §5.2 / Pulse-内核架构总览.md §5
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any
from uuid import uuid4


class ExecutionMode(str, Enum):
    """五类执行模式，对应设计文档 §4.1"""

    interactive_turn = "interactiveTurn"
    heartbeat_turn = "heartbeatTurn"
    detached_scheduled_task = "detachedScheduledTask"
    subagent_task = "subagentTask"
    resumed_task = "resumedTask"


class IsolationLevel(str, Enum):
    """Session 隔离策略"""

    shared = "shared"
    light_context = "lightContext"
    isolated = "isolated"


class StopReason(str, Enum):
    """统一终止原因，对应设计文档 §5.4。"""

    completed = "completed"
    max_steps = "max_steps"
    budget_exhausted = "budget_exhausted"
    tool_blocked = "tool_blocked"
    task_blocked = "task_blocked"
    no_llm = "no_llm"
    llm_error = "llm_error"
    error_aborted = "error_aborted"
    degraded = "degraded"
    empty_query = "empty_query"
    user_cancelled = "user_cancelled"
    compacted = "compacted"
    parent_cancelled = "parent_cancelled"


def _new_id(prefix: str = "") -> str:
    raw = uuid4().hex[:12]
    normalized_prefix = "trace" if prefix == "tr" else prefix
    return f"{normalized_prefix}_{raw}" if normalized_prefix else raw


@dataclass
class TaskContext:
    """单次执行的完整上下文信封。

    由 Agent OS 层在每次 patrol / interactive / heartbeat 触发时构造，
    沿 Runtime → Brain → Memory 全链路传播，不可中途丢弃。
    """

    trace_id: str = field(default_factory=lambda: _new_id("tr"))
    run_id: str = field(default_factory=lambda: _new_id("run"))
    task_id: str = ""
    session_id: str | None = None
    workspace_id: str | None = None

    mode: ExecutionMode = ExecutionMode.interactive_turn
    isolation_level: IsolationLevel = IsolationLevel.shared
    prompt_contract: str = "systemPrompt"

    token_budget: int = 8000
    parent_task_id: str | None = None

    created_at: datetime = field(
        default_factory=lambda: datetime.now(timezone.utc),
    )

    extra: dict[str, Any] = field(default_factory=dict)

    # ── 运行时可变状态 ──────────────────────────────────────

    _start_mono: float = field(default=0.0, repr=False, compare=False)
    _tokens_used: int = field(default=0, repr=False, compare=False)

    def start_clock(self) -> None:
        self._start_mono = time.monotonic()

    def elapsed_ms(self) -> int:
        if self._start_mono == 0.0:
            return 0
        return int((time.monotonic() - self._start_mono) * 1000)

    def consume_tokens(self, n: int) -> None:
        self._tokens_used += n

    @property
    def tokens_used(self) -> int:
        return self._tokens_used

    @property
    def budget_remaining(self) -> int:
        return max(0, self.token_budget - self._tokens_used)

    @property
    def over_budget(self) -> bool:
        return self._tokens_used >= self.token_budget

    # ── 序列化 ──────────────────────────────────────────────

    def to_dict(self) -> dict[str, Any]:
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
            "mode": self.mode.value,
            "isolation_level": self.isolation_level.value,
            "prompt_contract": self.prompt_contract,
            "token_budget": self.token_budget,
            "tokens_used": self._tokens_used,
            "parent_task_id": self.parent_task_id,
            "created_at": self.created_at.isoformat(),
            "elapsed_ms": self.elapsed_ms(),
        }

    def id_dict(self) -> dict[str, str | None]:
        """只返回关键 ID 集合，用于注入 Memory 写入。"""
        return {
            "trace_id": self.trace_id,
            "run_id": self.run_id,
            "task_id": self.task_id,
            "session_id": self.session_id,
            "workspace_id": self.workspace_id,
        }


@dataclass
class ExecutionRequest:
    """Agent OS → Task Runtime 的执行请求。

    由 AgentRuntime 在触发任务时构造，传递给 Task Runtime 层。
    """

    context: TaskContext
    query: str = ""
    handler_name: str = ""
    max_steps: int | None = None


# ── 工厂函数 ────────────────────────────────────────────────


def create_patrol_context(
    *,
    task_name: str,
    workspace_id: str | None = None,
    token_budget: int = 4000,
) -> TaskContext:
    """为 detachedScheduledTask（patrol）创建标准 TaskContext。"""
    return TaskContext(
        task_id=f"patrol:{task_name}",
        mode=ExecutionMode.detached_scheduled_task,
        isolation_level=IsolationLevel.isolated,
        prompt_contract="taskPrompt",
        workspace_id=workspace_id,
        token_budget=token_budget,
    )


def create_interactive_context(
    *,
    session_id: str | None = None,
    workspace_id: str | None = None,
    token_budget: int = 8000,
    extra: dict[str, Any] | None = None,
) -> TaskContext:
    """为 interactiveTurn（用户消息）创建标准 TaskContext。"""
    return TaskContext(
        task_id=f"interactive:{_new_id()}",
        session_id=session_id,
        mode=ExecutionMode.interactive_turn,
        isolation_level=IsolationLevel.shared,
        prompt_contract="systemPrompt",
        workspace_id=workspace_id,
        token_budget=token_budget,
        extra=extra or {},
    )


def create_heartbeat_context(
    *,
    workspace_id: str | None = None,
    token_budget: int = 2000,
) -> TaskContext:
    """为 heartbeatTurn 创建标准 TaskContext。"""
    return TaskContext(
        task_id="heartbeat",
        mode=ExecutionMode.heartbeat_turn,
        isolation_level=IsolationLevel.light_context,
        prompt_contract="heartbeatPrompt",
        workspace_id=workspace_id,
        token_budget=token_budget,
    )


def create_subagent_context(
    *,
    parent_task_id: str,
    parent_session_id: str | None = None,
    workspace_id: str | None = None,
    token_budget: int = 4000,
    extra: dict[str, Any] | None = None,
) -> TaskContext:
    """为 subagentTask 创建标准 TaskContext。

    子任务继承 parent 的 session/workspace，但拥有独立的 trace/run/task ID。
    隔离级别为 isolated，防止子任务污染主会话记忆。
    """
    return TaskContext(
        task_id=f"subagent:{_new_id()}",
        session_id=parent_session_id,
        parent_task_id=parent_task_id,
        mode=ExecutionMode.subagent_task,
        isolation_level=IsolationLevel.isolated,
        prompt_contract="taskPrompt",
        workspace_id=workspace_id,
        token_budget=token_budget,
        extra=extra or {},
    )


def create_resumed_context(
    *,
    original_task_id: str,
    original_trace_id: str,
    session_id: str | None = None,
    workspace_id: str | None = None,
    token_budget: int = 4000,
    checkpoint_data: dict[str, Any] | None = None,
) -> TaskContext:
    """为 resumedTask 创建标准 TaskContext。

    恢复执行时保留原始 trace_id 以维持追溯链，但生成新的 run_id。
    checkpoint_data 存入 extra 供 Brain 恢复状态。
    """
    ctx = TaskContext(
        trace_id=original_trace_id,
        task_id=f"resumed:{original_task_id}",
        session_id=session_id,
        mode=ExecutionMode.resumed_task,
        isolation_level=IsolationLevel.shared,
        prompt_contract="recoveryPrompt",
        workspace_id=workspace_id,
        token_budget=token_budget,
        extra={"checkpoint": checkpoint_data or {}},
    )
    return ctx
