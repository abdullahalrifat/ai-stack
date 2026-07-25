from unittest.mock import patch

from app.executor import execute_plan, normalize_tool_args


class DummyState:
    def __init__(self):
        self.user_message = "Analyze repository"
        self.workspace = "."
        self.history = []
        self.memories = []
        self.plan = []
        self.allow_write = False
        self.model = "test-model"

        self.steps = 0
        self.finished = False
        self.observations = []

    def add_tool(self, tool, result):
        self.observations.append(
            {
                "tool": tool,
                "result": result,
            }
        )


# ----------------------------------------------------
# normalize_tool_args
# ----------------------------------------------------


def test_normalize_tool_args_read_file():
    args = {"path": "README.md"}

    result = normalize_tool_args("read_file", args)

    assert result == {"file_path": "README.md"}


def test_normalize_tool_args_tree():
    args = {"path": "."}

    result = normalize_tool_args("tree", args)

    assert result == {"directory": "."}


def test_normalize_tool_args_no_change():
    args = {"directory": "."}

    result = normalize_tool_args("tree", args)

    assert result == {"directory": "."}


# ----------------------------------------------------
# execute_plan
# ----------------------------------------------------


@patch("app.executor.registry")
@patch("app.executor.extract_json")
@patch("app.executor.chat")
def test_execute_plan_returns_final_answer(
    mock_chat,
    mock_extract_json,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat.return_value = '{"final_answer":"Done"}'

    mock_extract_json.return_value = {"final_answer": "Done"}

    result = execute_plan(state)

    assert result == "Done"
    assert state.finished is True


@patch("app.executor.registry")
@patch("app.executor.parse_tool_arguments")
@patch("app.executor.extract_json")
@patch("app.executor.chat")
def test_execute_plan_executes_tool(
    mock_chat,
    mock_extract_json,
    mock_parse_args,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = ["list_files"]

    mock_chat.side_effect = [
        '{"tool":"list_files"}',
        '{"final_answer":"Finished"}',
    ]

    mock_extract_json.side_effect = [
        {
            "tool": "list_files",
            "args": {"directory": "."},
        },
        {
            "final_answer": "Finished",
        },
    ]

    mock_parse_args.return_value = {"directory": "."}

    mock_registry.execute.return_value = {"files": ["README.md"]}

    result = execute_plan(state)

    assert result == "Finished"

    mock_registry.execute.assert_called_once_with(
        "list_files",
        {"directory": "."},
    )


@patch("app.executor.registry")
@patch("app.executor.extract_json")
@patch("app.executor.chat")
def test_execute_plan_unavailable_tool(
    mock_chat,
    mock_extract_json,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat.return_value = "{}"

    mock_extract_json.side_effect = [
        {
            "tool": "write_file",
            "args": {},
        },
        {
            "final_answer": "Done",
        },
    ]

    result = execute_plan(state)

    assert result == "Done"


@patch("app.executor.registry")
@patch("app.executor.extract_json")
@patch("app.executor.chat")
def test_execute_plan_invalid_json_retries(
    mock_chat,
    mock_extract_json,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat.side_effect = [
        "not json",
        '{"final_answer":"Recovered"}',
    ]

    mock_extract_json.side_effect = [
        ValueError("Invalid JSON"),
        {
            "final_answer": "Recovered",
        },
    ]

    result = execute_plan(state)

    assert result == "Recovered"
    assert mock_chat.call_count == 2


@patch("app.executor.registry")
@patch("app.executor.chat")
@patch("app.executor.extract_json")
def test_execute_plan_max_steps(
    mock_extract_json,
    mock_chat,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat.return_value = "{}"

    mock_extract_json.return_value = {}

    result = execute_plan(state)

    assert "Maximum execution steps" in result
