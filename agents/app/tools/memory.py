import os
import json
import uuid

import redis

from qdrant_client import QdrantClient
from qdrant_client.models import PointStruct


# Redis for short-term memory

redis_client = redis.Redis(
    host="redis",
    port=6379,
    decode_responses=True
)


# Qdrant for long-term semantic memory

qdrant = QdrantClient(
    url=os.getenv(
        "QDRANT_URL",
        "http://qdrant:6333"
    )
)


MEMORY_COLLECTION = "agent_memory"


def save_conversation(
    session_id: str,
    role: str,
    content: str
):
    """
    Store current conversation.
    """

    key = f"conversation:{session_id}"

    message = {
        "role": role,
        "content": content
    }

    redis_client.rpush(
        key,
        json.dumps(message)
    )


def get_conversation(
    session_id: str,
    limit=20
):
    """
    Retrieve recent messages.
    """

    key = f"conversation:{session_id}"

    messages = redis_client.lrange(
        key,
        -limit,
        -1
    )

    return [
        json.loads(m)
        for m in messages
    ]



def save_long_term_memory(
    text: str,
    embedding: list,
    metadata: dict
):
    """
    Save permanent memory.
    """

    memory_id = str(uuid.uuid4())


    qdrant.upsert(

        collection_name=MEMORY_COLLECTION,

        points=[
            PointStruct(
                id=memory_id,
                vector=embedding,
                payload={
                    "text": text,
                    **metadata
                }
            )
        ]
    )


    return memory_id



def search_long_term_memory(
    embedding:list,
    limit=5
):
    """
    Retrieve related memories.
    """

    results = qdrant.search(

        collection_name=MEMORY_COLLECTION,

        query_vector=embedding,

        limit=limit
    )


    return [
        {
            "memory":r.payload,
            "score":r.score
        }

        for r in results
    ]