import json
import logging
import re
from types import SimpleNamespace

from ..core.config import (
    CONTEXT_COMPACT_EVERY_STEPS,
    CONTEXT_COMPACT_KEEP_RECENT,
    MAX_AGENT_STEPS,
    MAX_TOOL_OUTPUT_CHARS,
)
from ..core.exceptions import RunCancelled
from ..llm.client import chat, chat_with_tools, chat_with_tools_stream
from .parser import parse_tool_arguments
from .prompts import COMPACTION_PROMPT, EXECUTOR_PROMPT, WEB_RESEARCH_PROMPT
from ..tools.registry import registry
from ..tools.schemas import schemas_for

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

# Models with weak native tool calling commonly answer current-data questions
# from their training cutoff.  These requests must use the local search tool
# before the model is allowed to synthesize an answer.
_CURRENT_EXTERNAL_INFO = re.compile(
    r"\b(?:latest|current|today(?:'s)?|real[ -]?time|closing|close price|"
    r"stock|share price|market price|financial data|dse|nasdaq|nyse|"
    r"search (?:the )?(?:web|internet)|look up)\b",
    re.IGNORECASE,
)
_FINANCIAL_QUERY = re.compile(
    r"\b(?:stock|share price|market price|financial data|portfolio|dse|nasdaq|nyse|"
    r"closing|close price)\b",
    re.IGNORECASE,
)
_RESEARCH_REFUSAL = re.compile(
    r"(?:cannot|can't|do not)\s+(?:directly\s+)?(?:access|retrieve).*?(?:real[ -]?time|stock|market|data)|"
    r"(?:cannot|can't|do not)\s+(?:provide|find|verify).*?(?:closing|price|stock|market|data)|"
    r"(?:no|lack of)\s+(?:real[ -]?time|timestamped|verified).*?(?:data|price)|"
    r"(?:do not|does not)\s+(?:contain|include).*?(?:closing|price)|"
    r"do not have access to real[ -]?time",
    re.IGNORECASE | re.DOTALL,
)


def requires_external_search(message: str) -> bool:
    return bool(_CURRENT_EXTERNAL_INFO.search(message))


def is_financial_query(message: str) -> bool:
    return bool(_FINANCIAL_QUERY.search(message))


def financial_price_query(message: str) -> str:
    """Turn a conversational stock request into a compact price lookup.

    Passing the entire request (often including a five-year analysis question)
    to a search engine dilutes the result ranking.  Keep the named company and
    market, then explicitly ask for the two fields that establish a last close.
    """

    subject = message
    match = re.search(r"\b(?:search|find|look\s+up)\s+([\w.-]+)", message, re.IGNORECASE)
    if match:
        subject = match.group(1)

    market = "DSE" if re.search(r"\bdse\b", message, re.IGNORECASE) else "stock market"
    return f"{subject} {market} latest closing price previous close historical data"


def financial_research_queries(message: str) -> list[str]:
    """Return a minimal evidence set for an investment-style research request."""

    price_query = financial_price_query(message)
    subject = price_query.split(" ", 1)[0]
    market = "Bangladesh" if re.search(r"\bdse\b", message, re.IGNORECASE) else ""
    return [
        price_query,
        f"{subject} annual report revenue profit earnings financial statements pdf",
        f"{subject} latest company news expansion earnings {market}",
        f"{market} pharmaceutical healthcare sector outlook inflation healthcare spending latest",
    ]


def _prefetch_external_search(state, available_tools: list[str], on_event):
    """Fetch current evidence before a non-tool-capable model can decline.

    The workspace tool loop remains model-driven. This narrow preflight is
    only for explicit/time-sensitive external requests, where answering from
    a model's training data is known to be incorrect.
    """
    if "web_search" not in available_tools or not requires_external_search(state.user_message):
        return None

    queries = (
        financial_research_queries(state.user_message)
        if is_financial_query(state.user_message)
        else [state.user_message]
    )
    searches = []
    for query in queries:
        args = {"query": query}
        on_event("tool_call", {"tool": "web_search", "args": args, "prefetch": True})
        try:
            result = registry.execute("web_search", args)
        except Exception as exc:
            logger.exception("Prefetch web search failed")
            result = {"error": str(exc)}
        state.add_tool("web_search", result)
        on_event("tool_result", {"tool": "web_search", "result": result, "prefetch": True})
        searches.append(result)

    return searches[0] if len(searches) == 1 else {"research_queries": searches}


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


def _stream_message(messages: list, tools: list, model: str, on_token) -> SimpleNamespace:
    """Collect one streamed model turn while forwarding text deltas promptly.

    OpenAI-compatible APIs stream a function call in fragments.  The tool
    loop needs the completed call before it can execute it, whereas text can
    be delivered to the caller immediately.  This keeps tool use reliable
    without holding the final natural-language answer until the request ends.
    """

    content_parts: list[str] = []
    calls: dict[int, dict] = {}

    for chunk in chat_with_tools_stream(messages, tools=tools, model=model):
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue

        content = getattr(delta, "content", None)
        if content:
            content_parts.append(content)
            on_token(content)

        for part in getattr(delta, "tool_calls", None) or []:
            index = getattr(part, "index", None)
            if index is None:
                index = len(calls)
            call = calls.setdefault(index, {"id": None, "name": "", "arguments": ""})
            if getattr(part, "id", None):
                call["id"] = part.id
            function = getattr(part, "function", None)
            if function is not None:
                if getattr(function, "name", None):
                    call["name"] = function.name
                if getattr(function, "arguments", None):
                    call["arguments"] += function.arguments

    tool_calls = [
        SimpleNamespace(
            id=call["id"] or f"streamed-call-{index}",
            function=SimpleNamespace(name=call["name"], arguments=call["arguments"]),
        )
        for index, call in sorted(calls.items())
    ]
    return SimpleNamespace(content="".join(content_parts), tool_calls=tool_calls or None)


def execute_plan(state, on_event=None, on_token=None, should_cancel=None, force_research=False) -> str:
    """Run the tool-calling loop until the model produces a final answer.

    `on_event(event_type, payload)` is called for each notable step so a
    caller (see agent.execute_run) can stream progress to a durable event
    log. It is a no-op by default so synchronous callers are unaffected.
    """

    on_event = on_event or _noop_event
    should_cancel = should_cancel or (lambda: False)

    available_tools = registry.list_tools()
    if not state.allow_write:
        available_tools = [t for t in available_tools if t not in WRITE_TOOLS]

    research_mode = force_research or requires_external_search(state.user_message)
    if research_mode and "web_search" in available_tools:
        # Prevent a coding-oriented model from wandering through the mounted
        # repository when the user asked for current external information.
        available_tools = [tool for tool in ("web_search", "web_fetch") if tool in available_tools]

    tools = schemas_for(available_tools)
    external_search = _prefetch_external_search(state, available_tools, on_event)

    external_context = ""
    if external_search is not None:
        external_context = f"""

Current external search results (retrieved for this request; cite their URLs):
{_truncate(json.dumps(external_search, default=str))}
"""

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
{external_context}
"""

    messages = [
        {"role": "system", "content": WEB_RESEARCH_PROMPT if research_mode else EXECUTOR_PROMPT},
        {"role": "user", "content": task_context},
    ]

    leaked_tool_call_count = 0
    research_retry_count = 0

    for step in range(MAX_STEPS):
        if should_cancel():
            on_event("run_cancelling", {})
            raise RunCancelled()
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

        message = (
            _stream_message(messages, tools, state.model, on_token)
            if on_token is not None
            else chat_with_tools(messages, tools=tools, model=state.model)
        )

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
                if should_cancel():
                    on_event("run_cancelling", {})
                    raise RunCancelled()
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

        if research_mode and external_search and _RESEARCH_REFUSAL.search(answer):
            research_retry_count += 1
            if research_retry_count <= 1:
                on_event("research_answer_retry", {"reason": "model ignored retrieved evidence"})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Your draft contradicts the retrieved search results. Do not say you lack "
                            "real-time access and do not tell the user to search elsewhere. Use the result "
                            "snippets and URLs already provided. For a price request, extract the most recent "
                            "reported close or previous-close figure, name its source and date when supplied. "
                            "Then distinguish verified facts from an uncertain scenario analysis and answer now."
                        ),
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
