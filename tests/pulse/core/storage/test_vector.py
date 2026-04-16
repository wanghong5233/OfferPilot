from __future__ import annotations

import math
from typing import Any

from pulse.core.storage.vector import LocalVectorStore, chunk_text_blocks, deterministic_embedding


class _FakeCollection:
    def __init__(self) -> None:
        self.count_value = 0
        self.upsert_payload: dict[str, Any] | None = None
        self.query_payload: dict[str, Any] | None = None
        self.query_result: dict[str, Any] = {
            "ids": [[]],
            "documents": [[]],
            "metadatas": [[]],
            "distances": [[]],
        }

    def count(self) -> int:
        return self.count_value

    def upsert(self, **kwargs: Any) -> None:
        self.upsert_payload = kwargs
        self.count_value = len(kwargs.get("ids", []))

    def query(self, **kwargs: Any) -> dict[str, Any]:
        self.query_payload = kwargs
        return self.query_result


def test_deterministic_embedding_is_normalized() -> None:
    vector = deterministic_embedding("hello world", embedding_dim=128)
    norm = math.sqrt(sum(v * v for v in vector))
    assert len(vector) == 128
    assert 0.999 <= norm <= 1.001


def test_chunk_text_blocks_adds_overlap() -> None:
    text = "A" * 40 + "\n\n" + "B" * 40 + "\n\n" + "C" * 40
    chunks = chunk_text_blocks(text, chunk_size=55, overlap=8)
    assert len(chunks) >= 2
    assert chunks[1].startswith("A" * 8) or chunks[1].startswith("B" * 8)


def test_upsert_texts_builds_vector_payload() -> None:
    fake_collection = _FakeCollection()
    store = LocalVectorStore(collection_factory=lambda _: fake_collection, embedding_dim=64)
    store.upsert_texts(
        collection="demo",
        rows=[
            {"id": "1", "text": "alpha", "metadata": {"source": "x"}},
            {"id": "2", "text": "beta", "metadata": {"source": "y"}},
        ],
    )
    assert fake_collection.upsert_payload is not None
    payload = fake_collection.upsert_payload
    assert payload["ids"] == ["1", "2"]
    assert len(payload["embeddings"]) == 2
    assert len(payload["embeddings"][0]) == 64
    assert payload["metadatas"][0]["source"] == "x"


def test_upsert_texts_sanitizes_empty_and_nested_metadata() -> None:
    fake_collection = _FakeCollection()
    store = LocalVectorStore(collection_factory=lambda _: fake_collection, embedding_dim=64)
    store.upsert_texts(
        collection="demo",
        rows=[
            {
                "id": "1",
                "text": "alpha",
                "metadata": {
                    "used_tools": [],
                    "route_hint": {"target": "hello"},
                    "count": 3,
                },
            }
        ],
    )
    assert fake_collection.upsert_payload is not None
    metadata = fake_collection.upsert_payload["metadatas"][0]
    assert "used_tools" not in metadata
    assert metadata["route_hint"] == '{"target": "hello"}'
    assert metadata["count"] == 3


def test_query_texts_filters_by_similarity() -> None:
    fake_collection = _FakeCollection()
    fake_collection.count_value = 2
    fake_collection.query_result = {
        "ids": [["id-a", "id-b"]],
        "documents": [["doc-a", "doc-b"]],
        "metadatas": [[{"k": 1}, {"k": 2}]],
        "distances": [[0.2, 0.7]],
    }
    store = LocalVectorStore(collection_factory=lambda _: fake_collection, embedding_dim=32)
    rows = store.query_texts(collection="demo", query="alpha", top_k=5, min_similarity=0.5)
    assert rows == [
        {"id": "id-a", "text": "doc-a", "metadata": {"k": 1}, "similarity": 0.8},
    ]
