"""Pulse Hook Registry — P1 内核组件

生命周期钩子注册与触发，对应设计文档 §6.1-6.5。

Hook 点:
  beforeTaskStart  — Task Runtime 启动前 (可阻断)
  beforeToolUse    — 工具调用前 (可阻断)
  afterToolUse     — 工具调用后 (只观测)
  beforeCompact    — 压缩前 (只观测)
  afterCompact     — 压缩后 (只观测)
  beforePromotion  — 晋升前 (可阻断)
  afterPromotion   — 晋升后 (只观测)
  onRecovery       — 恢复时 (只观测)
  onCircuitOpen    — 熔断器打开时 (只观测)

每个 Hook 接收一个 HookContext，返回 HookResult。
可阻断的 Hook 如果返回 block=True，调用方必须中止操作。
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from enum import Enum
from typing import Any, Callable

from .task_context import TaskContext

logger = logging.getLogger(__name__)


class HookPoint(str, Enum):
    before_task_start = "beforeTaskStart"
    before_tool_use = "beforeToolUse"
    after_tool_use = "afterToolUse"
    before_compact = "beforeCompact"
    after_compact = "afterCompact"
    before_promotion = "beforePromotion"
    after_promotion = "afterPromotion"
    on_recovery = "onRecovery"
    on_circuit_open = "onCircuitOpen"
    on_task_end = "onTaskEnd"


_BLOCKABLE: frozenset[HookPoint] = frozenset({
    HookPoint.before_task_start,
    HookPoint.before_tool_use,
    HookPoint.before_promotion,
})


@dataclass
class HookContext:
    """传递给 Hook handler 的上下文。"""

    point: HookPoint
    ctx: TaskContext
    payload: dict[str, Any] = field(default_factory=dict)


@dataclass
class HookResult:
    """Hook handler 的返回值。"""

    block: bool = False
    reason: str = ""
    injected: dict[str, Any] = field(default_factory=dict)


HookHandler = Callable[[HookContext], HookResult | None]


class HookRegistry:
    """管理所有生命周期钩子的注册与触发。"""

    def __init__(self) -> None:
        self._hooks: dict[HookPoint, list[tuple[str, HookHandler, int]]] = {
            p: [] for p in HookPoint
        }

    def register(
        self,
        point: HookPoint,
        handler: HookHandler,
        *,
        name: str = "",
        priority: int = 100,
    ) -> None:
        """注册一个 Hook handler。priority 越小越先执行。"""
        hook_name = name or handler.__name__
        self._hooks[point].append((hook_name, handler, priority))
        self._hooks[point].sort(key=lambda t: t[2])
        logger.debug("Hook registered: %s → %s (priority=%d)", point.value, hook_name, priority)

    def fire(self, point: HookPoint, ctx: TaskContext, payload: dict[str, Any] | None = None) -> HookResult:
        """触发指定 Hook 点的所有 handler。

        对于可阻断的 Hook，任何一个 handler 返回 block=True 就立即中止。
        所有 handler 的 injected 会合并到最终结果中。
        """
        hctx = HookContext(point=point, ctx=ctx, payload=payload or {})
        merged = HookResult()
        blockable = point in _BLOCKABLE

        for hook_name, handler, _priority in self._hooks[point]:
            t0 = time.monotonic()
            try:
                result = handler(hctx)
            except Exception as exc:
                logger.warning("Hook %s/%s raised: %s", point.value, hook_name, exc)
                continue
            elapsed_ms = int((time.monotonic() - t0) * 1000)

            if result is None:
                continue

            merged.injected.update(result.injected)

            if blockable and result.block:
                merged.block = True
                merged.reason = result.reason or f"blocked by {hook_name}"
                logger.info(
                    "Hook %s/%s BLOCKED (reason=%s, %dms)",
                    point.value, hook_name, merged.reason, elapsed_ms,
                )
                return merged

        return merged

    def has_hooks(self, point: HookPoint) -> bool:
        return bool(self._hooks.get(point))

    def list_hooks(self) -> dict[str, list[str]]:
        """返回所有已注册 Hook 的概览。"""
        return {
            p.value: [name for name, _, _ in handlers]
            for p, handlers in self._hooks.items()
            if handlers
        }
