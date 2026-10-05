"""Concrete model IDs and their execution policy.

Model IDs are the only public selectors. Workflow names are internal routing
concepts and are never exposed as model aliases.
"""

from dataclasses import dataclass

from app.core.config import (
    DEFAULT_MODEL,
    FAST_MODEL,
    FINANCE_LLM_TIMEOUT_SECONDS,
    FINANCE_MAX_COMPLETION_TOKENS,
    FINANCE_MODEL,
    RESEARCH_MODEL,
)


@dataclass(frozen=True)
class AgentProfile:
    model: str
    prompt_mode: str = "default"
    force_research: bool = False
    max_completion_tokens: int | None = None
    timeout_seconds: int | None = None


PROFILES: dict[str, AgentProfile] = {
    DEFAULT_MODEL: AgentProfile(DEFAULT_MODEL, "default"),
    FAST_MODEL: AgentProfile(FAST_MODEL, "fast"),
    RESEARCH_MODEL: AgentProfile(RESEARCH_MODEL, "research", force_research=True),
    FINANCE_MODEL: AgentProfile(
        FINANCE_MODEL,
        "finance",
        force_research=True,
        max_completion_tokens=FINANCE_MAX_COMPLETION_TOKENS,
        timeout_seconds=FINANCE_LLM_TIMEOUT_SECONDS,
    ),
}


def resolve_profile(model_id: str | None) -> AgentProfile:
    model_id = model_id or DEFAULT_MODEL
    try:
        return PROFILES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown model '{model_id}'") from exc
