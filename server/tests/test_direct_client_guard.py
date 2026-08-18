import asyncio
import importlib.util
from pathlib import Path

MODULE_PATH = Path(__file__).parents[2] / "litellm" / "direct_client_guard.py"
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
                        "arguments": f'{{"command": {command!r}}}'.replace("'", '"'),
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


def test_user_role_tool_result_preserves_original_code_change_request():
    tool_messages = terminal_turn(
        "python -m pytest --cov=agents",
        "pytest: error: unrecognized arguments: --cov=agents",
    )
    tool_messages[-1]["role"] = "user"
    tool_messages[-1].pop("tool_call_id")
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *tool_messages,
        ],
        "tools": [terminal_tool()],
        "tool_choice": "auto",
    }

    changed = guard.apply_direct_client_guard(request)

    assert changed["tool_choice"] == "required"


def test_later_real_user_message_does_not_hide_original_change_request():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn("which coverage", "Command failed with exit code 1"),
            {"role": "user", "content": "Please continue"},
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


def test_coverage_failure_prose_is_replaced_with_plain_pytest_tool_call():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn(
                "coverage run -m pytest server/tests --cov=agents "
                "--cov-report=term-missing",
                "/bin/bash: coverage: command not found",
            ),
        ],
        "tools": [terminal_tool()],
    }
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {"role": "assistant", "content": "Let's install it."},
            }
        ]
    }

    changed = guard.enforce_coverage_recovery(request, response)
    tool_call = changed["choices"][0]["message"]["tool_calls"][0]

    assert changed["choices"][0]["finish_reason"] == "tool_calls"
    assert changed["choices"][0]["message"]["content"] is None
    assert tool_call["function"]["name"] == "run_terminal_command"
    assert (
        tool_call["function"]["arguments"]
        == '{"command": "python -m pytest server/tests"}'
    )


def test_coverage_recovery_is_not_repeated_after_plain_pytest_attempt():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn(
                "python -m pytest --cov=agents",
                "pytest: error: unrecognized arguments: --cov=agents",
            ),
            *terminal_turn("python -m pytest", "1 failed, 173 passed"),
        ],
        "tools": [terminal_tool()],
    }
    response = {
        "choices": [
            {
                "finish_reason": "stop",
                "message": {
                    "role": "assistant",
                    "content": "I found a failing test.",
                },
            }
        ]
    }

    assert guard.enforce_coverage_recovery(request, response) == response
    assert response["choices"][0]["finish_reason"] == "stop"


def test_plain_pytest_recovery_preserves_safe_args_and_drops_shell_suffix():
    messages = [
        *terminal_turn(
            "coverage run -m pytest server/tests -q --cov agents "
            "--cov-branch && pip install coverage",
            "coverage: command not found",
        )
    ]

    assert guard._plain_pytest_command(messages) == "python -m pytest server/tests -q"


def test_existing_coverage_recovery_tool_call_is_preserved():
    request = {
        "messages": [
            {"role": "user", "content": "Improve test coverage"},
            *terminal_turn(
                "python -m pytest --cov=agents",
                "pytest: error: unrecognized arguments: --cov=agents",
            ),
        ],
        "tools": [terminal_tool()],
    }
    response = {
        "choices": [
            {
                "finish_reason": "tool_calls",
                "message": {
                    "role": "assistant",
                    "content": None,
                    "tool_calls": [{"function": {"name": "read_file"}}],
                },
            }
        ]
    }

    assert guard.enforce_coverage_recovery(request, response) == response


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
