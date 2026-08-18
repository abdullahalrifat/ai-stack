"""Model-context budgeting independent of a specific chat client.

The tool loop feeds large tool payloads (directory listings, search results,
file contents) to the model. Rather than blindly slicing the serialized blob,
``summarize_tool_result`` keeps the structurally relevant parts: match lines
for searches, first/last lines for file contents, and head/tail windows for
anything else. The full result always remains in ``state.observations``; this
is only the model-visible transcript copy.
"""

import json
import re

from app.core.config import (
    CONTEXT_OUTPUT_RESERVE_TOKENS,
    CONTEXT_TOKEN_LIMIT,
    TOOL_RESULT_SUMMARY_CHARS,
    TOOL_RESULT_SUMMARY_ITEMS,
)

_SEARCH_TOOLS = {"search_text", "search_code"}
_FILE_TOOLS = {"read_file", "inspect_files"}
_ITEMS_TOOLS = {"list_files", "tree", "find_file"}


def estimate_tokens(value: object) -> int:
    """Conservative local estimate when an exact model tokenizer is unavailable."""
    text = value if isinstance(value, str) else json.dumps(value, default=str)
    # Code, JSON punctuation, and non-ASCII text generally tokenize more
    # densely than prose, so use the larger of word-ish and character bounds.
    pieces = len(re.findall(r"\w+|[^\w\s]", text, flags=re.UNICODE))
    return max(pieces, (len(text) + 2) // 3)


def fit_user_context(
    system: str, tools: list[dict], user: str
) -> tuple[str, dict[str, int]]:
    """Reserve response space and trim only user context when necessary."""
    fixed = estimate_tokens(system) + estimate_tokens(tools)
    available = max(256, CONTEXT_TOKEN_LIMIT - CONTEXT_OUTPUT_RESERVE_TOKENS - fixed)
    used = estimate_tokens(user)
    if used <= available:
        return user, {
            "fixed": fixed,
            "available": available,
            "used": used,
            "trimmed": 0,
        }
    # The task begins near the front; recent context/evidence is normally at
    # the tail. Preserve both with an explicit omission marker.
    max_chars = available * 3
    head = max_chars * 2 // 3
    tail = max_chars - head
    compacted = (
        f"{user[:head]}\n...[context omitted to fit model budget]...\n{user[-tail:]}"
    )
    return compacted, {
        "fixed": fixed,
        "available": available,
        "used": used,
        "trimmed": used - estimate_tokens(compacted),
    }


def window_lines(text: str, max_chars: int = TOOL_RESULT_SUMMARY_CHARS) -> str:
    """Keep the first and last lines of a large blob within a char budget."""

    if len(text) <= max_chars:
        return text
    head_chars = max_chars * 2 // 3
    tail_chars = max_chars - head_chars
    lines = text.splitlines(keepends=True)

    head = []
    total = 0
    for line in lines:
        if total + len(line) > head_chars:
            break
        head.append(line)
        total += len(line)

    tail = []
    total = 0
    for line in reversed(lines):
        if total + len(line) > tail_chars:
            break
        tail.append(line)
        total += len(line)
    tail = list(reversed(tail))

    omitted = len(lines) - len(head) - len(tail)
    if omitted <= 0:
        return text
    return (
        "".join(head)
        + f"\n...[truncated {omitted} lines]...\n"
        + "".join(tail)
    )


def _window_items(items: list, max_chars: int) -> tuple[list, int]:
    """Keep head/tail items within a char budget; return kept items + omitted."""

    kept = TOOL_RESULT_SUMMARY_ITEMS
    if len(items) <= kept * 2 or max_chars <= 0:
        if len(items) > kept * 2:
            items = [*items[:kept], *items[-kept:]]
        return items, max(0, len(items) - len(items))

    head = items[:kept]
    tail = items[-kept:]
    selected = [*head, *tail]
    omitted = len(items) - len(selected)
    return selected, omitted


def _serialize_match(item) -> str:
    if isinstance(item, dict):
        path = item.get("path", "")
        line = item.get("line_number", item.get("line"))
        text = item.get("text") or item.get("content") or ""
        if path:
            return f"{path}:{line}: {text}" if line is not None else f"{path}: {text}"
    return str(item)


def summarize_tool_result(
    tool_name: str,
    result,
    max_chars: int = TOOL_RESULT_SUMMARY_CHARS,
) -> str:
    """Return a compact, structure-preserving model-visible tool result."""

    if result is None:
        return "null"
    if isinstance(result, dict) and (
        result.get("error") or result.get("tool_error")
    ):
        return json.dumps(result, default=str)
    if tool_name in _SEARCH_TOOLS:
        return _summarize_search(tool_name, result, max_chars)
    if tool_name in _FILE_TOOLS:
        return _summarize_file_contents(result, max_chars)
    if tool_name in _ITEMS_TOOLS and isinstance(result, list):
        return _summarize_item_list(result, max_chars)
    return window_lines(json.dumps(result, default=str), max_chars)


def _summarize_search(tool_name: str, result, max_chars: int) -> str:
    if tool_name == "search_text":
        if isinstance(result, list):
            return _summarize_item_list(result, max_chars)
        return window_lines(json.dumps(result, default=str), max_chars)

    matches = result.get("matches") if isinstance(result, dict) else None
    if not isinstance(matches, list):
        return window_lines(json.dumps(result, default=str), max_chars)

    kept, omitted = _window_items(matches, max_chars)
    budget = max(200, max_chars // max(1, len(kept)))
    lines = [_serialize_match(match) for match in kept]
    summary = {
        "total_matches": len(matches),
        "matches": [window_lines(line, budget) for line in lines],
    }
    if omitted:
        summary["omitted_matches"] = omitted
        summary["note"] = (
            "only the first and last few matches are shown; use a narrower "
            "pattern to focus the search"
        )
    return json.dumps(summary, ensure_ascii=False)


def _summarize_file_contents(result, max_chars: int) -> str:
    if isinstance(result, dict) and isinstance(result.get("items"), list):
        items = result["items"]
        kept, omitted = _window_items(items, max_chars)
        budget = max(200, max_chars // max(1, len(kept)))
        summarized = []
        for item in kept:
            if isinstance(item, dict) and isinstance(item.get("content"), str):
                item = {**item, "content": window_lines(item["content"], budget)}
            elif isinstance(item, dict) and item.get("error"):
                pass
            summarized.append(item)
        payload = {"items": summarized}
        if omitted:
            payload["omitted_items"] = omitted
        return json.dumps(payload, ensure_ascii=False)

    if isinstance(result, dict) and isinstance(result.get("content"), str):
        payload = {
            **{k: v for k, v in result.items() if k != "content"},
            "content": window_lines(result["content"], max_chars),
        }
        return json.dumps(payload, ensure_ascii=False)

    return window_lines(json.dumps(result, default=str), max_chars)


def _summarize_item_list(items: list, max_chars: int) -> str:
    kept, omitted = _window_items(items, max_chars)
    payload = {"items": kept, "total_items": len(items)}
    if omitted:
        payload["omitted_items"] = omitted
    return json.dumps(payload, ensure_ascii=False)
