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
    """Create the scoped-retrieval index once per process when supported."""
    try:
        qdrant.create_payload_index(
            collection_name=COLLECTION,
            field_name="scope",
            field_schema=PayloadSchemaType.KEYWORD,
        )
    except Exception:
        # Existing collections/indexes and older Qdrant versions remain
        # compatible; retrieval still works without this optimization.
        pass


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


def _rerank(query: str, results: list[dict], limit: int) -> list[dict]:
    """Blend vector similarity with a small lexical signal for exact facts."""
    terms = set(re.findall(r"[a-z0-9]{3,}", query.lower()))
    for result in results:
        text = str(result["memory"].get("text", "")).lower()
        overlap = len(terms.intersection(re.findall(r"[a-z0-9]{3,}", text)))
        result["score"] = float(result["score"]) + min(overlap, 8) * 0.05
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

    return [{"memory": item.payload, "score": item.score} for item in result.points]


# =====================================================
# Agent Friendly Wrappers
# =====================================================


def search_memory(query: str, limit: int = 5, scope: str | None = None):

    embedding = create_embedding(query)

    return _rerank(query, search_long_term_memory(embedding, limit, scope), limit)


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
                "excerpt": excerpt,
            }
        )
        remaining -= len(excerpt)
    return selected


def save_memory(question: str, answer: str):

    text = f"""

Question:

{question}


Answer:

{answer}

"""

    embedding = create_embedding(text)

    return save_long_term_memory(text, embedding, {"type": "conversation"})


def clear_memory():

    qdrant.delete_collection(COLLECTION)
    _collection_exists.cache_clear()
    _ensure_scope_index.cache_clear()
