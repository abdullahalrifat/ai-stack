from unittest.mock import patch

from app.agent.parser import ParserError
from app.agent.planner import create_plan, replan
from app.core.config import PLANNER_MAX_COMPLETION_TOKENS, REPLAN_MAX_COMPLETION_TOKENS


class DummyState:
    def __init__(self, user_message, plan=None):
        self.user_message = user_message
        self.plan = plan or []


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_replan_returns_revised_plan(mock_chat, mock_extract_json):
    state = DummyState("Fix the failing build", plan=["run tests", "fix errors"])
    mock_chat.return_value = '{"plan": ["read the log", "fix root cause", "verify"]}'
    mock_extract_json.return_value = {
        "plan": ["read the log", "fix root cause", "verify"]
    }

    plan = replan(state, "run_tests failed: exit 1")

    assert plan == ["read the log", "fix root cause", "verify"]
    messages = mock_chat.call_args.args[0]
    assert len(messages) == 2
    assert "Current plan" in messages[1]["content"]
    assert "run_tests failed: exit 1" in messages[1]["content"]
    assert mock_chat.call_args.kwargs["max_tokens"] == REPLAN_MAX_COMPLETION_TOKENS


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_replan_returns_plan_list_from_response(mock_chat, mock_extract_json):
    state = DummyState("Task", plan=["step one"])
    mock_extract_json.return_value = {"plan": ["new a", "new b"]}

    plan = replan(state, "failures")

    assert plan == ["new a", "new b"]


@patch("app.agent.planner.extract_json", side_effect=ParserError("incomplete JSON"))
@patch("app.agent.planner.chat", return_value="nonsense")
def test_replan_keeps_current_plan_on_invalid_json(mock_chat, mock_extract_json):
    state = DummyState("Task", plan=["keep me"])

    plan = replan(state, "failures")

    assert plan == ["keep me"]
    mock_chat.assert_called_once()


@patch("app.agent.planner.extract_json", side_effect=RuntimeError("model down"))
@patch("app.agent.planner.chat")
def test_replan_keeps_current_plan_on_model_error(mock_chat, mock_extract_json):
    state = DummyState("Task", plan=["keep me"])

    plan = replan(state, "failures")

    assert plan == ["keep me"]


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_replan_caps_plan_to_five_steps(mock_chat, mock_extract_json):
    state = DummyState("Task", plan=["old"])
    mock_extract_json.return_value = {"plan": ["a", "b", "c", "d", "e", "f"]}

    plan = replan(state, "failures")

    assert plan == ["a", "b", "c", "d", "e"]


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_create_plan_success(
    mock_chat,
    mock_extract_json,
):
    state = DummyState("Build a Python API")

    mock_chat.return_value = """
    {
        "plan": [
            {
                "tool": "filesystem",
                "action": "list_files"
            }
        ]
    }
    """

    mock_extract_json.return_value = {
        "plan": [
            {
                "tool": "filesystem",
                "action": "list_files",
            }
        ]
    }

    plan = create_plan(state)

    assert plan == [
        {
            "tool": "filesystem",
            "action": "list_files",
        }
    ]

    mock_chat.assert_called_once()
    assert mock_chat.call_args.kwargs["max_tokens"] == PLANNER_MAX_COMPLETION_TOKENS
    mock_extract_json.assert_called_once_with(mock_chat.return_value)


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_create_plan_returns_empty_when_no_plan(
    mock_chat,
    mock_extract_json,
):
    state = DummyState("Hello")

    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {}

    plan = create_plan(state)

    assert plan == []


@patch("app.agent.planner.extract_json")
@patch("app.agent.planner.chat")
def test_create_plan_passes_correct_messages(
    mock_chat,
    mock_extract_json,
):
    state = DummyState("Find Docker logs")

    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {"plan": []}

    create_plan(state)

    messages = mock_chat.call_args.args[0]

    assert len(messages) == 2

    assert messages[0]["role"] == "system"
    assert messages[1]["role"] == "user"
    assert messages[1]["content"] == "Find Docker logs"


@patch("app.agent.planner.extract_json", side_effect=ParserError("incomplete JSON"))
@patch("app.agent.planner.chat", return_value='{"plan": ["partial"')
def test_create_plan_ignores_incomplete_json(mock_chat, mock_extract_json):
    plan = create_plan(DummyState("Review this repository"))

    assert plan == []
    mock_chat.assert_called_once()
    mock_extract_json.assert_called_once_with(mock_chat.return_value)
