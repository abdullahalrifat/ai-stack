from app.memory.memory import _rerank, memory_context


def test_rerank_prefers_exact_document_evidence():
    results = [
        {"memory": {"text": "General market discussion", "source": "general.pdf"}, "score": 0.8},
        {"memory": {"text": "Renata revenue increased in FY2025", "source": "report.pdf", "location": "page 12"}, "score": 0.7},
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
