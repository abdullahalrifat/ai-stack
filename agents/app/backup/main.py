import os
from datetime import datetime, timezone
from typing import Dict, List, Optional

from fastapi import (
    Depends,
    FastAPI,
    Header,
    HTTPException,
    Security,
)

from fastapi.concurrency import run_in_threadpool
from fastapi.security import APIKeyHeader
from pydantic import BaseModel


from agents.agent import (
    chat,
    ingest_documents,
)


from agents.app.backup.memory.memory import (
    get_conversation as fetch_conversation,
    search_long_term_memory,
)


from agents.app.backup.memory.embeddings import (
    create_embedding,
)



app = FastAPI(
    title="Local AI Agent API",
    version="1.0"
)



# ============================================================
# Authentication
# ============================================================

API_KEY = os.getenv(
    "AGENT_API_KEY"
)


api_key_header = APIKeyHeader(
    name="Authorization",
    auto_error=False,
)



def verify_api_key(
    key: Optional[str] = Security(api_key_header)
):

    if not API_KEY:
        return True


    if not key:
        raise HTTPException(
            status_code=401,
            detail="Missing authorization header"
        )


    token = key.replace(
        "Bearer ",
        ""
    )


    if token != API_KEY:
        raise HTTPException(
            status_code=401,
            detail="Invalid API key"
        )


    return True



# ============================================================
# Models
# ============================================================


class ChatRequest(BaseModel):

    message: str

    conversation_id: Optional[str] = None



class IngestRequest(BaseModel):

    documents: List[str]

    metadata: Optional[
        Dict[str,str]
    ] = None



class MemoryQuery(BaseModel):

    query: str

    top_k: int = 4



class OpenAIChatMessage(BaseModel):

    role: str

    content: str



class OpenAIChatCompletionRequest(BaseModel):

    model: Optional[str] = None

    messages: List[OpenAIChatMessage]

    temperature: Optional[float] = 0

    max_tokens: Optional[int] = None

    stream: Optional[bool] = False

    conversation_id: Optional[str] = None



class OpenAIEmbeddingRequest(BaseModel):

    model: Optional[str] = None

    input: List[str] | str



# ============================================================
# Health
# ============================================================


@app.get("/")
def health():

    return {
        "status": "running"
    }



# ============================================================
# Chat API
# ============================================================


@app.post(
    "/chat",
    dependencies=[
        Depends(verify_api_key)
    ]
)
async def api_chat(
    request: ChatRequest
):

    if not request.message.strip():

        raise HTTPException(
            status_code=400,
            detail="Message cannot be empty"
        )


    answer = await run_in_threadpool(
        chat,
        request.message,
        request.conversation_id,
    )


    return {

        "answer": answer,

        "conversation_id":
            request.conversation_id
            or "default"

    }



# ============================================================
# OpenAI Compatible Chat
# ============================================================


@app.post(
    "/v1/chat/completions",
    dependencies=[
        Depends(verify_api_key)
    ]
)
async def openai_chat_completions(
    request: OpenAIChatCompletionRequest,
    x_conversation_id: Optional[str] = Header(None),
):


    if not request.messages:

        raise HTTPException(
            status_code=400,
            detail="messages required"
        )


    conversation_id = (

        x_conversation_id

        or request.conversation_id

        or "default"

    )


    prompt = "\n".join(

        [
            f"{m.role}: {m.content}"

            for m in request.messages

        ]

    )


    answer = await run_in_threadpool(

        chat,

        prompt,

        conversation_id,

    )



    created = int(
        datetime.now(
            timezone.utc
        ).timestamp()
    )



    return {

        "id":
            f"chatcmpl-{created}",

        "object":
            "chat.completion",

        "created":
            created,

        "model":
            request.model
            or "qwen3-8b",

        "choices":

        [

            {

                "index":0,

                "message":
                {

                    "role":
                        "assistant",

                    "content":
                        answer,

                },

                "finish_reason":
                    "stop"

            }

        ]

    }



# ============================================================
# Embeddings API
# ============================================================


@app.post(
    "/v1/embeddings",
    dependencies=[
        Depends(verify_api_key)
    ]
)
async def openai_embeddings(
    request: OpenAIEmbeddingRequest
):


    inputs = request.input


    if isinstance(inputs,str):

        inputs = [
            inputs
        ]



    vectors=[]


    for text in inputs:

        vector = await run_in_threadpool(

            create_embedding,

            text

        )

        vectors.append(
            vector
        )



    return {

        "object":
            "list",

        "data":

        [

            {

                "object":
                    "embedding",

                "embedding":
                    vector,

                "index":
                    index,

            }

            for index,vector
            in enumerate(vectors)

        ],

        "model":
            request.model
            or "nomic-embed-text"

    }



# ============================================================
# Models
# ============================================================


@app.get(
    "/v1/models",
    dependencies=[
        Depends(verify_api_key)
    ]
)
def openai_models():

    return {

        "object":
            "list",

        "data":

        [

            {

                "id":
                    "qwen3-8b",

                "object":
                    "model",

                "owned_by":
                    "local"

            },

            {

                "id":
                    "nomic-embed-text",

                "object":
                    "model",

                "owned_by":
                    "local"

            }

        ]

    }



# ============================================================
# Ingest
# ============================================================


@app.post(
    "/ingest",
    dependencies=[
        Depends(verify_api_key)
    ]
)
async def api_ingest(
    request: IngestRequest
):


    return await run_in_threadpool(

        ingest_documents,

        request.documents,

        request.metadata,

    )



# ============================================================
# Conversation
# ============================================================


@app.get(
    "/conversation/{conversation_id}",
    dependencies=[
        Depends(verify_api_key)
    ]
)
def conversation_history(
    conversation_id:str
):

    return {

        "conversation_id":
            conversation_id,

        "history":
            fetch_conversation(
                conversation_id
            )

    }



# ============================================================
# Memory Search
# ============================================================


@app.post(
    "/memory/search",
    dependencies=[
        Depends(verify_api_key)
    ]
)
async def api_search_memory(
    request: MemoryQuery
):


    embedding = await run_in_threadpool(

        create_embedding,

        request.query,

    )



    result = await run_in_threadpool(

        search_long_term_memory,

        embedding,

        request.top_k,

    )



    return {

        "query":
            request.query,

        "results":
            result

    }