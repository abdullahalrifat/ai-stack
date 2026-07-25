import asyncio
import hmac
import json
import logging
import os
import threading
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
from fastapi.responses import StreamingResponse
from fastapi.security import APIKeyHeader
from pydantic import BaseModel

# =====================================================
# IMPORTANT
# Load tools before registry usage
# =====================================================
import app.tools.register
from app.agent import (
    approve_run,
    discard_run,
    execute_run,
    ingest_documents,
    run_agent,
)
from app.config import (
    AGENT_API_KEY,
    AGENT_MODEL_ID,
    ALLOW_INSECURE_NO_AUTH,
    DEFAULT_MODEL,
    POSTGRES_URL,
    WORKSPACE_ROOTS,
    validate_settings,
)
from app.memory.embeddings import create_embedding
from app.memory.memory import (
    get_conversation,
    search_memory,
)
from app.planner import create_plan
from app.run_store import get_run_store
from app.state import AgentState
from app.tool_registry import registry
from app.tools.filesystem import list_files

logger = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)


# =====================================================
# Application
# =====================================================


@asynccontextmanager
async def lifespan(app: FastAPI):
    validate_settings()
    logger.info("REGISTERED TOOLS: %s", registry.list_tools())
    logger.info("WORKSPACE ROOTS: %s", [str(r) for r in WORKSPACE_ROOTS])

    if POSTGRES_URL:
        get_run_store().initialize()
        logger.info("Durable run store initialized.")
    else:
        logger.warning(
            "POSTGRES_URL not set; /runs endpoints are unavailable "
            "(use /chat or /execute for single-response calls)."
        )

    yield


app = FastAPI(
    title="Local AI Engineering Agent",
    description="Private autonomous coding agent running in homelab",
    version="3.0",
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

    # Constant-time comparison to avoid a timing side-channel on the API key.
    if not hmac.compare_digest(token, API_KEY):
        raise HTTPException(status_code=401, detail="Invalid API key")

    return True


def require_run_store():
    if not POSTGRES_URL:
        raise HTTPException(
            503,
            "Durable runs require POSTGRES_URL to be configured for this service.",
        )


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


class RunRequest(BaseModel):
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
        "workspace_roots": [str(r) for r in WORKSPACE_ROOTS],
        "workspace": os.listdir("/workspace") if os.path.exists("/workspace") else [],
    }


# =====================================================
# Agent Chat (single blocking response, no sandboxing)
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
# Durable, streamed, sandbox-isolated runs
# =====================================================
#
# Unlike /chat and /execute, these endpoints return immediately with a run
# id. Progress can be streamed via /runs/{id}/events (Server-Sent Events),
# and write-enabled runs execute in a disposable Git worktree so the diff can
# be reviewed and explicitly approved or discarded before it touches the
# real repository.
#


@app.post("/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def create_run(request: RunRequest):
    if not request.task.strip():
        raise HTTPException(400, "task cannot be empty")

    store = get_run_store()
    run_id = await run_in_threadpool(
        store.create_run,
        task=request.task,
        model=request.model or DEFAULT_MODEL,
        workspace=request.workspace or "/workspace",
        conversation_id=request.conversation_id,
        allow_write=request.allow_write,
    )

    thread = threading.Thread(target=execute_run, args=(run_id,), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "queued"}


@app.get("/runs/{run_id}", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def get_run(run_id: str):
    store = get_run_store()
    run = await run_in_threadpool(store.get_run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@app.get(
    "/runs/{run_id}/events",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def run_events(run_id: str, after: int = 0):
    """Server-Sent Events stream of a run's progress.

    Emits one `data:` line per event as JSON, polling the durable event log
    until the run reaches a terminal status (completed, awaiting_approval,
    failed, or discarded).
    """
    store = get_run_store()

    run = await run_in_threadpool(store.get_run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")

    terminal_statuses = {"completed", "awaiting_approval", "failed", "discarded"}

    async def event_stream():
        last_id = after
        while True:
            events = await run_in_threadpool(store.events_after, run_id, last_id)
            for event in events:
                last_id = event["id"]
                payload = {
                    "id": event["id"],
                    "event_type": event["event_type"],
                    "payload": event["payload"],
                    "created_at": event["created_at"].isoformat()
                    if isinstance(event["created_at"], datetime)
                    else event["created_at"],
                }
                yield f"data: {json.dumps(payload, default=str)}\n\n"

            current = await run_in_threadpool(store.get_run, run_id)
            if current is not None and current["status"] in terminal_statuses and not events:
                yield f"data: {json.dumps({'event_type': 'stream_closed', 'status': current['status']})}\n\n"
                break

            await asyncio.sleep(1)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@app.post(
    "/runs/{run_id}/approve",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def approve(run_id: str):
    try:
        return await run_in_threadpool(approve_run, run_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception("Failed to approve run %s", run_id)
        raise HTTPException(500, f"Could not merge changes: {e}") from e


@app.post(
    "/runs/{run_id}/discard",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def discard(run_id: str):
    try:
        return await run_in_threadpool(discard_run, run_id)
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception("Failed to discard run %s", run_id)
        raise HTTPException(500, f"Could not discard sandbox: {e}") from e


# =====================================================
# OpenAI Compatible API
# =====================================================


@app.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def openai_chat(
    request: OpenAIChatCompletionRequest, x_conversation_id: str | None = Header(None)
):

    if request.stream:
        raise HTTPException(400, "Streaming not supported on this endpoint; use /runs/{id}/events for streamed progress.")

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


@app.get("/workspace/roots", dependencies=[Depends(verify_api_key)])
def workspace_roots():
    """List every directory the agent is permitted to operate in."""
    return {"roots": [str(r) for r in WORKSPACE_ROOTS]}