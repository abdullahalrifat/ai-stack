import json
import logging
import re
from types import SimpleNamespace

from ..core.config import (
    CONTEXT_COMPACT_KEEP_RECENT,
    CONTEXT_COMPACT_THRESHOLD_TOKENS,
    MAX_AGENT_STEPS,
    MAX_EMPTY_MODEL_TURNS,
    MAX_EMPTY_SEARCH_RESULTS,
    MAX_UNPRODUCTIVE_TOOL_CALLS,
    MAX_TOOL_OUTPUT_CHARS,
)
from ..core.exceptions import RunCancelled
from ..llm.client import chat, chat_with_tools, chat_with_tools_stream
from .parser import parse_tool_arguments
from .context_budget import estimate_tokens, fit_user_context
from .prompts import COMPACTION_PROMPT, PARTIAL_SYNTHESIS_PROMPT, executor_prompt
from ..tools.registry import registry
from ..tools.schemas import schemas_for

logger = logging.getLogger(__name__)

MAX_STEPS = MAX_AGENT_STEPS

# Tools that mutate the workspace. Excluded entirely from the tool list
# whenever a request does not have allow_write set.
WRITE_TOOLS = {"write_file", "edit_file", "run_command", "run_tests"}

# Quick requests must fit an 8K local context even after the agent's own
# prompt and native tool schemas are attached. These cover focused repository
# review without advertising write/test/web tools that are not needed there.
QUICK_WORKSPACE_TOOLS = {
    "tree",
    "list_files",
    "read_file",
    "find_file",
    "search_text",
    "project_summary",
    "inspect_files",
}


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
        f"{subject} PLC annual report 2024 2025 revenue profit financial statements pdf",
        f"{subject} latest company news expansion earnings {market}",
        f"{market} pharmaceutical healthcare sector outlook inflation healthcare spending latest",
    ]


def financial_document_urls(searches: list[dict]) -> list[str]:
    """Choose one filing and one company-development source to read fully."""

    chosen: list[str] = []
    filing_query = searches[1].get("query", "") if len(searches) > 1 else ""
    company = filing_query.split()[0].lower() if filing_query else ""
    # Search positions: 1 = financial reports, 2 = company developments.
    for index in (1, 2):
        results = searches[index].get("results", []) if index < len(searches) else []
        if not isinstance(results, list):
            continue
        candidates = [
            item
            for item in results
            if company and company in f"{item.get('title', '')} {item.get('url', '')}".lower()
        ]
        if not company:
            candidates = [item for item in results if isinstance(item, dict)]
        # Never substitute an unrelated PDF when the company-specific source
        # is absent. Prefer a company-matching PDF, then its report archive.
        ordered = sorted(
            candidates,
            key=lambda item: 0 if ".pdf" in str(item.get("url", "")).lower() else 1,
        )
        for item in ordered:
            url = item.get("url")
            if isinstance(url, str) and url.startswith(("https://", "http://")):
                chosen.append(url)
                break
    return chosen


def report_pdf_link(result: dict) -> str | None:
    """Find a report PDF linked by a fetched official archive page."""

    for link in result.get("links", []) if isinstance(result, dict) else []:
        lowered = str(link).lower()
        if lowered.endswith(".pdf") and ("annual" in lowered or "report" in lowered):
            return link
    return None


def financial_document_excerpt(text: str, limit: int = 3_000) -> str:
    """Keep both report context and end-of-report audited statements."""

    if len(text) <= limit:
        return text
    head = limit // 2
    return f"{text[:head]}\n...[middle omitted]...\n{text[-(limit - head):]}"


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

    if len(searches) == 1:
        return searches[0]

    documents = []
    if "web_fetch" in available_tools:
        for url in financial_document_urls(searches):
            args = {"url": url}
            on_event("tool_call", {"tool": "web_fetch", "args": args, "prefetch": True})
            try:
                result = registry.execute("web_fetch", args)
            except Exception as exc:
                logger.exception("Prefetch web fetch failed")
                result = {"error": str(exc)}
            if isinstance(result, dict) and isinstance(result.get("text"), str):
                # Preserve room for both documents in the model context.
                result = {**result, "text": financial_document_excerpt(result["text"])}
            state.add_tool("web_fetch", result)
            on_event("tool_result", {"tool": "web_fetch", "result": result, "prefetch": True})
            documents.append(result)
            linked_pdf = report_pdf_link(result) if isinstance(result, dict) else None
            if linked_pdf:
                # The PDF is the substantive source; keep it in the bounded
                # model context instead of spending that space on the archive.
                documents.pop()
                pdf_args = {"url": linked_pdf}
                on_event("tool_call", {"tool": "web_fetch", "args": pdf_args, "prefetch": True})
                try:
                    pdf_result = registry.execute("web_fetch", pdf_args)
                except Exception as exc:
                    logger.exception("Prefetch report PDF fetch failed")
                    pdf_result = {"error": str(exc)}
                if isinstance(pdf_result, dict) and isinstance(pdf_result.get("text"), str):
                    pdf_result = {**pdf_result, "text": financial_document_excerpt(pdf_result["text"])}
                state.add_tool("web_fetch", pdf_result)
                on_event("tool_result", {"tool": "web_fetch", "result": pdf_result, "prefetch": True})
                documents.append(pdf_result)

    # Keep the price evidence and full-document extracts first: context is
    # bounded later, so placement determines what the local model can use.
    return {
        "price_search": searches[0],
        "documents": documents,
        "other_research": searches[1:],
    }


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


def _bounded_context(value, limit: int) -> str:
    """Serialize stored history/memory without letting it exhaust model context."""

    text = json.dumps(value, default=str)
    if len(text) <= limit:
        return text
    return f"{text[:limit]}\n...[older stored context omitted]"


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


def _needs_compaction(messages: list, system_prompt: str, tools: list[dict]) -> bool:
    return estimate_tokens(messages) + estimate_tokens(system_prompt) + estimate_tokens(tools) >= CONTEXT_COMPACT_THRESHOLD_TOKENS


def _synthesize_partial_answer(state) -> str:
    """Return a useful answer from evidence when the tool loop loses progress."""

    def useful(observation: dict) -> bool:
        result = observation.get("result")
        if isinstance(result, dict) and result.get("error"):
            return False
        return bool(result)

    # Preserve both early discovery (README, manifests, root listing) and
    # recent useful evidence. A tail-only slice previously let repeated empty
    # searches erase all context for the partial final answer.
    observations = [item for item in state.observations if useful(item)]
    selected = observations[:4]
    for item in observations[-4:]:
        if item not in selected:
            selected.append(item)
    evidence = _bounded_context(selected, 8_000)
    prompt = f"""Task:
{state.user_message}

Collected tool evidence:
{evidence}
"""
    try:
        answer = chat(
            [
                {"role": "system", "content": PARTIAL_SYNTHESIS_PROMPT},
                {"role": "user", "content": prompt},
            ],
            model=state.model,
            max_tokens=getattr(state, "max_completion_tokens", None),
        ).strip()
        if answer:
            return answer
    except Exception:
        logger.exception("Partial-answer synthesis failed")

    if state.observations:
        tools = ", ".join(item["tool"] for item in state.observations)
        return (
            "I could not complete another tool-call turn, but I did inspect: "
            f"{tools}. Please retry the request for a fuller analysis."
        )
    return "I could not complete the investigation. Please retry the request."


def _stream_message(messages: list, tools: list, model: str, on_token, max_tokens: int | None = None, timeout_seconds: int | None = None) -> SimpleNamespace:
    """Collect one streamed model turn while forwarding text deltas promptly.

    OpenAI-compatible APIs stream a function call in fragments.  The tool
    loop needs the completed call before it can execute it, whereas text can
    be delivered to the caller immediately.  This keeps tool use reliable
    without holding the final natural-language answer until the request ends.
    """

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict] = {}

    for chunk in chat_with_tools_stream(messages, tools=tools, model=model, max_tokens=max_tokens, timeout_seconds=timeout_seconds):
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

        # Qwen-family OpenAI-compatible streams can emit thought tokens on a
        # separate field. They are not an answer or a tool call, but recording
        # them prevents us from treating the stream shape as mysterious when
        # debugging a model/provider mismatch.
        reasoning = getattr(delta, "reasoning_content", None) or getattr(delta, "reasoning", None)
        if reasoning:
            reasoning_parts.append(reasoning)

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
    return SimpleNamespace(
        content="".join(content_parts),
        tool_calls=tool_calls or None,
        reasoning_content="".join(reasoning_parts),
    )


def execute_plan(state, on_event=None, on_token=None, should_cancel=None, force_research=False, on_checkpoint=None) -> str:
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
    if getattr(state, "prompt_mode", "code") == "quick" and not research_mode:
        available_tools = [tool for tool in available_tools if tool in QUICK_WORKSPACE_TOOLS]

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
{_bounded_context(state.history[-4:], 1_200)}

Relevant memory:
{_bounded_context(state.memories[:3], 1_000)}

Plan:
{_bounded_context(state.plan, 800)}
{external_context}
"""

    system_prompt = executor_prompt(getattr(state, "prompt_mode", "code"), research_mode)
    task_context, budget = fit_user_context(system_prompt, tools, task_context)
    if budget["trimmed"]:
        on_event("context_budgeted", budget)
    messages = [{"role": "system", "content": system_prompt}, {"role": "user", "content": task_context}]

    leaked_tool_call_count = 0
    research_retry_count = 0
    empty_turn_count = 0
    empty_search_count = 0
    unproductive_calls: dict[str, int] = {}

    for step in range(MAX_STEPS):
        if should_cancel():
            on_event("run_cancelling", {})
            raise RunCancelled()
        state.steps += 1
        on_event("step_started", {"step": state.steps})

        if _needs_compaction(messages, system_prompt, tools):
            before = len(messages)
            messages = _compact_history(messages, state.model)
            if len(messages) < before:
                on_event(
                    "context_compacted",
                    {"messages_before": before, "messages_after": len(messages)},
                )

        message = (
            _stream_message(
                messages,
                tools,
                state.model,
                on_token,
                getattr(state, "max_completion_tokens", None),
                getattr(state, "timeout_seconds", None),
            )
            if on_token is not None
            else chat_with_tools(
                messages,
                tools=tools,
                model=state.model,
                max_tokens=getattr(state, "max_completion_tokens", None),
                timeout_seconds=getattr(state, "timeout_seconds", None),
            )
        )

        tool_calls = getattr(message, "tool_calls", None)

        if tool_calls:
            empty_turn_count = 0
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
                if on_checkpoint is not None:
                    on_checkpoint(
                        {
                            "steps": state.steps,
                            "plan": state.plan,
                            "observations": state.observations[-12:],
                        }
                    )

                if tool_name == "search_text" and not result:
                    empty_search_count += 1
                elif result and not (isinstance(result, dict) and result.get("error")):
                    empty_search_count = 0

                unproductive = not result or (isinstance(result, dict) and result.get("error"))
                fingerprint = f"{tool_name}:{json.dumps(args, sort_keys=True, default=str)}"
                if unproductive:
                    unproductive_calls[fingerprint] = unproductive_calls.get(fingerprint, 0) + 1
                else:
                    unproductive_calls.clear()

                result_text = _truncate(json.dumps(result, default=str))

                messages.append(
                    {
                        "role": "tool",
                        "tool_call_id": call.id,
                        "content": result_text,
                    }
                )

            if empty_search_count >= MAX_EMPTY_SEARCH_RESULTS:
                answer = _synthesize_partial_answer(state)
                state.finished = True
                on_event(
                    "unproductive_search_loop",
                    {"empty_searches": empty_search_count},
                )
                on_event("final_answer", {"answer": answer, "partial": True})
                return answer

            repeated = max(unproductive_calls.values(), default=0)
            if repeated >= MAX_UNPRODUCTIVE_TOOL_CALLS:
                answer = _synthesize_partial_answer(state)
                state.finished = True
                on_event("unproductive_tool_loop", {"repeated_calls": repeated})
                on_event("final_answer", {"answer": answer, "partial": True})
                return answer

            continue

        answer = (message.content or "").strip()

        if not answer:
            empty_turn_count += 1
            on_event(
                "empty_model_turn",
                {
                    "count": empty_turn_count,
                    "reasoning_chars": len(getattr(message, "reasoning_content", "") or ""),
                },
            )
            if empty_turn_count >= MAX_EMPTY_MODEL_TURNS:
                answer = _synthesize_partial_answer(state)
                state.finished = True
                on_event("final_answer", {"answer": answer, "partial": True})
                return answer
            messages.append(
                {
                    "role": "user",
                    "content": "You must either call a tool or provide a complete final answer.",
                }
            )
            continue

        empty_turn_count = 0

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

    answer = _synthesize_partial_answer(state)
    state.finished = True
    on_event("max_steps_reached", {"steps": state.steps})
    on_event("final_answer", {"answer": answer, "partial": True})
    return answer
