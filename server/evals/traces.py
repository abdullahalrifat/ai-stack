"""Golden trace schema for the offline agent replay harness.

A golden trace records everything needed to deterministically re-run the
executor against a recorded model (the ``model_turns`` script) while keeping
the tools real, and to check that the tool-call sequence the machinery
produces still matches the golden run (``tool_sequence``).

Traces are pure JSON so they can be committed to the repository, diffed, and
re-recorded after an intentional machinery change.
"""

from __future__ import annotations

import json
from pathlib import Path

TRACE_VERSION = 1

_REQUIRED_KEYS = (
    "id",
    "prompt",
    "profile",
    "model",
    "workspace",
    "allow_write",
    "plan",
    "tool_sequence",
    "model_turns",
    "answer",
)


def load(path) -> dict:
    return json.loads(Path(path).read_text(encoding="utf-8"))


def save(trace: dict, path) -> None:
    payload = dict(trace)
    payload["version"] = TRACE_VERSION
    Path(path).write_text(
        json.dumps(payload, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def validate(trace: dict) -> list[str]:
    """Return a list of schema violations (empty means the trace is valid)."""

    problems: list[str] = []
    for key in _REQUIRED_KEYS:
        if key not in trace:
            problems.append(f"missing required key: {key}")
            continue
        value = trace[key]
        if key in {"id", "prompt", "profile", "model", "workspace", "answer"}:
            if not isinstance(value, str):
                problems.append(f"{key} must be a string")
        elif key == "allow_write":
            if not isinstance(value, bool):
                problems.append("allow_write must be a boolean")
        elif key == "plan":
            if not isinstance(value, list) or not all(
                isinstance(item, str) for item in value
            ):
                problems.append("plan must be a list of strings")
        elif key == "tool_sequence":
            problems.extend(_validate_tool_sequence(value))
        elif key == "model_turns":
            problems.extend(_validate_model_turns(value))

    if not problems:
        sequence_names = {item["tool"] for item in trace["tool_sequence"]}
        script_names = {
            call["name"]
            for turn in trace["model_turns"]
            for call in turn.get("tool_calls", [])
        }
        # Prefetch tool calls are executed by the machinery (not the model) and
        # therefore appear in tool_sequence but not in the model script. The
        # reverse -- a scripted call with no executed record -- is impossible.
        unsupported = script_names - sequence_names
        if unsupported:
            problems.append(
                "model_turns script calls tools with no tool_sequence record: "
                f"{sorted(unsupported)}"
            )
    return problems


def _validate_tool_sequence(value) -> list[str]:
    problems: list[str] = []
    if not isinstance(value, list):
        return ["tool_sequence must be a list"]
    for index, item in enumerate(value):
        if not isinstance(item, dict):
            problems.append(f"tool_sequence[{index}] must be an object")
            continue
        if not isinstance(item.get("tool"), str):
            problems.append(f"tool_sequence[{index}].tool must be a string")
        if not isinstance(item.get("args"), dict):
            problems.append(f"tool_sequence[{index}].args must be an object")
    return problems


def _validate_model_turns(value) -> list[str]:
    problems: list[str] = []
    if not isinstance(value, list):
        return ["model_turns must be a list"]
    for index, turn in enumerate(value):
        if not isinstance(turn, dict):
            problems.append(f"model_turns[{index}] must be an object")
            continue
        calls = turn.get("tool_calls")
        if calls is not None:
            if not isinstance(calls, list) or not calls:
                problems.append(f"model_turns[{index}].tool_calls must be a non-empty list")
            else:
                for call in calls:
                    if not isinstance(call.get("name"), str):
                        problems.append(f"model_turns[{index}].tool_calls name must be a string")
                    if not isinstance(call.get("arguments"), dict):
                        problems.append(
                            f"model_turns[{index}].tool_calls arguments must be an object"
                        )
        elif not isinstance(turn.get("content"), str):
            problems.append(
                f"model_turns[{index}] must have tool_calls or a string content"
            )
    return problems


def tool_sequence_from_transcript(messages: list[dict]) -> list[dict]:
    """Reconstruct the ordered tool-call record from a checkpoint transcript.

    The executor appends one tool message (with a ``tool_call_id``) after each
    call in the model's assistant message, so the assistant ``tool_calls`` and
    the following tool messages can be paired by id to recover every executed
    call together with its result.
    """

    sequence: list[dict] = []
    index = 0
    while index < len(messages):
        message = messages[index]
        calls = message.get("tool_calls")
        if not isinstance(calls, list) or not calls:
            index += 1
            continue
        by_id = {
            call.get("id"): call for call in calls if isinstance(call, dict)
        }
        tool_messages = {}
        index += 1
        while index < len(messages) and messages[index].get("role") == "tool":
            tool_message = messages[index]
            if isinstance(tool_message.get("content"), str):
                tool_messages[tool_message.get("tool_call_id")] = tool_message[
                    "content"
                ]
            index += 1
        for call in calls:
            name = call.get("function", {}).get("name") if isinstance(call, dict) else None
            arguments = call.get("function", {}).get("arguments") if isinstance(call, dict) else None
            if name is None:
                continue
            args = _coerce_object(arguments)
            raw_result = tool_messages.get(call.get("id"))
            sequence.append(
                {
                    "tool": name,
                    "args": args or {},
                    "result": _coerce_object(raw_result),
                }
            )
    return sequence


def model_turns_from_transcript(messages: list[dict]) -> list[dict]:
    """Recover the model's actual responses from a checkpoint transcript.

    The executor only appends an assistant message itself when it rejects a
    draft and immediately issues a user repair prompt. So a model-produced
    response is exactly an assistant message that is *not* followed by a user
    message: either it triggered tool execution or it was accepted as final.
    """

    turns: list[dict] = []
    for index, message in enumerate(messages):
        if message.get("role") != "assistant":
            continue
        following = messages[index + 1] if index + 1 < len(messages) else None
        if following is not None and following.get("role") == "user":
            continue
        calls = message.get("tool_calls")
        if isinstance(calls, list) and calls:
            turns.append(
                {
                    "tool_calls": [
                        {
                            "id": call.get("id"),
                            "name": call.get("function", {}).get("name"),
                            "arguments": _coerce_object(
                                call.get("function", {}).get("arguments")
                            )
                            or {},
                        }
                        for call in calls
                        if isinstance(call, dict) and call.get("function")
                    ]
                }
            )
        else:
            turns.append({"content": message.get("content") or ""})
    return turns


def trace_from_transcript(
    messages: list[dict],
    *,
    run_id: str,
    prompt: str,
    profile: str,
    model: str,
    workspace: str,
    allow_write: bool,
    plan: list[str],
    answer: str,
    steps: int,
    partial: bool = False,
    successful_mutation: bool = False,
    successful_verification: bool = False,
    prefetch_calls: list[dict] | None = None,
) -> dict:
    """Build a golden trace from a run transcript.

    ``prefetch_calls`` carries the workspace/external prefetch tool calls that
    the executor runs before the model loop; they are recorded in the run
    events but not in the checkpoint transcript, so callers supply them from
    the event stream.
    """

    return {
        "id": run_id,
        "prompt": prompt,
        "profile": profile,
        "model": model,
        "workspace": workspace,
        "allow_write": allow_write,
        "plan": list(plan or []),
        "steps": int(steps),
        "partial": bool(partial),
        "successful_mutation": bool(successful_mutation),
        "successful_verification": bool(successful_verification),
        "tool_sequence": list(prefetch_calls or [])
        + tool_sequence_from_transcript(messages),
        "model_turns": model_turns_from_transcript(messages),
        "answer": answer,
    }


def _coerce_object(value):
    """Best-effort JSON parse of a serialized tool argument/result blob."""

    if isinstance(value, (dict, list)):
        return value
    if not isinstance(value, str) or not value:
        return value
    try:
        return json.loads(value)
    except (ValueError, TypeError):
        return value
