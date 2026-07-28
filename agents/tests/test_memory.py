from types import SimpleNamespace
from unittest.mock import call, patch

from app.memory.memory import (
    _rerank,
    _scoped_payloads,
    memory_context,
    save_memory,
    search_memory,
)


def test_rerank_prefers_exact_document_evidence():
    results = [
        {
            "memory": {"text": "General market discussion", "source": "general.pdf"},
            "score": 0.8,
        },
        {
            "memory": {
                "text": "Renata revenue increased in FY2025",
                "source": "report.pdf",
                "location": "page 12",
            },
            "score": 0.7,
        },
    ]

    ranked = _rerank("Renata revenue FY2025", results, 2)

    assert ranked[0]["memory"]["source"] == "report.pdf"
    assert ranked[0]["citation"] == "Document: report.pdf, page 12"


def test_memory_context_respects_budget_and_keeps_citation():
    result = memory_context(
        [{"memory": {"text": "x" * 1_000, "source": "report.pdf"}, "score": 0.9}],
        token_budget=100,
    )

    assert len(result[0]["excerpt"]) == 400
    assert result[0]["citation"] == "Document: report.pdf"


def test_rerank_penalizes_supplementary_section_for_current_query():
    results = [
        {
            "memory": {
                "text": "Active contract Alpha",
                "source": "report.pdf",
                "section_kind": "primary",
            },
            "score": 0.7,
        },
        {
            "memory": {
                "text": "Historical contract Alpha",
                "source": "report.pdf",
                "section_kind": "supplementary",
            },
            "score": 0.75,
        },
    ]

    ranked = _rerank("current contract Alpha", results, 2)

    assert ranked[0]["memory"]["section_kind"] == "primary"


@patch("app.memory.memory._collection_exists", return_value=True)
@patch("app.memory.memory.qdrant.scroll")
def test_scoped_payloads_paginates_beyond_first_page(mock_scroll, _exists):
    first = [
        SimpleNamespace(id=index, payload={"text": f"page one {index}"})
        for index in range(256)
    ]
    second = [SimpleNamespace(id="last", payload={"text": "page two"})]
    mock_scroll.side_effect = [(first, "next-page"), (second, None)]

    results = _scoped_payloads("/workspace/repo", limit=300)

    assert len(results) == 257
    assert results[-1]["memory"]["text"] == "page two"
    assert mock_scroll.call_args_list[1] == call(
        collection_name="agent_memory",
        scroll_filter=mock_scroll.call_args_list[0].kwargs["scroll_filter"],
        limit=44,
        offset="next-page",
        with_payload=True,
        with_vectors=False,
    )


@patch("app.memory.memory._scoped_payloads")
@patch("app.memory.memory._lexical_payloads")
@patch("app.memory.memory.search_long_term_memory")
@patch("app.memory.memory.create_embedding", return_value=[0.1])
def test_search_memory_adds_adjacent_chunks_from_selected_section(
    _embedding, mock_semantic, mock_lexical, mock_scoped
):
    mock_semantic.return_value = [
        {
            "id": "match",
            "memory": {
                "text": "Renata revenue",
                "source": "report.pdf",
                "location": "page 12",
            },
            "score": 0.9,
        }
    ]
    mock_lexical.return_value = []
    mock_scoped.return_value = [
        {
            "id": "adjacent",
            "memory": {
                "text": "Table continuation",
                "source": "report.pdf",
                "location": "page 12",
            },
            "score": 0.0,
        }
    ]

    results = search_memory("Renata revenue", limit=1, scope="/workspace/repo")

    assert {result["id"] for result in results} == {"match", "adjacent"}
    mock_scoped.assert_called_once_with("/workspace/repo")


@patch("app.memory.memory.save_long_term_memory")
@patch("app.memory.memory.create_embedding", return_value=[0.1, 0.2])
def test_save_memory_marks_generated_content_and_scope(
    _embedding, mock_save_long_term_memory
):
    save_memory("question", "answer", scope="/workspace/repo")

    metadata = mock_save_long_term_memory.call_args.args[2]
    assert metadata == {
        "type": "conversation",
        "generated": True,
        "scope": "/workspace/repo",
    }
