"""Compatibility policy for IDEs that run their own tool loop via LiteLLM."""

from __future__ import annotations

import json
import logging
import re
import shlex
import uuid

try:
    from litellm.integrations.custom_logger import CustomLogger
except ImportError:  # Allows policy unit tests without installing LiteLLM.

    class CustomLogger:  # type: ignore[no-redef]
        pass


POLICY_MARKER = "DIRECT_TOOL_LOOP_CONTINUATION_POLICY"
MAX_FORCED_TOOL_TURNS = 8
DIRECT_IDE_TOOL_NAMES = {
    "create_new_file",
    "edit_existing_file",
    "file_glob_search",
    "grep_search",
    "read_currently_open_file",
    "run_terminal_command",
    "single_find_and_replace",
    "view_diff",
}
CODE_CHANGE_REQUEST = re.compile(
    r"\b(?:add|build|change|create|edit|fix|implement|improve|modify|"
    r"refactor|remove|rename|replace|update|write)\b",
    re.IGNORECASE,
)
TOOL_FAILURE = re.compile(
    r"(?:command failed|exit(?:ed)? (?:code|status)\s*[1-9]|"
    r"not found|no such file|unrecognized arguments?|traceback|"
    r"permission denied|error:|failed\b)",
    re.IGNORECASE,
)
COVERAGE_TOOL_FAILURE = re.compile(
    r"(?:coverage: command not found|no module named (?:pytest_cov|coverage)|"
    r"unrecognized arguments?:[^\n]*(?:--cov|--cov-report)|"
    r"unknown option[^\n]*(?:--cov|--cov-report))",
    re.IGNORECASE,
)
VERIFICATION_COMMAND = re.compile(
    r"(?:^|\s)(?:python(?:3)?\s+-m\s+pytest|pytest|npm\s+(?:run\s+)?test|"
    r"ruff\s+check|mypy|make\s+(?:test|check)|cargo\s+test|go\s+test)(?:\s|$)",
    re.IGNORECASE,
)
logger = logging.getLogger("direct_client_guard")
DIRECT_TOOL_LOOP_POLICY = f"""
{POLICY_MARKER}
You are participating in a client-managed tool-execution loop. A prose message
ends that loop, so never finish with a future-tense progress announcement such
as "I will", "I'll", "next", or "let's first" followed by an action you have
not performed. Call the appropriate available tool instead. Return prose only
when the requested outcome is complete or when you have exhausted safe,
concrete recovery paths and can state the verified blocker.

After a command failure, use its exit code and output to recover autonomously.
Each terminal command runs in a fresh shell; activation, exported variables,
and shell state do not persist. Never run pip/apt/npm installation merely
because a command or plugin is missing, unless the user explicitly requested
dependency installation. First inspect repository test configuration,
dependency manifests, existing virtual environments, container configuration,
and executable paths. Prefer an existing project/container runner. If coverage
support is unavailable, run the plain test suite first and report that coverage
measurement is unavailable rather than modifying the host environment. For
pytest coverage, verify pytest-cov is available and use
`python -m pytest --cov=<package> --cov-report=term-missing`; never invoke the
`coverage` executable and never combine `coverage run` with pytest `--cov`
options.
""".strip()


def _tool_names(data: dict) -> set[str]:
    names = set()
    for tool in data.get("tools") or []:
        if not isinstance(tool, dict):
            continue
        function = tool.get("function") or {}
        if isinstance(function, dict) and function.get("name"):
            names.add(str(function["name"]))
    return names


def _direct_ide_request(data: dict) -> bool:
    return bool(_tool_names(data) & DIRECT_IDE_TOOL_NAMES)


def _message_text(message: dict) -> str:
    content = message.get("content", "")
    if isinstance(content, str):
        return content
    return json.dumps(content, default=str)


def _code_change_requested(messages: list) -> bool:
    """Keep the original task visible when clients encode tool output as user text."""

    return any(
        CODE_CHANGE_REQUEST.search(_message_text(message))
        for message in messages
        if isinstance(message, dict) and message.get("role") == "user"
    )


def _tool_turn_count(messages: list) -> int:
    return sum(
        1
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("tool_calls")
    )


def _latest_tool_output(messages: list) -> str | None:
    for index in range(len(messages) - 1, -1, -1):
        message = messages[index]
        if not isinstance(message, dict):
            continue
        if message.get("role") == "tool":
            return _message_text(message)
        # Some IDE tool loops use a user message for the result of the
        # immediately preceding assistant tool call.
        if message.get("role") == "user" and index > 0:
            previous = messages[index - 1]
            if (
                isinstance(previous, dict)
                and previous.get("role") == "assistant"
                and previous.get("tool_calls")
            ):
                return _message_text(message)
    return None


def _latest_terminal_command(messages: list) -> str:
    for message in reversed(messages):
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in reversed(message.get("tool_calls") or []):
            function = call.get("function") if isinstance(call, dict) else None
            if not isinstance(function, dict):
                continue
            if function.get("name") != "run_terminal_command":
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                return ""
            return str(arguments.get("command", ""))
    return ""


def _terminal_commands(messages: list) -> list[str]:
    commands = []
    for message in messages:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if (
                not isinstance(function, dict)
                or function.get("name") != "run_terminal_command"
            ):
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
            except (TypeError, json.JSONDecodeError):
                continue
            command = str(arguments.get("command", "")).strip()
            if command:
                commands.append(command)
    return commands


def _plain_pytest_command(messages: list) -> str:
    """Recover from unavailable coverage tooling without installing packages."""

    for command in reversed(_terminal_commands(messages)):
        try:
            tokens = shlex.split(command)
        except ValueError:
            continue
        try:
            pytest_index = tokens.index("pytest")
        except ValueError:
            continue
        remaining = tokens[pytest_index + 1 :]
        safe_args = []
        skip_value = False
        for token in remaining:
            if token in {"&&", "||", ";", "|"}:
                break
            if skip_value:
                skip_value = False
                continue
            if token in {"--cov", "--cov-report", "--cov-config"}:
                skip_value = True
                continue
            if token.startswith("--cov"):
                continue
            safe_args.append(token)
        return shlex.join(["python", "-m", "pytest", *safe_args])
    return "python -m pytest"


def _plain_pytest_attempted_after(messages: list, start_index: int) -> bool:
    for message in messages[start_index + 1 :]:
        if not isinstance(message, dict) or message.get("role") != "assistant":
            continue
        for call in message.get("tool_calls") or []:
            function = call.get("function") if isinstance(call, dict) else None
            if (
                not isinstance(function, dict)
                or function.get("name") != "run_terminal_command"
            ):
                continue
            try:
                arguments = json.loads(function.get("arguments") or "{}")
                command = str(arguments.get("command", ""))
                tokens = shlex.split(command)
            except (TypeError, ValueError, json.JSONDecodeError):
                continue
            if "pytest" in tokens and not any(
                token == "coverage" or token.startswith("--cov") for token in tokens
            ):
                return True
    return False


def _coverage_recovery_command(data: dict) -> str | None:
    messages = data.get("messages")
    if not isinstance(messages, list) or not require_recovery_tool_call(data):
        return None
    failure_index = next(
        (
            index
            for index in range(len(messages) - 1, -1, -1)
            if isinstance(messages[index], dict)
            and COVERAGE_TOOL_FAILURE.search(_message_text(messages[index]))
        ),
        None,
    )
    if failure_index is None or _plain_pytest_attempted_after(messages, failure_index):
        return None
    return _plain_pytest_command(messages)


def _tool_call(command: str) -> dict:
    return {
        "id": f"call_recovery_{uuid.uuid4().hex}",
        "type": "function",
        "function": {
            "name": "run_terminal_command",
            "arguments": json.dumps({"command": command}),
        },
    }


def _response_has_tool_call(response) -> bool:
    choices = (
        response.get("choices", [])
        if isinstance(response, dict)
        else getattr(response, "choices", [])
    )
    if not choices:
        return False
    choice = choices[0]
    message = (
        choice.get("message", {})
        if isinstance(choice, dict)
        else getattr(choice, "message", None)
    )
    if isinstance(message, dict):
        return bool(message.get("tool_calls"))
    return bool(message and getattr(message, "tool_calls", None))


def enforce_coverage_recovery(data: dict, response):
    """Replace premature prose with a deterministic safe recovery tool call."""

    command = _coverage_recovery_command(data)
    if command is None or _response_has_tool_call(response):
        return response
    choices = (
        response.get("choices", [])
        if isinstance(response, dict)
        else getattr(response, "choices", [])
    )
    if not choices:
        return response
    choice = choices[0]
    if isinstance(choice, dict):
        message = choice.setdefault("message", {})
        message["content"] = None
        message["tool_calls"] = [_tool_call(command)]
        choice["finish_reason"] = "tool_calls"
    else:
        choice.message.content = None
        choice.message.tool_calls = [_tool_call(command)]
        choice.finish_reason = "tool_calls"
    logger.warning("replaced premature coverage prose with plain pytest tool call")
    return response


def require_recovery_tool_call(data: dict) -> bool:
    """Require continued action until verification succeeds, with a hard bound."""

    if not _direct_ide_request(data):
        return False
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    if not _code_change_requested(messages):
        return False
    turns = _tool_turn_count(messages)
    if turns == 0 or turns >= MAX_FORCED_TOOL_TURNS:
        return False
    output = _latest_tool_output(messages)
    if output is None:
        return False
    command = _latest_terminal_command(messages)
    verification_succeeded = bool(
        VERIFICATION_COMMAND.search(command) and not TOOL_FAILURE.search(output)
    )
    return not verification_succeeded


def inject_direct_tool_policy(data: dict) -> dict:
    """Append the policy to tool-capable chat requests exactly once."""

    messages = data.get("messages")
    if not isinstance(messages, list) or not _direct_ide_request(data):
        return data
    if any(
        POLICY_MARKER in str(message.get("content", ""))
        for message in messages
        if isinstance(message, dict)
    ):
        return data

    updated = [
        dict(message) if isinstance(message, dict) else message for message in messages
    ]
    system_index = next(
        (
            index
            for index, message in enumerate(updated)
            if isinstance(message, dict) and message.get("role") == "system"
        ),
        None,
    )
    if system_index is None:
        updated.insert(0, {"role": "system", "content": DIRECT_TOOL_LOOP_POLICY})
    else:
        current = str(updated[system_index].get("content", "")).strip()
        updated[system_index]["content"] = (
            f"{current}\n\n{DIRECT_TOOL_LOOP_POLICY}"
            if current
            else DIRECT_TOOL_LOOP_POLICY
        )
    return {**data, "messages": updated}


def apply_direct_client_guard(data: dict) -> dict:
    updated = inject_direct_tool_policy(data)
    required = require_recovery_tool_call(updated)
    if _direct_ide_request(updated):
        messages = updated.get("messages") or []
        output = _latest_tool_output(messages) if isinstance(messages, list) else None
        logger.info(
            "decision required=%s change_request=%s tool_turns=%s "
            "tool_output=%s tool_failure=%s roles=%s",
            required,
            _code_change_requested(messages) if isinstance(messages, list) else False,
            _tool_turn_count(messages) if isinstance(messages, list) else 0,
            output is not None,
            bool(output and TOOL_FAILURE.search(output)),
            [message.get("role") for message in messages if isinstance(message, dict)][
                -8:
            ],
        )
    if required:
        updated = {**updated, "tool_choice": "required"}
    return updated


class DirectClientGuard(CustomLogger):
    async def async_pre_call_hook(
        self,
        user_api_key_dict,
        cache,
        data: dict,
        call_type,
    ):
        if call_type not in {"completion", "acompletion"}:
            return data
        return apply_direct_client_guard(data)

    async def async_post_call_success_hook(
        self,
        data: dict,
        user_api_key_dict,
        response,
    ):
        return enforce_coverage_recovery(data, response)

    async def async_post_call_streaming_iterator_hook(
        self,
        user_api_key_dict,
        response,
        request_data: dict,
    ):
        command = _coverage_recovery_command(request_data)
        if command is None:
            async for item in response:
                yield item
            return

        buffered = []
        has_tool_call = False
        async for item in response:
            buffered.append(item)
            choices = getattr(item, "choices", None) or []
            for choice in choices:
                delta = getattr(choice, "delta", None)
                if delta and getattr(delta, "tool_calls", None):
                    has_tool_call = True
        if has_tool_call:
            for item in buffered:
                yield item
            return

        from litellm.types.utils import ModelResponseStream

        first = buffered[0] if buffered else None
        yield ModelResponseStream(
            id=getattr(first, "id", None),
            model=getattr(first, "model", None),
            choices=[
                {
                    "index": 0,
                    "delta": {
                        "role": "assistant",
                        "content": None,
                        "tool_calls": [{**_tool_call(command), "index": 0}],
                    },
                    "finish_reason": "tool_calls",
                }
            ],
        )
        logger.warning("replaced streamed coverage prose with plain pytest tool call")


proxy_handler_instance = DirectClientGuard()
