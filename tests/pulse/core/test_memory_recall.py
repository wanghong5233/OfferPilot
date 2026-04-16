from __future__ import annotations

from datetime import datetime

from pulse.core.memory.recall_memory import RecallMemory


class _FakeRecallDB:
    def __init__(self) -> None:
        self.rows: list[dict] = []

    def execute(self, sql, params=None, *, fetch="none", commit=True):  # noqa: ANN001
        _ = commit
        normalized = " ".join(str(sql).lower().split())
        if normalized.startswith("create table") or normalized.startswith("create index"):
            return None
        if normalized.startswith("insert into conversations"):
            row_id, role, text, metadata_json, session_id, created_at = params
            self.rows.append(
                {
                    "id": row_id,
                    "role": role,
                    "text": text,
                    "metadata_json": metadata_json,
                    "session_id": session_id,
                    "created_at": datetime.fromisoformat(str(created_at).replace("z", "+00:00")),
                }
            )
            return None
        if "from conversations order by created_at asc" in normalized:
            ordered = sorted(self.rows, key=lambda item: item["created_at"])
            return [
                (item["id"], item["role"], item["text"], item["metadata_json"], item["session_id"], item["created_at"])
                for item in ordered
            ]
        if "from conversations where session_id =" in normalized:
            session_id, limit = params
            ordered = [item for item in self.rows if item["session_id"] == session_id]
            ordered = sorted(ordered, key=lambda item: item["created_at"], reverse=True)[: int(limit)]
            return [(item["id"], item["role"], item["text"], item["metadata_json"], item["created_at"]) for item in ordered]
        if "from conversations order by created_at desc" in normalized:
            limit = params[0]
            ordered = sorted(self.rows, key=lambda item: item["created_at"], reverse=True)[: int(limit)]
            return [(item["id"], item["role"], item["text"], item["metadata_json"], item["created_at"]) for item in ordered]
        if normalized.startswith("select count(1) from conversations"):
            return (len(self.rows),)
        raise AssertionError(f"unexpected SQL: {sql}")


class _FakeVectorStore:
    def __init__(self) -> None:
        self.data: dict[str, dict[str, dict]] = {}

    def collection_count(self, *, collection: str) -> int:
        return len(self.data.get(collection, {}))

    def upsert_texts(self, *, collection: str, rows: list[dict]) -> None:
        bucket = self.data.setdefault(collection, {})
        for row in rows:
            bucket[str(row["id"])] = {"text": str(row["text"]), "metadata": dict(row.get("metadata") or {})}

    def query_texts(self, *, collection: str, query: str, top_k: int = 5, min_similarity: float = 0.0) -> list[dict]:
        bucket = self.data.get(collection, {})
        items: list[dict] = []
        q = str(query or "").lower()
        for row_id, payload in bucket.items():
            text = str(payload["text"])
            score = 0.95 if q in text.lower() else 0.4
            if score < min_similarity:
                continue
            items.append(
                {"id": row_id, "text": text, "metadata": dict(payload["metadata"]), "similarity": score}
            )
        items.sort(key=lambda item: item["similarity"], reverse=True)
        return items[: max(1, int(top_k))]


def test_recall_memory_add_recent_and_search() -> None:
    db = _FakeRecallDB()
    vector = _FakeVectorStore()
    memory = RecallMemory(db_engine=db, vector_store=vector)
    memory.add_interaction(
        user_text="我想看看杭州天气",
        assistant_text="好的，我来查杭州天气。",
        metadata={"channel": "cli"},
        session_id="s1",
    )
    memory.add_interaction(
        user_text="再查一下上海航班",
        assistant_text="好的，我来查上海航班。",
        metadata={"channel": "cli"},
        session_id="s2",
    )

    recent_s1 = memory.recent(limit=10, session_id="s1")
    assert len(recent_s1) == 2
    assert all(item["metadata"]["session_id"] == "s1" for item in recent_s1)

    hits = memory.search(query="杭州天气", top_k=3, session_id="s1")
    assert len(hits) >= 1
    assert any("杭州" in item["text"] for item in hits)
    assert memory.count() == 4


def test_recall_memory_bootstrap_vector_from_postgres_rows() -> None:
    db = _FakeRecallDB()
    vector = _FakeVectorStore()
    memory = RecallMemory(db_engine=db, vector_store=vector)
    memory.add_entry(role="user", text="记录偏好：杭州", metadata={"session_id": "s3"})
    memory.add_entry(role="assistant", text="已记录杭州偏好", metadata={"session_id": "s3"})

    restored = RecallMemory(db_engine=db, vector_store=_FakeVectorStore())
    hits = restored.search(query="杭州偏好", top_k=3, session_id="s3")
    assert len(hits) >= 1
    assert restored.count() == 2
