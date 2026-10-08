import uuid
from contextlib import nullcontext
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

from app.agent import service as agent
from app.agent.router import PlannedTask, RouteDecision
from app.agent.service import ingest_documents, run_agent
from app.agent.state import AgentState


@patch("app.agent.service.workspace_context", return_value=nullcontext())
@patch("app.agent.service.save_memory")
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.execute_plan")
@patch("app.agent.service.create_plan")
@patch("app.agent.service.search_memory")
@patch("app.agent.service.get_conversation")
def test_run_agent(
    mock_get_conversation,
    mock_search_memory,
    mock_create_plan,
    mock_execute_plan,
    mock_save_conversation,
    mock_save_memory,
    mock_workspace,
):
    mock_get_conversation.return_value = []
    mock_search_memory.return_value = []
    mock_create_plan.return_value = [{"step": "answer"}]
    mock_execute_plan.return_value = "Hello from agent"

    result = run_agent(
        message="Hello",
        conversation_id="test-conversation",
    )

    assert result["conversation_id"] == "test-conversation"
    assert result["answer"] == "Hello from agent"

    mock_workspace.assert_called_once()
    mock_get_conversation.assert_called_once_with("test-conversation", limit=4)
    mock_search_memory.assert_not_called()
    mock_create_plan.assert_not_called()
    mock_execute_plan.assert_called_once()

    assert mock_save_conversation.call_count == 2
    mock_save_memory.assert_not_called()


@patch("app.agent.service.workspace_context", return_value=nullcontext())
@patch("app.agent.service.save_memory")
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.execute_plan")
@patch("app.agent.service.create_plan")
@patch("app.agent.service.search_memory")
@patch("app.agent.service.get_conversation")
def test_run_agent_generates_conversation_id(
    mock_get_conversation,
    mock_search_memory,
    mock_create_plan,
    mock_execute_plan,
    mock_save_conversation,
    mock_save_memory,
    mock_workspace,
):
    mock_get_conversation.return_value = []
    mock_search_memory.return_value = []
    mock_create_plan.return_value = []
    mock_execute_plan.return_value = "OK"

    result = run_agent("Hi")

    assert "conversation_id" in result

    # Verify it's a valid UUID
    uuid.UUID(result["conversation_id"])

    assert result["answer"] == "OK"

    mock_workspace.assert_called_once()


def test_memory_retrieval_failure_is_non_fatal(caplog):
    with (
        patch(
            "app.agent.service.search_memory",
            side_effect=RuntimeError("inference embeddings unavailable"),
        ),
        patch("app.agent.service.memory_context") as mock_memory_context,
    ):
        result = agent._load_memory_best_effort(
            "Reply with exactly OK.",
            "/workspace/repo",
        )

    assert result == []
    mock_memory_context.assert_not_called()
    assert "Could not retrieve conversation memory" in caplog.text


def test_memory_persistence_failure_is_non_fatal(caplog):
    with (
        patch("app.agent.service.GENERATED_MEMORY_ENABLED", True),
        patch(
            "app.agent.service.save_memory",
            side_effect=RuntimeError("embedding too long"),
        ),
    ):
        agent._save_memory_best_effort("question", "answer", scope="/workspace/repo")

    assert "Could not persist conversation memory" in caplog.text


def test_generated_memory_is_opt_in():
    with (
        patch("app.agent.service.GENERATED_MEMORY_ENABLED", False),
        patch("app.agent.service.save_memory") as mock_save_memory,
    ):
        agent._save_memory_best_effort("question", "answer", scope="/workspace/repo")

    mock_save_memory.assert_not_called()


@patch("app.agent.service.workspace_context", return_value=nullcontext())
@patch("app.agent.service.route_request")
@patch("app.agent.service.search_memory", return_value=[])
@patch("app.agent.service.memory_context", return_value=[])
@patch("app.agent.service.get_conversation", return_value=[])
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.save_memory")
@patch("app.agent.service.execute_plan", return_value="ok")
def test_run_agent_auto_route_loads_memory_after_routing(
    mock_execute_plan,
    mock_save_memory,
    mock_save_conversation,
    mock_get_conversation,
    mock_memory_context,
    mock_search_memory,
    mock_route_request,
    mock_workspace,
):
    mock_route_request.return_value = RouteDecision(
        workflow="research",
        translated_task="Inspect and summarize the repository.",
        requires_external_evidence=True,
        entities=[],
        deliverables=[],
        tasks=[],
        constraints=[],
        missing_inputs=[],
        assumptions=[],
        selected_document_sections=[],
        extracted_records=[],
        validation_warnings=[],
        source="test",
    )

    result = agent.run_agent(
        message="Inspect the repository and summarize findings.",
        conversation_id="test-conversation",
        prompt_mode="auto",
    )

    assert result["answer"] == "ok"
    assert mock_search_memory.call_count == 1
    assert mock_memory_context.call_count == 1


@patch("app.agent.service.save_long_term_memory")
@patch("app.agent.service.create_embedding")
def test_ingest_documents(
    mock_create_embedding,
    mock_save_long_term_memory,
):
    mock_create_embedding.return_value = [0.1, 0.2, 0.3]

    result = ingest_documents(
        [
            "Document one",
            "Document two",
        ]
    )

    assert result["status"] == "success"
    assert result["stored"] == 2

    assert mock_create_embedding.call_count == 2
    assert mock_save_long_term_memory.call_count == 2


@patch("app.agent.service.save_long_term_memory")
@patch("app.agent.service.create_embedding")
def test_ingest_documents_skips_empty_documents(
    mock_create_embedding,
    mock_save_long_term_memory,
):
    result = ingest_documents(
        [
            "",
            "   ",
            None,
            "Real document",
        ]
    )

    assert result["status"] == "success"
    assert result["stored"] == 1

    mock_create_embedding.assert_called_once_with("Real document")
    mock_save_long_term_memory.assert_called_once()


class FakeRunStore:
    def __init__(self):
        self.run = {
            "id": "run-1",
            "status": "queued",
            "conversation_id": None,
            "requested_workspace": "/workspace/project",
            "allow_write": False,
            "model": "code",
            "task": "inspect project",
        }
        self.events = []

    def get_run(self, run_id):
        assert run_id == "run-1"
        return self.run

    def update_run(self, run_id, **fields):
        assert run_id == "run-1"
        self.run.update(fields)

    def claim_run(self, run_id, worker_id, lease_seconds=600):
        assert run_id == "run-1"
        self.run.update(status="running", worker_id=worker_id)
        return True

    def heartbeat_run(self, run_id, worker_id, lease_seconds=600):
        assert run_id == "run-1"
        return self.run.get("worker_id") == worker_id

    def append_event(self, run_id, event_type, payload):
        assert run_id == "run-1"
        self.events.append((event_type, payload))

    def is_cancel_requested(self, run_id):
        assert run_id == "run-1"
        return False


@patch("app.agent.service.save_memory")
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.execute_plan", return_value="completed answer")
@patch("app.agent.service.create_plan", return_value=["inspect"])
@patch("app.agent.service.search_memory", return_value=[])
@patch("app.agent.service.get_conversation", return_value=[])
@patch("app.agent.service.workspace_context", return_value=nullcontext())
def test_execute_read_only_run_persists_answer_and_events(
    mock_workspace,
    mock_history,
    mock_memory,
    mock_plan,
    mock_execute,
    mock_save_conversation,
    mock_save_memory,
):
    store = FakeRunStore()
    with patch("app.agent.service.get_run_store", return_value=store):
        agent.execute_run("run-1")

    assert store.run["status"] == "completed"
    assert store.run["answer"] == "completed answer"
    assert [event for event, _ in store.events] == [
        "run_started",
        "planning",
        "plan_ready",
        "run_completed",
    ]
    mock_workspace.assert_called_once_with("/workspace/project")
    mock_execute.assert_called_once()
    assert mock_save_conversation.call_count == 2
    mock_save_memory.assert_not_called()


@patch("app.agent.service.remove_sandbox")
@patch(
    "app.agent.service.sandbox_diff", return_value="diff --git a/TODO.md b/TODO.md\n"
)
@patch("app.agent.service.create_sandbox")
@patch("app.agent.service.workspace_context", return_value=nullcontext())
@patch("app.agent.service.save_memory")
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.execute_plan")
@patch("app.agent.service.create_plan")
@patch("app.agent.service.search_memory")
@patch("app.agent.service.get_conversation")
def test_write_run_with_blocked_diff_is_not_offered_for_approval(
    mock_get_conversation,
    mock_search_memory,
    mock_create_plan,
    mock_execute_plan,
    mock_save_conversation,
    mock_save_memory,
    mock_workspace,
    mock_create_sandbox,
    mock_sandbox_diff,
    mock_remove_sandbox,
):
    store = FakeRunStore()
    store.run["allow_write"] = True
    store.run["task"] = "Review this project and from TODO list implement Tier 3"
    fake_sandbox = SimpleNamespace(
        path="/sandboxes/project-run-1",
        repository="/workspace/project",
        base_commit="abc123",
    )
    mock_create_sandbox.return_value = fake_sandbox
    mock_get_conversation.return_value = []
    mock_search_memory.return_value = []

    def fake_execute(state, **kwargs):
        # Simulate the executor's final rejection: only a doc/marker file was
        # mutated, so the run must not surface a pending diff for approval.
        state.diff_blocked = True
        state.partial = True
        return (
            "analysis only.\n\nIncomplete requirements:\n- change only modified "
            "documentation/marker files (TODO/README/roadmap); an implementation "
            "request must change code files"
        )

    mock_execute_plan.side_effect = fake_execute

    with patch("app.agent.service.get_run_store", return_value=store):
        agent.execute_run("run-1")

    assert store.run["status"] == "completed"
    assert store.run["sandbox_path"] is None
    assert store.run["answer"].startswith("analysis only.")
    assert "change only modified documentation/marker files" in store.run["answer"]
    mock_sandbox_diff.assert_called_once_with("/sandboxes/project-run-1")
    mock_remove_sandbox.assert_called_once_with(
        "/workspace/project", "/sandboxes/project-run-1"
    )
    assert "diff_ready" not in [event for event, _ in store.events]
    assert "diff_rejected" in [event for event, _ in store.events]
    assert store.run["status"] != "awaiting_approval"


def test_run_event_buffer_batches_output_and_publishes_durable_event(monkeypatch):
    class Store:
        def __init__(self):
            self.calls = []

        def append_event(self, run_id, event_type, payload):
            self.calls.append((run_id, event_type, payload))
            return {
                "id": len(self.calls),
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
            }

    class Publisher:
        def __init__(self):
            self.events = []

        def publish(self, event):
            self.events.append(event)

    store, publisher = Store(), Publisher()
    monkeypatch.setattr(agent, "RUN_EVENT_BATCH_CHARS", 5)
    monkeypatch.setattr(agent, "get_event_publisher", lambda: publisher)
    buffer = agent.RunEventBuffer(store, "run-1")

    buffer.emit("output_delta", {"content": "abc"})
    assert store.calls == []
    buffer.emit("output_delta", {"content": "def"})

    assert store.calls == [("run-1", "output_delta", {"content": "abcdef"})]
    assert publisher.events[0]["payload"] == {"content": "abcdef"}


def test_run_event_buffer_logs_auditable_failure_reason(caplog, monkeypatch):
    class Store:
        def append_event(self, run_id, event_type, payload):
            return {
                "id": 1,
                "run_id": run_id,
                "event_type": event_type,
                "payload": payload,
            }

    publisher = MagicMock()
    monkeypatch.setattr(agent, "get_event_publisher", lambda: publisher)
    buffer = agent.RunEventBuffer(Store(), "run-1")

    buffer.emit(
        "answer_audit_failed",
        {"failures": ["requested verification has not completed successfully"]},
    )

    assert "answer_audit_failed" in caplog.text
    assert "requested verification has not completed successfully" in caplog.text


def test_run_event_buffer_survives_transient_database_restart(caplog):
    class Store:
        def append_event(self, *_args, **_kwargs):
            raise ConnectionError("database restarting")

    buffer = agent.RunEventBuffer(Store(), "run-1")

    buffer.emit("step_started", {"step": 2})

    assert "Could not persist event step_started" in caplog.text


def test_durable_run_uses_independent_worker_heartbeat(monkeypatch):
    store = FakeRunStore()
    started = []

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            started.append(self.target)

        def join(self, timeout=None):
            assert timeout == 1

    monkeypatch.setattr(agent.threading, "Thread", Thread)
    monkeypatch.setattr(agent, "get_conversation", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(agent, "search_memory", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(agent, "create_plan", lambda *_args, **_kwargs: [])
    monkeypatch.setattr(agent, "execute_plan", lambda *_args, **_kwargs: "done")
    monkeypatch.setattr(agent, "save_conversation", lambda *_args, **_kwargs: None)
    monkeypatch.setattr(
        agent, "workspace_context", lambda *_args, **_kwargs: nullcontext()
    )

    with patch("app.agent.service.get_run_store", return_value=store):
        agent.execute_run("run-1")

    assert len(started) == 1


@patch("app.agent.service.save_memory")
@patch("app.agent.service.save_conversation")
@patch("app.agent.service.execute_plan", return_value="continued answer")
@patch("app.agent.service.create_plan", return_value=["inspect"])
@patch("app.agent.service.search_memory", return_value=[])
@patch("app.agent.service.get_conversation", return_value=[])
@patch("app.agent.service.workspace_context", return_value=nullcontext())
def test_execute_read_only_run_restores_checkpoint_transcript(
    mock_workspace,
    mock_history,
    mock_memory,
    mock_plan,
    mock_execute,
    mock_save_conversation,
    mock_save_memory,
    monkeypatch,
):
    store = FakeRunStore()
    store.run["checkpoint"] = {
        "steps": 3,
        "observations": [{"tool": "read_file", "result": "file content"}],
        "route_tasks": [],
        "task_progress": {},
        "successful_mutation": False,
        "successful_verification": False,
        "pending_failure_categories": [],
        "messages": [
            {"role": "assistant", "content": ""},
            {"role": "tool", "tool_call_id": "call_1", "content": "file content"},
        ],
    }

    class Thread:
        def __init__(self, *, target, **_kwargs):
            self.target = target

        def start(self):
            pass

        def join(self, timeout=None):
            assert timeout == 1

    monkeypatch.setattr(agent.threading, "Thread", Thread)
    with patch("app.agent.service.get_run_store", return_value=store):
        agent.execute_run("run-1")

    assert store.run["status"] == "completed"
    assert store.run["answer"] == "continued answer"
    executed_state = mock_execute.call_args.args[0]
    assert executed_state.steps == 3
    assert executed_state.observations == [
        {"tool": "read_file", "result": "file content"}
    ]
    assert executed_state.restored_transcript == [
        {"role": "assistant", "content": ""},
        {"role": "tool", "tool_call_id": "call_1", "content": "file content"},
    ]
    assert "checkpoint_restored" in [event for event, _ in store.events]


def test_auto_route_hands_full_planning_contract_to_executor(monkeypatch):
    decision = RouteDecision(
        workflow="finance",
        translated_task="Analyze every portfolio holding.",
        requires_external_evidence=True,
        complexity="complex",
        entities=["Fortune Shoes"],
        constraints=["Use dated evidence"],
        deliverables=["Holding analysis"],
        missing_inputs=["Risk tolerance"],
        assumptions=["Use a long-term analytical frame"],
        tasks=[
            PlannedTask(
                id="research_holding",
                objective="Research the named holding",
                workflow="finance",
                required_evidence=["DSE data"],
                completion_criteria=["Dated evidence is cited"],
            )
        ],
    )
    monkeypatch.setattr(agent, "route_request", lambda *_args: decision)
    state = AgentState(
        conversation_id="conversation",
        user_message="Analyze this portfolio",
        prompt_mode="auto",
    )
    events = []

    agent._apply_auto_route(state, lambda kind, payload: events.append((kind, payload)))

    assert state.prompt_mode == "finance"
    assert state.requires_external_evidence is True
    assert "Known missing inputs" in state.execution_brief
    assert "completion_criteria: Dated evidence is cited" in state.execution_brief
    assert state.plan == ["[research_holding/finance] Research the named holding"]
    assert state.expert_dispatch is True
    assert events[0][1]["task_count"] == 1
    assert events[0][1]["task_workflows"] == ["finance"]


def test_auto_route_simple_request_skips_expert_dispatch(monkeypatch):
    decision = RouteDecision(
        workflow="code",
        translated_task="Add a docstring to this function.",
        requires_external_evidence=False,
        complexity="simple",
        tasks=[
            PlannedTask(
                id="edit_docstring",
                objective="Add the docstring",
                workflow="code",
            )
        ],
    )
    monkeypatch.setattr(agent, "route_request", lambda *_args: decision)
    state = AgentState(
        conversation_id="conversation",
        user_message="Add a docstring to this function.",
        prompt_mode="auto",
    )

    agent._apply_auto_route(state)

    assert state.expert_dispatch is False


def test_auto_route_removes_entities_found_only_in_excluded_sections(monkeypatch):
    decision = RouteDecision(
        workflow="finance",
        translated_task="Analyze the selected portfolio records.",
        requires_external_evidence=True,
        entities=["Current Company", "Historical Example Limited"],
        tasks=[
            PlannedTask(
                id="analyze_records",
                objective="Analyze selected portfolio records",
                workflow="finance",
            )
        ],
    )
    monkeypatch.setattr(agent, "route_request", lambda *_: decision)
    state = AgentState(conversation_id="test", user_message="Analyze my portfolio")
    state.prompt_mode = "auto"
    state.document_evidence = {
        "records": [{"text": "Current Company | 100"}],
        "excluded_entities": ["Historical Example Limited BO"],
    }

    agent._apply_auto_route(state)

    assert state.routing_entities == ["Current Company"]
    assert "Entities rejected" in state.execution_brief


def test_cancelled_before_start_is_not_executed(monkeypatch):
    store = FakeRunStore()
    store.is_cancel_requested = lambda _run_id: True
    monkeypatch.setattr(agent, "get_event_publisher", lambda: None)
    with (
        patch("app.agent.service.get_run_store", return_value=store),
        patch("app.agent.service.execute_plan") as execute,
    ):
        agent.execute_run("run-1")

    assert store.run["status"] == "cancelled"
    execute.assert_not_called()
