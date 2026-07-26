"""Bound untrusted OpenAI-client conversation context for local models."""

from collections.abc import Iterable

from app.core.config import OPENAI_INPUT_MAX_CHARS


def _excerpt(text: str, limit: int) -> str:
    """Return a labeled head/tail excerpt without silently hiding truncation."""

    if len(text) <= limit:
        return text
    if limit < 80:
        return text[:limit]
    head = int(limit * 0.7)
    tail = limit - head
    return f"{text[:head]}\n...[earlier client context omitted]...\n{text[-tail:]}"


def compact_openai_messages(messages: Iterable[object], max_chars: int = OPENAI_INPUT_MAX_CHARS) -> list[dict[str, str]]:
    """Keep the current request while fitting client context into a local budget.

    Continue and Open WebUI can send large codebase snippets or tool payloads
    as ordinary chat messages. The agent subsequently adds its own system
    prompt and tool schemas, so forwarding those messages verbatim can exceed
    Ollama's context before the agent gets a chance to act. Keep system intent,
    the newest user request, and recent history; discard oldest material first.
    """

    normalized = [
        {"role": str(getattr(message, "role", "user")), "content": str(getattr(message, "content", ""))}
        for message in messages
        if str(getattr(message, "content", "")).strip()
    ]
    if not normalized:
        return []

    # Preserve system guidance, but client-generated instructions/tools must
    # not consume the entire budget.
    system = [message for message in normalized if message["role"] == "system"]
    non_system = [message for message in normalized if message["role"] != "system"]
    result: list[dict[str, str]] = []
    remaining = max_chars

    if system:
        system_budget = min(1_200, max(500, max_chars // 5))
        combined = "\n\n".join(message["content"] for message in system)
        content = _excerpt(combined, system_budget)
        result.append({"role": "system", "content": content})
        remaining -= len(content)

    # The latest user turn is the request that must never be displaced by old
    # editor context. Reserve most of the remaining budget for it.
    latest_user_index = next(
        (index for index in range(len(non_system) - 1, -1, -1) if non_system[index]["role"] == "user"),
        len(non_system) - 1,
    )
    latest = non_system[latest_user_index]
    latest_budget = min(max(1_500, remaining * 2 // 3), max(1, remaining))
    latest_content = _excerpt(latest["content"], latest_budget)
    remaining -= len(latest_content)

    # Add recent prior turns in chronological order, with short excerpts. Old
    # tool output is least useful after the client has already formed a reply.
    recent: list[dict[str, str]] = []
    for message in reversed(non_system[:latest_user_index]):
        if remaining <= 160:
            break
        per_message = min(900 if message["role"] != "tool" else 400, remaining)
        content = _excerpt(message["content"], per_message)
        recent.append({"role": message["role"], "content": content})
        remaining -= len(content)

    result.extend(reversed(recent))
    result.append({"role": latest["role"], "content": latest_content})
    return result


def openai_prompt(messages: Iterable[object], max_chars: int = OPENAI_INPUT_MAX_CHARS) -> str:
    """Convert compacted OpenAI messages to the agent's existing text input."""

    return "\n".join(
        f"{message['role']}: {message['content']}"
        for message in compact_openai_messages(messages, max_chars=max_chars)
    )
