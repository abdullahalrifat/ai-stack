import uuid
from contextlib import nullcontext
from unittest.mock import patch

from app.agent import run_agent, ingest_documents


@patch("app.agent.workspace_context", return_value=nullcontext())
@patch("app.agent.save_memory")
@patch("app.agent.save_conversation")
@patch("app.agent.execute_plan")
@patch("app.agent.create_plan")
@patch("app.agent.search_memory")
@patch("app.agent.get_conversation")
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
    mock_get_conversation.assert_called_once_with("test-conversation")
    mock_search_memory.assert_called_once_with("Hello")
    mock_create_plan.assert_called_once()
    mock_execute_plan.assert_called_once()

    assert mock_save_conversation.call_count == 2
    mock_save_memory.assert_called_once()


@patch("app.agent.workspace_context", return_value=nullcontext())
@patch("app.agent.save_memory")
@patch("app.agent.save_conversation")
@patch("app.agent.execute_plan")
@patch("app.agent.create_plan")
@patch("app.agent.search_memory")
@patch("app.agent.get_conversation")
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


@patch("app.agent.save_long_term_memory")
@patch("app.agent.create_embedding")
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


@patch("app.agent.save_long_term_memory")
@patch("app.agent.create_embedding")
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