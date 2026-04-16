from __future__ import annotations

from datetime import datetime
from typing import Any


class FakeVectorStore:
    def __init__(self, *, hit_score: float = 0.95, miss_score: float = 0.4) -> None:
        self._hit_score = float(hit_score)
        self._miss_score = float(miss_score)
        self.data: dict[str, dict[str, dict[str, Any]]] = {}

    def collection_count(self, *, collection: str) -> int:
        return len(self.data.get(collection, {}))

    def upsert_texts(self, *, collection: str, rows: list[dict[str, Any]]) -> None:
        bucket = self.data.setdefault(collection, {})
        for row in rows:
            bucket[str(row["id"])] = {
                "text": str(row["text"]),
                "metadata": dict(row.get("metadata") or {}),
            }

    def query_texts(
        self,
        *,
        collection: str,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        bucket = self.data.get(collection, {})
        q = str(query or "").lower()
        items: list[dict[str, Any]] = []
        for row_id, payload in bucket.items():
            text = str(payload["text"])
            score = self._hit_score if q and q in text.lower() else self._miss_score
            if score < min_similarity:
                continue
            items.append(
                {
                    "id": row_id,
                    "text": text,
                    "metadata": dict(payload["metadata"]),
                    "similarity": score,
                }
            )
        items.sort(key=lambda item: item["similarity"], reverse=True)
        return items[: max(1, int(top_k))]


class FakeRecallDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self.tool_calls: list[dict[str, Any]] = []

    def execute(self, sql, params=None, *, fetch="none", commit=True):  # noqa: ANN001
        _ = commit, fetch
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
        if normalized.startswith("insert into tool_calls"):
            (
                row_id,
                conversation_id,
                session_id,
                tool_name,
                tool_args,
                tool_result,
                status,
                latency_ms,
            ) = params
            self.tool_calls.append(
                {
                    "id": row_id,
                    "conversation_id": conversation_id,
                    "session_id": session_id,
                    "tool_name": tool_name,
                    "tool_args": tool_args,
                    "tool_result": tool_result,
                    "status": status,
                    "latency_ms": latency_ms,
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


class FakeArchivalDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
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


class FakeCorrectionsDB:
    def __init__(self) -> None:
        self.rows: list[dict[str, Any]] = []
        self._sequence = 0

    def execute(self, sql, params=None, *, fetch="none", commit=True):  # noqa: ANN001
        _ = fetch, commit
        normalized = " ".join(str(sql).lower().split())
        if normalized.startswith("create table") or normalized.startswith("create index"):
            return None
        if normalized.startswith("insert into corrections"):
            row_id, session_id, user_text, assistant_text, correction_json = params
            self._sequence += 1
            self.rows.append(
                {
                    "id": row_id,
                    "session_id": session_id,
                    "user_text": user_text,
                    "assistant_text": assistant_text,
                    "correction_json": correction_json,
                    "created_at": datetime.utcnow(),
                    "created_seq": self._sequence,
                }
            )
            return None
        if "from corrections order by created_at desc" in normalized:
            limit = int(params[0])
            ordered = sorted(self.rows, key=lambda item: item["created_seq"], reverse=True)[:limit]
            return [
                (
                    item["id"],
                    item["session_id"],
                    item["user_text"],
                    item["assistant_text"],
                    item["correction_json"],
                    item["created_at"],
                )
                for item in ordered
            ]
        if normalized.startswith("select count(1) from corrections"):
            return (len(self.rows),)
        raise AssertionError(f"unexpected SQL: {sql}")
