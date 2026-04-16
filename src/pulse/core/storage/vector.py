from __future__ import annotations

import hashlib
import json
import logging
import math
import os
import re
import urllib.error
import urllib.parse
import urllib.request
from typing import Any, Callable

logger = logging.getLogger(__name__)

CollectionFactory = Callable[[str], Any]
EmbedFn = Callable[[list[str]], list[list[float]]]
_TOKEN_PATTERN = re.compile(r"\w+", flags=re.UNICODE)


# ------------------------------------------------------------------
# Embedding providers
# ------------------------------------------------------------------

def _tokenize(text: str) -> list[str]:
    tokens = _TOKEN_PATTERN.findall(text.lower())
    if tokens:
        return tokens
    cleaned = re.sub(r"\s+", "", text)
    if len(cleaned) < 2:
        return [cleaned] if cleaned else []
    return [cleaned[i : i + 2] for i in range(len(cleaned) - 1)]


def deterministic_embedding(text: str, *, embedding_dim: int = 256) -> list[float]:
    dim = max(8, int(embedding_dim))
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


def _make_deterministic_fn(dim: int = 256) -> EmbedFn:
    def _fn(texts: list[str]) -> list[list[float]]:
        return [deterministic_embedding(t, embedding_dim=dim) for t in texts]
    return _fn


def _sanitize_metadata_value(value: Any) -> Any | None:
    if value is None:
        return None
    if isinstance(value, bool):
        return value
    if isinstance(value, (str, int, float)):
        return value
    if isinstance(value, list):
        if not value:
            return None
        sanitized_items: list[Any] = []
        item_type: type[Any] | None = None
        for item in value:
            if isinstance(item, bool):
                safe_item: Any = item
            elif isinstance(item, (str, int, float)):
                safe_item = item
            else:
                safe_item = json.dumps(item, ensure_ascii=False, default=str)
            current_type = type(safe_item)
            if item_type is None:
                item_type = current_type
            if current_type is not item_type:
                return json.dumps(value, ensure_ascii=False, default=str)
            sanitized_items.append(safe_item)
        return sanitized_items if sanitized_items else None
    if isinstance(value, dict):
        return json.dumps(value, ensure_ascii=False, default=str)
    return str(value)


def sanitize_metadata(metadata: dict[str, Any]) -> dict[str, Any]:
    safe: dict[str, Any] = {}
    for key, value in dict(metadata or {}).items():
        safe_key = str(key or "").strip()
        if not safe_key:
            continue
        sanitized = _sanitize_metadata_value(value)
        if sanitized is None:
            continue
        safe[safe_key] = sanitized
    return safe


def _make_openai_fn(
    *,
    api_key: str,
    base_url: str,
    model: str,
    timeout_sec: float = 15.0,
) -> EmbedFn:
    """OpenAI-compatible embedding API (works with Dashscope, DeepSeek, etc.)."""

    def _fn(texts: list[str]) -> list[list[float]]:
        safe_texts = [str(t or "").strip()[:8000] or " " for t in texts]
        payload = json.dumps({"model": model, "input": safe_texts}, ensure_ascii=False).encode("utf-8")
        url = f"{base_url.rstrip('/')}/embeddings"
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {api_key}",
        }
        req = urllib.request.Request(url, data=payload, headers=headers, method="POST")
        try:
            with urllib.request.urlopen(req, timeout=timeout_sec) as resp:
                body = json.loads(resp.read().decode("utf-8", errors="ignore"))
        except urllib.error.HTTPError as exc:
            detail = exc.read().decode("utf-8", errors="ignore")[:300]
            raise RuntimeError(f"Embedding API error {exc.code}: {detail}") from exc
        except urllib.error.URLError as exc:
            raise RuntimeError(f"Embedding API connection error: {exc.reason}") from exc

        data = body.get("data") if isinstance(body, dict) else None
        if not isinstance(data, list):
            raise RuntimeError(f"Unexpected embedding response: {str(body)[:300]}")
        data.sort(key=lambda d: int(d.get("index", 0)) if isinstance(d, dict) else 0)
        return [d["embedding"] for d in data if isinstance(d, dict) and "embedding" in d]

    return _fn


def _make_local_fn(model_name: str = "all-MiniLM-L6-v2") -> EmbedFn:
    """Local embedding via sentence-transformers (lazy loaded)."""
    _model: Any = None

    def _fn(texts: list[str]) -> list[list[float]]:
        nonlocal _model
        if _model is None:
            try:
                from sentence_transformers import SentenceTransformer  # type: ignore
                _model = SentenceTransformer(model_name)
            except ImportError as exc:
                raise RuntimeError(
                    "sentence-transformers not installed. "
                    "Run: pip install sentence-transformers"
                ) from exc
        embeddings = _model.encode(texts, normalize_embeddings=True)
        return [e.tolist() for e in embeddings]

    return _fn


def _resolve_embed_fn(embedding_dim: int | None = None) -> EmbedFn:
    """Build embedding function from environment configuration."""
    provider = os.getenv("PULSE_EMBEDDING_PROVIDER", "").strip().lower()

    if provider == "local":
        model = os.getenv("PULSE_EMBEDDING_MODEL", "all-MiniLM-L6-v2").strip()
        logger.info("Using local embedding: %s", model)
        return _make_local_fn(model)

    if provider == "deterministic":
        dim = int(embedding_dim or os.getenv("PULSE_EMBED_DIM", "256"))
        logger.info("Using deterministic embedding (dim=%d)", dim)
        return _make_deterministic_fn(dim)

    api_key = (
        os.getenv("PULSE_EMBEDDING_API_KEY", "").strip()
        or os.getenv("PULSE_MODEL_API_KEY", "").strip()
        or os.getenv("DASHSCOPE_API_KEY", "").strip()
        or os.getenv("QWEN_API_KEY", "").strip()
        or os.getenv("DEEPSEEK_API_KEY", "").strip()
    )
    if provider == "openai" or (not provider and api_key):
        base_url = (
            os.getenv("PULSE_EMBEDDING_BASE_URL", "").strip()
            or os.getenv("PULSE_MODEL_BASE_URL", "").strip()
            or os.getenv("OPENAI_COMPAT_BASE_URL", "").strip()
            or "https://dashscope.aliyuncs.com/compatible-mode/v1"
        )
        model = os.getenv("PULSE_EMBEDDING_MODEL", "text-embedding-v3").strip()
        if api_key:
            logger.info("Using OpenAI-compatible embedding: %s @ %s", model, base_url)
            return _make_openai_fn(api_key=api_key, base_url=base_url, model=model)

    dim = int(embedding_dim or os.getenv("PULSE_EMBED_DIM", "256"))
    logger.info("No embedding API key found, falling back to deterministic (dim=%d)", dim)
    return _make_deterministic_fn(dim)


# ------------------------------------------------------------------
# Text chunking utility
# ------------------------------------------------------------------

def chunk_text_blocks(text: str, *, chunk_size: int = 500, overlap: int = 80) -> list[str]:
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
    if overlap <= 0 or len(chunks) <= 1:
        return chunks
    overlapped = [chunks[0]]
    for index in range(1, len(chunks)):
        prefix = chunks[index - 1][-overlap:]
        overlapped.append((prefix + "\n" + chunks[index]).strip())
    return overlapped


# ------------------------------------------------------------------
# Vector Store
# ------------------------------------------------------------------

class LocalVectorStore:
    """ChromaDB-backed vector store with pluggable embedding provider."""

    def __init__(
        self,
        *,
        storage_dir: str | None = None,
        embedding_dim: int | None = None,
        collection_factory: CollectionFactory | None = None,
        embed_fn: EmbedFn | None = None,
    ) -> None:
        self._storage_dir = storage_dir or os.getenv("PULSE_CHROMA_DIR") or os.getenv("CHROMA_DIR") or "./chroma_db"
        self._embedding_dim = int(embedding_dim or os.getenv("PULSE_EMBED_DIM", "256"))
        self._collection_factory = collection_factory
        self._embed_fn = embed_fn or _resolve_embed_fn(self._embedding_dim)

    def _collection(self, name: str) -> Any:
        if self._collection_factory is not None:
            return self._collection_factory(name)
        import chromadb
        client = chromadb.PersistentClient(path=self._storage_dir)
        return client.get_or_create_collection(name=name, metadata={"hnsw:space": "cosine"})

    def upsert_texts(self, *, collection: str, rows: list[dict[str, Any]]) -> None:
        if not rows:
            return
        coll = self._collection(collection)
        ids: list[str] = []
        documents: list[str] = []
        metadatas: list[dict[str, Any]] = []

        for row in rows:
            ids.append(str(row["id"]))
            documents.append(str(row["text"])[:8000])
            metadatas.append(sanitize_metadata(dict(row.get("metadata") or {})))

        try:
            embeddings = self._embed_fn(documents)
        except Exception as exc:
            logger.warning("Embedding failed, falling back to deterministic: %s", exc)
            embeddings = [deterministic_embedding(d, embedding_dim=self._embedding_dim) for d in documents]

        coll.upsert(ids=ids, documents=documents, embeddings=embeddings, metadatas=metadatas)

    def collection_count(self, *, collection: str) -> int:
        coll = self._collection(collection)
        counter = getattr(coll, "count", None)
        if callable(counter):
            try:
                return int(counter())
            except Exception:
                return 0
        return 0

    def query_texts(
        self,
        *,
        collection: str,
        query: str,
        top_k: int = 5,
        min_similarity: float = 0.0,
    ) -> list[dict[str, Any]]:
        coll = self._collection(collection)
        if hasattr(coll, "count") and coll.count() == 0:
            return []

        try:
            query_embedding = self._embed_fn([query])[0]
        except Exception:
            query_embedding = deterministic_embedding(query, embedding_dim=self._embedding_dim)

        result: dict[str, Any] = coll.query(
            query_embeddings=[query_embedding],
            n_results=max(1, top_k),
            include=["metadatas", "distances", "documents"],
        )

        ids = (result.get("ids") or [[]])[0]
        docs = (result.get("documents") or [[]])[0]
        metas = (result.get("metadatas") or [[]])[0]
        dists = (result.get("distances") or [[]])[0]
        rows: list[dict[str, Any]] = []
        for doc_id, doc, meta, dist in zip(ids, docs, metas, dists):
            distance = float(dist if dist is not None else 1.0)
            similarity = max(0.0, min(1.0, 1.0 - distance))
            if similarity < min_similarity:
                continue
            rows.append({
                "id": str(doc_id),
                "text": str(doc or ""),
                "metadata": dict(meta or {}),
                "similarity": round(similarity, 6),
            })
        rows.sort(key=lambda row: row["similarity"], reverse=True)
        return rows[: max(1, top_k)]
