import asyncio
import importlib.util
from pathlib import Path


MODULE_PATH = (
    Path(__file__).parents[2] / "litellm" / "direct_client_guard.py"
)
SPEC = importlib.util.spec_from_file_location("direct_client_guard", MODULE_PATH)
guard = importlib.util.module_from_spec(SPEC)
assert SPEC and SPEC.loader
SPEC.loader.exec_module(guard)


def terminal_tool():
    return {
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "parameters": {"type": "object", "properties": {}},
        },
    }


def terminal_turn(command: str, output: str):
    return [
        {
            "role": "assistant",
            "content": None,
            "tool_calls": [
                {
                    "id": "call",
                    "type": "function",
                    "function": {
                        "name": "run_terminal_command",
                        "arguments": f'{{"command": {command!r}}}'.replace(
                            "'", '"'
                        ),
                    },
                }
            ],
        },
        {"role": "tool", "tool_call_id": "call", "content": output},
    ]


def test_policy_is_injected_into_existing_system_message_once():
    request = {
        "messages": [
            {"role": "system", "content": "IDE policy"},
            {"role": "user", "content": "Improve coverage"},
        ],
        "tools": [terminal_tool()],
    }

    first = guard.inject_direct_tool_policy(request)
    second = guard.inject_direct_tool_policy(first)

    assert first["messages"][0]["content"].startswith("IDE policy")
    assert "never combine" in first["messages"][0]["content"]
    assert "Never run pip/apt/npm installation" in first["messages"][0]["content"]
    assert first["messages"][0]["content"].count(guard.POLICY_MARKER) == 1
    assert second == first
    assert request["messages"][0]["content"] == "IDE policy"


def test_policy_is_not_injected_into_plain_chat():
    request = {
        "messages": [{"role": "user", "content": "Hello"}],
        "tools": [],
    }

    assert guard.inject_direct_tool_policy(request) == request


def test_callback_applies_only_to_chat_completions():
    request = {
        "messages": [{"role": "user", "content": "Run tests"}],
        "tools": [terminal_tool()],
    }

    changed = asyncio.run(
        guard.proxy_handler_instance.async_pre_call_hook(
            None, None, request, "acompletion"
        )
    )
    unchanged = asyncio.run(
        guard.proxy_handler_instance.async_pre_call_hook(
            None, None, request, "embedding"
        )
    )

    assert guard.POLICY_MARKER in changed["messages"][0]["content"]
    assert unchanged == request


def test_failed_command_for_code_change_requires_another_tool_call():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn(
                "python -m pytest --cov=agents",
                "pytest: error: unrecognized arguments: --cov=agents",
            ),
        ],
        "tools": [terminal_tool()],
        "tool_choice": "auto",
    }

    changed = guard.apply_direct_client_guard(request)

    assert changed["tool_choice"] == "required"


def test_successful_verification_allows_final_answer():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn("python -m pytest -q", "166 passed"),
        ],
        "tools": [terminal_tool()],
        "tool_choice": "auto",
    }

    changed = guard.apply_direct_client_guard(request)

    assert changed["tool_choice"] == "auto"


def test_central_agent_tools_are_not_modified():
    request = {
        "messages": [{"role": "user", "content": "Improve tests"}],
        "tools": [
            {
                "type": "function",
                "function": {"name": "run_tests", "parameters": {}},
            }
        ],
    }

    assert guard.apply_direct_client_guard(request) == request


def test_forced_recovery_is_bounded():
    messages = [{"role": "user", "content": "Improve coverage"}]
    for index in range(guard.MAX_FORCED_TOOL_TURNS):
        messages.extend(terminal_turn(f"missing-{index}", "command not found"))
    request = {"messages": messages, "tools": [terminal_tool()]}

    changed = guard.apply_direct_client_guard(request)

    assert "tool_choice" not in changed
