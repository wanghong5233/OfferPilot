"""PostgreSQL-backed game run store."""

from __future__ import annotations

import json
import logging
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timezone
from typing import Any

from ...core.storage.engine import DatabaseEngine

logger = logging.getLogger(__name__)


@dataclass(slots=True)
class GameRunRecord:
    game_id: str
    account_id: str = "default"
    status: str = "not_ready"
    tasks: list[dict[str, Any]] = field(default_factory=list)
    rewards_summary: str = ""
    dry_run: bool = True
    promoted_to_archival: bool = False
    id: str = ""
    started_at: datetime | None = None
    finished_at: datetime | None = None

    def __post_init__(self) -> None:
        if not self.id:
            self.id = str(uuid.uuid4())
        if self.started_at is None:
            self.started_at = datetime.now(timezone.utc)
        if self.finished_at is None:
            self.finished_at = datetime.now(timezone.utc)

    def to_payload(self) -> dict[str, Any]:
        return {
            "id": self.id,
            "game_id": self.game_id,
            "account_id": self.account_id,
            "started_at": (self.started_at or datetime.now(timezone.utc)).isoformat(),
            "finished_at": (self.finished_at or datetime.now(timezone.utc)).isoformat(),
            "status": self.status,
            "tasks": list(self.tasks),
            "rewards_summary": self.rewards_summary,
            "dry_run": bool(self.dry_run),
            "promoted_to_archival": bool(self.promoted_to_archival),
        }


_REQUIRED_COLUMNS: frozenset[str] = frozenset(
    {
        "id",
        "game_id",
        "account_id",
        "started_at",
        "finished_at",
        "status",
        "tasks",
        "rewards_summary",
        "dry_run",
        "promoted_to_archival",
    }
)


class GameRunStore:
    """Thin DAL over ``game_runs``."""

    def __init__(self, *, db_engine: DatabaseEngine | None = None) -> None:
        self._db = db_engine or DatabaseEngine()
        self._schema_ready = False

    def ensure_schema(self) -> None:
        if self._schema_ready:
            return
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS game_runs (
                id UUID PRIMARY KEY,
                game_id TEXT NOT NULL,
                account_id TEXT NOT NULL DEFAULT 'default',
                started_at TIMESTAMPTZ NOT NULL,
                finished_at TIMESTAMPTZ NOT NULL,
                status TEXT NOT NULL,
                tasks JSONB NOT NULL DEFAULT '[]'::jsonb,
                rewards_summary TEXT NOT NULL DEFAULT '',
                dry_run BOOLEAN NOT NULL DEFAULT TRUE,
                promoted_to_archival BOOLEAN NOT NULL DEFAULT FALSE
            )
            """
        )
        rows = self._db.execute(
            "SELECT column_name FROM information_schema.columns WHERE table_name = 'game_runs'",
            fetch="all",
        ) or []
        present = {str(row[0]) for row in rows}
        missing = _REQUIRED_COLUMNS - present
        if missing:
            raise RuntimeError(
                "game_runs schema is incompatible (missing columns: "
                f"{sorted(missing)}). Drop the legacy table manually before enabling GameModule."
            )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_game_runs_game_started ON game_runs(game_id, started_at DESC)"
        )
        self._schema_ready = True

    def append(self, record: GameRunRecord) -> str:
        self.ensure_schema()
        payload = record.to_payload()
        self._db.execute(
            """
            INSERT INTO game_runs(
                id, game_id, account_id, started_at, finished_at, status,
                tasks, rewards_summary, dry_run, promoted_to_archival
            ) VALUES (
                %s, %s, %s, %s::timestamptz, %s::timestamptz, %s,
                %s::jsonb, %s, %s, %s
            )
            """,
            (
                payload["id"],
                payload["game_id"],
                payload["account_id"],
                payload["started_at"],
                payload["finished_at"],
                payload["status"],
                json.dumps(payload["tasks"], ensure_ascii=False),
                payload["rewards_summary"],
                payload["dry_run"],
                payload["promoted_to_archival"],
            ),
        )
        return str(payload["id"])

    def list_recent(self, *, game_id: str | None = None, limit: int = 20) -> list[dict[str, Any]]:
        self.ensure_schema()
        safe_limit = max(1, min(int(limit), 100))
        if game_id:
            rows = self._db.execute(
                """
                SELECT id, game_id, account_id, started_at, finished_at, status,
                       tasks, rewards_summary, dry_run, promoted_to_archival
                FROM game_runs
                WHERE game_id = %s
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (str(game_id), safe_limit),
                fetch="all",
            ) or []
        else:
            rows = self._db.execute(
                """
                SELECT id, game_id, account_id, started_at, finished_at, status,
                       tasks, rewards_summary, dry_run, promoted_to_archival
                FROM game_runs
                ORDER BY started_at DESC
                LIMIT %s
                """,
                (safe_limit,),
                fetch="all",
            ) or []
        return [self._row_to_dict(row) for row in rows]

    def latest(self, *, game_id: str) -> dict[str, Any] | None:
        rows = self.list_recent(game_id=game_id, limit=1)
        return rows[0] if rows else None

    def count_task_today(self, *, game_id: str, task_name: str, account_id: str = "default") -> int:
        self.ensure_schema()
        rows = self._db.execute(
            """
            SELECT tasks, dry_run
            FROM game_runs
            WHERE game_id = %s
              AND account_id = %s
              AND started_at >= date_trunc('day', NOW() AT TIME ZONE 'Asia/Shanghai') AT TIME ZONE 'Asia/Shanghai'
            """,
            (str(game_id), str(account_id)),
            fetch="all",
        ) or []
        count = 0
        for row in rows:
            if bool(row[1]):
                continue
            tasks = self._decode_json(row[0])
            if not isinstance(tasks, list):
                continue
            for task in tasks:
                if not isinstance(task, dict):
                    continue
                if task.get("name") == task_name and bool(task.get("succeeded")):
                    count += 1
        return count

    @staticmethod
    def _decode_json(value: Any) -> Any:
        if isinstance(value, str):
            return json.loads(value)
        return value

    def _row_to_dict(self, row: Any) -> dict[str, Any]:
        return {
            "id": str(row[0]),
            "game_id": str(row[1]),
            "account_id": str(row[2]),
            "started_at": row[3].isoformat() if row[3] else None,
            "finished_at": row[4].isoformat() if row[4] else None,
            "status": str(row[5]),
            "tasks": self._decode_json(row[6]) or [],
            "rewards_summary": str(row[7] or ""),
            "dry_run": bool(row[8]),
            "promoted_to_archival": bool(row[9]),
        }
