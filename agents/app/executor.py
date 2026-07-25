import json
import logging
import re

from .config import (
    CONTEXT_COMPACT_EVERY_STEPS,
    CONTEXT_COMPACT_KEEP_RECENT,
    MAX_AGENT_STEPS,
    MAX_TOOL_OUTPUT_CHARS,
)
from .llm import chat, chat_with_tools
from .parser import parse_tool_arguments
from .prompts import COMPACTION_PROMPT, EXECUTOR_PROMPT
from .tool_registry import registry
from .tool_schemas import schemas_for

logger = logging.getLogger(__name__)

MAX_STEPS = MAX_AGENT_STEPS

# Tools that mutate the workspace. Excluded entirely from the tool list
# whenever a request does not have allow_write set.
WRITE_TOOLS = {"write_file", "edit_file", "run_command"}


def _noop_event(event_type: str, payload: dict) -> None:
    return None


def normalize_tool_args(tool_name: str, args: dict) -> dict:
    """Tolerate common argument-name mistakes from the model."""

    aliases = {
        "read_file": {"path": "file_path"},
        "tree": {"path": "directory"},
        "search_text": {"query": "keyword"},
    }

    if tool_name in aliases:
        for old, new in aliases[tool_name].items():
            if old in args and new not in args:
                args[new] = args.pop(old)

    return args


_LEAKED_TOOL_CALL_TAIL = re.compile(r"(\[.*\]|\{.*\})\s*\Z", re.DOTALL)

# How many times a model may leak a tool call as text before the loop gives
# up with a clear diagnostic instead of quietly burning all MAX_STEPS.
MAX_LEAKED_TOOL_CALLS = 2


def _leaked_tool_call(text: str) -> bool:
    """Detect a model that printed the tool call it "would" make as JSON text
    in its message content instead of using the API's native tool_calls
    field. This happens when the configured backend/model does not actually
    support OpenAI-style function calling even though it echoes the shape of
    one -- most commonly an Ollama model/template served without real tool
    support behind LiteLLM. Returning this text as a final answer would be
    confusing and wrong, so it needs to be caught explicitly.
    """

    match = _LEAKED_TOOL_CALL_TAIL.search(text.strip())
    if not match:
        return False

    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError:
        return False

    items = data if isinstance(data, list) else [data]
    if not items:
        return False

    for item in items:
        if not isinstance(item, dict):
            return False
        if "function" in item or "tool_calls" in item or (
            "tool" in item and "args" in item
        ):
            continue
        return False

    return True


def _truncate(text: str) -> str:
    if len(text) > MAX_TOOL_OUTPUT_CHARS:
        return text[:MAX_TOOL_OUTPUT_CHARS] + "\n...[truncated]"
    return text


def _compact_history(messages: list, model: str) -> list:
    """Summarize older tool exchanges once the transcript grows large.

    Keeps the system prompt, the original task message, and the most recent
    CONTEXT_COMPACT_KEEP_RECENT messages verbatim; folds everything else into
    a single summary message. This lets the loop keep running for many steps
    without unbounded context growth. If summarization itself fails, the
    full history is kept rather than losing information silently.
    """

    head_len = 2  # system prompt + initial task message
    if len(messages) <= head_len + CONTEXT_COMPACT_KEEP_RECENT:
        return messages

    head = messages[:head_len]
    recent = messages[-CONTEXT_COMPACT_KEEP_RECENT:]
    middle = messages[head_len : len(messages) - CONTEXT_COMPACT_KEEP_RECENT]

    transcript = json.dumps(
        [
            {"role": m.get("role"), "content": str(m.get("content"))[:2000]}
            for m in middle
        ],
        indent=2,
        default=str,
    )

    try:
        summary = chat(
            [
                {"role": "system", "content": COMPACTION_PROMPT},
                {"role": "user", "content": transcript},
            ],
            model=model,
        )
    except Exception:
        logger.exception("Context compaction failed; keeping full history")
        return messages

    return [
        *head,
        {
            "role": "user",
            "content": f"Summary of earlier investigation steps:\n{summary}",
        },
        *recent,
    ]


def execute_plan(state, on_event=None) -> str:
    """Run the tool-calling loop until the model produces a final answer.

    `on_event(event_type, payload)` is called for each notable step so a
    caller (see agent.execute_run) can stream progress to a durable event
    log. It is a no-op by default so synchronous callers are unaffected.
    """

    on_event = on_event or _noop_event

    available_tools = registry.list_tools()
    if not state.allow_write:
        available_tools = [t for t in available_tools if t not in WRITE_TOOLS]

    tools = schemas_for(available_tools)

    task_context = f"""
Workspace:
{state.workspace}

Task:
{state.user_message}

Recent conversation:
{state.history[-10:]}

Relevant memory:
{state.memories[:5]}

Plan:
{state.plan}
"""

    messages = [
        {"role": "system", "content": EXECUTOR_PROMPT},
        {"role": "user", "content": task_context},
    ]

    leaked_tool_call_count = 0

    for step in range(MAX_STEPS):
        state.steps += 1
        on_event("step_started", {"step": state.steps})

        if step > 0 and step % CONTEXT_COMPACT_EVERY_STEPS == 0:
            before = len(messages)
            messages = _compact_history(messages, state.model)
            if len(messages) < before:
                on_event(
                    "context_compacted",
                    {"messages_before": before, "messages_after": len(messages)},
                )

        message = chat_with_tools(messages, tools=tools, model=state.model)

        tool_calls = getattr(message, "tool_calls", None)

        if tool_calls:
            messages.append(
                {
                    "role": "assistant",
                    "content": message.content or "",
                    "tool_calls": [
                        {
                            "id": call.id,
                            "type": "function",
                            "function": {
                                "name": call.function.name,
                                "arguments": call.function.arguments,
                            },
                        }
                        for call in tool_calls
                    ],
                }
            )

            for call in tool_calls:
                tool_name = call.function.name
                raw_args = parse_tool_arguments(call.function.arguments)
                args = normalize_tool_args(tool_name, raw_args)

                on_event("tool_call", {"tool": tool_name, "args": args})
                logger.info("Executing tool %s with args=%s", tool_name, args)

                if tool_name not in available_tools:
                    result = {
                        "error": f"Tool '{tool_name}' is unavailable for this request."
                    }
                else:
                    try:
                        result = registry.execute(tool_name, args)
                    except Exception as e:
                        logger.exception("Tool %s raised unexpectedly", tool_name)
                        result = {"error": str(e)}

                state.add_tool(tool_name, result)
                on_event("tool_result", {"tool": tool_name, "result": result})

                result_text = _truncate(json.dumps(result, default=str))

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_text,
                    }
                )

            continue

        answer = (message.content or "").strip()

        if not answer:
            messages.append(
                {
                    "role": "user",
                    "content": "You must either call a tool or provide a complete final answer.",
                }
            )
            continue

        if _leaked_tool_call(answer):
            leaked_tool_call_count += 1
            logger.warning(
                "Model produced a tool call as text content instead of using "
                "native function calling (model=%s, occurrence=%d): %s",
                state.model,
                leaked_tool_call_count,
                answer,
            )
            on_event(
                "leaked_tool_call",
                {"model": state.model, "content": answer},
            )

            if leaked_tool_call_count > MAX_LEAKED_TOOL_CALLS:
                diagnostic = (
                    f"The configured model ('{state.model}') is not using real "
                    "function calling -- it keeps printing tool calls as text "
                    "instead of invoking them. This is a model/gateway "
                    "configuration issue, not a request problem: check that "
                    "this model is served with native tool-calling support "
                    "enabled (e.g. via LiteLLM's `ollama_chat` provider and a "
                    "tool-capable model such as qwen2.5-coder or llama3.1), "
                    "then retry."
                )
                state.finished = True
                on_event("final_answer", {"answer": diagnostic})
                return diagnostic

            messages.append(
                {
                    "role": "user",
                    "content": (
                        "Do not print tool calls as JSON text. Invoke the tool "
                        "through the function-calling mechanism provided to "
                        "you, or, if you are not calling a tool, respond with "
                        "plain natural-language final_answer text only."
                    ),
                }
            )
            continue

        state.finished = True
        on_event("final_answer", {"answer": answer})
        return answer

    on_event("max_steps_reached", {"steps": state.steps})
    return "Maximum execution steps reached before completion."