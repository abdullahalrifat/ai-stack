from unittest.mock import patch

import httpx
import pytest
from openai import APITimeoutError

from app.agent.parser import ParserError
from app.agent.router import (
    _bounded_router_message,
    _policy_workflow,
    _requires_router_escalation,
    route_request,
)


@pytest.mark.parametrize(
    ("message", "attachment", "proposed", "expected"),
    [
        (
            "Analyze this for the long term",
            "DSE portfolio holdings",
            "research",
            "finance",
        ),
        ("Review the authentication module", "", "research", "code"),
        ("Review this repo and compare its CLI", "", "research", "code"),
        (
            "Compare current release support using primary sources",
            "",
            "quick",
            "research",
        ),
        ("Evaluate the architecture trade-offs", "", "research", "deep"),
        ("Rewrite this sentence plainly", "", "quick", "quick"),
    ],
)
def test_policy_workflow_overrides_unreliable_model_classification(
    message, attachment, proposed, expected
):
    assert _policy_workflow(message, attachment, proposed) == expected


def test_router_escalates_for_low_quality_document_evidence():
    context = '{"source":"scan.pdf","needs_ocr":true}'

    assert _requires_router_escalation("Analyze this document", context) is True


def test_router_does_not_escalate_for_repeated_chunks_from_one_source():
    context = (
        '[{"source":"report.pdf","text":"A"},'
        '{"source":"report.pdf","text":"B"},'
        '{"source":"report.pdf","text":"C"}]'
    )

    assert _requires_router_escalation("Summarize this document", context) is False


def test_router_message_preserves_request_head_and_evidence_tail():
    message = "USER REQUEST\n" + ("middle " * 2_000) + "\nFINAL EVIDENCE"

    bounded = _bounded_router_message(message, limit=1_000)

    assert bounded.startswith("USER REQUEST")
    assert bounded.endswith("FINAL EVIDENCE")
    assert len(bounded) < 1_100


@patch(
    "app.agent.router.chat",
    side_effect=APITimeoutError(
        request=httpx.Request("POST", "http://litellm/v1/chat/completions")
    ),
)
def test_router_timeout_uses_concise_deterministic_fallback(mock_chat, caplog):
    route = route_request("Review the authentication module")

    assert route.source == "fallback"
    assert route.workflow == "code"
    assert "timed out; using deterministic fallback" in caplog.text


@patch("app.agent.router.chat")
def test_route_request_skips_model_for_focused_file_question(mock_chat):
    route = route_request("Inspect only README.md and summarize it")

    assert route.workflow == "quick"
    assert route.source == "deterministic_fast_path"
    mock_chat.assert_not_called()


@patch("app.agent.router.extract_json", side_effect=ParserError("bad JSON"))
@patch("app.agent.router.chat", return_value="not json")
def test_file_change_request_keeps_code_workflow(mock_chat, mock_extract_json):
    route = route_request("Review README.md and update it")

    assert route.workflow == "code"
    assert route.source == "fallback"
    mock_chat.assert_called_once()


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_builds_validated_finance_contract(mock_chat, mock_extract_json):
    mock_chat.return_value = '{"workflow":"finance"}'
    mock_extract_json.return_value = {
        "workflow": "finance",
        "translated_task": "Analyze every DSE portfolio holding.",
        "requires_external_evidence": False,
        "entities": ["Fortune Shoes", "Orion Pharma", "fortune shoes"],
        "constraints": ["Use current DSE evidence", "  Use current DSE evidence  "],
        "deliverables": ["Holding-level findings", "Portfolio risk summary"],
        "plan": ["Extract holdings", "Research every company", "Synthesize risks"],
    }

    route = route_request(
        "Analyze my portfolio",
        [{"text": "Fortune Shoes and Orion Pharma"}],
    )

    assert route.workflow == "finance"
    assert route.requires_external_evidence is True
    assert route.entities == ["Fortune Shoes", "Orion Pharma"]
    assert route.constraints == ["Use current DSE evidence"]
    assert route.deliverables == ["Holding-level findings", "Portfolio risk summary"]
    assert route.plan[1] == "[step_2/finance] Research every company"
    assert "Attachment excerpts" in mock_chat.call_args.args[0][1]["content"]


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_rejects_unusable_translation(mock_chat, mock_extract_json):
    mock_chat.return_value = '{"workflow":"code","translated_task":"fix"}'
    mock_extract_json.return_value = {
        "workflow": "code",
        "translated_task": "fix",
    }

    route = route_request("Fix the failing authentication test")

    assert route.workflow == "code"
    assert route.translated_task == "Fix the failing authentication test"
    assert route.source == "fallback"


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_builds_dependency_validated_task_graph(
    mock_chat, mock_extract_json
):
    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {
        "workflow": "finance",
        "complexity": "complex",
        "translated_task": "Analyze every holding and synthesize portfolio-level risks.",
        "entities": ["Fortune Shoes", "Orion Pharma"],
        "constraints": ["Use dated external evidence"],
        "deliverables": ["Holding analysis", "Portfolio synthesis"],
        "missing_inputs": ["Investor risk tolerance"],
        "assumptions": ["Use a long-term, moderate-risk analytical frame"],
        "tasks": [
            {
                "id": "research_holdings",
                "objective": "Collect current evidence for every portfolio holding",
                "workflow": "finance",
                "depends_on": [],
                "required_evidence": ["DSE prices", "annual reports", "recent news"],
                "completion_criteria": ["Every named holding has dated evidence"],
            },
            {
                "id": "synthesize_portfolio",
                "objective": "Assess concentration, diversification, and long-term scenarios",
                "workflow": "deep",
                "depends_on": ["research_holdings"],
                "required_evidence": ["completed holding research"],
                "completion_criteria": ["Every requested deliverable is addressed"],
            },
        ],
    }

    route = route_request("Analyze this portfolio")

    assert route.complexity == "complex"
    assert route.tasks[1].depends_on == ["research_holdings"]
    assert route.tasks[1].workflow == "deep"
    assert route.missing_inputs == ["Investor risk tolerance"]
    assert route.assumptions == ["Use a long-term, moderate-risk analytical frame"]
    assert route.plan == [
        "[research_holdings/finance] Collect current evidence for every portfolio holding",
        "[synthesize_portfolio/deep] Assess concentration, diversification, and long-term scenarios",
    ]


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_rejects_cyclic_task_graph(mock_chat, mock_extract_json):
    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {
        "workflow": "code",
        "translated_task": "Inspect and repair the authentication implementation.",
        "tasks": [
            {
                "id": "inspect",
                "objective": "Inspect the authentication implementation",
                "workflow": "code",
                "depends_on": ["repair"],
            },
            {
                "id": "repair",
                "objective": "Repair and verify the authentication implementation",
                "workflow": "code",
                "depends_on": ["inspect"],
            },
        ],
    }

    route = route_request("Fix authentication")

    assert route.source == "fallback"
    assert route.tasks[0].id == "execute_request"


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_ignores_unnecessary_external_evidence_request(
    mock_chat, mock_extract_json
):
    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {
        "workflow": "research",
        "translated_task": "Review the authentication module without modifying files.",
        "requires_external_evidence": True,
        "tasks": [
            {
                "id": "review_auth",
                "objective": "Review the authentication module for security problems",
                "workflow": "research",
                "depends_on": [],
            }
        ],
    }

    route = route_request("Review the authentication module, but do not modify files")

    assert route.workflow == "code"
    assert route.tasks[0].workflow == "code"
    assert route.requires_external_evidence is False


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_route_request_keeps_hybrid_repo_comparison_tasks(mock_chat, mock_extract_json):
    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {
        "workflow": "research",
        "translated_task": "Compare the mounted repository with Claude CLI.",
        "requires_external_evidence": True,
        "tasks": [
            {
                "id": "inspect_repo",
                "objective": "Inspect the mounted repository capabilities",
                "workflow": "code",
                "depends_on": [],
            },
            {
                "id": "compare_cli",
                "objective": "Compare those capabilities with Claude CLI",
                "workflow": "research",
                "depends_on": ["inspect_repo"],
            },
        ],
    }

    route = route_request("Review this repo and compare it with Claude CLI")

    assert route.workflow == "code"
    assert [task.workflow for task in route.tasks] == ["code", "research"]
    assert route.requires_external_evidence is True


@patch("app.agent.router.extract_json")
@patch("app.agent.router.chat")
def test_router_receives_multiple_document_sections_and_plans_validation(
    mock_chat, mock_extract_json
):
    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {
        "workflow": "deep",
        "translated_task": "Analyze the active obligations table, excluding historical examples.",
        "entities": ["Contract A", "Contract B"],
        "tasks": [
            {
                "id": "extract_active_rows",
                "objective": "Extract and validate every row in Active Obligations",
                "workflow": "deep",
                "depends_on": [],
                "completion_criteria": ["Historical Examples rows are excluded"],
            },
            {
                "id": "analyze_obligations",
                "objective": "Analyze the validated active obligations",
                "workflow": "deep",
                "depends_on": ["extract_active_rows"],
            },
        ],
    }
    context = [
        {"excerpt": "Active Obligations\nContract A | 2028\nContract B | 2030"},
        {"excerpt": "Historical Examples\nOld Contract | 2019"},
    ]

    route = route_request("Analyze our current contractual obligations", context)

    router_input = mock_chat.call_args.args[0][1]["content"]
    assert "Active Obligations" in router_input
    assert "Historical Examples" in router_input
    assert route.entities == ["Contract A", "Contract B"]
    assert route.tasks[1].depends_on == ["extract_active_rows"]


@patch("app.agent.router.extract_json", side_effect=ParserError("bad JSON"))
@patch("app.agent.router.chat", return_value="not json")
def test_route_request_falls_back_safely_for_portfolio(mock_chat, mock_extract_json):
    route = route_request("Give me a long-term analysis of this stock portfolio")

    assert route.workflow == "finance"
    assert route.requires_external_evidence is True
    assert route.source == "fallback"
