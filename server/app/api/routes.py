"""HTTP routes for the coding agent service."""

import asyncio
import json
import logging
import os
import queue
import threading
import time
import uuid
from datetime import datetime, timezone
from typing import Any

import requests
from fastapi import APIRouter, Depends, File, Form, Header, HTTPException, UploadFile
from fastapi.concurrency import run_in_threadpool
from fastapi.responses import StreamingResponse

from app.agent.planner import create_plan
from app.agent.service import (
    approve_run,
    discard_run,
    ingest_documents,
    ingest_extracted_documents,
    run_agent,
    submit_run,
)
from app.agent.state import AgentState
from app.core.config import (
    AGENT_MODEL_ID,
    DEFAULT_MODEL,
    DEFAULT_WORKSPACE,
    DOCUMENT_MAX_BYTES,
    IMAGE_GENERATION_TIMEOUT_SECONDS,
    IMAGE_GENERATION_URL,
    WORKSPACE_ROOTS,
)
from app.llm.client import get_available_models
from app.memory.documents import extract_document
from app.memory.embeddings import create_embedding
from app.memory.memory import get_conversation, search_memory
from app.runs.client_leases import lease_sweep_metrics
from app.runs.events import get_event_publisher
from app.runs.store import get_run_store
from app.tools.filesystem import (
    list_files,
    resolve_request_workspace,
    workspace_choices,
)
from app.tools.registry import registry

from .context import openai_prompt
from .dependencies import require_run_store, verify_api_key
from .profiles import PROFILES, resolve_profile
from .protocol import (
    EVENT_SCHEMA_VERSION,
    FEATURES,
    MAX_CLI_PROTOCOL_VERSION,
    MIN_CLI_PROTOCOL_VERSION,
    PROTOCOL_VERSION,
    event_envelope,
    verify_protocol_version,
)
from .schemas import (
    ChatRequest,
    ClaimVerificationRequest,
    ExecuteRequest,
    ImageGenerationRequest,
    HunkApprovalRequest,
    IngestRequest,
    MemoryQuery,
    MemoryUpdateRequest,
    OpenAIChatCompletionRequest,
    OpenAIEmbeddingRequest,
    PlanRequest,
    ProjectRequest,
    RunRequest,
)

logger = logging.getLogger(__name__)
router = APIRouter(dependencies=[Depends(verify_protocol_version)])
RUN_STREAM_HEARTBEAT_SECONDS = 10
RUN_CLIENT_LEASE_SECONDS = 30
RUN_CLIENT_LEASE_RENEW_SECONDS = 10


@router.get("/")
@router.get("/health")
def health():
    return {"status": "running", "service": "local-ai-agent"}


@router.get("/capabilities", dependencies=[Depends(verify_api_key)])
def capabilities():
    return {
        "api_version": str(PROTOCOL_VERSION),
        "protocol": {
            "current": PROTOCOL_VERSION,
            "min_cli": MIN_CLI_PROTOCOL_VERSION,
            "max_cli": MAX_CLI_PROTOCOL_VERSION,
            "event_schema": EVENT_SCHEMA_VERSION,
        },
        "features": FEATURES,
        "deprecations": [],
    }



@router.post("/evidence/verify", dependencies=[Depends(verify_api_key)])
async def verify_evidence(request: ClaimVerificationRequest):
    from app.core.claim_verification import verify_claims

    try:
        return {"claims": await run_in_threadpool(verify_claims, request.claims)}
    except (KeyError, TypeError, ValueError) as exc:
        raise HTTPException(400, f"Invalid claim evidence: {exc}") from exc


@router.get("/metrics/runs", dependencies=[Depends(verify_api_key)])
def run_metrics():
    """Small authenticated operational snapshot for the foreground sweeper."""

    return {
        "client_lease_sweeper": lease_sweep_metrics.snapshot(),
        "client_leases": get_run_store().client_lease_health(),
    }


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
        "workspace": (
            os.listdir(DEFAULT_WORKSPACE) if DEFAULT_WORKSPACE.exists() else []
        ),
        "default_workspace": str(DEFAULT_WORKSPACE),
    }


# =====================================================
# Agent Chat (single blocking response, no sandboxing)
# =====================================================


@router.post("/chat", dependencies=[Depends(verify_api_key)])
async def chat(request: ChatRequest):

    if not request.message.strip():
        raise HTTPException(400, "Message cannot be empty")
    if request.allow_write:
        raise HTTPException(
            400, "Direct chat is read-only. Use POST /runs for reviewed sandbox writes."
        )

    try:
        profile = (
            resolve_profile(request.model or AGENT_MODEL_ID)
            if (request.model or AGENT_MODEL_ID) in PROFILES
            else None
        )
        workspace = resolve_request_workspace(request.workspace, request.message)
        return await run_in_threadpool(
            run_agent,
            request.message,
            request.conversation_id,
            workspace,
            profile.model if profile else request.model or DEFAULT_MODEL,
            False,
            force_research=profile.force_research if profile else False,
            prompt_mode=profile.prompt_mode if profile else "custom",
            max_completion_tokens=profile.max_completion_tokens if profile else None,
            timeout_seconds=profile.timeout_seconds if profile else None,
        )

    except Exception as e:
        logger.exception("Agent chat failed")
        raise HTTPException(
            500, "Agent request failed. Check service logs for details."
        ) from e


@router.post("/execute", dependencies=[Depends(verify_api_key)])
async def execute(request: ExecuteRequest):
    if request.allow_write:
        raise HTTPException(
            400,
            "Direct execute is read-only. Use POST /runs for reviewed sandbox writes.",
        )
    profile = (
        resolve_profile(request.model or AGENT_MODEL_ID)
        if (request.model or AGENT_MODEL_ID) in PROFILES
        else None
    )
    workspace = resolve_request_workspace(request.workspace, request.task)
    return await run_in_threadpool(
        run_agent,
        request.task,
        request.conversation_id,
        workspace,
        profile.model if profile else request.model or DEFAULT_MODEL,
        False,
        force_research=profile.force_research if profile else False,
        prompt_mode=profile.prompt_mode if profile else "custom",
        max_completion_tokens=profile.max_completion_tokens if profile else None,
        timeout_seconds=profile.timeout_seconds if profile else None,
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


@router.post(
    "/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)]
)
async def create_run(request: RunRequest):
    if not request.task.strip():
        raise HTTPException(400, "task cannot be empty")

    # Validate before the background worker creates a Git worktree.  Tool
    # execution also validates its workspace, but that happens too late for
    # write-enabled runs.
    try:
        workspace = resolve_request_workspace(request.workspace, request.task)
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc

    store = get_run_store()
    run_id = await run_in_threadpool(
        store.create_run,
        task=request.task,
        model=request.model or AGENT_MODEL_ID,
        workspace=workspace,
        conversation_id=request.conversation_id,
        document_scope=request.document_scope,
        project_id=request.project_id,
        allow_write=request.allow_write,
        client_id=request.client_id,
        client_lease_seconds=RUN_CLIENT_LEASE_SECONDS,
    )

    submit_run(run_id)

    return {
        "run_id": run_id,
        "status": "queued",
        "client_id": request.client_id,
        "protocol_version": PROTOCOL_VERSION,
    }


@router.get(
    "/runs/{run_id}", dependencies=[Depends(verify_api_key), Depends(require_run_store)]
)
async def get_run(run_id: str):
    store = get_run_store()
    run = await run_in_threadpool(store.get_run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    return {"protocol_version": PROTOCOL_VERSION, **run}


@router.get("/runs", dependencies=[Depends(verify_api_key), Depends(require_run_store)])
async def list_runs(limit: int = 50):
    return {
        "protocol_version": PROTOCOL_VERSION,
        "runs": await run_in_threadpool(get_run_store().list_runs, limit),
    }


@router.get(
    "/projects", dependencies=[Depends(verify_api_key), Depends(require_run_store)]
)
async def list_projects():
    return {
        "protocol_version": PROTOCOL_VERSION,
        "projects": await run_in_threadpool(get_run_store().list_projects),
    }


@router.post(
    "/projects", dependencies=[Depends(verify_api_key), Depends(require_run_store)]
)
async def create_project(request: ProjectRequest):
    if not request.name.strip():
        raise HTTPException(400, "Project name cannot be empty")
    try:
        workspace = resolve_request_workspace(request.workspace)
        return await run_in_threadpool(
            get_run_store().create_project, request.name, workspace
        )
    except (ValueError, PermissionError) as exc:
        raise HTTPException(400, str(exc)) from exc


@router.get(
    "/runs/{run_id}/events",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def run_events(
    run_id: str,
    after: int = 0,
    client_id: str | None = None,
):
    """Server-Sent Events stream of a run's progress.

    Emits one `data:` line per event as JSON, polling the durable event log
    until the run reaches a terminal status (completed, awaiting_approval,
    failed, or discarded).
    """
    store = get_run_store()

    run = await run_in_threadpool(store.get_run, run_id)
    if run is None:
        raise HTTPException(404, "Run not found")
    owns_client_lease = bool(client_id and run.get("client_id") == client_id)
    if owns_client_lease:
        renewed = await run_in_threadpool(
            store.renew_client_lease,
            run_id,
            client_id,
            RUN_CLIENT_LEASE_SECONDS,
        )
        lease_sweep_metrics.renewal(renewed)

    terminal_statuses = {
        "completed",
        "awaiting_approval",
        "failed",
        "discarded",
        "cancelled",
    }

    async def event_stream():
        last_id = after
        last_emit = time.monotonic()
        last_client_renewal = time.monotonic()
        lease_attached = owns_client_lease
        subscription = None
        try:
            # Subscribe first, then replay from PostgreSQL. Any event that
            # arrives during replay carries an id and is de-duplicated below.
            try:
                subscription = await run_in_threadpool(
                    get_event_publisher().subscribe, run_id
                )
            except Exception:
                logger.exception(
                    "Redis Pub/Sub unavailable; falling back to durable polling"
                )

            while True:
                events = await run_in_threadpool(store.events_after, run_id, last_id)
                for event in events:
                    last_id = event["id"]
                    payload = event_envelope(
                        {
                            "id": event["id"],
                            "event_type": event["event_type"],
                            "payload": event["payload"],
                            "created_at": (
                                event["created_at"].isoformat()
                                if isinstance(event["created_at"], datetime)
                                else event["created_at"]
                            ),
                        }
                    )
                    yield f"data: {json.dumps(payload, default=str)}\n\n"
                    last_emit = time.monotonic()

                if subscription is not None:
                    while message := await run_in_threadpool(
                        subscription.get_message, timeout=0
                    ):
                        try:
                            event = json.loads(message["data"])
                        except (KeyError, TypeError, json.JSONDecodeError):
                            continue
                        if event.get("id", 0) <= last_id:
                            continue
                        last_id = event["id"]
                        event["created_at"] = str(event.get("created_at", ""))
                        event = event_envelope(event)
                        yield f"data: {json.dumps(event, default=str)}\n\n"
                        last_emit = time.monotonic()

                current = await run_in_threadpool(store.get_run, run_id)
                if (
                    current is not None
                    and current["status"] in terminal_statuses
                    and not events
                ):
                    closed = event_envelope(
                        {
                            "event_type": "stream_closed",
                            "status": current["status"],
                        }
                    )
                    yield f"data: {json.dumps(closed)}\n\n"
                    break
                if (
                    lease_attached
                    and time.monotonic() - last_client_renewal
                    >= RUN_CLIENT_LEASE_RENEW_SECONDS
                ):
                    renewed = await run_in_threadpool(
                        store.renew_client_lease,
                        run_id,
                        client_id,
                        RUN_CLIENT_LEASE_SECONDS,
                    )
                    lease_sweep_metrics.renewal(renewed)
                    if not renewed:
                        lease_attached = False
                    last_client_renewal = time.monotonic()
                if time.monotonic() - last_emit >= RUN_STREAM_HEARTBEAT_SECONDS:
                    yield ": heartbeat\n\n"
                    last_emit = time.monotonic()

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
        raise HTTPException(
            409, f"Run cannot be cancelled from status '{run['status']}'"
        )
    run = await run_in_threadpool(store.get_run, run_id)
    status = str(run["status"]) if run else "cancelling"
    event = await run_in_threadpool(
        store.append_event, run_id, "cancel_requested", {"status": status}
    )
    try:
        await run_in_threadpool(get_event_publisher().publish, event)
    except Exception:
        logger.exception("Could not publish cancellation for run %s", run_id)
    return {
        "protocol_version": PROTOCOL_VERSION,
        "run_id": run_id,
        "status": status,
    }


@router.post(
    "/runs/{run_id}/approve",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def approve(run_id: str):
    try:
        result = await run_in_threadpool(approve_run, run_id)
        return {"protocol_version": PROTOCOL_VERSION, **result}
    except ValueError as e:
        raise HTTPException(400, str(e)) from e
    except Exception as e:
        logger.exception("Failed to approve run %s", run_id)
        raise HTTPException(500, f"Could not merge changes: {e}") from e



@router.get(
    "/runs/{run_id}/review",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def review_hunks(run_id: str):
    from app.runs.review import review_run

    try:
        transaction = await run_in_threadpool(review_run, run_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {
        "base_revision": transaction.base_revision,
        "hunks": [
            {
                "id": hunk.id,
                "path": hunk.path,
                "patch": hunk.patch,
                "evidence": list(hunk.evidence),
                "state": hunk.state.value,
            }
            for hunk in transaction.hunks
        ],
    }


@router.post(
    "/runs/{run_id}/approve-hunks",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def approve_selected_hunks(run_id: str, request: HunkApprovalRequest):
    from app.runs.review import apply_hunks

    try:
        return await run_in_threadpool(apply_hunks, run_id, set(request.hunk_ids))
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/transactions/{transaction_id}/undo",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def undo_change_transaction(transaction_id: str):
    from app.runs.review import undo_transaction

    try:
        return await run_in_threadpool(undo_transaction, transaction_id)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    except RuntimeError as exc:
        raise HTTPException(409, str(exc)) from exc


@router.post(
    "/runs/{run_id}/discard",
    dependencies=[Depends(verify_api_key), Depends(require_run_store)],
)
async def discard(run_id: str):
    try:
        result = await run_in_threadpool(discard_run, run_id)
        return {"protocol_version": PROTOCOL_VERSION, **result}
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

    try:
        profile = resolve_profile(request.model)
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc

    # Bound client-supplied history before the executor adds its own prompt
    # and tool schemas. This protects the 8K local Ollama context from large
    # Open WebUI/Continue codebase payloads.
    prompt = openai_prompt(request.messages)
    if not prompt.strip():
        raise HTTPException(400, "A user message is required")
    if request.allow_write:
        raise HTTPException(
            400,
            "OpenAI-compatible chat is read-only. Use POST /runs for reviewed sandbox writes.",
        )
    workspace = resolve_request_workspace(request.workspace, prompt)

    created = int(datetime.now(timezone.utc).timestamp())

    # Chat clients already submit their own history. When they do not provide
    # a stable conversation id, never reuse the shared "default" Redis
    # conversation: it can accumulate unrelated, oversized prompts.
    conversation_id = x_conversation_id or request.conversation_id or str(uuid.uuid4())

    if request.stream:

        async def completion_stream():
            """OpenAI SSE with real model-token deltas and tool-status comments."""
            updates: queue.Queue[tuple[str, Any]] = queue.Queue()
            finished: dict[str, Any] = {}

            def run_stream() -> None:
                try:
                    result = run_agent(
                        prompt,
                        conversation_id,
                        workspace,
                        profile.model,
                        False,
                        on_event=lambda kind, payload: updates.put(
                            ("event", (kind, payload))
                        ),
                        on_token=lambda content: updates.put(("token", content)),
                        force_research=profile.force_research,
                        prompt_mode=profile.prompt_mode,
                        max_completion_tokens=profile.max_completion_tokens,
                        timeout_seconds=profile.timeout_seconds,
                    )
                    finished["answer"] = result["answer"]
                except Exception as exc:
                    finished["error"] = str(exc)
                finally:
                    updates.put(("done", None))

            worker = threading.Thread(target=run_stream, daemon=True)
            worker.start()
            initial = {
                "id": f"chatcmpl-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model or AGENT_MODEL_ID,
                "choices": [
                    {"index": 0, "delta": {"role": "assistant"}, "finish_reason": None}
                ],
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
                        "id": f"chatcmpl-{created}",
                        "object": "chat.completion.chunk",
                        "created": created,
                        "model": request.model or AGENT_MODEL_ID,
                        "choices": [
                            {
                                "index": 0,
                                "delta": {"content": payload},
                                "finish_reason": None,
                            }
                        ],
                    }
                    yield f"data: {json.dumps(delta)}\n\n"
                elif kind == "event":
                    event_type, event_payload = payload
                    yield f": {event_type} {json.dumps(event_payload, default=str)}\n\n"

            if finished.get("error"):
                error_delta = {
                    "id": f"chatcmpl-{created}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model or AGENT_MODEL_ID,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {
                                "content": f"Agent request failed: {finished['error']}"
                            },
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(error_delta)}\n\n"
            elif not emitted_token:
                # A backend may not stream content even though it accepts a
                # streaming request. Preserve a useful OpenAI response.
                fallback = {
                    "id": f"chatcmpl-{created}",
                    "object": "chat.completion.chunk",
                    "created": created,
                    "model": request.model or AGENT_MODEL_ID,
                    "choices": [
                        {
                            "index": 0,
                            "delta": {"content": finished.get("answer", "")},
                            "finish_reason": None,
                        }
                    ],
                }
                yield f"data: {json.dumps(fallback)}\n\n"
            final = {
                "id": f"chatcmpl-{created}",
                "object": "chat.completion.chunk",
                "created": created,
                "model": request.model or AGENT_MODEL_ID,
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
        conversation_id,
        workspace,
        profile.model,
        False,
        force_research=profile.force_research,
        prompt_mode=profile.prompt_mode,
        max_completion_tokens=profile.max_completion_tokens,
        timeout_seconds=profile.timeout_seconds,
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
            {
                "id": AGENT_MODEL_ID,
                "object": "model",
                "owned_by": "ai-stack-agent",
            }
        ],
    }


@router.get("/models/available")
def available_models():
    """Public, non-sensitive catalog for the Task Router model selector.

    Starting a run and all run data remain authenticated; this endpoint only
    returns gateway model aliases and never exposes credentials or settings.
    """
    try:
        return {"models": get_available_models()}
    except Exception as exc:
        logger.exception("Could not load gateway model list")
        raise HTTPException(503, "Model gateway is unavailable") from exc


@router.get("/images/status", dependencies=[Depends(verify_api_key)])
def image_generation_status():
    return {
        "available": bool(IMAGE_GENERATION_URL),
        "provider": "automatic1111" if IMAGE_GENERATION_URL else None,
    }


@router.post("/images/generations", dependencies=[Depends(verify_api_key)])
async def generate_image(request: ImageGenerationRequest):
    """Proxy an optional local Automatic1111/Forge image backend.

    The backend URL is administrator-configured; callers cannot choose an
    arbitrary destination. Returned images remain data URLs for the local UI.
    """
    if not IMAGE_GENERATION_URL:
        raise HTTPException(
            503,
            "Image generation is not configured. Set IMAGE_GENERATION_URL to an Automatic1111/Forge API.",
        )
    if not request.prompt.strip():
        raise HTTPException(400, "Image prompt cannot be empty")
    payload = request.model_dump()
    try:
        response = await run_in_threadpool(
            requests.post,
            f"{IMAGE_GENERATION_URL}/sdapi/v1/txt2img",
            json=payload,
            timeout=IMAGE_GENERATION_TIMEOUT_SECONDS,
        )
        response.raise_for_status()
        images = response.json().get("images", [])
    except requests.RequestException as exc:
        logger.exception("Image generation backend failed")
        raise HTTPException(502, "Image generation backend is unavailable") from exc
    return {
        "created": int(datetime.now(timezone.utc).timestamp()),
        "data": [{"url": f"data:image/png;base64,{image}"} for image in images],
    }


@router.get("/v1/models/{model_id}", dependencies=[Depends(verify_api_key)])
def model_detail(model_id: str):
    if model_id != AGENT_MODEL_ID:
        raise HTTPException(404, "Agent model not found")
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
        workspace=resolve_request_workspace(request.workspace, request.message),
    )

    result = await run_in_threadpool(create_plan, state)

    return {"plan": result}


# =====================================================
# Ingest
# =====================================================


@router.post("/ingest", dependencies=[Depends(verify_api_key)])
async def ingest(request: IngestRequest):
    return await run_in_threadpool(
        ingest_documents, request.documents, request.metadata, request.scope
    )


@router.post("/documents/ingest", dependencies=[Depends(verify_api_key)])
async def ingest_uploaded_documents(
    files: list[UploadFile] = File(...),
    scope: str = Form("global"),
):
    """Parse and index files with source/page/sheet metadata for citation."""
    extracted = []
    for upload in files[:10]:
        content = await upload.read()
        if len(content) > DOCUMENT_MAX_BYTES:
            raise HTTPException(413, f"{upload.filename} exceeds DOCUMENT_MAX_BYTES")
        try:
            extracted.extend(extract_document(upload.filename or "uploaded", content))
        except ValueError as exc:
            raise HTTPException(400, str(exc)) from exc
        except Exception as exc:
            logger.exception("Could not parse uploaded document %s", upload.filename)
            raise HTTPException(400, f"Could not parse {upload.filename}") from exc
    return await run_in_threadpool(ingest_extracted_documents, extracted, scope)


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
        "results": await run_in_threadpool(
            search_memory, request.query, request.top_k, request.scope
        ),
    }


# =====================================================
# Workspace
# =====================================================



@router.patch("/memory/{memory_id}", dependencies=[Depends(verify_api_key)])
async def edit_memory(memory_id: str, request: MemoryUpdateRequest):
    from app.memory.memory import update_memory

    try:
        await run_in_threadpool(
            update_memory,
            memory_id,
            text=request.text,
            expires_at=request.expires_at,
            scope=request.scope,
        )
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return {"id": memory_id, "status": "updated"}


@router.delete("/memory/{memory_id}", dependencies=[Depends(verify_api_key)])
async def remove_memory(memory_id: str):
    from app.memory.memory import delete_memory

    await run_in_threadpool(delete_memory, memory_id)
    return {"id": memory_id, "status": "deleted"}


@router.get("/workspace/tree", dependencies=[Depends(verify_api_key)])
def workspace_tree(path: str = str(DEFAULT_WORKSPACE)):
    return {"workspace": path, "files": list_files(path)}


@router.get("/workspace/roots", dependencies=[Depends(verify_api_key)])
def workspace_roots():
    """List every directory the agent is permitted to operate in."""
    return {"roots": [str(r) for r in WORKSPACE_ROOTS]}


@router.get("/workspace/choices", dependencies=[Depends(verify_api_key)])
def workspace_choice_list():
    """List selectable mounted repositories without recursively scanning them."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "workspaces": workspace_choices(),
    }


@router.get("/workspace/default", dependencies=[Depends(verify_api_key)])
def default_workspace():
    """Return the repository selected as the default for new agent requests."""
    return {
        "protocol_version": PROTOCOL_VERSION,
        "workspace": str(DEFAULT_WORKSPACE),
    }
