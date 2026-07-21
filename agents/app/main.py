from datetime import datetime
from typing import Dict, List, Optional

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from app.agent import chat, get_embeddings, ingest_documents, load_conversation, search_long_term_memory

app = FastAPI()


class ChatRequest(BaseModel):
    message: str
    conversation_id: Optional[str] = None


class IngestRequest(BaseModel):
    documents: List[str]
    metadata: Optional[Dict[str, str]] = None


class MemoryQuery(BaseModel):
    query: str
    top_k: Optional[int] = 4


class OpenAIChatMessage(BaseModel):
    role: str
    content: str


class OpenAIChatCompletionRequest(BaseModel):
    model: Optional[str] = None
    messages: List[OpenAIChatMessage]
    temperature: Optional[float] = 0.0
    max_tokens: Optional[int] = None
    n: Optional[int] = 1
    conversation_id: Optional[str] = None


class OpenAIEmbeddingRequest(BaseModel):
    model: Optional[str] = None
    input: List[str] | str


@app.get("/")
def health():
    return {"status": "running"}


@app.post("/chat")
async def api_chat(request: ChatRequest):
    try:
        answer = chat(request.message, request.conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))
    return {
        "answer": answer,
        "conversation_id": request.conversation_id or "default",
    }


@app.post("/v1/chat/completions")
async def openai_chat_completions(
    request: OpenAIChatCompletionRequest,
    x_conversation_id: Optional[str] = Header(None),
    conversation_id: Optional[str] = None,
):
    if not request.messages:
        raise HTTPException(status_code=400, detail="`messages` is required for chat completions.")

    conversation_id = conversation_id or x_conversation_id or request.conversation_id
    prompt_lines = []
    for message in request.messages:
        role = message.role.lower()
        if role == "assistant":
            prompt_lines.append(f"Assistant: {message.content}")
        elif role == "system":
            prompt_lines.append(f"System: {message.content}")
        else:
            prompt_lines.append(f"User: {message.content}")

    prompt = "\n".join(prompt_lines)
    try:
        answer = chat(prompt, conversation_id)
    except ValueError as exc:
        raise HTTPException(status_code=400, detail=str(exc))

    return {
        "id": f"chatcmpl-{datetime.utcnow().timestamp():.0f}",
        "object": "chat.completion",
        "created": int(datetime.utcnow().timestamp()),
        "model": request.model or "qwen3-8b",
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": answer},
                "finish_reason": "stop",
            }
        ],
        "usage": {"prompt_tokens": 0, "completion_tokens": 0, "total_tokens": 0},
    }


@app.post("/v1/embeddings")
async def openai_embeddings(request: OpenAIEmbeddingRequest):
    inputs = request.input
    if isinstance(inputs, str):
        inputs = [inputs]

    vectors = get_embeddings().embed_documents(inputs)
    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": vector, "index": index}
            for index, vector in enumerate(vectors)
        ],
        "model": request.model or "embedding",
    }


@app.get("/v1/models")
async def openai_models():
    return {
        "object": "list",
        "data": [
            {"id": "qwen3-8b", "object": "model", "owned_by": "local"},
            {"id": "embedding", "object": "model", "owned_by": "local"},
        ],
    }


@app.post("/ingest")
async def api_ingest(request: IngestRequest):
    if not request.documents:
        raise HTTPException(status_code=400, detail="No documents provided for ingestion.")
    result = ingest_documents(request.documents, request.metadata)
    return result


@app.get("/conversation/{conversation_id}")
async def get_conversation(conversation_id: str):
    return {"conversation_id": conversation_id, "history": load_conversation(conversation_id)}


@app.post("/memory/search")
async def api_search_memory(request: MemoryQuery):
    return {"query": request.query, "results": search_long_term_memory(request.query, top_k=request.top_k)}