import logging
import os
from contextlib import asynccontextmanager
from datetime import datetime, timezone
from typing import Any

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

# =====================================================
# IMPORTANT
# Load tools before registry usage
# =====================================================
from app.agent import (
    ingest_documents,
    run_agent,
)
from app.config import (
    AGENT_API_KEY,
    AGENT_MODEL_ID,
    ALLOW_INSECURE_NO_AUTH,
    DEFAULT_MODEL,
    validate_settings,
)
from app.memory.embeddings import create_embedding
from app.memory.memory import (
    get_conversation,
    search_memory,
)
from app.planner import create_plan
from app.state import AgentState
from app.tool_registry import registry
from app.tools.filesystem import list_files

logger = logging.getLogger(__name__)


# =====================================================
# Application
# =====================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings()
    print("REGISTERED TOOLS:", registry.list_tools())

    yield


app = FastAPI(
    title="Local AI Engineering Agent",
    description="Private autonomous coding agent running in homelab",
    version="2.0",
    lifespan=lifespan,
)


# =====================================================
# Authentication
# =====================================================


API_KEY = AGENT_API_KEY


api_key_header = APIKeyHeader(name="Authorization", auto_error=False)


def verify_api_key(key: str | None = Security(api_key_header)):

    if not API_KEY and ALLOW_INSECURE_NO_AUTH:
        return True

    if not API_KEY:
        raise HTTPException(
            status_code=503, detail="Agent authentication is not configured"
        )

    if not key:
        raise HTTPException(status_code=401, detail="Missing authorization header")

    token = key.replace("Bearer ", "")

    if token != API_KEY:
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


# =====================================================
# Models
# =====================================================


class ChatRequest(BaseModel):
    message: str

    conversation_id: str | None = None

    workspace: str | None = "/workspace"

    model: str | None = None
    allow_write: bool = False


class PlanRequest(BaseModel):
    message: str

    conversation_id: str | None = None

    workspace: str | None = "/workspace"


class ExecuteRequest(BaseModel):
    task: str

    conversation_id: str | None = None

    workspace: str | None = "/workspace"

    model: str | None = None
    allow_write: bool = False


class IngestRequest(BaseModel):
    documents: list[str]

    metadata: dict[str, Any] | None = None


class MemoryQuery(BaseModel):
    query: str

    top_k: int = 5


class OpenAIChatMessage(BaseModel):
    role: str

    content: str


class OpenAIChatCompletionRequest(BaseModel):
    model: str | None = None

    messages: list[OpenAIChatMessage]

    temperature: float | None = 0

    max_tokens: int | None = None

    stream: bool | None = False

    workspace: str | None = "/workspace"

    conversation_id: str | None = None

    allow_write: bool = False


class OpenAIEmbeddingRequest(BaseModel):
    model: str | None = None

    input: list[str] | str


# =====================================================
# Health
# =====================================================


@app.get("/")
@app.get("/health")
def health():

    return {"status": "running", "service": "local-ai-agent"}


# =====================================================
# Debug / Tools
# =====================================================


@app.get("/tools", dependencies=[Depends(verify_api_key)])
def tools():

    return {"tools": registry.list_tools()}


@app.get("/debug/tools")
def debug_tools():

    return {
        "count": len(registry.list_tools()),
        "tools": registry.list_tools(),
        "workspace": os.listdir("/workspace") if os.path.exists("/workspace") else [],
    }


# =====================================================
# Agent Chat
# =====================================================


@app.post("/chat", dependencies=[Depends(verify_api_key)])
async def chat(request: ChatRequest):

    if not request.message.strip():
        raise HTTPException(400, "Message cannot be empty")

    try:
        return await run_in_threadpool(
            run_agent,
            request.message,
            request.conversation_id,
            request.workspace,
            request.model or DEFAULT_MODEL,
            request.allow_write,
        )

    except Exception as e:
        logger.exception("Agent chat failed")
        raise HTTPException(
            500, "Agent request failed. Check service logs for details."
        ) from e


# =====================================================
# Execute Task
# =====================================================


@app.post("/execute", dependencies=[Depends(verify_api_key)])
async def execute(request: ExecuteRequest):

    return await run_in_threadpool(
        run_agent,
        request.task,
        request.conversation_id,
        request.workspace,
        request.model or DEFAULT_MODEL,
        request.allow_write,
    )


# =====================================================
# OpenAI Compatible API
# =====================================================


@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def openai_chat(
    request: OpenAIChatCompletionRequest, x_conversation_id: str | None = Header(None)
):

    if request.stream:
        raise HTTPException(400, "Streaming not supported yet")

    if request.model and request.model != AGENT_MODEL_ID:
        raise HTTPException(
            400, f"This endpoint only serves the '{AGENT_MODEL_ID}' model"
        )

    prompt = "\n".join(f"{m.role}: {m.content}" for m in request.messages)

    result = await run_in_threadpool(
        run_agent,
        prompt,
        x_conversation_id or request.conversation_id or "default",
        request.workspace,
        DEFAULT_MODEL,
        request.allow_write,
    )

    created = int(datetime.now(timezone.utc).timestamp())

    return {
        "id": f"chatcmpl-{created}",
        "object": "chat.completion",
        "created": created,
        "model": request.model or AGENT_MODEL_ID,
        "choices": [
            {
                "index": 0,
                "message": {"role": "assistant", "content": result["answer"]},
                "finish_reason": "stop",
            }
        ],
    }


# =====================================================
# Models
# =====================================================


@app.get("/v1/models", dependencies=[Depends(verify_api_key)])
@app.get("/v1/models")
def models():

    return {
        "object": "list",
        "data": [
            {"id": AGENT_MODEL_ID, "object": "model", "owned_by": "ai-stack-agent"}
        ],
    }


@app.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
def model_detail(model_id: str):

    return {"id": model_id, "object": "model", "owned_by": "local"}


# =====================================================
# Embeddings
# =====================================================


@app.post("/v1/embeddings", dependencies=[Depends(verify_api_key)])
async def embeddings(request: OpenAIEmbeddingRequest):

    inputs = request.input

    if isinstance(inputs, str):
        inputs = [inputs]

    vectors = []

    for text in inputs:
        vectors.append(await run_in_threadpool(create_embedding, text))

    return {
        "object": "list",
        "data": [
            {"object": "embedding", "embedding": v, "index": i}
            for i, v in enumerate(vectors)
        ],
        "model": request.model or "nomic-embed-text",
    }


# =====================================================
# Planning
# =====================================================


@app.post("/plan", dependencies=[Depends(verify_api_key)])
async def plan(request: PlanRequest):

    state = AgentState(
        conversation_id=request.conversation_id or "default",
        user_message=request.message,
        workspace=request.workspace,
    )

    result = await run_in_threadpool(create_plan, state)

    return {"plan": result}


# =====================================================
# Ingest
# =====================================================


@app.post("/ingest", dependencies=[Depends(verify_api_key)])
async def ingest(request: IngestRequest):

    return await run_in_threadpool(
        ingest_documents, request.documents, request.metadata
    )


# =====================================================
# Memory
# =====================================================


@app.get("/conversation/{conversation_id}", dependencies=[Depends(verify_api_key)])
def conversation(conversation_id: str):

    return {
        "conversation_id": conversation_id,
        "history": get_conversation(conversation_id),
    }


@app.post("/memory/search", dependencies=[Depends(verify_api_key)])
async def memory_search(request: MemoryQuery):

    return {
        "query": request.query,
        "results": await run_in_threadpool(search_memory, request.query),
    }


# =====================================================
# Workspace
# =====================================================


@app.get("/workspace/tree", dependencies=[Depends(verify_api_key)])
def workspace_tree(path: str = "/workspace"):

    return {"workspace": path, "files": list_files(path)}
