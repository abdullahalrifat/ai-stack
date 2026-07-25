"""Open WebUI-visible agent profiles and their execution policy."""

from dataclasses import dataclass

from app.core.config import AGENT_MODEL_ID, DEFAULT_MODEL, FAST_MODEL, FINANCE_MAX_COMPLETION_TOKENS, FINANCE_MODEL, RESEARCH_MODEL


@dataclass(frozen=True)
class AgentProfile:
    model: str
    prompt_mode: str = "code"
    force_research: bool = False
    max_completion_tokens: int | None = None


# `coding-agent` is retained for existing Open WebUI conversations.
PROFILES: dict[str, AgentProfile] = {
    AGENT_MODEL_ID: AgentProfile(DEFAULT_MODEL, "code"),
    "auto": AgentProfile(FAST_MODEL, "quick"),
    "quick": AgentProfile(FAST_MODEL, "quick"),
    "code": AgentProfile(DEFAULT_MODEL, "code"),
    "research": AgentProfile(RESEARCH_MODEL, "research", force_research=True),
    "finance": AgentProfile(FINANCE_MODEL, "finance", force_research=True, max_completion_tokens=FINANCE_MAX_COMPLETION_TOKENS),
    "deep": AgentProfile("reasoning", "deep"),
    "vision": AgentProfile("vision", "vision"),
}


def resolve_profile(model_id: str | None) -> AgentProfile:
    if not model_id or model_id == AGENT_MODEL_ID:
        return PROFILES[AGENT_MODEL_ID]
    try:
        return PROFILES[model_id]
    except KeyError as exc:
        raise ValueError(f"Unknown agent profile '{model_id}'") from exc
