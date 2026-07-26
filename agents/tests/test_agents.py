import uuid
from contextlib import nullcontext
from unittest.mock import patch

from app.agent.service import ingest_documents, run_agent
from app.agent import service as agent
from app.agent.router import PlannedTask, RouteDecision
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


def test_memory_persistence_failure_is_non_fatal(caplog):
    with patch("app.agent.service.save_memory", side_effect=RuntimeError("embedding too long")):
        agent._save_memory_best_effort("question", "answer")

    assert "Could not persist conversation memory" in caplog.text


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


def test_run_event_buffer_batches_output_and_publishes_durable_event(monkeypatch):
    class Store:
        def __init__(self):
            self.calls = []

        def append_event(self, run_id, event_type, payload):
            self.calls.append((run_id, event_type, payload))
            return {"id": len(self.calls), "run_id": run_id, "event_type": event_type, "payload": payload}

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
    assert events[0][1]["task_count"] == 1
    assert events[0][1]["task_workflows"] == ["finance"]


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
    with patch("app.agent.service.get_run_store", return_value=store), patch("app.agent.service.execute_plan") as execute:
        agent.execute_run("run-1")

    assert store.run["status"] == "cancelled"
    execute.assert_not_called()
