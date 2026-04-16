from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from ..storage.engine import DatabaseEngine


class DPOCollector:
    """DPO preference pair collector backed by PostgreSQL corrections table.

    Stores correction/preference data per architecture spec section 6.3,
    enabling future DPO fine-tuning and preference learning.
    """

    def __init__(
        self,
        *,
        storage_path: str = "",
        db_engine: DatabaseEngine | None = None,
    ) -> None:
        _ = storage_path
        self._db = db_engine or DatabaseEngine()
        self._ensure_schema()

    def _ensure_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS corrections (
                id TEXT PRIMARY KEY,
                session_id TEXT,
                user_text TEXT NOT NULL,
                assistant_text TEXT,
                correction_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_corrections_session_created_at ON corrections(session_id, created_at DESC)"
        )

    def add_pair(
        self,
        *,
        prompt: str,
        chosen: str,
        rejected: str,
        metadata: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        safe_prompt = str(prompt or "").strip()
        safe_chosen = str(chosen or "").strip()
        safe_rejected = str(rejected or "").strip()
        if not safe_prompt or not safe_chosen or not safe_rejected:
            raise ValueError("prompt/chosen/rejected must be non-empty")

        pair_id = f"dpo_{uuid.uuid4().hex[:14]}"
        now = datetime.now(timezone.utc).isoformat()
        safe_metadata = dict(metadata or {})

        correction_json = {
            "type": "dpo_pair",
            "prompt": safe_prompt,
            "chosen": safe_chosen,
            "rejected": safe_rejected,
            "metadata": safe_metadata,
            "timestamp": now,
        }
        session_id = str(safe_metadata.get("session_id") or "").strip() or None

        self._db.execute(
            """
            INSERT INTO corrections(id, session_id, user_text, assistant_text, correction_json, created_at)
            VALUES (%s, %s, %s, %s, %s::jsonb, NOW())
            """,
            (
                pair_id,
                session_id,
                safe_prompt,
                safe_rejected,
                json.dumps(correction_json, ensure_ascii=False),
            ),
        )
        return {
            "pair_id": pair_id,
            "timestamp": now,
            "prompt": safe_prompt,
            "chosen": safe_chosen,
            "rejected": safe_rejected,
            "metadata": safe_metadata,
        }

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 200))
        rows = self._db.execute(
            "SELECT id, session_id, user_text, assistant_text, correction_json, created_at "
            "FROM corrections ORDER BY created_at DESC LIMIT %s",
            (safe_limit,),
            fetch="all",
        )
        if not rows:
            return []
        result: list[dict[str, Any]] = []
        for row in rows:
            cj = row[4]
            if isinstance(cj, str):
                try:
                    cj = json.loads(cj)
                except Exception:
                    cj = {}
            if not isinstance(cj, dict):
                cj = {}
            result.append({
                "pair_id": str(row[0]),
                "session_id": str(row[1] or ""),
                "prompt": str(cj.get("prompt") or row[2] or ""),
                "chosen": str(cj.get("chosen") or ""),
                "rejected": str(cj.get("rejected") or row[3] or ""),
                "timestamp": str(row[5] or ""),
                "metadata": dict(cj.get("metadata") or {}),
            })
        return result

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(1) FROM corrections", fetch="one")
        if not row:
            return 0
        return int(row[0] or 0)
