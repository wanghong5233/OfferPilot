from __future__ import annotations

import json
import uuid
from datetime import datetime, timezone
from typing import Any

from .storage.engine import DatabaseEngine
from .storage.vector import LocalVectorStore


class IntelKnowledgeStore:
    """Intel knowledge store backed by PostgreSQL + ChromaDB vector index."""

    def __init__(
        self,
        *,
        storage_path: str | None = None,
        collection_name: str = "pulse_intel_knowledge",
        db_engine: DatabaseEngine | None = None,
        vector_store: LocalVectorStore | None = None,
    ) -> None:
        _ = storage_path
        self._collection_name = str(collection_name or "pulse_intel_knowledge").strip()
        self._db = db_engine or DatabaseEngine()
        self._store = vector_store or LocalVectorStore()
        self._ensure_schema()
        self._bootstrap_vector_index()

    @property
    def storage_path(self) -> str:
        return f"pg://intel_documents (collection: {self._collection_name})"

    def _ensure_schema(self) -> None:
        self._db.execute(
            """
            CREATE TABLE IF NOT EXISTS intel_documents (
                id TEXT PRIMARY KEY,
                category TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                content TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                source_url TEXT NOT NULL DEFAULT '',
                source TEXT NOT NULL DEFAULT 'unknown',
                tags JSONB NOT NULL DEFAULT '[]'::jsonb,
                collected_at TIMESTAMPTZ NOT NULL DEFAULT NOW(),
                metadata JSONB NOT NULL DEFAULT '{}'::jsonb
            )
            """
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_intel_docs_category ON intel_documents(category)"
        )
        self._db.execute(
            "CREATE INDEX IF NOT EXISTS idx_intel_docs_collected ON intel_documents(collected_at)"
        )

    def _bootstrap_vector_index(self) -> None:
        count = self._store.collection_count(collection=self._collection_name)
        if count > 0:
            return
        rows = self._db.execute(
            "SELECT id, title, content, category FROM intel_documents ORDER BY collected_at DESC LIMIT 5000",
            fetch="all",
        )
        if not rows:
            return
        batch = []
        for row in rows:
            doc_id = str(row[0])
            title = str(row[1] or "")
            content = str(row[2] or "")
            category = str(row[3] or "")
            text = f"{title}\n{content}".strip()
            if text:
                batch.append({"id": doc_id, "text": text[:4000], "metadata": {"category": category}})
        if batch:
            self._store.upsert_texts(collection=self._collection_name, rows=batch)

    def append(self, rows: list[dict[str, Any]]) -> int:
        now_iso = datetime.now(timezone.utc).isoformat()
        inserted = 0
        vector_batch: list[dict[str, Any]] = []

        for row in rows:
            if not isinstance(row, dict):
                continue
            title = str(row.get("title") or "").strip()
            content = str(row.get("content") or "").strip()
            category = str(row.get("category") or "").strip().lower()
            if not title or not content or not category:
                continue

            tags_raw = row.get("tags")
            tags = [str(item).strip() for item in list(tags_raw or []) if str(item).strip()]
            doc_id = str(row.get("id") or uuid.uuid4().hex)
            summary = str(row.get("summary") or "").strip()
            source_url = str(row.get("source_url") or "").strip()
            source = str(row.get("source") or "").strip() or "unknown"
            collected_at = str(row.get("collected_at") or now_iso)
            metadata = dict(row.get("metadata") or {})

            self._db.execute(
                """
                INSERT INTO intel_documents(id, category, title, content, summary, source_url, source, tags, collected_at, metadata)
                VALUES (%s, %s, %s, %s, %s, %s, %s, %s::jsonb, %s::timestamptz, %s::jsonb)
                ON CONFLICT (id) DO UPDATE SET
                    content = EXCLUDED.content,
                    summary = EXCLUDED.summary,
                    metadata = EXCLUDED.metadata
                """,
                (
                    doc_id, category, title, content, summary, source_url, source,
                    json.dumps(tags, ensure_ascii=False),
                    collected_at,
                    json.dumps(metadata, ensure_ascii=False),
                ),
            )
            inserted += 1
            text = f"{title}\n{content}".strip()
            if text:
                vector_batch.append({
                    "id": doc_id, "text": text[:4000], "metadata": {"category": category},
                })

        if vector_batch:
            self._store.upsert_texts(collection=self._collection_name, rows=vector_batch)
        return inserted

    def recent(self, *, limit: int = 5000, category: str | None = None) -> list[dict[str, Any]]:
        safe_limit = max(1, min(int(limit), 20000))
        safe_category = str(category or "").strip().lower()

        if safe_category:
            rows = self._db.execute(
                "SELECT id, category, title, content, summary, source_url, source, tags, collected_at, metadata "
                "FROM intel_documents WHERE category = %s ORDER BY collected_at DESC LIMIT %s",
                (safe_category, safe_limit),
                fetch="all",
            )
        else:
            rows = self._db.execute(
                "SELECT id, category, title, content, summary, source_url, source, tags, collected_at, metadata "
                "FROM intel_documents ORDER BY collected_at DESC LIMIT %s",
                (safe_limit,),
                fetch="all",
            )
        if not rows:
            return []
        return [self._row_to_dict(r) for r in rows]

    def search(self, *, query: str, top_k: int = 10, category: str | None = None) -> list[dict[str, Any]]:
        hits = self._store.query_texts(
            collection=self._collection_name, query=query, top_k=max(1, top_k),
        )
        if not hits:
            return []
        ids = [str(h["id"]) for h in hits]
        placeholders = ",".join(["%s"] * len(ids))
        sql = f"SELECT id, category, title, content, summary, source_url, source, tags, collected_at, metadata FROM intel_documents WHERE id IN ({placeholders})"
        rows = self._db.execute(sql, tuple(ids), fetch="all")
        if not rows:
            return []
        row_map = {str(item["id"]): item for item in (self._row_to_dict(r) for r in rows)}
        safe_category = str(category or "").strip().lower()
        result: list[dict[str, Any]] = []
        for hit in hits:
            doc_id = str(hit.get("id") or "")
            item = row_map.get(doc_id)
            if item is None:
                continue
            if safe_category and item.get("category") != safe_category:
                continue
            score = float(hit.get("similarity") or 0.0)
            merged = dict(item)
            merged["score"] = round(score, 6)
            merged["similarity"] = round(score, 6)
            result.append(merged)
        return result

    @staticmethod
    def _row_to_dict(row: tuple[Any, ...]) -> dict[str, Any]:
        tags = row[7]
        if isinstance(tags, str):
            try:
                tags = json.loads(tags)
            except Exception:
                tags = []
        metadata = row[9]
        if isinstance(metadata, str):
            try:
                metadata = json.loads(metadata)
            except Exception:
                metadata = {}
        return {
            "id": str(row[0]),
            "category": str(row[1] or ""),
            "title": str(row[2] or ""),
            "content": str(row[3] or ""),
            "summary": str(row[4] or ""),
            "source_url": str(row[5] or ""),
            "source": str(row[6] or "unknown"),
            "tags": list(tags) if isinstance(tags, list) else [],
            "collected_at": str(row[8] or ""),
            "metadata": dict(metadata) if isinstance(metadata, dict) else {},
        }
