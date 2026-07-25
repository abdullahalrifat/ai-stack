import logging
import uuid
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .config import DEFAULT_MODEL
from .executor import execute_plan
from .memory import (
    get_conversation,
    save_conversation,
    save_long_term_memory,
    save_memory,
    search_memory,
)
from .memory.embeddings import create_embedding
from .planner import create_plan
from .run_store import get_run_store
from .sandbox import Sandbox, create_sandbox, merge_sandbox, remove_sandbox, sandbox_diff
from .state import AgentState
from .tools.filesystem import workspace_context

logger = logging.getLogger(__name__)


def run_agent(
    message: str,
    conversation_id: str | None = None,
    workspace="/workspace",
    model=DEFAULT_MODEL,
    allow_write=False,
):
    """Synchronous, single-response agent turn.

    No sandboxing: if allow_write is set, edits apply directly to the given
    workspace. Used by /chat, /execute, and the OpenAI-compatible endpoint,
    where a single blocking response is expected. For reviewable,
    streamed, sandbox-isolated runs see execute_run below.
    """

    conversation_id = conversation_id or str(uuid.uuid4())

    state = AgentState(
        conversation_id=conversation_id,
        user_message=message,
        model=model,
        allow_write=allow_write,
        workspace=workspace,
    )

    with workspace_context(workspace):
        state.history = get_conversation(conversation_id)
        state.memories = search_memory(message)
        state.plan = create_plan(state)
        answer = execute_plan(state)

    state.answer = answer

    save_conversation(conversation_id, "user", message)
    save_conversation(conversation_id, "assistant", answer)
    save_memory(message, answer)

    return {"conversation_id": conversation_id, "answer": answer}


def execute_run(run_id: str) -> None:
    """Run a durable, streamed, sandbox-isolated agent turn.

    Intended to be launched in a background thread right after
    run_store.create_run. Progress is published as events a client can poll
    or stream via RunStore.events_after / the /runs/{id}/events endpoint.
    Write-enabled runs execute inside a disposable Git worktree so changes
    can be reviewed as a diff before being merged into the real repository.
    """

    store = get_run_store()
    run = store.get_run(run_id)
    if run is None:
        logger.error("execute_run called for unknown run_id=%s", run_id)
        return

    def on_event(event_type: str, payload: dict[str, Any]):
        store.append_event(run_id, event_type, payload)

    store.update_run(run_id, status="running", started_at=datetime.now(timezone.utc))
    on_event("run_started", {})

    conversation_id = run["conversation_id"] or run_id
    requested_workspace = run["requested_workspace"]
    allow_write = run["allow_write"]
    model = run["model"]
    task = run["task"]

    sandbox: Sandbox | None = None
    active_workspace = requested_workspace

    try:
        if allow_write:
            on_event("sandbox_creating", {"workspace": requested_workspace})
            sandbox = create_sandbox(requested_workspace, run_id)
            active_workspace = str(sandbox.path)
            store.update_run(
                run_id,
                sandbox_path=active_workspace,
                repository_path=str(sandbox.repository),
            )
            on_event("sandbox_ready", {"sandbox_path": active_workspace})

        state = AgentState(
            conversation_id=conversation_id,
            user_message=task,
            model=model,
            allow_write=allow_write,
            workspace=active_workspace,
        )
        store.update_run(run_id, active_workspace=active_workspace)

        with workspace_context(active_workspace):
            state.history = get_conversation(conversation_id)
            state.memories = search_memory(task)

            on_event("planning", {})
            state.plan = create_plan(state)
            on_event("plan_ready", {"plan": state.plan})

            answer = execute_plan(state, on_event=on_event)

        diff = sandbox_diff(str(sandbox.path)) if sandbox is not None else None
        has_pending_diff = bool(diff and diff.strip())
        if has_pending_diff:
            on_event("diff_ready", {"diff": diff})

        save_conversation(conversation_id, "user", task)
        save_conversation(conversation_id, "assistant", answer)
        save_memory(task, answer)

        if sandbox is not None and not has_pending_diff:
            # Nothing changed -- no point holding a worktree open for review.
            remove_sandbox(str(sandbox.repository), str(sandbox.path))
            store.update_run(run_id, sandbox_path=None)

        store.update_run(
            run_id,
            status="awaiting_approval" if has_pending_diff else "completed",
            answer=answer,
            completed_at=datetime.now(timezone.utc),
        )
        on_event(
            "run_completed",
            {"answer": answer, "has_pending_diff": has_pending_diff},
        )

    except Exception as e:
        logger.exception("Run %s failed", run_id)
        if sandbox is not None:
            try:
                remove_sandbox(str(sandbox.repository), str(sandbox.path))
            except Exception:
                logger.exception("Failed to clean up sandbox for run %s", run_id)
        store.update_run(
            run_id,
            status="failed",
            error=str(e),
            completed_at=datetime.now(timezone.utc),
        )
        on_event("run_failed", {"error": str(e)})


def approve_run(run_id: str) -> dict[str, Any]:
    """Merge a write-enabled run's sandbox diff into the real repository."""

    store = get_run_store()
    run = store.get_run(run_id)
    if run is None:
        raise ValueError("Run not found")
    if run["status"] != "awaiting_approval" or not run["sandbox_path"]:
        raise ValueError("Run has no pending changes to approve")

    sandbox = Sandbox(
        repository=Path(run["repository_path"]).resolve(),
        path=Path(run["sandbox_path"]).resolve(),
    )
    merge_sandbox(sandbox)
    remove_sandbox(str(sandbox.repository), str(sandbox.path))

    store.update_run(run_id, status="completed", sandbox_path=None)
    store.append_event(run_id, "run_merged", {})
    return {"run_id": run_id, "status": "completed"}


def discard_run(run_id: str) -> dict[str, Any]:
    """Discard a write-enabled run's sandbox without merging it."""

    store = get_run_store()
    run = store.get_run(run_id)
    if run is None:
        raise ValueError("Run not found")
    if not run["sandbox_path"]:
        raise ValueError("Run has no sandbox to discard")

    remove_sandbox(run["repository_path"], run["sandbox_path"])
    store.update_run(run_id, status="discarded", sandbox_path=None)
    store.append_event(run_id, "run_discarded", {})
    return {"run_id": run_id, "status": "discarded"}


# =====================================================
# Document ingestion
# =====================================================


def ingest_documents(texts: list[str], metadata: dict[str, Any] | None = None):
    stored = 0

    for text in texts:
        if not text or not text.strip():
            continue

        embedding = create_embedding(text)
        save_long_term_memory(text, embedding, metadata or {})
        stored += 1

    return {"stored": stored, "status": "success"}