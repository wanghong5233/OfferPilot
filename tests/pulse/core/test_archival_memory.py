from __future__ import annotations

from datetime import datetime

from pulse.core.memory import ArchivalMemory


class _FakeArchivalDB:
    def __init__(self) -> None:
        self.rows: list[dict] = []
        self._next_id = 1

    def execute(self, sql, params=None, *, fetch="none", commit=True):  # noqa: ANN001
        _ = fetch, commit
        normalized = " ".join(str(sql).lower().split())
        if normalized.startswith("create table") or normalized.startswith("create index"):
            return None
        if normalized.startswith("insert into facts"):
            (
                subject,
                predicate,
                object_text,
                object_json,
                metadata_json,
                valid_from,
                confidence,
                source,
            ) = params
            row = {
                "id": self._next_id,
                "subject": subject,
                "predicate": predicate,
                "object": object_text,
                "object_json": object_json,
                "metadata_json": metadata_json,
                "valid_from": datetime.fromisoformat(str(valid_from).replace("z", "+00:00")),
                "confidence": float(confidence),
                "source": source,
                "created_at": datetime.fromisoformat(str(valid_from).replace("z", "+00:00")),
            }
            self._next_id += 1
            self.rows.append(row)
            return (row["id"], row["valid_from"])
        if "from facts order by created_at asc" in normalized:
            ordered = sorted(self.rows, key=lambda item: item["created_at"])
            return [
                (
                    item["id"],
                    item["subject"],
                    item["predicate"],
                    item["object"],
                    item["source"],
                    item["confidence"],
                    item["valid_from"],
                )
                for item in ordered
            ]
        if "from facts where id in" in normalized:
            id_values = {int(v) for v in params}
            selected = [item for item in self.rows if int(item["id"]) in id_values]
            return [
                (
                    item["id"],
                    item["subject"],
                    item["predicate"],
                    item["object"],
                    item["source"],
                    item["confidence"],
                    item["metadata_json"],
                    item["valid_from"],
                )
                for item in selected
            ]
        if "from facts where 1=1" in normalized:
            data = list(self.rows)
            if "and subject = %s" in normalized:
                subject = params[0]
                data = [item for item in data if item["subject"] == subject]
            if "and predicate = %s" in normalized:
                predicate = params[1 if "and subject = %s" in normalized else 0]
                data = [item for item in data if item["predicate"] == predicate]
            limit = int(params[-1])
            data = sorted(data, key=lambda item: item["created_at"], reverse=True)[:limit]
            return [
                (
                    item["id"],
                    item["subject"],
                    item["predicate"],
                    item["object"],
                    item["source"],
                    item["confidence"],
                    item["metadata_json"],
                    item["valid_from"],
                )
                for item in data
            ]
        if "from facts order by created_at desc" in normalized:
            limit = int(params[0])
            data = sorted(self.rows, key=lambda item: item["created_at"], reverse=True)[:limit]
            return [
                (
                    item["id"],
                    item["subject"],
                    item["predicate"],
                    item["object"],
                    item["source"],
                    item["confidence"],
                    item["metadata_json"],
                    item["valid_from"],
                )
                for item in data
            ]
        if normalized.startswith("select count(1) from facts"):
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
        q = str(query or "").lower()
        items: list[dict] = []
        for row_id, payload in bucket.items():
            score = 0.96 if q in str(payload["text"]).lower() else 0.35
            if score < min_similarity:
                continue
            items.append(
                {
                    "id": row_id,
                    "text": str(payload["text"]),
                    "metadata": dict(payload["metadata"]),
                    "similarity": score,
                }
            )
        items.sort(key=lambda item: item["similarity"], reverse=True)
        return items[: max(1, int(top_k))]


def test_archival_memory_add_recent_and_query() -> None:
    memory = ArchivalMemory(db_engine=_FakeArchivalDB(), vector_store=_FakeVectorStore())
    memory.add_fact(
        subject="user",
        predicate="preference.default_location",
        object_value="hangzhou",
        source="unit-test",
        metadata={"session_id": "u1"},
    )
    memory.add_fact(
        subject="user",
        predicate="preference.dislike",
        object_value="outsourcing",
        source="unit-test",
        metadata={"session_id": "u1"},
    )

    recent = memory.recent(limit=10)
    assert len(recent) == 2

    rows = memory.query(subject="user", predicate="preference.default_location", limit=5)
    assert len(rows) == 1
    assert rows[0]["object"] == "hangzhou"


def test_archival_memory_keyword_query_uses_vector_index() -> None:
    db = _FakeArchivalDB()
    vector = _FakeVectorStore()
    memory = ArchivalMemory(db_engine=db, vector_store=vector)
    memory.add_fact(
        subject="user",
        predicate="preference.default_location",
        object_value="hangzhou",
        source="unit-test",
    )
    memory.add_fact(
        subject="user",
        predicate="preference.focus",
        object_value="agent engineering",
        source="unit-test",
    )
    rows = memory.query(keyword="agent engineering", limit=5)
    assert len(rows) >= 1
    assert any("agent engineering" in str(item["object"]) for item in rows)
    assert memory.count() == 2
