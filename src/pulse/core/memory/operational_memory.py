"""Pulse Operational Memory — 执行态临时记忆 (§8.5)

OperationalMemory 是 Memory Runtime 的最底层，位于 recall 之下：
  - 存储当前 turn 的临时执行状态（scratchpad、中间推理、工具观测缓存）
  - 生命周期 = 单个 turn 或 task run，不持久化到数据库
  - 为 Brain 的 ReAct 循环提供快速读写的工作记忆
  - turn 结束后由 compaction 压缩为 recall summary

设计参考：Pulse-MemoryRuntime设计.md §8.5
"""

from __future__ import annotations

import logging
import time
from collections import defaultdict
from dataclasses import dataclass, field
from typing import Any

logger = logging.getLogger(__name__)


@dataclass
class ScratchEntry:
    """单条 scratchpad 条目。"""
    key: str
    value: Any
    created_at: float = field(default_factory=time.monotonic)
    ttl_seconds: float = 0


class OperationalMemory:
    """执行态临时记忆 — 纯内存实现，不持久化。

    每个 task_id 拥有独立的 scratchpad 空间，互不干扰。
    支持 TTL 自动过期。
    """

    def __init__(self, *, max_entries_per_task: int = 200) -> None:
        self._max_entries = max_entries_per_task
        self._store: dict[str, dict[str, ScratchEntry]] = defaultdict(dict)

    def write(self, task_id: str, key: str, value: Any, *, ttl_seconds: float = 0) -> None:
        """写入一条临时记忆。"""
        space = self._store[task_id]
        if len(space) >= self._max_entries and key not in space:
            oldest_key = min(space, key=lambda k: space[k].created_at)
            del space[oldest_key]
        space[key] = ScratchEntry(key=key, value=value, ttl_seconds=ttl_seconds)

    def read(self, task_id: str, key: str, default: Any = None) -> Any:
        """读取一条临时记忆，自动检查 TTL。"""
        space = self._store.get(task_id)
        if space is None:
            return default
        entry = space.get(key)
        if entry is None:
            return default
        if entry.ttl_seconds > 0 and (time.monotonic() - entry.created_at) > entry.ttl_seconds:
            del space[key]
            return default
        return entry.value

    def read_all(self, task_id: str) -> dict[str, Any]:
        """读取某个 task 的全部临时记忆（过滤已过期）。"""
        space = self._store.get(task_id)
        if space is None:
            return {}
        now = time.monotonic()
        expired = [k for k, e in space.items() if e.ttl_seconds > 0 and (now - e.created_at) > e.ttl_seconds]
        for k in expired:
            del space[k]
        return {k: e.value for k, e in space.items()}

    def clear(self, task_id: str) -> int:
        """清除某个 task 的全部临时记忆。返回清除的条目数。"""
        space = self._store.pop(task_id, {})
        count = len(space)
        if count > 0:
            logger.debug("Cleared %d operational entries for task %s", count, task_id)
        return count

    def clear_all(self) -> int:
        """清除所有临时记忆。"""
        total = sum(len(s) for s in self._store.values())
        self._store.clear()
        return total

    def stats(self) -> dict[str, Any]:
        """返回当前状态统计。"""
        return {
            "active_tasks": len(self._store),
            "total_entries": sum(len(s) for s in self._store.values()),
            "max_entries_per_task": self._max_entries,
        }
