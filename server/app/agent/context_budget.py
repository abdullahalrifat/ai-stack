"""Model-context budgeting shared by the server executor."""

from jarvis_core.tokens import estimate_tokens

from app.core.config import CONTEXT_OUTPUT_RESERVE_TOKENS, CONTEXT_TOKEN_LIMIT


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

    max_chars = available * 3
    head = max_chars * 2 // 3
    tail = max_chars - head
    compacted = (
        f"{user[:head]}\n"
        "...[untrusted user context omitted to fit model budget]...\n"
        f"{user[-tail:]}"
    )
    return compacted, {
        "fixed": fixed,
        "available": available,
        "used": used,
        "trimmed": used - estimate_tokens(compacted),
    }
