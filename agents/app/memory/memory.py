import json
import os
import re
import uuid
from datetime import datetime, timezone
from functools import lru_cache

import redis
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchText,
    MatchValue,
    PayloadSchemaType,
)

from .embeddings import create_embedding

# =====================================================
# Configuration
# =====================================================


REDIS_HOST = os.getenv("REDIS_HOST", "redis")


REDIS_PORT = int(os.getenv("REDIS_PORT", "6379"))


QDRANT_URL = os.getenv("QDRANT_URL", "http://qdrant:6333")


COLLECTION = os.getenv("QDRANT_COLLECTION", "agent_memory")


# =====================================================
# Clients
# =====================================================


redis_client = redis.Redis(host=REDIS_HOST, port=REDIS_PORT, decode_responses=True)


qdrant = QdrantClient(url=QDRANT_URL)


# =====================================================
# Qdrant Collection
# =====================================================


@lru_cache(maxsize=1)
def _collection_exists() -> bool:
    return any(c.name == COLLECTION for c in qdrant.get_collections().collections)


def ensure_collection(vector_size: int):

    if not _collection_exists():
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )
        _collection_exists.cache_clear()

    _ensure_scope_index()


@lru_cache(maxsize=1)
def _ensure_scope_index() -> None:
    """Create server-side scope and lexical indexes when supported."""

    for field_name, field_schema in (
        ("scope", PayloadSchemaType.KEYWORD),
        ("text", PayloadSchemaType.TEXT),
    ):
        try:
            qdrant.create_payload_index(
                collection_name=COLLECTION,
                field_name=field_name,
                field_schema=field_schema,
            )
        except Exception:
            # Existing indexes and older Qdrant versions remain compatible;
            # retrieval falls back to paginated payload scoring.
            continue


# =====================================================
# Conversation Memory
# =====================================================


def save_conversation(session_id: str, role: str, content: str):

    if not content:
        return

    key = f"conversation:{session_id}"

    redis_client.rpush(key, json.dumps({"role": role, "content": content}))

    # keep last 50 messages

    redis_client.ltrim(key, -50, -1)


def get_conversation(session_id: str, limit: int = 20):

    key = f"conversation:{session_id}"

    messages = redis_client.lrange(key, -limit, -1)

    return [json.loads(item) for item in messages]


# =====================================================
# Long Term Vector Memory
# =====================================================


def save_long_term_memory(text: str, embedding: list[float], metadata=None):

    ensure_collection(len(embedding))

    point_id = str(uuid.uuid4())

    payload = {
        "text": text,
        "created_at": datetime.now(timezone.utc).isoformat(),
        **(metadata or {}),
    }

    qdrant.upsert(
        collection_name=COLLECTION,
        points=[PointStruct(id=point_id, vector=embedding, payload=payload)],
    )

    return point_id


def _citation(payload: dict) -> str | None:
    source = payload.get("source")
    if not source:
        return None
    location = payload.get("location")
    return f"Document: {source}{f', {location}' if location else ''}"


def _terms(text: str) -> list[str]:
    return re.findall(r"[a-z0-9][a-z0-9_.-]{1,}", text.lower())


def _lexical_score(query: str, payload: dict) -> float:
    """BM25-like exact-match score that works without a second search service."""
    query_terms = _terms(query)
    if not query_terms:
        return 0.0
    haystack = " ".join(
        str(payload.get(key, ""))
        for key in ("text", "source", "location", "headings", "sheet_name")
    ).lower()
    tokens = _terms(haystack)
    frequencies = {term: tokens.count(term) for term in set(query_terms)}
    score = sum((frequency / (frequency + 1.2)) for frequency in frequencies.values())
    phrase = " ".join(query_terms)
    if phrase and phrase in haystack:
        score += 2.0
    return score / max(len(set(query_terms)), 1)


def _section_adjustment(query: str, payload: dict) -> float:
    """Prefer primary/current sections unless the user explicitly asks for history."""
    query_lower = query.lower()
    wants_history = bool(re.search(r"\b(history|historical|previous|prior|archive|example)\b", query_lower))
    kind = payload.get("section_kind")
    if kind == "supplementary" and not wants_history:
        return -0.18
    if kind == "supplementary" and wants_history:
        return 0.12
    if kind == "primary":
        return 0.05
    return 0.0


def _rerank(query: str, results: list[dict], limit: int) -> list[dict]:
    """Blend semantic, lexical, and structural relevance."""
    for result in results:
        payload = result["memory"]
        semantic = float(result.get("semantic_score", result.get("score", 0)))
        lexical = float(result.get("lexical_score", _lexical_score(query, payload)))
        result["semantic_score"] = semantic
        result["lexical_score"] = lexical
        result["score"] = semantic * 0.72 + min(lexical, 2.0) * 0.28 + _section_adjustment(query, payload)
        result["citation"] = _citation(result["memory"])
    return sorted(results, key=lambda item: item["score"], reverse=True)[:limit]


def search_long_term_memory(embedding: list[float], limit: int = 5, scope: str | None = None):

    if not _collection_exists():
        return []

    query_filter = Filter(must=[FieldCondition(key="scope", match=MatchValue(value=scope))]) if scope else None
    result = qdrant.query_points(
        collection_name=COLLECTION,
        query=embedding,
        limit=min(max(limit * 3, limit), 30),
        query_filter=query_filter,
    )

    return [
        {"id": str(item.id), "memory": item.payload, "score": item.score, "semantic_score": item.score}
        for item in result.points
    ]


def _scoped_payloads(scope: str | None, limit: int = 2_000) -> list[dict]:
    if not _collection_exists():
        return []
    query_filter = Filter(must=[FieldCondition(key="scope", match=MatchValue(value=scope))]) if scope else None
    results = []
    offset = None
    while len(results) < limit:
        points, next_offset = qdrant.scroll(
            collection_name=COLLECTION,
            scroll_filter=query_filter,
            limit=min(256, limit - len(results)),
            offset=offset,
            with_payload=True,
            with_vectors=False,
        )
        results.extend(
            {"id": str(point.id), "memory": point.payload, "score": 0.0}
            for point in points
        )
        if next_offset is None or not points:
            break
        offset = next_offset
    return results


def _lexical_payloads(
    query: str, scope: str | None, limit: int = 100
) -> list[dict]:
    """Use Qdrant's text index instead of scanning an arbitrary first page."""

    conditions = [FieldCondition(key="text", match=MatchText(text=query))]
    if scope:
        conditions.insert(
            0, FieldCondition(key="scope", match=MatchValue(value=scope))
        )
    points, _ = qdrant.scroll(
        collection_name=COLLECTION,
        scroll_filter=Filter(must=conditions),
        limit=limit,
        with_payload=True,
        with_vectors=False,
    )
    return [
        {"id": str(point.id), "memory": point.payload, "score": 0.0}
        for point in points
    ]


# =====================================================
# Agent Friendly Wrappers
# =====================================================


def search_memory(query: str, limit: int = 5, scope: str | None = None):
    """Hybrid retrieval with semantic recall, lexical recall, and section context."""
    embedding = create_embedding(query)
    semantic = search_long_term_memory(embedding, max(limit, 8), scope)
    try:
        lexical_pool = _lexical_payloads(query, scope)
    except Exception:
        lexical_pool = _scoped_payloads(scope)

    merged: dict[str, dict] = {}
    for result in semantic:
        key = result.get("id") or str(result["memory"].get("text", ""))
        merged[key] = result
    for result in lexical_pool:
        result["lexical_score"] = _lexical_score(query, result["memory"])
        if result["lexical_score"] <= 0:
            continue
        key = result.get("id") or str(result["memory"].get("text", ""))
        if key in merged:
            merged[key]["lexical_score"] = result["lexical_score"]
        else:
            merged[key] = result

    ranked = _rerank(query, list(merged.values()), max(limit, 8))
    # Pull adjacent chunks from the selected page/sheet so table coverage is
    # not determined by the one chunk with the strongest vector score.
    selected_sections = {
        (item["memory"].get("source"), item["memory"].get("location"))
        for item in ranked[:limit]
        if item["memory"].get("source")
    }
    adjacency_pool = _scoped_payloads(scope) if selected_sections else []
    for result in adjacency_pool:
        payload = result["memory"]
        if (payload.get("source"), payload.get("location")) not in selected_sections:
            continue
        key = result.get("id") or str(payload.get("text", ""))
        if key not in merged:
            result["lexical_score"] = _lexical_score(query, payload)
            merged[key] = result
    return _rerank(query, list(merged.values()), max(limit, min(limit * 3, 15)))


def memory_context(results: list[dict], token_budget: int = 1_200) -> list[dict]:
    """Keep retrieved evidence within a prompt budget and retain citations."""
    remaining = token_budget * 4  # conservative chars-to-token approximation
    selected = []
    for result in results:
        payload = dict(result.get("memory", {}))
        text = str(payload.get("text", ""))
        if not text or remaining <= 0:
            continue
        excerpt = text[:remaining]
        selected.append(
            {
                "citation": result.get("citation") or _citation(payload),
                "score": round(float(result.get("score", 0)), 3),
                "provenance": {
                    key: payload.get(key)
                    for key in (
                        "source",
                        "location",
                        "page_number",
                        "sheet_name",
                        "headings",
                        "section_kind",
                        "document_index",
                        "chunk_index",
                        "quality_score",
                        "extraction_status",
                        "needs_ocr",
                        "row_count",
                        "table_row_count",
                        "row_start",
                        "row_end",
                    )
                    if payload.get(key) not in (None, "")
                },
                "excerpt": excerpt,
            }
        )
        remaining -= len(excerpt)
    return selected


def save_memory(question: str, answer: str, scope: str | None = None):

    text = f"""

Question:

{question}


Answer:

{answer}

"""

    embedding = create_embedding(text)

    metadata = {
        "type": "conversation",
        "generated": True,
    }
    if scope:
        metadata["scope"] = scope
    return save_long_term_memory(text, embedding, metadata)


def clear_memory():

    qdrant.delete_collection(COLLECTION)
    _collection_exists.cache_clear()
    _ensure_scope_index.cache_clear()
