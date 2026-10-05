import logging
import subprocess
import threading
import time
import uuid
from concurrent.futures import ThreadPoolExecutor
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from ..api.profiles import resolve_profile, resolve_workflow
from ..core.cancellation import cancellation_context, raise_if_cancelled
from ..core.config import (
    DEFAULT_MODEL,
    DEFAULT_WORKSPACE,
    GENERATED_MEMORY_ENABLED,
    MAX_CONCURRENT_AGENT_RUNS,
    MEMORY_CONTEXT_TOKENS,
    MEMORY_ENABLED,
    MEMORY_FOR_CODE_RUNS,
    RESEARCH_MODEL,
    RUN_EVENT_BATCH_CHARS,
    RUN_EVENT_BATCH_SECONDS,
)
from ..core.evidence import build_document_evidence, build_inline_document_evidence
from ..core.exceptions import ProcessKillFailed, RunCancelled
from ..memory import (
    get_conversation,
    memory_context,
    save_conversation,
    save_long_term_memory,
    save_memory,
    search_memory,
)
from ..memory.embeddings import create_embedding
from ..runs.events import get_event_publisher
from ..runs.sandbox import (
    Sandbox,
    create_sandbox,
    merge_sandbox,
    remove_sandbox,
    sandbox_diff,
)
from ..runs.store import get_run_store
from ..tools.filesystem import workspace_context
from .executor import execute_plan, requires_external_search
from .graph import transition_graph
from .planner import create_plan, deterministic_plan
from .review import review_change
from .router import route_request
from .state import AgentState

logger = logging.getLogger(__name__)
_run_executor = ThreadPoolExecutor(
    max_workers=MAX_CONCURRENT_AGENT_RUNS,
    thread_name_prefix="agent-run",
)


def _save_memory_best_effort(
    question: str, answer: str, scope: str | None = None
) -> None:
    """Memory persistence must never turn a completed answer into HTTP 500."""
    if not GENERATED_MEMORY_ENABLED:
        return
    try:
        save_memory(question, answer, scope=scope)
    except Exception as exc:
        logger.warning("Could not persist conversation memory: %s", exc)


def submit_run(run_id: str) -> None:
    """Queue a durable run without allocating an unbounded request thread."""
    _run_executor.submit(execute_run, run_id)


def shutdown_run_executor() -> None:
    _run_executor.shutdown(wait=False, cancel_futures=False)


def _task_model(message: str, requested_model: str) -> str:
    """Keep explicit Runs UI selection, but route default agent research."""
    if requested_model == DEFAULT_MODEL and requires_external_search(message):
        return RESEARCH_MODEL
    return requested_model


def _uses_memory(prompt_mode: str, document_scope: str | None = None) -> bool:
    # Explicit uploads must be available even to the fast Code/Quick paths.
    return MEMORY_ENABLED and (
        bool(document_scope)
        or MEMORY_FOR_CODE_RUNS
        or prompt_mode not in {"auto", "code", "quick"}
    )


def _execution_plan(state, research_mode: bool) -> list:
    if state.plan:
        return state.plan
    if research_mode:
        return []
    if state.prompt_mode in {"code", "quick"}:
        return deterministic_plan(state)
    return create_plan(state)


def _apply_auto_route(state, on_event=None) -> None:
    """Translate an Auto request and apply only server-approved profile policy."""
    if state.prompt_mode != "auto":
        return
    routing_context = list(state.memories)
    if state.document_evidence:
        routing_context.append({"document_evidence": state.document_evidence})
    decision = route_request(state.user_message, routing_context)
    profile = resolve_workflow(decision.workflow)
    state.model = profile.model
    state.prompt_mode = profile.prompt_mode
    state.max_completion_tokens = profile.max_completion_tokens
    state.timeout_seconds = profile.timeout_seconds
    state.requires_external_evidence = (
        decision.requires_external_evidence or profile.force_research
    )
    excluded_entities = [
        str(entity) for entity in state.document_evidence.get("excluded_entities", [])
    ]
    selected_text = " ".join(
        str(record.get("text", ""))
        for record in state.document_evidence.get("records", [])
    ).casefold()
    state.routing_entities = [
        entity
        for entity in decision.entities
        if entity.casefold() in selected_text
        or not any(
            entity.casefold() in excluded.casefold()
            or excluded.casefold() in entity.casefold()
            for excluded in excluded_entities
        )
    ]
    state.route_deliverables = decision.deliverables
    state.route_completion_criteria = [
        criterion for task in decision.tasks for criterion in task.completion_criteria
    ]
    state.route_tasks = [
        {
            "id": task.id,
            "objective": task.objective,
            "workflow": task.workflow,
            "depends_on": list(task.depends_on),
            "required_evidence": list(task.required_evidence),
            "completion_criteria": list(task.completion_criteria),
        }
        for task in decision.tasks
    ]
    state.task_progress = {task.id: "pending" for task in decision.tasks}
    # Complex or multi-workflow contracts benefit from several bounded expert
    # analyses (architecture/implementation/verification/risk) feeding structured
    # findings into the executor's evidence ledger. Ordinary single-path requests
    # keep the fast single-loop execution with no extra model calls.
    state.expert_dispatch = decision.complexity == "complex" or len(decision.tasks) >= 2
    brief_parts = [f"Translated objective:\n{decision.translated_task}"]
    if state.routing_entities:
        brief_parts.append(
            "Grounded entities:\n- " + "\n- ".join(state.routing_entities)
        )
    rejected_entities = [
        entity for entity in decision.entities if entity not in state.routing_entities
    ]
    if rejected_entities:
        brief_parts.append(
            "Entities rejected because they occur only in excluded document sections "
            "(do not analyze them as selected records):\n- "
            + "\n- ".join(rejected_entities)
        )
    if decision.constraints:
        brief_parts.append("Constraints:\n- " + "\n- ".join(decision.constraints))
    if decision.deliverables:
        brief_parts.append(
            "Required deliverables:\n- " + "\n- ".join(decision.deliverables)
        )
    if decision.missing_inputs:
        brief_parts.append(
            "Known missing inputs (continue when safe; do not invent them):\n- "
            + "\n- ".join(decision.missing_inputs)
        )
    if decision.assumptions:
        brief_parts.append(
            "Planner-approved assumptions (label them in the answer):\n- "
            + "\n- ".join(decision.assumptions)
        )
    if decision.selected_document_sections:
        brief_parts.append(
            "Selected document sections:\n- "
            + "\n- ".join(decision.selected_document_sections)
        )
    if decision.extracted_records:
        brief_parts.append(
            "Planner-grounded extracted records:\n- "
            + "\n- ".join(decision.extracted_records)
        )
    if decision.validation_warnings:
        brief_parts.append(
            "Route validation warnings:\n- " + "\n- ".join(decision.validation_warnings)
        )
    task_lines = []
    for task in decision.tasks:
        task_lines.append(
            f"- {task.id} [{task.workflow}] {task.objective}\n"
            f"  depends_on: {', '.join(task.depends_on) or 'none'}\n"
            f"  required_evidence: {'; '.join(task.required_evidence) or 'task-appropriate evidence'}\n"
            f"  completion_criteria: {'; '.join(task.completion_criteria) or 'objective demonstrably completed'}"
        )
    if task_lines:
        brief_parts.append("Validated task graph:\n" + "\n".join(task_lines))
    state.execution_brief = "\n\n".join(brief_parts)
    state.plan = decision.plan
    if on_event is not None:
        on_event(
            "route_selected",
            {
                "workflow": decision.workflow,
                "model": state.model,
                "requires_external_evidence": state.requires_external_evidence,
                "complexity": decision.complexity,
                "task_count": len(decision.tasks),
                "task_workflows": sorted({task.workflow for task in decision.tasks}),
                "source": decision.source,
            },
        )


class RunEventBuffer:
    """Batch output deltas while publishing durable events immediately enough for UI."""

    def __init__(self, store, run_id: str, heartbeat=None):
        self.store = store
        self.run_id = run_id
        self.heartbeat = heartbeat
        self.pending_output = ""
        self.last_flush = time.monotonic()
        self.last_heartbeat = time.monotonic()

    def _persist(self, event_type: str, payload: dict[str, Any]) -> None:
        try:
            event = self.store.append_event(self.run_id, event_type, payload)
        except Exception:
            # A brief PostgreSQL restart must not terminate an otherwise
            # healthy model/tool turn. Worker reconciliation handles a
            # prolonged outage or process crash from the durable checkpoint.
            logger.exception(
                "Could not persist event %s for run %s", event_type, self.run_id
            )
            return
        if event_type != "output_delta":
            log = (
                logger.warning
                if event_type
                in {
                    "answer_audit_failed",
                    "max_steps_reached",
                    "run_failed",
                    "tool_recovery_required",
                }
                else logger.info
            )
            log(
                "Run event run_id=%s type=%s tool=%s failures=%s",
                self.run_id,
                event_type,
                payload.get("tool"),
                payload.get("failures") or payload.get("error"),
            )
        if not event:
            return
        try:
            get_event_publisher().publish(event)
        except Exception:
            # Durable storage succeeded; a reconnecting client will replay it.
            logger.exception("Could not publish live event for run %s", self.run_id)

    def emit(self, event_type: str, payload: dict[str, Any]) -> None:
        if self.heartbeat is not None and time.monotonic() - self.last_heartbeat >= 30:
            self.heartbeat()
            self.last_heartbeat = time.monotonic()
        if event_type == "output_delta":
            self.pending_output += str(payload.get("content", ""))
            if (
                len(self.pending_output) >= RUN_EVENT_BATCH_CHARS
                or time.monotonic() - self.last_flush >= RUN_EVENT_BATCH_SECONDS
            ):
                self.flush()
            return
        self.flush()
        self._persist(event_type, payload)

    def flush(self) -> None:
        if not self.pending_output:
            return
        self._persist("output_delta", {"content": self.pending_output})
        self.pending_output = ""
        self.last_flush = time.monotonic()


def run_agent(
    message: str,
    conversation_id: str | None = None,
    workspace=str(DEFAULT_WORKSPACE),
    model=DEFAULT_MODEL,
    allow_write=False,
    on_event=None,
    on_token=None,
    force_research=False,
    prompt_mode="code",
    max_completion_tokens=None,
    timeout_seconds=None,
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
        model=_task_model(message, model),
        prompt_mode=prompt_mode,
        max_completion_tokens=max_completion_tokens,
        timeout_seconds=timeout_seconds,
        allow_write=allow_write,
        workspace=workspace,
    )

    # Model calls are serialized in llm.client. Tool and retrieval work may
    # overlap across bounded runs so slow I/O does not block every request.
    with workspace_context(workspace):
        # Client-facing OpenAI compatibility already carries recent history;
        # only a short server-side tail is needed for direct API callers.
        state.history = get_conversation(conversation_id, limit=4)
        state.memory_scope = str(workspace)
        state.document_evidence = build_inline_document_evidence(message)
        _apply_auto_route(state, on_event)
        if _uses_memory(state.prompt_mode):
            state.memories = memory_context(
                search_memory(message, scope=state.memory_scope),
                MEMORY_CONTEXT_TOKENS,
            )
            state.document_evidence = (
                build_document_evidence(message, state.memories)
                or state.document_evidence
            )
        else:
            state.memories = []
        research_mode = (
            force_research
            or state.requires_external_evidence
            or requires_external_search(message)
        )
        state.plan = _execution_plan(state, research_mode)
        answer = execute_plan(
            state,
            on_event=on_event,
            on_token=on_token,
            force_research=force_research or state.requires_external_evidence,
        )

    state.answer = answer

    save_conversation(conversation_id, "user", message)
    save_conversation(conversation_id, "assistant", answer)
    if _uses_memory(state.prompt_mode):
        _save_memory_best_effort(message, answer, scope=state.memory_scope)

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

    worker_id = str(uuid.uuid4())
    if not store.claim_run(run_id, worker_id):
        logger.info("Run %s is already claimed or no longer queued", run_id)
        return

    events = RunEventBuffer(
        store,
        run_id,
        heartbeat=lambda: store.heartbeat_run(run_id, worker_id),
    )

    def on_event(event_type: str, payload: dict[str, Any]):
        events.emit(event_type, payload)

    last_cancel_check = 0.0
    last_worker_heartbeat = 0.0
    cancel_cached = False

    def cancelled() -> bool:
        nonlocal last_cancel_check, last_worker_heartbeat, cancel_cached
        if cancel_cached:
            return True
        now = time.monotonic()
        if now - last_worker_heartbeat >= 5:
            try:
                store.heartbeat_run(run_id, worker_id)
            except Exception:
                logger.warning("Could not heartbeat run %s", run_id, exc_info=True)
            last_worker_heartbeat = now
        if now - last_cancel_check < 0.25:
            return False
        last_cancel_check = now
        try:
            cancel_cached = store.is_cancel_requested(run_id)
        except Exception:
            logger.warning(
                "Could not check cancellation for run %s", run_id, exc_info=True
            )
        return cancel_cached

    def persist_checkpoint(checkpoint_payload: dict[str, Any]) -> None:
        try:
            store.update_checkpoint(run_id, checkpoint_payload)
        except Exception:
            logger.warning("Could not checkpoint run %s", run_id, exc_info=True)

    if cancelled():
        store.update_run(
            run_id,
            status="cancelled",
            worker_id=None,
            lease_expires_at=None,
            completed_at=datetime.now(timezone.utc),
        )
        on_event("run_cancelled", {"before_start": True})
        events.flush()
        return

    heartbeat_stop = threading.Event()

    def heartbeat_worker() -> None:
        while not heartbeat_stop.wait(10):
            try:
                if not store.heartbeat_run(run_id, worker_id):
                    return
            except Exception:
                logger.warning("Could not heartbeat run %s", run_id, exc_info=True)

    heartbeat_thread = threading.Thread(
        target=heartbeat_worker,
        daemon=True,
        name=f"run-heartbeat-{run_id[:8]}",
    )
    heartbeat_thread.start()

    on_event("run_started", {})

    conversation_id = run["conversation_id"] or run_id
    requested_workspace = run["requested_workspace"]
    allow_write = run["allow_write"]
    requested_model = run["model"]
    try:
        profile = resolve_profile(requested_model)
        model = profile.model
        prompt_mode = profile.prompt_mode
        force_research = profile.force_research
        max_completion_tokens = profile.max_completion_tokens
        timeout_seconds = profile.timeout_seconds
    except ValueError:
        model = requested_model
        prompt_mode = "custom"
        force_research = False
        max_completion_tokens = None
        timeout_seconds = None
    task = run["task"]

    sandbox: Sandbox | None = None
    active_workspace = requested_workspace

    try:
        if allow_write:
            on_event("sandbox_creating", {"workspace": requested_workspace})
            with cancellation_context(cancelled):
                sandbox = create_sandbox(requested_workspace, run_id)
            active_workspace = str(sandbox.path)
            store.update_run(
                run_id,
                sandbox_path=active_workspace,
                repository_path=str(sandbox.repository),
                base_commit=sandbox.base_commit,
            )
            on_event("sandbox_ready", {"sandbox_path": active_workspace})

        state = AgentState(
            conversation_id=conversation_id,
            user_message=task,
            model=_task_model(task, model),
            prompt_mode=prompt_mode,
            max_completion_tokens=max_completion_tokens,
            timeout_seconds=timeout_seconds,
            allow_write=allow_write,
            workspace=active_workspace,
        )
        checkpoint = run.get("checkpoint") or {}
        if checkpoint and not allow_write:
            state.observations = list(checkpoint.get("observations") or [])
            state.steps = int(checkpoint.get("steps") or 0)
            state.route_tasks = list(checkpoint.get("route_tasks") or [])
            state.task_progress = dict(checkpoint.get("task_progress") or {})
            state.successful_mutation = bool(checkpoint.get("successful_mutation"))
            state.successful_verification = bool(
                checkpoint.get("successful_verification")
            )
            state.pending_failure_categories = set(
                checkpoint.get("pending_failure_categories") or []
            )
            state.evidence_ledger = dict(checkpoint.get("evidence_ledger") or {})
            state.roadmap_requirements = list(
                checkpoint.get("roadmap_requirements") or []
            )
            state.active_roadmap_item = str(checkpoint.get("active_roadmap_item") or "")
            state.active_requirement = str(checkpoint.get("active_requirement") or "")
            state.graph_phase = str(checkpoint.get("graph_phase") or "pending")
            state.graph_history = list(checkpoint.get("graph_history") or [])
            state.model_escalations = int(checkpoint.get("model_escalations") or 0)
            state.original_model = str(checkpoint.get("original_model") or "")
            state.restored_transcript = list(checkpoint.get("messages") or [])
            on_event(
                "checkpoint_restored",
                {
                    "steps": state.steps,
                    "observations": len(state.observations),
                    "transcript_messages": len(state.restored_transcript),
                },
            )
        store.update_run(run_id, active_workspace=active_workspace)

        workspace_options = (
            {
                "allow_sandbox": True,
                "source_workspace": requested_workspace,
            }
            if sandbox is not None
            else {}
        )
        with workspace_context(
            active_workspace,
            **workspace_options,
        ), cancellation_context(cancelled):
            raise_if_cancelled()
            state.history = get_conversation(conversation_id, limit=4)
            state.memory_scope = run.get("document_scope") or str(requested_workspace)
            state.document_evidence = build_inline_document_evidence(task)

            raise_if_cancelled()
            on_event("planning", {})
            _apply_auto_route(state, on_event)
            raise_if_cancelled()

            if _uses_memory(state.prompt_mode, run.get("document_scope")):
                state.memories = memory_context(
                    search_memory(task, scope=state.memory_scope),
                    MEMORY_CONTEXT_TOKENS,
                )
                state.document_evidence = (
                    build_document_evidence(task, state.memories)
                    or state.document_evidence
                )
            else:
                state.memories = []
            research_mode = (
                force_research
                or state.requires_external_evidence
                or requires_external_search(task)
            )
            state.plan = _execution_plan(state, research_mode)
            raise_if_cancelled()
            on_event("plan_ready", {"plan": state.plan})

            answer = execute_plan(
                state,
                on_event=on_event,
                on_token=lambda content: on_event("output_delta", {"content": content}),
                should_cancel=cancelled,
                force_research=force_research or state.requires_external_evidence,
                on_checkpoint=persist_checkpoint,
            )

        with cancellation_context(cancelled):
            diff = sandbox_diff(str(sandbox.path)) if sandbox is not None else None
        if diff and diff.strip():
            transition_graph(
                state,
                "reviewing",
                reason="workspace diff ready for independent review",
                on_event=on_event,
            )
            state.diff_review = review_change(state, diff, answer)
            on_event("change_reviewed", state.diff_review)
            if state.diff_review.get("decision") != "accept":
                state.diff_blocked = True
                reasons = state.diff_review.get("reasons") or [
                    "change review rejected the diff"
                ]
                if "Incomplete requirements:" not in answer:
                    answer += "\n\nIncomplete requirements:\n- " + "\n- ".join(
                        str(reason) for reason in reasons
                    )
        diff_blocked = bool(getattr(state, "diff_blocked", False))
        unsafe_incomplete_diff = bool(diff and diff.strip()) and (
            state.partial
            or not getattr(state, "successful_mutation", False)
            or not getattr(state, "successful_verification", False)
            or bool(getattr(state, "pending_failure_categories", set()))
        )
        if diff_blocked or unsafe_incomplete_diff:
            # Never offer changes from a run that failed its completion audit.
            # This covers checklist-gaming as well as partial, unverified, or
            # otherwise unresolved mutations. A plausible diff is not proof
            # that the requested implementation is safe to apply.
            diff = None
            if unsafe_incomplete_diff:
                on_event(
                    "diff_rejected",
                    {
                        "partial": state.partial,
                        "successful_mutation": state.successful_mutation,
                        "successful_verification": state.successful_verification,
                        "pending_failure_categories": sorted(
                            state.pending_failure_categories
                        ),
                    },
                )
            if sandbox is not None:
                remove_sandbox(str(sandbox.repository), str(sandbox.path))
                sandbox = None
            store.update_run(run_id, sandbox_path=None)
            transition_graph(
                state,
                "rejected" if diff_blocked else "partial",
                reason="workspace diff was not safe to expose",
                on_event=on_event,
                force=True,
            )
        has_pending_diff = bool(diff and diff.strip())
        if has_pending_diff:
            on_event("diff_ready", {"diff": diff})
            transition_graph(
                state,
                "complete",
                reason="change review accepted the verified diff",
                on_event=on_event,
            )

        save_conversation(conversation_id, "user", task)
        save_conversation(conversation_id, "assistant", answer)
        if _uses_memory(state.prompt_mode):
            _save_memory_best_effort(task, answer, scope=state.memory_scope)

        if sandbox is not None and not has_pending_diff:
            # Nothing changed -- no point holding a worktree open for review.
            remove_sandbox(str(sandbox.repository), str(sandbox.path))
            store.update_run(run_id, sandbox_path=None)

        store.update_run(
            run_id,
            status="awaiting_approval" if has_pending_diff else "completed",
            answer=answer,
            checkpoint=None,
            worker_id=None,
            lease_expires_at=None,
            completed_at=datetime.now(timezone.utc),
        )
        on_event(
            "run_completed",
            {
                "answer": answer,
                "has_pending_diff": has_pending_diff,
                "partial": state.partial,
            },
        )

    except RunCancelled:
        logger.info("Run %s cancelled", run_id)
        sandbox_removed = sandbox is None
        if sandbox is not None:
            try:
                remove_sandbox(str(sandbox.repository), str(sandbox.path))
                sandbox_removed = True
            except Exception:
                # Keep the durable sandbox_path so startup reconciliation can
                # retry after a transient Git/filesystem failure.
                logger.exception("Failed to clean cancelled sandbox for run %s", run_id)
        store.update_run(
            run_id,
            status="cancelled",
            sandbox_path=None if sandbox_removed else str(sandbox.path),
            worker_id=None,
            lease_expires_at=None,
            completed_at=datetime.now(timezone.utc),
        )
        on_event("run_cancelled", {})
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
            worker_id=None,
            lease_expires_at=None,
            completed_at=datetime.now(timezone.utc),
        )
        event_type = "run_failed"
        if isinstance(e, ProcessKillFailed):
            event_type = "run_kill_failed"
        elif isinstance(e, subprocess.TimeoutExpired):
            event_type = "run_timed_out"
        on_event(event_type, {"error": str(e)})
    finally:
        heartbeat_stop.set()
        heartbeat_thread.join(timeout=1)
        events.flush()


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
        base_commit=run["base_commit"],
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


def _document_chunks_with_positions(text: str, size: int = 1_800, overlap: int = 240):
    """Yield complete-row chunks with zero-based source row ranges."""
    lines = text.splitlines(keepends=True)
    if not lines:
        return
    current: list[tuple[int, str]] = []
    current_size = 0
    for row_index, line in enumerate(lines):
        # Exceptionally long machine-generated lines still need a hard bound.
        if len(line) > size:
            if current:
                yield (
                    "".join(item[1] for item in current),
                    current[0][0],
                    current[-1][0],
                )
                current, current_size = [], 0
            start = 0
            while start < len(line):
                end = min(len(line), start + size)
                yield line[start:end], row_index, row_index
                if end == len(line):
                    break
                start = end - overlap
            continue
        if current and current_size + len(line) > size:
            yield "".join(item[1] for item in current), current[0][0], current[-1][0]
            retained: list[tuple[int, str]] = []
            retained_size = 0
            for previous in reversed(current):
                if retained_size + len(previous[1]) > overlap:
                    break
                retained.insert(0, previous)
                retained_size += len(previous[1])
            current, current_size = retained, retained_size
        current.append((row_index, line))
        current_size += len(line)
    if current:
        yield "".join(item[1] for item in current), current[0][0], current[-1][0]


def _document_chunks(text: str, size: int = 1_800, overlap: int = 240):
    """Compatibility wrapper returning only chunk text."""
    for chunk, _, _ in _document_chunks_with_positions(text, size, overlap):
        yield chunk


def ingest_documents(
    texts: list[str], metadata: dict[str, Any] | None = None, scope: str | None = None
):
    stored = 0

    for document_index, text in enumerate(texts):
        if not text or not text.strip():
            continue

        for chunk_index, (chunk, row_start, row_end) in enumerate(
            _document_chunks_with_positions(text)
        ):
            embedding = create_embedding(chunk)
            save_long_term_memory(
                chunk,
                embedding,
                {
                    **(metadata or {}),
                    "type": "document",
                    "scope": scope or "global",
                    "document_index": document_index,
                    "chunk_index": chunk_index,
                    "row_start": row_start,
                    "row_end": row_end,
                },
            )
            stored += 1

    return {"stored": stored, "status": "success"}


def ingest_extracted_documents(documents, scope: str | None = None) -> dict[str, Any]:
    """Store parsed document sections while preserving file/page citations."""
    stored = 0
    sources: list[str] = []
    diagnostics: list[dict[str, Any]] = []
    for document_index, document in enumerate(documents):
        diagnostics.append(
            {
                key: document.metadata.get(key)
                for key in (
                    "source",
                    "location",
                    "extraction_strategy",
                    "extraction_status",
                    "quality_score",
                    "needs_ocr",
                    "row_count",
                    "table_row_count",
                )
                if document.metadata.get(key) is not None
            }
        )
        if not document.text or not document.text.strip():
            continue
        source = str(document.metadata.get("source", "uploaded document"))
        if source not in sources:
            sources.append(source)
        for chunk_index, (chunk, row_start, row_end) in enumerate(
            _document_chunks_with_positions(document.text)
        ):
            embedding = create_embedding(chunk)
            save_long_term_memory(
                chunk,
                embedding,
                {
                    **document.metadata,
                    "type": "document",
                    "scope": scope or "global",
                    "document_index": document_index,
                    "chunk_index": chunk_index,
                    "row_start": row_start,
                    "row_end": row_end,
                },
            )
            stored += 1
    low_quality = [
        item for item in diagnostics if item.get("extraction_status") == "low_quality"
    ]
    return {
        "stored": stored,
        "sources": sources,
        "scope": scope or "global",
        "status": "success" if not low_quality else "partial",
        "diagnostics": diagnostics,
        "warnings": [
            f"{item.get('source')}, {item.get('location')}: OCR or a higher-quality source is required"
            for item in low_quality
        ],
    }
