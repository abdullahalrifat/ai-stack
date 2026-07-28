"""Model-context budgeting independent of a specific chat client."""

import json
import re

from app.core.config import CONTEXT_TOKEN_LIMIT, CONTEXT_OUTPUT_RESERVE_TOKENS


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
