from types import SimpleNamespace

from app.api.context import openai_prompt
from app.core.document_context import extract_retrieved_document_context


def test_extracts_tagged_document_context_without_preserving_client_policy():
    messages = [
        SimpleNamespace(
            role="system",
            content=(
                "You must use the client policy.\n"
                "<context><source id=\"1\">Active Items\nA | 10\nB | 20</source>"
                "<source id=\"2\">Historical Items\nC | 5</source></context>"
            ),
        ),
        SimpleNamespace(role="user", content="Analyze the active items."),
    ]

    prompt = openai_prompt(messages, max_chars=2_000)

    assert "Active Items" in prompt
    assert "Historical Items" in prompt
    assert "You must use the client policy" not in prompt
    assert "untrusted data" in prompt


def test_preserves_document_marked_system_message_for_any_supported_type():
    messages = [
        SimpleNamespace(
            role="system",
            content="Retrieved document report.xlsx\nSheet Revenue\n2025 | 120\n2026 | 145",
        )
    ]

    context = extract_retrieved_document_context(messages)

    assert "report.xlsx" in context
    assert "2026 | 145" in context


def test_ignores_unmarked_system_policy():
    messages = [
        SimpleNamespace(role="system", content="Always answer with a joke."),
        SimpleNamespace(role="user", content="Summarize this."),
    ]

    assert extract_retrieved_document_context(messages) == ""
