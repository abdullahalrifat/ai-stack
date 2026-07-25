"""HTTP routes for the coding agent service."""

import asyncio
import json
import logging
import os
import queue
import threading
from datetime import datetime, timezone
from typing import Any

from fastapi import APIRouter, Depends, Header, HTTPException
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

import app.tools.register
from app.agent.planner import create_plan
from app.agent.service import approve_run, discard_run, execute_run, ingest_documents, run_agent
from app.agent.state import AgentState
from app.core.config import AGENT_MODEL_ID, DEFAULT_MODEL, WORKSPACE_ROOTS
from app.llm.client import get_available_models
from app.memory.embeddings import create_embedding
from app.memory.memory import get_conversation, search_memory
from app.runs.events import get_event_publisher
from app.runs.store import get_run_store
from app.tools.filesystem import list_files, validate_workspace
from app.tools.registry import registry

from .dependencies import require_run_store, verify_api_key
from .schemas import ChatRequest, ExecuteRequest, IngestRequest, MemoryQuery, OpenAIChatCompletionRequest, OpenAIEmbeddingRequest, PlanRequest, RunRequest

logger = logging.getLogger(__name__)
router = APIRouter()


@router.get("/")
@router.get("/health")
def health():
    return {"status": "running", "service": "local-ai-agent"}


# =====================================================
# Debug / Tools
# =====================================================


@router.get("/tools", dependencies=[Depends(verify_api_key)])
def tools():
    return {"tools": registry.list_tools()}


@router.get("/debug/tools", dependencies=[Depends(verify_api_key)])
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


@router.post("/chat", dependencies=[Depends(verify_api_key)])
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


@router.post("/execute", dependencies=[Depends(verify_api_key)])
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


@router.post("/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def create_run(request: RunRequest):
    if not request.task.strip():
        raise HTTPException(400, "task cannot be empty")

    # Validate before the background worker creates a Git worktree.  Tool
    # execution also validates its workspace, but that happens too late for
    # write-enabled runs.
    try:
        workspace = str(validate_workspace(request.workspace or "/workspace"))
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc

    store = get_run_store()
    run_id = await run_in_threadpool(
        store.create_run,
        task=request.task,
        model=request.model or DEFAULT_MODEL,
        workspace=workspace,
        conversation_id=request.conversation_id,
        allow_write=request.allow_write,
    )

    thread = threading.Thread(target=execute_run, args=(run_id,), daemon=True)
    thread.start()

    return {"run_id": run_id, "status": "queued"}


@router.get("/runs/{run_id}", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def get_run(run_id: str):
    store = get_run_store()
    run = await run_in_threadpool(store.get_run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return run


@router.get("/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def list_runs(limit: int = 50):
    return {"runs": await run_in_threadpool(get_run_store().list_runs, limit)}


@router.get(
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

    terminal_statuses = {"completed", "awaiting_approval", "failed", "discarded", "cancelled"}

    async def event_stream():
        last_id = after
        subscription = None
        try:
            # Subscribe first, then replay from PostgreSQL. Any event that
            # arrives during replay carries an id and is de-duplicated below.
            try:
                subscription = await run_in_threadpool(get_event_publisher().subscribe, run_id)
            except Exception:
                logger.exception("Redis Pub/Sub unavailable; falling back to durable polling")

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

                if subscription is not None:
                    while message := await run_in_threadpool(subscription.get_message, timeout=0):
                        try:
                            event = json.loads(message["data"])
                        except (KeyError, TypeError, json.JSONDecodeError):
                            continue
                        if event.get("id", 0) <= last_id:
                            continue
                        last_id = event["id"]
                        event["created_at"] = str(event.get("created_at", ""))
                        yield f"data: {json.dumps(event, default=str)}\n\n"

                current = await run_in_threadpool(store.get_run, run_id)
                if current is not None and current["status"] in terminal_statuses and not events:
                    yield f"data: {json.dumps({'event_type': 'stream_closed', 'status': current['status']})}\n\n"
                    break

                # Pub/Sub handles the common case in tens of milliseconds;
                # durable polling remains the recovery path.
                await asyncio.sleep(0.10 if subscription is not None else 1)
        finally:
            if subscription is not None:
                await run_in_threadpool(subscription.close)

    return StreamingResponse(event_stream(), media_type="text/event-stream")


@router.post(
    "/runs/{run_id}/cancel",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def cancel(run_id: str):
    store = get_run_store()
    changed = await run_in_threadpool(store.request_cancel, run_id)
    if not changed:
        run = await run_in_threadpool(store.get_run, run_id)
        if run is None:
            raise HTTPException(404, "Run not found")
        raise HTTPException(409, f"Run cannot be cancelled from status '{run['status']}'")
    event = await run_in_threadpool(store.append_event, run_id, "cancel_requested", {})
    try:
        await run_in_threadpool(get_event_publisher().publish, event)
    except Exception:
        logger.exception("Could not publish cancellation for run %s", run_id)
    return {"run_id": run_id, "status": "cancelling"}


@router.post(
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


@router.post(
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


@router.post("/v1/chat/completions", dependencies=[Depends(verify_api_key)])
async def openai_chat(
    request: OpenAIChatCompletionRequest, x_conversation_id: str | None = Header(None)
):

    if request.model and request.model != AGENT_MODEL_ID:
        raise HTTPException(
            400, f"This endpoint only serves the '{AGENT_MODEL_ID}' model"
        )

    prompt = "\n".join(f"{m.role}: {m.content}" for m in request.messages)

    created = int(datetime.now(timezone.utc).timestamp())

    if request.stream:
        async def completion_stream():
            """OpenAI SSE with real model-token deltas and tool-status comments."""
            updates: queue.Queue[tuple[str, Any]] = queue.Queue()
            finished: dict[str, Any] = {}

            def run_stream() -> None:
                try:
                    result = run_agent(
                        prompt,
                        x_conversation_id or request.conversation_id or "default",
                        request.workspace,
                        DEFAULT_MODEL,
                        request.allow_write,
                        on_event=lambda kind, payload: updates.put(("event", (kind, payload))),
                        on_token=lambda content: updates.put(("token", content)),
                    )
                    finished["answer"] = result["answer"]
                except Exception as exc:
                    finished["error"] = str(exc)
                finally:
                    updates.put(("done", None))

            worker = threading.Thread(target=run_stream, daemon=True)
            worker.start()
            initial = {
                "id": f"chatcmpl-{created}", "object": "chat.completion.chunk",
                "created": created, "model": request.model or AGENT_MODEL_ID,
                "choices": [{"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}],
            }
            yield f"data: {json.dumps(initial)}\n\n"
            emitted_token = False
            while worker.is_alive() or not updates.empty():
                try:
                    kind, payload = updates.get_nowait()
                except queue.Empty:
                    yield ": agent is working\n\n"
                    await asyncio.sleep(0.25)
                    continue

                if kind == "token":
                    emitted_token = True
                    delta = {
                        "id": f"chatcmpl-{created}", "object": "chat.completion.chunk",
                        "created": created, "model": request.model or AGENT_MODEL_ID,
                        "choices": [{"index": 0, "delta": {"content": payload}, "finish_reason": None}],
                    }
                    yield f"data: {json.dumps(delta)}\n\n"
                elif kind == "event":
                    event_type, event_payload = payload
                    yield f": {event_type} {json.dumps(event_payload, default=str)}\n\n"

            if finished.get("error"):
                error_delta = {
                    "id": f"chatcmpl-{created}", "object": "chat.completion.chunk",
                    "created": created, "model": request.model or AGENT_MODEL_ID,
                    "choices": [{"index": 0, "delta": {"content": f"Agent request failed: {finished['error']}"}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(error_delta)}\n\n"
            elif not emitted_token:
                # A backend may not stream content even though it accepts a
                # streaming request. Preserve a useful OpenAI response.
                fallback = {
                    "id": f"chatcmpl-{created}", "object": "chat.completion.chunk",
                    "created": created, "model": request.model or AGENT_MODEL_ID,
                    "choices": [{"index": 0, "delta": {"content": finished.get("answer", "")}, "finish_reason": None}],
                }
                yield f"data: {json.dumps(fallback)}\n\n"
            final = {
                "id": f"chatcmpl-{created}", "object": "chat.completion.chunk",
                "created": created, "model": request.model or AGENT_MODEL_ID,
                "choices": [{"index": 0, "delta": {}, "finish_reason": "stop"}],
            }
            yield f"data: {json.dumps(final)}\n\n"
            yield "data: [DONE]\n\n"

        return StreamingResponse(
            completion_stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    result = await run_in_threadpool(
        run_agent,
        prompt,
        x_conversation_id or request.conversation_id or "default",
        request.workspace,
        DEFAULT_MODEL,
        request.allow_write,
    )

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


@router.get("/v1/models", dependencies=[Depends(verify_api_key)])
def models():
    return {
        "object": "list",
        "data": [
            {"id": AGENT_MODEL_ID, "object": "model", "owned_by": "ai-stack-agent"}
        ],
    }


@router.get("/models/available", dependencies=[Depends(verify_api_key)])
def available_models():
    """Gateway models available for task planning and execution."""
    try:
        return {"models": get_available_models()}
    except Exception as exc:
        logger.exception("Could not load gateway model list")
        raise HTTPException(503, "Model gateway is unavailable") from exc


@router.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
def model_detail(model_id: str):
    return {"id": model_id, "object": "model", "owned_by": "local"}


# =====================================================
# Embeddings
# =====================================================


@router.post("/v1/embeddings", dependencies=[Depends(verify_api_key)])
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


@router.post("/plan", dependencies=[Depends(verify_api_key)])
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


@router.post("/ingest", dependencies=[Depends(verify_api_key)])
async def ingest(request: IngestRequest):
    return await run_in_threadpool(
        ingest_documents, request.documents, request.metadata
    )


# =====================================================
# Memory
# =====================================================


@router.get("/conversation/{conversation_id}", dependencies=[Depends(verify_api_key)])
def conversation(conversation_id: str):
    return {
        "conversation_id": conversation_id,
        "history": get_conversation(conversation_id),
    }


@router.post("/memory/search", dependencies=[Depends(verify_api_key)])
async def memory_search(request: MemoryQuery):
    return {
        "query": request.query,
        "results": await run_in_threadpool(search_memory, request.query),
    }


# =====================================================
# Workspace
# =====================================================


@router.get("/workspace/tree", dependencies=[Depends(verify_api_key)])
def workspace_tree(path: str = "/workspace"):
    return {"workspace": path, "files": list_files(path)}


@router.get("/workspace/roots", dependencies=[Depends(verify_api_key)])
def workspace_roots():
    """List every directory the agent is permitted to operate in."""
    return {"roots": [str(r) for r in WORKSPACE_ROOTS]}
