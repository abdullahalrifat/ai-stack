from unittest.mock import patch

from app.agent.router import _policy_workflow, route_request
from app.agent.parser import ParserError
import pytest


@pytest.mark.parametrize(
    ("message", "attachment", "proposed", "expected"),
    [
        ("Analyze this for the long term", "DSE portfolio holdings", "research", "finance"),
        ("Review the authentication module", "", "research", "code"),
        ("Compare current release support using primary sources", "", "quick", "research"),
        ("Evaluate the architecture trade-offs", "", "research", "deep"),
        ("Rewrite this sentence plainly", "", "quick", "quick"),
    ],
)
def test_policy_workflow_overrides_unreliable_model_classification(
    message, attachment, proposed, expected
):
    assert _policy_workflow(message, attachment, proposed) == expected


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

    route = route_request("Analyze my portfolio", [{"text": "Fortune Shoes"}])

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
def test_route_request_builds_dependency_validated_task_graph(mock_chat, mock_extract_json):
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


@patch("app.agent.router.extract_json", side_effect=ParserError("bad JSON"))
@patch("app.agent.router.chat", return_value="not json")
def test_route_request_falls_back_safely_for_portfolio(mock_chat, mock_extract_json):
    route = route_request("Give me a long-term analysis of this stock portfolio")

    assert route.workflow == "finance"
    assert route.requires_external_evidence is True
    assert route.source == "fallback"
