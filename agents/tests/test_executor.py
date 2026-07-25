import json
from types import SimpleNamespace
from unittest.mock import patch

import pytest

from app.agent.executor import execute_plan, normalize_tool_args


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


def make_tool_call(call_id: str, name: str, arguments: dict):
    """Build a stand-in for the OpenAI SDK's tool_call object: an object
    with .id and .function.name / .function.arguments (a JSON string)."""
    return SimpleNamespace(
        id=call_id,
        function=SimpleNamespace(name=name, arguments=json.dumps(arguments)),
    )


def make_message(content=None, tool_calls=None):
    """Build a stand-in for the OpenAI SDK's response message object."""
    return SimpleNamespace(content=content, tool_calls=tool_calls)


@pytest.fixture(autouse=True)
def no_real_compaction():
    """Executor tests never run long enough to need real compaction, and
    letting it run for real would call the live chat() completion. Keep the
    history untouched instead."""
    with patch(
        "app.agent.executor._compact_history",
        side_effect=lambda messages, model: messages,
    ):
        yield


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


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_returns_final_answer(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)

    result = execute_plan(state)

    assert result == "Done"
    assert state.finished is True


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_executes_tool(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}

    mock_chat_with_tools.side_effect = [
        make_message(
            content=None,
            tool_calls=[make_tool_call("call_1", "list_files", {"directory": "."})],
        ),
        make_message(content="Finished", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Finished"

    mock_registry.execute.assert_called_once_with(
        "list_files",
        {"directory": "."},
    )

    assert state.observations[0]["tool"] == "list_files"
    assert state.observations[0]["result"] == {"files": ["README.md"]}


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_prefetches_current_external_information(mock_chat_with_tools, mock_registry):
    state = DummyState()
    state.user_message = "Search Renata last closing price on DSE today"
    mock_registry.list_tools.return_value = ["web_search"]
    mock_registry.execute.return_value = {
        "results": [{"title": "RENATA", "url": "https://example.test/renata"}]
    }
    mock_chat_with_tools.return_value = make_message(content="Result", tool_calls=None)

    result = execute_plan(state)

    assert result == "Result"
    mock_registry.execute.assert_called_once_with("web_search", {"query": state.user_message})
    assert "https://example.test/renata" in mock_chat_with_tools.call_args.args[0][1]["content"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_unavailable_tool(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    # write_file is not in the available tool list for this request.
    mock_registry.list_tools.return_value = []

    mock_chat_with_tools.side_effect = [
        make_message(
            content=None,
            tool_calls=[
                make_tool_call(
                    "call_1", "write_file", {"file_path": "x.py", "content": "y"}
                )
            ],
        ),
        make_message(content="Done", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Done"
    mock_registry.execute.assert_not_called()
    assert "unavailable" in state.observations[0]["result"]["error"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_empty_content_retries(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    # A response with neither tool_calls nor usable content must be treated
    # as invalid and retried, rather than accepted as an empty final answer.
    mock_chat_with_tools.side_effect = [
        make_message(content="", tool_calls=None),
        make_message(content="Recovered", tool_calls=None),
    ]

    result = execute_plan(state)

    assert result == "Recovered"
    assert mock_chat_with_tools.call_count == 2


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_max_steps(
    mock_chat_with_tools,
    mock_registry,
):
    state = DummyState()

    mock_registry.list_tools.return_value = []

    # The model never calls a tool and never produces usable content, so
    # every step is retried until MAX_STEPS is exhausted.
    mock_chat_with_tools.return_value = make_message(content="", tool_calls=None)

    result = execute_plan(state)

    assert "Maximum execution steps" in result


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools")
def test_execute_plan_emits_events(
    mock_chat_with_tools,
    mock_registry,
):
    """The streaming hook used by durable /runs execution should observe at
    least a step_started and a final_answer event."""
    state = DummyState()

    mock_registry.list_tools.return_value = []
    mock_chat_with_tools.return_value = make_message(content="Done", tool_calls=None)

    events = []
    execute_plan(
        state,
        on_event=lambda event_type, payload: events.append((event_type, payload)),
    )

    event_types = [event_type for event_type, _ in events]
    assert "step_started" in event_types
    assert "final_answer" in event_types


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools_stream")
def test_execute_plan_streams_text_deltas(mock_stream, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = []
    mock_stream.return_value = [
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="Hel", tool_calls=None))]
        ),
        SimpleNamespace(
            choices=[SimpleNamespace(delta=SimpleNamespace(content="lo", tool_calls=None))]
        ),
    ]
    tokens = []

    result = execute_plan(state, on_token=tokens.append)

    assert result == "Hello"
    assert tokens == ["Hel", "lo"]


@patch("app.agent.executor.registry")
@patch("app.agent.executor.chat_with_tools_stream")
def test_execute_plan_reassembles_streamed_tool_arguments(mock_stream, mock_registry):
    state = DummyState()
    mock_registry.list_tools.return_value = ["list_files"]
    mock_registry.execute.return_value = {"files": ["README.md"]}
    mock_stream.side_effect = [
        [
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id="call-1",
                                    function=SimpleNamespace(
                                        name="list_files", arguments='{"directory":'
                                    ),
                                )
                            ],
                        )
                    )
                ]
            ),
            SimpleNamespace(
                choices=[
                    SimpleNamespace(
                        delta=SimpleNamespace(
                            content=None,
                            tool_calls=[
                                SimpleNamespace(
                                    index=0,
                                    id=None,
                                    function=SimpleNamespace(name=None, arguments='"."}'),
                                )
                            ],
                        )
                    )
                ]
            ),
        ],
        [
            SimpleNamespace(
                choices=[SimpleNamespace(delta=SimpleNamespace(content="Done", tool_calls=None))]
            )
        ],
    ]

    result = execute_plan(state, on_token=lambda _: None)

    assert result == "Done"
    mock_registry.execute.assert_called_once_with("list_files", {"directory": "."})
