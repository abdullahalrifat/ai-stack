import os
import json
import uuid
from datetime import datetime, timezone


import redis

from qdrant_client import QdrantClient
from qdrant_client.models import (
    PointStruct,
    Distance,
    VectorParams,
)



# ============================================================
# Configuration
# ============================================================

REDIS_HOST = os.getenv(
    "REDIS_HOST",
    "redis"
)

REDIS_PORT = int(
    os.getenv(
        "REDIS_PORT",
        "6379"
    )
)


QDRANT_URL = os.getenv(
    "QDRANT_URL",
    "http://qdrant:6333"
)


COLLECTION = os.getenv(
    "QDRANT_COLLECTION",
    "agent_memory"
)



# ============================================================
# Clients
# ============================================================


redis_client = redis.Redis(

    host=REDIS_HOST,

    port=REDIS_PORT,

    decode_responses=True

)



qdrant = QdrantClient(

    url=QDRANT_URL

)



# ============================================================
# Collection Management
# ============================================================


def ensure_collection(
    vector_size: int
):

    collections = [

        c.name

        for c in qdrant.get_collections().collections

    ]


    if COLLECTION not in collections:

        qdrant.create_collection(

            collection_name=COLLECTION,

            vectors_config=VectorParams(

                size=vector_size,

                distance=Distance.COSINE

            )

        )



# ============================================================
# Short Term Memory
# ============================================================


def save_conversation(
    session_id: str,
    role: str,
    content: str
):

    if not content:

        return


    key = f"conversation:{session_id}"


    redis_client.rpush(

        key,

        json.dumps(
            {
                "role": role,
                "content": content
            }
        )

    )



def get_conversation(
    session_id: str,
    limit: int = 20
):

    key = f"conversation:{session_id}"


    messages = redis_client.lrange(

        key,

        -limit,

        -1

    )


    return [

        json.loads(message)

        for message in messages

    ]



# ============================================================
# Long Term Memory
# ============================================================


def save_long_term_memory(
    text: str,
    embedding: list,
    metadata=None
):


    ensure_collection(

        len(embedding)

    )


    point_id = str(

        uuid.uuid4()

    )


    payload = {

        "text": text,

        "created_at":
            datetime.now(
                timezone.utc
            ).isoformat(),

        **(metadata or {})

    }



    qdrant.upsert(

        collection_name=COLLECTION,

        points=[

            PointStruct(

                id=point_id,

                vector=embedding,

                payload=payload

            )

        ]

    )


    return point_id




def search_long_term_memory(
    embedding: list,
    limit: int = 5
):


    collections = [

        c.name

        for c in qdrant.get_collections().collections

    ]


    # No memories stored yet

    if COLLECTION not in collections:

        return []



    results = qdrant.query_points(

        collection_name=COLLECTION,

        query=embedding,

        limit=limit,

    )



    return [

        {

            "memory":
                point.payload,

            "score":
                point.score

        }

        for point in results.points

    ]