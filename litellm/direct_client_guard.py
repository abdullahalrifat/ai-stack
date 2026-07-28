"""Compatibility policy for IDEs that run their own tool loop via LiteLLM."""

from __future__ import annotations

import json
import re

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
VERIFICATION_COMMAND = re.compile(
    r"(?:^|\s)(?:python(?:3)?\s+-m\s+pytest|pytest|npm\s+(?:run\s+)?test|"
    r"ruff\s+check|mypy|make\s+(?:test|check)|cargo\s+test|go\s+test)(?:\s|$)",
    re.IGNORECASE,
)
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


def _latest_user_task(messages: list) -> str:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "user":
            return _message_text(message)
    return ""


def _tool_turn_count(messages: list) -> int:
    return sum(
        1
        for message in messages
        if isinstance(message, dict)
        and message.get("role") == "assistant"
        and message.get("tool_calls")
    )


def _latest_tool_output(messages: list) -> str | None:
    for message in reversed(messages):
        if isinstance(message, dict) and message.get("role") == "tool":
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


def require_recovery_tool_call(data: dict) -> bool:
    """Require continued action until verification succeeds, with a hard bound."""

    if not _direct_ide_request(data):
        return False
    messages = data.get("messages")
    if not isinstance(messages, list):
        return False
    if not CODE_CHANGE_REQUEST.search(_latest_user_task(messages)):
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

    updated = [dict(message) if isinstance(message, dict) else message for message in messages]
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
    if require_recovery_tool_call(updated):
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


proxy_handler_instance = DirectClientGuard()
