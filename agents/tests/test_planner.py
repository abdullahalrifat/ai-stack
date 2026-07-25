from unittest.mock import patch

from app.planner import create_plan


class DummyState:
    def __init__(self, user_message):
        self.user_message = user_message


@patch("app.planner.extract_json")
@patch("app.planner.chat")
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
    mock_extract_json.assert_called_once_with(mock_chat.return_value)


@patch("app.planner.extract_json")
@patch("app.planner.chat")
def test_create_plan_returns_empty_when_no_plan(
    mock_chat,
    mock_extract_json,
):
    state = DummyState("Hello")

    mock_chat.return_value = "{}"
    mock_extract_json.return_value = {}

    plan = create_plan(state)

    assert plan == []


@patch("app.planner.extract_json")
@patch("app.planner.chat")
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