"""Open WebUI-visible agent profiles and their execution policy."""

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
    prompt_mode: str = "code"
    force_research: bool = False
    max_completion_tokens: int | None = None
    timeout_seconds: int | None = None


PROFILES: dict[str, AgentProfile] = {
    "auto": AgentProfile(DEFAULT_MODEL, "auto"),
    DEFAULT_MODEL: AgentProfile(DEFAULT_MODEL, "auto"),
    "quick": AgentProfile(FAST_MODEL, "quick"),
    "code": AgentProfile(DEFAULT_MODEL, "code"),
    "research": AgentProfile(RESEARCH_MODEL, "research", force_research=True),
    "finance": AgentProfile(
        FINANCE_MODEL,
        "finance",
        force_research=True,
        max_completion_tokens=FINANCE_MAX_COMPLETION_TOKENS,
        timeout_seconds=FINANCE_LLM_TIMEOUT_SECONDS,
    ),
    "deep": AgentProfile(DEFAULT_MODEL, "deep"),
    "vision": AgentProfile(DEFAULT_MODEL, "vision"),
}


def resolve_profile(model_id: str | None) -> AgentProfile:
    if not model_id:
        return PROFILES["auto"]
    try:
        return PROFILES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown agent profile '{model_id}'") from exc
