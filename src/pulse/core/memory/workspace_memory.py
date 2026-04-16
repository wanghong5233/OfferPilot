"""Pulse Workspace Memory — P2 内核组件

对应设计文档 §12.2: workspace 级 summary/facts 聚合。

WorkspaceMemory 是 Memory Runtime 的中间层，位于 recall 和 archival 之间：
  - 存储 workspace summary（由 session→workspace compaction 产出）
  - 存储 workspace-scoped facts（从 session 中提取的中频事实）
  - 为 PromptContract 提供 workspace essentials（heartbeat/task 模式使用）

存储后端复用 DatabaseEngine，独立表 workspace_summaries / workspace_facts。
"""

from __future__ import annotations

from datetime import datetime, timezone
from typing import Any

from ..storage.engine import DatabaseEngine


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


class WorkspaceMemory:
    """Workspace 级别的记忆聚合层。"""

    def __init__(
        self,
        *,
        db_engine: DatabaseEngine | None = None,
    ) -> None:
        self._db = db_engine or DatabaseEngine()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_summaries (
                id BIGSERIAL PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                summary TEXT NOT NULL DEFAULT '',
                token_estimate INTEGER NOT NULL DEFAULT 0,
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS workspace_facts (
                id BIGSERIAL PRIMARY KEY,
                workspace_id TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL DEFAULT '',
                updated_at TEXT NOT NULL DEFAULT ''
            )
            """
        )

    # ── Summary ────────────────────────────────────────────

    def get_summary(self, workspace_id: str) -> str:
        """读取 workspace summary。"""
        row = self._db.execute(
            "SELECT summary FROM workspace_summaries WHERE workspace_id = %s "
            "ORDER BY updated_at DESC LIMIT 1",
            (workspace_id,),
            fetch="one",
        )
        if not row:
            return ""
        return str(row[0] or "")

    def set_summary(
        self,
        workspace_id: str,
        summary: str,
        token_estimate: int = 0,
    ) -> None:
        """写入或更新 workspace summary（upsert 语义）。"""
        now = _utc_now_iso()
        existing = self._db.execute(
            "SELECT id FROM workspace_summaries WHERE workspace_id = %s LIMIT 1",
            (workspace_id,),
            fetch="one",
        )
        if existing:
            self._db.execute(
                "UPDATE workspace_summaries SET summary = %s, token_estimate = %s, updated_at = %s "
                "WHERE workspace_id = %s",
                (summary, token_estimate, now, workspace_id),
            )
        else:
            self._db.execute(
                "INSERT INTO workspace_summaries (workspace_id, summary, token_estimate, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s)",
                (workspace_id, summary, token_estimate, now, now),
            )

    # ── Facts (key-value) ──────────────────────────────────

    def get_fact(self, workspace_id: str, key: str) -> str | None:
        """读取单个 workspace fact。"""
        row = self._db.execute(
            "SELECT value FROM workspace_facts WHERE workspace_id = %s AND key = %s LIMIT 1",
            (workspace_id, key),
            fetch="one",
        )
        if not row:
            return None
        return str(row[0] or "")

    def set_fact(
        self,
        workspace_id: str,
        key: str,
        value: str,
        source: str = "",
    ) -> None:
        """写入或更新单个 workspace fact（upsert 语义）。"""
        now = _utc_now_iso()
        existing = self._db.execute(
            "SELECT id FROM workspace_facts WHERE workspace_id = %s AND key = %s LIMIT 1",
            (workspace_id, key),
            fetch="one",
        )
        if existing:
            self._db.execute(
                "UPDATE workspace_facts SET value = %s, source = %s, updated_at = %s "
                "WHERE workspace_id = %s AND key = %s",
                (value, source, now, workspace_id, key),
            )
        else:
            self._db.execute(
                "INSERT INTO workspace_facts (workspace_id, key, value, source, created_at, updated_at) "
                "VALUES (%s, %s, %s, %s, %s, %s)",
                (workspace_id, key, value, source, now, now),
            )

    def list_facts(self, workspace_id: str) -> list[dict[str, str]]:
        """列出 workspace 下所有 facts。"""
        rows = self._db.execute(
            "SELECT key, value, source FROM workspace_facts WHERE workspace_id = %s ORDER BY key",
            (workspace_id,),
            fetch="all",
        )
        return [
            {"key": str(r[0]), "value": str(r[1]), "source": str(r[2])}
            for r in (rows or [])
        ]

    get_facts = list_facts
    add_fact = set_fact

    def delete_fact(self, workspace_id: str, key: str) -> bool:
        """删除单个 workspace fact。"""
        self._db.execute(
            "DELETE FROM workspace_facts WHERE workspace_id = %s AND key = %s",
            (workspace_id, key),
        )
        return True

    # ── Essentials (for PromptContract) ────────────────────

    def read_essentials(self, workspace_id: str) -> dict[str, Any]:
        """读取 workspace essentials，供 PromptContract 使用。

        返回 summary + 所有 facts 的紧凑表示。
        """
        summary = self.get_summary(workspace_id)
        facts = self.list_facts(workspace_id)
        return {
            "workspace_id": workspace_id,
            "summary": summary,
            "facts": facts,
        }
