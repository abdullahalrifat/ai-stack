import json
import logging
import re
from types import SimpleNamespace

from ..core.cancellation import cancellation_context
from ..core.config import (
    ANALYSIS_SYNTHESIS_MAX_TOKENS,
    ANALYSIS_SYNTHESIS_MODEL,
    ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS,
    CONTEXT_COMPACT_KEEP_RECENT,
    CONTEXT_COMPACT_THRESHOLD_TOKENS,
    MAX_AGENT_STEPS,
    MAX_EMPTY_MODEL_TURNS,
    MAX_EMPTY_SEARCH_RESULTS,
    MAX_TOOL_OUTPUT_CHARS,
    MAX_UNPRODUCTIVE_TOOL_CALLS,
)
from ..core.evidence import evidence_prompt
from ..core.exceptions import RunCancelled
from ..llm.client import chat, chat_with_tools, chat_with_tools_stream
from ..tools.registry import registry
from ..tools.schemas import schemas_for
from ..tools.filesystem import current_workspace, resolve_path
from .completion import (
    answer_audit as _answer_audit,
)
from .completion import (
    record_tool_progress as _record_tool_progress,
)
from .completion import (
    tool_result_failed,
)
from .context_budget import estimate_tokens, fit_user_context
from .parser import parse_tool_arguments
from .prompts import COMPACTION_PROMPT, PARTIAL_SYNTHESIS_PROMPT, executor_prompt

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

WORKSPACE_PREFETCH_TOOLS = (("list_files", {"directory": "."}),)
HYBRID_RESEARCH_TOOLS = QUICK_WORKSPACE_TOOLS | {"web_search", "web_fetch"}
CACHEABLE_READ_TOOLS = HYBRID_RESEARCH_TOOLS | {
    "inspect_test_environment",
    "workspace_root",
}

_WORKSPACE_REQUEST = re.compile(
    r"\b(?:repo(?:sitory)?|codebase|source code|working tree|project files?|"
    r"module|package|implementation)\b",
    re.IGNORECASE,
)
_WORKSPACE_FILE_REFERENCE = re.compile(
    r"(?<![\w/])(?:\./)?[A-Za-z0-9_.-]+(?:/[A-Za-z0-9_.-]+)*\."
    r"(?:c|cc|cpp|css|go|h|hpp|html|ini|java|js|json|jsx|md|mjs|php|py|rb|rs|"
    r"sh|sql|toml|ts|tsx|txt|yaml|yml)(?![\w/])",
    re.IGNORECASE,
)


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


def _tool_fingerprint(tool_name: str, args: dict) -> str:
    canonical = dict(args)
    for key in ("directory", "file_path", "path"):
        value = canonical.get(key)
        if not isinstance(value, str) or any(char in value for char in "*?["):
            continue
        try:
            resolved = resolve_path(value)
            canonical[key] = str(resolved.relative_to(current_workspace())) or "."
        except (OSError, ValueError, PermissionError):
            continue
    return f"{tool_name}:{json.dumps(canonical, sort_keys=True, default=str)}"


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
_PREMATURE_RECOVERY_HANDOFF = re.compile(
    r"\b(?:i(?:'ll| will|'m going to| am going to)|we(?:'ll| will)|"
    r"let(?:'s| us)|next|now)\b.{0,120}\b"
    r"(?:check|run|execute|install|try|start|proceed|continue)\b",
    re.IGNORECASE | re.DOTALL,
)


def requires_external_search(message: str) -> bool:
    return bool(_CURRENT_EXTERNAL_INFO.search(message))


def requires_workspace_inspection(message: str) -> bool:
    """Identify requests whose answer must be grounded in mounted source."""

    return bool(
        _WORKSPACE_REQUEST.search(message) or _WORKSPACE_FILE_REFERENCE.search(message)
    )


def explicit_workspace_paths(message: str, limit: int = 8) -> list[str]:
    """Extract conservative relative file references for read-only prefetch."""

    paths = []
    for match in _WORKSPACE_FILE_REFERENCE.finditer(message):
        path = match.group(0).removeprefix("./")
        if path not in paths:
            paths.append(path)
        if len(paths) >= limit:
            break
    return paths


def is_financial_query(message: str) -> bool:
    return bool(_FINANCIAL_QUERY.search(message))


def financial_price_query(message: str) -> str:
    """Turn a conversational stock request into a compact price lookup.

    Passing the entire request (often including a five-year analysis question)
    to a search engine dilutes the result ranking.  Keep the named company and
    market, then explicitly ask for the two fields that establish a last close.
    """

    subject = message
    match = re.search(
        r"\b(?:search|find|look\s+up)\s+([\w.-]+)", message, re.IGNORECASE
    )
    if match:
        subject = match.group(1)

    market = "DSE" if re.search(r"\bdse\b", message, re.IGNORECASE) else "stock market"
    return f"{subject} {market} latest closing price previous close historical data"


def financial_research_queries(
    message: str, entities: list[str] | None = None
) -> list[str]:
    """Return a minimal evidence set for an investment-style research request."""

    named_entities = [item.strip() for item in (entities or []) if item.strip()][:10]
    if named_entities:
        market = (
            "DSE Bangladesh"
            if re.search(r"\bdse\b|bangladesh", message, re.IGNORECASE)
            else "stock market"
        )
        return [
            f"{entity} {market} latest price annual report revenue profit debt latest news"
            for entity in named_entities
        ] + [f"{market} market sector outlook inflation interest rates latest"]

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
            if company
            and company in f"{item.get('title', '')} {item.get('url', '')}".lower()
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
    return f"{text[:head]}\n...[middle omitted]...\n{text[-(limit - head) :]}"


def _prefetch_external_search(state, available_tools: list[str], on_event):
    """Fetch current evidence before a non-tool-capable model can decline.

    The workspace tool loop remains model-driven. This narrow preflight is
    only for explicit/time-sensitive external requests, where answering from
    a model's training data is known to be incorrect.
    """
    if "web_search" not in available_tools or not (
        getattr(state, "requires_external_evidence", False)
        or requires_external_search(state.user_message)
    ):
        return None

    search_task = _external_search_query(state)
    queries = (
        financial_research_queries(search_task, getattr(state, "routing_entities", []))
        if getattr(state, "prompt_mode", "") == "finance"
        or is_financial_query(search_task)
        else [search_task]
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
        on_event(
            "tool_result", {"tool": "web_search", "result": result, "prefetch": True}
        )
        searches.append(result)

    if len(searches) == 1:
        if (
            "web_fetch" in available_tools
            and requires_workspace_inspection(state.user_message)
            and isinstance(searches[0], dict)
        ):
            results = searches[0].get("results")
            first_url = (
                results[0].get("url")
                if isinstance(results, list)
                and results
                and isinstance(results[0], dict)
                else None
            )
            if isinstance(first_url, str) and first_url.startswith(
                ("https://", "http://")
            ):
                args = {"url": first_url}
                on_event(
                    "tool_call",
                    {"tool": "web_fetch", "args": args, "prefetch": True},
                )
                try:
                    document = registry.execute("web_fetch", args)
                except Exception as exc:
                    logger.exception("Comparison source prefetch failed")
                    document = {"error": str(exc)}
                state.add_tool("web_fetch", document)
                on_event(
                    "tool_result",
                    {"tool": "web_fetch", "result": document, "prefetch": True},
                )
                return {"search": searches[0], "documents": [document]}
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
            on_event(
                "tool_result", {"tool": "web_fetch", "result": result, "prefetch": True}
            )
            documents.append(result)
            linked_pdf = report_pdf_link(result) if isinstance(result, dict) else None
            if linked_pdf:
                # The PDF is the substantive source; keep it in the bounded
                # model context instead of spending that space on the archive.
                documents.pop()
                pdf_args = {"url": linked_pdf}
                on_event(
                    "tool_call",
                    {"tool": "web_fetch", "args": pdf_args, "prefetch": True},
                )
                try:
                    pdf_result = registry.execute("web_fetch", pdf_args)
                except Exception as exc:
                    logger.exception("Prefetch report PDF fetch failed")
                    pdf_result = {"error": str(exc)}
                if isinstance(pdf_result, dict) and isinstance(
                    pdf_result.get("text"), str
                ):
                    pdf_result = {
                        **pdf_result,
                        "text": financial_document_excerpt(pdf_result["text"]),
                    }
                state.add_tool("web_fetch", pdf_result)
                on_event(
                    "tool_result",
                    {"tool": "web_fetch", "result": pdf_result, "prefetch": True},
                )
                documents.append(pdf_result)

    # Keep the price evidence and full-document extracts first: context is
    # bounded later, so placement determines what the local model can use.
    return {
        "price_search": searches[0],
        "documents": documents,
        "other_research": searches[1:],
    }


def _external_search_query(state, limit: int = 500) -> str:
    """Build a focused query that always satisfies the search-tool contract."""

    original = str(state.user_message)
    comparison = re.search(
        r"\bcompare\b.{0,80}?\b(?:with|against|to)\s+"
        r"(.{2,80}?)(?=\s+(?:to|for)\b|[,.;]|$)",
        original,
        re.IGNORECASE,
    )
    if comparison:
        source = f"{comparison.group(1)} official documentation features capabilities"
    else:
        tasks = getattr(state, "route_tasks", []) or []
        research_objectives = [
            str(task.get("objective", ""))
            for task in tasks
            if task.get("workflow") in {"research", "finance"}
            and str(task.get("objective", "")).strip()
        ]
        if research_objectives:
            source = research_objectives[0]
        else:
            brief = str(getattr(state, "execution_brief", "") or "")
            translated = brief.split("\n\n", 1)[0]
            source = translated.removeprefix("Translated objective:").strip()
            if not source:
                source = original

    query = " ".join(source.split())
    # Correct the recurring product-name transposition before sending it to a
    # search engine; the original request remains untouched everywhere else.
    query = re.sub(r"\bcluade\b", "Claude", query, flags=re.IGNORECASE)
    query = re.sub(r"\bcli\b", "CLI", query, flags=re.IGNORECASE)
    if len(query) <= limit:
        return query
    bounded = query[:limit].rsplit(" ", 1)[0].strip()
    return bounded or query[:limit]


def _prefetch_workspace(state, available_tools: list[str], on_event):
    """Ground explicit repository analysis before the model may answer."""

    if not requires_workspace_inspection(state.user_message):
        return None

    evidence = {}
    prefetch_tools = list(WORKSPACE_PREFETCH_TOOLS)
    explicit_paths = explicit_workspace_paths(state.user_message)
    if explicit_paths and "inspect_files" in available_tools:
        prefetch_tools.append(("inspect_files", {"paths": explicit_paths}))
    if "inspect_test_environment" in available_tools and re.search(
        r"\b(?:coverage|test|tests|pytest|lint)\b", state.user_message, re.I
    ):
        prefetch_tools.append(("inspect_test_environment", {"directory": "."}))
    if "inspect_files" in available_tools and re.search(
        r"\b(?:cli|terminal agent)\b", state.user_message, re.IGNORECASE
    ):
        prefetch_tools.append(
            (
                "inspect_files",
                {
                    "paths": [
                        "cli/ROADMAP.md",
                        "cli/README.md",
                        "contracts/aistack-protocol-v1.json",
                        "README.md",
                        "cli/pyproject.toml",
                    ]
                },
            )
        )
    for tool_name, args in prefetch_tools:
        if tool_name not in available_tools:
            continue
        on_event("tool_call", {"tool": tool_name, "args": args, "prefetch": True})
        try:
            result = registry.execute(tool_name, args)
        except Exception as exc:
            logger.exception("Workspace prefetch failed for %s", tool_name)
            result = {"error": str(exc)}
        state.add_tool(tool_name, result)
        _record_tool_progress(state, tool_name, args, result)
        if not tool_result_failed(result):
            cache = getattr(state, "prefetched_tool_results", None)
            if cache is None:
                cache = {}
                state.prefetched_tool_results = cache
            cache[_tool_fingerprint(tool_name, args)] = result
        on_event(
            "tool_result",
            {"tool": tool_name, "result": result, "prefetch": True},
        )
        evidence[tool_name] = result
    return evidence or None


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
        if (
            "function" in item
            or "tool_calls" in item
            or ("tool" in item and "args" in item)
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
    return (
        estimate_tokens(messages)
        + estimate_tokens(system_prompt)
        + estimate_tokens(tools)
        >= CONTEXT_COMPACT_THRESHOLD_TOKENS
    )


def _synthesize_partial_answer(
    state,
    *,
    model: str | None = None,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
) -> str:
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
    per_observation = max(600, 11_000 // max(1, len(selected)))
    balanced = []
    for observation in selected:
        result = json.dumps(observation.get("result"), default=str)
        if len(result) > per_observation:
            result = f"{result[:per_observation]}\n...[observation truncated]"
        balanced.append({"tool": observation.get("tool"), "result": result})
    evidence = json.dumps(balanced, ensure_ascii=False)
    prompt = f"""Task:
{state.user_message}

Collected tool evidence:
{evidence}
"""
    candidates = [candidate for candidate in (model, state.model) if candidate]
    for candidate in dict.fromkeys(candidates):
        try:
            answer = chat(
                [
                    {"role": "system", "content": PARTIAL_SYNTHESIS_PROMPT},
                    {"role": "user", "content": prompt},
                ],
                model=candidate,
                max_tokens=(
                    max_tokens
                    if max_tokens is not None
                    else getattr(state, "max_completion_tokens", None)
                ),
                timeout_seconds=timeout_seconds,
            ).strip()
            if answer:
                return answer
        except Exception:
            logger.exception("Evidence synthesis failed with model %s", candidate)

    if state.observations:
        tools = ", ".join(item["tool"] for item in state.observations)
        return (
            "I could not complete another tool-call turn, but I did inspect: "
            f"{tools}. Please retry the request for a fuller analysis."
        )
    return "I could not complete the investigation. Please retry the request."


def _stream_message(
    messages: list,
    tools: list,
    model: str,
    max_tokens: int | None = None,
    timeout_seconds: int | None = None,
    should_cancel=None,
) -> SimpleNamespace:
    """Collect one streamed model turn without publishing unaudited answer text.

    OpenAI-compatible APIs stream a function call in fragments.  The tool
    loop needs the completed call before it can execute it. Natural-language
    output remains buffered until completion auditing accepts the final turn.
    """

    content_parts: list[str] = []
    reasoning_parts: list[str] = []
    calls: dict[int, dict] = {}

    for chunk in chat_with_tools_stream(
        messages,
        tools=tools,
        model=model,
        max_tokens=max_tokens,
        timeout_seconds=timeout_seconds,
        should_cancel=should_cancel,
    ):
        choices = getattr(chunk, "choices", None) or []
        if not choices:
            continue
        delta = getattr(choices[0], "delta", None)
        if delta is None:
            continue

        content = getattr(delta, "content", None)
        if content:
            content_parts.append(content)

        # Qwen-family OpenAI-compatible streams can emit thought tokens on a
        # separate field. They are not an answer or a tool call, but recording
        # them prevents us from treating the stream shape as mysterious when
        # debugging a model/provider mismatch.
        reasoning = getattr(delta, "reasoning_content", None) or getattr(
            delta, "reasoning", None
        )
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


def execute_plan(
    state,
    on_event=None,
    on_token=None,
    should_cancel=None,
    force_research=False,
    on_checkpoint=None,
) -> str:
    """Run the tool-calling loop until the model produces a final answer.

    `on_event(event_type, payload)` is called for each notable step so a
    caller (see agent.execute_run) can stream progress to a durable event
    log. It is a no-op by default so synchronous callers are unaffected.
    """

    on_event = on_event or _noop_event
    should_cancel = should_cancel or (lambda: False)

    def finalize(answer: str, *, partial: bool = False) -> str:
        """Publish exactly one audited or explicitly partial terminal answer."""

        if partial:
            failures = _answer_audit(state, answer)
            if failures and "Incomplete requirements:" not in answer:
                answer = f"{answer}\n\nIncomplete requirements:\n- " + "\n- ".join(
                    failures
                )
        state.finished = True
        state.partial = partial
        if on_token is not None:
            on_token(answer)
        payload = {"answer": answer}
        if partial:
            payload["partial"] = True
        on_event("final_answer", payload)
        return answer

    available_tools = registry.list_tools()
    if not state.allow_write:
        available_tools = [t for t in available_tools if t not in WRITE_TOOLS]

    research_mode = (
        force_research
        or getattr(state, "requires_external_evidence", False)
        or requires_external_search(state.user_message)
    )
    workspace_mode = requires_workspace_inspection(state.user_message)
    if getattr(state, "prompt_mode", "code") == "quick" and not research_mode:
        available_tools = [
            tool for tool in available_tools if tool in QUICK_WORKSPACE_TOOLS
        ]

    if research_mode and not workspace_mode and "web_search" in available_tools:
        # Prevent a coding-oriented model from wandering through the mounted
        # repository when the user asked for current external information.
        available_tools = [
            tool for tool in ("web_search", "web_fetch") if tool in available_tools
        ]
    elif research_mode and workspace_mode:
        # Hybrid comparisons need both evidence domains, but advertising every
        # read-only utility makes the local model process a much larger schema
        # before its first token. Keep the focused inspection and web tools.
        available_tools = [
            tool for tool in available_tools if tool in HYBRID_RESEARCH_TOOLS
        ]

    tools = schemas_for(available_tools)
    workspace_evidence = _prefetch_workspace(state, available_tools, on_event)
    external_search = _prefetch_external_search(state, available_tools, on_event)

    useful_prefetch_tools = {
        observation.get("tool")
        for observation in state.observations
        if not tool_result_failed(observation.get("result"))
    }
    if (
        research_mode
        and workspace_mode
        and not state.allow_write
        and {"inspect_files", "web_search", "web_fetch"}.issubset(useful_prefetch_tools)
    ):
        answer = _synthesize_partial_answer(
            state,
            model=ANALYSIS_SYNTHESIS_MODEL,
            max_tokens=ANALYSIS_SYNTHESIS_MAX_TOKENS,
            timeout_seconds=ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS,
        )
        on_event(
            "evidence_synthesis",
            {
                "steps": state.steps,
                "observations": len(state.observations),
                "complete_evidence": True,
                "prefetched": True,
            },
        )
        return finalize(answer)

    workspace_context = ""
    if workspace_evidence is not None:
        workspace_context = f"""

Verified workspace discovery (continue with focused file inspection):
{_truncate(json.dumps(workspace_evidence, default=str))}
"""
    external_context = ""
    if external_search is not None:
        external_context = f"""

Current external search results (retrieved for this request; cite their URLs):
{_truncate(json.dumps(external_search, default=str))}
"""
    document_context = evidence_prompt(getattr(state, "document_evidence", {}))
    if document_context:
        document_context = f"""

Structured document evidence and provenance ledger:
{_truncate(document_context)}
"""

    task_context = f"""
Workspace:
{state.workspace}

Task:
{state.user_message}

Execution brief:
{_bounded_context(getattr(state, "execution_brief", ""), 3_500)}

Recent conversation:
{_bounded_context(state.history[-4:], 1_200)}

Relevant memory:
{_bounded_context(state.memories[:3], 1_000)}

Plan:
{_bounded_context(state.plan, 800)}
{workspace_context}
{external_context}
{document_context}
"""
    restored_observations = getattr(state, "observations", [])
    if restored_observations:
        task_context += (
            "\nRestored evidence from a checkpoint:\n"
            + _bounded_context(restored_observations[-8:], 3_000)
            + "\n"
        )

    system_prompt = executor_prompt(
        getattr(state, "prompt_mode", "code"), research_mode
    )
    task_context, budget = fit_user_context(system_prompt, tools, task_context)
    if budget["trimmed"]:
        on_event("context_budgeted", budget)
    messages = [
        {"role": "system", "content": system_prompt},
        {"role": "user", "content": task_context},
    ]

    leaked_tool_call_count = 0
    research_retry_count = 0
    completion_retry_count = 0
    empty_turn_count = 0
    empty_search_count = 0
    unproductive_calls: dict[str, int] = {}
    tool_call_counts: dict[str, int] = {}
    tool_result_cache: dict[str, object] = dict(
        getattr(state, "prefetched_tool_results", {}) or {}
    )
    recovery_required = False
    recovery_handoff_count = 0

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
                getattr(state, "max_completion_tokens", None),
                getattr(state, "timeout_seconds", None),
                should_cancel,
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
            failed_tools: list[str] = []
            duplicate_tools: list[str] = []
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
                fingerprint = _tool_fingerprint(tool_name, args)
                tool_call_counts[fingerprint] = tool_call_counts.get(fingerprint, 0) + 1
                duplicate = (
                    tool_name in CACHEABLE_READ_TOOLS
                    and fingerprint in tool_result_cache
                )

                on_event("tool_call", {"tool": tool_name, "args": args})
                logger.info("Executing tool %s with args=%s", tool_name, args)

                if tool_name not in available_tools:
                    result = {
                        "error": f"Tool '{tool_name}' is unavailable for this request."
                    }
                elif duplicate:
                    result = tool_result_cache[fingerprint]
                    duplicate_tools.append(tool_name)
                    on_event(
                        "duplicate_tool_call",
                        {"tool": tool_name, "args": args, "cached": True},
                    )
                else:
                    try:
                        with cancellation_context(should_cancel):
                            result = registry.execute(tool_name, args)
                    except RunCancelled:
                        raise
                    except Exception as e:
                        logger.exception("Tool %s raised unexpectedly", tool_name)
                        result = {"error": str(e)}

                if not duplicate:
                    state.add_tool(tool_name, result)
                    _record_tool_progress(state, tool_name, args, result)
                on_event("tool_result", {"tool": tool_name, "result": result})
                if isinstance(result, dict) and result.get("status") in {
                    "timed_out",
                    "kill_failed",
                }:
                    on_event(
                        f"tool_{result['status']}",
                        {
                            "tool": tool_name,
                            "job_id": result.get("job_id"),
                            "exit_code": result.get("exit_code"),
                        },
                    )
                if on_checkpoint is not None:
                    on_checkpoint(
                        {
                            "steps": state.steps,
                            "plan": state.plan,
                            "observations": [
                                *state.observations[:4],
                                *state.observations[
                                    max(4, len(state.observations) - 8) :
                                ],
                            ],
                            "route_tasks": getattr(state, "route_tasks", []),
                            "task_progress": getattr(state, "task_progress", {}),
                            "successful_mutation": getattr(
                                state, "successful_mutation", False
                            ),
                            "successful_verification": getattr(
                                state, "successful_verification", False
                            ),
                            "pending_failure_categories": sorted(
                                getattr(state, "pending_failure_categories", set())
                            ),
                        }
                    )

                failed = tool_result_failed(result)
                if not failed and not duplicate and tool_name in CACHEABLE_READ_TOOLS:
                    tool_result_cache[fingerprint] = result
                if tool_name == "search_text" and not result:
                    empty_search_count += 1
                elif not failed:
                    empty_search_count = 0

                unproductive = failed
                if unproductive:
                    unproductive_calls[fingerprint] = (
                        unproductive_calls.get(fingerprint, 0) + 1
                    )
                    failed_tools.append(tool_name)
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
                on_event(
                    "unproductive_search_loop",
                    {"empty_searches": empty_search_count},
                )
                return finalize(answer, partial=True)

            repeated = max(unproductive_calls.values(), default=0)
            if repeated >= MAX_UNPRODUCTIVE_TOOL_CALLS:
                answer = _synthesize_partial_answer(state)
                on_event("unproductive_tool_loop", {"repeated_calls": repeated})
                return finalize(answer, partial=True)

            duplicate_repeats = max(
                (
                    count - 1
                    for fingerprint, count in tool_call_counts.items()
                    if fingerprint in tool_result_cache
                ),
                default=0,
            )
            if duplicate_repeats >= 2:
                answer = _synthesize_partial_answer(state)
                on_event(
                    "repeated_tool_loop",
                    {"duplicate_repeats": duplicate_repeats},
                )
                return finalize(answer, partial=True)

            if research_mode and workspace_mode and not state.allow_write:
                useful_evidence = [
                    observation
                    for observation in state.observations
                    if not tool_result_failed(observation.get("result"))
                ]
                evidence_ready = state.steps >= 3 and len(useful_evidence) >= 6
                evidence_limit_reached = state.steps >= 6 and len(useful_evidence) >= 4
                if evidence_ready or evidence_limit_reached:
                    answer = _synthesize_partial_answer(
                        state,
                        model=ANALYSIS_SYNTHESIS_MODEL,
                        max_tokens=ANALYSIS_SYNTHESIS_MAX_TOKENS,
                        timeout_seconds=ANALYSIS_SYNTHESIS_TIMEOUT_SECONDS,
                    )
                    on_event(
                        "evidence_synthesis",
                        {
                            "steps": state.steps,
                            "observations": len(useful_evidence),
                            "complete_evidence": evidence_ready,
                        },
                    )
                    return finalize(answer, partial=not evidence_ready)

            if failed_tools:
                recovery_required = True
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "A tool call failed. Diagnose the returned error and continue toward the "
                            "requested outcome using a safe alternative or prerequisite check. Do not "
                            "stop at a future-tense proposal such as 'next I will install or run it'. "
                            "Only report a blocker after available recovery paths have been exhausted. "
                            f"Failed tool(s): {', '.join(failed_tools)}."
                        ),
                    }
                )
            elif duplicate_tools:
                recovery_required = False
                recovery_handoff_count = 0
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "That exact read-only tool result was already collected and was returned "
                            "from cache. Do not request it again. Use the existing evidence to answer "
                            "now, or inspect a materially different file/source only if one is still "
                            "required by the task."
                        ),
                    }
                )
            else:
                recovery_required = False
                recovery_handoff_count = 0

            continue

        answer = (message.content or "").strip()

        if not answer:
            empty_turn_count += 1
            on_event(
                "empty_model_turn",
                {
                    "count": empty_turn_count,
                    "reasoning_chars": len(
                        getattr(message, "reasoning_content", "") or ""
                    ),
                },
            )
            if empty_turn_count >= MAX_EMPTY_MODEL_TURNS:
                answer = _synthesize_partial_answer(state)
                return finalize(answer, partial=True)
            messages.append(
                {
                    "role": "user",
                    "content": "You must either call a tool or provide a complete final answer.",
                }
            )
            continue

        empty_turn_count = 0

        if recovery_required and _PREMATURE_RECOVERY_HANDOFF.search(answer):
            recovery_handoff_count += 1
            on_event(
                "premature_handoff_retry",
                {"count": recovery_handoff_count, "answer": answer},
            )
            messages.append({"role": "assistant", "content": answer})
            messages.append(
                {
                    "role": "user",
                    "content": (
                        "This is a progress announcement, not the requested outcome. Perform the "
                        "stated check or recovery action now using an available tool. Remember that "
                        "each command runs in a fresh shell: activation and shell state do not persist, "
                        "so invoke a virtual environment executable by its explicit path. Return a "
                        "final answer only after completing the task or exhausting recovery paths."
                    ),
                }
            )
            continue

        if research_mode and external_search and _RESEARCH_REFUSAL.search(answer):
            research_retry_count += 1
            if research_retry_count <= 1:
                on_event(
                    "research_answer_retry",
                    {"reason": "model ignored retrieved evidence"},
                )
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
                return finalize(diagnostic)

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

        audit_failures = _answer_audit(state, answer)
        if audit_failures:
            on_event("answer_audit_failed", {"failures": audit_failures})
            # Draft text is buffered until this audit succeeds, so both
            # streaming and non-streaming callers can receive one repair turn
            # without exposing the rejected draft.
            if completion_retry_count < 1:
                completion_retry_count += 1
                messages.append({"role": "assistant", "content": answer})
                messages.append(
                    {
                        "role": "user",
                        "content": (
                            "Revise the draft to satisfy these missing requirements:\n- "
                            + "\n- ".join(audit_failures)
                            + "\nUse only the evidence already provided, cite document source/location, "
                            "and disclose any requirement that cannot be completed."
                        ),
                    }
                )
                continue
            answer = f"{answer}\n\nIncomplete requirements:\n- " + "\n- ".join(
                audit_failures
            )
            return finalize(answer, partial=True)

        return finalize(answer)

    answer = _synthesize_partial_answer(state)
    on_event("max_steps_reached", {"steps": state.steps})
    return finalize(answer, partial=True)
