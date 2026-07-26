import json
import os
import uuid
from datetime import datetime, timezone

import redis
from qdrant_client import QdrantClient
from qdrant_client.models import (
    Distance,
    PointStruct,
    VectorParams,
    Filter,
    FieldCondition,
    MatchValue,
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


def ensure_collection(vector_size: int):

    existing = [c.name for c in qdrant.get_collections().collections]

    if COLLECTION not in existing:
        qdrant.create_collection(
            collection_name=COLLECTION,
            vectors_config=VectorParams(size=vector_size, distance=Distance.COSINE),
        )


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


def search_long_term_memory(embedding: list[float], limit: int = 5, scope: str | None = None):

    collections = [c.name for c in qdrant.get_collections().collections]

    if COLLECTION not in collections:
        return []

    query_filter = Filter(must=[FieldCondition(key="scope", match=MatchValue(value=scope))]) if scope else None
    result = qdrant.query_points(collection_name=COLLECTION, query=embedding, limit=limit, query_filter=query_filter)

    return [{"memory": item.payload, "score": item.score} for item in result.points]


# =====================================================
# Agent Friendly Wrappers
# =====================================================


def search_memory(query: str, limit: int = 5, scope: str | None = None):

    embedding = create_embedding(query)

    return search_long_term_memory(embedding, limit, scope)


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
