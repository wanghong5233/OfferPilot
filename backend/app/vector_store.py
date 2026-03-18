from __future__ import annotations

import hashlib
import logging
import math
import os
import re
from typing import Any

import chromadb

from .schemas import SimilarJob

logger = logging.getLogger(__name__)

_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


def _embed_dim() -> int:
    return int(os.getenv("LOCAL_EMBED_DIM", "256"))


def _chroma_dir() -> str:
    return os.getenv("CHROMA_DIR", "./chroma_db")


def _chroma_collection() -> str:
    return os.getenv("CHROMA_JD_HISTORY_COLLECTION", "jd_history")


def _resume_collection() -> str:
    return os.getenv("CHROMA_RESUME_COLLECTION", "resume_chunks")


def _jd_similarity_min() -> float:
    raw = os.getenv("JD_HISTORY_MIN_SIMILARITY", "0.35")
    try:
        value = float(raw)
    except ValueError:
        value = 0.35
    return max(0.0, min(1.0, value))


def _normalize_dedupe_key(value: str) -> str:
    return re.sub(r"\s+", " ", value.strip().lower())


def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_PATTERN.findall(text.lower())
    if tokens:
        return tokens
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < 2:
        return [cleaned] if cleaned else []
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def _deterministic_embedding(text: str) -> list[float]:
    dim = _embed_dim()
    vec = [0.0] * dim
    for token in _tokenize(text):
        digest = hashlib.sha256(token.encode("utf-8")).digest()
        idx = int.from_bytes(digest[:4], "little") % dim
        sign = 1.0 if digest[4] % 2 == 0 else -1.0
        weight = 1.0 + (digest[5] / 255.0)
        vec[idx] += sign * weight

    norm = math.sqrt(sum(v * v for v in vec))
    if norm == 0:
        return vec
    return [v / norm for v in vec]


def _collection(name: str):
    client = chromadb.PersistentClient(path=_chroma_dir())
    return client.get_or_create_collection(
        name=name,
        metadata={"hnsw:space": "cosine"},
    )


def upsert_jd_history(
    *,
    doc_id: str,
    jd_text: str,
    title: str,
    company: str,
    match_score: float,
) -> None:
    try:
        coll = _collection(_chroma_collection())
        coll.upsert(
            ids=[doc_id],
            embeddings=[_deterministic_embedding(jd_text)],
            documents=[jd_text[:8000]],
            metadatas=[
                {
                    "title": title[:300],
                    "company": company[:300],
                    "match_score": float(match_score),
                }
            ],
        )
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        logger.warning("Upsert jd_history failed: %s", exc)


def query_similar_jds(jd_text: str, top_k: int = 3) -> list[SimilarJob]:
    try:
        coll = _collection(_chroma_collection())
        # Fast path: empty collection
        total = coll.count()
        if total == 0:
            return []

        requested = max(1, top_k)
        # Query wider than top_k so threshold filtering and de-dup still keep enough candidates.
        n_results = min(total, max(requested, requested * 4))
        result: dict[str, Any] = coll.query(
            query_embeddings=[_deterministic_embedding(jd_text)],
            n_results=n_results,
            include=["metadatas", "distances"],
        )

        metadatas = (result.get("metadatas") or [[]])[0]
        distances = (result.get("distances") or [[]])[0]

        min_similarity = _jd_similarity_min()
        deduped: dict[str, SimilarJob] = {}
        for meta, dist in zip(metadatas, distances):
            if not isinstance(meta, dict):
                continue
            distance = float(dist if dist is not None else 1.0)
            similarity = max(0.0, min(1.0, 1.0 - distance))
            if similarity < min_similarity:
                continue

            title = str(meta.get("title") or "Unknown Title")
            company = str(meta.get("company") or "Unknown Company")
            row = SimilarJob(
                title=title,
                company=company,
                similarity=round(similarity, 3),
                match_score=(
                    float(meta["match_score"])
                    if meta.get("match_score") is not None
                    else None
                ),
            )
            key = f"{_normalize_dedupe_key(title)}::{_normalize_dedupe_key(company)}"
            existing = deduped.get(key)
            if existing is None or row.similarity > existing.similarity:
                deduped[key] = row

        rows = sorted(deduped.values(), key=lambda item: item.similarity, reverse=True)
        return rows[:requested]
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        logger.warning("Query jd_history failed: %s", exc)
        return []


def _chunk_resume_text(text: str, chunk_size: int = 500, overlap: int = 80) -> list[str]:
    paragraphs = [p.strip() for p in text.split("\n\n") if p.strip()]
    if not paragraphs:
        return []

    chunks: list[str] = []
    current = ""
    for para in paragraphs:
        if len(current) + len(para) + 2 <= chunk_size:
            current = f"{current}\n\n{para}".strip()
            continue

        if current:
            chunks.append(current)
        current = para

    if current:
        chunks.append(current)

    # Apply overlap by stitching tail to next chunk for better retrieval continuity.
    if overlap <= 0 or len(chunks) <= 1:
        return chunks

    overlapped: list[str] = [chunks[0]]
    for i in range(1, len(chunks)):
        prefix = chunks[i - 1][-overlap:]
        overlapped.append((prefix + "\n" + chunks[i]).strip())
    return overlapped


def index_resume_text(resume_text: str, source_id: str = "manual") -> int:
    chunks = _chunk_resume_text(resume_text)
    if not chunks:
        return 0

    ids: list[str] = []
    docs: list[str] = []
    embs: list[list[float]] = []
    metas: list[dict[str, Any]] = []
    for idx, chunk in enumerate(chunks):
        raw_id = f"{source_id}:{idx}:{chunk[:80]}"
        chunk_id = hashlib.sha256(raw_id.encode("utf-8")).hexdigest()
        ids.append(chunk_id)
        docs.append(chunk[:8000])
        embs.append(_deterministic_embedding(chunk))
        metas.append(
            {
                "source_id": source_id[:120],
                "chunk_index": int(idx),
                "chunk_type": "generic",
            }
        )

    try:
        coll = _collection(_resume_collection())
        # Remove previous chunks for the same source for deterministic refresh.
        coll.delete(where={"source_id": source_id[:120]})
        coll.upsert(ids=ids, documents=docs, embeddings=embs, metadatas=metas)
        return len(chunks)
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        logger.warning("Index resume text failed: %s", exc)
        return 0


def retrieve_resume_context(
    queries: list[str],
    *,
    top_k_per_query: int = 2,
    max_chunks: int = 6,
) -> list[str]:
    if not queries:
        return []
    try:
        coll = _collection(_resume_collection())
        if coll.count() == 0:
            return []

        unique: dict[str, None] = {}
        for query in queries:
            result: dict[str, Any] = coll.query(
                query_embeddings=[_deterministic_embedding(query)],
                n_results=max(1, top_k_per_query),
                include=["documents"],
            )
            docs = (result.get("documents") or [[]])[0]
            for doc in docs:
                if isinstance(doc, str) and doc.strip():
                    unique[doc.strip()] = None
                if len(unique) >= max_chunks:
                    return list(unique.keys())[:max_chunks]
        return list(unique.keys())[:max_chunks]
    except Exception as exc:  # pragma: no cover - runtime environment dependent
        logger.warning("Retrieve resume context failed: %s", exc)
        return []
