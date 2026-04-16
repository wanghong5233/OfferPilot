from __future__ import annotations

import json
from datetime import datetime, timezone
from typing import Any

from ..storage.engine import DatabaseEngine
from ..storage.vector import LocalVectorStore
from .envelope import MemoryEnvelope, MemoryKind


def _utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _parse_metadata(raw: Any) -> dict[str, Any]:
    if isinstance(raw, dict):
        return dict(raw)
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return {}
        try:
            parsed = json.loads(text)
        except Exception:
            return {}
        return dict(parsed) if isinstance(parsed, dict) else {}
    return {}


def _normalize_object_text(value: Any) -> str:
    if isinstance(value, str):
        return value.strip()
    return json.dumps(value, ensure_ascii=False)


class ArchivalMemory:
    """Archival memory backed by PostgreSQL facts + Chroma semantic index."""

    def __init__(
        self,
        *,
        storage_path: str | None = None,
        collection_name: str = "pulse_archival_memory",
        db_engine: DatabaseEngine | None = None,
        vector_store: LocalVectorStore | None = None,
    ) -> None:
        _ = storage_path
        self._collection_name = str(collection_name or "pulse_archival_memory").strip()
        self._db = db_engine or DatabaseEngine()
        self._store = vector_store or LocalVectorStore()
        self._ensure_schema()
        self._bootstrap_vector_index()

    def _ensure_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS facts (
                id BIGSERIAL PRIMARY KEY,
                subject TEXT NOT NULL,
                predicate TEXT NOT NULL,
                "object" TEXT NOT NULL,
                object_json JSONB,
                metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
                valid_from TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                valid_to TIMESTAMPTZ,
                confidence DOUBLE PRECISION NOT NULL DEFAULT 1.0,
                source TEXT,
                superseded_by BIGINT REFERENCES facts(id),
                evidence_refs JSONB NOT NULL DEFAULT '[]'::jsonb,
                promoted_from TEXT,
                promotion_reason TEXT,
                task_id TEXT,
                run_id TEXT,
                workspace_id TEXT,
                created_at TIMESTAMPTZ NOT NULL DEFAULT NOW()
            )
            """
        )
        self._migrate_add_columns()
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_subject_predicate ON facts(subject, predicate)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_created_at ON facts(created_at DESC)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_valid_from ON facts(valid_from DESC)")
        self._db.execute("CREATE INDEX IF NOT EXISTS idx_facts_task_id ON facts(task_id)")

    def _migrate_add_columns(self) -> None:
        """Idempotent migration: add P0 columns to existing facts table."""
        for col, col_type in [
            ("evidence_refs", "JSONB NOT NULL DEFAULT '[]'::jsonb"),
            ("promoted_from", "TEXT"),
            ("promotion_reason", "TEXT"),
            ("task_id", "TEXT"),
            ("run_id", "TEXT"),
            ("workspace_id", "TEXT"),
        ]:
            self._db.execute(
                f"ALTER TABLE facts ADD COLUMN IF NOT EXISTS {col} {col_type}"  # noqa: S608
            )

    def _bootstrap_vector_index(self) -> None:
        if self._store.collection_count(collection=self._collection_name) > 0:
            return
        rows = self._db.execute(
            """
            SELECT id, subject, predicate, object, source, confidence, valid_from
            FROM facts
            ORDER BY created_at ASC
            """,
            fetch="all",
        ) or []
        upsert_rows: list[dict[str, Any]] = []
        for fact_id, subject, predicate, object_text, source, confidence, valid_from in rows:
            upsert_rows.append(
                {
                    "id": f"fact:{int(fact_id)}",
                    "text": f"{str(subject or '')} {str(predicate or '')} {str(object_text or '')}",
                    "metadata": {
                        "fact_id": int(fact_id),
                        "subject": str(subject or ""),
                        "predicate": str(predicate or ""),
                        "source": str(source or ""),
                        "confidence": float(confidence or 0.0),
                        "timestamp": str(valid_from.isoformat() if hasattr(valid_from, "isoformat") else valid_from or ""),
                    },
                }
            )
        if upsert_rows:
            self._store.upsert_texts(collection=self._collection_name, rows=upsert_rows)

    def add_fact(
        self,
        *,
        subject: str,
        predicate: str,
        object_value: Any,
        source: str,
        confidence: float = 0.8,
        metadata: dict[str, Any] | None = None,
        evidence_refs: list[str] | None = None,
        promoted_from: str | None = None,
        promotion_reason: str | None = None,
        task_id: str | None = None,
        run_id: str | None = None,
        workspace_id: str | None = None,
    ) -> dict[str, Any]:
        safe_subject = str(subject or "").strip()
        safe_predicate = str(predicate or "").strip()
        if not safe_subject or not safe_predicate:
            raise ValueError("subject and predicate are required")
        safe_source = str(source or "").strip()
        safe_confidence = max(0.0, min(float(confidence), 1.0))
        timestamp = _utc_now_iso()
        metadata_json = dict(metadata or {})
        object_text = _normalize_object_text(object_value)
        safe_evidence = list(evidence_refs or [])
        row = self._db.execute(
            """
            INSERT INTO facts(
                subject, predicate, "object", object_json, metadata_json, valid_from,
                confidence, source, evidence_refs, promoted_from, promotion_reason,
                task_id, run_id, workspace_id
            )
            VALUES (%s, %s, %s, %s::jsonb, %s::jsonb, %s::timestamptz,
                    %s, %s, %s::jsonb, %s, %s, %s, %s, %s)
            RETURNING id, valid_from
            """,
            (
                safe_subject,
                safe_predicate,
                object_text,
                json.dumps(object_value, ensure_ascii=False),
                json.dumps(metadata_json, ensure_ascii=False),
                timestamp,
                safe_confidence,
                safe_source,
                json.dumps(safe_evidence),
                promoted_from,
                promotion_reason,
                task_id,
                run_id,
                workspace_id,
            ),
            fetch="one",
        )
        if not row:
            raise RuntimeError("failed to insert fact")
        fact_id = int(row[0])
        valid_from = row[1]
        valid_from_text = str(valid_from.isoformat() if hasattr(valid_from, "isoformat") else valid_from or timestamp)
        self._store.upsert_texts(
            collection=self._collection_name,
            rows=[
                {
                    "id": f"fact:{fact_id}",
                    "text": f"{safe_subject} {safe_predicate} {object_text}",
                    "metadata": {
                        "fact_id": fact_id,
                        "subject": safe_subject,
                        "predicate": safe_predicate,
                        "source": safe_source,
                        "confidence": safe_confidence,
                        "timestamp": valid_from_text,
                    },
                }
            ],
        )
        return {
            "id": fact_id,
            "timestamp": valid_from_text,
            "subject": safe_subject,
            "predicate": safe_predicate,
            "object": object_value,
            "source": safe_source,
            "confidence": safe_confidence,
            "metadata": metadata_json,
        }

    def recent(self, *, limit: int = 20) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 500))
        rows = self._db.execute(
            """
            SELECT id, subject, predicate, object, source, confidence, metadata_json, valid_from
            FROM facts
            ORDER BY created_at DESC
            LIMIT %s
            """,
            (safe_limit,),
            fetch="all",
        ) or []
        return [
            {
                "id": int(fact_id),
                "timestamp": str(valid_from.isoformat() if hasattr(valid_from, "isoformat") else valid_from or ""),
                "subject": str(subject or ""),
                "predicate": str(predicate or ""),
                "object": obj,
                "source": str(source or ""),
                "confidence": float(confidence or 0.0),
                "metadata": _parse_metadata(metadata_raw),
            }
            for fact_id, subject, predicate, obj, source, confidence, metadata_raw, valid_from in rows
        ]

    def _fetch_facts_by_ids(self, ids: list[int]) -> dict[int, dict[str, Any]]:
        if not ids:
            return {}
        placeholders = ", ".join(["%s"] * len(ids))
        rows = self._db.execute(
            f"""
            SELECT id, subject, predicate, object, source, confidence, metadata_json, valid_from
            FROM facts
            WHERE id IN ({placeholders})
            """,
            tuple(ids),
            fetch="all",
        ) or []
        result: dict[int, dict[str, Any]] = {}
        for fact_id, subject, predicate, obj, source, confidence, metadata_raw, valid_from in rows:
            key = int(fact_id)
            result[key] = {
                "id": key,
                "timestamp": str(valid_from.isoformat() if hasattr(valid_from, "isoformat") else valid_from or ""),
                "subject": str(subject or ""),
                "predicate": str(predicate or ""),
                "object": obj,
                "source": str(source or ""),
                "confidence": float(confidence or 0.0),
                "metadata": _parse_metadata(metadata_raw),
            }
        return result

    def query(
        self,
        *,
        subject: str | None = None,
        predicate: str | None = None,
        keyword: str | None = None,
        limit: int = 30,
    ) -> list[dict[str, Any]]:
        safe_subject = str(subject or "").strip()
        safe_predicate = str(predicate or "").strip()
        safe_keyword = str(keyword or "").strip()
        safe_limit = max(1, min(int(limit), 300))

        if safe_keyword:
            hits = self._store.query_texts(
                collection=self._collection_name,
                query=safe_keyword,
                top_k=max(safe_limit * 4, 20),
                min_similarity=0.0,
            )
            ordered_ids: list[int] = []
            similarity_map: dict[int, float] = {}
            for hit in hits:
                metadata = dict(hit.get("metadata") or {})
                if safe_subject and str(metadata.get("subject") or "") != safe_subject:
                    continue
                if safe_predicate and str(metadata.get("predicate") or "") != safe_predicate:
                    continue
                fact_id_raw = metadata.get("fact_id")
                if fact_id_raw is None:
                    continue
                fact_id = int(fact_id_raw)
                if fact_id in similarity_map:
                    continue
                similarity_map[fact_id] = float(hit.get("similarity") or 0.0)
                ordered_ids.append(fact_id)
                if len(ordered_ids) >= safe_limit:
                    break
            rows_map = self._fetch_facts_by_ids(ordered_ids)
            items: list[dict[str, Any]] = []
            for fact_id in ordered_ids:
                item = rows_map.get(fact_id)
                if not item:
                    continue
                item["similarity"] = similarity_map.get(fact_id, 0.0)
                items.append(item)
            return items

        sql = """
            SELECT id, subject, predicate, object, source, confidence, metadata_json, valid_from
            FROM facts
            WHERE 1=1
        """
        params: list[Any] = []
        if safe_subject:
            sql += " AND subject = %s"
            params.append(safe_subject)
        if safe_predicate:
            sql += " AND predicate = %s"
            params.append(safe_predicate)
        sql += " ORDER BY created_at DESC LIMIT %s"
        params.append(safe_limit)
        rows = self._db.execute(sql, tuple(params), fetch="all") or []
        return [
            {
                "id": int(fact_id),
                "timestamp": str(valid_from.isoformat() if hasattr(valid_from, "isoformat") else valid_from or ""),
                "subject": str(safe_subject_value or ""),
                "predicate": str(safe_predicate_value or ""),
                "object": obj,
                "source": str(source_value or ""),
                "confidence": float(confidence_value or 0.0),
                "metadata": _parse_metadata(metadata_raw),
            }
            for (
                fact_id,
                safe_subject_value,
                safe_predicate_value,
                obj,
                source_value,
                confidence_value,
                metadata_raw,
                valid_from,
            ) in rows
        ]

    def count(self) -> int:
        row = self._db.execute("SELECT COUNT(1) FROM facts", fetch="one")
        if not row:
            return 0
        return int(row[0] or 0)

    # -- Envelope-based write -----------------------------------------------

    def store_envelope(self, envelope: MemoryEnvelope) -> dict[str, Any]:
        """Write a MemoryEnvelope to archival storage.

        Expects envelope.kind == MemoryKind.fact with content containing
        subject/predicate/object keys.
        """
        c = envelope.content
        return self.add_fact(
            subject=str(c.get("subject", "")),
            predicate=str(c.get("predicate", "")),
            object_value=c.get("object", ""),
            source=envelope.source or "envelope",
            confidence=envelope.confidence,
            metadata={"envelope_id": envelope.memory_id, "scope": envelope.scope.value},
            evidence_refs=envelope.evidence_refs,
            promoted_from=envelope.promoted_from,
            promotion_reason=envelope.promotion_reason,
            task_id=envelope.task_id or None,
            run_id=envelope.run_id or None,
            workspace_id=envelope.workspace_id or None,
        )

    def supersede_fact(self, *, old_fact_id: str | int, new_fact_id: str | int) -> bool:
        """标记旧 fact 被新 fact 取代 (§9.4 Step 5)。

        设置 old_fact 的 superseded_by 字段和 valid_to 时间戳。
        """
        self._db.execute(
            """
            UPDATE facts
            SET superseded_by = %s,
                valid_to = NOW()
            WHERE id = %s AND superseded_by IS NULL
            """,
            (int(new_fact_id) if str(new_fact_id).isdigit() else None, int(old_fact_id)),
        )
        return True
